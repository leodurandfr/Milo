"""
Agent Bluetooth pour accepter automatiquement les connexions sans intervention utilisateur
"""
import asyncio
import logging
import uuid
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next.constants import BusType

class BluetoothAgent(ServiceInterface):
    """Agent Bluetooth avec mode NoInputNoOutput pour accepter automatiquement les appareils"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.path = f"/org/oakos/agent_{uuid.uuid4().hex[:8]}"
        super().__init__('org.bluez.Agent1')
        self.bus = None
        self.registered = False
    
    async def register(self) -> bool:
        """Enregistre l'agent auprès de BlueZ"""
        try:
            # Connecter au bus système
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            
            # Exporter l'interface
            self.bus.export(self.path, self)
            
            # Obtenir l'interface AgentManager1
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager_iface = agent_manager.get_interface('org.bluez.AgentManager1')
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            await agent_manager_iface.call_register_agent(self.path, 'NoInputNoOutput')
            await agent_manager_iface.call_request_default_agent(self.path)
            
            self.registered = True
            self.logger.info(f"Agent Bluetooth enregistré avec succès: {self.path}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    async def unregister(self) -> bool:
        """Désenregistre l'agent auprès de BlueZ"""
        if not self.registered or not self.bus:
            return True
        
        try:
            # Obtenir l'interface AgentManager1
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager_iface = agent_manager.get_interface('org.bluez.AgentManager1')
            
            # Désenregistrer l'agent
            await agent_manager_iface.call_unregister_agent(self.path)
            
            # Nettoyer les ressources
            self.bus.unexport(self.path)
            self.registered = False
            
            self.logger.info("Agent Bluetooth désenregistré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            return False
    
    # Méthodes requises par l'interface Agent1
    
    @method()
    def Release(self) -> None:
        """Appelé lorsque l'agent est libéré"""
        self.logger.info("Agent Bluetooth libéré")
    
    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        """Automatiquement fournir un code PIN"""
        self.logger.info(f"PIN demandé pour {device}")
        return "0000"  # Code PIN standard
    
    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        """Affiche un code PIN (non utilisé en mode NoInputNoOutput)"""
        self.logger.info(f"Code PIN pour {device}: {pincode}")
    
    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        """Automatiquement fournir une clé de passe"""
        self.logger.info(f"Passkey demandée pour {device}")
        return 0000  # Passkey simple
    
    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
        """Affiche une clé de passe (non utilisé en mode NoInputNoOutput)"""
        self.logger.info(f"Passkey pour {device}: {passkey}")
    
    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        """Accepte automatiquement la confirmation"""
        self.logger.info(f"Confirmation automatique pour {device} avec passkey {passkey}")
    
    @method()
    def RequestAuthorization(self, device: 'o') -> None:
        """Accepte automatiquement l'autorisation"""
        self.logger.info(f"Autorisation automatique pour {device}")
    
    @method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        """Autorise automatiquement l'utilisation d'un service"""
        self.logger.info(f"Service {uuid} autorisé pour {device}")
    
    @method()
    def Cancel(self) -> None:
        """Appelé lorsque l'opération est annulée"""
        self.logger.info("Opération annulée")