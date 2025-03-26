"""
Traitement des métadonnées pour le plugin snapclient.
"""
import logging
from typing import Dict, Any, Optional
from backend.application.event_bus import EventBus

class MetadataProcessor:
    """
    Gère les métadonnées du plugin snapclient.
    Snapclient ne fournit pas de métadonnées audio mais des informations 
    sur le périphérique connecté.
    """
    
    def __init__(self, event_bus: EventBus, source_name: str):
        self.event_bus = event_bus
        self.source_name = source_name
        self.logger = logging.getLogger(f"snapclient.metadata")
        self.last_metadata = {}
        self.log_interval_counter = 0
    
    async def update_device_info(self, device_info: Dict[str, Any]) -> None:
        """
        Met à jour les informations sur le périphérique connecté.
        
        Args:
            device_info: Informations sur le périphérique
        """
        if not device_info:
            return
        
        # Vérifier si les informations ont changé
        if device_info == self.last_metadata:
            return
        
        # Mettre à jour les dernières métadonnées
        self.last_metadata = device_info.copy()
        
        # Logs moins fréquents
        self.log_interval_counter = (self.log_interval_counter + 1) % 3
        if self.log_interval_counter == 0:
            self.logger.info(f"Informations sur le périphérique mises à jour: {device_info.get('deviceName', 'Inconnu')}")
        
        # Publier les informations comme métadonnées
        await self.event_bus.publish("audio_metadata_updated", {
            "source": self.source_name,
            "metadata": device_info
        })
    
    async def publish_status(self, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un événement de statut sur le bus d'événements.
        
        Args:
            status: Statut à publier (connected, disconnected, etc.)
            details: Détails supplémentaires du statut
        """
        # Déterminer si le statut implique une connexion
        is_connected = status in ["connected", "active"]
        
        # Créer un objet avec les informations essentielles
        status_data = {
            "source": self.source_name,
            "status": status,
            "connected": is_connected,
            "deviceConnected": is_connected,
            "plugin_state": status  # Ajouter l'état pour compatibilité
        }
        
        # Ajouter les détails supplémentaires
        if details:
            for key, value in details.items():
                status_data[key] = value
        
        # Publier l'événement
        self.logger.debug(f"Publication du statut: {status}")
        await self.event_bus.publish("audio_status_updated", status_data)
        
        # Si déconnecté, effacer les métadonnées
        if status == "disconnected":
            self.last_metadata = {}
    
    # Nouvelle méthode compatible avec la standardisation
    async def publish_plugin_state(self, state: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un état standardisé sur le bus d'événements.
        
        Args:
            state: État standardisé (inactive, ready_to_connect, etc.)
            details: Détails supplémentaires à inclure
        """
        # Déterminer l'état de connexion en fonction de l'état standardisé
        is_connected = state == "connected"
        
        # Créer un objet avec les informations essentielles
        status_data = {
            "source": self.source_name,
            "plugin_state": state,
            "status": state,  # Pour compatibilité avec le code existant
            "connected": is_connected,
            "deviceConnected": is_connected
        }
        
        # Ajouter les détails supplémentaires
        if details:
            status_data.update(details)
        
        # Publier l'événement
        self.logger.debug(f"Publication de l'état standardisé: {state}")
        await self.event_bus.publish("audio_status_updated", status_data)
        
        # Si déconnecté, effacer les métadonnées
        if state == "inactive":
            self.last_metadata = {}
    
    def get_last_metadata(self) -> Dict[str, Any]:
        """
        Récupère les dernières métadonnées.
        
        Returns:
            Dict[str, Any]: Dernières métadonnées
        """
        return self.last_metadata