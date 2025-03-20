"""
Surveillant de connexion pour le plugin snapclient.
"""
import asyncio
import logging
import time
import subprocess
import re
from typing import Dict, Any, Optional, Callable

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
            
            # Analyser la sortie pour déterminer l'état de connexion
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
            
            # Mettre à jour l'état interne
            if is_connected != self.connected or host != self.last_host:
                self.connected = is_connected
                self.last_host = host
                
                # Mettre à jour les informations sur le périphérique
                self.device_info = {
                    "deviceName": server_name or host or "Snapcast",
                    "host": host,
                    "version": server_version,
                    "connected": is_connected,
                    "last_checked": time.time()
                }
                
                # Publier l'état
                if is_connected:
                    await self.metadata_processor.publish_status("connected", {
                        "deviceConnected": True,
                        "connected": True,
                        "host": host,
                        "device_name": server_name or host or "Snapcast"
                    })
                else:
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
    
    async def _monitoring_loop(self) -> None:
        """Boucle principale de surveillance"""
        try:
            while True:
                try:
                    # Vérifier la connexion
                    await self.check_connection()
                        
                except Exception as e:
                    self.logger.error(f"Erreur dans la boucle de surveillance: {str(e)}")
                
                await asyncio.sleep(self.polling_interval)
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de surveillance annulée")
            raise