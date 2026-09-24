"""The Bluetooth source's outside world, as measured on the unit (docs: source
architecture, phase 4): BlueZ 5.82 and bluez-alsa 4.3.1, the owner's iPhone
and Mac mini, systemd holding the units.

What was measured (2026-09-24, a passive probe on the system bus, a second
`bluealsa-cli monitor -p` and Milō's WS on one clock):

- A phone connecting: `PCMAdded`, then `PropertyChanged <pcm> Running true`
  once its stream opens (the iPhone opens it at connection, before playing).
- A pause stops nothing: Status goes `paused`, `Running` stays true (the Mac
  streams silence). A sender that sends nothing while its player says
  `playing` (the Mac switching its output to its own speakers): `Running`
  false, the PCM and the player stay.
- The Mac answers no AVRCP status for 100 s after connecting.
- bluetoothd stopped cleanly removes transport, player and PCM itself; killed,
  it removes nothing — `NameOwnerChanged(org.bluez)` at once, BlueALSA drops
  the PCM 7 ms later — and in both cases the daemon systemd brings back has
  the adapter closed (not discoverable, pairable nor connectable) and no
  agent, so every audio connection is refused.
- BlueALSA killed: `bluealsa-cli monitor` prints `ServiceStopped` and keeps
  running (no PCMRemoved); systemd restarts BlueALSA (`ServiceRunning`) but
  not `milo-bluealsa-aplay`, which `BindsTo=` it.

`Bluetooth` is also the adapter the old-wire golden drives (golden/
test_old_wire_bluetooth.py): its scenarios are written in these words.
"""
import asyncio
import heapq
import itertools
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import aiohttp
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
    AsyncioProxy, Wire, instant_short_sleep, make_settings, make_state_machine, settle,
)

_real_sleep = asyncio.sleep

ADAPTER_PATH = "/org/bluez/hci0"
PLAYER_IFACE = "org.bluez.MediaPlayer1"
DEVICE_IFACE = "org.bluez.Device1"
ADAPTER_IFACE = "org.bluez.Adapter1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
AVRCP_TARGET_UUID = "0000110c-0000-1000-8000-00805f9b34fb"

BLUEALSA_UNIT = "milo-bluealsa.service"
APLAY_UNIT = "milo-bluealsa-aplay.service"
BLUEZ_UNIT = "bluetooth.service"

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
    """`bluealsa-cli monitor -p`: prints the service's state, then PCM and
    property events, until terminated."""

    def __init__(self, service_running: bool) -> None:
        self.stdout = _Stream()
        self.stderr = _Stream()
        self.returncode: Optional[int] = None
        self.print(f"Service{'Running' if service_running else 'Stopped'} org.bluealsa")

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
    """A command that prints its answer and exits."""

    def __init__(self, output: str, returncode: int = 0) -> None:
        self._output = output.encode()
        self.returncode = returncode

    async def communicate(self):
        return self._output, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


