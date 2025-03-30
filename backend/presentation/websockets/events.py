"""
Gestion des événements WebSocket.
"""
import logging
import time
from typing import Dict, Any, Callable
from backend.application.event_bus import EventBus
from backend.presentation.websockets.manager import WebSocketManager


class WebSocketEventHandler:
    """Relais entre le bus d'événements et les WebSockets"""
    
    def __init__(self, event_bus: EventBus, ws_manager: WebSocketManager):
        self.event_bus = event_bus
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
        self.event_mappings: Dict[str, str] = {
            # Mapping entre événements internes et événements WebSocket
            "audio_state_changing": "audio_state_changing",
            "audio_state_changed": "audio_state_changed",
            "audio_transition_error": "audio_error",
            "volume_changed": "volume_changed",
            "audio_metadata_updated": "audio_metadata_updated",
            "audio_status_updated": "audio_status_updated",
            "audio_seek": "audio_seek",
            
            # Événements spécifiques à Snapclient
            "snapclient_monitor_connected": "snapclient_monitor_connected",
            "snapclient_monitor_disconnected": "snapclient_monitor_disconnected",
            "snapclient_server_event": "snapclient_server_event",
            "snapclient_server_disappeared": "snapclient_server_disappeared"
        }
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Enregistre les handlers pour les événements internes"""
        for internal_event, ws_event in self.event_mappings.items():
            self.event_bus.subscribe(
                internal_event,
                self._create_event_handler(ws_event, internal_event)
            )
    
    def _create_event_handler(self, ws_event: str, internal_event: str) -> Callable:
        """Crée un handler pour un type d'événement spécifique"""
        async def handler(data: Dict[str, Any]) -> None:
            try:
                # Ajouter un timestamp si non présent
                if 'timestamp' not in data:
                    data['timestamp'] = time.time()
                
                # Gérer les logs de manière spécifique selon le type d'événement
                self._log_event(internal_event, data)
                
                # Diffuser l'événement à tous les clients WebSocket
                await self.ws_manager.broadcast(ws_event, data)
            except Exception as e:
                self.logger.error(f"Erreur lors de la diffusion de l'événement {ws_event}: {e}")
        
        return handler
    
    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Gère les logs de manière spécifique selon le type d'événement."""
        # Pour les événements standard, utiliser des logs debug
        if event_type in ["audio_state_changing", "audio_state_changed"]:
            self.logger.info(f"Événement {event_type}: {data.get('from_state', 'unknown')} -> {data.get('current_state', data.get('to_state', 'unknown'))}")
            
        # Pour les mises à jour de métadonnées, ajouter des détails
        elif event_type == "audio_metadata_updated":
            self.logger.info(f"Métadonnées mises à jour: source={data.get('source')}, "
                           f"titre={data.get('metadata', {}).get('title')}, "
                           f"artiste={data.get('metadata', {}).get('artist')}")
        
        # Pour les événements de seek, ajouter des détails
        elif event_type == "audio_seek":
            self.logger.info(f"Seek détecté: position={data.get('position_ms')}ms, "
                           f"timestamp={data.get('seek_timestamp')}")
        
        # Pour les événements Snapclient spécifiques
        elif event_type == "snapclient_monitor_connected":
            self.logger.info(f"⚡ Moniteur connecté au serveur: {data.get('host')}")
            
        elif event_type == "snapclient_monitor_disconnected":
            self.logger.info(f"⚡ Moniteur déconnecté du serveur: {data.get('host')}, raison: {data.get('reason', 'unknown')}")
            
        elif event_type == "snapclient_server_disappeared":
            self.logger.info(f"⚡ Serveur disparu: {data.get('host')}")
            
        elif event_type == "snapclient_server_event":
            method = data.get("data", {}).get("method", "unknown")
            self.logger.debug(f"Événement serveur Snapcast: {method}")
            
        else:
            # Pour les autres événements, juste un log de base
            self.logger.debug(f"Événement {event_type} reçu: {data}")