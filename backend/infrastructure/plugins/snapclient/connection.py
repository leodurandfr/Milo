"""
Gestion des connexions aux serveurs Snapcast - Version optimisée.
"""
import logging
import asyncio
import uuid
from typing import Dict, Any, Optional, List

from backend.infrastructure.plugins.snapclient.models import SnapclientServer, ConnectionRequest
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess

class SnapclientConnection:
    """
    Gère la connexion à un serveur Snapcast et les demandes de connexion.
    Version simplifiée.
    """
    
    def __init__(self, process_manager: SnapclientProcess, plugin=None):
        """
        Initialise le gestionnaire de connexion.
        
        Args:
            process_manager: Gestionnaire de processus Snapclient
            plugin: Référence au plugin parent (pour accéder au moniteur)
        """
        self.logger = logging.getLogger("plugin.snapclient.connection")
        self.process_manager = process_manager
        self.plugin = plugin 
        self.current_server: Optional[SnapclientServer] = None
        self.pending_requests: Dict[str, ConnectionRequest] = {}
        self.auto_connect = False
    
    def set_auto_connect(self, value: bool):
        """
        Définit si le plugin doit se connecter automatiquement aux serveurs découverts.
        
        Args:
            value: True pour activer la connexion automatique, False sinon
        """
        self.auto_connect = value
    
    async def connect(self, server: SnapclientServer) -> bool:
        """
        Se connecte à un serveur Snapcast.
        Version simplifiée.
        
        Args:
            server: Serveur auquel se connecter
                
        Returns:
            bool: True si la connexion a réussi, False sinon
        """
        try:
            self.logger.info(f"Connexion au serveur {server.name} ({server.host})")
            
            # Vérifier si nous sommes déjà connectés au même serveur
            if self.current_server and self.current_server.host == server.host:
                # Vérifier si le processus est toujours en cours d'exécution
                process_info = await self.process_manager.get_process_info()
                if process_info.get("running", False):
                    self.logger.info(f"Déjà connecté au serveur {server.name}")
                    return True
                
                # Redémarrer le processus
                success = await self.process_manager.start(server.host)
                return success
            
            # Si nous sommes connectés à un serveur différent, nous déconnecter d'abord
            if self.current_server:
                await self.disconnect()
            
            # Démarrer le processus avec le nouvel hôte
            success = await self.process_manager.start(server.host)
            
            if success:
                self.current_server = server
                self.logger.info(f"Connecté au serveur {server.name} ({server.host})")
                
                # Démarrer le moniteur WebSocket après connexion réussie
                if hasattr(self, 'plugin') and hasattr(self.plugin, 'monitor'):
                    await self.plugin.monitor.start(server.host)
                    
                return True
            else:
                self.logger.error(f"Échec de la connexion au serveur {server.name}")
                return False
                    
        except Exception as e:
            self.logger.error(f"Erreur lors de la connexion: {str(e)}")
            return False
    
    async def disconnect(self) -> bool:
        """
        Se déconnecte du serveur actuel.
        Version simplifiée.
        
        Returns:
            bool: True si la déconnexion a réussi, False sinon
        """
        if not self.current_server:
            return True
        
        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            # Arrêter le moniteur WebSocket
            if hasattr(self, 'plugin') and hasattr(self.plugin, 'monitor'):
                await self.plugin.monitor.stop()
            
            # Arrêter le processus
            await self.process_manager.stop()
            
            # Réinitialiser l'état de connexion
            self.current_server = None
            
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion: {str(e)}")
            # Réinitialiser l'état même en cas d'erreur
            self.current_server = None
            return True
    
    def create_connection_request(self, server: SnapclientServer) -> ConnectionRequest:
        """
        Crée une demande de connexion pour un serveur.
        
        Args:
            server: Serveur pour lequel créer une demande
            
        Returns:
            ConnectionRequest: Demande de connexion créée
        """
        request_id = str(uuid.uuid4())
        request = ConnectionRequest(server=server, request_id=request_id)
        self.pending_requests[request_id] = request
        self.logger.info(f"Demande de connexion créée pour {server.name}, ID: {request_id}")
        return request
    
    async def handle_connection_request(self, request_id: str, accept: bool) -> Dict[str, Any]:
        """
        Traite une demande de connexion.
        
        Args:
            request_id: ID de la demande à traiter
            accept: True pour accepter la demande, False pour la rejeter
            
        Returns:
            Dict[str, Any]: Résultat du traitement
        """
        if request_id not in self.pending_requests:
            return {
                "success": False,
                "message": f"Demande de connexion inconnue: {request_id}"
            }
        
        request = self.pending_requests.pop(request_id)
        
        if accept:
            self.logger.info(f"Acceptation de la demande {request_id} pour {request.server.name}")
            
            # Se déconnecter du serveur actuel si nécessaire
            if self.current_server:
                await self.disconnect()
            
            # Se connecter au nouveau serveur
            success = await self.connect(request.server)
            
            return {
                "success": success,
                "message": f"Connexion au serveur {request.server.name}" if success else f"Échec de la connexion",
                "server": request.server.to_dict() if success else None
            }
        else:
            self.logger.info(f"Rejet de la demande {request_id} pour {request.server.name}")
            
            return {
                "success": True,
                "message": f"Demande de connexion pour {request.server.name} rejetée"
            }
    
    def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur la connexion actuelle.
        
        Returns:
            Dict[str, Any]: Informations sur la connexion
        """
        if not self.current_server:
            return {
                "device_connected": False,
                "device_name": None,
                "host": None
            }
        
        return {
            "device_connected": True,
            "device_name": self.current_server.name,
            "host": self.current_server.host,
            "port": self.current_server.port
        }
    
    def get_pending_requests(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste des demandes de connexion en attente.
        
        Returns:
            List[Dict[str, Any]]: Liste des demandes en attente
        """
        return [
            {
                "request_id": request_id,
                "server": request.server.to_dict(),
                "timestamp": request.timestamp
            }
            for request_id, request in self.pending_requests.items()
        ]
    
    def clear_pending_requests(self):
        """Efface toutes les demandes de connexion en attente."""
        self.pending_requests.clear()