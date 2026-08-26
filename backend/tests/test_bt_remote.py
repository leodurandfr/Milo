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
import contextlib
import logging
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
        self.written = []          # one list of stdin lines per interactive session
        self.paired = []
        self.connected = []
        self.discovered = []       # seen by a scan, not bonded — the daemon cache
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
                self.connected if "Connected" in argv
                else self.paired + self.connected + self.discovered)
            seen, unique = set(), []
            for address, name in rows:
                if address not in seen:
                    seen.add(address)
                    unique.append((address, name))
            stdout = "".join(f"Device {a} {n}\n" for a, n in unique).encode()
        if len(argv) > 1 and argv[1] == "connect" and not self.connect_succeeds:
            proc.returncode = 1
        if len(argv) > 2 and argv[1] == "remove":
            # `bluetoothctl remove` drops the bond, so the next `devices Paired`
            # no longer lists it — what is_paired() reads back after unpairing.
            self.paired = [row for row in self.paired if row[0] != argv[2]]
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()
        stdin = MagicMock()
        # An interactive session (`bluetoothctl` with no argv) is driven only
        # through stdin, so what is written there IS the command sequence.
        lines = []
        if len(argv) == 1:
            self.written.append(lines)
        stdin.write = MagicMock(side_effect=lambda blob: lines.append(blob.decode()))
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
async def test_a_node_that_vanished_from_the_kernel_table_is_announced_by_the_scan(bt):
    """The remote is switched off or walks out of range; the scan must say so.

    architecture/test_bt_remote_notifications.py cannot reach this branch: it
    asks whether a method contains a broadcast, and _scan_devices carries a
    second one for the "a new MAC appeared" branch, so the rule stays satisfied
    with this one deleted. Without it the Réglages panel — which writes
    `connected` optimistically and has no other route back to the truth — draws
    a remote that is gone until something else happens to broadcast.
    """
    bt.controller.running = True
    await connect_remote(bt)
    assert bt.controller._monitored_paths
    bt.broadcasts.clear()

    bt.evdev.nodes.clear()          # the kernel node is gone
    bt.bluez.connected = []
    await bt.controller._scan_devices()

    assert bt.controller._monitored_paths == set()
    assert bt.status(), "the scan dropped the node without telling the UI"
    assert bt.status()[-1].connected_devices == []


@pytest.mark.asyncio
async def test_unpairing_a_sleeping_remote_is_announced(bt):
    """forget_remote() on a remote with no evdev node still has to broadcast.

    A bonded remote that is asleep is monitored by nothing, so the scan
    forget_remote() runs first drops nothing and stays silent — the explicit
    broadcast is the only thing that carries paired=False to the surfaces still
    offering "unpair". forget_remote() writes none of the three containers
    NODE_STATE names either, so the notification guardrail does not apply to it
    at all.
    """
    bt.controller.enabled = True
    bt.controller.running = True
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connected = []         # bonded, but asleep: no node, no connection
    assert await bt.controller.is_paired() is True
    bt.broadcasts.clear()

    result = await bt.controller.forget_remote()

    assert result["status"] == "success"
    assert "remove" in bt.bluez.argv_names(), "no bond was removed"
    assert bt.status(), "unpairing an asleep remote broadcast nothing"
    assert bt.status()[-1].paired is False
    assert bt.status()[-1].connected_devices == []


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


# ------------------------------------------------- the key map, validated once
def test_the_default_key_map_only_uses_actions_the_dispatcher_knows():
    """DEFAULT_KEY_MAP ships the appliance's remote; an action outside the
    declared set would be a factory default that does nothing when pressed."""
    from backend.config.constants import BT_REMOTE_ACTIONS

    assert set(bt_remote_module.DEFAULT_KEY_MAP.values()) <= BT_REMOTE_ACTIONS
    assert all(k.isdigit() for k in bt_remote_module.DEFAULT_KEY_MAP)


@pytest.mark.parametrize("bad_map", [
    {"KEY_VOLUMEUP": "volume_up"},   # a name where an evdev code belongs
    {"115": "volume_upp"},           # an action nothing dispatches
    {"115": None},
], ids=["non-numeric-keycode", "unknown-action", "no-action"])
def test_a_key_map_the_controller_cannot_use_is_refused(bad_map):
    """Accepted, these reached `_is_bt_hid_device`, where `int(k)` over the whole
    map raised inside a broad `except` logged at debug — no device matched, no
    remote worked, and nothing said so."""
    from pydantic import ValidationError

    from backend.api.models import BtRemoteConfigRequest

    with pytest.raises(ValidationError):
        BtRemoteConfigRequest(key_map=bad_map)


def test_a_usable_key_map_is_accepted():
    from backend.api.models import BtRemoteConfigRequest

    payload = BtRemoteConfigRequest(key_map=dict(bt_remote_module.DEFAULT_KEY_MAP))
    assert payload.key_map == bt_remote_module.DEFAULT_KEY_MAP


# ------------------------------------------- the name filter, the destructive one
@pytest.mark.asyncio
async def test_an_empty_name_filter_unpairs_nothing_instead_of_everything(bt):
    """`forget_remote()` runs `bluetoothctl remove` over whatever the filter
    selects, and the selection skipped its name test when the filter was falsy.

    The appliance pairs A2DP senders on the same adapter (sources/bluetooth), so
    an empty filter aimed the "unpair the remote" button at the owner's phone —
    a bond removal, not a disconnect, so the phone has to be paired again from
    scratch. `_disconnect_matching_devices` promises the opposite in writing.
    """
    bt.controller.enabled = True
    bt.controller.running = True
    bt.controller.device_name_filter = ""
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME), ("11:22:33:44:55:66", "Pixel 8")]
    bt.bluez.connected = [("11:22:33:44:55:66", "Pixel 8")]

    await bt.controller.forget_remote()

    assert "remove" not in bt.bluez.argv_names(), "an empty filter removed a bond"
    assert "disconnect" not in bt.bluez.argv_names(), "an empty filter dropped a live link"
    assert ("11:22:33:44:55:66", "Pixel 8") in bt.bluez.paired


@pytest.mark.asyncio
async def test_an_empty_name_filter_does_not_report_a_phone_as_the_paired_remote(bt):
    """is_paired() is what makes the panel offer "unpair". Reading true off an
    unrelated A2DP bond both lies and arms the button above."""
    bt.controller.device_name_filter = ""
    bt.bluez.paired = [("11:22:33:44:55:66", "Pixel 8")]

    assert await bt.controller.is_paired() is False


@pytest.mark.asyncio
async def test_a_matching_filter_still_selects_only_the_remote(bt):
    """The guard above must not cost the ordinary case: with the real filter,
    the remote's bond goes and the phone's stays."""
    bt.controller.enabled = True
    bt.controller.running = True
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME), ("11:22:33:44:55:66", "Pixel 8")]

    await bt.controller.forget_remote()

    removed = [argv[2] for argv, _ in bt.bluez.calls
               if len(argv) > 2 and argv[1] == "remove"]
    assert removed == [REMOTE_MAC]


