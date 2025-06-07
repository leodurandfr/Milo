"""
Service de routage audio pour oakOS - Version CLEAN sans redondances
"""
import os
import logging
import asyncio
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode, AudioRoutingState
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """Service de routage audio - Version simplifiée qui réutilise les plugins existants"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        self.state = AudioRoutingState()
        self.state_machine = None  # Référence pour accéder aux plugins
        
        # Services snapcast (seuls services gérés directement)
        self.snapserver_service = "oakos-snapserver-multiroom.service"
        self.snapclient_service = "oakos-snapclient-multiroom.service"
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence à la machine à états"""
        self.state_machine = state_machine
    
    async def set_routing_mode(self, mode: AudioRoutingMode, active_source: AudioSource = None) -> bool:
        """Change le mode de routage audio"""
        if self.state.mode == mode:
            self.logger.info(f"Already in {mode.value} mode")
            return True
        
        try:
            old_mode = self.state.mode
            self.logger.info(f"Changing routing from {old_mode.value} to {mode.value}")
            
            # 1. Mettre à jour la variable d'environnement
            await self._update_systemd_environment(mode)
            
            # 2. Effectuer la transition
            if mode == AudioRoutingMode.MULTIROOM:
                success = await self._transition_to_multiroom(active_source)
            else:  # DIRECT
                success = await self._transition_to_direct(active_source)
            
            if success:
                self.state.mode = mode
                return True
            else:
                self.logger.error(f"Failed to transition to {mode.value} mode")
                return False
            
        except Exception as e:
            self.logger.error(f"Error changing routing mode: {e}")
            return False
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode multiroom - Version CLEAN"""
        try:
            # 1. Arrêter le plugin actif via ses propres méthodes
            if active_source and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Stopping plugin {active_source.value}")
                    await plugin.stop()
            
            # 2. Démarrer snapcast
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast()
            
            # 3. Redémarrer le plugin via ses propres méthodes
            if active_source and snapcast_success and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Starting plugin {active_source.value}")
                    await plugin.start()
            
            return snapcast_success
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode direct - Version CLEAN"""
        try:
            # 1. Arrêter snapcast
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast()
            
            # 2. Redémarrer le plugin actif via ses propres méthodes
            if active_source and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value}")
                    await plugin.stop()
                    await asyncio.sleep(0.2)  # Délai minimal
                    await plugin.start()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in direct transition: {e}")
            return False
    
    async def _start_snapcast(self) -> bool:
        """Démarre les services snapcast"""
        try:
            # Snapserver d'abord
            success = await self.service_manager.start(self.snapserver_service)
            if not success:
                return False
            
            # Attendre que snapserver soit prêt
            await asyncio.sleep(0.5)
            
            # Puis snapclient
            success = await self.service_manager.start(self.snapclient_service)
            return success
            
        except Exception as e:
            self.logger.error(f"Error starting snapcast: {e}")
            return False
    
    async def _stop_snapcast(self) -> None:
        """Arrête les services snapcast"""
        try:
            await self.service_manager.stop(self.snapclient_service)
            await self.service_manager.stop(self.snapserver_service)
        except Exception as e:
            self.logger.error(f"Error stopping snapcast: {e}")
    
    async def _update_systemd_environment(self, mode: AudioRoutingMode) -> None:
        """Met à jour la variable d'environnement OAKOS_MODE"""
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
            self.logger.info(f"Updated OAKOS_MODE to {mode_value}")
            
        except Exception as e:
            self.logger.error(f"Error updating systemd environment: {e}")
            raise
    
    def get_device_for_source(self, source: AudioSource) -> str:
        """Récupère le device ALSA pour une source"""
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
        
        # Liste des services à vérifier
        services_to_check = [
            "oakos-go-librespot.service",
            "oakos-roc.service", 
            "oakos-bluealsa-aplay.service",
            self.snapserver_service,
            self.snapclient_service
        ]
        
        for service in services_to_check:
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