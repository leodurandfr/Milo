"""
Gestion des événements WebSocket - Version optimisée.
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
            "librespot_metadata_updated": "librespot_metadata_updated",
            "librespot_status_updated": "librespot_status_updated",
            "librespot_seek": "librespot_seek",
            
            # Événements spécifiques à Snapclient
            "snapclient_monitor_connected": "snapclient_monitor_connected",
            "snapclient_monitor_disconnected": "snapclient_monitor_disconnected",
            "snapclient_server_event": "snapclient_server_event",
            "snapclient_server_disappeared": "snapclient_server_disappeared",
            
            # AJOUT : Nouvel événement pour l'état des plugins
            "audio_plugin_state_changed": "audio_plugin_state_changed"
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
                
                # Liste des événements critiques qui nécessitent une diffusion immédiate
                critical_events = [
                    'snapclient_monitor_disconnected',
                    'snapclient_server_disappeared'
                ]
                
                # Diffusion prioritaire pour les événements critiques
                if internal_event in critical_events:
                    await self.ws_manager.broadcast(ws_event, data)
                    self._log_event(internal_event, data)
                else:
                    self._log_event(internal_event, data)
                    await self.ws_manager.broadcast(ws_event, data)
                    
            except Exception as e:
                self.logger.error(f"Erreur lors de la diffusion de l'événement {ws_event}: {e}")
        
        return handler
    
    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Gère les logs de manière spécifique selon le type d'événement."""
        # Pour les événements snapclient critiques, loguer en info
        if event_type in ["snapclient_monitor_disconnected", "snapclient_server_disappeared"]:
            self.logger.info(f"⚡ {event_type}: {data.get('host', 'unknown')}, raison: {data.get('reason', 'unknown')}")
        # Pour les événements d'état audio, loguer en info
        elif event_type == "librespot_status_updated" and data.get("source") == "snapclient":
            self.logger.info(f"État audio mis à jour: {data.get('plugin_state', 'unknown')} - {data.get('source', 'unknown')}")
        # Pour les événements de monitoring, loguer en info
        elif event_type == "snapclient_monitor_connected":
            self.logger.info(f"⚡ Moniteur connecté au serveur: {data.get('host', 'unknown')}")
        # Pour les autres événements, loguer en debug
        else:
            self.logger.debug(f"Événement {event_type} reçu: {data}")