@pytest.mark.parametrize("blank", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
def test_a_blank_name_filter_is_refused_at_the_route(blank):
    """PATCH /api/bt-remote/config is the only writer of this field and it took
    any string up to 64 chars. Nothing downstream could tell a blank filter from
    an absent one."""
    from pydantic import ValidationError

    from backend.api.models import BtRemoteConfigRequest

    with pytest.raises(ValidationError):
        BtRemoteConfigRequest(device_name_filter=blank)


def test_a_name_filter_is_stored_stripped():
    """Surrounding spaces are the silent half: " ANTICATER " is in no device
    name, so the remote stops being adopted with nothing logged."""
    from backend.api.models import BtRemoteConfigRequest

    payload = BtRemoteConfigRequest(device_name_filter="  ANTICATER  ")
    assert payload.device_name_filter == "ANTICATER"


# ------------------------------------- the D-Bus filter, the instant-reconnect gate
def dbus_signal(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
                member="PropertiesChanged", interface="org.bluez.Device1",
                props=None, message_type=None):
    """One BlueZ PropertiesChanged signal, shaped as dbus_next delivers it."""
    from dbus_next.constants import MessageType

    body = [interface, {} if props is None else props, []]
    return types.SimpleNamespace(
        message_type=MessageType.SIGNAL if message_type is None else message_type,
        member=member, path=path, body=body)


def variant(value):
    return types.SimpleNamespace(value=value)


def test_a_connect_signal_queues_the_device_for_an_evdev_rescan(bt):
    """The whole point of the D-Bus listener: BlueZ auto-connects a trusted
    remote the moment it wakes, and this is what turns that into a scan. Without
    it the remote works again only at the next SCAN_INTERVAL — 30 s of a button
    that does nothing, with no error anywhere to say why."""
    bt.controller._on_dbus_message(dbus_signal(props={"Connected": variant(True)}))

    assert bt.controller._dbus_reconnect_queue.get_nowait() == \
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


@pytest.mark.parametrize("msg_kwargs", [
    {"props": {"Connected": variant(False)}},
    {"props": {"RSSI": variant(-60)}},
    {"props": {"ServicesResolved": variant(True)}},
    {"path": "/org/bluez/hci0", "props": {"Connected": variant(True)}},
    {"member": "InterfacesAdded", "props": {"Connected": variant(True)}},
    {"interface": "org.bluez.MediaPlayer1", "props": {"Connected": variant(True)}},
], ids=["disconnect", "rssi-churn", "services-resolved", "adapter-not-device",
        "other-member", "other-interface"])
def test_a_signal_that_is_not_a_device_connecting_queues_nothing(bt, msg_kwargs):
    """The queue holds one item (maxsize=1) and a hit costs a full evdev scan a
    second later. BlueZ emits RSSI and ServicesResolved continuously while a
    device is in range, and the A2DP source shares the adapter — so anything but
    a Device1 Connected->True must fall through."""
    bt.controller._on_dbus_message(dbus_signal(**msg_kwargs))

    assert bt.controller._dbus_reconnect_queue.empty()


def test_a_method_call_is_not_read_as_a_signal(bt):
    """`add_message_handler` sees every message on the bus, replies included."""
    from dbus_next.constants import MessageType

    bt.controller._on_dbus_message(dbus_signal(
        props={"Connected": variant(True)}, message_type=MessageType.METHOD_CALL))

    assert bt.controller._dbus_reconnect_queue.empty()


def test_a_second_connect_before_the_scan_ran_is_dropped_not_raised(bt):
    """The queue is maxsize=1 and this handler is a synchronous D-Bus callback:
    a QueueFull escaping it kills the listener session, and with it every instant
    reconnect until the outer loop notices. One pending scan covers both signals."""
    for _ in range(3):
        bt.controller._on_dbus_message(dbus_signal(props={"Connected": variant(True)}))

    assert bt.controller._dbus_reconnect_queue.qsize() == 1


# ----------------------------------------- the Search button, past its early return
@pytest.fixture
def instant(monkeypatch):
    """Neutralise the clock. _run_discovery spends 5 s scanning and 10 s waiting
    for BLE pairing; the yields still happen, only the waiting does not."""
    real_sleep = asyncio.sleep

    async def no_sleep(_delay, *args, **kwargs):
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)


@pytest.mark.asyncio
async def test_search_reconnects_a_bonded_remote_without_a_discovery_scan(bt, instant):
    """The ordinary press: the remote is bonded and asleep. BlueZ reconnects it
    on a plain `connect`, and running a full scan+pair on top would re-pair a
    device that is already bonded — 15 s of the panel spinning for nothing."""
    bt.controller.enabled = True
    bt.controller.running = True
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]
    bt.evdev.nodes["/dev/input/event5"] = FakeInputDevice("/dev/input/event5")

    result = await bt.controller.trigger_discovery()

    assert result["status"] == "success"
    assert "connect" in bt.bluez.argv_names()
    assert "<interactive>" not in bt.bluez.argv_names(), "a bonded remote paid for a full scan"
    assert bt.controller._monitored_paths == {"/dev/input/event5"}
    await bt.controller._stop_scanning()


@pytest.mark.asyncio
async def test_search_falls_through_to_a_full_scan_when_the_bond_is_stale(bt, instant):
    """A bond BlueZ still lists but the remote no longer honours: `connect` fails
    and the only way back is the scan+pair sequence. Returning not_found here
    would leave the user with a remote that can never be re-paired from the UI."""
    bt.controller.enabled = True
    bt.controller.running = True
    bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
    bt.bluez.connect_succeeds = False

    await bt.controller.trigger_discovery()

    assert bt.bluez.argv_names().count("<interactive>") == 2, \
        "no scan session then pair session — the stale bond is a dead end"


@pytest.mark.asyncio
async def test_search_reports_not_found_rather_than_success_when_nothing_answers(bt, instant):
    """The panel binds :loading and :disabled to `discovering` and clears it on
    the reply. A success with no device leaves it showing a remote that is not
    there; the run must also hand `discovering` back to false."""
    bt.controller.enabled = True
    bt.controller.running = True
    bt.bluez.paired = []
    bt.bluez.connected = []

    result = await bt.controller.trigger_discovery()

    assert result["status"] == "not_found"
    assert bt.controller._monitored_paths == set()
    assert bt.status()[-1].discovering is False


@pytest.mark.asyncio
async def test_search_on_a_disabled_controller_touches_no_adapter(bt, instant):
    """`enabled` false means the user turned the feature off. Discovery would
    scan and pair on an adapter the A2DP source is using."""
    bt.controller.enabled = False
    bt.controller.running = False

    result = await bt.controller.trigger_discovery()

    assert result["status"] == "error"
    assert bt.bluez.calls == []


