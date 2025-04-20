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

            # Si connecté, déconnecter d'abord (arrête le processus par défaut)
            if self.current_server:
                await self.disconnect() # stop_process=True par défaut

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

    # --- MODIFICATION ICI ---
    async def disconnect(self, stop_process: bool = True) -> bool:
        """
        Se déconnecte du serveur actuel.
        Args:
            stop_process: Si True (par défaut), arrête également le processus snapclient.
                          Si False, laisse le processus tourner (utile si serveur disparaît).
        """
        if not self.current_server:
            self.logger.debug("Appel à disconnect alors que non connecté, rien à faire.")
            return True

        disconnected_server_name = self.current_server.name # Garder pour le log

        try:
            self.logger.info(f"Déconnexion du serveur {disconnected_server_name} (stop_process={stop_process})")

            # Arrêter le moniteur WebSocket s'il est actif pour ce serveur
            # C'est important car le moniteur pourrait tourner même si disconnect est appelé
            if hasattr(self, 'plugin') and hasattr(self.plugin, 'monitor') and self.plugin.monitor.host == self.current_server.host:
                 self.logger.debug("Arrêt du moniteur depuis la déconnexion.")
                 await self.plugin.monitor.stop()

            # Arrêter le processus snapclient SEULEMENT si demandé
            if stop_process:
                self.logger.info("Arrêt du processus snapclient demandé.")
                await self.process_manager.stop()
            else:
                self.logger.info("Déconnexion demandée sans arrêt du processus snapclient.")
                # Optionnel: redémarrer le processus sans hôte pour le remettre en attente ?
                # await self.process_manager.restart(host=None) # A évaluer si c'est le comportement désiré

            # Réinitialiser l'état de connexion APRÈS les opérations
            self.current_server = None
            self.logger.info(f"État de connexion réinitialisé après déconnexion de {disconnected_server_name}")

            return True

        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion de {disconnected_server_name}: {str(e)}")
            # Réinitialiser l'état même en cas d'erreur pour éviter un état incohérent
            self.current_server = None
            self.logger.warning(f"État de connexion réinitialisé suite à une erreur de déconnexion.")
            return True # Indiquer succès pour ne pas bloquer, l'erreur est logguée

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