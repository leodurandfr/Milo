# backend/infrastructure/services/volume_service.py
"""
Volume management service - facade/router for direct and multiroom modes.
Delegates volume operations to appropriate handler based on current mode.
"""
import asyncio
import logging
import alsaaudio
from typing import Optional, Dict, Any

from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.volume_converter_service import VolumeConverterService
from backend.infrastructure.services.volume_config_service import VolumeConfigService
from backend.infrastructure.services.volume_storage_service import VolumeStorageService
from backend.infrastructure.services.direct_volume_handler import DirectVolumeHandler
from backend.infrastructure.services.multiroom_volume_handler import MultiroomVolumeHandler


class VolumeService:
    """System volume management service with centralized multiroom support."""

    BROADCAST_DELAY_MS = 30

    def __init__(self, state_machine, snapcast_service, settings_service=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = settings_service if settings_service is not None else SettingsService()
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        # Initialize sub-services
        self._config_service = VolumeConfigService(self.settings_service)
        self._converter = VolumeConverterService()
        self._storage = VolumeStorageService()

        # Initialize mode handlers
        self._direct_handler = DirectVolumeHandler(self._converter)
        self._multiroom_handler = MultiroomVolumeHandler(
            self._converter,
            self.snapcast_service,
            self.state_machine,
        )

        # Shared state
        self._adjustment_counter = 0
        self._pending_volume_broadcast = False
        self._pending_show_bar = False
        self._broadcast_task = None

    # ============================================================================
    # EXPOSED SUB-SERVICES (for external access)
    # ============================================================================

    @property
    def converter(self) -> VolumeConverterService:
        """Access to volume converter service for ALSA/display conversions."""
        return self._converter

    @property
    def config(self) -> VolumeConfigService:
        """Access to volume configuration service."""
        return self._config_service

    @property
    def storage(self) -> VolumeStorageService:
        """Access to volume storage service for persistence."""
        return self._storage

    # ============================================================================
    # MODE HANDLER SELECTION
    # ============================================================================

    def _is_multiroom_enabled(self) -> bool:
        """Check if multiroom mode is currently enabled."""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service'):
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.get('multiroom_enabled', False)
        except Exception:
            return False

    def _get_handler(self):
        """Get the appropriate handler for current mode."""
        return self._multiroom_handler if self._is_multiroom_enabled() else self._direct_handler

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        await self._config_service.load()
        self._converter.update_limits(
            self._config_service.config.alsa_min,
            self._config_service.config.alsa_max
        )

    def _save_last_volume(self, display_volume: int) -> None:
        """Save last volume in background."""
        self._storage.save(display_volume, self.config.config.restore_last_volume)

    def _determine_startup_volume(self) -> int:
        """Determine startup volume (restored or default)."""
        return self._storage.get_startup_volume(
            self.config.config.startup_volume,
            self.config.config.restore_last_volume
        )

    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        try:
            old_display_volume = await self.get_display_volume()
            old_min, old_max = await self._config_service.reload_limits()

            self._converter.update_limits(
                self._config_service.config.alsa_min,
                self._config_service.config.alsa_max
            )

            if old_min == self.config.config.alsa_min and old_max == self.config.config.alsa_max:
                return True

            self.invalidate_client_caches()

            if not self._is_multiroom_enabled():
                old_alsa_volume = self.converter.display_to_alsa_with_old_limits(
                    old_display_volume, old_min, old_max
                )
                new_display_volume = self.converter.alsa_to_display(old_alsa_volume)

                if (old_alsa_volume < self.config.config.alsa_min or
                        old_alsa_volume > self.config.config.alsa_max):
                    center_volume = (self.config.config.alsa_min + self.config.config.alsa_max) // 2
                    safe_display_volume = self.converter.alsa_to_display(center_volume)
                    await self.set_display_volume(safe_display_volume, show_bar=False)
                else:
                    self._direct_handler.set_precise_volume(float(new_display_volume))
                    await self._schedule_broadcast(show_bar=False)

            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume limits: {e}")
            return False

    async def reload_startup_config(self) -> bool:
        """Reload startup configuration."""
        try:
            await self._config_service.load()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def reload_volume_steps_config(self) -> bool:
        """Reload volume step configuration."""
        try:
            await self._config_service.load()
            await self._schedule_broadcast(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume steps: {e}")
            return False

    async def reload_rotary_steps_config(self) -> bool:
        """Reload rotary encoder step configuration."""
        try:
            await self._config_service.load()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading rotary steps: {e}")
            return False

    def invalidate_client_caches(self) -> None:
        """Invalidate client caches (called when toggling multiroom)."""
        self._multiroom_handler.invalidate_caches()

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (Multiroom delegation)
    # ============================================================================

    async def initialize_new_client_volume(self, client_id: str, client_alsa_volume: int) -> bool:
        """Initialize new client and apply current volume in multiroom mode."""
        if self._is_multiroom_enabled():
            return await self._multiroom_handler.initialize_new_client_volume(
                client_id,
                client_alsa_volume,
                self._determine_startup_volume,
            )
        else:
            # In direct mode, just track the client state
            display_volume = float(self.converter.alsa_to_display(client_alsa_volume))
            self._multiroom_handler._client_display_states[client_id] = display_volume
            return True

    async def sync_existing_client_from_snapcast(self, client_id: str, snapcast_alsa_volume: int) -> bool:
        """Synchronize existing client from Snapcast WITHOUT modifying its volume."""
        return await self._multiroom_handler.sync_existing_client_from_snapcast(
            client_id, snapcast_alsa_volume
        )

    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Sync client volume from external change (e.g., snapcast UI)."""
        if self._is_multiroom_enabled():
            await self._multiroom_handler.sync_client_volume_from_external(
                client_id,
                new_alsa_volume,
                self._adjustment_counter,
                self._schedule_broadcast,
            )

    def update_client_display_volume(self, client_id: str, display_volume: int) -> None:
        """Update client display volume (called from API routes)."""
        self._multiroom_handler.update_client_display_volume(client_id, display_volume)

    # ============================================================================
    # SERVICE INITIALIZATION
    # ============================================================================

    async def initialize(self) -> bool:
        """Initialize volume service and set startup volume."""
        try:
            await self._load_volume_config()

            try:
                self.mixer = alsaaudio.Mixer('Digital')
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False

            startup_display = self._determine_startup_volume()
            startup_alsa = self.converter.display_to_alsa(startup_display)

            handler = self._get_handler()
            await handler.set_startup_volume(startup_alsa)

            # Sync both handlers with startup volume
            self._direct_handler.set_precise_volume(float(startup_display))
            self._multiroom_handler.set_multiroom_volume(float(startup_display))

            asyncio.create_task(self._delayed_initial_broadcast())
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False

    async def _delayed_initial_broadcast(self):
        """Send initial volume broadcast after short delay."""
        try:
            await asyncio.sleep(0.5)
            await self._schedule_broadcast(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in delayed broadcast: {e}")

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    async def get_display_volume(self) -> int:
        """Get current centralized volume (0-100%)."""
        return self._get_handler().get_display_volume()

    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Set volume to specific level (0-100%) with timeout protection."""
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1
                        clamped = self.converter.clamp_display(float(display_volume))

                        success = await self._get_handler().set_volume(clamped)

                        if success:
                            self._save_last_volume(int(clamped))
                            await self._schedule_broadcast(show_bar)

                        asyncio.create_task(self._mark_adjustment_done())
                        return success
                    except Exception as e:
                        self.logger.error(f"Error setting volume: {e}")
                        self._adjustment_counter = max(0, self._adjustment_counter - 1)
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Adjust volume by delta with timeout protection."""
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1

                        success = await self._get_handler().adjust_volume(delta)

                        if success:
                            final = await self.get_display_volume()
                            self._save_last_volume(final)
                            await self._schedule_broadcast(show_bar)

                        asyncio.create_task(self._mark_adjustment_done())
                        return success
                    except Exception as e:
                        self.logger.error(f"Error adjusting: {e}")
                        self._adjustment_counter = max(0, self._adjustment_counter - 1)
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    # ============================================================================
    # WEBSOCKET BATCHING
    # ============================================================================

    async def _schedule_broadcast(self, show_bar: bool = True) -> None:
        """Schedule volume broadcast (batched to reduce websocket traffic)."""
        self._pending_volume_broadcast = True
        self._pending_show_bar = self._pending_show_bar or show_bar

        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._execute_delayed_broadcast())

    async def _execute_delayed_broadcast(self) -> None:
        """Execute delayed volume broadcast to WebSocket clients."""
        try:
            await asyncio.sleep(self.BROADCAST_DELAY_MS / 1000)

            if not self._pending_volume_broadcast:
                return

            show_bar = self._pending_show_bar
            self._pending_volume_broadcast = False
            self._pending_show_bar = False

            volume = await self.get_display_volume()

            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self.config.config.mobile_volume_steps
            })
        except Exception as e:
            self.logger.error(f"Error broadcast: {e}")

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    async def _mark_adjustment_done(self):
        """Mark adjustment as done after delay."""
        await asyncio.sleep(0.15)
        self._adjustment_counter = max(0, self._adjustment_counter - 1)

    def get_volume_config_public(self) -> Dict[str, Any]:
        """Get current volume configuration."""
        return self._config_service.get_config_dict()

    async def get_status(self) -> dict:
        """Get complete volume service status."""
        try:
            multiroom = self._is_multiroom_enabled()
            volume = await self.get_display_volume()

            status = {
                "volume": volume,
                "multiroom_enabled": multiroom,
                "mixer_available": self.mixer is not None,
                "display_volume": True,
                "config": self.get_volume_config_public()
            }

            if multiroom:
                multiroom_status = await self._multiroom_handler.get_detailed_status()
                status.update(multiroom_status)
            else:
                status.update(self._direct_handler.get_status())

            return status
        except Exception as e:
            self.logger.error(f"Error status: {e}")
            return {"volume": 50, "mode": "error", "error": str(e)}

    async def cleanup(self) -> None:
        """Clean up and wait for pending tasks to complete."""
        try:
            if self._broadcast_task and not self._broadcast_task.done():
                await self._broadcast_task

            await self._storage.cleanup()

            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")
