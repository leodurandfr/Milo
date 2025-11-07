# backend/infrastructure/plugins/librespot/plugin.py
"""
Librespot plugin with 0 = disabled support for auto_disconnect
"""
import os
import yaml
import asyncio
import aiohttp
import json
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import WebSocketManager

class LibrespotPlugin(UnifiedAudioPlugin):
    """Spotify plugin via go-librespot with 0 = disabled support"""
    
    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__("librespot", state_machine)
        self.config = config
        self.service_name = config.get("service_name")
        self.config_path = os.path.expanduser(config.get("config_path"))
        self.settings_service = settings_service
        
        # Auto-disconnect configuration (depuis SettingsService)
        self.auto_disconnect_enabled = True
        self.pause_disconnect_delay = 10.0
        
        # État
        self.api_url = None
        self.ws_url = None
        self.session = None
        self._metadata = {}
        self._is_playing = False
        self._device_connected = False
        self._ws_connected = False
        
        # Auto-disconnect timer after pause
        self._pause_disconnect_timer = None
        
        # Create WebSocket manager
        self.ws_manager = WebSocketManager(self.logger)
    
    async def _do_initialize(self) -> bool:
        """Initialization with config reading from SettingsService"""
        try:
            # Check service existence
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {} not found")
            
            # Read configuration to get current device
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            self._current_device = config.get('audio_device', 'milo_spotify')
            
            server = config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)
            
            self.api_url = f"http://{addr}:{port}"
            self.ws_url = f"ws://{addr}:{port}/events"
            
            # Load config from SettingsService
            await self._load_settings_config()
            
            self.logger.info(f"Spotify disconnect config: enabled={self.auto_disconnect_enabled}, delay={self.pause_disconnect_delay}s")
            
            return True
        except Exception as e:
            self.logger.error(f"Initialization error: {e}")
            return False
    
    async def _load_settings_config(self):
        """Loads configuration from SettingsService avec support 0 = désactivé"""
        if self.settings_service:
            try:
                spotify_delay = await self.settings_service.get_setting('spotify.auto_disconnect_delay')
                if spotify_delay is not None:
                    # MODIFIÉ : 0 = disabled support
                    if spotify_delay == 0.0:
                        self.auto_disconnect_enabled = False
                        self.pause_disconnect_delay = 10.0  # Default value for display
                        self.logger.info("Spotify auto-disconnect DISABLED (delay = 0)")
                    else:
                        self.auto_disconnect_enabled = True
                        self.pause_disconnect_delay = float(spotify_delay)
                        self.logger.info(f"Spotify auto-disconnect ENABLED: delay={self.pause_disconnect_delay}s")
                else:
                    self.logger.info("Using default Spotify config (settings not found)")
            except Exception as e:
                self.logger.error(f"Error loading Spotify settings: {e}")
    
    async def _do_start(self) -> bool:
        """Specific go-librespot startup"""
        try:
            # Start service
            if not await self.control_service(self.service_name, "start"):
                return False
            
            # Reset state at startup
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._cancel_pause_timer()
            
            # Create HTTP session
            self.session = aiohttp.ClientSession()
            
            # Start WebSocket connection
            await self._start_websocket()
            
            return True
        except Exception as e:
            self.logger.error(f"Start error: {e}")
            return False
    
    async def restart(self) -> bool:
        """Restarts go-librespot avec reset d'état et reconnexion WebSocket"""
        try:
            self.logger.info("Restarting librespot service")

            # Cancel disconnect timer
            self._cancel_pause_timer()

            # Reset état avant redémarrage
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}

            # Stop old connection WebSocket
            if self.ws_manager:
                await self.ws_manager.stop()

            # Restart service
            success = await self.control_service(self.service_name, "restart")

            if not success:
                self.logger.error(f"Failed to restart service {self.service_name}")
                return False

            # Wait for new service to be ready
            await asyncio.sleep(0.5)

            # WebSocket reconnection to new process
            if self.session:
                await self._start_websocket()
            else:
                self.logger.warning("No session available, skipping WebSocket reconnection")

            # Notify change d'état APRÈS le restart (sans attendre pour éviter deadlock)
            async def notify_ready_state():
                await asyncio.sleep(0.1)
                await self.notify_state_change(PluginState.READY, {"device_connected": False})

            asyncio.create_task(notify_ready_state())

            self.logger.info("Librespot restart completed")
            return True
        except Exception as e:
            self.logger.error(f"Error restarting librespot: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stops the plugin"""
        try:
            # Cancel disconnect timer
            self._cancel_pause_timer()

            # Stop WebSocket
            await self.ws_manager.stop()

            # Close HTTP session
            if self.session:
                await self.session.close()
                self.session = None

            # Stop service
            await self.control_service(self.service_name, "stop")

            # Reset state
            self._ws_connected = False
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}

            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Stop error: {e}")
            return False

    async def set_auto_disconnect_config(self, enabled: bool, delay: float = None, save_to_settings: bool = True) -> bool:
        """Configures auto-disconnect avec support 0 = désactivé"""
        old_enabled = self.auto_disconnect_enabled
        old_delay = self.pause_disconnect_delay
        
        # MODIFIÉ : Handle delay = 0 as disabled
        if delay is not None and delay == 0:
            self.auto_disconnect_enabled = False
            self.pause_disconnect_delay = 10.0  # Default value for display
            self.logger.info("Auto-disconnect DISABLED (delay = 0)")
        elif delay is not None:
            self.auto_disconnect_enabled = enabled
            self.pause_disconnect_delay = max(1.0, delay)
            self.logger.info(f"Auto-disconnect configured: enabled={enabled}, delay={self.pause_disconnect_delay}s")
        else:
            self.auto_disconnect_enabled = enabled
            self.logger.info(f"Auto-disconnect enabled state changed: {enabled}")
        
        # Save to SettingsService si demandé
        if save_to_settings and self.settings_service:
            try:
                # Save 0 if disabled, sinon la vraie valeur
                save_value = 0.0 if not self.auto_disconnect_enabled else self.pause_disconnect_delay
                success = await self.settings_service.set_setting('spotify.auto_disconnect_delay', save_value)
                if not success:
                    self.logger.error("Failed to save Spotify config to settings")
                    # Restore old values
                    self.auto_disconnect_enabled = old_enabled
                    self.pause_disconnect_delay = old_delay
                    return False
            except Exception as e:
                self.logger.error(f"Error saving Spotify config: {e}")
                self.auto_disconnect_enabled = old_enabled
                self.pause_disconnect_delay = old_delay
                return False
        
        # Management of running timer
        if (self._pause_disconnect_timer and 
            not self._pause_disconnect_timer.done() and 
            self._device_connected and 
            not self._is_playing):
            
            self._cancel_pause_timer()
            
            # Restart timer only if enabled
            if self.auto_disconnect_enabled:
                self.logger.info(f"Restarting pause timer with new delay: {self.pause_disconnect_delay}s")
                self._start_pause_timer()
            else:
                self.logger.info("Timer cancelled (auto-disconnect disabled)")
        
        # Cancel timer if disabling complètement
        elif not self.auto_disconnect_enabled and self._pause_disconnect_timer:
            self._cancel_pause_timer()
            self.logger.info("Timer cancelled (auto-disconnect disabled)")
        
        return True
    
    def get_auto_disconnect_config(self) -> Dict[str, Any]:
        """Gets configuration de déconnexion automatique"""
        return {
            "enabled": self.auto_disconnect_enabled,
            "delay": self.pause_disconnect_delay,
            "timer_active": self._pause_disconnect_timer is not None and not self._pause_disconnect_timer.done()
        }
    
    def _cancel_pause_timer(self) -> None:
        """Cancels auto-disconnect timer"""
        if self._pause_disconnect_timer:
            self._pause_disconnect_timer.cancel()
            self._pause_disconnect_timer = None
            self.logger.debug("Pause disconnect timer cancelled")
    
    def _start_pause_timer(self) -> None:
        """Starts auto-disconnect timer après pause"""
        if not self.auto_disconnect_enabled:
            self.logger.debug("Auto-disconnect disabled, skipping timer")
            return

        # Cancel existing timer s'il y en a un
        self._cancel_pause_timer()

        # Create new timer
        async def disconnect_after_pause():
            try:
                await asyncio.sleep(self.pause_disconnect_delay)
                self.logger.info(f"Auto-disconnecting librespot after {self.pause_disconnect_delay}s of pause")
                await self.restart()
            except asyncio.CancelledError:
                self.logger.debug("Pause disconnect timer was cancelled")
            except Exception as e:
                self.logger.error(f"Error in auto-disconnect: {e}")

        self._pause_disconnect_timer = asyncio.create_task(disconnect_after_pause())
        self.logger.debug(f"Started pause disconnect timer ({self.pause_disconnect_delay}s)")
    
    async def _start_websocket(self) -> None:
        """Starts WebSocket connection"""
        async def connect_func():
            try:
                await self._refresh_metadata()
                return True
            except Exception as e:
                self.logger.error(f"Connection error: {e}")
                return False
                
        async def process_func():
            try:
                async with self.session.ws_connect(self.ws_url) as ws:
                    self._ws_connected = True
                    self.logger.info(f"WebSocket connected à {self.ws_url}")
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_event(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
            finally:
                self._ws_connected = False
        
        await self.ws_manager.start(connect_func, process_func)
    
    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Processes a WebSocket event"""
        event_type = event.get("type")
        data = event.get("data", {})
        
        handlers = {
            "active": self._handle_active_state,
            "inactive": self._handle_inactive_state,
            "playing": lambda: self._handle_playback_state(True),
            "paused": lambda: self._handle_playback_state(False),
            "metadata": self._handle_metadata_update,
            "seek": lambda: self._handle_seek_update(data)
        }
        
        handler = handlers.get(event_type)
        if handler:
            await handler()
    
    async def _handle_active_state(self):
        """Processes event 'device active'"""
        self._device_connected = True
        # Enrich with current position
        await self._refresh_metadata()
        await self.notify_state_change(PluginState.CONNECTED, self._metadata)
    
    async def _handle_inactive_state(self):
        """Processes event 'device inactive'"""
        # Cancel disconnect timer car l'appareil est déjà inactif
        self._cancel_pause_timer()
        
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        await self.notify_state_change(PluginState.READY, {"device_connected": False})
    
    async def _handle_playback_state(self, is_playing):
        """Traite les événements de lecture/pause avec gestion du timer de déconnexion"""
        self._is_playing = is_playing
        self._device_connected = True
        
        # Gestion du timer de déconnexion automatique
        if is_playing:
            # Music playing → annuler le timer
            self._cancel_pause_timer()
        else:
            # Music paused → démarrer le timer de déconnexion (si activé)
            self._start_pause_timer()
        
        # Enrichir avec position actuelle
        await self._refresh_metadata()
        self._metadata["is_playing"] = is_playing
        
        await self.notify_state_change(PluginState.CONNECTED, self._metadata)
    
    async def _handle_metadata_update(self):
        """Processes event de mise à jour des métadonnées"""
        await self._refresh_metadata()
        await self.notify_state_change(PluginState.CONNECTED, self._metadata)
    
    async def _handle_seek_update(self, data):
        """Processes event de recherche dans la piste"""
        if self._metadata:
            self._metadata["position"] = data.get("position", 0)
            
        await self.notify_state_change(PluginState.CONNECTED, {
            **self._metadata,
            "position": data.get("position", 0)
        })
    
    async def _refresh_metadata(self) -> bool:
        """Refreshes metadata from REST API"""
        if not self.session:
            return False
            
        try:
            async with self.session.get(f"{self.api_url}/status") as resp:
                if resp.status != 200:
                    return False
                    
                data = await resp.json()
                
                # Update connection state
                self._device_connected = bool(data.get("track"))
                self._is_playing = not data.get("paused", True)
                
                # Extract metadata
                if data.get("track"):
                    track = data["track"]
                    self._metadata = {
                        "title": track.get("name"),
                        "artist": ", ".join(track.get("artist_names", [])),
                        "album": track.get("album_name"),
                        "album_art_url": track.get("album_cover_url"),
                        "duration": track.get("duration", 0),
                        "position": track.get("position", 0),
                        "uri": track.get("uri"),
                        "is_playing": self._is_playing
                    }
                else:
                    self._metadata = {}
                
                return True
        except Exception as e:
            self.logger.error(f"Metadata refresh error: {e}")
            return False
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Processes commands"""
        if command == "restart_service":
            return await self._restart_service()
            
        elif command == "refresh_metadata":
            success = await self._refresh_metadata()
            return self.format_response(
                success=success,
                message="Metadata refreshed" if success else "Refresh failed",
                metadata=self._metadata
            )
            
        elif command == "seek" and "position_ms" in data:
            return await self._send_command("seek", {"position": data["position_ms"]})
            
        elif command in ["play", "pause", "resume", "playpause"]:
            return await self._send_command(command)
            
        elif command in ["next", "prev"]:
            payload = {"uri": data.get("uri")} if data.get("uri") else {}
            return await self._send_command(command, payload)
            
        return self.format_response(False, error=f"Unsupported command: {command}")
    
    async def _restart_service(self) -> Dict[str, Any]:
        """Restarts service"""
        success = await self.control_service(self.service_name, "restart")
        return self.format_response(
            success=success,
            message="Service restarted" if success else "Restart failed"
        )
    
    async def _send_command(self, command: str, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """Sends command to REST API"""
        if not self.session:
            return self.format_response(False, error="Inactive session")
            
        try:
            async with self.session.post(
                f"{self.api_url}/player/{command}", 
                json=payload or {}
            ) as resp:
                return self.format_response(resp.status == 200)
        except Exception as e:
            return self.format_response(False, error=str(e))
    
    async def get_status(self) -> Dict[str, Any]:
        """Gets current plugin state"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            
            return {
                "device_connected": self._device_connected,
                "ws_connected": self._ws_connected,
                "is_playing": self._is_playing,
                "metadata": self._metadata,
                "service_active": service_status.get("active", False),
                "service_state": service_status.get("state", "unknown"),
                "current_device": self._current_device,
                "auto_disconnect_config": self.get_auto_disconnect_config()
            }
        except Exception as e:
            self.logger.error(f"Status error: {e}")
            return {
                "device_connected": False,
                "ws_connected": False,
                "metadata": {},
                "is_playing": False,
                "current_device": self._current_device,
                "auto_disconnect_config": {
                    "enabled": self.auto_disconnect_enabled,
                    "delay": self.pause_disconnect_delay,
                    "timer_active": False
                },
                "error": str(e)
            }
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state for WebSockets"""
        if self.session:
            await self._refresh_metadata()
        
        return await self.get_status()