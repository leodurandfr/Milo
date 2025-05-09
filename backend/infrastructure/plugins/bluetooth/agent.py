"""
Agent Bluetooth pour gérer les demandes d'autorisation et d'appairage
"""
import logging
import dbus
import dbus.service
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

class BluetoothAgent(dbus.service.Object):
    """Agent Bluetooth pour accepter automatiquement les connexions A2DP"""
    
    # Générer un chemin unique à chaque instanciation
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
            self.logger.info(f"Enregistrement de l'agent Bluetooth au chemin {self.agent_path}...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            self.logger.info("Enregistrement de l'agent avec capacité NoInputNoOutput")
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
            self.logger.info(f"Désenregistrement de l'agent Bluetooth au chemin {self.agent_path}...")
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            manager.UnregisterAgent(self.agent_path)
            self.logger.info("Agent Bluetooth désenregistré")
            
            # Libérer explicitement les ressources
            self.remove_from_connection()
            self.logger.info("Agent supprimé de la connexion D-Bus")
            
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
    
    # Les méthodes d'interface restent identiques, juste modifie les logs pour inclure le chemin
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        """Autorise automatiquement les services"""
        self.logger.info(f"Service autorisé via {self.agent_path}: {device}, UUID: {uuid}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        """Fournit un code PIN par défaut"""
        self.logger.info(f"Code PIN demandé via {self.agent_path} pour {device}")
        return "0000"
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        """Fournit une clé numérique par défaut"""
        self.logger.info(f"Passkey demandée via {self.agent_path} pour {device}")
        return dbus.UInt32(0000)
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        """Affiche la clé de passage (pour les logs uniquement)"""
        self.logger.info(f"Affichage passkey via {self.agent_path}: {device}, {passkey}, saisi: {entered}")
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        """Affiche le code PIN (pour les logs uniquement)"""
        self.logger.info(f"Affichage code PIN via {self.agent_path}: {device}, {pincode}")
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        """Confirme automatiquement l'appairage"""
        self.logger.info(f"Confirmation automatique via {self.agent_path} pour {device}, passkey: {passkey}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        """Autorise automatiquement les périphériques"""
        self.logger.info(f"Autorisation automatique via {self.agent_path} pour {device}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        """Gère l'annulation d'une demande"""
        self.logger.info(f"Demande d'agent annulée via {self.agent_path}")
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        """Libération de l'agent par BlueZ"""
        self.logger.info(f"Agent libéré par BlueZ via {self.agent_path}")