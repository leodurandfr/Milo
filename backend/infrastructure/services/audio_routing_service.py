"""
Service de routage audio pour oakOS - Version simplifiée avec ALSA dynamique
"""
import os
import logging
import asyncio
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode, AudioRoutingState
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """Service de routage audio - Version simplifiée avec ALSA dynamique"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        self.state = AudioRoutingState()  # État étendu avec equalizer
        self.state_machine = None  # Référence pour accéder aux plugins
        self._initial_detection_done = False
        
        # Services snapcast (seuls services gérés directement)
        self.snapserver_service = "oakos-snapserver-multiroom.service"
        self.snapclient_service = "oakos-snapclient-multiroom.service"
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence à la machine à états"""
        self.state_machine = state_machine
        # Lancer la détection d'état initial après configuration
        if not self._initial_detection_done:
            asyncio.create_task(self._detect_initial_state())
    
    async def _detect_initial_state(self):
        """Initialise et détecte l'état initial selon l'état réel des services snapcast"""
        try:
            self.logger.info("Initializing ALSA environment and detecting routing state...")
            
            # 1. D'abord initialiser les variables d'environnement avec les valeurs par défaut
            default_mode = AudioRoutingMode.MULTIROOM
            default_equalizer = False
            
            self.state.mode = default_mode
            self.state.equalizer_enabled = default_equalizer
            
            # Initialiser les variables d'environnement
            await self._update_systemd_environment(default_mode)
            self.logger.info(f"ALSA environment initialized: MODE={default_mode.value}, EQUALIZER={default_equalizer}")
            
            # 2. Détecter l'état réel des services snapcast
            snapcast_status = await self.get_snapcast_status()
            if snapcast_status.get("multiroom_available", False):
                detected_mode = AudioRoutingMode.MULTIROOM
                self.logger.info("Snapcast services active → keeping MULTIROOM mode")
            else:
                detected_mode = AudioRoutingMode.DIRECT
                self.logger.info("Snapcast services inactive → switching to DIRECT mode")
            
            # 3. Si l'état détecté diffère des valeurs par défaut, mettre à jour
            if detected_mode != default_mode:
                self.state.mode = detected_mode
                await self._update_systemd_environment(detected_mode)
            
            self._initial_detection_done = True
            self.logger.info(f"Initial routing state: {self.state.mode.value}, equalizer: {self.state.equalizer_enabled}")
            
        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            # En cas d'erreur, garder les valeurs par défaut
            self._initial_detection_done = True
    
    async def set_routing_mode(self, mode: AudioRoutingMode, active_source: AudioSource = None) -> bool:
        """Change le mode de routage audio - Version simplifiée"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
        
        if self.state.mode == mode:
            self.logger.info(f"Already in {mode.value} mode")
            return True
        
        try:
            old_mode = self.state.mode
            self.logger.info(f"Changing routing from {old_mode.value} to {mode.value}")
            
            # 1. Mettre à jour l'état AVANT la transition
            self.state.mode = mode
            
            # 2. Mettre à jour les variables d'environnement ALSA
            await self._update_systemd_environment(mode)
            
            # 3. Effectuer la transition avec le nouvel état
            if mode == AudioRoutingMode.MULTIROOM:
                success = await self._transition_to_multiroom(active_source)
            else:  # DIRECT
                success = await self._transition_to_direct(active_source)
            
            if not success:
                # En cas d'échec, restaurer l'ancien état
                self.state.mode = old_mode
                await self._update_systemd_environment(old_mode)
                self.logger.error(f"Failed to transition to {mode.value} mode, reverting to {old_mode.value}")
                return False
            
            return True
            
        except Exception as e:
            # En cas d'erreur, restaurer l'ancien état
            self.state.mode = old_mode
            await self._update_systemd_environment(old_mode)
            self.logger.error(f"Error changing routing mode: {e}")
            return False

    async def set_equalizer_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Active/désactive l'equalizer - Version simplifiée"""
        if self.state.equalizer_enabled == enabled:
            self.logger.info(f"Equalizer already {'enabled' if enabled else 'disabled'}")
            return True
        
        try:
            old_state = self.state.equalizer_enabled
            self.logger.info(f"Changing equalizer from {old_state} to {enabled}")
            
            # 1. Mettre à jour l'état AVANT de redémarrer le plugin
            self.state.equalizer_enabled = enabled
            
            # 2. Mettre à jour les variables d'environnement ALSA
            await self._update_systemd_environment(self.state.mode)
            
            # 3. Redémarrer le plugin actif - ALSA gère automatiquement le device
            if active_source and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} with equalizer {'enabled' if enabled else 'disabled'}")
                    await plugin.restart()
                    # Plus besoin de change_audio_device() - ALSA fait tout !
            
            return True
            
        except Exception as e:
            # En cas d'erreur, revenir à l'ancien état
            self.state.equalizer_enabled = old_state
            await self._update_systemd_environment(self.state.mode)
            self.logger.error(f"Error changing equalizer state: {e}")
            return False
    
    async def _update_systemd_environment(self, mode: AudioRoutingMode) -> None:
        """Met à jour les variables d'environnement ALSA"""
        try:
            mode_value = mode.value
            equalizer_suffix = "_eq" if self.state.equalizer_enabled else ""
            
            # Mettre à jour OAKOS_MODE et ATTENDRE que ça finisse
            proc1 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_MODE={mode_value}"
            )
            await proc1.communicate()  # ← AJOUTÉ
            
            # Mettre à jour OAKOS_EQUALIZER et ATTENDRE que ça finisse
            proc2 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"OAKOS_EQUALIZER={equalizer_suffix}"
            )
            await proc2.communicate()  # ← AJOUTÉ
            
            os.environ["OAKOS_MODE"] = mode_value
            os.environ["OAKOS_EQUALIZER"] = equalizer_suffix
            
            self.logger.info(f"Updated ALSA environment: MODE={mode_value}, EQUALIZER={equalizer_suffix}")
            
        except Exception as e:
            self.logger.error(f"Error updating environment: {e}")
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode multiroom - Version simplifiée"""
        try:
            # 1. Démarrer snapcast d'abord
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast()
            if not snapcast_success:
                return False
            
            # 2. Redémarrer le plugin si actif - ALSA gère le device automatiquement
            if active_source and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for multiroom mode")
                    await plugin.restart()
                    # Plus besoin de change_audio_device() - ALSA gère tout !
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition vers le mode direct - Version simplifiée"""
        try:
            # 1. Arrêter snapcast
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast()
            
            # 2. Redémarrer le plugin si actif - ALSA gère le device automatiquement
            if active_source and self.state_machine:
                plugin = self.state_machine.plugins.get(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for direct mode")
                    await plugin.restart()
                    # Plus besoin de change_audio_device() - ALSA gère tout !
            
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
    
    def get_state(self) -> AudioRoutingState:
        """Récupère l'état actuel du routage (incluant equalizer)"""
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