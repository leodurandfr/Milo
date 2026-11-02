# backend/tests/test_bluetooth_adapter.py
"""BlueZ adapter writes: the read-back, and who the block sweep may reach.

Replaces a `bluetoothctl` session whose success test was `returncode == 0` —
which measured that bluetoothctl ran, not that anything applied. Measured on the
unit 2026-08-15, with the source started and reporting a configured adapter:
`Pairable: no`, so no unpaired device could pair at all.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, Mock

from dbus_next import DBusError, Variant

from backend.sources.bluetooth import adapter as adapter_module
from backend.sources.bluetooth.adapter import (
    ADAPTER_IFACE,
    ADAPTER_PATH,
    A2DP_SOURCE_UUID,
    DEVICE_IFACE,
    PROPS_IFACE,
    BluetoothAdapter,
)

HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"  # Human Interface Device


def _props(stored):
    """A Properties interface backed by a dict, so a set is observable."""
    props = Mock()

    async def call_set(iface, name, variant):
        stored[name] = variant.value

    async def call_get(iface, name):
        return Variant("b", stored.get(name))

    props.call_set = call_set
    props.call_get = call_get
    return props


class TestReadBack:
    @pytest.mark.asyncio
    async def test_a_property_that_took_is_a_success(self):
        adapter = BluetoothAdapter()
        stored = {}
        adapter._properties = AsyncMock(return_value=_props(stored))

        assert await adapter.set_exposure(discoverable=True, pairable=True) is True
        assert stored == {"Discoverable": True, "Pairable": True}

    @pytest.mark.asyncio
    async def test_a_property_the_adapter_did_not_take_is_a_failure(self):
        """The 7.4 case: the write is accepted, the adapter settles elsewhere."""
        adapter = BluetoothAdapter()
        props = Mock()
        props.call_set = AsyncMock()
        props.call_get = AsyncMock(return_value=Variant("b", False))
        adapter._properties = AsyncMock(return_value=props)

        assert await adapter.set_exposure(discoverable=True, pairable=True) is False

    @pytest.mark.asyncio
    async def test_a_refusal_is_a_failure_not_an_exception(self):
        """BlueZ absent (dev host) must fail closed, not crash the source start."""
        adapter = BluetoothAdapter()
        adapter._properties = AsyncMock(side_effect=RuntimeError("no bluez"))

        assert await adapter.power_on() is False
        assert await adapter.set_exposure(discoverable=False, pairable=False) is False


class TestDroppingOnePeer:
    """`disconnect_device` — what the "Disconnect" button and the single-device
    rule both call now.

    It replaced two copies of `bluetoothctl disconnect <address>`, which exited
    0 on having issued the request. The read-back is the whole difference, so it
    is what these pin — including on the paths where the call itself failed.
    """

    @staticmethod
    def _adapter(*, connected_after=False, disconnect=None):
        """Only `_interface` is doubled, so the read-back resolves through the
        real `_properties` and its path is asserted with the call's."""
        adapter = BluetoothAdapter()
        device = Mock()
        device.call_disconnect = disconnect or AsyncMock()
        props = Mock()
        props.call_get = AsyncMock(return_value=Variant("b", connected_after))

        async def interface(path, name):
            interface.paths.append((path, name))
            return device if name == DEVICE_IFACE else props

        interface.paths = []
        adapter._interface = interface
        return adapter, device, interface

    @pytest.mark.asyncio
    async def test_a_peer_that_let_go_is_a_success(self):
        adapter, device, _ = self._adapter(connected_after=False)

        assert await adapter.disconnect_device("AA:BB:CC:DD:EE:FF") is True
        device.call_disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_peer_still_connected_afterwards_is_a_failure(self):
        """The whole reason this left bluetoothctl: BlueZ can accept the call
        and the link survive it. Reported as success, the button looks inert."""
        adapter, _, _ = self._adapter(connected_after=True)

        assert await adapter.disconnect_device("AA:BB:CC:DD:EE:FF") is False

    @pytest.mark.asyncio
    async def test_the_call_and_the_read_back_address_the_same_object(self):
        """A wrong path drops somebody else's link, or reads somebody else's
        Connected and calls that a verification. BlueZ spells a device with the
        separators replaced and the case raised."""
        adapter, _, interface = self._adapter(connected_after=False)

        await adapter.disconnect_device("aa:bb:cc:dd:ee:ff")

        expected = f"{ADAPTER_PATH}/dev_AA_BB_CC_DD_EE_FF"
        assert [name for _, name in interface.paths] == [DEVICE_IFACE, PROPS_IFACE]
        assert {path for path, _ in interface.paths} == {expected}

    @pytest.mark.asyncio
    async def test_a_refused_call_still_defers_to_the_read_back(self):
        """`NotConnected` on a peer that already left is the answer we want.
        Treated as a failure, a phone that dropped on its own while the BlueALSA
        feed was down would answer the button with "did not release the link"
        and raise the UI's error banner for a link that is already gone."""
        adapter, _, _ = self._adapter(
            connected_after=False,
            disconnect=AsyncMock(side_effect=DBusError(
                "org.bluez.Error.NotConnected", "Not connected"
            )),
        )

        assert await adapter.disconnect_device("AA:BB") is True

    @pytest.mark.asyncio
    async def test_a_refused_call_is_not_a_success_on_its_own(self):
        """The other half of the rule above: deferring to the read-back must not
        become "any error means it worked"."""
        adapter, _, _ = self._adapter(
            connected_after=True,
            disconnect=AsyncMock(side_effect=DBusError(
                "org.bluez.Error.Failed", "nope"
            )),
        )

        assert await adapter.disconnect_device("AA:BB") is False

    @pytest.mark.asyncio
    async def test_a_call_that_never_answers_is_bounded_and_refused(self, monkeypatch):
        """dbus-next bounds only `introspect`, and at 30 s. Unbounded, a
        bluetoothd that stops answering parks the HTTP request the button made
        AND stops the BlueALSA connect/disconnect feed, which awaits this
        inline. Both `bluetoothctl` spawns this replaced carried a 10 s cap."""
        async def never():
            await asyncio.Event().wait()

        monkeypatch.setattr(adapter_module, "DBUS_CALL_TIMEOUT", 0.01)
        adapter, _, _ = self._adapter(disconnect=lambda: never())

        assert await adapter.disconnect_device("AA:BB") is False

    @pytest.mark.asyncio
    async def test_an_unknown_peer_is_a_failure_not_an_exception(self):
        """This runs from a BlueALSA monitor callback; raising would kill the
        feed that called it."""
        adapter = BluetoothAdapter()
        adapter._interface = AsyncMock(side_effect=RuntimeError("no such object"))

        assert await adapter.disconnect_device("AA:BB") is False


