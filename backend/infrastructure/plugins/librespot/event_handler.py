"""
Gestionnaire d'événements pour le plugin librespot.
"""
import logging
import time
from typing import Dict, Any, Optional, Callable

class EventHandler:
    """Gère les événements reçus de go-librespot via WebSocket"""
    
    def __init__(self, 
                 metadata_processor, 
                 connection_status_callback: Optional[Callable[[bool], None]] = None):
        self.metadata_processor = metadata_processor
        self.connection_status_callback = connection_status_callback
        self.logger = logging.getLogger("librespot.events")
        self.last_seek_timestamp = 0
    
    async def handle_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Traite un événement WebSocket reçu de go-librespot.
        
        Args:
            event_type: Type d'événement
            event_data: Données de l'événement
        """
        self.logger.info(f"Événement WebSocket go-librespot reçu: {event_type}")
        self.logger.debug(f"Données de l'événement: {event_data}")
        
        # État de connexion persistant
        connection_state = True
        
        # Si un callback d'état de connexion est défini, l'appeler
        if self.connection_status_callback:
            self.connection_status_callback(connection_state)
        
        if event_type == 'active':
            # Appareil devient actif - TOUJOURS considérer comme connecté
            await self.metadata_processor.publish_status("active", {
                "connected": True,
                "deviceConnected": True
            })
            
        elif event_type == 'inactive':
            # Appareil devient inactif - toujours publier l'événement
            await self.metadata_processor.publish_status("inactive", {
                "connected": False,
                "deviceConnected": False
            })
                
        elif event_type == 'metadata':
            # Nouvelles métadonnées de piste - TOUJOURS marquer comme connecté
            metadata = await self.metadata_processor.extract_from_event(event_type, event_data)
            if metadata:
                await self.metadata_processor.publish_metadata(metadata)
            
        elif event_type in ['will_play', 'playing']:
            # Lecture en cours - TOUJOURS marquer comme connecté
            await self.metadata_processor.publish_status("playing", {
                "is_playing": True,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            
            # Force immédiate log pour débogage
            self.logger.info("Événement playing reçu - État mis à jour: is_playing=True")
                
        elif event_type == 'paused':
            # Lecture en pause - maintenir l'état connecté
            await self.metadata_processor.publish_status("paused", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            
            # Force immédiate log pour débogage
            self.logger.info("Événement paused reçu - État mis à jour: is_playing=False")
            
        elif event_type == 'seek':
            # Changement de position dans la piste
            await self._handle_seek_event(event_data)
                
        elif event_type == 'stopped':
            # Lecture arrêtée - maintenir l'état connecté
            await self.metadata_processor.publish_status("stopped", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True
            })
            
        else:
            # Autres événements non gérés spécifiquement
            self.logger.debug(f"Événement non traité spécifiquement: {event_type}")
    
    async def _handle_seek_event(self, event_data: Dict[str, Any]) -> None:
        """
        Gère les événements de position (seek) avec plus de précision.
        
        Args:
            event_data: Données de l'événement de seek
        """
        position = event_data.get("position")
        duration = event_data.get("duration")
        
        if position is not None:
            # Créer un dictionnaire avec les données de seek enrichies
            seek_data = {
                "position_ms": position,
                "duration_ms": duration,
                "seek_timestamp": int(time.time() * 1000),  # Timestamp en millisecondes
                "track_uri": event_data.get("uri"),
                "connected": True,
                "deviceConnected": True,
                "source": self.metadata_processor.source_name  # Ajouter la source pour le filtrage côté frontend
            }
            
            # Enregistrer le timestamp du dernier seek
            self.last_seek_timestamp = seek_data["seek_timestamp"]
            
            # Publier un événement spécifique de seek via le bus d'événements
            await self.metadata_processor.event_bus.publish("audio_seek", seek_data)
            
            self.logger.info(f"Événement de seek traité: position={position}ms, durée={duration}ms")