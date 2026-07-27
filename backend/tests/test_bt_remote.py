"""BtRemoteController: what the UI is told when the remote's connection changes.

This module had no test at all, for a mechanical reason: `evdev` is absent
outside the Pi, so `EVDEV_AVAILABLE` is False and the controller is inert unless
a test injects a stand-in. Everything below therefore fakes the two pieces of
the outside world it talks to — the evdev node table and `bluetoothctl` — and
asserts what the controller *did* to them and what it broadcast.

What breaks when these fail: the BT-remote panel in Réglages (BtRemoteSettings
.vue → settingsStore.btRemote) shows a stale connection. That panel writes
`connected`/`discovering` optimistically the moment the user acts and relies
entirely on `bt_remote_status_changed` to correct itself, so a path that mutates
the monitored set without broadcasting strands it — the "Search" CTA binds both
:loading and :disabled to `discovering`, so a stuck value is an unclickable
button. Milo-Mac ignores this event (see its vendored WebSocketService.swift).
"""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.models.ws_events import BtRemoteConfigChanged, BtRemoteStatusChanged
from backend.hardware import bt_remote as bt_remote_module

REMOTE_MAC = "AA:BB:CC:DD:EE:FF"
REMOTE_NAME = "ANTICATER VK-01"


# ---------------------------------------------------------------- fake evdev
class FakeEcodes:
    EV_KEY = 1


class FakeInputDevice:
    """One /dev/input node. BLE HID exposes several per physical remote."""

    def __init__(self, path, name=REMOTE_NAME, uniq=REMOTE_MAC, keys=(113, 114, 115)):
        self.path = path
        self.name = name
        self.uniq = uniq
        self._keys = list(keys)
        self.closed = False
        self.events = asyncio.Queue()

    def capabilities(self, verbose=False):
        return {FakeEcodes.EV_KEY: self._keys}

    def close(self):
        self.closed = True

    async def async_read_loop(self):
        while True:
            event = await self.events.get()
            if event is None:
                raise OSError("device went away")
            yield event


class FakeEvdev:
    """Stand-in for the evdev module; `nodes` is the kernel's node table."""

    ecodes = FakeEcodes

    def __init__(self):
        self.nodes = {}

    def list_devices(self):
        return list(self.nodes)

    def InputDevice(self, path):  # noqa: N802 - mirrors evdev's own API
        if path not in self.nodes:
            raise OSError(f"no such device: {path}")
        return self.nodes[path]


def key_event(code):
    return types.SimpleNamespace(type=FakeEcodes.EV_KEY, code=code, value=1)


# --------------------------------------------------------- fake bluetoothctl
class FakeBluez:
    """Answers `bluetoothctl devices ...` and records every invocation."""

    def __init__(self):
        self.calls = []            # (argv, controller.running at call time)
        self.paired = []
        self.connected = []
        self.connect_succeeds = True
        self._controller = None

    def argv_names(self):
        return [argv[1] if len(argv) > 1 else "<interactive>" for argv, _ in self.calls]

    def make(self, argv):
        proc = MagicMock()
        proc.returncode = 0
        stdout = b""
        if len(argv) > 1 and argv[1] == "devices":
            rows = self.paired if "Paired" in argv else (
                self.connected if "Connected" in argv else self.paired + self.connected)
            seen, unique = set(), []
            for address, name in rows:
                if address not in seen:
                    seen.add(address)
                    unique.append((address, name))
            stdout = "".join(f"Device {a} {n}\n" for a, n in unique).encode()
        if len(argv) > 1 and argv[1] == "connect" and not self.connect_succeeds:
            proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()
        stdin = MagicMock()
        stdin.write = MagicMock()
        stdin.drain = AsyncMock()
        proc.stdin = stdin
        return proc


