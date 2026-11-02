# backend/tests/test_bluetooth_agent.py
"""
Unit tests for the BlueZ auto-pairing agent: its system-bus connection, and
which services it authorizes.

The agent opens one connection to the system bus per `register()`, and the
Bluetooth source registers on every start. What is asserted here is that the
connection is given back — on the way out, and on a registration that failed
before it ever got one. BlueZ stands in as a mock: it is the outside world, and
what matters is what the agent did to the socket it owns.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest
from dbus_next import DBusError

from backend.sources.bluetooth.agent import BluetoothAgent

DEVICE_PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def _fake_bus():
    """A system bus and the AgentManager1 interface reached through it."""
    manager = Mock(
        call_register_agent=AsyncMock(),
        call_request_default_agent=AsyncMock(),
        call_unregister_agent=AsyncMock(),
    )
    bus = Mock(
        export=Mock(),
        unexport=Mock(),
        disconnect=Mock(),
        introspect=AsyncMock(return_value=Mock()),
        get_proxy_object=Mock(return_value=Mock(get_interface=Mock(return_value=manager))),
    )
    return bus, manager


class TestBusLifetime:
    """One connection per start, and one hand-back per stop."""

    @patch("backend.sources.bluetooth.agent.MessageBus")
    async def test_a_full_cycle_leaves_no_connection_open(self, message_bus):
        """The non-triviality check and the fix in one: a register that never
        reached BlueZ would satisfy the disconnect assertion on its own, so the
        registration is asserted too."""
        bus, manager = _fake_bus()
        message_bus.return_value.connect = AsyncMock(return_value=bus)
        agent = BluetoothAgent()

        assert await agent.register() is True
        manager.call_register_agent.assert_awaited_once_with(agent.path, 'NoInputNoOutput')

        assert await agent.unregister() is True
        manager.call_unregister_agent.assert_awaited_once_with(agent.path)
        bus.disconnect.assert_called_once()

    @patch("backend.sources.bluetooth.agent.MessageBus")
    async def test_a_second_start_does_not_stack_a_second_socket(self, message_bus):
        """The source registers on every start. One connection left behind per
        cycle is a file descriptor the process never gets back."""
        buses = [_fake_bus()[0] for _ in range(2)]
        message_bus.return_value.connect = AsyncMock(side_effect=buses)
        agent = BluetoothAgent()

        for _ in range(2):
            assert await agent.register() is True
            assert await agent.unregister() is True

        assert [bus.disconnect.call_count for bus in buses] == [1, 1]

    @patch("backend.sources.bluetooth.agent.MessageBus")
    async def test_a_registration_that_fails_closes_what_it_opened(self, message_bus):
        """The bus is connected before BlueZ is asked anything. A refusal there
        leaves `_registered` false, and `unregister()` returns early on that —
        so nothing else would ever close it."""
        bus, manager = _fake_bus()
        manager.call_register_agent = AsyncMock(side_effect=RuntimeError("BlueZ refused"))
        message_bus.return_value.connect = AsyncMock(return_value=bus)
        agent = BluetoothAgent()

        assert await agent.register() is False

        bus.disconnect.assert_called_once()

    @patch("backend.sources.bluetooth.agent.MessageBus")
    async def test_a_refused_unregister_still_closes_the_socket(self, message_bus):
        """bluetoothd is routinely already gone by the time the source stops —
        the socket is ours whatever it answers."""
        bus, manager = _fake_bus()
        message_bus.return_value.connect = AsyncMock(return_value=bus)
        agent = BluetoothAgent()
        assert await agent.register() is True
        manager.call_unregister_agent = AsyncMock(side_effect=RuntimeError("no such agent"))

        assert await agent.unregister() is False

        bus.disconnect.assert_called_once()


def _bus_handler(agent, name):
    """The callable dbus-next dispatches to, rather than the `@method()`
    wrapper on the class, which discards what the function returns."""
    wrapped = getattr(type(agent), name)
    return wrapped.__dict__["__DBUS_METHOD"].fn.__get__(agent)


class TestServiceAuthorization:
    """What a paired but untrusted device may open while the source is exposed.

    The agent pairs anyone in Just Works, so `AuthorizeService` is what stands
    between a stranger in radio range and the Classic profiles BlueZ serves —
    an input device among them, whose keystrokes reach the kiosk."""

    @pytest.mark.parametrize("short", ["1124", "1101", "112f"])
    def test_the_agent_refuses_a_service_that_is_not_audio(self, short):
        """HID, serial port, phonebook: none is something a speaker offers.
        `org.bluez.Error.Rejected` is the Agent1 contract BlueZ reads as a
        refusal; anything returned is an acceptance."""
        handler = _bus_handler(BluetoothAgent(), "AuthorizeService")

        with pytest.raises(DBusError) as exc:
            handler(DEVICE_PATH, f"0000{short}{BASE_UUID_SUFFIX}")

        assert exc.value.type == "org.bluez.Error.Rejected"

    @pytest.mark.parametrize("uuid", [
        *(f"0000110{x}{BASE_UUID_SUFFIX}" for x in "abcdef"),
        f"0000110d{BASE_UUID_SUFFIX}".upper(),
    ])
    def test_the_agent_authorizes_every_audio_profile(self, uuid):
        """A2DP and AVRCP in every role. BlueZ asks for A2DP under
        ADVANCED_AUDIO (110d), so a list missing it would leave every phone
        paired and silent; the upper-case spelling pins a case-blind match."""
        assert _bus_handler(BluetoothAgent(), "AuthorizeService")(DEVICE_PATH, uuid) is None
