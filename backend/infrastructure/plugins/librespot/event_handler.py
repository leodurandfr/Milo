"""
Gestionnaire d'événements pour le plugin librespot - Version sans polling.
"""
import logging
import time
from typing import Dict, Any, Optional, Callable

class EventHandler:
    """Gère les événements reçus de go-librespot via WebSocket"""
    
    def __init__(self, 
                 metadata_processor, 
                 connection_status_callback: Optional[Callable[[bool], None]] = None,
                 api_client = None):
        self.metadata_processor = metadata_processor
        self.connection_status_callback = connection_status_callback
        self.api_client = api_client
        self.logger = logging.getLogger("librespot.events")
        self.last_seek_timestamp = 0
        self.min_seek_interval_ms = 100  # Intervalle minimum entre deux événements seek (100ms)
        self.is_connected = False
    
    async def check_initial_state(self) -> None:
        """
        Effectue une vérification initiale unique de l'état à partir de l'API.
        """
        if not self.api_client:
            self.logger.warning("API client non disponible, impossible de vérifier l'état initial")
            return
            
        try:
            self.logger.info("Vérification initiale de l'état de connexion")
            status = await self.api_client.fetch_status()
            
            # Vérifier la connexion dans le statut
            is_connected = bool(status.get("username")) or bool(status.get("connected")) or bool(status.get("deviceConnected"))
            
            # Vérifier si le player a des données
            if status.get("player", {}).get("current_track"):
                is_connected = True
            
            # Mettre à jour l'état de connexion
            self.is_connected = is_connected
            
            if self.connection_status_callback:
                self.connection_status_callback(is_connected)
                
            # Si connecté, publier les métadonnées initiales
            if is_connected:
                metadata = await self.metadata_processor.extract_from_status(status)
                if metadata:
                    await self.metadata_processor.publish_metadata(metadata)
                    
                    # Publier l'état initial
                    is_playing = metadata.get("is_playing", False)
                    await self.metadata_processor.publish_status(
                        "playing" if is_playing else "paused",
                        {
                            "is_playing": is_playing,
                            "connected": True,
                            "deviceConnected": True
                        }
                    )
                else:
                    # Publier juste l'état connecté
                    await self.metadata_processor.publish_status("connected", {
                        "connected": True,
                        "deviceConnected": True
                    })
            else:
                # Publier l'état déconnecté
                await self.metadata_processor.publish_status("disconnected", {
                    "connected": False,
                    "deviceConnected": False
                })
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la vérification initiale: {e}")
    
    async def handle_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """
        Traite un événement WebSocket reçu de go-librespot.
        
        Args:
            event_type: Type d'événement
            event_data: Données de l'événement
        """
        self.logger.info(f"Événement WebSocket reçu: {event_type}")
        
        # Traiter les événements qui indiquent une connexion
        if event_type in ['active', 'metadata', 'will_play', 'playing', 'paused', 'seek']:
            if not self.is_connected:
                self.is_connected = True
                if self.connection_status_callback:
                    self.connection_status_callback(True)
                    
        # Traiter les événements spécifiques
        if event_type == 'active':
            await self.metadata_processor.publish_status("active", {
                "connected": True,
                "deviceConnected": True
            })
            
        elif event_type == 'inactive':
            # Cet événement peut indiquer une déconnexion
            self.is_connected = False
            if self.connection_status_callback:
                self.connection_status_callback(False)
                
            await self.metadata_processor.publish_status("inactive", {
                "connected": False,
                "deviceConnected": False
            })
                
        elif event_type == 'metadata':
            metadata = await self.metadata_processor.extract_from_event(event_type, event_data)
            if metadata:
                await self.metadata_processor.publish_metadata(metadata)
            
        elif event_type in ['will_play', 'playing']:
            await self.metadata_processor.publish_status("playing", {
                "is_playing": True,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
                
        elif event_type == 'paused':
            await self.metadata_processor.publish_status("paused", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            
        elif event_type == 'seek':
            await self._handle_seek_event(event_data)
                
        elif event_type == 'stopped':
            await self.metadata_processor.publish_status("stopped", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True
            })
            
        elif event_type == 'ws_connected':
            self.logger.info("Connexion WebSocket établie")
            # Vérifier l'état après connexion WebSocket
            await self.check_initial_state()
            
        elif event_type == 'ws_disconnected':
            self.logger.warning("Connexion WebSocket perdue")
            # Une déconnexion WebSocket n'implique pas nécessairement une déconnexion du device
            
        elif event_type == 'connection_lost':
            # Événement spécial envoyé par le client WebSocket en cas de déconnexion prolongée
            self.is_connected = False
            if self.connection_status_callback:
                self.connection_status_callback(False)
                
            await self.metadata_processor.publish_status("disconnected", {
                "connected": False,
                "deviceConnected": False
            })
    
    async def _handle_seek_event(self, event_data: Dict[str, Any]) -> None:
        """
        Gère les événements de position (seek) avec limitation de fréquence.
        
        Args:
            event_data: Données de l'événement de seek
        """
        position = event_data.get("position")
        duration = event_data.get("duration")
        
        if position is not None:
            # Vérifier si l'événement est trop proche du précédent
            current_time = int(time.time() * 1000)
            time_since_last_seek = current_time - self.last_seek_timestamp
            
            # Ignorer les événements trop fréquents
            if time_since_last_seek < self.min_seek_interval_ms:
                return
            
            # Créer un dictionnaire avec les données de seek enrichies
            seek_data = {
                "position_ms": position,
                "duration_ms": duration,
                "seek_timestamp": current_time,
                "track_uri": event_data.get("uri"),
                "connected": True,
                "deviceConnected": True,
                "source": self.metadata_processor.source_name
            }
            
            # Enregistrer le timestamp du dernier seek
            self.last_seek_timestamp = current_time
            
            # Publier un événement spécifique de seek via le bus d'événements
            await self.metadata_processor.event_bus.publish("audio_seek", seek_data)