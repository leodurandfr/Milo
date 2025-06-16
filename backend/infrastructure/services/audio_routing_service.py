# backend/infrastructure/services/audio_routing_service.py
"""
Service de routage audio pour oakOS - Version refactorisée avec multiroom_enabled
"""
import os
import logging
import asyncio
from typing import Dict, Any, Callable, Optional
from backend.domain.audio_routing import AudioRoutingState
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """Service de routage audio - Version refactorisée avec multiroom_enabled"""
    
    def __init__(self, get_plugin_callback: Optional[Callable] = None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        self.state = AudioRoutingState()
        self.get_plugin = get_plugin_callback  # Callback pour accéder aux plugins
        self._initial_detection_done = False
        
        # Services snapcast
        self.snapserver_service = "oakos-snapserver-multiroom.service"
        self.snapclient_service = "oakos-snapclient-multiroom.service"
    
    def set_plugin_callback(self, callback: Callable) -> None:
        """Définit le callback pour accéder aux plugins (fallback si pas dans constructeur)"""
        if not self.get_plugin:
            self.get_plugin = callback
    
    async def initialize(self) -> None:
        """Initialise l'état du service (appelé après construction)"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
    
    async def _detect_initial_state(self):
        """Initialise et détecte l'état initial selon l'état réel des services snapcast"""
        try:
            self.logger.info("Initializing ALSA environment and detecting routing state...")
            
            # Initialiser avec les valeurs par défaut
            default_multiroom = True  # Par défaut multiroom activé
            default_equalizer = False
            
            self.state.multiroom_enabled = default_multiroom
            self.state.equalizer_enabled = default_equalizer
            
            await self._update_systemd_environment()
            self.logger.info(f"ALSA environment initialized: MULTIROOM={default_multiroom}, EQUALIZER={default_equalizer}")
            
            # Détecter l'état réel des services snapcast
            snapcast_status = await self.get_snapcast_status()
            if snapcast_status.get("multiroom_available", False):
                detected_multiroom = True
                self.logger.info("Snapcast services active → keeping multiroom enabled")
            else:
                detected_multiroom = False
                self.logger.info("Snapcast services inactive → switching to multiroom disabled")
            
            # Mettre à jour si nécessaire
            if detected_multiroom != default_multiroom:
                self.state.multiroom_enabled = detected_multiroom
                await self._update_systemd_environment()
            
            self._initial_detection_done = True
            self.logger.info(f"Initial routing state: multiroom={self.state.multiroom_enabled}, equalizer={self.state.equalizer_enabled}")
            
        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            self._initial_detection_done = True
    
    async def set_multiroom_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Active/désactive le mode multiroom"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
        
        if self.state.multiroom_enabled == enabled:
            self.logger.info(f"Multiroom already {'enabled' if enabled else 'disabled'}")
            return True
        
        try:
            old_state = self.state.multiroom_enabled
            self.logger.info(f"Changing multiroom from {old_state} to {enabled}")
            
            self.state.multiroom_enabled = enabled
            await self._update_systemd_environment()
            
            if enabled:
                success = await self._transition_to_multiroom(active_source)
            else:
                success = await self._transition_to_direct(active_source)
            
            if not success:
                # Restaurer l'ancien état
                self.state.multiroom_enabled = old_state
                await self._update_systemd_environment()
                self.logger.error(f"Failed to transition multiroom to {enabled}, reverting to {old_state}")
                return False
            
            return True
            
        except Exception as e:
            # Restaurer l'ancien état
            self.state.multiroom_enabled = old_state
            await self._update_systemd_environment()
            self.logger.error(f"Error changing multiroom state: {e}")
            return False

    async def set_equalizer_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Active/désactive l'equalizer"""
        if self.state.equalizer_enabled == enabled:
            self.logger.info(f"Equalizer already {'enabled' if enabled else 'disabled'}")
            return True
        
        try:
            old_state = self.state.equalizer_enabled
            self.logger.info(f"Changing equalizer from {old_state} to {enabled}")
            
            self.state.equalizer_enabled = enabled
            await self._update_systemd_environment()
            
            # Redémarrer le plugin actif via callback
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} with equalizer {'enabled' if enabled else 'disabled'}")
                    await plugin.restart()
            
            return True
            
        except Exception as e:
            # Restaurer l'ancien état
            self.state.equalizer_enabled = old_state
            await self._update_systemd_environment()
            self.logger.error(f"Error changing equalizer state: {e}")
            return False
    
    async def _update_systemd_environment(self) -> None:
        """Met à jour les variables d'environnement ALSA - Version COMPATIBLE"""
        try:
            # Conversion bool → string pour ALSA (compatible avec asound.conf existant)
            mode_value = "multiroom" if self.state.multiroom_enabled else "direct"
            equalizer_value = "_eq" if self.state.equalizer_enabled else ""
            
            proc1 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_MODE={mode_value}"
            )
            await proc1.communicate()
            
            proc2 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_EQUALIZER={equalizer_value}"
            )
            await proc2.communicate()
            
            os.environ["OAKOS_MODE"] = mode_value
            os.environ["OAKOS_EQUALIZER"] = equalizer_value
            
            self.logger.info(f"Updated ALSA environment: MODE={mode_value}, EQUALIZER={equalizer_value}")
            
        except Exception as e:
            self.logger.error(f"Error updating environment: {e}")
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode multiroom"""
        try:
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast()
            if not snapcast_success:
                return False
            
            # Redémarrer le plugin via callback
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for multiroom mode")
                    await plugin.restart()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode direct"""
        try:
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast()
            
            # Redémarrer le plugin via callback
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for direct mode")
                    await plugin.restart()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in direct transition: {e}")
            return False
    
    async def _start_snapcast(self) -> bool:
        """Démarre les services snapcast"""
        try:
            success = await self.service_manager.start(self.snapserver_service)
            if not success:
                return False
            
            await asyncio.sleep(0.5)
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
            return {"server_active": False, "client_active": False, "multiroom_available": False}
    
    async def get_available_services(self) -> Dict[str, bool]:
        """Récupère la liste des services disponibles"""
        services_status = {}
        
        services_to_check = [
            "oakos-go-librespot.service", "oakos-roc.service", 
            "oakos-bluealsa-aplay.service", self.snapserver_service, self.snapclient_service
        ]
        
        for service in services_to_check:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "systemctl", "list-unit-files", service,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()
                
                exists = proc.returncode == 0 and service in stdout.decode()
                is_active = False
                
                if exists:
                    is_active = await self.service_manager.is_active(service)
                
                services_status[service] = {"exists": exists, "active": is_active}
                
            except Exception as e:
                self.logger.error(f"Error checking service {service}: {e}")
                services_status[service] = {"exists": False, "active": False, "error": str(e)}
        
        return services_status