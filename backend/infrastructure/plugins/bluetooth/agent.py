"""
Bluetooth agent to automatically accept connections - Concise version
"""
import logging
import uuid
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next.constants import BusType

class BluetoothAgent(ServiceInterface):
    """Bluetooth agent with NoInputNoOutput mode"""

    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.agent")
        self.path = f"/org/milo/agent_{uuid.uuid4().hex[:8]}"
        super().__init__('org.bluez.Agent1')
        self.bus = None
        self.registered = False
    
    async def register(self) -> bool:
        """Registers agent with BlueZ"""
        try:
            # Connect to system bus
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            
            # Export interface
            self.bus.export(self.path, self)
            
            # Get AgentManager1 interface
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager_iface = agent_manager.get_interface('org.bluez.AgentManager1')
            
            # Register agent
            await agent_manager_iface.call_register_agent(self.path, 'NoInputNoOutput')
            await agent_manager_iface.call_request_default_agent(self.path)
            
            self.registered = True
            return True
        except Exception as e:
            self.logger.error(f"Agent registration error: {e}")
            return False
    
    async def unregister(self) -> bool:
        """Unregisters agent with BlueZ"""
        if not self.registered or not self.bus:
            return True
        
        try:
            # Get AgentManager1 interface
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager_iface = agent_manager.get_interface('org.bluez.AgentManager1')
            
            # Unregister agent
            await agent_manager_iface.call_unregister_agent(self.path)
            
            # Clean resources
            self.bus.unexport(self.path)
            self.registered = False
            
            return True
        except Exception as e:
            self.logger.error(f"Unregistration error: {e}")
            return False
    
    # Agent1 interface methods (minimal implementation)
    
    @method()
    def Release(self) -> None:
        pass
    
    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        return "0000"
    
    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        pass
    
    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        return 0000
    
    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
        pass
    
    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        pass
    
    @method()
    def RequestAuthorization(self, device: 'o') -> None:
        pass

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        pass
    
    @method()
    def Cancel(self) -> None:
        pass