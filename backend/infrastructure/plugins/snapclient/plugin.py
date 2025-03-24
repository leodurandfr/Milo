"""
Plugin pour la source audio snapclient (MacOS).
C'est la classe principale qui intègre tous les composants.
"""
import asyncio
import logging
import os
from typing import Dict, Any, Optional

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.snapclient.process_manager import ProcessManager
from backend.infrastructure.plugins.snapclient.connection_monitor import ConnectionMonitor
from backend.infrastructure.plugins.snapclient.metadata_processor import MetadataProcessor
from backend.infrastructure.plugins.snapclient.discovery_services import DiscoveryService
from backend.infrastructure.plugins.snapclient.connection_manager import ConnectionManager

class SnapclientPlugin(BaseAudioPlugin):
    """
    Plugin pour intégrer snapclient comme source audio.
    
    Snapclient est un client pour recevoir de l'audio depuis un serveur Snapcast,
    typiquement exécuté sur un Mac.
    """
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Initialise le plugin snapclient.
        
        Args:
            event_bus: Bus d'événements pour la communication
            config: Configuration du plugin
        """
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.snapclient_executable = os.path.expanduser(config.get("executable_path", "/usr/bin/snapclient"))
        self.polling_interval = config.get("polling_interval", 5.0)  # En secondes
        self.auto_discover = config.get("auto_discover", True)
        self.auto_connect = config.get("auto_connect", True)
        
        # Composants
        self.process_manager = None
        self.metadata_processor = None
        self.connection_monitor = None
        self.discovery_service = None
        self.connection_manager = None
        
        # État interne
        self.device_connected = False
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin en vérifiant les prérequis, sans démarrer snapclient.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        self.logger.info("Initialisation du plugin snapclient")
        try:
            # Initialiser les composants dans le bon ordre
            self.process_manager = ProcessManager(self.snapclient_executable)
            
            self.metadata_processor = MetadataProcessor(self.event_bus, self.name)
            
            self.connection_monitor = ConnectionMonitor(
                self.process_manager,
                self.metadata_processor,
                self.polling_interval
            )
            
            # Fonction wrapper non-async pour le callback
            def discovery_callback_wrapper(server_info):
                if self.connection_manager:
                    asyncio.create_task(self.connection_manager.handle_new_server_discovery(server_info))

            self.discovery_service = DiscoveryService(discovery_callback_wrapper)
            
            self.connection_manager = ConnectionManager(
                self.event_bus,
                self.metadata_processor,
                self.process_manager
            )
            
            self.logger.info("Plugin snapclient initialisé avec succès (prêt à démarrer)")
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du plugin snapclient: {str(e)}")
            return False
    
    async def start(self) -> bool:
        """
        Démarre la source audio snapclient.
        
        Returns:
            bool: True si le démarrage a réussi, False sinon
        """
        self.logger.info("Démarrage de la source audio snapclient")
        try:
            # Démarrer les services de surveillance et découverte
            await self.connection_monitor.start()
            
            if self.auto_discover:
                await self.discovery_service.start()
            
            await self.connection_manager.start()
            
            # Activement vérifier la connexion pour mettre à jour l'interface
            is_connected = await self.connection_monitor.check_connection()
            self.device_connected = is_connected
            
            # Démarrer automatiquement la découverte si configuré
            if self.auto_discover and not is_connected:
                self.logger.info("Lancement de la découverte de serveurs Snapcast...")
                servers = await self.discovery_service.force_scan()
                
                # Si des serveurs sont trouvés et auto_connect est activé
                if servers and self.auto_connect:
                    # Se connecter au premier serveur trouvé
                    server = servers[0]
                    self.logger.info(f"Connexion automatique au serveur: {server['host']}")
                    await self.connection_manager.connect_to_server(server["host"])
            
            # Activer le plugin
            self.is_active = True
            
            # Publier l'état initial
            connection_info = await self.get_connection_info()
            if connection_info["device_connected"]:
                await self.metadata_processor.publish_status("connected", {
                    "deviceConnected": True,
                    "connected": True,
                    "host": connection_info.get("host"),
                    "device_name": connection_info.get("device_name", "Snapcast")
                })
            else:
                await self.metadata_processor.publish_status("disconnected", {
                    "deviceConnected": False,
                    "connected": False
                })
            
            self.logger.info("Source audio snapclient démarrée avec succès")
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la source audio snapclient: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête la source audio snapclient.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        self.logger.info("Arrêt de la source audio snapclient")
        try:
            # Désactiver le plugin
            self.is_active = False
            
            # Arrêter les services de surveillance et découverte
            await self.connection_monitor.stop()
            await self.discovery_service.stop()
            await self.connection_manager.stop()
            
            # Déconnecter du serveur actuel
            await self.connection_manager.disconnect()
            
            # Publier l'état d'arrêt
            await self.metadata_processor.publish_status("stopped")
            
            self.logger.info("Source audio snapclient arrêtée avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt de la source audio snapclient: {str(e)}")
            return False
        
        async def _force_kill_snapclient(self) -> None:
            """Méthode de secours pour forcer l'arrêt du processus snapclient"""
            try:
                # Utiliser pkill en cas d'échec des méthodes standard
                self.logger.warning("Tentative de kill forcé du processus snapclient")
                subprocess.run(["pkill", "-9", "snapclient"], check=False)
                await asyncio.sleep(0.5)  # Laisser le temps au système
                
                # Vérifier si ça a fonctionné
                if subprocess.run(["pgrep", "snapclient"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0:
                    self.logger.error("Impossible de tuer le processus snapclient même avec pkill -9")
                else:
                    self.logger.info("Processus snapclient terminé avec succès par pkill")
            except Exception as e:
                self.logger.error(f"Erreur lors de la tentative de kill forcé: {str(e)}")
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Récupère l'état actuel de la source audio.
        
        Returns:
            Dict[str, Any]: État actuel avec informations sur le périphérique
        """
        try:
            # Si le plugin n'est pas actif, renvoyer un statut simple
            if not self.is_active:
                return {
                    "is_active": False,
                    "is_playing": False,
                    "device_connected": False,
                    "metadata": {}
                }
                
            # Obtenir l'état actuel
            is_connected = self.connection_monitor.is_connected()
            device_info = self.connection_monitor.get_device_info()
            active_connection = self.connection_manager.get_active_connection()
            
            # Construire le résultat
            result = {
                "is_active": self.is_active,
                "device_connected": is_connected,
                "metadata": device_info,
                "active_connection": active_connection
            }
            
            # Ajouter les serveurs découverts
            if self.discovery_service:
                result["discovered_servers"] = self.discovery_service.get_known_servers()
                
            # Ajouter les connexions en attente
            if self.connection_manager:
                result["pending_connections"] = self.connection_manager.get_pending_connections()
                
            return result
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut snapclient: {str(e)}")
            return {
                "is_active": self.is_active,
                "device_connected": False,
                "error": str(e),
                "metadata": {}
            }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande spécifique à cette source.
        
        Args:
            command: Commande à exécuter
            data: Données supplémentaires pour la commande
                
        Returns:
            Dict[str, Any]: Résultat de la commande
        """
        self.logger.info(f"Traitement de la commande snapclient: {command}")
        try:
            if command == "connect":
                # Connexion à un serveur spécifique
                host = data.get("host")
                if not host:
                    return {"success": False, "error": "Hôte manquant"}
                    
                success = await self.connection_manager.connect_to_server(host)
                return {"success": success}
                
            elif command == "disconnect":
                # Déconnexion du serveur actuel
                success = await self.connection_manager.disconnect()
                return {"success": success}
                
            elif command == "discover":
                # Force un scan de découverte
                servers = await self.discovery_service.force_scan()
                return {"success": True, "servers": servers}
                
            elif command == "accept_connection":
                # Accepte une demande de connexion
                host = data.get("host")
                if not host:
                    return {"success": False, "error": "Hôte manquant"}
                    
                success = await self.connection_manager.accept_connection_request(host)
                return {"success": success}
                
            elif command == "reject_connection":
                # Rejette une demande de connexion
                host = data.get("host")
                if not host:
                    return {"success": False, "error": "Hôte manquant"}
                    
                success = await self.connection_manager.reject_connection_request(host)
                return {"success": success}
                
            elif command == "refresh_status":
                # Force une actualisation du statut
                is_connected = await self.connection_monitor.check_connection()
                device_info = self.connection_monitor.get_device_info()
                
                # Publier les informations mises à jour
                if device_info:
                    await self.metadata_processor.update_device_info(device_info)
                
                # Publier l'état
                if is_connected:
                    await self.metadata_processor.publish_status("connected", device_info)
                else:
                    await self.metadata_processor.publish_status("disconnected")
                
                return {
                    "success": True,
                    "is_connected": is_connected,
                    "device_info": device_info
                }
                
            else:
                return {"success": False, "error": f"Commande non supportée: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur l'état de connexion.
        
        Returns:
            Dict[str, Any]: Informations sur la connexion
        """
        is_connected = self.connection_monitor.is_connected() if self.connection_monitor else False
        device_info = self.connection_monitor.get_device_info() if self.connection_monitor else {}
        active_connection = self.connection_manager.get_active_connection() if self.connection_manager else None
        
        return {
            "device_connected": is_connected,
            "host": active_connection["host"] if active_connection else None,
            "device_name": device_info.get("deviceName", "Snapcast") if device_info else "Snapcast"
        }