# ------------------------------------------------------------------ the boot
@pytest.fixture(autouse=True)
def never_the_real_system_bus(monkeypatch):
    """The appliance's own BlueZ is on this machine's system bus.

    `read_battery_level` and `_connect_dbus_listener` both open one, so the
    module-level name is replaced by a raiser for the whole file: a test that
    forgets its own stand-in fails loudly instead of talking to the adapter
    that holds the owner's phone.
    """
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the appliance's real D-Bus system bus")

    monkeypatch.setattr(bt_remote_module, "MessageBus", refuse, raising=False)


class TestWhatBootDecides:
    """`initialize` was at 0 % — the method that decides, on every boot,
    whether the remote works at all."""

    @pytest.mark.asyncio
    async def test_an_enabled_remote_starts_scanning(self, bt):
        assert await bt.controller.initialize() is True

        assert bt.controller.running is True
        assert bt.controller._scan_task is not None
        assert bt.controller._discovery_task is not None
        await bt.controller.cleanup()

    @pytest.mark.asyncio
    async def test_a_disabled_remote_hangs_up_on_what_is_connected(self, bt):
        """Disabled means the keys must stop reaching the volume. A remote
        already connected when the setting was flipped off — or connected by
        BlueZ auto-connect before the backend came up — keeps working
        otherwise, since nothing else drops the link."""
        bt.settings.get_setting = AsyncMock(return_value={
            "enabled": False,
            "device_name_filter": "ANTICATER",
            "key_map": dict(bt_remote_module.DEFAULT_KEY_MAP),
        })
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]

        assert await bt.controller.initialize() is True

        assert bt.controller.running is False
        assert bt.controller._scan_task is None
        assert ["devices", "disconnect"] == [
            argv[1] for argv, _ in bt.bluez.calls
        ]

    @pytest.mark.asyncio
    async def test_the_bond_is_kept_when_the_remote_is_switched_off(self, bt):
        """Only the explicit "unpair" removes it: a toggle that also removed
        the bond would make re-enabling need a full 15 s re-pair."""
        bt.settings.get_setting = AsyncMock(return_value={"enabled": False})
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]

        await bt.controller.initialize()

        assert "remove" not in bt.bluez.argv_names()

    @pytest.mark.asyncio
    async def test_a_host_without_evdev_is_not_a_failure(self, bt, monkeypatch):
        monkeypatch.setattr(bt_remote_module, "EVDEV_AVAILABLE", False)

        assert await bt.controller.initialize() is True

        assert bt.controller.running is False
        assert bt.bluez.calls == []

    @pytest.mark.asyncio
    async def test_the_dbus_listener_is_only_started_when_dbus_is_there(
        self, bt, monkeypatch
    ):
        monkeypatch.setattr(bt_remote_module, "DBUS_AVAILABLE", False)
        await bt.controller.initialize()
        assert bt.controller._dbus_listener_task is None
        await bt.controller.cleanup()


class TestTheSettingsKeysTheRemoteReadsThrough:
    """`_load_config_from_settings` was at 0 %. Every value is a
    `.get(key, default)`, so a renamed key does not fail — it answers the
    default, and the remote quietly stops being the thing it was configured as.
    """

    async def _load(self, bt, stored):
        bt.settings.get_setting = AsyncMock(return_value=stored)
        await bt.controller._load_config_from_settings()
        return bt.settings.get_setting.await_args.args[0]

    @pytest.mark.asyncio
    async def test_the_three_values_come_from_hardware_bt_remote(self, bt):
        key = await self._load(bt, {
            "enabled": True,
            "device_name_filter": "MYREMOTE",
            "key_map": {"200": "click"},
        })

        assert key == "hardware.bt_remote"
        assert bt.controller.enabled is True
        assert bt.controller.device_name_filter == "MYREMOTE"
        assert bt.controller.key_map == {"200": "click"}

    @pytest.mark.asyncio
    async def test_a_unit_that_never_configured_a_remote_keeps_its_defaults(self, bt):
        """The section is absent from `SettingsService.defaults`, so a fresh
        unit answers nothing here — and the constructor's values must survive
        rather than be overwritten with an empty config."""
        await self._load(bt, None)

        assert bt.controller.enabled is False
        assert bt.controller.device_name_filter == bt_remote_module.DEFAULT_DEVICE_FILTER
        assert bt.controller.key_map == bt_remote_module.DEFAULT_KEY_MAP

    @pytest.mark.asyncio
    async def test_a_remote_with_no_stored_switch_stays_off(self, bt):
        """The default is False on purpose: an accessory nobody configured must
        not start scanning and pairing on its own."""
        await self._load(bt, {"device_name_filter": "MYREMOTE"})
        assert bt.controller.enabled is False

    @pytest.mark.parametrize("stored_map", [{}, None, "115:volume_up"])
    @pytest.mark.asyncio
    async def test_an_unusable_key_map_leaves_the_default_in_place(self, bt, stored_map):
        """`_is_bt_hid_device` intersects the device's keys with this map, so an
        empty one matches NO device — the remote would connect in BlueZ and
        never be adopted, with nothing logged above debug. The settings
        validator lets `key_map: {}` through, which is what makes this reachable.
        """
        await self._load(bt, {"enabled": True, "key_map": stored_map})

        assert bt.controller.key_map == bt_remote_module.DEFAULT_KEY_MAP


