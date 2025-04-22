"""
Plugin pour la source audio librespot (Spotify) - Version sans polling.
"""
import asyncio
import logging
import os
import yaml
from typing import Dict, Any

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.librespot.api_client import LibrespotApiClient
from backend.infrastructure.plugins.librespot.websocket_client import LibrespotWebSocketClient
from backend.infrastructure.plugins.librespot.metadata_processor import MetadataProcessor
from backend.infrastructure.plugins.librespot.process_manager import ProcessManager
from backend.infrastructure.plugins.librespot.event_handler import EventHandler

class LibrespotPlugin(BaseAudioPlugin):
    """Plugin pour intégrer go-librespot comme source audio."""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "librespot")
        self.config = config
        self.librespot_config_path = os.path.expanduser(config.get("config_path", "~/.config/go-librespot/config.yml"))
        self.executable_path = os.path.expanduser(config.get("executable_path", "~/oakOS/go-librespot/go-librespot"))
        
        # URLs et composants (seront initialisés plus tard)
        self.api_url = None
        self.ws_url = None
        self.process_manager = None
        self.api_client = None
        self.metadata_processor = None
        self.event_handler = None
        self.ws_client = None
        self.device_connected = False
    
    async def initialize(self) -> bool:
        """Initialise le plugin."""
        self.logger.info("Initialisation du plugin librespot")
        try:
            # Configuration URL
            if not await self._read_librespot_config():
                self.api_url = "http://localhost:3678"
                self.ws_url = "ws://localhost:3678/events"
            
            # Initialiser les composants
            self.process_manager = ProcessManager(self.executable_path)
            self.api_client = LibrespotApiClient(self.api_url, self.librespot_config_path)
            await self.api_client.initialize()
            
            self.metadata_processor = MetadataProcessor(self.event_bus, self.name)
            self.event_handler = EventHandler(
                self.metadata_processor, 
                self._update_connection_status,
                self.api_client
            )
            self.ws_client = LibrespotWebSocketClient(
                self.ws_url,
                self.event_handler.handle_event
            )
            await self.ws_client.initialize(self.api_client.session)
            
            await self.transition_to_state(self.STATE_INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {str(e)}")
            return False
    
    async def _read_librespot_config(self) -> bool:
        """Lit la configuration go-librespot."""
        try:
            config_path = os.path.expanduser(self.librespot_config_path)
            
            if not os.path.exists(config_path):
                return False
            
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            server = config.get('server', {})
            if not server.get('enabled', False):
                return False
                
            address = server.get('address', 'localhost')
            if address == "0.0.0.0":
                address = "localhost"
                
            port = server.get('port', 3678)
            
            self.api_url = f"http://{address}:{port}"
            self.ws_url = f"ws://{address}:{port}/events"
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur config: {str(e)}")
            return False
    
    def _update_connection_status(self, is_connected: bool) -> None:
        """Met à jour l'état de connexion."""
        if is_connected != self.device_connected:
            self.logger.info(f"État de connexion: {is_connected}")
            self.device_connected = is_connected
            
            # Mise à jour de l'état
            state_data = {
                "connected": is_connected,
                "deviceConnected": is_connected
            }
            asyncio.create_task(
                self.transition_to_state(
                    self.STATE_CONNECTED if is_connected else self.STATE_READY, 
                    state_data
                )
            )
    
    async def start(self) -> bool:
        """Démarre la source audio."""
        try:
            # Démarrer le processus si nécessaire
            if not self.process_manager.is_running():
                if not await self.process_manager.start_process():
                    await self.transition_to_state(self.STATE_ERROR)
                    return False
                await asyncio.sleep(1.0)
            
            # Démarrer uniquement la connexion WebSocket
            await self.ws_client.start()
            
            # Requête initiale pour vérifier l'état courant
            await self.event_handler.check_initial_state()
            
            self.is_active = True
            await self.transition_to_state(self.STATE_READY)
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage: {str(e)}")
            await self.transition_to_state(self.STATE_ERROR)
            return False
    
    async def stop(self) -> bool:
        """Arrête la source audio."""
        try:
            self.is_active = False
            
            # Arrêter la connexion WebSocket
            await self.ws_client.stop()
            
            # Essayer de mettre en pause
            try:
                await self.api_client.send_command("pause")
            except Exception:
                pass
            
            # Arrêter le processus
            await self.process_manager.stop_process()
            
            await self.transition_to_state(self.STATE_INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt: {str(e)}")
            await self.transition_to_state(self.STATE_INACTIVE)
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel."""
        if not self.is_active:
            return {
                "is_active": False,
                "plugin_state": self.current_state
            }
            
        try:
            status = await self.api_client.fetch_status()
            metadata = await self.metadata_processor.extract_from_status(status)
            
            return {
                "is_active": self.is_active,
                "is_playing": status.get("player", {}).get("is_playing", False),
                "metadata": metadata,
                "device_connected": self.device_connected,
                "plugin_state": self.current_state
            }
        except Exception as e:
            return {
                "is_active": self.is_active,
                "error": str(e),
                "plugin_state": self.current_state
            }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite une commande."""
        if not self.is_active and command != "get_status":
            return {"success": False, "error": "Plugin inactif"}
            
        try:
            # Commande spéciale pour le statut
            if command == "get_status":
                return await self.get_status()
                
            # Commandes de contrôle
            if command in ["play", "resume", "pause", "playpause", "prev", "previous", "next"]:
                # Mapping pour l'API
                api_cmd = command
                if command == "previous":
                    api_cmd = "prev"
                
                await self.api_client.send_command(api_cmd, data)
                
                # Vérifier le nouvel état
                status = await self.api_client.fetch_status()
                is_playing = status.get("player", {}).get("is_playing", False)
                
                # Publier le statut
                new_status = "playing" if is_playing else "paused"
                await self.metadata_processor.publish_status(new_status, {
                    "is_playing": is_playing,
                    "connected": True, 
                    "deviceConnected": True
                })
                
                # Mettre à jour les métadonnées
                metadata = await self.metadata_processor.extract_from_status(status)
                if metadata:
                    await self.metadata_processor.publish_metadata(metadata)
                
                return {"success": True, "status": new_status}
                
            # Commande seek
            elif command == "seek":
                position_ms = data.get("position_ms")
                if position_ms is not None:
                    await self.api_client.send_command("seek", {"position": position_ms})
                    
                    await self.event_bus.publish("audio_seek", {
                        "position_ms": position_ms,
                        "source": self.name
                    })
                    
                    return {"success": True}
                else:
                    return {"success": False, "error": "Position manquante"}
                    
            # Commande refresh_metadata
            elif command == "refresh_metadata":
                status = await self.api_client.fetch_status()
                metadata = await self.metadata_processor.extract_from_status(status)
                
                if metadata:
                    await self.metadata_processor.publish_metadata(metadata)
                    await self.metadata_processor.publish_status(
                        "playing" if metadata.get("is_playing", False) else "paused",
                        metadata
                    )
                
                return {"success": True}
                
            # Commande non supportée
            else:
                return {"success": False, "error": f"Commande non supportée: {command}"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}