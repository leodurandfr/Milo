"""
Service de routage audio pour oakOS - Version optimisée sans blocage des signaux.
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
        """Change le mode de routage audio - version optimisée et stable"""
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
                success = await self._transition_to_multiroom_fast(active_source)
            else:  # DIRECT
                success = await self._transition_to_direct_fast(active_source)
            
            if success:
                self.state.mode = mode
                return True
            else:
                self.logger.error(f"Failed to transition to {mode.value} mode")
                return False
            
        except Exception as e:
            self.logger.error(f"Error changing routing mode: {e}")
            return False
    
    async def _transition_to_multiroom_fast(self, active_source: AudioSource = None) -> bool:
        """Transition rapide vers le mode multiroom sans parallélisation excessive"""
        try:
            # 1. Arrêter le plugin actif s'il y en a un
            if active_source:
                self.logger.info("Stopping active plugin before multiroom transition")
                await self._stop_plugin_fast(active_source)
            
            # 2. Démarrer snapcast services séquentiellement mais rapidement
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast_fast()
            
            # 3. Redémarrer le plugin actif
            if active_source and snapcast_success:
                self.logger.info("Starting active plugin for multiroom mode")
                await self._start_plugin_fast(active_source)
            
            return snapcast_success
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct_fast(self, active_source: AudioSource = None) -> bool:
        """Transition rapide vers le mode direct"""
        try:
            # 1. Arrêter snapcast rapidement
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast_fast()
            
            # 2. Redémarrer le plugin actif
            if active_source:
                self.logger.info("Restarting active plugin for direct mode")
                await self._restart_plugin_fast(active_source)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in direct transition: {e}")
            return False
    
    async def _start_snapcast_fast(self) -> bool:
        """Démarre snapcast rapidement et de manière stable"""
        try:
            # Snapserver d'abord
            server_success = await self._start_service_fast(self.snapserver_service)
            if not server_success:
                return False
            
            # Délai minimal pour que snapserver soit prêt
            await asyncio.sleep(0.3)
            
            # Puis snapclient
            client_success = await self._start_service_fast(self.snapclient_service)
            return client_success
            
        except Exception as e:
            self.logger.error(f"Error starting snapcast: {e}")
            return False
    
    async def _stop_snapcast_fast(self) -> None:
        """Arrête snapcast rapidement"""
        try:
            # Arrêter snapclient d'abord (plus rapide)
            await self.service_manager.stop(self.snapclient_service)
            
            # Puis snapserver
            await self.service_manager.stop(self.snapserver_service)
            
            # Attendre que snapserver soit arrêté (libération des ressources)
            await self._wait_for_service_stop_fast(self.snapserver_service, max_wait=3)
            
        except Exception as e:
            self.logger.error(f"Error stopping snapcast: {e}")
    
    async def _start_service_fast(self, service_name: str, max_retries: int = 2) -> bool:
        """Démarre un service rapidement avec retry minimal"""
        for attempt in range(max_retries):
            try:
                success = await self.service_manager.start(service_name)
                if success:
                    # Vérification rapide mais avec timeout court
                    await asyncio.sleep(0.3)
                    is_active = await self.service_manager.is_active(service_name)
                    if is_active:
                        self.logger.info(f"Successfully started {service_name}")
                        return True
                
                if attempt < max_retries - 1:
                    self.logger.warning(f"Retry starting {service_name}...")
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                self.logger.error(f"Error starting {service_name} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
        
        return False
    
    async def _stop_plugin_fast(self, active_source: AudioSource) -> None:
        """Arrête un plugin rapidement"""
        service_name = self.source_services.get(active_source)
        if not service_name:
            return
        
        try:
            is_active = await self.service_manager.is_active(service_name)
            if is_active:
                await self.service_manager.stop(service_name)
                await self._wait_for_service_stop_fast(service_name, max_wait=3)
        except Exception as e:
            self.logger.error(f"Error stopping plugin {service_name}: {e}")
    
    async def _start_plugin_fast(self, active_source: AudioSource) -> None:
        """Démarre un plugin rapidement"""
        service_name = self.source_services.get(active_source)
        if not service_name:
            return
        
        try:
            await self.service_manager.start(service_name)
            await self._wait_for_service_start_fast(service_name, max_wait=3)
        except Exception as e:
            self.logger.error(f"Error starting plugin {service_name}: {e}")
    
    async def _restart_plugin_fast(self, active_source: AudioSource) -> None:
        """Redémarre un plugin rapidement"""
        await self._stop_plugin_fast(active_source)
        await asyncio.sleep(0.2)  # Délai minimal pour libérer les ressources
        await self._start_plugin_fast(active_source)
    
    async def _wait_for_service_stop_fast(self, service_name: str, max_wait: int = 3) -> None:
        """Attente rapide d'arrêt de service avec timeout court"""
        for i in range(max_wait * 5):  # Vérifications toutes les 0.2s
            try:
                is_active = await self.service_manager.is_active(service_name)
                if not is_active:
                    return
                await asyncio.sleep(0.2)
            except Exception as e:
                self.logger.error(f"Error checking service status {service_name}: {e}")
                return
        
        self.logger.warning(f"Service {service_name} still active after {max_wait}s")
    
    async def _wait_for_service_start_fast(self, service_name: str, max_wait: int = 3) -> None:
        """Attente rapide de démarrage de service avec timeout court"""
        for i in range(max_wait * 5):  # Vérifications toutes les 0.2s
            try:
                is_active = await self.service_manager.is_active(service_name)
                if is_active:
                    return
                await asyncio.sleep(0.2)
            except Exception as e:
                self.logger.error(f"Error checking service status {service_name}: {e}")
                return
        
        self.logger.warning(f"Service {service_name} not active after {max_wait}s")
    
    async def _update_systemd_environment(self, mode: AudioRoutingMode) -> None:
        """Met à jour la variable d'environnement via systemctl set-environment"""
        try:
            mode_value = mode.value
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_MODE={mode_value}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to set environment: {stderr.decode()}")
            
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