# backend/infrastructure/plugins/bluetooth/agent.py
"""
Agent Bluetooth pour gérer les demandes d'autorisation et d'appairage
"""
import logging
import dbus
import dbus.service
from typing import Optional

class BluetoothAgent(dbus.service.Object):
    """Agent Bluetooth pour accepter automatiquement les connexions A2DP"""
    
    AGENT_PATH = "/org/bluez/agent/oakos"
    AGENT_INTERFACE = "org.bluez.Agent1"
    
    def __init__(self, bus):
        super().__init__(bus, self.AGENT_PATH)
        self.bus = bus
        self.logger = logging.getLogger(__name__)
        self.logger.info("Agent Bluetooth initialisé")
    
    async def register(self) -> bool:
        """Enregistre l'agent avec BlueZ"""
        try:
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1"
            )
            
            # Désactiver tout agent existant
            try:
                manager.UnregisterAgent(self.AGENT_PATH)
            except:
                pass
            
            # Enregistrer l'agent avec capacité NoInputNoOutput
            self.logger.info("Enregistrement de l'agent avec capacité NoInputNoOutput")
            manager.RegisterAgent(self.AGENT_PATH, "NoInputNoOutput")
            manager.RequestDefaultAgent(self.AGENT_PATH)
            
            self.logger.info("Agent Bluetooth enregistré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'enregistrement de l'agent: {e}")
            return False
    
    async def unregister(self) -> bool:
        """Désenregistre l'agent"""
        try:
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
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        """Autorise les connexions au service A2DP"""
        self.logger.info(f"Service autorisé: {device}, UUID: {uuid}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        """Fournit un code PIN pour l'appairage"""
        self.logger.info(f"Code PIN demandé pour {device}")
        return "0000"
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        """Fournit une clé de passage pour l'appairage"""
        self.logger.info(f"Passkey demandée pour {device}")
        return dbus.UInt32(0)
    
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
        self.logger.info(f"Confirmation demandée pour {device}, passkey: {passkey}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        """Autorise automatiquement les périphériques"""
        self.logger.info(f"Autorisation demandée pour {device}")
        return
    
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        """Gère l'annulation d'une demande"""
        self.logger.info("Demande annulée")