"""
Surveillant de connexion pour le plugin snapclient.
"""
import asyncio
import logging
import time
import subprocess
import re
from typing import Dict, Any, Optional, Callable, List

class ConnectionMonitor:
    """Surveille l'état de connexion du client Snapcast"""
    
    def __init__(self, process_manager, metadata_processor, polling_interval: float = 5.0):
        self.process_manager = process_manager
        self.metadata_processor = metadata_processor
        self.polling_interval = polling_interval
        self.logger = logging.getLogger("snapclient.monitor")
        self.monitoring_task = None
        self.connected = False
        self.last_host = None
        self.last_connection_check = 0
        self.device_info = {}
        self.consecutive_failures = 0  # Compteur d'échecs consécutifs
        self.max_failures = 2  # Nombre d'échecs consécutifs avant de considérer comme déconnecté
        self.last_stdout = ""  # Pour détecter les changements dans la sortie du processus
        self.disconnection_patterns = [
            "disconnected",
            "connection closed",
            "error",
            "connection reset",
            "timeout",
            "aborted",
            "terminated"
        ]
        self.reconnection_patterns = [
            "connected to",
            "server:",
            "playing stream",
            "time of server"
        ]
    
    async def start(self) -> None:
        """Démarre la surveillance de connexion"""
        if self.monitoring_task is None or self.monitoring_task.done():
            self.monitoring_task = asyncio.create_task(self._monitoring_loop())
            self.logger.info("Surveillance de connexion Snapcast démarrée")
    
    async def stop(self) -> None:
        """Arrête la surveillance de connexion"""
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None
            self.logger.info("Surveillance de connexion Snapcast arrêtée")
    
    async def check_connection(self) -> bool:
        """
        Vérifie si le client est connecté à un serveur Snapcast.
        Détecte activement les déconnexions et reconnexions du serveur.
        
        Returns:
            bool: True si connecté, False sinon
        """
        # Vérifier si le processus est en cours d'exécution
        if not self.process_manager.is_running():
            self.logger.debug("Processus snapclient non en cours d'exécution")
            if self.connected:
                # Si on était connecté mais le processus n'est plus en cours d'exécution
                self.connected = False
                await self.metadata_processor.publish_status("disconnected", {
                    "deviceConnected": False,
                    "connected": False
                })
            return False
        
        try:
            # Obtenir la sortie du processus
            stdout, stderr = await self.process_manager.get_process_output()
            
            # Vérifier s'il y a des messages indiquant une déconnexion
            new_output = stdout.lower()
            
            # Si nous n'étions pas connectés auparavant, chercher des signes de disponibilité du serveur
            # Cela permet de détecter quand un serveur précédemment arrêté redémarre
            if not self.connected and self.last_host:
                reconnect_detected = False
                for pattern in self.reconnection_patterns:
                    if pattern in new_output:
                        self.logger.info(f"Signe de reconnexion détecté: '{pattern}'")
                        reconnect_detected = True
                        
                        # Si le serveur a redémarré, on peut tenter de se reconnecter directement
                        # via le système de découverte
                        if hasattr(self.metadata_processor, 'event_bus'):
                            self.logger.info(f"Publication d'un événement de redémarrage du serveur {self.last_host}")
                            await self.metadata_processor.event_bus.publish("snapclient_server_restarted", {
                                "source": "snapclient",
                                "host": self.last_host
                            })
                            
                        # Communiquer cette information au service de découverte
                        # pour optimiser les tentatives de reconnexion
                        try:
                            from backend.infrastructure.plugins.snapclient.discovery_services import DiscoveryService
                            if hasattr(DiscoveryService, 'set_last_known_active_server'):
                                # Si le service de découverte est accessible, lui signaler le serveur redémarré
                                DiscoveryService.set_last_known_active_server(self.last_host)
                        except ImportError:
                            pass
                        
                        break
                
                if reconnect_detected:
                    self.connected = True
                    return True
            
            # Vérifier s'il y a des messages d'erreur ou de déconnexion
            disconnect_detected = False
            
            # Vérifier les messages de déconnexion dans le nouveau texte
            for pattern in self.disconnection_patterns:
                if pattern in new_output:
                    self.logger.info(f"Message de déconnexion détecté: '{pattern}'")
                    disconnect_detected = True
                    break
            
            # Vérifier également stderr
            if stderr and any(pattern in stderr.lower() for pattern in self.disconnection_patterns):
                self.logger.info(f"Message d'erreur détecté dans stderr")
                disconnect_detected = True
            
            # Si une déconnexion est détectée
            if disconnect_detected:
                self.consecutive_failures += 1
                self.logger.info(f"Possible déconnexion détectée ({self.consecutive_failures}/{self.max_failures})")
                
                if self.consecutive_failures >= self.max_failures:
                    self.logger.warning("Déconnexion confirmée après plusieurs détections")
                    old_connected = self.connected
                    self.connected = False
                    
                    # Ne publier qu'en cas de changement d'état
                    if old_connected:
                        await self.metadata_processor.publish_status("disconnected", {
                            "deviceConnected": False,
                            "connected": False
                        })
                        
                    self.consecutive_failures = 0
                    return False
            else:
                # Si aucun problème n'est détecté, réinitialiser le compteur d'échecs
                if self.consecutive_failures > 0:
                    self.consecutive_failures = 0
            
            # Si nous étions déjà connectés et que le processus est toujours en cours,
            # et qu'aucune déconnexion n'a été détectée, supposer que nous sommes toujours connectés.
            if self.connected and not disconnect_detected:
                return True
                
            # Si on n'était pas connecté, chercher des indications de connexion
            is_connected = False
            host = None
            
            # Chercher des indications de connexion dans stdout
            if "Connected to" in stdout or "connected to" in stdout:
                is_connected = True
                
                # Essayer d'extraire l'hôte
                host_match = re.search(r"[Cc]onnected to\s+([^\s:]+)", stdout)
                if host_match:
                    host = host_match.group(1)
            
            # Chercher des informations sur le serveur
            server_name = None
            server_version = None
            server_match = re.search(r"Server:\s+([^,]+),\s+Version:\s+([^\s]+)", stdout)
            if server_match:
                server_name = server_match.group(1)
                server_version = server_match.group(2)
            
            # Si on trouve des informations de serveur, c'est probablement que la connexion est établie
            if server_name or "Playing stream" in stdout or "Time of server" in stdout:
                is_connected = True
            
            # Mettre à jour l'état interne
            if is_connected != self.connected or host != self.last_host:
                old_connected = self.connected
                self.connected = is_connected
                self.last_host = host
                
                # Mettre à jour les informations sur le périphérique
                if is_connected:
                    self.device_info = {
                        "deviceName": server_name or host or "Snapcast",
                        "host": host,
                        "version": server_version,
                        "connected": is_connected,
                        "last_checked": time.time()
                    }
                    
                    # Stocker ce serveur dans le service de découverte pour les reconnexions futures
                    try:
                        from backend.infrastructure.plugins.snapclient.discovery_services import DiscoveryService
                        if hasattr(DiscoveryService, 'set_last_known_active_server'):
                            DiscoveryService.set_last_known_active_server(host)
                    except ImportError:
                        pass
                    
                    # Publier l'état
                    await self.metadata_processor.publish_status("connected", {
                        "deviceConnected": True,
                        "connected": True,
                        "host": host,
                        "device_name": server_name or host or "Snapcast"
                    })
                elif old_connected:
                    # Ne publier la déconnexion que si on était connecté avant
                    await self.metadata_processor.publish_status("disconnected", {
                        "deviceConnected": False,
                        "connected": False
                    })
            
            self.last_connection_check = time.time()
            return is_connected
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la vérification de connexion: {str(e)}")
            return self.connected  # Garder l'état précédent en cas d'erreur
    
    def get_device_info(self) -> Dict[str, Any]:
        """
        Récupère les informations sur le périphérique connecté.
        
        Returns:
            Dict[str, Any]: Informations sur le périphérique
        """
        return self.device_info
    
    def is_connected(self) -> bool:
        """
        Vérifie si le client est actuellement connecté.
        
        Returns:
            bool: True si connecté, False sinon
        """
        return self.connected
    
    def get_last_host(self) -> Optional[str]:
        """
        Récupère le dernier hôte auquel le client était connecté.
        
        Returns:
            Optional[str]: Dernier hôte connecté ou None
        """
        return self.last_host
    
    async def _monitoring_loop(self) -> None:
        """Boucle principale de surveillance avec vérifications plus fréquentes"""
        try:
            while True:
                try:
                    # Vérifier la connexion
                    is_connected = await self.check_connection()
                    
                    # Si la connexion est perdue alors qu'on était connecté
                    if self.connected and not is_connected:
                        self.logger.warning("Perte de connexion détectée")
                        # Force une vérification supplémentaire après un court délai
                        await asyncio.sleep(1)
                        await self.check_connection()
                        
                except Exception as e:
                    self.logger.error(f"Erreur dans la boucle de surveillance: {str(e)}")
                
                # Intervalle de surveillance plus dynamique: plus rapide si connected
                polling_interval = self.polling_interval / 2 if self.connected else self.polling_interval
                await asyncio.sleep(polling_interval)
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de surveillance annulée")
            raise