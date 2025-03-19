"""
Client API pour communiquer avec go-librespot.
"""
import aiohttp
import logging
import json
import yaml
import os
import time
from typing import Dict, Any, Optional

class LibrespotApiClient:
    """Client API pour communiquer avec go-librespot"""
    
    def __init__(self, api_url: Optional[str] = None, config_path: Optional[str] = None):
        self.api_url = api_url
        self.config_path = config_path
        self.session = None
        self.logger = logging.getLogger("librespot.api")
        # Initialisation du cache pour les requêtes API
        self.status_cache = {"data": None, "timestamp": 0}
        self.cache_ttl_ms = 1500  # 500ms de TTL pour le cache
    
    async def initialize(self) -> bool:
        """Initialise le client API"""
        try:
            # Si l'URL API n'est pas définie et qu'un chemin de config est fourni, 
            # essayer de lire la configuration
            if not self.api_url and self.config_path:
                if not await self._read_librespot_config():
                    # Utiliser une URL par défaut en cas d'échec
                    self.api_url = "http://localhost:3678"
                    self.logger.warning(f"Utilisation de l'URL par défaut: {self.api_url}")
            
            # Si toujours pas d'URL API, utiliser la valeur par défaut
            if not self.api_url:
                self.api_url = "http://localhost:3678"
                self.logger.warning(f"Utilisation de l'URL par défaut: {self.api_url}")
            
            # Ne pas créer de nouvelle session si une existe déjà
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()
                
            self.logger.info(f"Client API initialisé avec URL: {self.api_url}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du client API: {e}")
            # Fermer la session en cas d'erreur
            await self.close()
            return False
    
    async def close(self) -> None:
        """Ferme la session HTTP de manière sécurisée"""
        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception as e:
                self.logger.error(f"Erreur lors de la fermeture de la session HTTP: {e}")
            finally:
                self.session = None
                self.logger.debug("Session HTTP fermée")
    
    async def _read_librespot_config(self) -> bool:
        """
        Lit et valide le fichier de configuration go-librespot.
        
        Returns:
            bool: True si la configuration est valide, False sinon
        """
        try:
            if not self.config_path:
                self.logger.warning("Aucun chemin de configuration fourni")
                return False
                
            config_path = os.path.expanduser(self.config_path)
            
            # Vérifier si le fichier de configuration existe
            if not os.path.exists(config_path):
                self.logger.warning(f"Le fichier de configuration go-librespot n'existe pas: {config_path}")
                return False
            
            # Lire le fichier YAML
            with open(config_path, 'r') as f:
                librespot_config = yaml.safe_load(f)
            
            # Vérifier que la section server existe et que l'API est activée
            server_config = librespot_config.get('server', {})
            if not server_config.get('enabled', False):
                self.logger.warning("L'API server n'est pas activée dans la configuration go-librespot")
                return False
                
            # Obtenir l'adresse et le port de l'API
            api_address = server_config.get('address', 'localhost')
            api_port = server_config.get('port', 3678)
            
            # Corriger l'adresse 0.0.0.0 pour les connexions locales
            if api_address == "0.0.0.0":
                api_address = "localhost"
                
            # Construire l'URL de l'API
            self.api_url = f"http://{api_address}:{api_port}"
            self.logger.info(f"URL de l'API go-librespot configurée: {self.api_url}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture de la configuration go-librespot: {str(e)}")
            return False
    
    async def fetch_status(self) -> Dict[str, Any]:
        """
        Récupère le statut complet de go-librespot avec mise en cache.
        
        Returns:
            Dict[str, Any]: Statut de go-librespot
        """
        if not self.api_url:
            self.logger.error("URL de l'API go-librespot non configurée")
            raise Exception("URL de l'API go-librespot non configurée")
        
        # Vérifier si le cache est valide
        now = time.time() * 1000
        if (self.status_cache["data"] and 
            now - self.status_cache["timestamp"] < self.cache_ttl_ms):
            self.logger.debug("Statut go-librespot retourné depuis le cache")
            return self.status_cache["data"]
            
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
                
            self.logger.debug(f"Récupération du statut depuis {self.api_url}/status")
            async with self.session.get(f"{self.api_url}/status") as response:
                if response.status == 200:
                    status_data = await response.json()
                    self.logger.debug(f"Statut go-librespot reçu: {status_data}")
                    
                    # Mettre à jour le cache
                    self.status_cache = {
                        "data": status_data,
                        "timestamp": now
                    }
                    
                    return status_data
                elif response.status == 204:
                    # Pour les réponses 204 (No Content), retourner un état minimal valide
                    result = {"player": {"is_playing": False, "current_track": None}}
                    
                    # Mettre à jour le cache même pour les réponses 204
                    self.status_cache = {
                        "data": result,
                        "timestamp": now
                    }
                    
                    self.logger.debug("API go-librespot : aucun contenu musical disponible (204)")
                    return result
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
            
    async def send_command(self, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Envoie une commande à l'API go-librespot.
        
        Args:
            command: Commande à envoyer (play, resume, pause, playpause, next, prev, seek)
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
            # Journaliser la requête pour déboguer
            self.logger.debug(f"Envoi de commande {method} {url} avec params: {params}")
            
            if method == "GET":
                async with self.session.get(url) as response:
                    if response.status == 200:
                        result = await response.json()
                        self.logger.debug(f"Réponse API: {result}")
                        # Invalider le cache pour les commandes GET qui modifient l'état
                        if command == "status":
                            self.status_cache["timestamp"] = 0
                        return result
                    else:
                        error_text = await response.text()
                        self.logger.error(f"Erreur API ({response.status}): {error_text}")
                        raise Exception(f"Erreur API ({response.status}): {error_text}")
            else:
                # Adapter les paramètres selon les attentes de l'API go-librespot
                adapted_params = params or {}  # Toujours utiliser au moins un objet vide pour POST

                # Adaptation spécifique pour 'seek'
                if command == 'seek' and 'position_ms' in adapted_params:
                    adapted_params = {'position': adapted_params['position_ms']}
                    if 'relative' in params:
                        adapted_params['relative'] = params['relative']
                
                # S'assurer que next reçoit un corps même vide
                if command == 'next' and not adapted_params:
                    adapted_params = {}  # Envoyer un objet vide explicit
                    
                data = json.dumps(adapted_params)
                headers = {"Content-Type": "application/json"}
                
                # Logs plus détaillés pour le débogage
                self.logger.info(f"Envoi POST détaillé: {url}, data={data}, headers={headers}")
                
                # Faire la requête API
                async with self.session.post(url, data=data, headers=headers) as response:
                    if response.status in [200, 204]:
                        # Invalider le cache après une commande qui modifie l'état
                        self.status_cache["timestamp"] = 0
                        
                        if response.status == 204:
                            self.logger.debug(f"Commande exécutée avec succès (204)")
                            return {}
                        else:
                            result = await response.json()
                            self.logger.debug(f"Réponse API: {result}")
                            return result
                    else:
                        error_text = await response.text()
                        self.logger.error(f"Erreur API ({response.status}): {error_text}")
                        raise Exception(f"Erreur API ({response.status}): {error_text}")
                        
        except aiohttp.ClientError as e:
            self.logger.error(f"Erreur de connexion lors de l'envoi de la commande {command}: {e}")
            raise Exception(f"Erreur de connexion à go-librespot: {e}")
        except Exception as e:
            self.logger.error(f"Erreur lors de l'envoi de la commande {command}: {str(e)}")
            raise