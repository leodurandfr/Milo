"""
Plugin pour la source audio librespot (Spotify).
C'est la classe principale qui intègre tous les composants.
"""
import asyncio
import logging
import os
import yaml
from typing import Dict, Any, Optional

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.librespot.api_client import LibrespotApiClient
from backend.infrastructure.plugins.librespot.websocket_client import LibrespotWebSocketClient
from backend.infrastructure.plugins.librespot.metadata_processor import MetadataProcessor
from backend.infrastructure.plugins.librespot.connection_monitor import ConnectionMonitor
from backend.infrastructure.plugins.librespot.process_manager import ProcessManager
from backend.infrastructure.plugins.librespot.event_handler import EventHandler

class LibrespotPlugin(BaseAudioPlugin):
    """
    Plugin pour intégrer go-librespot comme source audio.
    """
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Initialise le plugin librespot.
        
        Args:
            event_bus: Bus d'événements pour la communication
            config: Configuration du plugin
        """
        super().__init__(event_bus, "librespot")
        self.config = config
        self.librespot_config_path = os.path.expanduser(config.get("config_path", "~/.config/go-librespot/config.yml"))
        self.executable_path = os.path.expanduser(config.get("executable_path", "~/oakOS/go-librespot/go-librespot"))
        self.polling_interval = config.get("polling_interval", 1.0)  # En secondes
        
        # URLs (seront définies après la lecture de la configuration)
        self.api_url = None
        self.ws_url = None
        
        # Composants
        self.process_manager = None
        self.api_client = None
        self.metadata_processor = None
        self.event_handler = None
        self.ws_client = None
        self.connection_monitor = None
        
        # État interne
        self.device_connected = False
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin en vérifiant les prérequis, sans démarrer go-librespot.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        self.logger.info("Initialisation du plugin librespot")
        try:
            # Lire la configuration go-librespot pour obtenir les URLs
            if not await self._read_librespot_config():
                self.logger.error("Impossible de lire la configuration go-librespot")
                return False
            
            # Initialiser les composants dans le bon ordre
            self.process_manager = ProcessManager(self.executable_path)
            
            self.api_client = LibrespotApiClient(self.api_url, self.librespot_config_path)
            if not await self.api_client.initialize():
                self.logger.error("Échec de l'initialisation du client API")
                return False
            
            self.metadata_processor = MetadataProcessor(self.event_bus, self.name)
            
            self.event_handler = EventHandler(
                self.metadata_processor,
                self._update_connection_status
            )
            
            self.ws_client = LibrespotWebSocketClient(
                self.ws_url,
                self.event_handler.handle_event
            )
            if not await self.ws_client.initialize(self.api_client.session):
                self.logger.error("Échec de l'initialisation du client WebSocket")
                return False
            
            self.connection_monitor = ConnectionMonitor(
                self.api_client,
                self.metadata_processor,
                self.polling_interval
            )
            
            self.logger.info("Plugin librespot initialisé avec succès (prêt à démarrer)")
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du plugin librespot: {str(e)}")
            return False
    
    async def _read_librespot_config(self) -> bool:
        """
        Lit et valide le fichier de configuration go-librespot.
        
        Returns:
            bool: True si la configuration est valide, False sinon
        """
        try:
            config_path = os.path.expanduser(self.librespot_config_path)
            
            # Vérifier si le fichier de configuration existe
            if not os.path.exists(config_path):
                self.logger.warning(f"Le fichier de configuration go-librespot n'existe pas: {self.librespot_config_path}")
                self.logger.warning("Utilisation des paramètres par défaut")
                
                # Utiliser une URL par défaut
                self.api_url = "http://localhost:3678"
                self.ws_url = "ws://localhost:3678/events"
                return True
            
            # Lire le fichier YAML
            with open(config_path, 'r') as f:
                librespot_config = yaml.safe_load(f)
            
            # Vérifier que la section server existe et que l'API est activée
            server_config = librespot_config.get('server', {})
            if not server_config.get('enabled', False):
                self.logger.warning("L'API server n'est pas activée dans la configuration go-librespot")
                self.logger.warning("Certaines fonctionnalités peuvent ne pas fonctionner correctement")
                
                # Utiliser une URL par défaut
                self.api_url = "http://localhost:3678"
                self.ws_url = "ws://localhost:3678/events"
                return True
            
            # Obtenir l'adresse et le port de l'API
            api_address = server_config.get('address', 'localhost')
            api_port = server_config.get('port', 3678)
            
            # Corriger l'adresse 0.0.0.0 pour les connexions locales
            if api_address == "0.0.0.0":
                api_address = "localhost"
                
            # Construire l'URL de l'API
            self.api_url = f"http://{api_address}:{api_port}"
            self.ws_url = f"ws://{api_address}:{api_port}/events"
            self.logger.info(f"URL de l'API go-librespot configurée: {self.api_url}")
            self.logger.info(f"URL WebSocket go-librespot configurée: {self.ws_url}")
            
            # Vérifier d'autres paramètres importants
            device_name = librespot_config.get('device_name', 'go-librespot')
            self.logger.info(f"Nom de l'appareil go-librespot: {device_name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture de la configuration go-librespot: {str(e)}")
            
            # Utiliser une URL par défaut en cas d'erreur
            self.api_url = "http://localhost:3678"
            self.ws_url = "ws://localhost:3678/events"
            return True  # Retourner True même en cas d'erreur pour permettre l'initialisation
    
    def _update_connection_status(self, is_connected: bool) -> None:
        """
        Met à jour l'état de connexion interne.
        
        Args:
            is_connected: True si un appareil est connecté
        """
        if is_connected != self.device_connected:
            self.logger.info(f"État de connexion mis à jour: {is_connected}")
            self.device_connected = is_connected
    
    async def start(self) -> bool:
        """Démarre la lecture audio via librespot."""
        self.logger.info("Démarrage de la source audio librespot")
        try:
            # Vérifier si go-librespot est déjà en cours d'exécution
            running = self.process_manager.is_running()
            
            if not running:
                # Démarrer le processus go-librespot
                if not await self.process_manager.start_process():
                    self.logger.error("Échec du démarrage du processus go-librespot")
                    return False
                
                # OPTIMISATION 1: Réduire le temps d'attente de 3s à 1s 
                # et le mettre dans une variable configurable
                await asyncio.sleep(self.config.get("process_startup_delay", 1.0))
            
            # OPTIMISATION 2: Démarrer les connexions en parallèle
            await asyncio.gather(
                self.ws_client.start(),
                self.connection_monitor.start()
            )
            
            # Activer le plugin
            self.is_active = True
            
            # Publier l'état initial
            await self.metadata_processor.publish_status("started", {
                "is_playing": False,
                "connected": self.device_connected
            })
            
            self.logger.info("Source audio librespot démarrée avec succès")
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la source audio librespot: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête la lecture audio via librespot.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        self.logger.info("Arrêt de la source audio librespot")
        try:
            # Désactiver le plugin
            self.is_active = False
            
            # Arrêter les surveillances
            await self.connection_monitor.stop()
            await self.ws_client.stop()
            
            # Pause de la lecture (mais ne pas arrêter le service)
            try:
                await self.api_client.send_command("pause")
            except Exception as e:
                self.logger.warning(f"Impossible de mettre en pause lors de l'arrêt: {str(e)}")
            
            # Publier l'état d'arrêt
            await self.metadata_processor.publish_status("stopped")
            
            # Arrêter le processus go-librespot
            if not await self.process_manager.stop_process():
                self.logger.warning("Problème lors de l'arrêt du processus go-librespot")
            
            self.logger.info("Source audio librespot arrêtée avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt de la source audio librespot: {str(e)}")
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Récupère l'état actuel de la source audio.
        
        Returns:
            Dict[str, Any]: État actuel avec métadonnées
        """
        try:
            # Si le plugin n'est pas actif, renvoyer un statut simple
            if not self.is_active:
                return {
                    "is_active": False,
                    "is_playing": False,
                    "metadata": {}
                }
                
            # Récupérer le statut complet
            status = await self.api_client.fetch_status()
            player_status = status.get("player", {})
            
            # Extraire les métadonnées
            metadata = await self.metadata_processor.extract_from_status(status)
            
            result = {
                "is_active": self.is_active,
                "is_playing": player_status.get("is_playing", False),
                "metadata": metadata,
                "device_connected": self.device_connected
            }
            
            # Ajouter d'autres informations si disponibles
            if "position_ms" in player_status:
                result["position"] = player_status["position_ms"]
                
            if "duration_ms" in player_status:
                result["duration"] = player_status["duration_ms"]
                
            return result
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut librespot: {str(e)}")
            return {
                "is_active": self.is_active,
                "is_playing": False,
                "error": str(e),
                "metadata": {},
                "device_connected": self.device_connected
            }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande spécifique à cette source.
        
        Args:
            command: Commande à exécuter (play, resume, pause, next, prev, etc.)
            data: Données supplémentaires pour la commande
                
        Returns:
            Dict[str, Any]: Résultat de la commande
        """
        self.logger.info(f"Traitement de la commande librespot: {command}")
        try:
            # Mapper les commandes frontend aux commandes API go-librespot
            api_command_mapping = {
                "play": "play",         # Démarrer un nouveau contenu
                "resume": "resume",     # Reprendre après pause
                "pause": "pause",       # Mettre en pause
                "playpause": "playpause", # Toggle play/pause
                "prev": "prev",         # Piste précédente
                "previous": "prev",     # Alias pour prev
                "next": "next",         # Piste suivante  
                "seek": "seek"          # Seek
            }
            
            # Obtenir la commande API correspondante
            api_command = api_command_mapping.get(command, command)
            
            if api_command in ["play", "resume", "pause", "playpause", "prev", "next"]:
                # Commandes de base supportées directement
                self.logger.info(f"Exécution de la commande API: {api_command}")
                await self.api_client.send_command(api_command, data)
                
                # Obtenir immédiatement le nouvel état après la commande
                status = await self.api_client.fetch_status()
                
                # Vérifier si la piste est en cours de lecture selon l'API
                is_playing = not status.get("paused", True)  # Par défaut considérer comme pausé
                
                # Mettre à jour le statut avec les infos correctes
                new_status = "playing" if is_playing else "paused"
                await self.metadata_processor.publish_status(new_status, {
                    "is_playing": is_playing,
                    "connected": True, 
                    "deviceConnected": True
                })
                
                # Actualiser les métadonnées complètes
                metadata = await self.metadata_processor.extract_from_status(status)
                if metadata:
                    await self.metadata_processor.publish_metadata(metadata)
                
                return {
                    "success": True, 
                    "status": new_status,
                    "is_playing": is_playing
                }
                    
            elif api_command == "seek":
                # Position en millisecondes
                position_ms = data.get("position_ms")
                if position_ms is not None:
                    # Envoyer la commande de seek
                    await self.api_client.send_command("seek", {"position": position_ms})
                    
                    # Publier un événement de seek
                    seek_data = {
                        "position_ms": position_ms,
                        "seek_timestamp": int(time.time() * 1000),
                        "source": self.name
                    }
                    await self.event_bus.publish("audio_seek", seek_data)
                    
                    return {"success": True, "position": position_ms}
                else:
                    return {"success": False, "error": "Position manquante"}
                        
            elif command == "volume":
                # Volume en pourcentage (0-100)
                volume = data.get("volume")
                if volume is not None:
                    # Note: go-librespot ne gère pas directement le volume, celui-ci est géré
                    # par le service de volume centralisé
                    return {"success": True, "note": "Le volume est géré par le service de volume centralisé"}
                else:
                    return {"success": False, "error": "Volume manquant"}
            
            # Ajout des autres commandes...
            elif command == "refresh_metadata":
                # Forcer une actualisation des métadonnées
                try:
                    # Vérifier la connexion via WebSocket
                    if not self.ws_client.is_connected:
                        # Redémarrer la connexion WebSocket
                        await self.ws_client.stop()
                        await self.ws_client.start()
                    
                    # Récupérer et publier les métadonnées actuelles
                    status = await self.api_client.fetch_status()
                    metadata = await self.metadata_processor.extract_from_status(status)
                    
                    if metadata:
                        await self.metadata_processor.publish_metadata(metadata)
                        status = "playing" if metadata.get("is_playing", False) else "paused"
                        await self.metadata_processor.publish_status(status, metadata)
                        return {"success": True, "metadata": metadata}
                    else:
                        # Aucune métadonnée trouvée
                        return {"success": True, "status": status, "message": "Aucune métadonnée disponible"}
                except Exception as e:
                    return {"success": False, "error": f"Erreur lors de l'actualisation des métadonnées: {str(e)}"}
            
            # Si on arrive ici, la commande n'est pas supportée
            else:
                return {"success": False, "error": f"Commande non supportée: {command}"}
                    
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def get_process_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur le processus go-librespot.
        
        Returns:
            Dict[str, Any]: Informations sur le processus
        """
        if not self.process_manager:
            return {"running": False}
            
        return self.process_manager.get_process_info()
    
    async def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur l'état de connexion.
        
        Returns:
            Dict[str, Any]: Informations sur la connexion
        """
        return {
            "device_connected": self.device_connected,
            "ws_connected": self.ws_client.is_connected if self.ws_client else False,
            "api_url": self.api_url,
            "ws_url": self.ws_url
        }