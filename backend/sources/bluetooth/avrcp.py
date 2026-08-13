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

**The playhead has two sources and they are not equally trustworthy.** BlueZ
does not read Position off the link on demand; it extrapolates from the sender's
last anchor. Traced on a live iPhone:

  41.181  signal   Status = paused
  41.182  our Get           41552      <- 24 s wrong
  41.191  signal   Position = 65294    <- the truth, 10 ms later
  44.714  signal   Status = playing
  44.714  our Get           68817      <- 3.1 s ahead
  44.725  signal   Position = 65681    <- the truth again

So a `Get` issued inside the window around a state change reads an anchor BlueZ
has not yet corrected, while the `PropertiesChanged` that follows carries the
corrected value. Hence the rule this module implements: **a signalled Position is
authoritative and always taken; a Get is advisory**, discarded while a state
change is still settling (see REANCHOR_SETTLE_S).

Between state changes there are no Position signals at all — the same trace shows
three seconds of playback carrying none — so the Get is still what moves the bar,
and a poll runs while playing.

**A seek done on the sender is invisible, and the reason is upstream.** BlueZ
does subscribe to AVRCP's position-changed event — but with the largest interval
the field can carry, deliberately:

    if (event == AVRCP_EVENT_PLAYBACK_POS_CHANGED)
        bt_put_be32(UINT32_MAX / 1000, &pdu->params[1]);
    /* "Set maximum interval possible for position changed as we only use it to
       resync." — bluez, profiles/audio/avrcp.c */

So the sender reports a position only at the resync points BlueZ cares about — a
state change, a track change — and never periodically. A mid-track scrub falls
between them and is never mentioned. The interval is hardcoded with no D-Bus
knob, so the only way to see one would be a patched bluetoothd, which is not a
thing this appliance is going to carry for one source.