class TestBatteryOverDbus:
    """`read_battery_level` was at 0 %. It is a one-shot system-bus read, and
    `settingsStore.fetchBtRemoteBattery()` fires it the moment a remote
    connects — so it runs unattended on a live BlueZ."""

    @pytest.fixture
    def dbus(self, monkeypatch):
        state = types.SimpleNamespace(
            introspected=[], value=87, get_error=None, disconnected=0,
        )

        class FakeProps:
            async def call_get(self, interface, prop):
                state.asked = (interface, prop)
                if state.get_error:
                    raise state.get_error
                return types.SimpleNamespace(value=state.value)

        class FakeObject:
            def get_interface(self, name):
                state.interface = name
                return FakeProps()

        class FakeBus:
            async def introspect(self, service, path):
                state.introspected.append((service, path))
                return object()

            def get_proxy_object(self, service, path, _introspection):
                state.proxied = (service, path)
                return FakeObject()

            def disconnect(self):
                state.disconnected += 1

        class FakeMessageBus:
            def __init__(self, **kwargs):
                state.bus_type = kwargs.get("bus_type")

            async def connect(self):
                return FakeBus()

        monkeypatch.setattr(bt_remote_module, "DBUS_AVAILABLE", True)
        monkeypatch.setattr(bt_remote_module, "MessageBus", FakeMessageBus)
        return state

    @pytest.mark.asyncio
    async def test_the_percentage_is_read_off_the_bluez_battery_interface(
        self, bt, dbus
    ):
        assert await bt.controller.read_battery_level(REMOTE_MAC.lower()) == 87

        assert dbus.bus_type is bt_remote_module.BusType.SYSTEM
        assert dbus.proxied == (
            "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        )
        assert dbus.asked == ("org.bluez.Battery1", "Percentage")

    def test_the_object_path_is_the_uppercased_mac_with_underscores(self):
        """BlueZ names its device objects that way; a lowercase or
        colon-separated path introspects to nothing and every read answers
        None — a battery that is simply never shown."""
        assert bt_remote_module.BtRemoteController._mac_to_dbus_path(
            "aa:bb:cc:dd:ee:ff"
        ) == "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"

    @pytest.mark.asyncio
    async def test_a_remote_with_no_battery_service_answers_none(self, bt, dbus):
        """Not every BLE HID exposes Battery1, and a sleeping one exposes
        nothing at all. The panel hides the gauge on None; raising here would
        turn the whole `/battery` route into a 500."""
        dbus.get_error = RuntimeError("No such interface 'org.bluez.Battery1'")

        assert await bt.controller.read_battery_level(REMOTE_MAC) is None

    @pytest.mark.asyncio
    async def test_the_bus_is_closed_whether_the_read_worked_or_not(self, bt, dbus):
        """One connection per read, and the route makes one read per device:
        a leaked bus per poll ends as a file-descriptor exhaustion days later."""
        await bt.controller.read_battery_level(REMOTE_MAC)
        dbus.get_error = RuntimeError("gone")
        await bt.controller.read_battery_level(REMOTE_MAC)

        assert dbus.disconnected == 2

    @pytest.mark.asyncio
    async def test_a_host_without_dbus_answers_none_without_connecting(
        self, bt, monkeypatch
    ):
        """None is also what the `except` answers, so the absence of a
        connection attempt is the only thing that separates the two."""
        attempts = MagicMock(side_effect=RuntimeError("no bus here"))
        monkeypatch.setattr(bt_remote_module, "DBUS_AVAILABLE", False)
        monkeypatch.setattr(bt_remote_module, "MessageBus", attempts)

        assert await bt.controller.read_battery_level(REMOTE_MAC) is None

        attempts.assert_not_called()


class TestWhatTheScanRefusesToAdopt:
    """The three rejection arms of `_is_bt_hid_device`, all at 0 %.

    Only the name filter had a test. The other three decide, silently, that a
    device is not the remote — and a false rejection is a remote that pairs in
    BlueZ and never works.
    """

    @pytest.mark.asyncio
    async def test_a_device_with_no_key_events_is_not_a_remote(self, bt):
        """BLE HID exposes several evdev nodes per remote and only some carry
        EV_KEY; the others are consumer-control or vendor nodes."""
        bt.controller.running = True
        node = FakeInputDevice("/dev/input/event9")
        node.capabilities = lambda verbose=False: {}
        bt.evdev.nodes["/dev/input/event9"] = node
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]

        await bt.controller._scan_devices()

        assert bt.controller._monitored_paths == set()
        assert node.closed, "a rejected node must not be left open"

    @pytest.mark.asyncio
    async def test_a_device_with_no_address_is_not_a_remote(self, bt):
        """`uniq` is empty for built-in inputs — the Pi's own power button and
        any USB keyboard. Adopting one would send its keys to the volume."""
        bt.controller.running = True
        bt.evdev.nodes["/dev/input/event0"] = FakeInputDevice(
            "/dev/input/event0", name="ANTICATER-lookalike", uniq=""
        )

        await bt.controller._scan_devices()

        assert bt.controller._monitored_paths == set()

    @pytest.mark.asyncio
    async def test_a_device_sharing_no_configured_keycode_is_not_a_remote(self, bt):
        bt.controller.running = True
        bt.evdev.nodes["/dev/input/event9"] = FakeInputDevice(
            "/dev/input/event9", keys=(1, 2, 3)
        )
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]

        await bt.controller._scan_devices()

        assert bt.controller._monitored_paths == set()


class TestAScanThatCannotSeeTheKernelTable:
    """`_scan_devices`'s error arms were at 0 %.

    The scan runs every 30 s for the life of the unit and each arm returns
    quietly; what matters is that none of them leaves the monitored set
    half-updated or drops a node that is still alive.
    """

    @pytest.mark.asyncio
    async def test_an_unreadable_dev_input_leaves_the_known_remotes_alone(self, bt):
        """A transient EACCES must not read as "every remote disappeared" — the
        panel would blink to disconnected and back on the next pass."""
        bt.controller.running = True
        await connect_remote(bt)
        before = set(bt.controller._monitored_paths)
        assert before, "the remote has to be adopted first for this to say anything"
        bt.evdev.list_devices = lambda: (_ for _ in ()).throw(OSError("EACCES"))
        bt.broadcasts.clear()

        await bt.controller._scan_devices()

        assert bt.controller._monitored_paths == before
        assert bt.status() == []
        await bt.controller._stop_scanning()

    @pytest.mark.asyncio
    async def test_an_unexpected_listing_fault_is_a_warning_not_a_debug(
        self, bt, caplog
    ):
        """OSError is ordinary; anything else is a fault of ours and used to be
        indistinguishable from a dev host with no /dev/input."""
        bt.controller.running = True
        bt.evdev.list_devices = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

        with caplog.at_level(logging.WARNING):
            await bt.controller._scan_devices()

        assert "Unexpected error listing input devices" in caplog.text

    @pytest.mark.asyncio
    async def test_a_node_that_vanishes_between_listing_and_opening_is_skipped(
        self, bt
    ):
        """The window is real: BLE nodes appear and go in the same second."""
        bt.controller.running = True
        bt.evdev.nodes["/dev/input/event9"] = FakeInputDevice("/dev/input/event9")
        real_open = bt.evdev.InputDevice

        def open_or_vanish(path):
            if path == "/dev/input/event9":
                raise OSError("no such device")
            return real_open(path)

        bt.evdev.InputDevice = open_or_vanish

        await bt.controller._scan_devices()

        assert bt.controller._monitored_paths == set()

    @pytest.mark.asyncio
    async def test_an_unexpected_open_fault_is_a_warning(self, bt, caplog):
        bt.controller.running = True
        bt.evdev.nodes["/dev/input/event9"] = FakeInputDevice("/dev/input/event9")
        bt.evdev.InputDevice = lambda _path: (_ for _ in ()).throw(RuntimeError("boom"))

        with caplog.at_level(logging.WARNING):
            await bt.controller._scan_devices()

        assert "Unexpected error opening device" in caplog.text

    @pytest.mark.asyncio
    async def test_a_node_that_dies_while_being_checked_is_closed_and_skipped(self, bt):
        bt.controller.running = True
        node = FakeInputDevice("/dev/input/event9")
        node.capabilities = lambda verbose=False: (_ for _ in ()).throw(OSError("gone"))
        bt.evdev.nodes["/dev/input/event9"] = node

        await bt.controller._scan_devices()

        assert node.closed
        assert bt.controller._monitored_paths == set()

    @pytest.mark.asyncio
    async def test_a_fault_in_our_own_matching_is_a_warning_and_closes_the_node(
        self, bt, caplog
    ):
        """A key_map with a non-numeric key makes `int(k)` raise here. At debug
        it silently ignored every remote for the life of the process."""
        bt.controller.running = True
        node = FakeInputDevice("/dev/input/event9")
        bt.evdev.nodes["/dev/input/event9"] = node
        bt.controller.key_map = {"not-a-keycode": "click"}

        with caplog.at_level(logging.WARNING):
            await bt.controller._scan_devices()

        assert node.closed
        assert "Unexpected error checking device" in caplog.text


