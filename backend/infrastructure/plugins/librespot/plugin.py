"""
Plugin librespot optimisé pour oakOS - Version restructurée et simplifiée
"""
import os
import yaml
import asyncio
import aiohttp
import json
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import WebSocketManager

class LibrespotPlugin(UnifiedAudioPlugin):
    """Plugin Spotify via go-librespot - Optimisé avec la nouvelle architecture"""
    
    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.service_name = config.get("service_name")
        self.config_path = os.path.expanduser(config.get("config_path"))
        
        # État
        self.api_url = None
        self.ws_url = None
        self.session = None
        self._metadata = {}
        self._is_playing = False
        self._device_connected = False
        self._ws_connected = False
        
        # Créer le gestionnaire WebSocket
        self.ws_manager = WebSocketManager(self.logger)
    
    async def _do_initialize(self) -> bool:
        """Initialisation spécifique à go-librespot"""
        try:
            # Vérifier l'existence du service
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} non trouvé")
            
            # Lire la configuration pour récupérer le device actuel
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            self._current_device = config.get('audio_device', 'oakos_spotify')
            
            server = config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)
            
            self.api_url = f"http://{addr}:{port}"
            self.ws_url = f"ws://{addr}:{port}/events"
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            return False
    
    async def _do_start(self) -> bool:
        """Démarrage spécifique à go-librespot"""
        try:
            # Démarrer le service
            if not await self.control_service(self.service_name, "start"):
                return False
            
            # Créer la session HTTP
            self.session = aiohttp.ClientSession()
            
            # Démarrer la connexion WebSocket
            await self._start_websocket()
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin"""
        try:
            # Arrêter le WebSocket
            await self.ws_manager.stop()
            
            # Fermer la session HTTP
            if self.session:
                await self.session.close()
                self.session = None
            
            # Arrêter le service
            await self.control_service(self.service_name, "stop")
            
            # Réinitialiser l'état
            self._ws_connected = False
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt: {e}")
            return False
    
    async def change_audio_device(self, new_device: str) -> bool:
        """Change le device audio de go-librespot - Force reconnexion WebSocket"""
        if self._current_device == new_device:
            self.logger.info(f"Librespot device already set to {new_device}")
            return True
        
        try:
            self.logger.info(f"Changing librespot device from {self._current_device} to {new_device}")
            
            # Mettre à jour le device
            self._current_device = new_device
            
            # OPTIM: Fermer et rouvrir la connexion WebSocket pour le nouveau processus
            if self.session:
                await self.ws_manager.stop()  # Ferme l'ancienne connexion
                await asyncio.sleep(0.5)      # Attendre que le nouveau service soit prêt
                await self._start_websocket() # Rouvrir sur le nouveau processus
            
            return True
        except Exception as e:
            self.logger.error(f"Error changing device: {e}")
            return False
    
    async def _start_websocket(self) -> None:
        """Démarre la connexion WebSocket"""
        # Définir les fonctions de callback
        async def connect_func():
            try:
                # OPTIM: Force sync état initial avec le nouveau processus
                self._device_connected = False  # Reset avant sync
                self._is_playing = False
                self._metadata = {}
                
                await self._refresh_metadata()  # Lit l'état réel du nouveau processus
                
                # Notifier l'état correct selon ce qu'on a trouvé
                if self._device_connected:
                    await self.notify_state_change(PluginState.CONNECTED, self._metadata)
                else:
                    await self.notify_state_change(PluginState.READY, {"device_connected": False})
                
                return True
            except Exception as e:
                self.logger.error(f"Erreur connexion: {e}")
                return False
                
        async def process_func():
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self._ws_connected = True
                    self.logger.info(f"WebSocket connecté à {self.ws_url}")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_event(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
            finally:
                self._ws_connected = False
        
        # Démarrer le gestionnaire WebSocket
        await self.ws_manager.start(connect_func, process_func)
    
    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get("type")
        data = event.get("data", {})
        
        handlers = {
            "active": self._handle_active_state,
            "inactive": self._handle_inactive_state,
            "playing": lambda: self._handle_playback_state(True),
            "paused": lambda: self._handle_playback_state(False),
            "metadata": self._handle_metadata_update,
            "seek": lambda: self._handle_seek_update(data)
        }
        
        handler = handlers.get(event_type)
        if handler:
            await handler()
    
    async def _handle_active_state(self):
        """Traite l'événement 'device active'"""
        self._device_connected = True
        await self.notify_state_change(PluginState.CONNECTED, {"device_connected": True})
    
    async def _handle_inactive_state(self):
        """Traite l'événement 'device inactive'"""
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        await self.notify_state_change(PluginState.READY, {"device_connected": False})
    
    async def _handle_playback_state(self, is_playing):
        """Traite les événements de lecture/pause"""
        self._is_playing = is_playing
        self._device_connected = True
        
        if self._metadata:
            self._metadata["is_playing"] = is_playing
            
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "is_playing": is_playing
        })
    
    async def _handle_metadata_update(self):
        """Traite l'événement de mise à jour des métadonnées"""
        await self._refresh_metadata()
        await self.notify_state_change(PluginState.CONNECTED, self._metadata)
    
    async def _handle_seek_update(self, data):
        """Traite l'événement de recherche dans la piste"""
        if self._metadata:
            self._metadata["position"] = data.get("position", 0)
            
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "position": data.get("position", 0)
        })
    
    async def _refresh_metadata(self) -> bool:
        """Rafraîchit les métadonnées depuis l'API REST"""
        if not self.session:
            return False
            
        try:
            async with self.session.get(f"{self.api_url}/status") as resp:
                if resp.status != 200:
                    return False
                    
                data = await resp.json()
                
                # Mettre à jour l'état de connexion
                self._device_connected = bool(data.get("track"))
                self._is_playing = not data.get("paused", True)
                
                # Extraire les métadonnées
                if data.get("track"):
                    track = data["track"]
                    self._metadata = {
                        "title": track.get("name"),
                        "artist": ", ".join(track.get("artist_names", [])),
                        "album": track.get("album_name"),
                        "album_art_url": track.get("album_cover_url"),
                        "duration": track.get("duration", 0),
                        "position": track.get("position", 0),
                        "uri": track.get("uri"),
                        "is_playing": self._is_playing
                    }
                else:
                    self._metadata = {}
                
                return True
        except Exception as e:
            self.logger.error(f"Erreur rafraîchissement métadonnées: {e}")
            return False
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes"""
        # Commandes simples
        if command == "restart_service":
            return await self._restart_service()
            
        elif command == "refresh_metadata":
            success = await self._refresh_metadata()
            return self.format_response(
                success=success,
                message="Métadonnées rafraîchies" if success else "Échec du rafraîchissement",
                metadata=self._metadata
            )
            
        elif command == "seek" and "position_ms" in data:
            return await self._send_command("seek", {"position": data["position_ms"]})
            
        # Commandes de lecture simples
        elif command in ["play", "pause", "resume", "playpause"]:
            return await self._send_command(command)
            
        # Commandes next/prev avec URI optionnel
        elif command in ["next", "prev"]:
            payload = {"uri": data.get("uri")} if data.get("uri") else {}
            return await self._send_command(command, payload)
            
        # Commande inconnue
        return self.format_response(False, error=f"Commande non supportée: {command}")
    
    async def _restart_service(self) -> Dict[str, Any]:
        """Redémarre le service"""
        success = await self.control_service(self.service_name, "restart")
        return self.format_response(
            success=success,
            message="Service redémarré" if success else "Échec du redémarrage"
        )
    
    async def _send_command(self, command: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """Envoie une commande à l'API REST"""
        if not self.session:
            return self.format_response(False, error="Session inactive")
            
        try:
            async with self.session.post(
                f"{self.api_url}/player/{command}", 
                json=payload or {}
            ) as resp:
                return self.format_response(resp.status == 200)
        except Exception as e:
            return self.format_response(False, error=str(e))
    
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
                "current_device": self._current_device
            }
        except Exception as e:
            self.logger.error(f"Erreur status: {e}")
            return {
                "device_connected": False,
                "ws_connected": False,
                "metadata": {},
                "is_playing": False,
                "current_device": self._current_device,
                "error": str(e)
            }
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial pour les WebSockets"""
        if self.session:
            await self._refresh_metadata()
        
        return await self.get_status()