# backend/infrastructure/services/audio_routing_service.py
"""
Service de routage audio pour Milo - Version avec événements multiroom_enabling et multiroom_disabling
"""
import os
import json
import logging
import asyncio
import aiofiles
from pathlib import Path
from typing import Dict, Any, Callable, Optional
from backend.domain.audio_routing import AudioRoutingState
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """Service de routage audio avec notifications anticipées d'activation/désactivation"""
    
    # Constantes pour la persistance
    STATE_DIR = Path("/var/lib/milo")
    STATE_FILE = STATE_DIR / "last_routing_state.json"
    
    def __init__(self, get_plugin_callback: Optional[Callable] = None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        self.state = AudioRoutingState()
        self.get_plugin = get_plugin_callback
        self._initial_detection_done = False
        
        self.snapcast_websocket_service = None
        self.snapcast_service = None
        self.state_machine = None
        
        # Services snapcast
        self.snapserver_service = "milo-snapserver-multiroom.service"
        self.snapclient_service = "milo-snapclient-multiroom.service"
        
        self._ensure_state_directory()
    
    def set_snapcast_websocket_service(self, service) -> None:
        """Définit la référence vers SnapcastWebSocketService"""
        self.snapcast_websocket_service = service
    
    def set_snapcast_service(self, service) -> None:
        """Définit la référence vers SnapcastService"""
        self.snapcast_service = service
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence vers StateMachine"""
        self.state_machine = state_machine
    
    def set_plugin_callback(self, callback: Callable) -> None:
        """Définit le callback pour accéder aux plugins"""
        if not self.get_plugin:
            self.get_plugin = callback
    
    async def initialize(self) -> None:
        """Initialise l'état du service"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
    
    def _ensure_state_directory(self) -> None:
        """Crée le répertoire d'état si nécessaire"""
        try:
            self.STATE_DIR.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"State directory ensured: {self.STATE_DIR}")
        except Exception as e:
            self.logger.error(f"Failed to create state directory {self.STATE_DIR}: {e}")
    
    async def load_last_state(self) -> bool:
        """Charge le dernier état depuis le fichier JSON"""
        try:
            if not self.STATE_FILE.exists():
                self.logger.info(f"No state file found at {self.STATE_FILE}, using defaults")
                return False
            
            async with aiofiles.open(self.STATE_FILE, 'r') as f:
                content = await f.read()
                data = json.loads(content)
            
            loaded_state = AudioRoutingState.from_dict(data)
            self.state = loaded_state
            
            self.logger.info(f"Loaded last state: multiroom={self.state.multiroom_enabled}, equalizer={self.state.equalizer_enabled}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to load last state from {self.STATE_FILE}: {e}")
            return False
    
    async def save_current_state(self) -> bool:
        """Sauvegarde l'état actuel dans le fichier JSON"""
        try:
            data = self.state.to_dict()
            
            temp_file = self.STATE_FILE.with_suffix('.tmp')
            
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(json.dumps(data, indent=2))
            
            temp_file.replace(self.STATE_FILE)
            
            self.logger.debug(f"Saved current state: {data}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save state to {self.STATE_FILE}: {e}")
            return False
    
    async def _detect_initial_state(self):
        """Initialise et détecte l'état initial"""
        try:
            self.logger.info("Initializing routing state with persistence...")
            
            state_loaded = await self.load_last_state()
            
            if not state_loaded:
                self.logger.info("Using default values (first run)")
                self.state.multiroom_enabled = False
                self.state.equalizer_enabled = False
            
            await self._update_systemd_environment()
            self.logger.info(f"ALSA environment initialized: MULTIROOM={self.state.multiroom_enabled}, EQUALIZER={self.state.equalizer_enabled}")
            
            snapcast_status = await self.get_snapcast_status()
            services_running = snapcast_status.get("multiroom_available", False)
            
            if self.state.multiroom_enabled and not services_running:
                self.logger.info("Persisted state requires multiroom, starting snapcast services")
                await self._start_snapcast()
            elif not self.state.multiroom_enabled and services_running:
                self.logger.info("Persisted state requires direct mode, stopping snapcast services")
                await self._stop_snapcast()
            
            self._initial_detection_done = True
            self.logger.info(f"Routing initialized with persisted state: multiroom={self.state.multiroom_enabled}, equalizer={self.state.equalizer_enabled}")
            
        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            self.state.multiroom_enabled = False
            self.state.equalizer_enabled = False
            await self._update_systemd_environment()
            self._initial_detection_done = True
    
    async def set_multiroom_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Active/désactive le mode multiroom avec notification anticipée"""
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
                # NOUVEAU : Envoyer l'événement AVANT de démarrer les services
                if self.state_machine:
                    self.logger.info("📢 Broadcasting multiroom_enabling event")
                    await self.state_machine.broadcast_event("routing", "multiroom_enabling", {
                        "reason": "user_action"
                    })
                    
                    # Petit délai pour laisser le frontend réagir
                    await asyncio.sleep(0.1)
                else:
                    self.logger.warning("⚠️ state_machine not available, cannot broadcast event")
                
                success = await self._transition_to_multiroom(active_source)
            else:
                # Envoyer l'événement AVANT d'arrêter les services
                if self.state_machine:
                    self.logger.info("📢 Broadcasting multiroom_disabling event")
                    await self.state_machine.broadcast_event("routing", "multiroom_disabling", {
                        "reason": "user_action"
                    })
                    
                    # Petit délai pour laisser le frontend réagir
                    await asyncio.sleep(0.1)
                else:
                    self.logger.warning("⚠️ state_machine not available, cannot broadcast event")
                
                success = await self._transition_to_direct(active_source)
            
            if not success:
                self.state.multiroom_enabled = old_state
                await self._update_systemd_environment()
                self.logger.error(f"Failed to transition multiroom to {enabled}, reverting to {old_state}")
                return False
            
            if enabled and success:
                asyncio.create_task(self._auto_configure_multiroom())
            
            if self.snapcast_websocket_service:
                if enabled:
                    await self.snapcast_websocket_service.start_connection()
                else:
                    await self.snapcast_websocket_service.stop_connection()
            
            await self.save_current_state()
            self.logger.info(f"Multiroom state changed and saved: {enabled}")
            
            return True
            
        except Exception as e:
            self.state.multiroom_enabled = old_state
            await self._update_systemd_environment()
            self.logger.error(f"Error changing multiroom state: {e}")
            return False
    
    async def _auto_configure_multiroom(self):
        """Configure automatiquement tous les groupes sur Multiroom"""
        try:
            for _ in range(10):
                if await self.snapcast_service.is_available():
                    await self.snapcast_service.set_all_groups_to_multiroom()
                    self.logger.info("✅ Groups automatically configured to Multiroom")
                    return
                await asyncio.sleep(1)
            
            self.logger.warning("⚠️ Snapserver not available after 10 seconds")
            
        except Exception as e:
            self.logger.error(f"❌ Auto-configure multiroom failed: {e}")

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
            
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} with equalizer {'enabled' if enabled else 'disabled'}")
                    await plugin.restart()
            
            await self.save_current_state()
            self.logger.info(f"Equalizer state changed and saved: {enabled}")
            
            return True
            
        except Exception as e:
            self.state.equalizer_enabled = old_state
            await self._update_systemd_environment()
            self.logger.error(f"Error changing equalizer state: {e}")
            return False
    
    async def _update_systemd_environment(self) -> None:
        """Met à jour les variables d'environnement ALSA"""
        try:
            mode_value = "multiroom" if self.state.multiroom_enabled else "direct"
            equalizer_value = "_eq" if self.state.equalizer_enabled else ""
            
            proc1 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"MILO_MODE={mode_value}"
            )
            await proc1.communicate()
            
            proc2 = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "set-environment", f"MILO_EQUALIZER={equalizer_value}"
            )
            await proc2.communicate()
            
            os.environ["MILO_MODE"] = mode_value
            os.environ["MILO_EQUALIZER"] = equalizer_value
            
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
            "milo-go-librespot.service", "milo-roc.service", 
            "milo-bluealsa-aplay.service", self.snapserver_service, self.snapclient_service
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