class TestTheLinesBluetoothctlPrintsThatAreNotDevices:
    """`_get_matching_devices`'s three parse guards, all at 0 %.

    `bluetoothctl devices` prints its banner and agent chatter on the same
    stream. A line taken for a device feeds a MAC-shaped string to
    `bluetoothctl remove`.
    """

    @pytest.mark.parametrize("line", [
        "Agent registered",
        "Device",
        "Device AA:BB:CC:DD:EE:FF",
        "Device not-a-mac ANTICATER VK-01",
        "[CHG] Device AA:BB:CC:DD:EE:FF ANTICATER VK-01",
        # `bluetoothctl list` prints the adapter in the same three-token shape,
        # with a real MAC — only the row type separates it from a device.
        "Controller DC:A6:32:7E:D3:43 ANTICATER",
    ])
    @pytest.mark.asyncio
    async def test_a_line_that_is_not_a_device_row_is_not_a_device(self, bt, line):
        async def one_line(*_argv, **_kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(line.encode() + b"\n", b""))
            proc.wait = AsyncMock(return_value=0)
            proc.kill = MagicMock()
            return proc

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(asyncio, "create_subprocess_exec", one_line)
            assert await bt.controller._get_matching_devices("Paired") == []

    @pytest.mark.asyncio
    async def test_a_well_formed_row_still_gets_through(self, bt):
        """The guards above are only worth anything next to this one."""
        bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
        assert await bt.controller._get_matching_devices("Paired") == [
            (REMOTE_MAC, REMOTE_NAME)
        ]


