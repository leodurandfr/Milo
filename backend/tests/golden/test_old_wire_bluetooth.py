"""Bluetooth's old wire, scenario by scenario (see harness.py for the rules).

The outside world is BlueZ and BlueALSA. Every stimulus is written as what one
of them says: BlueALSA prints a PCMAdded/PCMRemoved line on `bluealsa-cli
monitor`, BlueZ emits InterfacesAdded/InterfacesRemoved/PropertiesChanged for
`org.bluez.MediaPlayer1`, answers a Get on Position by extrapolating from its
last anchor, and iTunes answers the cover lookup. The adapter below decides
which entry point hears it today — a later phase rewrites the adapter, never a
scenario.
"""
import asyncio
import heapq
import itertools
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import aiohttp
import pytest
from dbus_next import Variant
from dbus_next.constants import MessageType

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import artwork_resolver
from backend.sources.bluetooth import adapter as adapter_module
from backend.sources.bluetooth import agent as agent_module
from backend.sources.bluetooth import avrcp as avrcp_module
from backend.sources.bluetooth import monitor as monitor_module
from backend.sources.bluetooth import source as source_module
from backend.sources.bluetooth.source import BluetoothSource
from backend.tests.golden.harness import (
    AsyncioProxy, Wire, check_recording, instant_short_sleep, make_settings,
    make_state_machine, make_systemd, settle,
)

_real_sleep = asyncio.sleep

ADAPTER_PATH = "/org/bluez/hci0"
PLAYER_IFACE = "org.bluez.MediaPlayer1"
DEVICE_IFACE = "org.bluez.Device1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
AVRCP_TARGET_UUID = "0000110c-0000-1000-8000-00805f9b34fb"

SAYS = {"Title": "Says", "Artist": "Nils Frahm", "Album": "Spaces",
        "Duration": 511000, "TrackNumber": 4, "NumberOfTracks": 11}
HAMMERS = {"Title": "Hammers", "Artist": "Nils Frahm", "Album": "Spaces",
           "Duration": 381000, "TrackNumber": 5, "NumberOfTracks": 11}
FEELING_GOOD = {"Title": "Feeling Good", "Artist": "Nina Simone",
                "Album": "I Put a Spell on You", "Duration": 177000}
UNKNOWN_DEMO = {"Title": "Demo 3 (voice memo)", "Artist": "Léo",
                "Album": "Garage", "Duration": 95000}

# What iTunes knows: artist ids by exact name, then each artist's catalogue.
ITUNES_ARTISTS = {"Nils Frahm": 1001, "Nina Simone": 1002}
ITUNES_CATALOGUES = {
    (1001, "album"): [
        {"collectionName": "Spaces",
         "artworkUrl100": "https://is1.example/nils/spaces/100x100bb.jpg"},
    ],
    (1002, "album"): [
        {"collectionName": "I Put a Spell on You",
         "artworkUrl100": "https://is1.example/nina/spell/100x100bb.jpg"},
    ],
}


class VirtualClock:
    """Monotonic time that moves only when a scenario says so.

    Patched into avrcp.py (`time.monotonic` and `asyncio.sleep`: the position
    poll, the post-command re-reads, the half-updated-track flush and the
    self-counted playhead) and into source.py (`loop.time()`, the floor on
    progress broadcasts), and read by the fake BlueZ to extrapolate Position —
    so every playhead on the wire is a function of the scenario, never of how
    fast the host ran it.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self._timers: List[tuple] = []
        self._seq = itertools.count()

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float, *a: Any, **k: Any) -> None:
        if delay <= 0:
            await _real_sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._timers, (self.now + delay, next(self._seq), future))
        await future

    async def advance(self, seconds: float) -> None:
        """Let `seconds` pass, waking each sleeper at its own due time."""
        target = self.now + seconds
        await settle()
        while True:
            while self._timers and self._timers[0][2].done():
                heapq.heappop(self._timers)
            if not self._timers or self._timers[0][0] > target:
                break
            due, _, future = heapq.heappop(self._timers)
            self.now = due
            future.set_result(None)
            await settle()
        self.now = target
        await settle()


class _Asyncio:
    """One module's `asyncio`, with the named members replaced."""

    def __init__(self, **overrides: Any) -> None:
        self.__dict__.update(overrides)

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