class TestAudioPeerSelection:
    """The block sweep must reach senders and must never reach the HID remote.

    `_do_stop` deliberately leaves bluetooth.service running when the Bluetooth
    remote is enabled; a sweep that blocked it by address would take the remote
    down with the exposure.
    """

    def _bus_with(self, devices):
        manager = Mock()
        manager.call_get_managed_objects = AsyncMock(return_value=devices)
        obj = Mock()
        obj.get_interface = Mock(return_value=manager)
        bus = Mock()
        bus.introspect = AsyncMock(return_value=Mock())
        bus.get_proxy_object = Mock(return_value=obj)
        return bus

    def _device(self, address, uuids):
        return {DEVICE_IFACE: {
            "Address": Variant("s", address),
            "UUIDs": Variant("as", uuids),
        }}

    @pytest.mark.asyncio
    async def test_only_a2dp_senders_are_listed(self):
        adapter = BluetoothAdapter()
        adapter._connect = AsyncMock(return_value=self._bus_with({
            "/org/bluez/hci0/dev_AA": self._device("AA:AA:AA:AA:AA:AA", [A2DP_SOURCE_UUID]),
            "/org/bluez/hci0/dev_BB": self._device("BB:BB:BB:BB:BB:BB", [HID_UUID]),
            "/org/bluez/hci0": {ADAPTER_IFACE: {}},
        }))

        peers = await adapter.audio_peers()

        assert peers == {"/org/bluez/hci0/dev_AA": "AA:AA:AA:AA:AA:AA"}

    @pytest.mark.asyncio
    async def test_the_holder_is_left_unblocked(self):
        """Blocking the sender currently playing would drop its audio."""
        adapter = BluetoothAdapter()
        adapter.audio_peers = AsyncMock(return_value={
            "/dev_AA": "AA:AA:AA:AA:AA:AA",
            "/dev_CC": "CC:CC:CC:CC:CC:CC",
        })
        written = []

        async def _set_verified(path, iface, name, variant, expected):
            written.append((path, name, variant.value))
            return True
        adapter._set_verified = _set_verified

        assert await adapter.set_audio_peers_blocked(
            True, keep_unblocked="aa:aa:aa:aa:aa:aa"
        ) is True
        assert written == [("/dev_CC", "Blocked", True)]

    @pytest.mark.asyncio
    async def test_a_peer_that_stayed_reachable_is_reported(self):
        adapter = BluetoothAdapter()
        adapter.audio_peers = AsyncMock(return_value={"/dev_AA": "AA:AA:AA:AA:AA:AA"})
        adapter._set_verified = AsyncMock(return_value=False)

        assert await adapter.set_audio_peers_blocked(True) is False

    @pytest.mark.asyncio
    async def test_no_known_sender_is_not_a_failure(self):
        adapter = BluetoothAdapter()
        adapter.audio_peers = AsyncMock(return_value={})

        assert await adapter.set_audio_peers_blocked(True) is True


