# backend/infrastructure/plugins/librespot/plugin.py
"""
Plugin librespot adapté au contrôle centralisé des processus
"""
import asyncio
import logging
import os
import yaml
import aiohttp
import json
from typing import Dict, Any, Optional, List
from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState


class LibrespotPlugin(UnifiedAudioPlugin):
    """Plugin Spotify via go-librespot - Version avec contrôle centralisé"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.ws_task = None
        self.session = None
        self.config_path = os.path.expanduser(config.get("config_path", "~/.config/go-librespot/config.yml"))
        self.executable_path = os.path.expanduser(config.get("executable_path", "~/oakOS/go-librespot/go-librespot"))
        self.api_url = None
        self.ws_url = None
        self._current_metadata = {}
        self._is_playing = False  
        self._device_connected = False
        self._ws_connected = False
        self._initialized = False
    
    async def _handle_ws_event(self, data: Dict[str, Any]) -> None:
        """Gère les événements WebSocket avec persistance de l'état"""
        event_type = data.get("event")
        
        if event_type == "active":
            self._device_connected = True
            self._ws_connected = True
            await self.notify_state_change(PluginState.CONNECTED, {
                "device_connected": True,
                "message": "Device connected via librespot"
            })
        
        elif event_type == "inactive":
            self._device_connected = False
            self._is_playing = False
            self._current_metadata = {}
            await self.notify_state_change(PluginState.READY, {
                "device_connected": False,
                "message": "Device disconnected from librespot"
            })
        
        elif event_type == "metadata":
            # Persister les métadonnées
            self._current_metadata = data
            if data.get("metadata", {}).get("name"):
                await self.notify_state_change(PluginState.PLAYING, data)
        
        elif event_type == "playing":
            self._is_playing = True
            metadata = {**self._current_metadata, "is_playing": True}
            await self.notify_state_change(PluginState.PLAYING, metadata)
        
        elif event_type == "paused":
            self._is_playing = False
            metadata = {**self._current_metadata, "is_playing": False}
            await self.notify_state_change(PluginState.PLAYING, metadata)
        
        elif event_type == "not_playing":
            self._is_playing = False  
    
    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer go-librespot"""
        return [self.executable_path]
    
    async def initialize(self) -> bool:
        """Initialise le plugin"""
        if self._initialized:
            return True
            
        try:
            # Lire la configuration go-librespot
            with open(self.config_path, 'r') as f:
                librespot_config = yaml.safe_load(f)
            
            server = librespot_config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)
            
            self.api_url = f"http://{addr}:{port}"
            self.ws_url = f"ws://{addr}:{port}/events"
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "initialization_error"
            })
            return False
    
    async def start(self) -> bool:
        """Démarre le plugin - le processus est géré par la machine à états"""
        try:
            # Créer la session HTTP si nécessaire
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            # Le processus est maintenant démarré par la machine à états
            # On démarre seulement la connexion WebSocket
            self.ws_task = asyncio.create_task(self._websocket_loop())
            
            # Notifier que le plugin est prêt
            await self.notify_state_change(PluginState.READY)
            return True
        except Exception as e:
            self.logger.error(f"Erreur de démarrage: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "start_error"
            })
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin - le processus est géré par la machine à états"""
        try:
            # Arrêter WebSocket
            if self.ws_task:
                self.ws_task.cancel()
                try:
                    await self.ws_task
                except asyncio.CancelledError:
                    pass
                self.ws_task = None
            
            # Fermer la session HTTP
            if self.session:
                await self.session.close()
                self.session = None
            
            # Nettoyer l'état
            self._current_metadata = {}
            
            # Notifier l'arrêt
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'arrêt: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "stop_error"
            })
            return False
    
    async def _websocket_loop(self) -> None:
        """Boucle WebSocket pour recevoir les événements"""
        while True:
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("WebSocket connecté à go-librespot")
                    self._ws_connected = True  # Marquer comme connecté
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            await self._handle_websocket_event(event)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error(f"Erreur WebSocket: {msg}")
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
                self._ws_connected = False
                await asyncio.sleep(3)
    
    async def _handle_websocket_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get('type')
        data = event.get('data', {})
        
        self.logger.debug(f"Événement WebSocket reçu: {event_type}")
        
        # Mise à jour de l'état en fonction de l'événement
        if event_type == 'active':
            self._device_connected = True
            await self.notify_state_change(PluginState.CONNECTED, {
                "device_connected": True,
                "status": "active"
            })
        
        elif event_type == 'inactive':
            self._device_connected = False
            self._is_playing = False
            self._current_metadata = {}
            await self.notify_state_change(PluginState.READY, {
                "device_connected": False,
                "status": "inactive"
            })
        
        elif event_type == 'playing':
            self._is_playing = True
            self._device_connected = True
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "is_playing": True,
                "status": "playing"
            })
        
        elif event_type == 'paused':
            self._is_playing = False
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "is_playing": False,
                "status": "paused"
            })
        
        elif event_type == 'metadata':
            # Stocker les métadonnées dans le bon format
            self._current_metadata = {
                "title": data.get('name'),
                "artist": ", ".join(data.get('artist_names', [])),
                "album": data.get('album_name'),
                "album_art_url": data.get('album_cover_url'),
                "duration": data.get('duration'),
                "position": data.get('position', 0),
                "uri": data.get('uri'),
                "is_playing": self._is_playing
            }
            
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "status": "metadata_updated"
            })
        
        elif event_type == 'seek':
            # Mettre à jour la position dans les métadonnées
            if self._current_metadata:
                self._current_metadata['position'] = data.get('position', 0)
            
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "position": data.get('position', 0),
                "status": "seek_update"
            })
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes"""
        if not self.session:
            return {"success": False, "error": "Plugin not active"}
        
        try:
            if command in ["play", "resume", "pause", "playpause", "next", "prev"]:
                payload = {} if command not in ["next", "prev"] else {}
                async with self.session.post(f"{self.api_url}/player/{command}", json=payload) as resp:
                    return {"success": resp.status == 200}
            
            elif command == "seek":
                position = data.get("position_ms")
                if position is not None:
                    async with self.session.post(f"{self.api_url}/player/seek", 
                                              json={"position": position}) as resp:
                        return {"success": resp.status == 200}
            
            return {"success": False, "error": f"Commande non supportée: {command}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_current_state_from_api(self) -> Dict[str, Any]:
        """Récupère l'état actuel depuis l'API go-librespot"""
        if not self.session:
            return {}
        
        try:
            async with self.session.get(f"{self.api_url}/status") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Mettre à jour les métadonnées internes
                    if data.get('track'):
                        track = data['track']
                        self._current_metadata = {
                            "title": track.get('name'),
                            "artist": ", ".join(track.get('artist_names', [])),
                            "album": track.get('album_name'),
                            "album_art_url": track.get('album_cover_url'),
                            "duration": track.get('duration'),
                            "position": track.get('position', 0),  # Position actuelle
                            "uri": track.get('uri'),
                            "is_playing": not data.get('paused', False)
                        }
                        self._is_playing = not data.get('paused', False)
                    return data
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération de l'état: {e}")
        return {}

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin avec métadonnées persistées"""
        try:
            status = {
                "device_connected": self._device_connected,
                "ws_connected": self._ws_connected,
                "is_playing": self._is_playing,
            }
            
            # Inclure les métadonnées actuelles
            if self._current_metadata:
                status["metadata"] = self._current_metadata
            else:
                status["metadata"] = {}
            
            return status
            
        except Exception as e:
            self.logger.error(f"Erreur récupération status: {str(e)}")
            return {
                "device_connected": False,
                "ws_connected": False,
                "metadata": {},
                "is_playing": False,
                "error": str(e)
            }