class _Loop:
    """The running loop, answering `time()` from the virtual clock."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock.monotonic()

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio.get_running_loop(), name)


def _variant(value: Any) -> Variant:
    if isinstance(value, bool):
        return Variant("b", value)
    if isinstance(value, int):
        return Variant("u", value)
    if isinstance(value, list):
        return Variant("as", value)
    return Variant("s", value)


def _track_variant(track: Dict[str, Any]) -> Variant:
    return Variant("a{sv}", {k: _variant(v) for k, v in track.items()})


class Phone:
    """A paired sender, as BlueZ and BlueALSA name it."""

    def __init__(self, address: str, alias: str) -> None:
        self.address = address
        self.alias = alias
        token = address.replace(":", "_")
        self.path = f"{ADAPTER_PATH}/dev_{token}"
        self.player_path = f"{self.path}/player0"
        self.pcm_path = f"/org/bluealsa/hci0/dev_{token}/a2dpsnk/source"


IPHONE = Phone("F0:5C:77:12:34:56", "iPhone de Léo")
MAC_MINI = Phone("3C:22:FB:AA:BB:CC", "Mac mini de Léo")
PIXEL = Phone("58:24:29:01:02:03", "Pixel 8")


class _Stream:
    """A subprocess pipe fed line by line."""

    def __init__(self) -> None:
        self.lines: asyncio.Queue = asyncio.Queue()
        self.eof = False

    async def readline(self) -> bytes:
        line = await self.lines.get()
        if not line:
            self.eof = True
        return line

    def at_eof(self) -> bool:
        return self.eof

    async def read(self) -> bytes:
        return b""


class _MonitorProcess:
    """`bluealsa-cli monitor -p`: prints PCM events until terminated."""

    def __init__(self) -> None:
        self.stdout = _Stream()
        self.stderr = _Stream()
        self.returncode: Optional[int] = None

    def print(self, line: str) -> None:
        self.stdout.lines.put_nowait(f"{line}\n".encode())

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.lines.put_nowait(b"")

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        return self.returncode


class _OneShotProcess:
    """A command that prints its answer and exits 0."""

    def __init__(self, output: str) -> None:
        self._output = output.encode()
        self.returncode = 0

    async def communicate(self):
        return self._output, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return 0


class FakeBluez:
    """BlueZ + BlueALSA as the system bus and `bluealsa-cli` show them."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self.adapter = {"Powered": False, "Discoverable": False, "Pairable": False,
                        "DiscoverableTimeout": 180}
        self.devices: Dict[str, Dict[str, Any]] = {}
        for phone in (IPHONE, MAC_MINI, PIXEL):
            self.devices[phone.path] = {
                "Address": phone.address, "Alias": phone.alias,
                "UUIDs": [A2DP_SOURCE_UUID, AVRCP_TARGET_UUID],
                "Connected": False, "Blocked": False,
            }
        # The HID remote: known, never an audio peer, never blocked.
        self.devices[f"{ADAPTER_PATH}/dev_E4_17_D8_00_00_01"] = {
            "Address": "E4:17:D8:00:00:01", "Alias": "Milō remote",
            "UUIDs": ["00001124-0000-1000-8000-00805f9b34fb"],
            "Connected": True, "Blocked": False,
        }
        self.pcms: List[str] = []
        self.handlers: List[Any] = []
        self.monitor: Optional[_MonitorProcess] = None
        self.player: Optional[Phone] = None
        self.track: Dict[str, Any] = {}
        self.status = ""
        self._anchor = 0
        self._anchored_at = 0.0
        self.transport: List[str] = []

    # --- what BlueZ extrapolates -----------------------------------------
    def position(self) -> int:
        if self.status == "playing":
            return self._anchor + int(round((self.clock.now - self._anchored_at) * 1000))
        return self._anchor

    def _reanchor(self, position: Optional[int] = None) -> None:
        self._anchor = self.position() if position is None else position
        self._anchored_at = self.clock.now

    def player_props(self) -> Dict[str, Variant]:
        props = {"Status": _variant(self.status), "Position": _variant(self.position())}
        if self.track:
            props["Track"] = _track_variant(self.track)
        return props

    def managed_objects(self) -> Dict[str, Dict[str, Dict[str, Variant]]]:
        objects = {
            path: {DEVICE_IFACE: {k: _variant(v) for k, v in props.items()}}
            for path, props in self.devices.items()
        }
        if self.player:
            objects[self.player.player_path] = {PLAYER_IFACE: self.player_props()}
        return objects

    def props_of(self, path: str) -> Dict[str, Any]:
        return self.adapter if path == ADAPTER_PATH else self.devices[path]

    # --- the signals ------------------------------------------------------
    def _emit(self, member: str, body: list, path: Optional[str] = None) -> None:
        message = SimpleNamespace(
            message_type=MessageType.SIGNAL, member=member, body=body, path=path
        )
        for handler in list(self.handlers):
            handler(message)

    def already_linked(self, phone: Phone, track: Optional[Dict[str, Any]],
                       status: str, position: int) -> None:
        """A PCM and a player that predate every listener: no signal, no line."""
        self.devices[phone.path]["Connected"] = True
        self.pcms.append(phone.pcm_path)
        self.player, self.track, self.status = phone, dict(track or {}), status
        self._reanchor(position)

    def player_added(self, phone: Phone, track: Optional[Dict[str, Any]],
                     status: str, position: int) -> None:
        self.player, self.track, self.status = phone, dict(track or {}), status
        self._reanchor(position)
        self._emit("InterfacesAdded", [phone.player_path, {PLAYER_IFACE: self.player_props()}])

    def player_changed(self, props: Dict[str, Any]) -> None:
        body: Dict[str, Variant] = {}
        if "Track" in props:
            self.track = dict(props["Track"])
            body["Track"] = _track_variant(self.track)
        if "Status" in props:
            self._reanchor()
            self.status = props["Status"]
            body["Status"] = _variant(self.status)
        if "Position" in props:
            self._reanchor(props["Position"])
            body["Position"] = _variant(props["Position"])
        self._emit("PropertiesChanged", [PLAYER_IFACE, body, []], path=self.player.player_path)

    def player_removed(self) -> None:
        phone, self.player, self.track, self.status = self.player, None, {}, ""
        self._emit("InterfacesRemoved", [phone.player_path, [PLAYER_IFACE]])

    def pcm_added(self, phone: Phone) -> None:
        self.devices[phone.path]["Connected"] = True
        self.pcms.append(phone.pcm_path)
        self.monitor.print(f"PCMAdded {phone.pcm_path}")

    def pcm_removed(self, phone: Phone) -> None:
        self.devices[phone.path]["Connected"] = False
        self.pcms.remove(phone.pcm_path)
        self.monitor.print(f"PCMRemoved {phone.pcm_path}")

    # --- processes --------------------------------------------------------
    async def exec(self, *argv: str, **_: Any):
        if argv[:2] == ("bluealsa-cli", "monitor"):
            self.monitor = _MonitorProcess()
            return self.monitor
        if argv[:2] == ("bluealsa-cli", "list-pcms"):
            return _OneShotProcess("".join(f"{p}\n" for p in self.pcms))
        raise AssertionError(f"unexpected process {argv}")


