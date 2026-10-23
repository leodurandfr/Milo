# backend/sources/airplay/remote.py
"""shairport-sync's D-Bus interface: what the sender is playing, and what Milō asks of it.

Since 5.6 the AirPlay 2 sender's own "now playing" reaches shairport-sync as
the binary plists of `POST /command` (the default, `get_plist_metadata`), and
shairport-sync publishes them here already decoded and merged — the pipe
carries them only raw (`copl`), and no longer carries the `core` tags, the
`PICT` cover or the `prgr` progress at all. Measured 2026-10-16 on 5.6 with a
Mac (Music, over the system output), an iPhone (Music) and an iPhone (Spotify):

- `Client.NowPlayingInformation`: the whole current item — title, artist,
  album, duration, the playhead as ElapsedTime at Timestamp moving at
  PlaybackRate, the sender's UniqueIdentifier for the track, its
  ArtworkIdentifier, and the picture as a file shairport-sync wrote
  (`kShairportSyncNowPlayingInfoArtworkFilePath`). Each update is the whole
  item, and it is replaced then re-sent with the picture 8-90 ms later, so an
  update without a path is not a withdrawn picture. `{}` when a sender takes
  the receiver.
- `Client.CommandInformation`: the MediaRemote commands the sender takes, by
  number (0 play, 1 pause, 2 toggle, 4 next, 5 previous, 6/25 shuffle, 7/26
  repeat). Spotify lists 25/26 and not 6/7.
- `AdvancedRemoteControl.Shuffle` / `.LoopStatus`: the sender's modes, echoed
  back by the sender when Milō sets them (shairport-sync does not write them).
- `RemoteControl.AirplayVolume`: the sender's slider, -30..0 dB (-144 muted).
  Setting it with `SetAirplayVolume` moves the sender's slider; the sender
  sends nothing back but the final value.

Every request here was obeyed by all three senders: `Pause`/`Play`/`Next`/
`Previous` (fire-and-forget on the AirPlay 2 event port — the reply says
shairport-sync took it, never that the sender did, so the state follows what
the sender then publishes), the two modes, and the volume. There is no seek:
5.6 implements no command carrying a position.

`DropSession` ends the current AirPlay session and replaces it with nothing
(measured on 5.5.1, 2026-09-23: the daemon sends `disc` at once and the phone
falls back to its own speaker). It is how the idle timeout ends a paused
session (REQUEST_END) without restarting the daemon under the sender.

The interface is compiled in on every unit (`--with-dbus-interface`, in both
build paths) and the policy lets `milo` call it
(rootfs/etc/dbus-1/system.d/shairport-sync-dbus.conf).
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.core.models.audio_wire import RepeatMode
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger("source.airplay.remote")

BUS_NAME = "org.gnome.ShairportSync"
OBJECT_PATH = "/org/gnome/ShairportSync"
CLIENT_IFACE = f"{BUS_NAME}.Client"
REMOTE_IFACE = f"{BUS_NAME}.RemoteControl"
ADVANCED_IFACE = f"{BUS_NAME}.AdvancedRemoteControl"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
DBUS_DAEMON = "org.freedesktop.DBus"

# A daemon that no longer answers is the one case this can fail with a session
# still open (a dead one has already ended it): don't wait on it for long.
CALL_TIMEOUT_S = 5.0

_NPI = "kMRMediaRemoteNowPlayingInfo"
_ARTWORK_FILE = "kShairportSyncNowPlayingInfoArtworkFilePath"

# The sender's repeat modes, in shairport-sync's spelling, and Milō's.
_LOOP_TO_REPEAT: dict[str, RepeatMode] = {"Off": "off", "All": "context", "One": "track"}
_REPEAT_TO_LOOP = {mode: loop for loop, mode in _LOOP_TO_REPEAT.items()}


@dataclass(frozen=True)
class NowPlaying:
    """One `NowPlayingInformation`: the sender's current item.

    `at` is the sender's clock (epoch seconds) for `elapsed_ms` — the phone's
    and the unit's agree within milliseconds (NTP on both). `artwork_path` is
    a local file, None while the picture is in flight or there is none.
    """
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    duration_ms: Optional[int]
    elapsed_ms: Optional[int]
    at: Optional[float]
    rate: float
    track_id: Optional[int | str]
    artwork_id: Optional[str]
    artwork_path: Optional[str]

    @classmethod
    def from_info(cls, info: dict) -> Optional["NowPlaying"]:
        """None for the empty dict a sender taking the receiver starts with."""
        if not info:
            return None

        def seconds_ms(key):
            value = info.get(_NPI + key)
            return int(value * 1000) if isinstance(value, (int, float)) else None

        path = info.get(_ARTWORK_FILE) or None
        return cls(
            title=info.get(_NPI + "Title") or None,
            artist=info.get(_NPI + "Artist") or None,
            album=info.get(_NPI + "Album") or None,
            duration_ms=seconds_ms("Duration"),
            elapsed_ms=seconds_ms("ElapsedTime"),
            at=_epoch(info.get(_NPI + "Timestamp")),
            rate=float(info.get(_NPI + "PlaybackRate") or 0.0),
            # Some items carry no UniqueIdentifier, only the ContentItemIdentifier
            # (a Mac, measured 2026-10-23).
            track_id=_first_present(info, "UniqueIdentifier", "ContentItemIdentifier"),
            artwork_id=info.get(_NPI + "ArtworkIdentifier") or None,
            artwork_path=path.removeprefix("file://") if isinstance(path, str) else None,
        )

    def position_ms(self, now: float) -> Optional[int]:
        """Where the sender's playhead is at `now`, from its last report."""
        if self.elapsed_ms is None:
            return None
        if self.rate <= 0 or self.at is None:
            return self.elapsed_ms
        return self.elapsed_ms + int(max(0.0, now - self.at) * self.rate * 1000)


