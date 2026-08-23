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
        if len(argv) > 2 and argv[1] == "remove":
            # `bluetoothctl remove` drops the bond, so the next `devices Paired`
            # no longer lists it — what is_paired() reads back after unpairing.
            self.paired = [row for row in self.paired if row[0] != argv[2]]
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