class _Interface:
    def __init__(self, world: FakeBluez, path: str, name: str) -> None:
        self._world, self._path, self._name = world, path, name

    async def call_set(self, iface: str, name: str, value: Variant) -> None:
        self._world.props_of(self._path)[name] = value.value

    async def call_get(self, iface: str, name: str) -> Variant:
        return _variant(self._world.props_of(self._path)[name])

    async def call_disconnect(self) -> None:
        self._world.devices[self._path]["Connected"] = False

    async def call_get_managed_objects(self):
        return self._world.managed_objects()

    async def call_register_agent(self, path: str, capability: str) -> None:
        pass

    async def call_request_default_agent(self, path: str) -> None:
        pass

    async def call_unregister_agent(self, path: str) -> None:
        pass


class FakeSystemBus:
    """One `MessageBus(bus_type=SYSTEM).connect()`, answered by FakeBluez."""

    def __init__(self, world: FakeBluez) -> None:
        self._world = world

    async def connect(self) -> "FakeSystemBus":
        return self

    def disconnect(self) -> None:
        pass

    def add_message_handler(self, handler) -> None:
        self._world.handlers.append(handler)

    def remove_message_handler(self, handler) -> None:
        if handler in self._world.handlers:
            self._world.handlers.remove(handler)

    def export(self, path: str, interface: Any) -> None:
        pass

    def unexport(self, path: str) -> None:
        pass

    async def introspect(self, service: str, path: str) -> str:
        return path

    def get_proxy_object(self, service: str, path: str, introspection: str):
        world = self._world
        return SimpleNamespace(get_interface=lambda name: _Interface(world, path, name))

    async def call(self, message):
        world = self._world

        def reply(*body):
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=list(body))

        if message.interface == "org.freedesktop.DBus" and message.member == "AddMatch":
            return reply()
        if message.interface == OBJECT_MANAGER_IFACE and message.member == "GetManagedObjects":
            return reply(world.managed_objects())
        if message.interface == PROPS_IFACE and message.member == "Get":
            assert message.body == [PLAYER_IFACE, "Position"], message.body
            return reply(_variant(world.position()))
        if message.interface == PLAYER_IFACE:
            world.transport.append(message.member)
            return reply()
        raise AssertionError(f"unexpected D-Bus call {message.interface}.{message.member}")