class TestBluetoothctlThatDoesNotAnswer:
    """`_run_bluetoothctl`'s timeout and reap arms were at 0 %.

    bluetoothctl blocks on a wedged adapter. Left unreaped it becomes a zombie
    per scan cycle, every 30 s, forever.
    """

    def _hanging(self, bt, monkeypatch):
        proc = MagicMock()
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()

        async def spawn(*_argv, **_kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        return proc

    @pytest.mark.asyncio
    async def test_a_hung_listing_answers_empty_rather_than_hanging(
        self, bt, monkeypatch
    ):
        self._hanging(bt, monkeypatch)
        assert await bt.controller._run_bluetoothctl(
            "devices", "Paired", capture_stdout=True
        ) == ""

    @pytest.mark.asyncio
    async def test_a_hung_action_answers_failure(self, bt, monkeypatch):
        self._hanging(bt, monkeypatch)
        assert await bt.controller._run_bluetoothctl("connect", REMOTE_MAC) is False

    @pytest.mark.asyncio
    async def test_a_process_left_running_is_killed_and_reaped(self, bt, monkeypatch):
        proc = self._hanging(bt, monkeypatch)

        await bt.controller._run_bluetoothctl("connect", REMOTE_MAC)

        proc.kill.assert_called_once_with()
        proc.wait.assert_awaited_once_with()


# --------------------------------------------------------- the D-Bus session
class FakeMessageBus:
    """One BlueZ system-bus session. `add_match_reply` is what AddMatch answers."""

    def __init__(self, state):
        self._state = state

    async def connect(self):
        self._state.connects += 1
        return self._state.bus


class FakeBus:
    def __init__(self, state):
        self._state = state
        self._dropped = asyncio.Event()

    async def call(self, message):
        self._state.calls.append(message)
        return types.SimpleNamespace(
            message_type=self._state.add_match_reply, body=["Access denied"]
        )

    def add_message_handler(self, handler):
        self._state.handlers.append(handler)

    async def wait_for_disconnect(self):
        await self._dropped.wait()

    def drop(self):
        self._dropped.set()

    def disconnect(self):
        self._state.disconnects += 1


@pytest.fixture
def dbus_session(bt, monkeypatch):
    """A stand-in BlueZ session bus wired into the controller's module."""
    state = types.SimpleNamespace(
        calls=[], handlers=[], connects=0, disconnects=0,
        add_match_reply=bt_remote_module.MessageType.METHOD_RETURN,
    )
    state.bus = FakeBus(state)
    monkeypatch.setattr(bt_remote_module, "DBUS_AVAILABLE", True)
    monkeypatch.setattr(
        bt_remote_module, "MessageBus", lambda **_kw: FakeMessageBus(state)
    )
    return state


class TestTheReconnectListener:
    """`_connect_dbus_listener` was at 0 % — all 25 lines.

    It is what makes a BLE remote usable at all: the device deep-sleeps to save
    its battery, BlueZ auto-connects it when a key is pressed, and this is what
    notices in time to adopt the new evdev node. Without it the remote is dead
    until the 30 s fallback scan, which is long enough that the press is lost.
    """

    async def _session(self, bt, timeout=0.5):
        """Run one listener session to completion or until it parks."""
        task = asyncio.create_task(bt.controller._connect_dbus_listener())
        await asyncio.sleep(0)
        return task

    @pytest.mark.asyncio
    async def test_the_match_rule_subscribes_to_bluez_device_properties(
        self, bt, dbus_session
    ):
        """A broader rule wakes the handler on every signal on the bus; a
        narrower one never fires. The rule is the whole subscription."""
        bt.controller.running = True
        task = await self._session(bt)

        assert len(dbus_session.calls) == 1
        message = dbus_session.calls[0]
        assert message.member == "AddMatch"
        assert message.body == [bt_remote_module._DBUS_MATCH_RULE]
        assert dbus_session.handlers == [bt.controller._on_dbus_message]

        bt.controller.running = False
        dbus_session.bus.drop()
        await task

    @pytest.mark.asyncio
    async def test_a_refused_subscription_ends_the_session_and_closes_the_bus(
        self, bt, dbus_session
    ):
        """The outer loop reconnects on the raise. Carrying on instead would
        leave a live bus that never delivers a signal — the remote silently
        falls back to the 30 s scan for the life of the process."""
        dbus_session.add_match_reply = bt_remote_module.MessageType.ERROR
        bt.controller.running = True

        # Bounded on purpose: without the raise this session parks on the
        # reconnect queue forever, so the mutation that drops it would hang the
        # run instead of reddening it (the B4 "bound the double" lesson).
        with pytest.raises(RuntimeError, match="AddMatch failed"):
            await asyncio.wait_for(bt.controller._connect_dbus_listener(), timeout=2)

        assert dbus_session.handlers == []
        assert dbus_session.disconnects == 1

    @pytest.mark.asyncio
    async def test_a_connect_signal_triggers_a_rescan_after_the_settle_delay(
        self, bt, dbus_session, monkeypatch
    ):
        """The evdev node does not exist yet when the signal lands — the kernel
        creates it a moment later. Scanning immediately finds nothing."""
        slept = []
        real_sleep = asyncio.sleep

        async def record(delay, *args, **kwargs):
            slept.append(delay)
            return await real_sleep(0)

        scans = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", record)
        monkeypatch.setattr(bt.controller, "_scan_devices", scans)
        bt.controller.running = True

        task = asyncio.create_task(bt.controller._connect_dbus_listener())
        await real_sleep(0)
        bt.controller._dbus_reconnect_queue.put_nowait("/org/bluez/hci0/dev_X")
        for _ in range(20):
            await real_sleep(0)
            if scans.await_count:
                break

        scans.assert_awaited()
        assert bt_remote_module.DBUS_EVDEV_SETTLE in slept

        bt.controller.running = False
        dbus_session.bus.drop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_a_dropped_bus_returns_so_the_outer_loop_can_reconnect(
        self, bt, dbus_session
    ):
        """BlueZ restarts on an adapter reset. Waiting on the queue forever
        after that is a listener that exists and hears nothing."""
        bt.controller.running = True
        task = asyncio.create_task(bt.controller._connect_dbus_listener())
        await asyncio.sleep(0)

        dbus_session.bus.drop()
        await asyncio.wait_for(task, timeout=1)

        assert dbus_session.disconnects == 1

    @pytest.mark.asyncio
    async def test_the_outer_loop_reconnects_after_a_failed_session(
        self, bt, dbus_session, monkeypatch
    ):
        """One refused AddMatch — BlueZ not up yet at boot — must not end the
        listener for good."""
        delays = []
        attempts = []
        real_sleep = asyncio.sleep

        async def record(delay, *args, **kwargs):
            delays.append(delay)
            return await real_sleep(0)

        async def session():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("D-Bus AddMatch failed")
            bt.controller.running = False

        monkeypatch.setattr(asyncio, "sleep", record)
        monkeypatch.setattr(bt.controller, "_connect_dbus_listener", session)
        bt.controller.running = True

        await bt.controller._run_dbus_listener()

        assert len(attempts) == 2, "the listener gave up after one failure"
        assert delays == [bt_remote_module.DBUS_RECONNECT_DELAY]

    @pytest.mark.asyncio
    async def test_a_cancelled_listener_does_not_reconnect(self, bt, monkeypatch):
        """Teardown cancels this task; swallowing the cancellation would make
        it immortal — it would log, wait 5 s, reconnect, and hold the system
        bus open past shutdown, forever.

        The dedicated `except asyncio.CancelledError: raise` arm is inert on
        its own (CancelledError is a BaseException, so the broad arm below
        cannot reach it either); what this catches is the arm below being
        widened. Bounded because that regression loops rather than fails.
        """
        async def session():
            raise asyncio.CancelledError

        monkeypatch.setattr(bt.controller, "_connect_dbus_listener", session)
        bt.controller.running = True

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(bt.controller._run_dbus_listener(), timeout=2)


# ------------------------------------------------------ the background loops
class TestThePeriodicScan:
    """`_periodic_scan` was at 0 %. It is the fallback for everything the D-Bus
    listener misses, so it is what recovers a unit whose bus session died."""

    async def _run(self, bt, monkeypatch, ticks):
        delays = []
        real_sleep = asyncio.sleep

        async def record(delay, *args, **kwargs):
            delays.append(delay)
            if len(delays) >= ticks:
                bt.controller.running = False
            return await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", record)
        bt.controller.running = True
        await bt.controller._periodic_scan()
        return delays

    @pytest.mark.asyncio
    async def test_the_first_scan_waits_for_stale_nodes_to_disappear(
        self, bt, monkeypatch
    ):
        """A disable/re-enable leaves kernel nodes behind for a moment, and
        `_scan_devices` refuses any node BlueZ does not report connected — so
        scanning at once adopts nothing and then waits a full interval."""
        scans = AsyncMock()
        monkeypatch.setattr(bt.controller, "_scan_devices", scans)

        delays = await self._run(bt, monkeypatch, ticks=1)

        assert delays[0] == 2
        assert scans.await_count == 0, "the wait comes before the first scan"

    @pytest.mark.asyncio
    async def test_it_scans_once_per_interval(self, bt, monkeypatch):
        scans = AsyncMock()
        monkeypatch.setattr(bt.controller, "_scan_devices", scans)

        delays = await self._run(bt, monkeypatch, ticks=3)

        assert delays == [2, bt_remote_module.SCAN_INTERVAL, bt_remote_module.SCAN_INTERVAL]
        assert scans.await_count == 2

    @pytest.mark.asyncio
    async def test_a_failing_scan_does_not_end_the_loop(self, bt, monkeypatch, caplog):
        scans = AsyncMock(side_effect=[RuntimeError("kernel table gone"), None])
        monkeypatch.setattr(bt.controller, "_scan_devices", scans)

        with caplog.at_level(logging.ERROR):
            delays = await self._run(bt, monkeypatch, ticks=3)

        assert scans.await_count == 2, "the loop kept scanning after the failure"
        assert len(delays) == 3
        assert "Error scanning BT HID devices" in caplog.text


class TestThePeriodicDiscovery:
    """`_periodic_discovery` was at 0 %. It is the unattended half of pairing:
    it reconnects a bonded remote after a deep sleep, and runs a full
    scan+pair when there is no bond at all."""

    async def _run(self, bt, monkeypatch, ticks):
        delays = []
        real_sleep = asyncio.sleep

        async def record(delay, *args, **kwargs):
            delays.append(delay)
            if len(delays) >= ticks:
                bt.controller.running = False
            return await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", record)
        bt.controller.running = True
        await bt.controller._periodic_discovery()
        return delays

    @pytest.mark.asyncio
    async def test_a_bonded_remote_is_reconnected_not_re_paired(self, bt, monkeypatch):
        """A full discovery on a device that is already bonded is 15 s of
        adapter time for nothing, and it re-pairs a remote that never lost its
        bond."""
        bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]
        pair = AsyncMock()
        scans = AsyncMock()
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", pair)
        monkeypatch.setattr(bt.controller, "_scan_devices", scans)

        await self._run(bt, monkeypatch, ticks=2)

        assert "connect" in bt.bluez.argv_names()
        pair.assert_not_awaited()
        # The reconnect is only half of it: BlueZ brings the link back but the
        # evdev node is new, and nothing adopts it until something scans.
        scans.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_bond_at_all_runs_the_full_discovery(self, bt, monkeypatch):
        bt.bluez.paired = []
        pair = AsyncMock()
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", pair)

        await self._run(bt, monkeypatch, ticks=2)

        pair.assert_awaited()
        assert "connect" not in bt.bluez.argv_names()

    @pytest.mark.asyncio
    async def test_a_remote_already_monitored_is_left_alone(self, bt, monkeypatch):
        """Scanning and pairing over a working remote is what makes the adapter
        drop it mid-use."""
        bt.controller._monitored_paths.add("/dev/input/event5")
        pair = AsyncMock()
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", pair)

        await self._run(bt, monkeypatch, ticks=2)

        pair.assert_not_awaited()
        assert bt.bluez.calls == []

    @pytest.mark.asyncio
    async def test_the_first_attempts_retry_fast_then_settle_to_the_long_cycle(
        self, bt, monkeypatch
    ):
        """BlueZ is often not ready when the backend comes up, and 300 s of
        silence after a cold boot is a remote that looks broken."""
        bt.bluez.paired = []
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", AsyncMock())

        delays = await self._run(bt, monkeypatch, ticks=6)

        assert delays == [6, 15, 15, 15, bt_remote_module.DISCOVERY_INTERVAL,
                          bt_remote_module.DISCOVERY_INTERVAL]

    @pytest.mark.asyncio
    async def test_a_connected_remote_stops_the_fast_retries(self, bt, monkeypatch):
        """The boot budget is for finding the remote; once found, going on
        polling at 15 s keeps the adapter busy for nothing."""
        async def adopt():
            bt.controller._monitored_paths.add("/dev/input/event5")

        bt.bluez.paired = []
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", AsyncMock(side_effect=adopt))

        delays = await self._run(bt, monkeypatch, ticks=3)

        assert delays == [6, bt_remote_module.DISCOVERY_INTERVAL,
                          bt_remote_module.DISCOVERY_INTERVAL]

    @pytest.mark.asyncio
    async def test_a_failing_cycle_does_not_end_the_loop(self, bt, monkeypatch, caplog):
        pair = AsyncMock(side_effect=[RuntimeError("adapter down"), None])
        bt.bluez.paired = []
        monkeypatch.setattr(bt.controller, "_auto_discover_and_pair", pair)

        with caplog.at_level(logging.ERROR):
            await self._run(bt, monkeypatch, ticks=3)

        assert pair.await_count == 2
        assert "Error in BT auto-discovery" in caplog.text