class FakeBluez:
    """BlueZ + BlueALSA as the system bus and `bluealsa-cli` show them."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        # What bluetoothd starts with, as measured after a restart: powered by
        # its own AutoEnable, closed until someone opens it.
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
        self.running: Dict[str, bool] = {}          # PCM path -> Running
        self.handlers: List[Any] = []
        self.monitor: Optional[_MonitorProcess] = None
        self.player: Optional[Phone] = None
        self.track: Dict[str, Any] = {}
        self.status = ""
        self._anchor = 0
        self._anchored_at = 0.0
        self.transport: List[str] = []
        self.agents: List[str] = []                  # agents the running bluetoothd knows
        self.bluez_up = True
        self.bluealsa_up = True
        self.disconnects: List[str] = []             # addresses a Disconnect was called on
        self.keeps_link: set = set()                 # device paths whose peer does not let go
        self.refused_methods: set = set()            # MediaPlayer1 methods the target answers NotSupported
        self.adapter_refuses: set = set()            # adapter properties BlueZ settles back on
        self.cli_missing = False                     # `bluealsa-cli` cannot be spawned
        self.disconnect_gate: Optional[asyncio.Event] = None   # a Disconnect that takes its time
        self.adapter_busy = 0                        # adapter writes answered Busy (AutoEnable pending)
        self.adapter_gate: Optional[asyncio.Event] = None      # adapter writes held until set
        # Players other than `player` (a phone publishing two, measured:
        # "Musique" and "Spotify"): path -> (phone, status), and the one each
        # device's MediaControl1 names as its live player.
        self.others: Dict[str, Tuple[Phone, str]] = {}
        # GetManagedObjects held until set: the round trip a rescan costs
        # (measured, the Spotify player adopted 8 ms after the Music one left).
        self.object_manager_gate: Optional[asyncio.Event] = None
        self.controls: Dict[str, str] = {}
        self._owners = itertools.count(100)
        self.bluez_owner = f":1.{next(self._owners)}"

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
        for path, (phone, status) in self.others.items():
            objects[path] = {PLAYER_IFACE: {"Status": _variant(status), "Position": _variant(0)}}
        for device, player in self.controls.items():
            objects[device]["org.bluez.MediaControl1"] = {"Player": Variant("o", player)}
        return objects

    def props_of(self, path: str) -> Dict[str, Any]:
        return self.adapter if path == ADAPTER_PATH else self.devices[path]

    def accepts_audio(self) -> bool:
        """Whether an audio link can be made at all: measured, a bluetoothd
        with no agent refuses A2DP and AVRCP ("Authentication attempt without
        agent"), and a closed adapter is not connectable."""
        return self.bluez_up and bool(self.agents) and self.adapter["Discoverable"]

    # --- the signals ------------------------------------------------------
    def _emit(self, member: str, body: list, path: Optional[str] = None,
              interface: Optional[str] = None) -> None:
        sender = "org.freedesktop.DBus" if member == "NameOwnerChanged" else self.bluez_owner
        message = SimpleNamespace(
            message_type=MessageType.SIGNAL, member=member, body=body, path=path,
            interface=interface, sender=sender,
        )
        for handler in list(self.handlers):
            handler(message)

    def already_linked(self, phone: Phone, track: Optional[Dict[str, Any]],
                       status: str, position: int) -> None:
        """A PCM and a player that predate every listener: no signal, no line."""
        self.devices[phone.path]["Connected"] = True
        self.pcms.append(phone.pcm_path)
        self.running[phone.pcm_path] = True
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

    def other_player_added(self, phone: Phone, name: str, status: str) -> str:
        """A second player on the same phone (another app); nothing names it
        the live one."""
        path = f"{phone.path}/{name}"
        self.others[path] = (phone, status)
        self._emit("InterfacesAdded", [path, {PLAYER_IFACE: {
            "Status": _variant(status), "Position": _variant(0)}}])
        return path

    def other_player_changed(self, path: str, status: str) -> None:
        phone, _ = self.others[path]
        self.others[path] = (phone, status)
        self._emit("PropertiesChanged", [PLAYER_IFACE, {"Status": _variant(status)}, []], path=path)

    def other_player_removed(self, path: str) -> None:
        del self.others[path]
        self._emit("InterfacesRemoved", [path, [PLAYER_IFACE]])

    def control_names(self, phone: Phone, path: str) -> None:
        """BlueZ's MediaControl1.Player: the device's live player (measured
        switching to the second app's player when it started playing)."""
        self.controls[phone.path] = path
        self._emit("PropertiesChanged", ["org.bluez.MediaControl1",
                                          {"Player": Variant("o", path)}, []], path=phone.path)

    def player_removed(self) -> None:
        phone, self.player, self.track, self.status = self.player, None, {}, ""
        self._emit("InterfacesRemoved", [phone.player_path, [PLAYER_IFACE]])

    def pcm_added(self, phone: Phone, running: bool = True) -> None:
        self.devices[phone.path]["Connected"] = True
        self.pcms.append(phone.pcm_path)
        self.running[phone.pcm_path] = False
        self.monitor.print(f"PCMAdded {phone.pcm_path}")
        if running:
            self.stream(phone, True)

    def stream(self, phone: Phone, running: bool) -> None:
        """The sender's A2DP stream opens or stops (BlueALSA's `Running`)."""
        self.running[phone.pcm_path] = running
        self.monitor.print(
            f"PropertyChanged {phone.pcm_path} Running {'true' if running else 'false'}"
        )

    def pcm_removed(self, phone: Phone) -> None:
        self.devices[phone.path]["Connected"] = False
        self.pcms.remove(phone.pcm_path)
        self.running.pop(phone.pcm_path, None)
        self.monitor.print(f"PCMRemoved {phone.pcm_path}")

    # --- the daemons dying ------------------------------------------------
    def _owner_changed(self, old: str, new: str) -> None:
        self._emit("NameOwnerChanged", ["org.bluez", old, new],
                   path="/org/freedesktop/DBus", interface="org.freedesktop.DBus")

    def bluez_removes_its_objects(self) -> None:
        """A clean exit's first half: bluetoothd removes its player and its
        transports, and BlueALSA the PCMs (measured 88 ms before the name
        leaves the bus)."""
        if self.player:
            self.player_removed()
        for phone in (IPHONE, MAC_MINI, PIXEL):
            if phone.pcm_path in self.pcms:
                self.pcm_removed(phone)

    def bluez_leaves_the_bus(self) -> None:
        """The name goes. Killed, nothing was removed first: the player is gone
        with the daemon, unannounced, and BlueALSA drops the PCMs after it
        (measured 7 ms later)."""
        old, self.bluez_owner = self.bluez_owner, ""
        self.bluez_up = False
        self.agents.clear()
        self._owner_changed(old, "")
        self.player, self.track, self.status = None, {}, ""
        for phone in (IPHONE, MAC_MINI, PIXEL):
            if phone.pcm_path in self.pcms:
                self.pcm_removed(phone)
        for path in self.devices:
            if path != f"{ADAPTER_PATH}/dev_E4_17_D8_00_00_01":
                self.devices[path]["Connected"] = False
        self.adapter.update({"Powered": False, "Discoverable": False, "Pairable": False,
                             "DiscoverableTimeout": 180})

    def bluez_returns(self) -> None:
        """systemd's new bluetoothd: on the bus, then its adapter, powered by
        AutoEnable and closed."""
        self.bluez_up = True
        self.bluez_owner = f":1.{next(self._owners)}"
        self._owner_changed("", self.bluez_owner)
        self.adapter["Powered"] = True
        self._emit("InterfacesAdded", [ADAPTER_PATH, {ADAPTER_IFACE: {
            k: _variant(v) for k, v in self.adapter.items()}}], path="/")

    def bluealsa_exits(self) -> None:
        """BlueALSA killed: the monitor says so and keeps running; the PCMs go
        without a PCMRemoved line; BlueZ drops the transport and the player."""
        self.bluealsa_up = False
        self.monitor.print("ServiceStopped org.bluealsa")
        for path in list(self.pcms):
            self.pcms.remove(path)
            self.running.pop(path, None)
        if self.player:
            self.player_removed()

    def bluealsa_returns(self) -> None:
        self.bluealsa_up = True
        self.monitor.print("ServiceRunning org.bluealsa")

    # --- processes --------------------------------------------------------
    async def exec(self, *argv: str, **_: Any):
        if self.cli_missing and argv[0] == "bluealsa-cli":
            raise FileNotFoundError(2, "No such file or directory", "bluealsa-cli")
        if argv[:2] == ("bluealsa-cli", "monitor"):
            self.monitor = _MonitorProcess(self.bluealsa_up)
            return self.monitor
        if argv[:2] == ("bluealsa-cli", "list-pcms"):
            return _OneShotProcess("".join(f"{p}\n" for p in self.pcms))
        if argv[:2] == ("bluealsa-cli", "info"):
            path = argv[2]
            if path not in self.pcms:
                return _OneShotProcess("", returncode=1)
            running = "true" if self.running.get(path) else "false"
            return _OneShotProcess(
                f"Device: {path}\nTransport: A2DP-sink\nMode: source\nRunning: {running}\n"
            )
        raise AssertionError(f"unexpected process {argv}")


class _Interface:
    def __init__(self, world: FakeBluez, path: str, name: str) -> None:
        self._world, self._path, self._name = world, path, name

    def _alive(self) -> None:
        if not self._world.bluez_up:
            raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")

    async def call_set(self, iface: str, name: str, value: Variant) -> None:
        # A round trip: the caller is suspended while bluetoothd answers.
        await asyncio.sleep(0)
        self._alive()
        if self._path == ADAPTER_PATH and self._world.adapter_gate is not None:
            await self._world.adapter_gate.wait()
        if self._path == ADAPTER_PATH and self._world.adapter_busy:
            self._world.adapter_busy -= 1
            raise RuntimeError("org.bluez.Error.Busy")
        if self._path == ADAPTER_PATH and name in self._world.adapter_refuses:
            return
        self._world.props_of(self._path)[name] = value.value

    async def call_get(self, iface: str, name: str) -> Variant:
        self._alive()
        return _variant(self._world.props_of(self._path)[name])

    async def call_disconnect(self) -> None:
        self._alive()
        device = self._world.devices[self._path]
        self._world.disconnects.append(device["Address"])
        if self._world.disconnect_gate is not None:
            await self._world.disconnect_gate.wait()
        if self._path not in self._world.keeps_link:
            device["Connected"] = False

    async def call_get_managed_objects(self):
        self._alive()
        return self._world.managed_objects()

    async def call_register_agent(self, path: str, capability: str) -> None:
        self._alive()
        self._world.agents.append(path)

    async def call_request_default_agent(self, path: str) -> None:
        self._alive()

    async def call_unregister_agent(self, path: str) -> None:
        self._alive()
        if path in self._world.agents:
            self._world.agents.remove(path)


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
        if not self._world.bluez_up:
            raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")
        return path

    def get_proxy_object(self, service: str, path: str, introspection: str):
        world = self._world
        return SimpleNamespace(get_interface=lambda name: _Interface(world, path, name))

    async def call(self, message):
        world = self._world

        def reply(*body):
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=list(body))

        def error(name):
            return SimpleNamespace(message_type=MessageType.ERROR, body=[name])

        if message.interface == "org.freedesktop.DBus" and message.member == "AddMatch":
            return reply()
        if not world.bluez_up:
            return error("org.freedesktop.DBus.Error.ServiceUnknown")
        if message.interface == OBJECT_MANAGER_IFACE and message.member == "GetManagedObjects":
            if world.object_manager_gate is not None:
                await world.object_manager_gate.wait()
            return reply(world.managed_objects())
        if message.interface == PROPS_IFACE and message.member == "Get":
            assert message.body == [PLAYER_IFACE, "Position"], message.body
            if world.player is None or message.path != world.player.player_path:
                return error("org.freedesktop.DBus.Error.UnknownObject")
            return reply(_variant(world.position()))
        if message.interface == PLAYER_IFACE:
            if world.player is None or message.path != world.player.player_path:
                return error("org.freedesktop.DBus.Error.UnknownObject")
            if message.member in world.refused_methods:
                return error("org.bluez.Error.NotSupported")
            world.transport.append(message.member)
            return reply()
        raise AssertionError(f"unexpected D-Bus call {message.interface}.{message.member}")