class _Response:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.status = 200
        self._data = data

    async def json(self, content_type=None) -> Dict[str, Any]:
        return self._data

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeITunes:
    """iTunes' /search (artist step) and /lookup (catalogue step)."""

    def __init__(self, timeout=None) -> None:
        pass

    async def __aenter__(self) -> "FakeITunes":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def get(self, url: str, params: Dict[str, str]) -> _Response:
        if url.endswith("/search"):
            term = params["term"]
            results = (
                [{"wrapperType": "artist", "artistName": term,
                  "artistId": ITUNES_ARTISTS[term]}]
                if term in ITUNES_ARTISTS else []
            )
            return _Response({"resultCount": len(results), "results": results})
        rows = ITUNES_CATALOGUES.get((int(params["id"]), params["entity"]), [])
        results = [{"wrapperType": "artist", "artistId": int(params["id"])}] + [
            {"wrapperType": "collection", **row} for row in rows
        ]
        return _Response({"resultCount": len(results), "results": results})


class Bluetooth:
    """Adapter: how each outside-world stimulus reaches BluetoothSource today."""

    def __init__(self, monkeypatch) -> None:
        self.clock = VirtualClock()
        self.bluez = FakeBluez(self.clock)
        for module in (adapter_module, agent_module, avrcp_module, monitor_module):
            monkeypatch.setattr(module, "MessageBus", lambda **_: FakeSystemBus(self.bluez))
        monkeypatch.setattr(avrcp_module, "time", SimpleNamespace(monotonic=self.clock.monotonic))
        monkeypatch.setattr(avrcp_module, "asyncio", _Asyncio(sleep=self.clock.sleep))
        monkeypatch.setattr(monitor_module, "asyncio", _Asyncio(
            create_subprocess_exec=self.bluez.exec))
        monkeypatch.setattr(source_module, "asyncio", _Asyncio(
            create_subprocess_exec=self.bluez.exec,
            get_running_loop=lambda: _Loop(self.clock)))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        # The resolver's politeness spacing is a real sleep; it passes at once.
        monkeypatch.setattr(artwork_resolver, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(artwork_resolver, "aiohttp", SimpleNamespace(
            ClientSession=FakeITunes, ClientTimeout=aiohttp.ClientTimeout,
            ClientError=aiohttp.ClientError))
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = BluetoothSource(
            {},
            state_machine=self.machine,
            settings_service=make_settings(),
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.BLUETOOTH, self.source)

    # --- lifecycle and commands (public API) -----------------------------
    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.BLUETOOTH)
        await settle()

    async def deselect(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd: str) -> None:
        result = await self.source.command(cmd, None)
        assert result.get("success"), result
        await settle()

    async def elapse(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    def already_linked(self, phone: Phone, track: Optional[Dict[str, Any]] = None,
                       status: str = "playing", position: int = 0) -> None:
        """Before select: the link and its player exist, nobody heard them arrive."""
        self.bluez.already_linked(phone, track, status, position)

    # --- what BlueALSA says ----------------------------------------------
    async def pcm_added(self, phone: Phone) -> None:
        self.bluez.pcm_added(phone)
        await settle()

    async def pcm_removed(self, phone: Phone) -> None:
        self.bluez.pcm_removed(phone)
        await settle()

    # --- what BlueZ says about the sender's AVRCP player -----------------
    async def player_added(self, phone: Phone, track: Optional[Dict[str, Any]] = None,
                           status: str = "playing", position: int = 0) -> None:
        self.bluez.player_added(phone, track, status, position)
        await settle()

    async def player_changed(self, *batches: Dict[str, Any]) -> None:
        """One PropertiesChanged per batch, all on the bus before anyone reads."""
        for props in batches:
            self.bluez.player_changed(props)
        await settle()

    async def player_removed(self) -> None:
        self.bluez.player_removed()
        await settle()


@pytest.fixture
def bt(monkeypatch):
    return Bluetooth(monkeypatch)


async def test_select_and_leave(bt):
    await bt.select()
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "select_and_leave", bt.wire)


