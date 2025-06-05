"""
Service de routage audio pour oakOS - Version finale avec séquencement des opérations.
"""
import os
import logging
import asyncio
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode, AudioRoutingState
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """Service de gestion du routage audio avec configuration ALSA dynamique"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        self.state = AudioRoutingState()
        
        # Services snapcast
        self.snapserver_service = "oakos-snapserver-multiroom.service"
        self.snapclient_service = "oakos-snapclient-multiroom.service"
        
        # Mapping source -> service
        self.source_services = {
            AudioSource.LIBRESPOT: "oakos-go-librespot.service",
            AudioSource.ROC: "oakos-roc.service",
            AudioSource.BLUETOOTH: "oakos-bluealsa-aplay.service"
        }
    
    async def set_routing_mode(self, mode: AudioRoutingMode, active_source: AudioSource = None) -> bool:
        """Change le mode de routage audio - avec séquencement des opérations"""
        if self.state.mode == mode:
            self.logger.info(f"Already in {mode.value} mode")
            return True
        
        try:
            old_mode = self.state.mode
            self.logger.info(f"Changing routing from {old_mode.value} to {mode.value}")
            
            # 1. Mettre à jour la variable d'environnement globale
            await self._update_systemd_environment(mode)
            
            # 2. Gérer les transitions selon le mode cible
            if mode == AudioRoutingMode.MULTIROOM:
                success = await self._transition_to_multiroom(active_source)
            else:  # DIRECT
                success = await self._transition_to_direct(active_source)
            
            if success:
                # 3. Mettre à jour l'état seulement si tout s'est bien passé
                self.state.mode = mode
                return True
            else:
                self.logger.error(f"Failed to transition to {mode.value} mode")
                return False
            
        except Exception as e:
            self.logger.error(f"Error changing routing mode: {e}")
            return False
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode multiroom avec séquencement"""
        try:
            # 1. Arrêter le plugin actif s'il y en a un
            if active_source:
                self.logger.info("Stopping active plugin before multiroom transition")
                await self._stop_and_wait_plugin(active_source)
            
            # 2. Démarrer snapserver et snapclient
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast_services_with_retry()
            
            # 3. Redémarrer le plugin actif pour qu'il utilise les devices loopback
            if active_source and snapcast_success:
                self.logger.info("Starting active plugin for multiroom mode")
                await self._start_and_wait_plugin(active_source)
            
            return snapcast_success
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode direct avec séquencement"""
        try:
            # 1. Arrêter snapcast d'abord
            self.logger.info("Stopping snapcast services")
            await self.service_manager.stop(self.snapclient_service)
            await self.service_manager.stop(self.snapserver_service)
            
            # 2. Attendre que snapcast soit complètement arrêté
            await self._wait_for_service_stop(self.snapserver_service)
            
            # 3. Redémarrer le plugin actif pour qu'il utilise la sortie directe
            if active_source:
                self.logger.info("Restarting active plugin for direct mode")
                await self._restart_and_wait_plugin(active_source)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in direct transition: {e}")
            return False
    
    async def _stop_and_wait_plugin(self, active_source: AudioSource) -> None:
        """Arrête un plugin et attend qu'il soit complètement arrêté"""
        service_name = self.source_services.get(active_source)
        if not service_name:
            return
        
        is_active = await self.service_manager.is_active(service_name)
        if is_active:
            self.logger.info(f"Stopping {service_name}")
            await self.service_manager.stop(service_name)
            await self._wait_for_service_stop(service_name)
    
    async def _start_and_wait_plugin(self, active_source: AudioSource) -> None:
        """Démarre un plugin et attend qu'il soit opérationnel"""
        service_name = self.source_services.get(active_source)
        if not service_name:
            return
        
        self.logger.info(f"Starting {service_name}")
        await self.service_manager.start(service_name)
        await self._wait_for_service_start(service_name)
    
    async def _restart_and_wait_plugin(self, active_source: AudioSource) -> None:
        """Redémarre un plugin avec attente complète"""
        await self._stop_and_wait_plugin(active_source)
        # Délai supplémentaire pour libérer les ressources
        await asyncio.sleep(1)
        await self._start_and_wait_plugin(active_source)
    
    async def _wait_for_service_stop(self, service_name: str, max_wait: int = 10) -> None:
        """Attend qu'un service soit complètement arrêté"""
        for i in range(max_wait):
            is_active = await self.service_manager.is_active(service_name)
            if not is_active:
                self.logger.info(f"Service {service_name} fully stopped after {i+1}s")
                return
            await asyncio.sleep(1)
        
        self.logger.warning(f"Service {service_name} still active after {max_wait}s")
    
    async def _wait_for_service_start(self, service_name: str, max_wait: int = 10) -> None:
        """Attend qu'un service soit complètement démarré"""
        for i in range(max_wait):
            is_active = await self.service_manager.is_active(service_name)
            if is_active:
                self.logger.info(f"Service {service_name} fully started after {i+1}s")
                return
            await asyncio.sleep(1)
        
        self.logger.warning(f"Service {service_name} not active after {max_wait}s")
    
    async def _start_snapcast_services_with_retry(self) -> bool:
        """Démarre les services snapcast avec retry"""
        # Démarrer snapserver d'abord
        server_success = await self._start_service_with_retry(self.snapserver_service)
        if not server_success:
            return False
        
        # Attendre un peu puis démarrer snapclient
        await asyncio.sleep(2)
        client_success = await self._start_service_with_retry(self.snapclient_service)
        
        return server_success and client_success
    
    async def _start_service_with_retry(self, service_name: str, max_retries: int = 3) -> bool:
        """Démarre un service avec retry"""
        for attempt in range(max_retries):
            self.logger.info(f"Starting {service_name} (attempt {attempt + 1}/{max_retries})")
            
            success = await self.service_manager.start(service_name)
            if success:
                # Vérifier que le service est vraiment démarré
                await asyncio.sleep(2)
                is_active = await self.service_manager.is_active(service_name)
                if is_active:
                    self.logger.info(f"Successfully started {service_name}")
                    return True
            
            if attempt < max_retries - 1:
                self.logger.warning(f"Failed to start {service_name}, retrying in 3s...")
                await asyncio.sleep(3)
        
        return False
    
    async def _update_systemd_environment(self, mode: AudioRoutingMode) -> None:
        """Met à jour la variable d'environnement via systemctl set-environment"""
        try:
            mode_value = mode.value
            
            # Utiliser systemctl set-environment (méthode recommandée)
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_MODE={mode_value}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to set environment: {stderr.decode()}")
            
            # Mettre à jour l'environnement local pour ce processus
            os.environ["OAKOS_MODE"] = mode_value
            
            self.logger.info(f"Updated OAKOS_MODE to {mode_value} via systemctl")
            
        except Exception as e:
            self.logger.error(f"Error updating systemd environment: {e}")
            raise
    
    def get_device_for_source(self, source: AudioSource) -> str:
        """Récupère le device ALSA pour une source (toujours le device dynamique)"""
        mapping = {
            AudioSource.LIBRESPOT: "oakos_spotify",
            AudioSource.BLUETOOTH: "oakos_bluetooth", 
            AudioSource.ROC: "oakos_roc"
        }
        return mapping.get(source, "default")
    
    def get_state(self) -> AudioRoutingState:
        """Récupère l'état actuel du routage"""
        return self.state
    
    async def get_snapcast_status(self) -> Dict[str, Any]:
        """Récupère l'état des services snapcast"""
        try:
            server_active = await self.service_manager.is_active(self.snapserver_service)
            client_active = await self.service_manager.is_active(self.snapclient_service)
            
            return {
                "server_active": server_active,
                "client_active": client_active,
                "multiroom_available": server_active and client_active
            }
        except Exception as e:
            self.logger.error(f"Error getting snapcast status: {e}")
            return {
                "server_active": False,
                "client_active": False,
                "multiroom_available": False
            }
    
    async def get_available_services(self) -> Dict[str, bool]:
        """Récupère la liste des services disponibles"""
        services_status = {}
        
        all_services = list(self.source_services.values()) + [
            self.snapserver_service, 
            self.snapclient_service
        ]
        
        for service in all_services:
            try:
                # Vérifier si le service existe
                proc = await asyncio.create_subprocess_exec(
                    "systemctl", "list-unit-files", service,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()
                
                exists = proc.returncode == 0 and service in stdout.decode()
                is_active = False
                
                if exists:
                    is_active = await self.service_manager.is_active(service)
                
                services_status[service] = {
                    "exists": exists,
                    "active": is_active
                }
                
            except Exception as e:
                self.logger.error(f"Error checking service {service}: {e}")
                services_status[service] = {
                    "exists": False,
                    "active": False,
                    "error": str(e)
                }
        
        return services_status