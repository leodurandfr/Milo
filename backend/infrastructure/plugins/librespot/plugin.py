"""
Plugin librespot simplifié - Version WebSocket uniquement
"""
import asyncio
import logging
import os
import subprocess
import yaml
import aiohttp
import json
from typing import Dict, Any, Optional
from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin


class LibrespotPlugin(BaseAudioPlugin):
    """Plugin Spotify via go-librespot - Version simplifiée"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.process = None
        self.ws_task = None
        self.session = None
        self.config_path = os.path.expanduser(config.get("config_path", "~/.config/go-librespot/config.yml"))
        self.executable_path = os.path.expanduser(config.get("executable_path", "~/oakOS/go-librespot/go-librespot"))
        self.api_url = None
        self.ws_url = None
        # Ajout: stockage des dernières métadonnées reçues
        self.last_metadata = {}
        self.is_playing = False
        self.device_connected = False
    
    async def initialize(self) -> bool:
        """Initialise le plugin"""
        try:
            # Lire la configuration go-librespot
            with open(self.config_path, 'r') as f:
                librespot_config = yaml.safe_load(f)
            
            server = librespot_config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)
            
            self.api_url = f"http://{addr}:{port}"
            self.ws_url = f"ws://{addr}:{port}/events"
            
            self.session = aiohttp.ClientSession()
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            return False
    
    async def start(self) -> bool:
        """Démarre le plugin"""
        try:
            # S'assurer que la session existe (au cas où elle aurait été fermée dans stop())
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            # Démarrer le processus si nécessaire
            if not self._is_process_running():
                self.process = subprocess.Popen([self.executable_path])
                await asyncio.sleep(0.2)  # Attendre que le processus démarre
            
            # Démarrer la connexion WebSocket
            self.ws_task = asyncio.create_task(self._websocket_loop())
            self.is_active = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur de démarrage: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin"""
        try:
            self.is_active = False
            
            # Arrêter WebSocket
            if self.ws_task:
                self.ws_task.cancel()
                self.ws_task = None
            
            # Arrêter le processus
            if self.process:
                self.process.terminate()
                self.process = None
            
            if self.session:
                await self.session.close()
                self.session = None
            
            # Nettoyer les métadonnées et l'état
            self.last_metadata = {}
            self.is_playing = False
            self.device_connected = False
                
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'arrêt: {e}")
            return False
    
    def _is_process_running(self) -> bool:
        """Vérifie si le processus est en cours"""
        return self.process is not None and self.process.poll() is None
    
    async def _websocket_loop(self) -> None:
        """Boucle WebSocket pour recevoir les événements"""
        while self.is_active:
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("WebSocket connecté à go-librespot")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.logger.info(f"Message WebSocket brut reçu: {msg.data}")
                            event = json.loads(msg.data)
                            await self._handle_websocket_event(event)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error(f"Erreur WebSocket: {msg}")
                            break
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
                if self.is_active:
                    await asyncio.sleep(3)
    
    async def _handle_websocket_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get('type')
        data = event.get('data', {})
        
        self.logger.info(f"Événement WebSocket reçu: {event_type} - {data}")
        
        if event_type:
            self.logger.info(f"Type d'événement: {event_type}")
        
        # Mapping des événements
        if event_type == 'active':
            self.device_connected = True
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "active",
                "connected": True,
                "is_playing": self.is_playing
            })
        
        elif event_type == 'inactive':
            self.device_connected = False
            self.is_playing = False
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "inactive",
                "connected": False,
                "is_playing": False
            })
        
        elif event_type == 'playing':
            self.is_playing = True
            self.device_connected = True
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "playing",
                "connected": True,
                "is_playing": True
            })
        
        elif event_type == 'paused':
            self.is_playing = False
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "paused",
                "connected": True,
                "is_playing": False
            })
        
        elif event_type == 'stopped':
            self.is_playing = False
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "stopped",
                "connected": self.device_connected,
                "is_playing": False
            })
        
        elif event_type == 'not_playing':
            # Cette chanson s'est terminée, mais une autre peut suivre
            # Ne pas changer is_playing pour éviter le clignotement
            self.logger.info(f"Transition entre chansons: {data}")
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "track_ended",
                "connected": True,
                "is_playing": self.is_playing,  # Garder l'état actuel
                "track_uri": data.get('uri')
            })
        
        elif event_type == 'will_play':
            # Une nouvelle chanson va commencer
            self.logger.info(f"Préparation nouvelle chanson: {data}")
            self.device_connected = True
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "preparing",
                "connected": True,
                "is_playing": self.is_playing,  # Garder l'état actuel
                "track_uri": data.get('uri')
            })
        
        elif event_type == 'metadata':
            self.logger.info(f"Métadonnées reçues: {data}")
            
            # Stocker les métadonnées
            self.last_metadata = {
                "title": data.get('name'),
                "artist": ", ".join(data.get('artist_names', [])),
                "album": data.get('album_name'),
                "album_art_url": data.get('album_cover_url'),
                "duration_ms": data.get('duration'),
                "position_ms": data.get('position', 0),
                "uri": data.get('uri'),
                "is_playing": self.is_playing
            }
            
            # Publier les métadonnées
            await self.event_bus.publish("librespot_metadata_updated", {
                "source": self.name,
                "metadata": self.last_metadata
            })
            
            # Publier aussi un statut connected
            self.device_connected = True
            await self.event_bus.publish("librespot_status_updated", {
                "source": self.name,
                "status": "connected",
                "connected": True,
                "is_playing": self.is_playing
            })
        
        elif event_type == 'seek':
            # Mettre à jour la position dans last_metadata
            if 'position' in data:
                self.last_metadata['position_ms'] = data.get('position', 0)
            
            await self.event_bus.publish("librespot_seek", {
                "source": self.name,
                "position_ms": data.get('position', 0),
                "duration_ms": data.get('duration')
            })
        
        else:
            self.logger.info(f"Événement non géré: {event_type} - {data}")
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes"""
        if not self.is_active:
            return {"success": False, "error": "Plugin inactif"}
        
        try:
            if command in ["play", "resume", "pause", "playpause", "next", "prev"]:
                # Certaines commandes nécessitent un corps JSON même vide
                if command in ["next", "prev"]:
                    async with self.session.post(f"{self.api_url}/player/{command}", json={}) as resp:
                        return {"success": resp.status == 200}
                else:
                    async with self.session.post(f"{self.api_url}/player/{command}") as resp:
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
        """Retourne le statut actuel, incluant la position actuelle"""
        status = {
            "is_active": self.is_active,
            "plugin_state": self.current_state,
            "metadata": self.last_metadata.copy() if self.last_metadata else {},
            "is_playing": self.is_playing,
            "device_connected": self.device_connected
        }
        
        # Si on est connecté et en lecture, récupérer la position actuelle
        if self.is_active and self.device_connected and self.session:
            try:
                async with self.session.get(f"{self.api_url}/status") as resp:
                    if resp.status == 200:
                        api_status = await resp.json()
                        
                        # Mettre à jour la position actuelle si une piste est en cours
                        if api_status.get('track') and not api_status.get('stopped'):
                            current_position = api_status.get('track', {}).get('position', 0)
                            status['metadata']['position_ms'] = current_position
            except Exception as e:
                self.logger.warning(f"Impossible de récupérer la position actuelle: {e}")
        
        return status
    
    async def get_connection_info(self) -> Dict[str, Any]:
        """Retourne les informations de connexion"""
        return {
            "device_connected": self.device_connected,
            "ws_connected": self.ws_task is not None and not self.ws_task.done(),
            "api_url": self.api_url,
            "ws_url": self.ws_url
        }
    
    async def get_process_info(self) -> Dict[str, Any]:
        """Retourne les informations sur le processus"""
        running = self._is_process_running()
        return {
            "running": running,
            "pid": self.process.pid if running else None
        }