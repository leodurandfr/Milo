"""
Gestion des connexions aux serveurs Snapcast - Version optimisée pour éviter les redémarrages inutiles.
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
        """
        Se connecte à un serveur Snapcast, en évitant les redémarrages inutiles.
        """
        try:
            self.logger.info(f"Connexion au serveur {server.name} ({server.host})")
            
            # Vérifier si déjà connecté au même serveur pour éviter un redémarrage inutile
            if self.current_server and self.current_server.host == server.host:
                process_info = await self.process_manager.get_process_info()
                
                if process_info.get("running", False):
                    self.logger.info(f"Déjà connecté au serveur {server.name}, pas besoin de redémarrer")
                    
                    # Assurer que le moniteur WebSocket est bien démarré
                    if hasattr(self.plugin, 'monitor') and not self.plugin.monitor.is_connected:
                        await self.plugin.monitor.start(server.host)
                    
                    # Server est déjà défini comme serveur actuel, pas besoin de le changer
                    return True
            
            # Si on est connecté à un autre serveur ou si le processus n'est pas actif,
            # on doit effectuer une nouvelle connexion
            if self.current_server and self.current_server.host != server.host:
                self.logger.info(f"Déconnexion du serveur actuel avant de se connecter à {server.name}")
                await self.disconnect()
            
            # Démarrage du processus (seulement si nécessaire)
            if not (await self.process_manager.get_process_info()).get("running", False):
                self.logger.info(f"Démarrage du processus pour {server.name}")
                if not await self.process_manager.start(server.host):
                    self.logger.error(f"Échec du démarrage du processus pour {server.name}")
                    return False
            
            # Mettre à jour le serveur actuel
            self.current_server = server
            self.logger.info(f"Connecté au serveur {server.name}")
            
            # Démarrer le moniteur WebSocket
            if hasattr(self.plugin, 'monitor'):
                # Arrêter l'ancien moniteur si on change de serveur
                if self.plugin.monitor.host != server.host:
                    await self.plugin.monitor.stop()
                
                # Démarrer le nouveau moniteur si nécessaire
                if not self.plugin.monitor.is_connected:
                    await self.plugin.monitor.start(server.host)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la connexion: {str(e)}")
            return False

    async def disconnect(self, stop_process: bool = True) -> bool:
        """Se déconnecte du serveur actuel."""
        if not self.current_server:
            return True

        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            # Arrêter le moniteur
            if hasattr(self.plugin, 'monitor') and self.plugin.monitor.host == self.current_server.host:
                await self.plugin.monitor.stop()

            # Arrêter le processus si demandé
            if stop_process:
                await self.process_manager.stop()

            # Réinitialiser le serveur actuel
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