class TestTheDiscoveryItself:
    """`_auto_discover_and_pair` and `_run_discovery`: the panel's spinner and
    the trust/pair/connect session."""

    @pytest.mark.asyncio
    async def test_the_panel_is_told_when_discovery_starts_and_stops(
        self, bt, monkeypatch
    ):
        """`discovering` drives both :loading and :disabled on the Search CTA,
        so a start with no matching stop is an unclickable button."""
        seen = []
        monkeypatch.setattr(
            bt.controller, "_run_discovery",
            AsyncMock(side_effect=lambda: seen.append(bt.controller._discovering)),
        )

        await bt.controller._auto_discover_and_pair()

        assert seen == [True]
        assert bt.controller._discovering is False
        assert [e.discovering for e in bt.status()] == [True, False]

    @pytest.mark.asyncio
    async def test_a_discovery_that_raises_still_clears_the_spinner(
        self, bt, monkeypatch
    ):
        monkeypatch.setattr(
            bt.controller, "_run_discovery", AsyncMock(side_effect=RuntimeError("boom"))
        )

        with pytest.raises(RuntimeError):
            await bt.controller._auto_discover_and_pair()

        assert bt.controller._discovering is False
        assert bt.status()[-1].discovering is False

    @pytest.mark.asyncio
    async def test_a_second_discovery_while_one_runs_is_dropped(self, bt, monkeypatch):
        run = AsyncMock()
        monkeypatch.setattr(bt.controller, "_run_discovery", run)
        bt.controller._discovering = True

        await bt.controller._auto_discover_and_pair()

        run.assert_not_awaited()
        assert bt.status() == [], "no spinner event either"

    @pytest.mark.asyncio
    async def test_trust_pair_and_connect_run_in_one_bluetoothctl_session(
        self, bt, instant
    ):
        """BLE devices go unavailable between separate invocations, so three
        calls would pair a device the third can no longer reach.

        The remote here is *discovered, not bonded*: this path only runs when
        there is no bond, so reading the bonded list instead of the daemon
        cache would find nothing and the pairing would never happen.
        """
        bt.controller.running = True
        bt.bluez.paired = []
        bt.bluez.discovered = [(REMOTE_MAC, REMOTE_NAME)]
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]  # after the pair session

        await bt.controller._run_discovery()

        assert bt.bluez.written[-1] == [
            f"trust {REMOTE_MAC}\n", f"pair {REMOTE_MAC}\n", f"connect {REMOTE_MAC}\nquit\n",
        ]

    @pytest.mark.asyncio
    async def test_a_pairing_bluez_did_not_take_is_reported_not_assumed(
        self, bt, instant, caplog
    ):
        """`bluetoothctl pair` prints its outcome and exits 0 either way, so
        the daemon's own view is the only verdict. Assuming success rescans for
        a node that will never appear and reports a remote that is not there."""
        bt.controller.running = True
        bt.bluez.paired = []
        bt.bluez.discovered = [(REMOTE_MAC, REMOTE_NAME)]
        bt.bluez.connected = []  # BlueZ never took it
        scans = AsyncMock()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bt.controller, "_scan_devices", scans)
            with caplog.at_level(logging.WARNING):
                await bt.controller._run_discovery()

        scans.assert_not_awaited()
        assert "Pairing/connect failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_pairing_bluez_took_is_followed_by_a_rescan(self, bt, instant):
        bt.controller.running = True
        bt.bluez.paired = []
        bt.bluez.discovered = [(REMOTE_MAC, REMOTE_NAME)]
        bt.bluez.connected = [(REMOTE_MAC, REMOTE_NAME)]
        scans = AsyncMock()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bt.controller, "_scan_devices", scans)
            await bt.controller._run_discovery()

        scans.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_a_scan_that_finds_nothing_matching_stops_before_pairing(
        self, bt, instant
    ):
        bt.controller.running = True
        bt.bluez.paired = []
        bt.bluez.connected = []

        await bt.controller._run_discovery()

        assert bt.bluez.written == [["scan on\n", "scan off\nquit\n"]]

    @pytest.mark.asyncio
    async def test_a_remote_adopted_during_the_scan_ends_the_discovery(
        self, bt, instant
    ):
        """`_periodic_scan` runs concurrently; pairing on top of a node it just
        adopted drops the link the user is already using."""
        bt.controller.running = True
        bt.controller._monitored_paths.add("/dev/input/event5")
        bt.bluez.paired = [(REMOTE_MAC, REMOTE_NAME)]

        await bt.controller._run_discovery()

        assert bt.bluez.written == [["scan on\n", "scan off\nquit\n"]]


