"""
Plugin pour la source audio librespot (Spotify).
"""
import aiohttp
import asyncio
import logging
import json
import subprocess
import os
import yaml
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin


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
        self.api_url = None  # Sera défini après la lecture de la configuration
        self.ws_url = None  # URL pour la connexion WebSocket
        self.process = None
        self.session = None
        self.metadata_polling_task = None
        self.polling_interval = config.get("polling_interval", 1.0)  # En secondes
        self.ws_task = None
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin en vérifiant juste les prérequis, sans démarrer go-librespot.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        self.logger.info("Initialisation du plugin librespot")
        try:
            # Vérifier si l'exécutable existe
            if not os.path.isfile(self.executable_path):
                self.logger.error(f"L'exécutable go-librespot n'existe pas: {self.executable_path}")
                return False
                
            # Lire la configuration go-librespot
            if not await self._read_librespot_config():
                self.logger.error("Impossible de lire la configuration go-librespot")
                return False
            
            # Créer la session HTTP qui sera utilisée plus tard
            self.session = aiohttp.ClientSession()
            
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
            config_path = Path(self.librespot_config_path)
            
            # Vérifier si le fichier de configuration existe
            if not config_path.exists():
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
    
    async def _is_librespot_running(self) -> bool:
        """
        Vérifie si go-librespot est déjà en cours d'exécution.
        
        Returns:
            bool: True si go-librespot est en cours d'exécution
        """
        try:
            await self._fetch_status()
            return True
        except Exception:
            return False
    
    async def _start_librespot_process(self) -> bool:
        """
        Démarre le processus go-librespot en arrière-plan.
        
        Returns:
            bool: True si le processus a démarré avec succès, False sinon
        """
        try:
            # Si un processus est déjà en cours, l'arrêter d'abord
            if self.process and self.process.poll() is None:
                self.logger.info("Un processus go-librespot est déjà en cours, tentative d'arrêt")
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.process = None
            
            # Démarrer le processus en arrière-plan
            self.logger.info(f"Démarrage de go-librespot: {self.executable_path}")
            process = subprocess.Popen(
                [self.executable_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Attendre que le service soit prêt
            for i in range(10):  # Attendre jusqu'à 10 secondes
                try:
                    await asyncio.sleep(1)
                    self.logger.debug(f"Tentative de connexion à go-librespot ({i+1}/10)...")
                    
                    # Vérifier si le processus est toujours en cours
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        self.logger.error(f"Le processus go-librespot s'est terminé prématurément avec le code: {process.returncode}")
                        self.logger.error(f"Sortie standard: {stdout.decode('utf-8', errors='replace')}")
                        self.logger.error(f"Erreur standard: {stderr.decode('utf-8', errors='replace')}")
                        return False
                    
                    # Tenter de se connecter à l'API
                    status = await self._fetch_status()
                    self.logger.info("Service go-librespot démarré avec succès")
                    self.process = process
                    return True
                except Exception as e:
                    if i == 9:  # Dernière tentative
                        self.logger.error(f"Échec de la connexion à go-librespot après 10 tentatives: {str(e)}")
                    continue
                    
            self.logger.error("Impossible de démarrer le service go-librespot dans le délai imparti")
            
            # Vérifier si le processus est toujours en cours
            if process.poll() is None:
                # Le processus est toujours en cours, mais ne répond pas
                self.logger.warning("Le processus go-librespot est en cours d'exécution, mais l'API ne répond pas")
                self.process = process  # Conserver la référence au processus
                return True  # Considérer comme un succès même si l'API ne répond pas encore
            else:
                # Le processus s'est terminé
                stdout, stderr = process.communicate()
                self.logger.error(f"Le processus go-librespot s'est terminé avec le code: {process.returncode}")
                self.logger.error(f"Sortie standard: {stdout.decode('utf-8', errors='replace')}")
                self.logger.error(f"Erreur standard: {stderr.decode('utf-8', errors='replace')}")
                return False
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du processus go-librespot: {str(e)}")
            return False
    
    async def start(self) -> bool:
        """
        Démarre la lecture audio via librespot.
        
        Returns:
            bool: True si la lecture a démarré avec succès, False sinon
        """
        self.logger.info("Démarrage de la source audio librespot")
        try:
            # Vérifier si go-librespot est déjà en cours d'exécution
            running = False
            try:
                await self._fetch_status()
                running = True
                self.logger.info("go-librespot est déjà en cours d'exécution")
            except Exception as e:
                self.logger.info(f"go-librespot n'est pas en cours d'exécution, démarrage... Erreur: {e}")
            
            if not running:
                # Démarrer go-librespot
                self.logger.info(f"Démarrage de go-librespot: {self.executable_path}")
                self.process = subprocess.Popen(
                    [self.executable_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Attendre que le processus démarre
                await asyncio.sleep(5)
                
                # Vérifier si le processus est toujours en cours
                if self.process.poll() is not None:
                    stdout, stderr = self.process.communicate()
                    self.logger.error(f"go-librespot s'est terminé prématurément avec code: {self.process.returncode}")
                    self.logger.error(f"Sortie: {stdout.decode('utf-8', errors='replace')}")
                    self.logger.error(f"Erreurs: {stderr.decode('utf-8', errors='replace')}")
                    return False
                    
                self.logger.info("Process go-librespot démarré")
            
            # Activer le plugin
            self.is_active = True
            
            # Démarrer la connexion WebSocket
            await self._start_websocket_connection()
            
            # Démarrer la surveillance des métadonnées (en fallback)
            self._start_metadata_polling()
            
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
            # Arrêter la connexion WebSocket
            await self._stop_websocket_connection()
            
            # Arrêter la surveillance des métadonnées
            self._stop_metadata_polling()
            
            # Pause de la lecture (mais ne pas arrêter le service)
            try:
                await self._send_command("pause")
            except Exception as e:
                self.logger.warning(f"Impossible de mettre en pause lors de l'arrêt: {str(e)}")
            
            self.is_active = False
            await self.publish_status("stopped")
            
            # Arrêter le processus go-librespot
            if self.process and self.process.poll() is None:
                self.logger.info("Arrêt du processus go-librespot")
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.logger.warning("Le processus go-librespot ne s'est pas arrêté proprement, utilisation de kill")
                    self.process.kill()
                self.process = None
            
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
                    "metadata": self.metadata
                }
                
            # Tenter de récupérer le statut complet
            status = await self._fetch_status()
            player_status = status.get("player", {})
            
            result = {
                "is_active": self.is_active,
                "is_playing": player_status.get("is_playing", False),
                "metadata": self.metadata
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
                "metadata": self.metadata
            }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande spécifique à cette source.
        
        Args:
            command: Commande à exécuter (play, pause, next, prev, etc.)
            data: Données supplémentaires pour la commande
            
        Returns:
            Dict[str, Any]: Résultat de la commande
        """
        self.logger.info(f"Traitement de la commande librespot: {command}")
        try:
            if command in ["play", "pause", "next", "previous"]:
                # Commandes de base supportées directement
                result = await self._send_command(command)
                
                # Mettre à jour le statut
                try:
                    status = await self._fetch_status()
                    is_playing = status.get("player", {}).get("is_playing", False)
                    
                    await self.publish_status("playing" if is_playing else "paused", {
                        "is_playing": is_playing,
                        "connected": True,
                        "deviceConnected": True
                    })
                except Exception as e:
                    self.logger.error(f"Erreur lors de la mise à jour du statut après {command}: {e}")
                
                return {"success": True, "status": "playing" if is_playing else "paused"}
                
            elif command == "seek":
                # Position en millisecondes
                position_ms = data.get("position_ms")
                if position_ms is not None:
                    result = await self._send_command("seek", {"position_ms": position_ms})
                    return {"success": True}
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
            
            elif command == "refresh_metadata":
                # Forcer une actualisation des métadonnées
                try:
                    # Vérifier la connexion via WebSocket
                    if self.ws_task and self.ws_task.done():
                        # La tâche WebSocket est terminée, la redémarrer
                        await self._start_websocket_connection()
                    
                    # Récupérer et publier les métadonnées actuelles
                    metadata = await self._fetch_metadata()
                    if metadata:
                        await self.publish_metadata(metadata)
                        status = "playing" if metadata.get("is_playing", False) else "paused"
                        await self.publish_status(status, metadata)
                        return {"success": True, "metadata": metadata}
                    else:
                        # Aucune métadonnée trouvée, vérifier si un appareil est connecté
                        try:
                            status = await self._fetch_status()
                            return {"success": True, "status": status, "message": "Aucune métadonnée disponible"}
                        except Exception as e:
                            return {"success": False, "error": f"Erreur lors de la récupération du statut: {str(e)}"}
                except Exception as e:
                    return {"success": False, "error": f"Erreur lors de l'actualisation des métadonnées: {str(e)}"}
                    
            else:
                return {"success": False, "error": f"Commande non supportée: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _fetch_status(self) -> Dict[str, Any]:
        """
        Récupère le statut complet de go-librespot avec meilleure gestion des erreurs.
        
        Returns:
            Dict[str, Any]: Statut de go-librespot
        """
        if not self.api_url:
            self.logger.error("URL de l'API go-librespot non configurée")
            raise Exception("URL de l'API go-librespot non configurée")
            
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
                
            self.logger.debug(f"Récupération du statut depuis {self.api_url}/status")
            async with self.session.get(f"{self.api_url}/status") as response:
                if response.status == 200:
                    status_data = await response.json()
                    self.logger.debug(f"Statut go-librespot reçu: {status_data}")
                    return status_data
                elif response.status == 204:
                    # Pour les réponses 204 (No Content), retourner un état minimal valide
                    # C'est normal quand aucun appareil n'est connecté
                    self.logger.debug("API go-librespot : aucun contenu musical disponible (204)")
                    return {"player": {"is_playing": False, "current_track": None}}
                else:
                    error_text = await response.text()
                    self.logger.error(f"Erreur API go-librespot ({response.status}): {error_text}")
                    raise Exception(f"Erreur API ({response.status}): {error_text}")
        except aiohttp.ClientError as e:
            self.logger.error(f"Erreur de connexion à go-librespot: {e}")
            raise Exception(f"Erreur de connexion à go-librespot: {e}")
        except Exception as e:
            self.logger.error(f"Erreur imprévue lors de la récupération du statut: {e}")
            raise
    
    async def _fetch_metadata(self) -> Dict[str, Any]:
        """
        Récupère les métadonnées actuelles de la piste en cours.
        
        Returns:
            Dict[str, Any]: Métadonnées de la piste en cours
        """
        try:
            status = None
            try:
                status = await self._fetch_status()
            except Exception as e:
                # Si l'erreur est une réponse 204, ce n'est pas critique
                if "Erreur API (204)" in str(e):
                    self.logger.debug("API a répondu avec 204 - Aucune piste en cours")
                    return {}
                else:
                    # Pour les autres erreurs, les propager
                    raise
            
            # Vérifier si nous avons un username (si connecté à Spotify)
            username = status.get("username")
            
            player = status.get("player", {})
            current_track = player.get("current_track", {})
            
            # Si pas de piste en cours mais l'utilisateur est connecté
            if not current_track and username:
                # Retourner simplement l'état connecté, sans avertissement
                self.logger.debug(f"Appareil connecté via username: {username} - En attente de lecture")
                return {
                    "connected": True,
                    "deviceConnected": True,
                    "username": username,
                    "device_name": status.get("device_name", "oakOS")
                }
                    
            # Si pas de piste en cours et pas connecté, renvoyer des métadonnées vides
            if not current_track:
                return {}
                    
            # Extraction des métadonnées si une piste est en cours
            metadata = {
                "title": current_track.get("name", "Inconnu"),
                "artist": self._format_artists(current_track.get("artists", [])),
                "album": current_track.get("album", {}).get("name", "Inconnu"),
                "album_art_url": self._get_album_art_url(current_track.get("album", {})),
                "duration_ms": current_track.get("duration_ms", 0),
                "position_ms": player.get("position_ms", 0),
                "is_playing": player.get("is_playing", False),
                # Simplification - un seul état "connected" au lieu de deux
                "connected": True,
                "username": username,
                "device_name": status.get("device_name", "oakOS"),
                "track_uri": current_track.get("uri")
            }
            
            # Log uniquement au niveau debug pour les métadonnées régulières
            self.logger.debug(f"Métadonnées extraites: {metadata}")
            
            return metadata
                
        except Exception as e:
            # Réduire le niveau de log pour les erreurs fréquentes
            if "Erreur API (204)" in str(e):
                self.logger.debug(f"Aucun contenu disponible lors de la récupération des métadonnées: {str(e)}")
            else:
                self.logger.error(f"Erreur lors de la récupération des métadonnées: {str(e)}")
            return {}
    
    async def is_device_connected(self) -> bool:
        """
        Vérifie si un appareil est connecté à go-librespot de manière plus fiable.
        
        Returns:
            bool: True si un appareil est connecté
        """
        try:
            status = await self._fetch_status()
            self.logger.debug(f"Vérification de connexion, statut: {status}")
            
            # Vérifier des conditions plus larges pour déterminer si on est connecté
            
            # 1. Vérifier si un username est présent (connexion Spotify)
            if status.get("username"):
                self.logger.info(f"Appareil connecté détecté via username: {status.get('username')}")
                return True
                
            # 2. Vérifier les informations du player
            player_status = status.get("player", {})
            
            # Si le player a un statut, on est probablement connecté
            if player_status:
                # Vérifier des indices de connexion dans le player
                if (player_status.get("current_track") or 
                    player_status.get("is_playing", False) or 
                    player_status.get("position_ms", 0) > 0 or
                    player_status.get("duration_ms", 0) > 0):
                    self.logger.info("Appareil connecté détecté via player status")
                    return True
                    
                # Même si pas de piste en cours, s'il y a des indications de device
                if "device" in status or "device_id" in status or "device_name" in status:
                    self.logger.info("Appareil connecté détecté via device info")
                    return True
            
            # 3. Vérifier les données de connexion directes
            if status.get("connected") == True or status.get("deviceConnected") == True:
                self.logger.info("Appareil connecté détecté via flags connecté")
                return True
                
            # Si aucune condition n'est remplie, on n'est probablement pas connecté
            self.logger.debug("Aucun appareil connecté détecté")
            return False
            
        except Exception as e:
            # En cas d'erreur, considérer que l'absence d'information n'est pas une déconnexion
            self.logger.debug(f"Erreur lors de la vérification de connexion: {e}")
            # Conserver l'état de connexion précédent si disponible
            return getattr(self, "_last_known_connection_state", False)


    async def _handle_ws_event(self, event):
        """
        Traite un événement WebSocket reçu de go-librespot.
        
        Args:
            event: Événement reçu
        """
        event_type = event.get('type')
        event_data = event.get('data', {})
        
        self.logger.info(f"Événement WebSocket go-librespot reçu: {event_type}")
        self.logger.debug(f"Données de l'événement: {event_data}")
        
        # État de connexion persistant
        connection_state = True
        
        # Mettre à jour l'attribut _last_known_connection_state
        setattr(self, "_last_known_connection_state", connection_state)
        
        if event_type == 'active':
            # Appareil devient actif - TOUJOURS considérer comme connecté
            await self.publish_status("active", {
                "connected": True,
                "deviceConnected": True
            })
            
        elif event_type == 'inactive':
            # Appareil devient inactif - vérifier s'il s'agit d'une vraie déconnexion
            # ou juste d'une pause entre les pistes
            is_still_connected = await self.is_device_connected()
            
            if not is_still_connected:
                # C'est une vraie déconnexion
                await self.publish_status("inactive", {
                    "connected": False,
                    "deviceConnected": False
                })
                # Mettre à jour l'état persistant
                setattr(self, "_last_known_connection_state", False)
            else:
                # C'est juste une pause, garder l'état connecté
                await self.publish_status("paused", {
                    "connected": True,
                    "deviceConnected": True,
                    "is_playing": False
                })
                
        elif event_type == 'metadata':
            # Nouvelles métadonnées de piste - TOUJOURS marquer comme connecté
            metadata = {
                "title": event_data.get("name"),
                "artist": ", ".join(event_data.get("artist_names", [])),
                "album": event_data.get("album_name"),
                "album_art_url": event_data.get("album_cover_url"),
                "duration_ms": event_data.get("duration"),
                "position_ms": event_data.get("position", 0),
                "is_playing": True,  # Supposer que c'est en lecture
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            }
            await self.publish_metadata(metadata)
            
        elif event_type in ['will_play', 'playing']:
            # Lecture en cours - TOUJOURS marquer comme connecté
            await self.publish_status("playing", {
                "is_playing": True,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            # Actualiser les métadonnées via l'API REST pour obtenir toutes les infos
            metadata = await self._fetch_metadata()
            if metadata:
                # S'assurer que connected et deviceConnected sont toujours vrais
                metadata["connected"] = True
                metadata["deviceConnected"] = True
                await self.publish_metadata(metadata)
                
        elif event_type == 'paused':
            # Lecture en pause - maintenir l'état connecté
            await self.publish_status("paused", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            
        elif event_type == 'seek':
            # Changement de position dans la piste
            if event_data:
                seek_data = {
                    "position_ms": event_data.get("position"),
                    "duration_ms": event_data.get("duration"),
                    "track_uri": event_data.get("uri"),
                    "connected": True,
                    "deviceConnected": True
                }
                # Actualiser les métadonnées pour la nouvelle position
                metadata = await self._fetch_metadata()
                if metadata:
                    metadata.update(seek_data)
                    await self.publish_metadata(metadata)
                
        elif event_type == 'stopped':
            # Lecture arrêtée - vérifier si toujours connecté
            is_still_connected = await self.is_device_connected()
            
            if is_still_connected:
                # Arrêté mais toujours connecté
                await self.publish_status("stopped", {
                    "is_playing": False,
                    "connected": True,
                    "deviceConnected": True
                })
            else:
                # Vraiment déconnecté
                await self.publish_status("disconnected", {
                    "is_playing": False,
                    "connected": False,
                    "deviceConnected": False
                })
                # Mettre à jour l'état persistant
                setattr(self, "_last_known_connection_state", False)
            
        else:
            # Autres événements non gérés spécifiquement
            self.logger.debug(f"Événement non traité spécifiquement: {event_type}")
    
    async def _handle_seek_event(self, event_data: Dict[str, Any]) -> None:
        """
        Gère les événements de position (seek) avec plus de précision.
        
        Args:
            event_data: Données de l'événement de seek
        """
        position = event_data.get("position")
        duration = event_data.get("duration")
        
        if position is not None:
            # Créer un dictionnaire avec les données de seek enrichies
            seek_data = {
                "position_ms": position,
                "duration_ms": duration,
                "seek_timestamp": int(time.time() * 1000),  # Timestamp en millisecondes
                "track_uri": event_data.get("uri"),
                "connected": True,
                "deviceConnected": True,
                "source": self.name  # Ajouter la source pour le filtrage côté frontend
            }
            
            # Publier un événement spécifique de seek via le bus d'événements
            await self.event_bus.publish("audio_seek", seek_data)
            
            # Actualiser aussi les métadonnées
            metadata = await self._fetch_metadata()
            if metadata:
                metadata.update(seek_data)
                await self.publish_metadata(metadata)
                
            self.logger.info(f"Événement de seek traité: position={position}ms, durée={duration}ms")
    
    
    async def _start_websocket_connection(self):
        """Démarre une connexion WebSocket vers go-librespot/events"""
        if self.ws_task is None or self.ws_task.done():
            self.ws_task = asyncio.create_task(self._websocket_loop())
            self.logger.info("Connexion WebSocket à go-librespot démarrée")
    
    async def _stop_websocket_connection(self):
        """Arrête la connexion WebSocket"""
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
            self.ws_task = None
            self.logger.info("Connexion WebSocket à go-librespot arrêtée")
    
    async def _websocket_loop(self):
        """Maintient une connexion WebSocket et traite les événements"""
        retry_delay = 1
        max_retry_delay = 30
        
        while True:
            try:
                self.logger.info(f"Tentative de connexion WebSocket à {self.ws_url}")
                
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("Connexion WebSocket établie avec go-librespot")
                    retry_delay = 1  # Réinitialiser le délai de reconnexion
                    
                    # Publier un état pour indiquer que nous sommes connectés à l'API WebSocket
                    await self.publish_status("ws_connected", {
                        "ws_connected": True,
                        "api_url": self.api_url
                    })
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self._handle_ws_event(data)
                            except json.JSONDecodeError:
                                self.logger.error(f"Message WebSocket non-JSON reçu: {msg.data}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error(f"Erreur WebSocket: {ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            self.logger.warning("Connexion WebSocket fermée")
                            break
                
                self.logger.warning("Connexion WebSocket fermée, reconnexion...")
                
            except aiohttp.ClientError as e:
                self.logger.error(f"Erreur de connexion WebSocket: {e}")
            except asyncio.CancelledError:
                self.logger.info("Tâche WebSocket annulée")
                return
            except Exception as e:
                self.logger.error(f"Erreur WebSocket inattendue: {e}")
            
            # Attendre avant de reconnecter (avec backoff exponentiel)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
    
    async def _handle_ws_event(self, event):
        """
        Traite un événement WebSocket reçu de go-librespot.
        
        Args:
            event: Événement reçu
        """
        event_type = event.get('type')
        event_data = event.get('data', {})
        
        self.logger.info(f"Événement WebSocket go-librespot reçu: {event_type}")
        self.logger.debug(f"Données de l'événement: {event_data}")
        
        if event_type == 'active':
            # Appareil devient actif
            await self.publish_status("active", {
                "connected": True,
                "deviceConnected": True
            })
            
        elif event_type == 'inactive':
            # Appareil devient inactif
            await self.publish_status("inactive", {
                "connected": False,
                "deviceConnected": False
            })
            
        elif event_type == 'metadata':
            # Nouvelles métadonnées de piste
            metadata = {
                "title": event_data.get("name"),
                "artist": ", ".join(event_data.get("artist_names", [])),
                "album": event_data.get("album_name"),
                "album_art_url": event_data.get("album_cover_url"),
                "duration_ms": event_data.get("duration"),
                "position_ms": event_data.get("position", 0),
                "is_playing": True,  # Supposer que c'est en lecture
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            }
            await self.publish_metadata(metadata)
            
        elif event_type in ['will_play', 'playing']:
            # Lecture en cours
            await self.publish_status("playing", {
                "is_playing": True,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            # Actualiser les métadonnées via l'API REST pour obtenir toutes les infos
            metadata = await self._fetch_metadata()
            if metadata:
                await self.publish_metadata(metadata)
                
        elif event_type == 'paused':
            # Lecture en pause
            await self.publish_status("paused", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            })
            
        elif event_type == 'seek':
            # Changement de position dans la piste - utiliser la méthode dédiée
            await self._handle_seek_event(event_data)
                
        elif event_type == 'stopped':
            # Lecture arrêtée
            await self.publish_status("stopped", {
                "is_playing": False,
                "connected": True,
                "deviceConnected": True
            })
            
        else:
            # Autres événements non gérés spécifiquement
            self.logger.debug(f"Événement non traité spécifiquement: {event_type}")
    
    def _format_artists(self, artists: List[Dict[str, Any]]) -> str:
        """
        Formate la liste des artistes en une chaîne.
        
        Args:
            artists: Liste des artistes
            
        Returns:
            str: Chaîne formatée des artistes
        """
        if not artists:
            return "Inconnu"
            
        # Extraire les noms des artistes
        names = [artist.get("name", "") for artist in artists if artist.get("name")]
        
        # Joindre les noms avec des virgules
        return ", ".join(names)
    
    def _get_album_art_url(self, album: Dict[str, Any]) -> Optional[str]:
        """
        Récupère l'URL de la pochette d'album.
        
        Args:
            album: Informations sur l'album
            
        Returns:
            Optional[str]: URL de la pochette d'album, ou None si non disponible
        """
        images = album.get("images", [])
        
        # Trier par taille (du plus grand au plus petit)
        sorted_images = sorted(
            images, 
            key=lambda img: img.get("width", 0) * img.get("height", 0), 
            reverse=True
        )
        
        # Prendre la plus grande image
        if sorted_images:
            return sorted_images[0].get("url")
            
        return None
    
    async def _send_command(self, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Envoie une commande à l'API go-librespot.
        
        Args:
            command: Commande à envoyer
            params: Paramètres de la commande
            
        Returns:
            Dict[str, Any]: Réponse de l'API
        """
        if not self.api_url:
            raise Exception("URL de l'API go-librespot non configurée")
            
        url = f"{self.api_url}/player/{command}"
        method = "POST"
        
        # Certaines commandes utilisent GET au lieu de POST
        if command in ["status"]:
            method = "GET"
        
        try:
            if method == "GET":
                async with self.session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error_text = await response.text()
                        raise Exception(f"Erreur API ({response.status}): {error_text}")
            else:
                data = json.dumps(params) if params else None
                headers = {"Content-Type": "application/json"} if data else None
                
                async with self.session.post(url, data=data, headers=headers) as response:
                    if response.status in [200, 204]:
                        if response.status == 204:
                            return {}
                        else:
                            return await response.json()
                    else:
                        error_text = await response.text()
                        raise Exception(f"Erreur API ({response.status}): {error_text}")
                        
        except Exception as e:
            self.logger.error(f"Erreur lors de l'envoi de la commande {command}: {str(e)}")
            raise
    
    def _start_metadata_polling(self) -> None:
        """Démarre la tâche de surveillance des métadonnées"""
        if self.metadata_polling_task is None or self.metadata_polling_task.done():
            self.metadata_polling_task = asyncio.create_task(self._metadata_polling_loop())
            self.logger.debug("Démarrage de la surveillance des métadonnées")
    
    def _stop_metadata_polling(self) -> None:
        """Arrête la tâche de surveillance des métadonnées"""
        if self.metadata_polling_task and not self.metadata_polling_task.done():
            self.metadata_polling_task.cancel()
            self.logger.debug("Arrêt de la surveillance des métadonnées")
            
    async def _metadata_polling_loop(self) -> None:
        """Boucle de surveillance des métadonnées simplifiée"""
        last_metadata = {}
        was_connected = False
        connection_check_counter = 0
        connection_lost_counter = 0
        max_lost_checks = 3  # Nombre de vérifications consécutives échouées avant de déclarer une déconnexion
        
        try:
            while True:
                if not self.is_active:
                    await asyncio.sleep(self.polling_interval)
                    continue
                    
                try:
                    # Vérifier la connexion à chaque itération
                    is_connected = await self.is_device_connected()
                    
                    # Si la connexion est perdue, incrémenter le compteur
                    if was_connected and not is_connected:
                        connection_lost_counter += 1
                        self.logger.debug(f"Possible perte de connexion détectée ({connection_lost_counter}/{max_lost_checks})")
                    else:
                        # Réinitialiser le compteur si la connexion est rétablie
                        connection_lost_counter = 0
                    
                    # Si plusieurs vérifications consécutives échouent, déclarer une déconnexion
                    if connection_lost_counter >= max_lost_checks:
                        self.logger.info(f"Déconnexion confirmée après {max_lost_checks} échecs consécutifs")
                        was_connected = False
                        connection_lost_counter = 0
                        
                        # Publier un événement de déconnexion explicite
                        await self.publish_status("disconnected")
                        
                        # Continuer pour éviter de traiter d'autres cas
                        await asyncio.sleep(self.polling_interval)
                        continue
                    
                    # Si l'état de connexion a changé, publier immédiatement
                    if is_connected != was_connected:
                        self.logger.info(f"État de connexion modifié: {was_connected} -> {is_connected}")
                        was_connected = is_connected
                        
                        # Publier l'événement de connexion ou déconnexion
                        await self.publish_status("connected" if is_connected else "disconnected")
                    
                    # Si connecté, obtenir les métadonnées
                    if is_connected:
                        current_metadata = await self._fetch_metadata()
                        
                        # S'assurer que current_metadata n'est pas vide
                        if current_metadata:
                            # Vérifier si les métadonnées ont changé
                            if current_metadata != last_metadata:
                                self.logger.debug("Métadonnées mises à jour, publication de l'événement")
                                await self.publish_metadata(current_metadata)
                                last_metadata = current_metadata
                                
                                # Si la lecture est en cours, publier également l'état "playing"
                                if current_metadata.get("is_playing", False):
                                    await self.publish_status("playing")
                                else:
                                    await self.publish_status("paused")
                    else:
                        # Si nous n'étions pas connectés, publier un état déconnecté périodiquement
                        if connection_check_counter % 10 == 0:  # Tous les ~10 intervalles
                            await self.publish_status("disconnected")
                    
                    connection_check_counter += 1
                            
                except Exception as e:
                    # Log de débogage plus détaillé
                    if "Erreur API (204)" in str(e):
                        self.logger.debug("Aucun contenu musical disponible (204)")
                    else:
                        self.logger.warning(f"Erreur dans la boucle de surveillance: {str(e)}")
                        
                    # Si nous avons eu une erreur et que nous étions connectés auparavant,
                    # incrémenter le compteur d'erreurs
                    if was_connected:
                        connection_lost_counter += 1
                        self.logger.debug(f"Erreur de communication lors de la vérification de connexion ({connection_lost_counter}/{max_lost_checks})")
                        
                        # Si trop d'erreurs consécutives, déclarer une déconnexion
                        if connection_lost_counter >= max_lost_checks:
                            self.logger.warning(f"Déconnexion présumée après {max_lost_checks} erreurs consécutives")
                            was_connected = False
                            connection_lost_counter = 0
                            
                            # Publier un événement de déconnexion explicite
                            await self.publish_status("disconnected")
                
                await asyncio.sleep(self.polling_interval)
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de surveillance des métadonnées arrêtée")
            
        except Exception as e:
            self.logger.error(f"Erreur inattendue dans la boucle de surveillance: {str(e)}") 
             
    async def _get_current_position(self) -> int:
        """Récupère la position actuelle de lecture"""
        try:
            status = await self._fetch_status()
            return status.get("player", {}).get("position_ms", 0)
        except Exception:
            return 0

    async def publish_status(self, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un événement de statut sur le bus d'événements avec format simplifié.
        
        Args:
            status: Statut à publier (playing, paused, stopped, etc.)
            details: Détails supplémentaires du statut
        """
        # Déterminer si l'état implique une connexion
        is_connected = status in ["playing", "paused", "connected", "active"]
        
        # Créer un objet de base avec les informations essentielles
        status_data = {
            "source": self.name,
            "status": status,
            "connected": is_connected,
            "is_playing": status == "playing"
        }
        
        # Ajouter les détails supplémentaires s'ils existent
        if details:
            # Éviter les doublons en ne surchargeant pas les champs déjà définis
            # sauf si c'est intentionnel (par exemple, forcer connected=False)
            for key, value in details.items():
                if key not in status_data or details.get("force_override", False):
                    status_data[key] = value
        
        # Nettoyer les champs de métadonnées trop volumineux pour les logs
        log_data = {k: v for k, v in status_data.items() 
                    if k not in ["album_art_url", "track_uri"]}
        
        self.logger.debug(f"Publication du statut {status}: {log_data}")
        
        # Publier l'événement
        await self.event_bus.publish("audio_status_updated", status_data)