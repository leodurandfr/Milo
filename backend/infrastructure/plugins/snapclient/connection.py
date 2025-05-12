"""
Gestion des connexions aux serveurs Snapcast - Version concise
"""
import asyncio
import logging
import aiofiles
from typing import Dict, Any, Optional

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
        """Se connecte à un serveur Snapcast via systemd."""
        try:
            self.logger.info(f"Connexion au serveur {server.name} ({server.host})")
            
            # Vérifier si déjà connecté au même serveur
            if self.current_server and self.current_server.host == server.host:
                if await self.service_manager.is_active(self.service_name):
                    self.logger.info(f"Déjà connecté à {server.name}")
                    
                    # S'assurer que le moniteur est démarré
                    if hasattr(self.plugin, 'monitor') and not self.plugin.monitor.is_connected:
                        await self.plugin.monitor.start(server.host)
                    
                    return True
            
            # Configurer le service
            if not await self._configure_snapclient(server):
                return False
            
            # Redémarrer le service
            if not await self.service_manager.restart(self.service_name):
                return False
            
            # Mettre à jour l'état
            self.current_server = server
            
            # Démarrer le moniteur WebSocket
            if hasattr(self.plugin, 'monitor'):
                await self.plugin.monitor.start(server.host)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur connexion: {e}")
            return False

    async def disconnect(self, stop_service: bool = True) -> bool:
        """Se déconnecte du serveur actuel."""
        if not self.current_server:
            return True

        try:
            self.logger.info(f"Déconnexion du serveur {self.current_server.name}")
            
            # Arrêter le moniteur
            if hasattr(self.plugin, 'monitor'):
                await self.plugin.monitor.stop()

            # Arrêter le service si demandé
            if stop_service:
                await self.service_manager.stop(self.service_name)

            # Réinitialiser l'état
            self.current_server = None
            return True
        except Exception as e:
            self.logger.error(f"Erreur déconnexion: {e}")
            self.current_server = None
            return False
    
    async def _configure_snapclient(self, server: SnapclientServer) -> bool:
        """Configure snapclient pour le serveur spécifié."""
        try:
            # Créer le contenu
            content = f"# Généré par oakOS\nSERVER_HOST={server.host}\nSERVER_PORT={server.port}\n"
            
            # Écrire le fichier temporaire
            async with aiofiles.open("/tmp/snapclient.env", 'w') as f:
                await f.write(content)
            
            # Copier avec sudo
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cp", "/tmp/snapclient.env", "/etc/default/snapclient",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Erreur configuration: {e}")
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