class _Response:
    def __init__(self, data: Dict[str, Any], held: Optional[asyncio.Event]) -> None:
        self.status = 200
        self._data = data
        self._held = held

    async def json(self, content_type=None) -> Dict[str, Any]:
        if self._held is not None:
            await self._held.wait()
        return self._data

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeITunes:
    """iTunes' /search (artist step) and /lookup (catalogue step). While
    `held` is set and not yet released, every answer waits for it — a slow
    network, for a scenario that moves on during a lookup."""

    def __init__(self, held: Callable[[], Optional[asyncio.Event]], timeout=None) -> None:
        self._held = held

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
            return _Response({"resultCount": len(results), "results": results}, self._held())
        rows = ITUNES_CATALOGUES.get((int(params["id"]), params["entity"]), [])
        results = [{"wrapperType": "artist", "artistId": int(params["id"])}] + [
            {"wrapperType": "collection", **row} for row in rows
        ]
        return _Response({"resultCount": len(results), "results": results}, self._held())


class Bluetooth:
    """The Bluetooth source on a real state machine, in a world the scenario
    drives: lifecycle and commands through the public API, everything else as
    what BlueZ, BlueALSA and systemd say."""

    def __init__(self, monkeypatch, settings: Optional[Dict[str, Any]] = None) -> None:
        world = self
        self.clock = VirtualClock()
        self.bluez = FakeBluez(self.clock)
        self.units: Dict[str, bool] = {BLUEZ_UNIT: True, BLUEALSA_UNIT: False, APLAY_UNIT: False}
        self._pids = itertools.count(124420)
        self.bluealsa_pid: Optional[int] = None
        self.bluealsa_state: Tuple[str, str] = ("inactive", "success")
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.unit_calls: List[Tuple[str, str]] = []
        self.failing_units: set = set()              # units systemd fails to start
        self.itunes_held: Optional[asyncio.Event] = None

        class Watch:
            def __init__(self, pid: int, on_exit: Callable[[], None], *a: Any, **k: Any) -> None:
                self.on_exit = on_exit
                world.watches.append((pid, on_exit))
                if pid != world.bluealsa_pid:
                    asyncio.get_running_loop().call_soon(on_exit)

            def close(self) -> None:
                world.watches[:] = [w for w in world.watches if w[1] is not self.on_exit]

        for module in (adapter_module, agent_module, avrcp_module, monitor_module):
            monkeypatch.setattr(module, "MessageBus", lambda **_: FakeSystemBus(self.bluez))
        monkeypatch.setattr(avrcp_module, "time", SimpleNamespace(monotonic=self.clock.monotonic))
        monkeypatch.setattr(avrcp_module, "asyncio", _Asyncio(sleep=self.clock.sleep))
        monkeypatch.setattr(monitor_module, "asyncio", _Asyncio(
            create_subprocess_exec=self.bluez.exec))
        monkeypatch.setattr(source_module, "asyncio", _Asyncio(
            create_subprocess_exec=self.bluez.exec,
            get_running_loop=lambda: _Loop(self.clock),
            sleep=instant_short_sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)
        # The resolver's politeness spacing is a real sleep; it passes at once.
        monkeypatch.setattr(artwork_resolver, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(artwork_resolver, "aiohttp", SimpleNamespace(
            ClientSession=lambda **k: FakeITunes(lambda: self.itunes_held, **k),
            ClientTimeout=aiohttp.ClientTimeout,
            ClientError=aiohttp.ClientError))

        systemd = Mock()
        systemd.start = AsyncMock(side_effect=lambda unit, *a: self._unit("start", unit))
        systemd.stop = AsyncMock(side_effect=lambda unit, *a: self._unit("stop", unit))
        systemd.restart = AsyncMock(side_effect=lambda unit, *a: self._unit("restart", unit))
        systemd.is_active = AsyncMock(side_effect=lambda unit, *a: self.units.get(unit, False))
        systemd.probe_active = AsyncMock(side_effect=lambda unit, *a: self.units.get(unit, False))
        # systemd slow to answer (a busy card): the session opens unwatched.
        self.pid_unreadable = False
        systemd.main_pid = AsyncMock(
            side_effect=lambda unit, *a: (
                None if self.pid_unreadable or unit != BLUEALSA_UNIT else self.bluealsa_pid))
        self.bluez_state: Tuple[str, str] = ("active", "success")
        systemd.unit_state = AsyncMock(side_effect=lambda unit, *a: (
            self.bluez_state if unit == BLUEZ_UNIT else self.bluealsa_state))
        self.systemd = systemd

        self.machine, recorder = make_state_machine()
        self.recorder = recorder
        # Whether the adapter was open when each envelope went out: what a
        # second phone scanning at that instant would have found.
        self.exposed_at: List[bool] = []
        record = recorder.broadcast_dict

        async def broadcast_dict(envelope: Dict[str, Any]) -> None:
            self.exposed_at.append(self.exposed())
            await record(envelope)
        recorder.broadcast_dict = broadcast_dict
        self.wire = Wire(self.machine, recorder)
        self.source = BluetoothSource(
            {},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.BLUETOOTH, self.source)

    # --- systemd ------------------------------------------------------------
    def _unit(self, verb: str, unit: str) -> bool:
        self.unit_calls.append((verb, unit))
        if verb in ("start", "restart") and unit in self.failing_units:
            return False
        if verb in ("start", "restart"):
            if unit == BLUEALSA_UNIT and (verb == "restart" or not self.units[unit]):
                self._bluealsa_spawn()
            self.units[unit] = True
        else:
            self.units[unit] = False
            if unit == APLAY_UNIT:
                pass
            elif unit == BLUEALSA_UNIT:
                self.bluealsa_state = ("inactive", "success")
                self.units[APLAY_UNIT] = False
                self._bluealsa_die()
        return True

    def _bluealsa_spawn(self) -> None:
        self.bluealsa_pid = next(self._pids)
        self.bluealsa_state = ("active", "success")
        self.bluez.bluealsa_up = True

    def _bluealsa_die(self) -> None:
        pid, self.bluealsa_pid = self.bluealsa_pid, None
        self.bluez.bluealsa_up = False
        for watched, on_exit in list(self.watches):
            if watched == pid:
                on_exit()

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

    async def try_command(self, cmd: str) -> Dict[str, Any]:
        result = await self.source.command(cmd, None)
        await settle()
        return result

    async def reroute(self, during: Optional[Callable[[], None]] = None) -> None:
        """A multiroom toggle; `during` is what the world does while the
        output is being switched (the writer down, the source held)."""
        async def apply_mode() -> None:
            if during is not None:
                during()
                await settle()
        await self.machine.reroute_active_source(apply_mode)
        await settle()

    async def elapse(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    def already_linked(self, phone: Phone, track: Optional[Dict[str, Any]] = None,
                       status: str = "playing", position: int = 0) -> None:
        """Before select: the link and its player exist, nobody heard them arrive."""
        self.bluez.already_linked(phone, track, status, position)

    # --- what BlueALSA says ----------------------------------------------
    async def pcm_added(self, phone: Phone, running: bool = True) -> None:
        """A phone's A2DP link: the PCM, then (measured on the iPhone) its
        stream opening at once, before anything plays."""
        self.bluez.pcm_added(phone, running)
        await settle()

    async def stream(self, phone: Phone, running: bool) -> None:
        self.bluez.stream(phone, running)
        await settle()

    async def pcm_removed(self, phone: Phone) -> None:
        self.bluez.pcm_removed(phone)
        await settle()

    async def pcm_announced_again(self, phone: Phone) -> None:
        """BlueALSA re-announcing a PCM it already holds (a codec change)."""
        self.bluez.monitor.print(f"PCMAdded {phone.pcm_path}")
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

    # --- the daemons -----------------------------------------------------
    async def bluetoothd_restarts(self) -> None:
        """`systemctl restart bluetooth`: a clean exit, then a new daemon."""
        self.bluez.bluez_removes_its_objects()
        await settle()
        self.bluez.bluez_leaves_the_bus()
        await settle()
        self.bluez.bluez_returns()
        await settle()

    async def bluetoothd_restarted_by_hand_name_first(self) -> None:
        """`systemctl restart bluetooth` where the name leaving is heard before
        BlueALSA's PCMRemoved (88 ms apart when measured — a busy loop can
        swap them)."""
        self.bluez_state = ("activating", "success")
        self.bluez.bluez_leaves_the_bus()
        await settle()
        self.bluez.bluez_returns()
        self.bluez_state = ("active", "success")
        await settle()

    async def bluetoothd_killed_and_restarted(self) -> None:
        """SIGKILL, then systemd's `Restart=on-failure` 60 ms later."""
        self.bluez_state = ("activating", "signal")
        self.bluez.bluez_leaves_the_bus()
        await settle()
        self.bluez.bluez_returns()
        await settle()

    async def bluealsa_killed(self) -> None:
        """SIGKILL of BlueALSA: systemd stops what `BindsTo=` it (aplay)."""
        self.bluealsa_state = ("activating", "signal")
        self.units[APLAY_UNIT] = False
        self.bluez.bluealsa_exits()
        self._bluealsa_die()
        await settle()

    async def bluealsa_stopped_by_systemd(self) -> None:
        """A backend restart: systemd stops the source units first (BindsTo),
        while the backend still runs; bluetooth.service is not bound to it.
        Measured: BlueALSA stopping cleanly removes its PCMs first
        (`PCMRemoved`), then the monitor says `ServiceStopped` — and the
        monitor child itself gets the backend's SIGTERM (exit -15)."""
        self.bluealsa_state = ("deactivating", "success")
        self.units[APLAY_UNIT] = False
        for phone in (IPHONE, MAC_MINI, PIXEL):
            if phone.pcm_path in self.bluez.pcms:
                self.bluez.pcm_removed(phone)
        await settle()
        self.bluealsa_state = ("inactive", "success")
        self.units[BLUEALSA_UNIT] = False
        self.bluez.bluealsa_exits()
        self._bluealsa_die()
        await settle()
        self.bluez.monitor.returncode = -15
        self.bluez.monitor.stdout.lines.put_nowait(b"")
        await settle()

    async def bluealsa_restarted_by_systemd(self) -> None:
        """`Restart=on-failure`: a new BlueALSA 280 ms later — and aplay, bound
        to it, is not started again by anyone but Milō."""
        self._bluealsa_spawn()
        self.units[BLUEALSA_UNIT] = True
        self.bluez.bluealsa_returns()
        await settle()

    async def monitor_dies(self) -> None:
        """`bluealsa-cli monitor` itself exits (its output closes)."""
        self.bluez.monitor.terminate()
        self.bluez.monitor.returncode = 1
        await settle()

    # --- iTunes ----------------------------------------------------------
    def hold_itunes(self) -> None:
        """From now on every cover lookup waits until `release_itunes`."""
        self.itunes_held = asyncio.Event()

    async def release_itunes(self) -> None:
        self.itunes_held.set()
        await settle()

    # --- what the wire says ----------------------------------------------
    def state(self) -> Dict[str, Any]:
        return self.machine.get_current_state()

    def active(self) -> bool:
        return self.state()["source_state"] == "active"

    def meta(self) -> Dict[str, Any]:
        return self.state()["metadata"] or {}

    def playing(self) -> bool:
        return bool(self.meta().get("is_playing"))

    def envelopes(self, category: str, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.recorder.envelopes if e["category"] == category and e["type"] == kind]

    def errors(self) -> List[str]:
        return [e["data"]["reason"] for e in self.envelopes("source", "error")]

    def cleared(self) -> int:
        return len(self.envelopes("source", "error_cleared"))

    def published(self) -> List[Dict[str, Any]]:
        out = []
        for e in self.envelopes("source", "state_changed"):
            full = (e.get("data") or {}).get("full_state")
            if full:
                out.append({"state": full["source_state"], **(full.get("metadata") or {})})
        return out

    def exposed(self) -> bool:
        return bool(self.bluez.adapter["Discoverable"] and self.bluez.adapter["Pairable"])

    def blocked(self) -> List[str]:
        """The aliases of the A2DP senders BlueZ refuses a link to."""
        return sorted(
            d["Alias"] for d in self.bluez.devices.values()
            if d["Blocked"] and A2DP_SOURCE_UUID in d["UUIDs"]
        )
