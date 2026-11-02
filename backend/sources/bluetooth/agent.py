# backend/sources/bluetooth/agent.py
"""
Bluetooth D-Bus agent for auto-pairing.

Implements the org.bluez.Agent1 interface to pair incoming devices without
user interaction, then authorize the audio profiles only (A2DP, AVRCP):
any other service an untrusted device asks for — HID, serial, phonebook — is
rejected, since pairing itself is Just Works for anyone in range.
"""
import asyncio
import contextlib
import logging
import uuid
from typing import Optional

from dbus_next import DBusError
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next.constants import BusType

from backend.shared.decorators import handle_errors
from backend.sources.bluetooth import adapter as bluez_adapter

# A2DP and AVRCP in every role. BlueZ asks for A2DP under ADVANCED_AUDIO (110d).
AUDIO_SERVICE_UUIDS = frozenset(
    f"0000110{x}-0000-1000-8000-00805f9b34fb" for x in "abcdef"
)


class BluetoothAgent(ServiceInterface):
    """
    Bluetooth agent with NoInputNoOutput capability.

    Registers with BlueZ to handle pairing requests automatically.
    Uses NoInputNoOutput mode for headless operation.
    """

    def __init__(self):
        """Initialize agent with unique path."""
        self._logger = logging.getLogger("source.bluetooth.agent")
        self._path = f"/org/milo/agent_{uuid.uuid4().hex[:8]}"
        super().__init__('org.bluez.Agent1')
        self._bus: Optional[MessageBus] = None
        self._registered = False

    @property
    def path(self) -> str:
        """Get agent D-Bus path."""
        return self._path

    @handle_errors(default=False)
    async def register(self) -> bool:
        """
        Register agent with BlueZ.

        Returns:
            True if registration succeeded
        """
        # Connect to system bus
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        timeout = bluez_adapter.DBUS_CALL_TIMEOUT
        try:
            # Export agent interface
            self._bus.export(self._path, self)

            # Bounded as a whole: START runs this holding the source's mailbox.
            await asyncio.wait_for(self._register_with_bluez(), timeout)
        except asyncio.TimeoutError:
            self._disconnect()
            self._logger.warning(f"bluetoothd did not register the agent within {timeout} s")
            return False
        except BaseException:
            # Nothing else will: unregister() returns early on a failed
            # registration, so the socket would live until the process dies and
            # the next source start would open another. A cancellation too — a
            # stop cutting the registration short.
            self._disconnect()
            raise

        self._registered = True
        self._logger.info(f"Agent registered at {self._path}")
        return True

    @handle_errors(default=False)
    async def unregister(self) -> bool:
        """
        Unregister agent from BlueZ, within DBUS_CALL_TIMEOUT for the whole
        exchange.

        It runs inside the source's STOP, which nothing preempts, ahead of the
        exposure write. A bluetoothd that does not answer costs nothing past
        the bound: BlueZ drops an agent whose bus connection closes, and the
        connection is closed below whatever happened.

        Returns:
            True if unregistration succeeded
        """
        if not self._registered or not self._bus:
            return True

        bound = bluez_adapter.DBUS_CALL_TIMEOUT
        try:
            await asyncio.wait_for(self._unregister_from_bluez(), bound)
        except asyncio.TimeoutError:
            self._logger.warning(
                f"bluetoothd did not unregister the agent within {bound} s — "
                "closing its bus connection drops it"
            )
            return False
        finally:
            # Whatever BlueZ answered, the socket is ours to close: the source
            # is stopping and register() opens a fresh one on every start.
            self._bus.unexport(self._path)
            self._disconnect()
            self._registered = False

        self._logger.info("Agent unregistered")
        return True

    async def _agent_manager(self):
        """BlueZ's AgentManager1, on this agent's bus."""
        introspect = await self._bus.introspect('org.bluez', '/org/bluez')
        agent_manager = self._bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
        return agent_manager.get_interface('org.bluez.AgentManager1')

    async def _register_with_bluez(self) -> None:
        """Register, then become the default agent."""
        agent_iface = await self._agent_manager()
        await agent_iface.call_register_agent(self._path, 'NoInputNoOutput')
        await agent_iface.call_request_default_agent(self._path)

    async def _unregister_from_bluez(self) -> None:
        agent_iface = await self._agent_manager()
        await agent_iface.call_unregister_agent(self._path)

    async def register_again(self) -> bool:
        """Register with a bluetoothd that replaced the one this agent was
        registered with. The old registration died with that daemon, so there
        is nothing to unregister — only our side of it to drop. Measured: a
        bluetoothd with no agent refuses every audio connection."""
        if self._bus:
            with contextlib.suppress(Exception):
                self._bus.unexport(self._path)
            self._disconnect()
        self._registered = False
        return await self.register()

    def _disconnect(self) -> None:
        """Close the system-bus connection this agent opened."""
        if self._bus:
            self._bus.disconnect()
            self._bus = None

    # === org.bluez.Agent1 Interface Methods ===

    @method()
    def Release(self) -> None:
        """Called when agent is released."""
        self._logger.debug("Agent released")

    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        """Request PIN code for pairing."""
        self._logger.debug(f"PIN code requested for {device}")
        return "0000"

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        """Display PIN code (no-op for headless)."""
        self._logger.debug(f"Display PIN {pincode} for {device}")

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        """Request passkey for pairing."""
        self._logger.debug(f"Passkey requested for {device}")
        return 0

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', _entered: 'q') -> None:
        """Display passkey (no-op for headless)."""
        self._logger.debug(f"Display passkey {passkey} for {device}")

    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        """Confirm passkey (auto-accept)."""
        self._logger.debug(f"Confirmation requested for {device} with {passkey}")

    @method()
    def RequestAuthorization(self, device: 'o') -> None:
        """Authorize device (auto-accept)."""
        self._logger.debug(f"Authorization requested for {device}")

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        """Authorize an audio profile; reject every other service."""
        if uuid.lower() not in AUDIO_SERVICE_UUIDS:
            self._logger.warning(f"Rejected service {uuid} for {device}: not audio")
            raise DBusError("org.bluez.Error.Rejected", f"Service {uuid} is not audio")
        self._logger.debug(f"Service {uuid} authorization for {device}")

    @method()
    def Cancel(self) -> None:
        """Cancel pending request."""
        self._logger.debug("Agent request cancelled")
