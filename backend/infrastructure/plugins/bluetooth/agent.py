"""
Agent Bluetooth pour gérer les demandes d'autorisation et d'appairage - Version optimisée
"""
import logging
import dbus
import dbus.service
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

class BluetoothAgent(dbus.service.Object):
    """Agent Bluetooth pour accepter automatiquement les connexions A2DP"""
    
    # Interface standard BlueZ
    AGENT_INTERFACE = "org.bluez.Agent1"
    
    def __init__(self, bus):
        # Générer un chemin unique pour éviter les conflits
        self.agent_path = f"/org/oakos/agent_{uuid.uuid4().hex[:8]}"
        super().__init__(bus, self.agent_path)
        self.bus = bus
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Agent Bluetooth initialisé avec le chemin {self.agent_path}")
        self._executor = ThreadPoolExecutor(max_workers=1)
    
    def register_sync(self):
        """Version synchrone pour enregistrer l'agent Bluetooth"""
        try:
            self.logger.info(f"Enregistrement de l'agent Bluetooth synchrone...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            manager.RegisterAgent(self.agent_path, "NoInputNoOutput")
            manager.RequestDefaultAgent(self.agent_path)
            
            self.logger.info("Agent Bluetooth enregistré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    def unregister_sync(self):
        """Version synchrone pour désenregistrer l'agent"""
        try:
            self.logger.info(f"Désenregistrement de l'agent Bluetooth synchrone...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            manager.UnregisterAgent(self.agent_path)
            self.remove_from_connection()
            self.logger.info("Agent Bluetooth désenregistré")
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            return False
    
    async def register(self):
        """Enregistre l'agent avec BlueZ (version asynchrone)"""
        try:
            self.logger.info("Enregistrement de l'agent Bluetooth...")
            loop = asyncio.get_running_loop()
            
            def _register():
                manager = dbus.Interface(
                    self.bus.get_object("org.bluez", "/org/bluez"),
                    "org.bluez.AgentManager1"
                )
                
                # Enregistrer l'agent avec capacité NoInputNoOutput
                manager.RegisterAgent(self.agent_path, "NoInputNoOutput")
                manager.RequestDefaultAgent(self.agent_path)
                return True
                
            result = await loop.run_in_executor(self._executor, _register)
            if result:
                self.logger.info("Agent Bluetooth enregistré avec succès")
            return result
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    async def unregister(self):
        """Désenregistre l'agent (version asynchrone)"""
        try:
            self.logger.info("Désenregistrement de l'agent Bluetooth...")
            loop = asyncio.get_running_loop()
            
            def _unregister():
                manager = dbus.Interface(
                    self.bus.get_object("org.bluez", "/org/bluez"),
                    "org.bluez.AgentManager1"
                )
                
                manager.UnregisterAgent(self.agent_path)
                self.remove_from_connection()
                return True
                
            result = await loop.run_in_executor(self._executor, _unregister)
            if result:
                self.logger.info("Agent Bluetooth désenregistré avec succès")
            return result
        except Exception as e:
            self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            return False
    
    # Les méthodes D-Bus restent identiques, mais avec des logs simplifiés
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        """Autorise automatiquement les services"""
        self.logger.info(f"Service autorisé: {device}, UUID: {uuid}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        """Fournit un code PIN par défaut"""
        self.logger.info(f"Code PIN demandé pour {device}")
        return "0000"
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        """Fournit une clé numérique par défaut"""
        self.logger.info(f"Passkey demandée pour {device}")
        return dbus.UInt32(0000)
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        """Affiche la clé de passage (pour les logs uniquement)"""
        self.logger.info(f"Affichage passkey: {device}, {passkey}, saisi: {entered}")
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        """Affiche le code PIN (pour les logs uniquement)"""
        self.logger.info(f"Affichage code PIN: {device}, {pincode}")
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        """Confirme automatiquement l'appairage"""
        self.logger.info(f"Confirmation automatique pour {device}, passkey: {passkey}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        """Autorise automatiquement les périphériques"""
        self.logger.info(f"Autorisation automatique pour {device}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        """Gère l'annulation d'une demande"""
        self.logger.info("Demande d'agent annulée")
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        """Libération de l'agent par BlueZ"""
        self.logger.info("Agent libéré par BlueZ")