class TestPropertyTargets:
    """Pin what is written where: these paths and names are the BlueZ contract."""

    @pytest.mark.asyncio
    async def test_exposure_is_written_on_the_adapter_object(self):
        adapter = BluetoothAdapter()
        seen = []

        async def _set_verified(path, iface, name, variant, expected):
            seen.append((path, iface, name))
            return True
        adapter._set_verified = _set_verified

        await adapter.set_exposure(discoverable=True, pairable=True)

        assert seen == [
            (ADAPTER_PATH, ADAPTER_IFACE, "Discoverable"),
            (ADAPTER_PATH, ADAPTER_IFACE, "Pairable"),
        ]


class _SilentBluez:
    """A system bus whose bluetoothd answers everything but one method.

    Stands for the outside world only: the adapter's own `_interface`,
    `_properties` and `_set_verified` run unchanged over it. `introspect` waits
    out the `timeout` it is handed, the way dbus-next does, so an adapter that
    passes none is the one left waiting.
    """

    PEER = "/org/bluez/hci0/dev_AA"

    def __init__(self, silent=None, *, introspect_silent=False,
                 managed_objects_error=None):
        self.silent = silent
        self.introspect_silent = introspect_silent
        self.managed_objects_error = managed_objects_error
        self.introspect_timeouts = []
        self.stored = {}

    async def _never(self, *args):
        await asyncio.Event().wait()

    async def introspect(self, service, path, timeout=30.0):
        self.introspect_timeouts.append(timeout)
        if self.introspect_silent:
            await asyncio.wait_for(asyncio.Event().wait(), timeout)
        return Mock()

    def get_proxy_object(self, service, path, introspection):
        bluez = self

        async def call_set(iface, name, variant):
            bluez.stored[(path, name)] = variant.value

        async def call_get(iface, name):
            return Variant("b", bluez.stored.get((path, name)))

        async def call_get_managed_objects():
            if bluez.managed_objects_error is not None:
                raise bluez.managed_objects_error
            return {bluez.PEER: {DEVICE_IFACE: {
                "Address": Variant("s", "AA:AA:AA:AA:AA:AA"),
                "UUIDs": Variant("as", [A2DP_SOURCE_UUID]),
            }}}

        interface = Mock()
        interface.call_set = call_set
        interface.call_get = call_get
        interface.call_get_managed_objects = call_get_managed_objects
        if self.silent:
            setattr(interface, self.silent, self._never)
        obj = Mock()
        obj.get_interface = Mock(return_value=interface)
        return obj

    def adapter(self):
        adapter = BluetoothAdapter()
        adapter._connect = AsyncMock(return_value=self)
        return adapter


