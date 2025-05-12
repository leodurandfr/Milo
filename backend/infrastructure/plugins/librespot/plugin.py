"""
Plugin librespot optimisé pour oakOS
"""
import asyncio
import os
import yaml
import aiohttp
import json
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.plugins.plugin_utils import safely_control_service, format_response


class LibrespotPlugin(UnifiedAudioPlugin):
    """Plugin Spotify via go-librespot - Version optimisée"""
    
    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.service_name = config.get("service_name")
        self.config_path = os.path.expanduser(config.get("config_path"))
        self.service_manager = SystemdServiceManager()
        
        # État
        self.api_url = None
        self.ws_url = None
        self.ws_task = None
        self.session = None
        self._metadata = {}
        self._is_playing = False
        self._device_connected = False
        self._ws_connected = False
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialise le plugin"""
        if self._initialized:
            return True
            
        try:
            # Vérifier l'existence du service
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise FileNotFoundError(f"Service not found: {self.service_name}")
            
            # Lire la configuration
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            server = config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)
            
            self.api_url = f"http://{addr}:{port}"
            self.ws_url = f"ws://{addr}:{port}/events"
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def start(self) -> bool:
        """Démarre le plugin"""
        try:
            if not await safely_control_service(self.service_manager, self.service_name, "start", self.logger):
                raise RuntimeError(f"Impossible de démarrer le service {self.service_name}")
            
            self.session = aiohttp.ClientSession()
            self.ws_task = asyncio.create_task(self._websocket_loop())
            
            await self.notify_state_change(PluginState.READY)
            return True
        except Exception as e:
            self.logger.error(f"Erreur de démarrage: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin"""
        try:
            # Nettoyage des ressources
            if self.ws_task:
                self.ws_task.cancel()
                try:
                    await self.ws_task
                except asyncio.CancelledError:
                    pass
                self.ws_task = None
            
            if self.session:
                await self.session.close()
                self.session = None
            
            await safely_control_service(self.service_manager, self.service_name, "stop", self.logger)
            
            # Réinitialiser l'état
            self._metadata = {}
            self._ws_connected = False
            self._device_connected = False
            self._is_playing = False
            
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'arrêt: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def _websocket_loop(self) -> None:
        """Boucle WebSocket pour recevoir les événements"""
        retry_delay = 1
        max_delay = 30
        
        while True:
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("WebSocket connecté à go-librespot")
                    self._ws_connected = True
                    retry_delay = 1
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_websocket_event(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_connected = False
                self.logger.error(f"Erreur WebSocket: {e}")
                await asyncio.sleep(retry_delay)
                retry_delay = min(max_delay, retry_delay * 2)
    
    async def _get_api_data(self) -> Optional[Dict[str, Any]]:
        """Récupère les données depuis l'API"""
        if not self.session:
            return None
            
        try:
            async with self.session.get(f"{self.api_url}/status") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    self._device_connected = bool(data.get('track'))
                    self._is_playing = not data.get('paused', True)
                    
                    if data.get('track'):
                        track = data['track']
                        self._metadata = {
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
                        self._metadata = {}
                    
                    return data
                return None
        except Exception as e:
            self.logger.error(f"Erreur API: {e}")
            return None
    
    async def _handle_websocket_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get('type')
        data = event.get('data', {})
        
        # Tableau de correspondance type d'événement -> état et actions
        handlers = {
            'active': lambda: self._handle_active_state(),
            'inactive': lambda: self._handle_inactive_state(),
            'playing': lambda: self._handle_playback_state(True),
            'paused': lambda: self._handle_playback_state(False),
            'metadata': lambda: self._handle_metadata_update(),
            'seek': lambda: self._handle_seek_update(data)
        }
        
        handler = handlers.get(event_type)
        if handler:
            await handler()
    
    async def _handle_active_state(self):
        self._device_connected = True
        await self.notify_state_change(PluginState.CONNECTED, {
            "device_connected": True,
            "status": "active"
        })
    
    async def _handle_inactive_state(self):
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        await self.notify_state_change(PluginState.READY, {
            "device_connected": False,
            "status": "inactive"
        })
    
    async def _handle_playback_state(self, is_playing):
        self._is_playing = is_playing
        self._device_connected = True
        
        if self._metadata:
            self._metadata['is_playing'] = is_playing
        
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "status": "playing" if is_playing else "paused"
        })
    
    async def _handle_metadata_update(self):
        await self._get_api_data()
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "status": "metadata_updated"
        })
    
    async def _handle_seek_update(self, data):
        if self._metadata:
            self._metadata['position'] = data.get('position', 0)
        
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "position": data.get('position', 0),
            "status": "seek_update"
        })
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes avec réponses standardisées"""
        if not self.session:
            return format_response(success=False, error="Plugin not active")
        
        try:
            # Commandes spéciales
            if command == "restart_service":
                success = await safely_control_service(self.service_manager, self.service_name, "restart", self.logger)
                return format_response(success=success, message="Service redémarré" if success else "Échec du redémarrage")
                
            elif command == "refresh_metadata":
                api_data = await self._get_api_data()
                return format_response(
                    success=bool(api_data),
                    message="Métadonnées rafraîchies" if api_data else "Échec du rafraîchissement",
                    metadata=self._metadata
                )
                
            elif command == "seek" and data.get("position_ms") is not None:
                async with self.session.post(
                    f"{self.api_url}/player/seek", 
                    json={"position": data["position_ms"]}
                ) as resp:
                    return format_response(success=resp.status == 200)
                    
            # Commandes de lecture standardisées
            elif command in ["play", "resume", "pause", "playpause", "next", "prev"]:
                payload = {}
                if command in ["next", "prev"] and data.get("uri"):
                    payload = {"uri": data["uri"]}
                
                async with self.session.post(f"{self.api_url}/player/{command}", json=payload) as resp:
                    return format_response(success=resp.status == 200)
            
            return format_response(success=False, error=f"Commande non supportée: {command}")
        except Exception as e:
            return format_response(success=False, error=str(e))
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            
            return {
                "device_connected": self._device_connected,
                "ws_connected": self._ws_connected,
                "is_playing": self._is_playing,
                "metadata": self._metadata,
                "service_active": service_status.get("active", False),
                "service_state": service_status.get("state", "unknown"),
                "service_error": service_status.get("error")
            }
        except Exception as e:
            self.logger.error(f"Erreur status: {e}")
            return {
                "device_connected": False,
                "ws_connected": False,
                "metadata": {},
                "is_playing": False,
                "error": str(e)
            }
            
    async def get_initial_state(self) -> Dict[str, Any]:
        """Récupère l'état initial complet du plugin"""
        if self.session:
            await self._get_api_data()
        
        return await self.get_status()