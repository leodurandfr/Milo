"""
Gestion des connexions aux serveurs Snapcast - Version optimisée pour utiliser systemd
"""
import asyncio
import logging
from typing import Dict, Any, Optional
import aiofiles

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientConnection:
    """Gère la connexion à un serveur Snapcast."""

    def __init__(self, service_manager, plugin=None):
        self.logger = logging.getLogger("plugin.snapclient.connection")
        self.service_manager = service_manager
        self.plugin = plugin
        self.current_server: Optional[SnapclientServer] = None
        self.service_name = plugin.service_name if plugin else "snapclient.service"

    async def connect(self, server: SnapclientServer) -> bool:
        """
        Se connecte à un serveur Snapcast via systemd.
        """
        try:
            self.logger.info(f"Connexion au serveur {server.name} ({server.host})")
            
            # Vérifier si déjà connecté au même serveur
            if self.current_server and self.current_server.host == server.host:
                service_status = await self.service_manager.get_status(self.service_name)
                
                if service_status.get("active", False):
                    self.logger.info(f"Déjà connecté au serveur {server.name}, pas besoin de redémarrer")
                    
                    # Assurer que le moniteur WebSocket est bien démarré
                    if hasattr(self.plugin, 'monitor') and not self.plugin.monitor.is_connected:
                        await self.plugin.monitor.start(server.host)
                    
                    return True
            
            # Configurer et redémarrer le service
            await self._configure_snapclient(server)
            
            # Redémarrer le service
            if not await self.service_manager.restart(self.service_name):
                self.logger.error(f"Échec du redémarrage du service pour {server.name}")
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

    async def disconnect(self, stop_service: bool = True) -> bool:
        """Se déconnecte du serveur actuel."""
        if not self.current_server:
            return True

        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            # Arrêter le moniteur
            if hasattr(self.plugin, 'monitor') and self.plugin.monitor.host == self.current_server.host:
                await self.plugin.monitor.stop()

            # Arrêter le service si demandé
            if stop_service:
                await self.service_manager.stop(self.service_name)

            # Réinitialiser le serveur actuel
            self.current_server = None
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion: {str(e)}")
            self.current_server = None
            return True
    
    async def _configure_snapclient(self, server: SnapclientServer) -> bool:
        """
        Configure snapclient pour le serveur spécifié.
        Mise à jour du fichier /etc/default/snapclient
        """
        try:
            # Méthode simple: mise à jour d'un fichier d'environnement
            env_file = "/etc/default/snapclient"
            temp_file = "/tmp/snapclient.env"
            
            # Créer un contenu temporaire
            content = f"# Généré automatiquement par oakOS\n"
            content += f"SERVER_HOST={server.host}\n"
            content += f"SERVER_PORT={server.port}\n"
            
            # Écrire dans un fichier temporaire
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(content)
            
            # Copier avec sudo vers l'emplacement final
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cp", temp_file, env_file,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                self.logger.error(f"Erreur configuration snapclient: {stderr.decode().strip()}")
                return False
                
            return True
        except Exception as e:
            self.logger.error(f"Erreur configuration snapclient: {e}")
            return False

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