"""
Agent Bluetooth asynchrone pour gérer les demandes d'autorisation et d'appairage
"""
import logging
import uuid
import asyncio
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.errors import DBusError
from dbus_next.constants import PropertyAccess, BusType
from dbus_next import Variant

class BluetoothAgent(ServiceInterface):
    """Agent Bluetooth pour accepter automatiquement les connexions A2DP"""
    
    def __init__(self, bus: MessageBus):
        self.logger = logging.getLogger(__name__)
        
        # Générer un chemin unique pour éviter les conflits
        self.path = f"/org/oakos/agent_{uuid.uuid4().hex[:8]}"
        
        # Initialiser l'interface de service D-Bus
        super().__init__('org.bluez.Agent1')
        
        self.bus = bus
        self.agent_registered = False
    
    async def register(self) -> bool:
        """Enregistre l'agent auprès de BlueZ"""
        try:
            # Exporter l'interface de service
            self.bus.export(self.path, self)
            
            # Obtenir l'interface AgentManager1
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager_object = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager = agent_manager_object.get_interface('org.bluez.AgentManager1')
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            await agent_manager.call_register_agent(self.path, 'NoInputNoOutput')
            await agent_manager.call_request_default_agent(self.path)
            
            self.agent_registered = True
            self.logger.info(f"Agent Bluetooth enregistré avec succès sur {self.path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    async def unregister(self) -> bool:
        """Désenregistre l'agent auprès de BlueZ"""
        if not self.agent_registered:
            return True
            
        try:
            # Obtenir l'interface AgentManager1
            introspect = await self.bus.introspect('org.bluez', '/org/bluez')
            agent_manager_object = self.bus.get_proxy_object('org.bluez', '/org/bluez', introspect)
            agent_manager = agent_manager_object.get_interface('org.bluez.AgentManager1')
            
            # Désenregistrer l'agent
            await agent_manager.call_unregister_agent(self.path)
            
            # Supprimer l'exportation
            self.bus.unexport(self.path)
            self.agent_registered = False
            
            self.logger.info("Agent Bluetooth désenregistré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            return False
    
    # Méthodes de l'interface org.bluez.Agent1
    @method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        """Autorise automatiquement les services"""
        self.logger.info(f"Service autorisé: {device}, UUID: {uuid}")
        return
    
    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        """Fournit un code PIN par défaut"""
        self.logger.info(f"Code PIN demandé pour {device}")
        return "0000"
    
    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        """Fournit une clé numérique par défaut"""
        self.logger.info(f"Passkey demandée pour {device}")
        return 0000
    
    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
        """Affiche la clé de passage (pour les logs uniquement)"""
        self.logger.info(f"Affichage passkey: {device}, {passkey}, saisi: {entered}")
        return
    
    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        """Affiche le code PIN (pour les logs uniquement)"""
        self.logger.info(f"Affichage code PIN: {device}, {pincode}")
        return
    
    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        """Confirme automatiquement l'appairage"""
        self.logger.info(f"Confirmation automatique pour {device}, passkey: {passkey}")
        return
    
    @method()
    def RequestAuthorization(self, device: 'o') -> None:
        """Autorise automatiquement les périphériques"""
        self.logger.info(f"Autorisation automatique pour {device}")
        return
    
    @method()
    def Cancel(self) -> None:
        """Gère l'annulation d'une demande"""
        self.logger.info("Demande d'agent annulée")
        return
    
    @method()
    def Release(self) -> None:
        """Libération de l'agent par BlueZ"""
        self.logger.info("Agent libéré par BlueZ")
        return