@pytest.fixture
def bt(monkeypatch):
    """A controller wired to fake evdev + bluetoothctl, with its broadcasts recorded."""
    evdev = FakeEvdev()
    monkeypatch.setattr(bt_remote_module, "evdev", evdev, raising=False)
    monkeypatch.setattr(bt_remote_module, "EVDEV_AVAILABLE", True)
    monkeypatch.setattr(bt_remote_module, "DBUS_AVAILABLE", False)

    bluez = FakeBluez()

    async def create_subprocess_exec(*argv, **kwargs):
        bluez.calls.append((argv, controller.running))
        return bluez.make(argv)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    broadcasts = []
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock(side_effect=broadcasts.append)

    settings = MagicMock()
    settings.get_setting = AsyncMock(return_value={
        "enabled": True,
        "device_name_filter": "ANTICATER",
        "key_map": dict(bt_remote_module.DEFAULT_KEY_MAP),
    })
    settings.set_setting = AsyncMock(return_value=True)

    volume_service = MagicMock()
    volume_service.volume_config = types.SimpleNamespace(step_bt_remote_db=2.0)
    volume_service.adjust_volume_db = AsyncMock()

    controller = bt_remote_module.BtRemoteController(volume_service, state_machine, settings)
    bluez._controller = controller

    return types.SimpleNamespace(
        controller=controller, evdev=evdev, bluez=bluez, broadcasts=broadcasts,
        volume_service=volume_service, settings=settings,
        status=lambda: [e for e in broadcasts if isinstance(e, BtRemoteStatusChanged)],
    )


async def connect_remote(bt, path="/dev/input/event5"):
    """Bring one node up and let the controller adopt it, as _periodic_scan would."""
    bt.evdev.nodes[path] = FakeInputDevice(path)
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]
    await bt.controller._scan_devices()
    return bt.evdev.nodes[path]


# ------------------------------------------------------- the toggle notifies
@pytest.mark.asyncio
async def test_disabling_announces_the_now_empty_device_list(bt):
    """Disabling clears the monitored set; without a status broadcast every other
    open surface (phone + kiosk at once) keeps showing the remote as connected."""
    bt.controller.enabled = True
    bt.controller.running = True
    await connect_remote(bt)
    assert bt.controller._monitored_paths
    bt.broadcasts.clear()

    await bt.controller.update_config({"enabled": False})

    assert bt.status(), "disabling broadcast no status — the UI cannot learn the remote is gone"
    assert bt.status()[-1].connected_devices == []
    assert any(isinstance(e, BtRemoteConfigChanged) for e in bt.broadcasts)


@pytest.mark.asyncio
async def test_enabling_announces_a_status_even_when_the_remote_stays_asleep(bt):
    """The store sets `discovering` true when it sends the toggle. Only a status
    broadcast clears it, and a bonded-but-asleep remote produces no other event —
    that is the case that left the Search button spinning and unclickable."""
    bt.controller.enabled = False
    bt.controller.running = False
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connect_succeeds = False

    await bt.controller.update_config({"enabled": True})

    assert bt.status(), "enabling broadcast no status — `discovering` stays true forever"
    assert bt.status()[-1].discovering is False
    await bt.controller._stop_scanning()


@pytest.mark.asyncio
async def test_disabling_stops_scanning_before_it_disconnects(bt):
    """_stop_scanning() clears `running`, which is what silences the monitor
    tasks dying on the dropped evdev nodes. Disconnecting first let one of them
    race a half-broadcast, so the same action notified or not depending on how
    fast the kernel tore the node down."""
    bt.controller.enabled = True
    bt.controller.running = True
    await connect_remote(bt)
    bt.bluez.calls.clear()

    await bt.controller.update_config({"enabled": False})

    disconnects = [running for argv, running in bt.bluez.calls
                   if len(argv) > 1 and argv[1] == "disconnect"]
    assert disconnects, "no disconnect was issued"
    assert not any(disconnects), "disconnect ran while scanning was still live"


@pytest.mark.asyncio
async def test_a_config_write_that_changes_nothing_broadcasts_no_status(bt):
    """Only a transition touches the connection. A status broadcast costs a
    bluetoothctl call (is_paired), so a plain key_map write must not pay it."""
    bt.controller.enabled = True
    bt.controller.running = True
    bt.broadcasts.clear()

    await bt.controller.update_config({"key_map": {"115": "volume_up"}})

    assert not bt.status()
    assert any(isinstance(e, BtRemoteConfigChanged) for e in bt.broadcasts)


@pytest.mark.asyncio
async def test_discovery_on_an_already_connected_remote_still_answers(bt):
    """POST /api/bt-remote/discover returns early when a device is already
    monitored. The store set `discovering` true before calling, and this is the
    one remaining path that would return while it is still set."""
    bt.controller.enabled = True
    bt.controller.running = True
    await connect_remote(bt)
    bt.broadcasts.clear()

    result = await bt.controller.trigger_discovery()

    assert result["status"] == "already_connected"
    assert bt.status(), "early return left the UI holding an optimistic `discovering`"
    assert bt.status()[-1].discovering is False