# What each write reaches: GetManagedObjects is only on the peer sweep's path.
_EXPOSURE_WRITES = {
    "set_exposure": lambda a: a.set_exposure(True, True),
    "power_on": lambda a: a.power_on(),
    "set_audio_peers_blocked": lambda a: a.set_audio_peers_blocked(True),
}
_REACHED_BY = {
    "call_set": tuple(_EXPOSURE_WRITES),
    "call_get": tuple(_EXPOSURE_WRITES),
    "call_get_managed_objects": ("set_audio_peers_blocked",),
}


class TestEveryCallIsBounded:
    """The module promises every call is bounded by DBUS_CALL_TIMEOUT.

    Unbounded, a bluetoothd that answers Introspect and then stays silent parks
    `_apply_exposure` inside the source's STOP: nothing preempts a STOP, so
    BlueALSA is never stopped and the next Bluetooth selection waits behind it
    until the backend restarts. The outer `wait_for` makes a regression fail
    here instead of hanging the suite.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("silent", sorted(_REACHED_BY))
    async def test_every_exposure_call_is_bounded(self, monkeypatch, silent):
        monkeypatch.setattr(adapter_module, "DBUS_CALL_TIMEOUT", 0.01)

        for write in _REACHED_BY[silent]:
            adapter = _SilentBluez(silent).adapter()
            assert await asyncio.wait_for(_EXPOSURE_WRITES[write](adapter), 1.0) is False, write

    @pytest.mark.asyncio
    async def test_the_same_bus_answering_is_a_success(self):
        """The counter-case of the one above: the fake is not failing on its own."""
        for write, call in _EXPOSURE_WRITES.items():
            assert await asyncio.wait_for(call(_SilentBluez().adapter()), 1.0) is True, write

    @pytest.mark.asyncio
    async def test_an_introspect_that_never_answers_is_bounded_by_the_module(self, monkeypatch):
        """dbus-next's own default is 30 s, twice TEARDOWN_GRACE."""
        monkeypatch.setattr(adapter_module, "DBUS_CALL_TIMEOUT", 0.01)
        bluez = _SilentBluez(introspect_silent=True)

        for write, call in _EXPOSURE_WRITES.items():
            assert await asyncio.wait_for(call(bluez.adapter()), 1.0) is False, write

        assert bluez.introspect_timeouts
        assert set(bluez.introspect_timeouts) == {adapter_module.DBUS_CALL_TIMEOUT}


class TestPeerListFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [
        DBusError("org.freedesktop.DBus.Error.ServiceUnknown", "bluetoothd went away"),
        asyncio.TimeoutError(),
    ], ids=["service-unknown", "timeout"])
    async def test_a_peer_list_bluez_will_not_give_is_not_an_applied_exposure(self, error):
        """A failed enumeration is not "no known sender": reported as applied,
        the source logs "Bluetooth exposure: closed" with no Blocked written,
        and a new bluetoothd raises no banner while paired phones can dial in."""
        for blocked in (True, False):
            adapter = _SilentBluez(managed_objects_error=error).adapter()
            assert await adapter.set_audio_peers_blocked(blocked) is False, blocked
