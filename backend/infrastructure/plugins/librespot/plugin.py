# backend/infrastructure/plugins/librespot/plugin.py
"""
Plugin librespot adapté pour utiliser systemd
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
from backend.infrastructure.services.systemd_manager import SystemdServiceManager


class LibrespotPlugin(UnifiedAudioPlugin):
    """Plugin Spotify via go-librespot - Version avec gestion systemd"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.ws_task = None
        self.session = None
        
        # Configuration
        self.config_path = os.path.expanduser(config.get("config_path"))
        self.service_name = config.get("service_name")
        
        # Gestionnaire de service systemd
        self.service_manager = SystemdServiceManager()
        
        # Informations d'API
        self.api_url = None
        self.ws_url = None
        
        # État interne
        self._current_metadata = {}
        self._is_playing = False  
        self._device_connected = False
        self._ws_connected = False
        self._initialized = False
    
    
    async def initialize(self) -> bool:
        """Initialise le plugin"""
        if self._initialized:
            return True
            
        try:
            # Vérifier si le service systemd existe
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise FileNotFoundError(f"Service not found: {self.service_name}")
            
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
        """Démarre le plugin avec gestion systemd"""
        try:
            # Démarrer le service go-librespot via systemd
            if not await self.service_manager.start(self.service_name):
                raise RuntimeError(f"Impossible de démarrer le service {self.service_name}")
            
            # Créer la session HTTP
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            # Démarrer la connexion WebSocket
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
        """Arrête le plugin avec gestion systemd"""
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
            
            # Arrêter le service go-librespot (optionnel, selon votre configuration)
            # Si vous souhaitez que go-librespot reste actif même quand oakOS n'utilise pas ce plugin,
            # vous pouvez commenter cette ligne
            await self.service_manager.stop(self.service_name)
            
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
                    self._ws_connected = True
                    
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
    
    async def _get_api_data(self) -> Optional[Dict[str, Any]]:
        """Récupère les données depuis l'API - Simplifié pour toujours récupérer tout"""
        if not self.session:
            self.logger.warning("No session available for API call")
            return None
            
        try:
            async with self.session.get(f"{self.api_url}/status") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Mise à jour des états de base
                    self._device_connected = bool(data.get('track'))
                    self._is_playing = not data.get('paused', True)
                    
                    # Mise à jour des métadonnées si track présent
                    if data.get('track'):
                        track = data['track']
                        self._current_metadata = {
                            "title": track.get('name'),
                            "artist": ", ".join(track.get('artist_names', [])),
                            "album": track.get('album_name'),
                            "album_art_url": track.get('album_cover_url'),
                            "duration": track.get('duration', 0),
                            "position": track.get('position', 0),
                            "uri": track.get('uri'),
                            "is_playing": self._is_playing
                        }
                    else:
                        self._current_metadata = {}
                    
                    return data
                else:
                    self.logger.error(f"API returned status {resp.status}")
                    return None
        except Exception as e:
            self.logger.error(f"Erreur récupération données API: {e}")
            return None
    
    async def _handle_websocket_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get('type')
        data = event.get('data', {})
        
        self.logger.debug(f"Événement WebSocket reçu: {event_type}")
        
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
        
        elif event_type in ['playing', 'paused']:
            self._is_playing = (event_type == 'playing')
            self._device_connected = True
            
            # Mise à jour de l'état
            if self._current_metadata:
                self._current_metadata['is_playing'] = self._is_playing
            
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "status": event_type
            })
        
        elif event_type == 'metadata':
            # Pour metadata, on fait une mise à jour complète depuis l'API
            await self._get_api_data()
            
            await self.notify_state_change(PluginState.CONNECTED, {
                **self._current_metadata,
                "status": "metadata_updated"
            })
        
        elif event_type == 'seek':
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
            if command == "restart_service":
                success = await self.service_manager.restart(self.service_name)
                return {
                    "success": success,
                    "message": "Service redémarré avec succès" if success else "Échec du redémarrage du service"
                }
            elif command in ["play", "resume", "pause", "playpause", "next", "prev"]:
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
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            
            status = {
                "device_connected": self._device_connected,
                "ws_connected": self._ws_connected,
                "is_playing": self._is_playing,
                "metadata": self._current_metadata,
                "service_active": service_status.get("active", False),
                "service_state": service_status.get("state", "unknown"),
                "service_error": service_status.get("error")
            }
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
            
    async def get_initial_state(self) -> Dict[str, Any]:
        """Récupère l'état initial complet du plugin depuis l'API - force toujours un appel API"""
        self.logger.info("Getting initial state - forcing API refresh")
        
        # Force toujours un appel API pour avoir l'état le plus récent
        if self.session:
            await self._get_api_data()
        else:
            self.logger.warning("No session available for initial state")
        
        return await self.get_status()