"""
Client API pour communiquer avec go-librespot.
"""
import aiohttp
import logging
import json
import yaml
import os
from typing import Dict, Any, Optional

class LibrespotApiClient:
    """Client API pour communiquer avec go-librespot"""
    
    def __init__(self, api_url: Optional[str] = None, config_path: Optional[str] = None):
        self.api_url = api_url
        self.config_path = config_path
        self.session = None
        self.logger = logging.getLogger("librespot.api")
    
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
            
            self.session = aiohttp.ClientSession()
            self.logger.info(f"Client API initialisé avec URL: {self.api_url}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du client API: {e}")
            return False
    
    async def close(self) -> None:
        """Ferme la session HTTP"""
        if self.session:
            await self.session.close()
            self.session = None
    
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
        Récupère le statut complet de go-librespot.
        
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
            
    async def send_command(self, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Envoie une commande à l'API go-librespot.
        
        Args:
            command: Commande à envoyer (play, pause, next, previous, seek)
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
            if not self.session:
                self.session = aiohttp.ClientSession()
                
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