Measured here: 94 s of scrubbing produced not one signal and left the playhead
14.3 s adrift with no sign of converging, and a pause snapped it back in a single
step. The bar is therefore wrong after a sender-side seek until the next pause,
skip, or track end. That is accepted, not worked around — and it is the same
family of limitation as AVRCP having no seek to offer in the first place.
"""
import asyncio
import contextlib
import logging
import time
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

# How often the playhead is re-read while the sender is playing, so a client
# arriving mid-track is handed something current rather than the last signalled
# value. The read is a local D-Bus Get, not a network call.
#
# It does *not* catch a scrub done on the phone. That was the original reason for
# polling and it was wrong: measured, a seek on the iPhone emitted no signal at
# all and left BlueZ's Get counting from the pre-seek anchor, 14.3 s adrift and
# never correcting. Nothing observable reports a sender-side seek — see the
# module docstring.
POSITION_POLL_INTERVAL = 5.0

# How long after a state change a *Get* is not to be believed. The trace in the
# module docstring puts BlueZ's stale window at ~10 ms; this is that, with room.
#
# Deliberately a delay and not a magnitude. Sizing it by "how far backwards is
# too far" cannot work: a Previous restarting a track three seconds in moves the
# playhead by exactly as much as a bad anchor does, so any threshold wide enough
# to absorb the noise also freezes the bar on a real restart — which is what
# happened. When the reading is wrong is knowable; how wrong is not.
REANCHOR_SETTLE_S = 0.25

# A transport command's effect is not observable when it returns. A Previous that
# restarts the track republishes an identical Track dict, so no property signal
# fires at all, and BlueZ keeps extrapolating from the pre-command anchor:
# measured, the restart stayed invisible for ~3 s. Nothing pushes the new value,
# so the only way to see it is to look again, a few times, and stop.
COMMAND_REREAD_DELAYS = (0.7, 1.6, 3.2, 5.5)

# How long a half-updated Track dict is given to complete. Measured gap between
# the incoming duration and the incoming title: ~600 ms. Past this the sender is
# assumed not to be mid-skip after all, and the playhead resumes — a frozen bar
# is worse than a wrong one.
TRACK_SETTLE_TIMEOUT_S = 2.0

# A track change this soon after a Next or Previous is that press's doing, so the
# new track started when the button was pressed — not when BlueZ got round to
# saying so, measured ~900 ms later. Past this window the queue advanced on its
# own and the only honest origin is the moment we noticed.
COMMAND_ATTRIBUTION_S = 3.0


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
    coalescing slot wakes the async notifier — a state change arrives as a burst
    of separate PropertiesChanged messages, and they must collapse to one
    broadcast rather than paint the UI once per property.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("source.bluetooth.avrcp")
        self._bus: Optional[MessageBus] = None
        self._player_path: Optional[str] = None
        self._last_address: Optional[str] = None
        self._track: Dict[str, Any] = {}
        self._status: str = ""
        self._position: Optional[int] = None
        self._on_update: Optional[UpdateCallback] = None
        self._dirty: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._notify_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._reread_task: Optional[asyncio.Task] = None
        self._status_changed_at: float = 0.0
        self._settling_since: float = 0.0
        self._own_playhead_from: Optional[float] = None
        self._command_at: float = 0.0
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

        for task in (self._notify_task, self._poll_task, self._reread_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._notify_task = None
        self._poll_task = None
        self._reread_task = None

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

        if member in ("Next", "Previous"):
            # Whatever this press turns out to have done, the playhead it lands
            # on starts here — see `_playhead_origin`.
            self._command_at = time.monotonic()

        if member == "Previous":
            # Previous puts the playhead at zero whichever thing it does — it
            # either restarts this track or moves to the one before, and both
            # start from the top. So there is nothing to wait and see: the
            # playhead becomes ours from this instant, immediately, and the bar
            # answers the press rather than three seconds after it.
            #
            # The two cases diverge only in *who* re-anchors BlueZ afterwards. A
            # move fires a Track change, which hands the playhead straight back.
            # A restart fires nothing at all, ever — BlueZ keeps counting from
            # the anchor the track had before, permanently adrift (traced on the
            # unit: 44 s with no discontinuity, a pause finally revealing 20242 ms
            # of error). There, ours is the only playhead there is.
            #
            # Taken while paused too, where it is worth even more: BlueZ
            # re-anchors on a state change and a paused Previous is not one, so
            # its Get answers the pre-press offset for as long as the pause
            # lasts. Owning it is what pins the bar to zero; `snapshot` is where
            # it stays there instead of counting.
            self._own_playhead_from = time.monotonic()
            self._mark_dirty()

        # Every transport command goes through here, so this is the one place
        # that can guarantee the playhead is looked at again afterwards.
        self._schedule_reread()
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

        self._set_position(reply.body[0].value)
        return True

    def _set_position(self, value: int) -> None:
        """Adopt a *read* playhead unless BlueZ has not re-anchored yet.

        Only the Get path goes through here. A signalled Position is the value
        BlueZ corrected itself to and is written straight in — see the module
        docstring for the trace, and REANCHOR_SETTLE_S for the window.
        """
        if time.monotonic() - self._status_changed_at < REANCHOR_SETTLE_S:
            return
        if self._settling:
            # Reads it would answer belong to the incoming track, and we cannot
            # yet say which track that is (see _is_half_updated).
            return
        if self._own_playhead_from is not None:
            # BlueZ is counting from an anchor a Previous invalidated, and it
            # will never notice (see `send`).
            return
        self._position = value

    def snapshot(self) -> Dict[str, Any]:
        """Current player state, keyed like PlaybackMetadata."""
        snap = parse_track(self._track)
        snap["is_playing"] = self._status in PLAYING_STATES
        position = self._position
        if self._own_playhead_from is not None:
            # Ours to count — but only a playing track advances. A Previous
            # pressed while paused owns the playhead at zero and holds it there
            # (see `send`); counted, it would creep up a bar the user can see is
            # stopped.
            position = (
                int((time.monotonic() - self._own_playhead_from) * 1000)
                if snap["is_playing"] else 0
            )
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
        """Forget the current player (sender gone, or AVRCP dropped).

        `_last_address` outlives the path on purpose. Every publish is addressed
        by MAC and the path is where the MAC comes from, so a departure would
        have nobody to be reported for — the notifier would wake, find no
        address, and drop it, leaving the track the sender took with it on
        screen until something else happened to move.
        """
        self._last_address = self.device_address or self._last_address
        self._player_path = None
        self._track = {}
        self._status = ""
        self._position = None
        self._settling_since = 0.0
        self._own_playhead_from = None
        self._command_at = 0.0

    def _apply_props(self, props: Dict[str, Any]) -> None:
        """Mirror a Track/Status/Position property batch (values are Variants)."""
        if "Track" in props:
            incoming = {k: v.value for k, v in props["Track"].value.items()}
            if self._is_half_updated(incoming):
                self._settling_since = time.monotonic()
            else:
                self._settling_since = 0.0
                self._track = incoming
                # The outgoing track's playhead is not an anchor for the incoming
                # one. Dropped before any Position in the same batch lands.
                self._position = None
                # A new track starts at zero, and BlueZ may take seconds to say
                # so — or never, leaving the bar frozen at 0:00 until the poll.
                # Counting it ourselves from the press keeps it moving; a
                # signalled Position hands it straight back.
                self._own_playhead_from = self._playhead_origin()
        if "Status" in props:
            if props["Status"].value != self._status:
                # Opens the window in which a Get answers a stale anchor — and
                # closes the one in which BlueZ's anchor was stale for good,
                # since a state change is exactly what makes it re-anchor.
                self._status_changed_at = time.monotonic()
                self._own_playhead_from = None
            self._status = props["Status"].value
        if "Position" in props and not self._settling:
            # Authoritative: BlueZ only signals Position when it has re-anchored,
            # and that value is precisely the correction a Get cannot give us.
            self._position = props["Position"].value
            self._own_playhead_from = None

    def _playhead_origin(self) -> Optional[float]:
        """When the track now starting actually started.

        A press is the better origin than the notification that follows it: BlueZ
        announced the new track ~900 ms after the Next that caused it, and the
        audio did not wait. With no press to attribute it to, the queue advanced
        by itself and now is all we know. Paused, nothing is counting.
        """
        if self._status not in PLAYING_STATES:
            return None
        now = time.monotonic()
        if now - self._command_at < COMMAND_ATTRIBUTION_S:
            return self._command_at
        return now

    def _is_half_updated(self, incoming: Dict[str, Any]) -> bool:
        """Whether a Track dict describes two tracks at once.

        On a skip, BlueZ carries the incoming track's Duration ~600 ms before its
        Title — traced on the unit, `dur 215533 → 211426` while the title stayed
        `Canto De Ossanha`, with the playhead resetting under the old name. A
        track that keeps its title and artist but changes length is not a
        different track; it is this one, mid-update. Publishing it resets the bar
        once for the phantom and again for the real change, which is the
        0:00 → 0:01 → 0:00 the user sees.
        """
        return (
            bool(self._track)
            and incoming.get("Title") == self._track.get("Title")
            and incoming.get("Artist") == self._track.get("Artist")
            and incoming.get("Duration") != self._track.get("Duration")
        )

    @property
    def _settling(self) -> bool:
        """Whether a half-updated Track is still waiting for its identity.

        Bounded: a sender that never completes the update must not freeze the
        playhead for the rest of the track.
        """
        if not self._settling_since:
            return False
        return time.monotonic() - self._settling_since < TRACK_SETTLE_TIMEOUT_S

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
                address = self.device_address or self._last_address
                if not address:
                    continue
                # Deliberately does not read the playhead. A signal is what wakes
                # this loop most often, and a Get issued next to a state change
                # is exactly the reading BlueZ has not corrected yet — the one
                # that jumps the bar. Whoever needs a fresh value reads it before
                # marking dirty; the signal path carries its own.
                await self._on_update(address, self.snapshot())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"AVRCP notify failed: {e}")

    def _schedule_reread(self) -> None:
        """Re-read the playhead a few times after a transport command.

        Replaces any re-read still pending: two commands in a row means the
        first one's schedule is describing a state that no longer exists.
        """
        if self._reread_task and not self._reread_task.done():
            self._reread_task.cancel()
        self._reread_task = asyncio.create_task(self._reread_loop())

    async def _reread_loop(self) -> None:
        """Look again on the COMMAND_REREAD_DELAYS schedule, then stop.

        Bounded on purpose: this covers the window where the sender has not yet
        told BlueZ what it did, and the poll owns the steady state.
        """
        elapsed = 0.0
        for delay in COMMAND_REREAD_DELAYS:
            try:
                await asyncio.sleep(delay - elapsed)
                elapsed = delay
                if self._stopped or not self._player_path:
                    return
                await self.read_position()
                self._mark_dirty()
            except asyncio.CancelledError:
                raise

    async def _poll_loop(self) -> None:
        """Advance the playhead while playing — no signal reports it moving.

        Reads before waking the notifier. Steady playback is exactly where a Get
        is trustworthy: far from any state change, BlueZ's extrapolation tracks
        real time (measured, to the millisecond over several seconds).
        """
        while not self._stopped:
            try:
                await asyncio.sleep(POSITION_POLL_INTERVAL)
                if self._player_path and self._status in PLAYING_STATES:
                    await self.read_position()
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