# ------------------------------------------------------------ node adoption
@pytest.mark.asyncio
async def test_a_matching_node_is_adopted_and_announced(bt):
    bt.controller.running = True
    await connect_remote(bt)

    assert bt.controller._monitored_paths == {"/dev/input/event5"}
    assert bt.status()[-1].connected_devices == [
        {"path": "/dev/input/event5", "name": REMOTE_NAME, "address": REMOTE_MAC}
    ]
    await bt.controller._stop_scanning()


@pytest.mark.asyncio
async def test_a_node_bluez_does_not_report_connected_is_left_alone(bt):
    """A disable/re-enable cycle leaves stale kernel nodes behind; adopting one
    shows a remote as connected that is not."""
    bt.controller.running = True
    bt.evdev.nodes["/dev/input/event5"] = FakeInputDevice("/dev/input/event5")
    bt.bluez.connected = []

    await bt.controller._scan_devices()

    assert bt.controller._monitored_paths == set()
    assert not bt.status()


@pytest.mark.asyncio
async def test_a_node_whose_name_does_not_match_the_filter_is_ignored(bt):
    bt.controller.running = True
    bt.evdev.nodes["/dev/input/event9"] = FakeInputDevice(
        "/dev/input/event9", name="Some Bluetooth Keyboard", uniq="11:22:33:44:55:66")
    bt.bluez.connected = [("11:22:33:44:55:66", "Some Bluetooth Keyboard")]

    await bt.controller._scan_devices()

    assert bt.controller._monitored_paths == set()


@pytest.mark.asyncio
async def test_losing_one_node_drops_every_node_of_the_same_remote(bt):
    """BLE HID publishes several nodes per connection and only one carries the
    volume keys. When one dies the rest are stale, so status must go to empty
    rather than keep reporting a remote that is gone."""
    bt.controller.running = True
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]
    for path in ("/dev/input/event5", "/dev/input/event6"):
        bt.evdev.nodes[path] = FakeInputDevice(path)
    await bt.controller._scan_devices()
    assert len(bt.controller._monitored_paths) == 2

    tasks = dict(bt.controller._monitor_tasks)
    await bt.evdev.nodes["/dev/input/event5"].events.put(None)  # OSError in the read loop
    await asyncio.sleep(0.05)

    assert bt.controller._monitored_paths == set()
    assert bt.status()[-1].connected_devices == []
    # Dropping the sibling has to cancel its monitor task, not merely forget it:
    # a task left reading a node that is gone outlives the remote it monitors.
    assert all(task.done() for task in tasks.values())


@pytest.mark.asyncio
async def test_status_reports_one_entry_per_remote_not_per_node(bt):
    bt.controller.running = True
    bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]
    for path in ("/dev/input/event5", "/dev/input/event6"):
        bt.evdev.nodes[path] = FakeInputDevice(path)

    await bt.controller._scan_devices()

    assert len(bt.controller._monitored_paths) == 2
    assert len(bt.controller.get_status()["connected_devices"]) == 1
    assert len(bt.controller.get_device_info()) == 1
    await bt.controller._stop_scanning()


# --------------------------------------------------------------- key events
@pytest.mark.asyncio
@pytest.mark.parametrize("keycode,expected_db", [(115, 2.0), (114, -2.0)])
async def test_a_volume_key_reaches_the_volume_service(bt, keycode, expected_db):
    """The point of the whole module: a keycode becomes a dB delta, signed by
    direction and scaled by the configured step."""
    bt.controller.running = True
    device = await connect_remote(bt)

    await device.events.put(key_event(keycode))
    await asyncio.sleep(0.05)

    bt.volume_service.adjust_volume_db.assert_awaited_once_with(expected_db)
    await bt.controller._stop_scanning()


@pytest.mark.asyncio
async def test_an_unmapped_keycode_is_ignored(bt):
    bt.controller.running = True
    device = await connect_remote(bt)

    await device.events.put(key_event(999))
    await asyncio.sleep(0.05)

    bt.volume_service.adjust_volume_db.assert_not_awaited()
    await bt.controller._stop_scanning()