async def test_phone_with_no_player_connects_and_leaves(bt):
    await bt.select()
    await bt.pcm_added(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.elapse(12)
    await bt.pcm_removed(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "phone_with_no_player_connects_and_leaves", bt.wire)


async def test_player_before_pcm_publishes_a_track(bt):
    await bt.select()
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.pcm_added(IPHONE)
    await bt.elapse(5)                              # one position poll
    await bt.wire.snapshot_rest()
    await bt.elapse(11)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "player_before_pcm_publishes_a_track", bt.wire)


async def test_pause_and_resume_from_the_phone_and_from_milo(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, FEELING_GOOD, status="playing", position=30000)
    await bt.elapse(5)
    # Paused on the phone: BlueZ sends the state, then the corrected anchor.
    await bt.player_changed({"Status": "paused"}, {"Position": 35120})
    await bt.wire.snapshot_rest()
    await bt.elapse(8)
    await bt.player_changed({"Status": "playing"}, {"Position": 35120})
    await bt.elapse(5)
    # Paused from Milō: the phone answers the same way a beat later.
    await bt.command("pause")
    await bt.elapse(0.125)
    await bt.player_changed({"Status": "paused"}, {"Position": 40250})
    await bt.elapse(6)                              # the post-command re-reads
    await bt.wire.snapshot_rest()
    await bt.command("resume")
    await bt.elapse(0.125)
    await bt.player_changed({"Status": "playing"}, {"Position": 40250})
    await bt.elapse(6)
    await bt.deselect()
    check_recording("bluetooth", "pause_and_resume_from_the_phone_and_from_milo", bt.wire)


async def test_track_changes(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.elapse(5)
    # Next from Milō: BlueZ names the new track ~900 ms after the press.
    await bt.command("next")
    await bt.elapse(0.875)
    await bt.player_changed({"Track": HAMMERS})
    await bt.elapse(6)
    # Prev from Milō restarts the track: BlueZ says nothing at all.
    await bt.command("prev")
    await bt.elapse(6)
    await bt.wire.snapshot_rest()
    # The queue advances by itself, the new Duration landing before the Title.
    await bt.elapse(10)
    await bt.player_changed({"Track": {**HAMMERS, "Duration": FEELING_GOOD["Duration"]}})
    await bt.elapse(0.625)
    await bt.player_changed({"Track": FEELING_GOOD}, {"Position": 0})
    await bt.elapse(5)
    # A track iTunes does not know: no cover, the glyph stays.
    await bt.player_changed({"Track": UNKNOWN_DEMO}, {"Position": 0})
    await bt.elapse(5)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "track_changes", bt.wire)


async def test_disconnect_command_and_a_second_phone(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.elapse(2)
    # A second phone dials in while the first holds the link: it is dropped.
    await bt.pcm_added(PIXEL)
    await bt.pcm_removed(PIXEL)
    await bt.command("disconnect")
    # The link goes: BlueZ drops the player, BlueALSA the PCM.
    await bt.player_removed()
    await bt.pcm_removed(IPHONE)
    await bt.wire.snapshot_rest()
    await bt.elapse(6)
    await bt.deselect()
    check_recording("bluetooth", "disconnect_command_and_a_second_phone", bt.wire)


async def test_sender_already_connected_at_select(bt):
    # A backend restart under a live link: the PCM and a track-less player
    # (a Mac mini registers one and never serves its metadata) predate us.
    # Its Position is BlueZ extrapolating from an anchor nothing re-anchors:
    # hours into a song, inert because no duration comes with it.
    bt.already_linked(MAC_MINI, track=None, status="playing", position=9874000)
    await bt.select()
    await bt.wire.snapshot_rest()
    await bt.elapse(5)
    await bt.player_changed({"Status": "paused"})
    await bt.player_removed()
    await bt.pcm_removed(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "sender_already_connected_at_select", bt.wire)