# ---------------------------------------------------------------- teardown
class TestStopScanning:
    @pytest.mark.asyncio
    async def test_every_task_and_every_monitored_node_is_dropped(self, bt):
        await bt.controller.initialize()
        await connect_remote(bt)
        assert bt.controller._monitored_paths

        await bt.controller.cleanup()

        assert bt.controller.running is False
        assert bt.controller._scan_task is None
        assert bt.controller._discovery_task is None
        assert bt.controller._dbus_listener_task is None
        assert bt.controller._monitor_tasks == {}
        assert bt.controller._monitored_paths == set()
        assert bt.controller._device_info == {}

    @pytest.mark.asyncio
    async def test_a_pending_reconnect_event_does_not_survive_the_stop(self, bt):
        """The queue holds one slot. A stale path left in it makes the next
        session scan immediately for a device that disconnected long ago."""
        bt.controller._dbus_reconnect_queue.put_nowait("/org/bluez/hci0/dev_X")

        await bt.controller._stop_scanning()

        assert bt.controller._dbus_reconnect_queue.empty()

    @pytest.mark.asyncio
    async def test_both_collaborators_are_drained(self, bt, monkeypatch):
        """The dispatcher holds a 400 ms multi-click timer and the accumulator a
        drain task: left alive they act on a remote that is already gone."""
        dispatcher = AsyncMock()
        volume = AsyncMock()
        monkeypatch.setattr(bt.controller._dispatcher, "cleanup", dispatcher)
        monkeypatch.setattr(bt.controller._volume, "cleanup", volume)

        await bt.controller.cleanup()

        dispatcher.assert_awaited_once_with()
        volume.assert_awaited_once_with()


class TestUnknownActions:
    @pytest.mark.asyncio
    async def test_an_action_the_dispatcher_does_not_know_moves_no_volume(
        self, bt, monkeypatch
    ):
        """`key_map` is operator-editable through the API; a typo must not
        become a volume change.

        The assertion is on the accumulator rather than on the volume service:
        `accumulate` batches into a task, so nothing is *awaited* in this tick
        either way and `assert_not_awaited` would pass on the regression.
        """
        deltas = []
        monkeypatch.setattr(bt.controller._volume, "accumulate", deltas.append)

        await bt.controller._dispatch_action("volume_sideways")

        assert deltas == []

    @pytest.mark.asyncio
    async def test_a_volume_service_that_raises_is_logged_not_propagated(
        self, bt, caplog
    ):
        """This runs inside the per-device monitor task; an escaping exception
        ends it and the remote stops working until it reconnects."""
        bt.volume_service.volume_config = None  # attribute access raises

        with caplog.at_level(logging.ERROR):
            await bt.controller._dispatch_action("volume_up")

        assert "Error dispatching BT remote action" in caplog.text


class TestTheRemainingEntryGuards:
    """Small arms that decide whether a whole action happens at all."""

    @pytest.mark.asyncio
    async def test_a_new_key_map_reaches_the_scanner_and_the_store(self, bt):
        """The map is both the dispatch table and the adoption filter, so a
        write that lands in settings.json but not in memory leaves the running
        scanner matching devices by the old codes."""
        await bt.controller.update_config({"key_map": {"200": "click"}})

        assert bt.controller.key_map == {"200": "click"}
        stored = bt.settings.set_setting.await_args.args[1]
        assert stored["key_map"] == {"200": "click"}

    @pytest.mark.parametrize("bad", ["115:volume_up", None, 7])
    @pytest.mark.asyncio
    async def test_a_key_map_that_is_not_a_map_is_ignored(self, bt, bad):
        await bt.controller.update_config({"key_map": bad})
        assert bt.controller.key_map == bt_remote_module.DEFAULT_KEY_MAP

    @pytest.mark.asyncio
    async def test_search_on_a_host_without_evdev_says_so(self, bt, monkeypatch):
        monkeypatch.setattr(bt_remote_module, "EVDEV_AVAILABLE", False)

        assert await bt.controller.trigger_discovery() == {
            "status": "error", "message": "evdev not available",
        }
        assert bt.bluez.calls == []

    @pytest.mark.asyncio
    async def test_unpair_on_a_host_without_evdev_touches_no_bond(self, bt, monkeypatch):
        """A `bluetoothctl remove` issued on a box with no adapter is the same
        command that removes the A2DP phone's bond on one that has."""
        monkeypatch.setattr(bt_remote_module, "EVDEV_AVAILABLE", False)

        assert await bt.controller.forget_remote() == {
            "status": "error", "message": "evdev not available",
        }
        assert bt.bluez.calls == []


class TestTheDeviceMonitor:
    """`_monitor_device`'s exit paths — what happens when a remote goes away."""

    @pytest.mark.asyncio
    async def test_a_non_key_event_is_not_an_action(self, bt):
        """evdev delivers SYN and MSC on the same node; treating either as a
        keypress would fire the volume on every report."""
        bt.controller.running = True
        node = await connect_remote(bt)
        deltas = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bt.controller._volume, "accumulate", deltas.append)
            await node.events.put(types.SimpleNamespace(type=99, code=115, value=1))
            await node.events.put(key_event(115))
            for _ in range(20):
                await asyncio.sleep(0)
                if deltas:
                    break

        assert deltas == [2.0], "only the EV_KEY report counted"
        await bt.controller._stop_scanning()

    @pytest.mark.asyncio
    async def test_a_key_release_is_not_a_second_press(self, bt):
        bt.controller.running = True
        node = await connect_remote(bt)
        deltas = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(bt.controller._volume, "accumulate", deltas.append)
            await node.events.put(
                types.SimpleNamespace(type=FakeEcodes.EV_KEY, code=115, value=0)
            )
            await node.events.put(key_event(115))
            for _ in range(20):
                await asyncio.sleep(0)
                if deltas:
                    break

        assert deltas == [2.0]
        await bt.controller._stop_scanning()

    @pytest.mark.asyncio
    async def test_a_remote_that_disappears_leaves_no_bookkeeping_behind(self, bt):
        """The node dies when the remote sleeps. A path left in the monitored
        set means the next scan skips it, so the remote never comes back."""
        bt.controller.running = True
        node = await connect_remote(bt)
        assert bt.controller._monitored_paths

        await node.events.put(None)  # the read loop raises OSError
        for _ in range(50):
            await asyncio.sleep(0)
            if not bt.controller._monitored_paths:
                break

        assert bt.controller._monitored_paths == set()
        assert bt.controller._device_info == {}
        assert bt.controller._monitor_tasks == {}
        assert bt.status()[-1].connected_devices == []
        await bt.controller._stop_scanning()
