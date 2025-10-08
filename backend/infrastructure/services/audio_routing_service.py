# backend/infrastructure/services/audio_routing_service.py
"""
Service de routage audio pour Milo - Version UNIFIÉE avec SystemAudioState comme source de vérité unique
"""
import os
import logging
import asyncio
from typing import Dict, Any, Callable, Optional
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """
    Service de routage audio - Version UNIFIÉE

    IMPORTANT: Ce service n'a plus son propre état. Il utilise directement
    state_machine.system_state comme source de vérité unique pour multiroom_enabled
    et equalizer_enabled. Cela élimine les risques de désynchronisation.
    """

    # Whitelist stricte pour validation des commandes
    ALLOWED_MODES = frozenset(["direct", "multiroom"])
    ALLOWED_EQUALIZER = frozenset(["", "_eq"])

    def __init__(self, get_plugin_callback: Optional[Callable] = None, settings_service=None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        # SUPPRIMÉ: self.state = AudioRoutingState()  # Plus besoin, on utilise state_machine.system_state
        self.get_plugin = get_plugin_callback
        self.settings_service = settings_service
        self._initial_detection_done = False

        self.snapcast_websocket_service = None
        self.snapcast_service = None
        self.state_machine = None

        # Services snapcast
        self.snapserver_service = "milo-snapserver-multiroom.service"
        self.snapclient_service = "milo-snapclient-multiroom.service"
    
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

    # === Propriétés d'accès à l'état unifié (state_machine.system_state) ===

    @property
    def multiroom_enabled(self) -> bool:
        """Accède à l'état multiroom depuis la source de vérité unique"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.multiroom_enabled

    @multiroom_enabled.setter
    def multiroom_enabled(self, value: bool) -> None:
        """Modifie l'état multiroom dans la source de vérité unique"""
        if self.state_machine:
            self.state_machine.system_state.multiroom_enabled = value

    @property
    def equalizer_enabled(self) -> bool:
        """Accède à l'état equalizer depuis la source de vérité unique"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.equalizer_enabled

    @equalizer_enabled.setter
    def equalizer_enabled(self, value: bool) -> None:
        """Modifie l'état equalizer dans la source de vérité unique"""
        if self.state_machine:
            self.state_machine.system_state.equalizer_enabled = value
    
    async def initialize(self) -> None:
        """Initialise l'état du service"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
    
    async def _detect_initial_state(self):
        """Initialise et détecte l'état initial"""
        try:
            self.logger.info("Initializing routing state with persistence...")

            # Charger l'état depuis SettingsService
            if self.settings_service:
                multiroom = self.settings_service.get_setting('routing.multiroom_enabled')
                equalizer = self.settings_service.get_setting('routing.equalizer_enabled')
                self.multiroom_enabled = multiroom if multiroom is not None else False
                self.equalizer_enabled = equalizer if equalizer is not None else False
                self.logger.info(f"Loaded state from settings: multiroom={self.multiroom_enabled}, equalizer={self.equalizer_enabled}")
            else:
                self.logger.warning("SettingsService not available, using defaults")
                self.multiroom_enabled = False
                self.equalizer_enabled = False

            await self._update_systemd_environment()
            self.logger.info(f"ALSA environment initialized: MULTIROOM={self.multiroom_enabled}, EQUALIZER={self.equalizer_enabled}")

            snapcast_status = await self.get_snapcast_status()
            services_running = snapcast_status.get("multiroom_available", False)

            if self.multiroom_enabled and not services_running:
                self.logger.info("Persisted state requires multiroom, starting snapcast services")
                await self._start_snapcast()
            elif not self.multiroom_enabled and services_running:
                self.logger.info("Persisted state requires direct mode, stopping snapcast services")
                await self._stop_snapcast()

            self._initial_detection_done = True
            self.logger.info(f"Routing initialized with persisted state: multiroom={self.multiroom_enabled}, equalizer={self.equalizer_enabled}")

        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            self.multiroom_enabled = False
            self.equalizer_enabled = False
            await self._update_systemd_environment()
            self._initial_detection_done = True
    
    async def set_multiroom_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Active/désactive le mode multiroom avec notification anticipée"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
        
        if self.multiroom_enabled == enabled:
            self.logger.info(f"Multiroom already {'enabled' if enabled else 'disabled'}")
            return True
        
        try:
            old_state = self.multiroom_enabled
            self.logger.info(f"Changing multiroom from {old_state} to {enabled}")
            
            self.multiroom_enabled = enabled
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
                self.multiroom_enabled = old_state
                await self._update_systemd_environment()
                self.logger.error(f"Failed to transition multiroom to {enabled}, reverting to {old_state}")
                return False
            
            if enabled and success:
                await self._auto_configure_multiroom()

            if self.snapcast_websocket_service:
                if enabled:
                    await self.snapcast_websocket_service.start_connection()
                else:
                    await self.snapcast_websocket_service.stop_connection()

            # Sauvegarder l'état via SettingsService
            if self.settings_service:
                self.settings_service.set_setting('routing.multiroom_enabled', enabled)

            self.logger.info(f"Multiroom state changed and saved: {enabled}")

            return True
            
        except Exception as e:
            self.multiroom_enabled = old_state
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
        if self.equalizer_enabled == enabled:
            self.logger.info(f"Equalizer already {'enabled' if enabled else 'disabled'}")
            return True
        
        try:
            old_state = self.equalizer_enabled
            self.logger.info(f"Changing equalizer from {old_state} to {enabled}")
            
            self.equalizer_enabled = enabled
            await self._update_systemd_environment()
            
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} with equalizer {'enabled' if enabled else 'disabled'}")
                    await plugin.restart()

            # Sauvegarder l'état via SettingsService
            if self.settings_service:
                self.settings_service.set_setting('routing.equalizer_enabled', enabled)

            self.logger.info(f"Equalizer state changed and saved: {enabled}")

            return True
            
        except Exception as e:
            self.equalizer_enabled = old_state
            await self._update_systemd_environment()
            self.logger.error(f"Error changing equalizer state: {e}")
            return False
    
    async def _update_systemd_environment(self) -> None:
        """
        Met à jour les variables d'environnement ALSA via fichier statique

        NOUVEAU: Plus de sudo runtime ! Les variables sont écrites dans
        /var/lib/milo/milo_environment qui est lu par les services systemd.
        """
        mode_value = "multiroom" if self.multiroom_enabled else "direct"
        equalizer_value = "_eq" if self.equalizer_enabled else ""

        # Validation stricte
        if mode_value not in self.ALLOWED_MODES:
            raise ValueError(f"Invalid mode value: {mode_value}. Allowed: {self.ALLOWED_MODES}")

        if equalizer_value not in self.ALLOWED_EQUALIZER:
            raise ValueError(f"Invalid equalizer value: {equalizer_value}. Allowed: {self.ALLOWED_EQUALIZER}")

        environment_file = os.path.expanduser("~/milo/var/lib/milo/milo_environment")

        # Fallback si le chemin n'existe pas (pour compatibilité)
        if not os.path.exists(os.path.dirname(environment_file)):
            environment_file = "/var/lib/milo/milo_environment"

        try:
            # Écriture atomique du fichier d'environnement
            temp_file = environment_file + ".tmp"

            with open(temp_file, 'w') as f:
                f.write("# Milo Environment Variables\n")
                f.write("# Ce fichier est modifié automatiquement par Milo backend\n")
                f.write("# Ne pas éditer manuellement\n\n")
                f.write(f"# Mode de routage audio : \"direct\" ou \"multiroom\"\n")
                f.write(f"MILO_MODE={mode_value}\n\n")
                f.write(f"# Equalizer : \"\" (désactivé) ou \"_eq\" (activé)\n")
                f.write(f"MILO_EQUALIZER={equalizer_value}\n")
                f.flush()
                os.fsync(f.fileno())

            # Renommage atomique
            os.replace(temp_file, environment_file)

            # Mise à jour locale pour compatibilité
            os.environ["MILO_MODE"] = mode_value
            os.environ["MILO_EQUALIZER"] = equalizer_value

            self.logger.info(f"✅ Updated ALSA environment file: MODE={mode_value}, EQUALIZER={equalizer_value}")

        except Exception as e:
            self.logger.error(f"Failed to update environment file: {e}")
            # Nettoyer le fichier temporaire si échec
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
            raise RuntimeError(f"Failed to update environment file: {e}")
    
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
    
    def get_state(self) -> Dict[str, bool]:
        """
        Récupère l'état actuel du routage depuis la source de vérité unique

        NOUVEAU: Retourne un dict au lieu d'AudioRoutingState (qui n'existe plus)
        """
        return {
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_enabled": self.equalizer_enabled
        }
    
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