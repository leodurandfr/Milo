# backend/sources/bluetooth/avrcp.py
"""AVRCP player feed for the Bluetooth source.

BlueALSA carries the A2DP audio and nothing else. Track metadata and the
transport commands ride AVRCP, a separate channel that bluetoothd terminates
itself and publishes as `org.bluez.MediaPlayer1` — so this reader talks to
BlueZ, not to BlueALSA. The two feeds are independent: the player object
routinely appears a beat after the PCM, and sometimes never (an AVRCP target
is optional, and plenty of senders publish an empty track). Everything here is
therefore best-effort — a missing player means the source falls back to the
device-name status card, not that anything failed.

No cover art on this channel. AVRCP 1.6 carries images over a separate OBEX BIP
connection, which BlueZ exposes only as the experimental `ImgHandle` — a handle
valid for the lifetime of a BIP session it provides no client to open — and an
iPhone's Track dict was measured to carry no image field at all (Title,
TrackNumber, NumberOfTracks, Duration, Album, Artist, and nothing else). So
this module never produces an `album_art_url`; the source resolves one from the
track text instead, see source.py.

**Position is polled, not pushed** — the one place this module cannot be
event-driven. BlueZ advertises `Position` as `emits-change`, but the value only
moves when the sender sends an AVRCP PLAYBACK_POS_CHANGED notification, and iOS
never does: measured on a live session, the property answered a Get correctly
(21152 → 27197 → 33244 ms) while no `PropertiesChanged` for it was ever
delivered, leaving a published playhead frozen at the value captured on the
track change. So every publish re-reads it, and a poll runs while playing —
which also catches a scrub done on the phone, invisible from every signal.
"""
import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.shared.decorators import handle_errors


# Called with (device_address, snapshot) whenever the player state moved.
UpdateCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]

BLUEZ_SERVICE = "org.bluez"
MEDIA_PLAYER_IFACE = "org.bluez.MediaPlayer1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
DEVICE_PATH_TOKEN = "dev_"

# AVRCP playback states that mean audio is moving. The two seek states are the
# sender running its own fast-forward/rewind: Milō offers no such button, but a
# phone holding one must not flip the on-screen play icon for its duration.
PLAYING_STATES = frozenset({"playing", "forward-seek", "reverse-seek"})

# BlueZ documents UINT32_MAX on Position as "the track ended", not as a
# playhead. Read literally it would draw a 49-day progress bar.
POSITION_ENDED = 0xFFFFFFFF

# How often the playhead is re-read while the sender is playing. This is the
# ceiling on how long a scrub done on the phone can leave the bar wrong — no
# signal reports one — so it is deliberately shorter than the drift correction
# a pushed feed would need. The read is a local D-Bus Get, not a network call.
POSITION_POLL_INTERVAL = 5.0

_MATCH_RULES = (
    f"type='signal',interface='{PROPERTIES_IFACE}',member='PropertiesChanged',"
    f"arg0='{MEDIA_PLAYER_IFACE}'",
    f"type='signal',interface='{OBJECT_MANAGER_IFACE}',member='InterfacesAdded'",
    f"type='signal',interface='{OBJECT_MANAGER_IFACE}',member='InterfacesRemoved'",
)


def parse_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """Project an AVRCP `Track` dict onto Milō's canonical metadata keys.

    Empty strings are normalised to None because senders genuinely publish
    them — a paused player with no queue, a podcast app between episodes — and
    the frontend's rich-display gate keys on title/artist being truthy. A `""`
    that survived would mount the full-screen player on a blank track.

    Duration 0 means "unknown" in AVRCP, not "zero-length": kept as None so the
    player draws no progress bar rather than a full one.
    """
    def text(key: str) -> Optional[str]:
        value = track.get(key)
        return value.strip() or None if isinstance(value, str) else None

    duration = track.get("Duration")
    return {
        "title": text("Title"),
        "artist": text("Artist"),
        "album": text("Album"),
        "duration": duration if isinstance(duration, int) and duration > 0 else None,
    }


