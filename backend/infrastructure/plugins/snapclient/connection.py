"""
Gestion des connexions aux serveurs Snapcast - Version optimisée.
"""
import logging
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess

class SnapclientConnection:
    """Gère la connexion à un serveur Snapcast."""

    def __init__(self, process_manager: SnapclientProcess, plugin=None):
        self.logger = logging.getLogger("plugin.snapclient.connection")
        self.process_manager = process_manager
        self.plugin = plugin
        self.current_server: Optional[SnapclientServer] = None

    async def connect(self, server: SnapclientServer) -> bool:
        """Se connecte à un serveur Snapcast."""
        try:
            self.logger.info(f"Connexion au serveur {server.name} ({server.host})")
            
            # Vérification simplifiée si déjà connecté au même serveur
            if (self.current_server and 
                self.current_server.host == server.host and 
                (await self.process_manager.get_process_info()).get("running", False)):
                self.logger.info(f"Déjà connecté au serveur {server.name}")
                return True

            # Déconnexion si on est connecté à un autre serveur
            if self.current_server:
                await self.disconnect()

            # Démarrage du processus
            if await self.process_manager.start(server.host):
                self.current_server = server
                self.logger.info(f"Connecté au serveur {server.name}")
                if hasattr(self.plugin, 'monitor'):
                    await self.plugin.monitor.start(server.host)
                return True
            
            self.logger.error(f"Échec de connexion au serveur {server.name}")
            return False
        except Exception as e:
            self.logger.error(f"Erreur lors de la connexion: {str(e)}")
            return False

    async def disconnect(self, stop_process: bool = True) -> bool:
        """Se déconnecte du serveur actuel."""
        if not self.current_server:
            return True

        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            if hasattr(self.plugin, 'monitor') and self.plugin.monitor.host == self.current_server.host:
                await self.plugin.monitor.stop()

            if stop_process:
                await self.process_manager.stop()

            self.current_server = None
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion: {str(e)}")
            self.current_server = None
            return True  

    def get_connection_info(self) -> Dict[str, Any]:
        """Récupère des informations sur la connexion actuelle."""
        if not self.current_server:
            return {"device_connected": False, "device_name": None, "host": None}

        return {
            "device_connected": True,
            "device_name": self.current_server.name,
            "host": self.current_server.host,
            "port": self.current_server.port
        }