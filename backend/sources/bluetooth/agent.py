# backend/sources/bluetooth/agent.py
"""
Bluetooth D-Bus agent for auto-pairing.

Implements the org.bluez.Agent1 interface to automatically accept
incoming Bluetooth connections without user interaction.
"""
import logging
import uuid
from typing import Optional

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next.constants import BusType

from backend.shared.decorators import handle_errors


class BluetoothAgent(ServiceInterface):
    """
    Bluetooth agent with NoInputNoOutput capability.

    Registers with BlueZ to handle pairing requests automatically.
    Uses NoInputNoOutput mode for headless operation.
    """

    def __init__(self):
        """Initialize agent with unique path."""
        self._logger = logging.getLogger("feature.bluetooth.agent")
        self._path = f"/org/milo/agent_{uuid.uuid4().hex[:8]}"
        super().__init__('org.bluez.Agent1')
        self._bus: Optional[MessageBus] = None
        self._registered = False

    @property
    def path(self) -> str:
        """Get agent D-Bus path."""
        return self._path

    @property
    def is_registered(self) -> bool:
        """Check if agent is registered."""
        return self._registered

    @handle_errors(default=False)
    async def register(self) -> bool:
        """
        Register agent with BlueZ.

        Returns:
            True if registration succeeded
        """
        # Connect to system bus
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # Export agent interface
        self._bus.export(self._path, self)

        # Get AgentManager1 interface
        introspect = await self._bus.introspect('org.bluez', '/org/bluez')
        agent_manager = self._bus.get_proxy_object(
            'org.bluez', '/org/bluez', introspect
        )
        agent_iface = agent_manager.get_interface('org.bluez.AgentManager1')

        # Register and set as default
        await agent_iface.call_register_agent(self._path, 'NoInputNoOutput')
        await agent_iface.call_request_default_agent(self._path)

        self._registered = True
        self._logger.info(f"Agent registered at {self._path}")
        return True

    @handle_errors(default=False)
    async def unregister(self) -> bool:
        """
        Unregister agent from BlueZ.

        Returns:
            True if unregistration succeeded
        """
        if not self._registered or not self._bus:
            return True

        # Get AgentManager1 interface
        introspect = await self._bus.introspect('org.bluez', '/org/bluez')
        agent_manager = self._bus.get_proxy_object(
            'org.bluez', '/org/bluez', introspect
        )
        agent_iface = agent_manager.get_interface('org.bluez.AgentManager1')

        # Unregister agent
        await agent_iface.call_unregister_agent(self._path)

        # Clean up resources
        self._bus.unexport(self._path)
        self._registered = False

        self._logger.info("Agent unregistered")
        return True

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
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
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
        """Authorize service (auto-accept)."""
        self._logger.debug(f"Service {uuid} authorization for {device}")

    @method()
    def Cancel(self) -> None:
        """Cancel pending request."""
        self._logger.debug("Agent request cancelled")
