"""
Surveillant de connexion pour le plugin snapclient.
"""
import asyncio
import logging
import time
import subprocess
import re
import socket
from typing import Dict, Any, Optional, Callable, List
from backend.infrastructure.plugins.base import BaseAudioPlugin


class ConnectionMonitor:
    """Surveille l'état de connexion du client Snapcast"""
    
    def __init__(self, process_manager, metadata_processor, polling_interval: float = 2.0):
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
        self.max_failures = 1  # Réduit à 1 pour confirmer une déconnexion plus rapidement
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
                await self.metadata_processor.publish_plugin_state(
                    BaseAudioPlugin.STATE_INACTIVE,
                    {
                        "deviceConnected": False,
                        "connected": False,
                        "forceUpdate": True
                    }
                )
                
                # Forcer une publication d'événement direct pour le WebSocket
                if hasattr(self.metadata_processor, 'event_bus'):
                    await self.metadata_processor.event_bus.publish("snapclient_disconnected", {
                        "source": "snapclient",
                        "status": "disconnected",
                        "connected": False,
                        "deviceConnected": False,
                        "timestamp": time.time()
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
                                "host": self.last_host,
                                "timestamp": time.time()
                            })
                        
                        # Forcer l'état connecté et publier immédiatement
                        self.connected = True
                        
                        # Tenter de détecter le nom du serveur dans les logs
                        server_name = None
                        server_version = None
                        
                        # Chercher le nom du serveur avec différents patterns
                        server_match = re.search(r"Server:\s+([^,]+),\s+Version:\s+([^\s]+)", stdout)
                        if server_match:
                            server_name = server_match.group(1)
                            server_version = server_match.group(2)
                        
                        # Chercher également avec d'autres patterns
                        if not server_name:
                            host_name_match = re.search(r"Hello from [^,]*, host:\s+([^,\s]+)", stdout)
                            if host_name_match:
                                server_name = host_name_match.group(1)
                        
                        # Essayer de résoudre le nom d'hôte à partir de l'IP
                        if not server_name and self.last_host:
                            try:
                                hostname = socket.getfqdn(self.last_host)
                                if hostname and hostname != self.last_host:
                                    server_name = hostname
                            except:
                                pass
                                
                        # Publier un événement de connexion avec les infos disponibles
                        await self.metadata_processor.publish_plugin_state(
                            BaseAudioPlugin.STATE_CONNECTED,
                            {
                                "deviceConnected": True,
                                "connected": True,
                                "host": self.last_host,
                                "device_name": server_name or self.last_host or "Snapcast",
                                "forceUpdate": True
                            }
                        )
                        
                        self.logger.info(f"Reconnexion automatique détectée pour {self.last_host}")
                        return True
                
                if reconnect_detected:
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
                    
                    # Publier la déconnexion immédiatement avec force update
                    await self.metadata_processor.publish_plugin_state(
                        BaseAudioPlugin.STATE_INACTIVE,
                        {
                            "deviceConnected": False,
                            "connected": False,
                            "forceUpdate": True
                        }
                    )
                    
                    # Forcer une publication d'événement direct pour le WebSocket
                    if hasattr(self.metadata_processor, 'event_bus'):
                        await self.metadata_processor.event_bus.publish("snapclient_disconnected", {
                            "source": "snapclient",
                            "status": "disconnected",
                            "connected": False,
                            "deviceConnected": False,
                            "timestamp": time.time()
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
            
            # Chercher des informations sur le serveur de façon plus complète
            server_name = None
            server_version = None
            
            # Chercher le nom du serveur avec différents patterns
            server_match = re.search(r"Server:\s+([^,]+),\s+Version:\s+([^\s]+)", stdout)
            if server_match:
                server_name = server_match.group(1)
                server_version = server_match.group(2)
            
            # Chercher également avec d'autres patterns courants dans les logs
            if not server_name:
                host_name_match = re.search(r"Hello from [^,]*, host:\s+([^,\s]+)", stdout)
                if host_name_match:
                    server_name = host_name_match.group(1)
            
            # Si on trouve des informations de serveur, c'est probablement que la connexion est établie
            if server_name or "Playing stream" in stdout or "Time of server" in stdout:
                is_connected = True
            
            # Essayer de résoudre le nom d'hôte à partir de l'IP si on a trouvé une IP mais pas de nom
            if host and not server_name:
                try:
                    # Essayer de faire une résolution DNS inverse
                    hostname = socket.getfqdn(host)
                    if hostname and hostname != host:
                        server_name = hostname
                        self.logger.info(f"Nom d'hôte résolu pour {host}: {server_name}")
                except Exception as e:
                    self.logger.debug(f"Impossible de résoudre le nom d'hôte pour {host}: {e}")
            
            # Mettre à jour l'état interne si quelque chose a changé
            if is_connected != self.connected or host != self.last_host:
                old_connected = self.connected
                self.connected = is_connected
                self.last_host = host
                
                # Mettre à jour les informations sur le périphérique
                if is_connected:
                    # Ajouter une trace de débogage pour analyse
                    self.logger.info(f"Périphérique connecté - IP: {host}, Nom: {server_name}")
                    
                    self.device_info = {
                        "deviceName": server_name or host or "Snapcast",
                        "host": host,
                        "version": server_version,
                        "connected": is_connected,
                        "last_checked": time.time()
                    }
                    
                    # Publier l'état standardisé de connexion
                    await self.metadata_processor.publish_plugin_state(
                        BaseAudioPlugin.STATE_CONNECTED,
                        {
                            "deviceConnected": True,
                            "connected": True,
                            "host": host,
                            "device_name": server_name or host or "Snapcast",
                            "forceUpdate": True
                        }
                    )
                    self.logger.info(f"Publication d'état STATE_CONNECTED pour {host}")
                    
                elif old_connected:
                    # Ne publier la déconnexion que si on était connecté avant
                    await self.metadata_processor.publish_plugin_state(
                        BaseAudioPlugin.STATE_INACTIVE,
                        {
                            "deviceConnected": False,
                            "connected": False,
                            "forceUpdate": True
                        }
                    )
                    
                    # Publier un événement direct pour le WebSocket
                    if hasattr(self.metadata_processor, 'event_bus'):
                        await self.metadata_processor.event_bus.publish("snapclient_disconnected", {
                            "source": "snapclient",
                            "status": "disconnected",
                            "connected": False,
                            "deviceConnected": False,
                            "timestamp": time.time()
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