@dataclass(frozen=True)
class RemoteUpdate:
    """One property the sender moved. `kind` is now_playing (a NowPlaying or
    None), commands (a frozenset of enabled command numbers), shuffle (bool),
    repeat (a RepeatMode) or volume (the sender's slider in dB)."""
    kind: str
    value: Any


def _first_present(info: dict, *keys: str):
    """The first of `keys` the item carries, a 0 included."""
    return next((info[_NPI + k] for k in keys if info.get(_NPI + k) is not None), None)


def _epoch(stamp) -> Optional[float]:
    """The plist date shairport-sync renders as ISO 8601 ("…T10:02:52.337123Z")."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _plain(value):
    """A D-Bus value with every Variant unwrapped."""
    if isinstance(value, Variant):
        return _plain(value.value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _enabled_commands(entries: list) -> frozenset:
    return frozenset(
        entry["kCommandInfoCommandKey"] for entry in entries
        if isinstance(entry, dict) and "kCommandInfoCommandKey" in entry
        and entry.get("kCommandInfoEnabledKey", True)
    )


def _update(interface: str, name: str, value) -> Optional[RemoteUpdate]:
    """The update a changed property means, or None for one Milō ignores."""
    value = _plain(value)
    if interface == CLIENT_IFACE and name == "NowPlayingInformation":
        return RemoteUpdate("now_playing", NowPlaying.from_info(value or {}))
    if interface == CLIENT_IFACE and name == "CommandInformation":
        return RemoteUpdate("commands", _enabled_commands(value or []))
    if interface == ADVANCED_IFACE and name == "Shuffle":
        return RemoteUpdate("shuffle", value == "On")
    if interface == ADVANCED_IFACE and name == "LoopStatus" and value in _LOOP_TO_REPEAT:
        return RemoteUpdate("repeat", _LOOP_TO_REPEAT[value])
    if interface == REMOTE_IFACE and name == "AirplayVolume":
        return RemoteUpdate("volume", float(value))
    return None


_WATCHED = (CLIENT_IFACE, ADVANCED_IFACE, REMOTE_IFACE)


class ShairportRemote:
    """A connection to shairport-sync's bus object for one run of the source.

    `on_update` is called synchronously from the bus's reader, once per moved
    property, in signal order — the source posts it into its mailbox.
    """

    def __init__(self, on_update: Callable[[RemoteUpdate], None]):
        self._on_update = on_update
        self._bus: Optional[MessageBus] = None
        self._bg = BackgroundTaskSet(logger, "airplay.remote")

    async def start(self) -> None:
        """Subscribe, then read what is already there: signals only carry changes."""
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        for rule in (
            f"type='signal',interface='{PROPERTIES_IFACE}',member='PropertiesChanged',"
            f"path='{OBJECT_PATH}'",
            f"type='signal',sender='{DBUS_DAEMON}',interface='{DBUS_DAEMON}',"
            f"member='NameOwnerChanged',arg0='{BUS_NAME}'",
        ):
            reply = await self._bus.call(Message(
                destination=DBUS_DAEMON, path="/org/freedesktop/DBus", interface=DBUS_DAEMON,
                member="AddMatch", signature="s", body=[rule],
            ))
            if reply.message_type is MessageType.ERROR:
                raise RuntimeError(f"AddMatch refused: {reply.error_name} {reply.body}")
        self._bus.add_message_handler(self._on_message)
        await self._read_all()

    async def stop(self) -> None:
        await self._bg.cancel_all()
        if self._bus is not None:
            self._bus.remove_message_handler(self._on_message)
            self._bus.disconnect()
            self._bus = None

    async def _read_all(self) -> None:
        """Every watched property as it stands. A daemon not on the bus yet
        has none to give: its first signals will."""
        for interface in _WATCHED:
            reply = await self._call(PROPERTIES_IFACE, "GetAll", "s", [interface], quiet=True)
            if reply is None:
                continue
            for name, value in reply.body[0].items():
                update = _update(interface, name, value)
                if update is not None:
                    self._on_update(update)

    def _on_message(self, msg: Message) -> None:
        if msg.message_type is not MessageType.SIGNAL:
            return
        if msg.member == "NameOwnerChanged" and msg.interface == DBUS_DAEMON:
            # A new daemon (Restart=) starts with nothing; read it once it is up.
            if msg.body and msg.body[0] == BUS_NAME and msg.body[2]:
                self._bg.spawn(self._read_all(), label="read_all")
            return
        if msg.member != "PropertiesChanged" or msg.path != OBJECT_PATH:
            return
        interface, changed, invalidated = msg.body
        if interface not in _WATCHED:
            return
        for name, value in changed.items():
            update = _update(interface, name, value)
            if update is not None:
                self._on_update(update)
        for name in invalidated:
            self._bg.spawn(self._read_one(interface, name), label="read_one")

    async def _read_one(self, interface: str, name: str) -> None:
        reply = await self._call(PROPERTIES_IFACE, "Get", "ss", [interface, name], quiet=True)
        if reply is not None:
            update = _update(interface, name, reply.body[0])
            if update is not None:
                self._on_update(update)

    async def _call(
        self, interface: str, member: str, signature: str = "", body=(), quiet: bool = False,
    ) -> Optional[Message]:
        """One method call on the daemon's object, bounded. None when it did
        not take it (logged unless `quiet`: a property read before the daemon
        is up is expected)."""
        if self._bus is None:
            return None
        try:
            reply = await asyncio.wait_for(self._bus.call(Message(
                destination=BUS_NAME, path=OBJECT_PATH, interface=interface,
                member=member, signature=signature, body=list(body),
            )), CALL_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning(f"{member} did not reach shairport-sync: {e!r}")
            return None
        if reply.message_type is MessageType.ERROR:
            log = logger.debug if quiet else logger.warning
            log(f"shairport-sync refused {member}: {reply.error_name} {reply.body}")
            return None
        return reply

    async def send(self, member: str) -> bool:
        """A transport request (`Play`, `Pause`, `Next`, `Previous`)."""
        return await self._call(REMOTE_IFACE, member) is not None

    async def set_shuffle(self, shuffle: bool) -> bool:
        return await self._set(ADVANCED_IFACE, "Shuffle", Variant("s", "On" if shuffle else "Off"))

    async def set_repeat(self, mode: RepeatMode) -> bool:
        return await self._set(ADVANCED_IFACE, "LoopStatus", Variant("s", _REPEAT_TO_LOOP[mode]))

    async def set_volume(self, airplay_db: float) -> bool:
        """Move the sender's slider (-30..0 dB, -144 muted)."""
        return await self._call(REMOTE_IFACE, "SetAirplayVolume", "d", [airplay_db]) is not None

    async def _set(self, interface: str, name: str, value: Variant) -> bool:
        reply = await self._call(PROPERTIES_IFACE, "Set", "ssv", [interface, name, value])
        return reply is not None

    async def drop_session(self) -> bool:
        """Ask shairport-sync to end its session. True when it took the request."""
        return await self._call(BUS_NAME, "DropSession") is not None
