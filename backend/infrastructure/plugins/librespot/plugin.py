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
            # Démarrer le processus si nécessaire
            if not self._is_process_running():
                self.process = subprocess.Popen([self.executable_path])
                await asyncio.sleep(1)  # Attendre que le processus démarre
            
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
                            event = json.loads(msg.data)
                            await self._handle_websocket_event(event)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
                if self.is_active:
                    await asyncio.sleep(3)  # Attendre avant de reconnecter
    
    async def _handle_websocket_event(self, event: Dict[str, Any]) -> None:
        """Traite un événement WebSocket"""
        event_type = event.get('type')
        data = event.get('data', {})
        
        # Utiliser INFO au lieu de DEBUG pour voir ce qui se passe
        self.logger.info(f"Événement WebSocket reçu: {event_type} - {data}")
        
        # Capture TOUS les événements pour voir ce qui arrive
        if event_type:
            self.logger.info(f"Type d'événement: {event_type}")
        
        # Mapping des événements
        if event_type == 'active':
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "active",
                "connected": True,
                "is_playing": False
            })
        
        elif event_type == 'inactive':
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "inactive",
                "connected": False,
                "is_playing": False
            })
        
        elif event_type == 'playing':
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "playing",
                "connected": True,
                "is_playing": True
            })
        
        elif event_type == 'paused':
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "paused",
                "connected": True,
                "is_playing": False
            })
        
        elif event_type == 'stopped':
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "stopped",
                "connected": True,
                "is_playing": False
            })
        
        elif event_type == 'metadata':
            # Log des métadonnées reçues
            self.logger.info(f"Métadonnées reçues: {data}")
            
            # Publier les métadonnées
            await self.event_bus.publish("audio_metadata_updated", {
                "source": self.name,
                "metadata": {
                    "title": data.get('name'),
                    "artist": ", ".join(data.get('artist_names', [])),
                    "album": data.get('album_name'),
                    "album_art_url": data.get('album_cover_url'),
                    "duration_ms": data.get('duration'),
                    "position_ms": data.get('position', 0),
                    "is_playing": True
                }
            })
            
            # IMPORTANT: Publier aussi un statut connected
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "connected",
                "connected": True,
                "is_playing": True
            })
        
        elif event_type == 'will_play':
            # Log de l'événement will_play
            self.logger.info(f"will_play reçu: {data}")
            
            await self.event_bus.publish("audio_status_updated", {
                "source": self.name,
                "status": "connected",
                "connected": True,
                "is_playing": False
            })
        
        elif event_type == 'seek':
            await self.event_bus.publish("audio_seek", {
                "source": self.name,
                "position_ms": data.get('position'),
                "duration_ms": data.get('duration')
            })
        
        else:
            # Log des événements non gérés
            self.logger.info(f"Événement non géré: {event_type} - {data}")
    
    async def _websocket_loop(self) -> None:
        """Boucle WebSocket pour recevoir les événements"""
        while self.is_active:
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("WebSocket connecté à go-librespot")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.logger.info(f"Message WebSocket brut reçu: {msg.data}")  # Ajout
                            event = json.loads(msg.data)
                            await self._handle_websocket_event(event)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error(f"Erreur WebSocket: {msg}")  # Ajout
                            break
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
                if self.is_active:
                    await asyncio.sleep(3)
    
    
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
        """Retourne le statut basique"""
        return {
            "is_active": self.is_active,
            "plugin_state": self.current_state
        }