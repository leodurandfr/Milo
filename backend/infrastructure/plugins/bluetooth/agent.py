"""
Agent Bluetooth pour gérer les demandes d'autorisation et d'appairage
"""
import logging
import dbus
import dbus.service
import asyncio
from concurrent.futures import ThreadPoolExecutor

class BluetoothAgent(dbus.service.Object):
    """Agent Bluetooth pour accepter automatiquement les connexions A2DP"""
    
    AGENT_PATH = "/org/oakos/agent"
    AGENT_INTERFACE = "org.bluez.Agent1"
    
    def __init__(self, bus):
        super().__init__(bus, self.AGENT_PATH)
        self.bus = bus
        self.logger = logging.getLogger(__name__)
        self.logger.info("Agent Bluetooth initialisé")
        self._executor = ThreadPoolExecutor(max_workers=1)
    
    def register_sync(self):
        """Version synchrone pour enregistrer l'agent Bluetooth"""
        try:
            self.logger.info("Enregistrement de l'agent Bluetooth...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            # Désactiver tout agent existant
            try:
                manager.UnregisterAgent(self.AGENT_PATH)
                self.logger.info("Agent précédent désenregistré")
            except Exception as e:
                self.logger.debug(f"Pas d'agent précédent à désenregistrer: {e}")
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            self.logger.info("Enregistrement de l'agent avec capacité NoInputNoOutput")
            manager.RegisterAgent(self.AGENT_PATH, "NoInputNoOutput")
            manager.RequestDefaultAgent(self.AGENT_PATH)
            
            self.logger.info("Agent Bluetooth enregistré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    def unregister_sync(self):
        """Version synchrone pour désenregistrer l'agent"""
        try:
            self.logger.info("Désenregistrement de l'agent Bluetooth...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            manager.UnregisterAgent(self.AGENT_PATH)
            self.logger.info("Agent Bluetooth désenregistré")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            return False
    
    async def register(self):
        """Enregistre l'agent avec BlueZ (version asynchrone)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.register_sync)
    
    async def unregister(self):
        """Désenregistre l'agent (version asynchrone)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.unregister_sync)
    
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