"""
Gestion des connexions aux serveurs Snapcast - Version minimale.
"""
import logging
import uuid
from typing import Dict, Any, Optional, List

from backend.infrastructure.plugins.snapclient.models import SnapclientServer, ConnectionRequest
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess

class SnapclientConnection:
    """
    Gère la connexion à un serveur Snapcast.
    Version minimaliste.
    """
    
    def __init__(self, process_manager: SnapclientProcess, plugin=None):
        """
        Initialise le gestionnaire de connexion.
        """
        self.logger = logging.getLogger("plugin.snapclient.connection")
        self.process_manager = process_manager
        self.plugin = plugin 
        self.current_server: Optional[SnapclientServer] = None
        self.pending_requests: Dict[str, ConnectionRequest] = {}
        self.auto_connect = True  # Auto-connexion activée par défaut
    
    async def connect(self, server: SnapclientServer) -> bool:
        """
        Se connecte à un serveur Snapcast.
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
            
            # Si connecté, déconnecter d'abord
            if self.current_server:
                await self.disconnect()
            
            # Démarrer le processus
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
        """
        if not self.current_server:
            return True
        
        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            # Arrêter le moniteur WebSocket
            if hasattr(self, 'plugin') and hasattr(self.plugin, 'monitor'):
                await self.plugin.monitor.stop()
            
            # Arrêter complètement le processus
            await self.process_manager.stop()
            
            # Réinitialiser l'état de connexion
            self.current_server = None
            
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion: {str(e)}")
            # Réinitialiser l'état même en cas d'erreur
            self.current_server = None
            return True
    
    def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur la connexion actuelle.
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
    
    def clear_pending_requests(self):
        """Efface toutes les demandes de connexion en attente."""
        self.pending_requests.clear()