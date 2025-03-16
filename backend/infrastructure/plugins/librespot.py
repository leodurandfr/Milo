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
        self.executable_path = os.path.expanduser(config.get("executable_path", "~/sonoak/go-librespot/go-librespot"))
        self.api_url = None  # Sera défini après la lecture de la configuration
        self.process = None
        self.session = None
        self.metadata_polling_task = None
        self.polling_interval = config.get("polling_interval", 1.0)  # En secondes
    
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
                return True
            
            # Obtenir l'adresse et le port de l'API
            api_address = server_config.get('address', 'localhost')
            api_port = server_config.get('port', 3678)
            
            # Corriger l'adresse 0.0.0.0 pour les connexions locales
            if api_address == "0.0.0.0":
                api_address = "localhost"
                
            # Construire l'URL de l'API
            self.api_url = f"http://{localhost:}:{api_port}"
            self.logger.info(f"URL de l'API go-librespot configurée: {self.api_url}")
            
            # Vérifier d'autres paramètres importants
            device_name = librespot_config.get('device_name', 'go-librespot')
            self.logger.info(f"Nom de l'appareil go-librespot: {device_name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture de la configuration go-librespot: {str(e)}")
            
            # Utiliser une URL par défaut en cas d'erreur
            self.api_url = "http://localhost:3678"
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
            except Exception:
                self.logger.info("go-librespot n'est pas en cours d'exécution, démarrage...")
            
            if not running:
                # Démarrer go-librespot
                self.logger.info(f"Démarrage de go-librespot: {self.executable_path}")
                self.process = subprocess.Popen(
                    [self.executable_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Attendre que le processus démarre (mais ne pas attendre l'API)
                await asyncio.sleep(5)
                
                # Ne pas vérifier si l'API est prête, assumons qu'elle le sera bientôt
                self.logger.info("Process go-librespot démarré, continuons sans attendre l'API")
            
            # Activer le plugin même si l'API n'est pas encore prête
            self.is_active = True
            await self.publish_status("ready")
            
            # Démarrer la surveillance des métadonnées (qui gérera les erreurs)
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
                status = await self._fetch_status()
                is_playing = status.get("player", {}).get("is_playing", False)
                
                await self.publish_status("playing" if is_playing else "paused")
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
                    
            else:
                return {"success": False, "error": f"Commande non supportée: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _fetch_status(self) -> Dict[str, Any]:
        """
        Récupère le statut complet de go-librespot.
        
        Returns:
            Dict[str, Any]: Statut de go-librespot
        """
        if not self.api_url:
            raise Exception("URL de l'API go-librespot non configurée")
            
        async with self.session.get(f"{self.api_url}/status") as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 204:
                # Pour les réponses 204 (No Content), retourner un état minimal valide
                # C'est normal quand aucun appareil n'est connecté
                self.logger.debug("API go-librespot : aucun contenu musical disponible (204)")
                return {"player": {"is_playing": False, "current_track": None}}
            else:
                error_text = await response.text()
                raise Exception(f"Erreur API ({response.status}): {error_text}")
    
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
            
            player = status.get("player", {})
            current_track = player.get("current_track", {})
            
            # Si pas de piste en cours, renvoyer des métadonnées vides
            if not current_track:
                return {}
                
            metadata = {
                "title": current_track.get("name", "Inconnu"),
                "artist": self._format_artists(current_track.get("artists", [])),
                "album": current_track.get("album", {}).get("name", "Inconnu"),
                "album_art_url": self._get_album_art_url(current_track.get("album", {})),
                "duration_ms": current_track.get("duration_ms", 0),
                "position_ms": player.get("position_ms", 0),
                "is_playing": player.get("is_playing", False)
            }
            
            return metadata
            
        except Exception as e:
            # Réduire le niveau de log pour les erreurs fréquentes
            if "Erreur API (204)" in str(e):
                self.logger.debug(f"Aucun contenu disponible lors de la récupération des métadonnées: {str(e)}")
            else:
                self.logger.error(f"Erreur lors de la récupération des métadonnées: {str(e)}")
            return {}
    
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
        """Boucle de surveillance des métadonnées"""
        last_metadata = {}
        
        try:
            while True:
                if not self.is_active:
                    await asyncio.sleep(self.polling_interval)
                    continue
                    
                try:
                    current_metadata = await self._fetch_metadata()
                    
                    # Vérifier si les métadonnées ont changé
                    if current_metadata != last_metadata:
                        await self.publish_metadata(current_metadata)
                        last_metadata = current_metadata
                        
                        # Si la lecture est en cours, publier également l'état "playing"
                        if current_metadata.get("is_playing", False):
                            await self.publish_status("playing")
                        else:
                            await self.publish_status("paused")
                            
                except Exception as e:
                    # Réduire le niveau de log pour les erreurs fréquentes/normales
                    if "Erreur API (204)" in str(e):
                        self.logger.debug(f"Polling métadonnées: aucun contenu musical disponible")
                    else:
                        self.logger.error(f"Erreur dans la boucle de surveillance des métadonnées: {str(e)}")
                
                await asyncio.sleep(self.polling_interval)
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de surveillance des métadonnées arrêtée")
            
        except Exception as e:
            self.logger.error(f"Erreur inattendue dans la boucle de surveillance des métadonnées: {str(e)}")