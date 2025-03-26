"""
Gestion des connexions aux serveurs Snapcast.
"""
import logging
import asyncio
import uuid
from typing import Dict, Any, Optional, List

from backend.infrastructure.plugins.snapclient.models import SnapclientServer, ConnectionRequest
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.protocol import SnapcastProtocol


class SnapclientConnection:
    """
    Gère la connexion à un serveur Snapcast et les demandes de connexion.
    """
    
    def __init__(self, process_manager: SnapclientProcess):
        """
        Initialise le gestionnaire de connexion.
        
        Args:
            process_manager: Gestionnaire de processus Snapclient
        """
        self.logger = logging.getLogger("plugin.snapclient.connection")
        self.process_manager = process_manager
        self.current_server: Optional[SnapclientServer] = None
        self.pending_requests: Dict[str, ConnectionRequest] = {}
        self.auto_connect = False
        self.protocol = SnapcastProtocol()
    
    def set_auto_connect(self, value: bool):
        """
        Définit si le plugin doit se connecter automatiquement aux serveurs découverts.
        
        Args:
            value: True pour activer la connexion automatique, False sinon
        """
        if self.auto_connect != value:
            self.logger.info(f"Changement de l'état auto_connect: {self.auto_connect} -> {value}")
        self.auto_connect = value
    
    async def connect(self, server: SnapclientServer) -> bool:
        """
        Se connecte à un serveur Snapcast.
        
        Args:
            server: Serveur auquel se connecter
            
        Returns:
            bool: True si la connexion a réussi, False sinon
        """
        try:
            self.logger.info(f"Connexion au serveur Snapcast {server.name} ({server.host})")
            
            # Démarrer le processus avec le nouvel hôte
            success = await self.process_manager.start(server.host)
            
            if success:
                self.current_server = server
                self.logger.info(f"Connecté au serveur {server.name} ({server.host})")
                return True
            else:
                self.logger.error(f"Échec de la connexion au serveur {server.name} ({server.host})")
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la connexion au serveur {server.name} ({server.host}): {str(e)}")
            return False
    
    async def disconnect(self) -> bool:
        """
        Se déconnecte du serveur actuel.
        
        Returns:
            bool: True si la déconnexion a réussi, False sinon
        """
        try:
            if not self.current_server:
                self.logger.warning("Aucun serveur n'est actuellement connecté")
                return True
            
            self.logger.info(f"Déconnexion du serveur {self.current_server.name} ({self.current_server.host})")
            
            # Arrêter le processus
            success = await self.process_manager.stop()
            
            # Même si l'arrêt échoue, considérer que nous sommes déconnectés
            # pour éviter de bloquer l'utilisateur
            if not success:
                self.logger.warning(f"Déconnexion du serveur {self.current_server.name} non confirmée, mais assumée réussie")
            else:
                self.logger.info(f"Déconnecté du serveur {self.current_server.name} ({self.current_server.host})")
                
            # Toujours réinitialiser l'état de connexion
            previous_server = self.current_server
            self.current_server = None
            
            # Pour sécurité, s'assurer qu'aucun processus snapclient ne tourne
            try:
                await asyncio.create_subprocess_exec(
                    "killall", "-q", "snapclient",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            except Exception as e:
                self.logger.debug(f"Erreur lors de l'exécution de killall (ignorée): {str(e)}")
            
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion du serveur: {str(e)}")
            # Même en cas d'erreur, réinitialiser l'état
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
        self.logger.info(f"Demande de connexion créée pour le serveur {server.name} ({server.host}), ID: {request_id}")
        return request
    
    async def handle_discovered_servers(self, servers: List[SnapclientServer], blacklisted_servers: List[str] = None) -> Dict[str, Any]:
        """
        Gère les serveurs découverts en fonction de l'état actuel.
        
        Args:
            servers: Liste des serveurs découverts
            blacklisted_servers: Liste des serveurs blacklistés à éviter
            
        Returns:
            Dict[str, Any]: Résultat du traitement
        """
        if not servers:
            return {
                "action": "none",
                "message": "Aucun serveur trouvé",
                "servers": []
            }
        
        # Filtrer les serveurs blacklistés si une liste est fournie
        available_servers = []
        if blacklisted_servers:
            for server in servers:
                if server.host not in blacklisted_servers:
                    available_servers.append(server)
                else:
                    self.logger.info(f"Serveur {server.host} ignoré car présent dans la blacklist")
        else:
            available_servers = servers
            
        # Si aucun serveur disponible après filtrage
        if not available_servers:
            return {
                "action": "all_servers_blacklisted",
                "message": "Tous les serveurs découverts sont blacklistés",
                "servers": [s.to_dict() for s in servers]
            }
        
        # Si auto_connect est activé et qu'aucun serveur n'est connecté
        if self.auto_connect and not self.current_server and len(available_servers) == 1:
            # Se connecter automatiquement au seul serveur trouvé
            server = available_servers[0]
            self.logger.info(f"Tentative de connexion automatique au serveur {server.name} ({server.host})")
            success = await self.connect(server)
            
            return {
                "action": "auto_connected" if success else "connection_failed",
                "message": f"Connexion automatique au serveur {server.name}" if success else f"Échec de la connexion automatique au serveur {server.name}",
                "server": server.to_dict() if success else None,
                "servers": [s.to_dict() for s in servers]
            }
        
        # Si un serveur est déjà connecté, vérifier s'il est toujours dans la liste
        elif self.current_server:
            # Trouver le serveur actuel dans la liste des serveurs découverts
            current_server_found = False
            for server in servers:
                if server.host == self.current_server.host:
                    current_server_found = True
                    break
            
            if not current_server_found:
                self.logger.warning(f"Le serveur actuellement connecté {self.current_server.name} ({self.current_server.host}) n'est plus disponible")
                
                # Déconnecter automatiquement
                await self.disconnect()
                
                return {
                    "action": "server_disappeared",
                    "message": f"Le serveur {self.current_server.name} n'est plus disponible",
                    "servers": [s.to_dict() for s in servers]
                }
            
            # Vérifier s'il y a de nouveaux serveurs
            new_servers = []
            for server in available_servers:  # Utiliser available_servers pour exclure les blacklistés
                if server.host != self.current_server.host:
                    new_servers.append(server)
            
            if new_servers:
                self.logger.info(f"Nouveaux serveurs trouvés pendant qu'un serveur est connecté: {[s.name for s in new_servers]}")
                
                # Créer une demande de connexion pour le premier nouveau serveur
                if len(new_servers) == 1:
                    request = self.create_connection_request(new_servers[0])
                    
                    return {
                        "action": "new_server_available",
                        "message": f"Un nouveau serveur {new_servers[0].name} est disponible",
                        "request_id": request.request_id,
                        "server": new_servers[0].to_dict(),
                        "current_server": self.current_server.to_dict(),
                        "servers": [s.to_dict() for s in servers]
                    }
                else:
                    # Plusieurs nouveaux serveurs, laisser l'utilisateur choisir
                    return {
                        "action": "multiple_servers_available",
                        "message": f"{len(new_servers)} nouveaux serveurs sont disponibles",
                        "new_servers": [s.to_dict() for s in new_servers],
                        "current_server": self.current_server.to_dict(),
                        "servers": [s.to_dict() for s in servers]
                    }
        
        # Plusieurs serveurs trouvés sans connexion active
        elif len(available_servers) > 1:
            self.logger.info(f"Plusieurs serveurs trouvés sans connexion active: {[s.name for s in available_servers]}")
            
            return {
                "action": "multiple_servers_found",
                "message": f"{len(available_servers)} serveurs trouvés",
                "servers": [s.to_dict() for s in servers]
            }
        
        # Cas par défaut: un seul serveur trouvé, pas de connexion active, pas d'auto-connect
        else:
            return {
                "action": "servers_found",
                "message": f"{len(available_servers)} serveur(s) trouvé(s)",
                "servers": [s.to_dict() for s in servers]
            }
    
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
            self.logger.warning(f"Demande de connexion inconnue: {request_id}")
            return {
                "success": False,
                "message": f"Demande de connexion inconnue: {request_id}"
            }
        
        request = self.pending_requests.pop(request_id)
        
        if accept:
            self.logger.info(f"Acceptation de la demande de connexion {request_id} pour le serveur {request.server.name}")
            
            # Se déconnecter du serveur actuel si nécessaire
            if self.current_server:
                await self.disconnect()
            
            # Se connecter au nouveau serveur
            success = await self.connect(request.server)
            
            return {
                "success": success,
                "message": f"Connexion au serveur {request.server.name}" if success else f"Échec de la connexion au serveur {request.server.name}",
                "server": request.server.to_dict() if success else None
            }
        else:
            self.logger.info(f"Rejet de la demande de connexion {request_id} pour le serveur {request.server.name}")
            
            return {
                "success": True,
                "message": f"Demande de connexion pour le serveur {request.server.name} rejetée"
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
        self.logger.info("Toutes les demandes de connexion en attente ont été effacées")