class AvrcpController:
    """Watches the connected sender's `org.bluez.MediaPlayer1` object.

    Owns one system-bus connection: it tracks the player object appearing and
    disappearing, mirrors its Track/Status/Position, and sends transport
    commands back. Signal handling is synchronous (a D-Bus message handler
    cannot await), so property updates are applied in the handler and a single
    coalescing slot wakes the async notifier — a playing sender emits Position
    roughly once a second and every burst must collapse to one broadcast.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("source.bluetooth.avrcp")
        self._bus: Optional[MessageBus] = None
        self._player_path: Optional[str] = None
        self._track: Dict[str, Any] = {}
        self._status: str = ""
        self._position: Optional[int] = None
        self._on_update: Optional[UpdateCallback] = None
        self._dirty: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._notify_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stopped = False

    def set_callback(self, on_update: UpdateCallback) -> None:
        """Set the coroutine notified after each coalesced player change."""
        self._on_update = on_update

    @property
    def has_player(self) -> bool:
        """Whether a sender is currently publishing an AVRCP player."""
        return self._player_path is not None

    @property
    def device_address(self) -> Optional[str]:
        """MAC of the sender owning the current player, or None."""
        return self._address_from_path(self._player_path) if self._player_path else None

    @handle_errors(default=False)
    async def start(self) -> bool:
        """Connect to BlueZ and begin tracking the sender's player.

        Returns False (fail open) when the bus or BlueZ is unavailable — the
        source keeps working as a plain receiver, minus the metadata.
        """
        self._stopped = False
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        for rule in _MATCH_RULES:
            reply = await self._bus.call(Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            ))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"D-Bus AddMatch failed: {reply.body}")

        self._bus.add_message_handler(self._on_dbus_message)
        self._notify_task = asyncio.create_task(self._notify_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())

        # A player already exists whenever the backend restarted under a live
        # session: the signals above only report changes from here on.
        await self._adopt_existing_player()

        self._logger.info("AVRCP player listener active")
        return True

    async def stop(self) -> None:
        """Drop the listener, the bus and any tracked player."""
        self._stopped = True

        for task in (self._notify_task, self._poll_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._notify_task = None
        self._poll_task = None

        if self._bus:
            with contextlib.suppress(Exception):
                self._bus.remove_message_handler(self._on_dbus_message)
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

        self._clear_player()

    async def send(self, member: str) -> bool:
        """Invoke an AVRCP transport method (`Play`, `Pause`, `Next`, `Previous`).

        A sender may answer NotSupported per method — the AVRCP target chooses
        which it implements — so the caller gets a plain False, not an
        exception, and reports it as a failed command.
        """
        if not self._bus or not self._player_path:
            return False

        reply = await self._bus.call(Message(
            destination=BLUEZ_SERVICE,
            path=self._player_path,
            interface=MEDIA_PLAYER_IFACE,
            member=member,
        ))
        if reply is None or reply.message_type == MessageType.ERROR:
            self._logger.warning(f"AVRCP {member} refused: {reply.body if reply else 'no reply'}")
            return False
        return True

    async def read_position(self) -> bool:
        """Re-read the playhead from BlueZ with an explicit Get.

        The only property fetched rather than received: see the module
        docstring — no sender observed here ever notifies a moved playhead, so
        a published position is only as fresh as its last read.
        """
        if not self._bus or not self._player_path:
            return False

        reply = await self._bus.call(Message(
            destination=BLUEZ_SERVICE,
            path=self._player_path,
            interface=PROPERTIES_IFACE,
            member="Get",
            signature="ss",
            body=[MEDIA_PLAYER_IFACE, "Position"],
        ))
        if reply is None or reply.message_type == MessageType.ERROR or not reply.body:
            return False

        self._position = reply.body[0].value
        return True

    def snapshot(self) -> Dict[str, Any]:
        """Current player state, keyed like PlaybackMetadata."""
        snap = parse_track(self._track)
        snap["is_playing"] = self._status in PLAYING_STATES
        position = self._position
        snap["position"] = None if position is None or position >= POSITION_ENDED else position
        return snap

    # === D-Bus plumbing ===

    async def _adopt_existing_player(self) -> None:
        """Adopt a player object that predates our listener, if any."""
        reply = await self._bus.call(Message(
            destination=BLUEZ_SERVICE,
            path="/",
            interface=OBJECT_MANAGER_IFACE,
            member="GetManagedObjects",
        ))
        if reply is None or reply.message_type == MessageType.ERROR:
            self._logger.debug("GetManagedObjects unavailable, no pre-existing player adopted")
            return

        for path, interfaces in (reply.body[0] or {}).items():
            if MEDIA_PLAYER_IFACE in interfaces:
                self._adopt_player(path, interfaces[MEDIA_PLAYER_IFACE])
                self._mark_dirty()
                return

    def _on_dbus_message(self, msg) -> None:
        """Route one BlueZ signal into the mirrored player state (synchronous)."""
        if msg.message_type != MessageType.SIGNAL or not msg.body:
            return

        if msg.member == "InterfacesAdded" and len(msg.body) >= 2:
            path, interfaces = msg.body[0], msg.body[1]
            if MEDIA_PLAYER_IFACE not in interfaces:
                return
            self._adopt_player(path, interfaces[MEDIA_PLAYER_IFACE])

        elif msg.member == "InterfacesRemoved" and len(msg.body) >= 2:
            path, interfaces = msg.body[0], msg.body[1]
            if path != self._player_path or MEDIA_PLAYER_IFACE not in interfaces:
                return
            self._clear_player()

        elif msg.member == "PropertiesChanged" and len(msg.body) >= 2:
            if msg.body[0] != MEDIA_PLAYER_IFACE or msg.path != self._player_path:
                return
            self._apply_props(msg.body[1])

        else:
            return

        self._mark_dirty()

    def _adopt_player(self, path: str, props: Dict[str, Any]) -> None:
        """Take a player object as the current one and seed it from its props."""
        if path != self._player_path:
            self._logger.info(f"AVRCP player at {path}")
            self._player_path = path
            self._track = {}
            self._status = ""
            self._position = None
        self._apply_props(props)

    def _clear_player(self) -> None:
        """Forget the current player (sender gone, or AVRCP dropped)."""
        self._player_path = None
        self._track = {}
        self._status = ""
        self._position = None

    def _apply_props(self, props: Dict[str, Any]) -> None:
        """Mirror a Track/Status/Position property batch (values are Variants)."""
        if "Track" in props:
            self._track = {k: v.value for k, v in props["Track"].value.items()}
        if "Status" in props:
            self._status = props["Status"].value
        if "Position" in props:
            self._position = props["Position"].value

    def _mark_dirty(self) -> None:
        """Wake the notifier, coalescing with any change it has not read yet."""
        with contextlib.suppress(asyncio.QueueFull):
            self._dirty.put_nowait(None)

    async def _notify_loop(self) -> None:
        """Deliver coalesced player changes to the source."""
        while not self._stopped:
            try:
                await self._dirty.get()
                if self._stopped or not self._on_update:
                    continue
                address = self.device_address
                if not address:
                    continue
                # Every publish carries a freshly read playhead, including the
                # ones a property signal triggered: a pause arrives as Status
                # alone, and publishing it with the position captured at the
                # last track change would jump the bar backwards.
                await self.read_position()
                await self._on_update(address, self.snapshot())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"AVRCP notify failed: {e}")

    async def _poll_loop(self) -> None:
        """Wake the notifier while playing, so the playhead keeps moving.

        Marks dirty rather than reading here, leaving `_notify_loop` the single
        place that fetches — otherwise the two would race over `_position`.
        """
        while not self._stopped:
            try:
                await asyncio.sleep(POSITION_POLL_INTERVAL)
                if self._player_path and self._status in PLAYING_STATES:
                    self._mark_dirty()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"AVRCP position poll failed: {e}")

    @staticmethod
    def _address_from_path(path: str) -> Optional[str]:
        """MAC out of /org/bluez/hciX/dev_XX_XX_XX_XX_XX_XX/playerN."""
        for part in path.split("/"):
            if part.startswith(DEVICE_PATH_TOKEN):
                return part[len(DEVICE_PATH_TOKEN):].replace("_", ":")
        return None
