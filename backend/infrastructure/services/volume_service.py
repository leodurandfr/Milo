# backend/infrastructure/services/volume_service.py
"""
Volume management service - CamillaDSP always active.

All volume values are in decibels (-80 to 0 dB).
ALSA is set to 100% passthrough - volume control is entirely via CamillaDSP.
"""
import asyncio
import logging
from typing import Optional, Dict, Any

from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.volume_converter_service import VolumeConverterService
from backend.infrastructure.services.volume_config_service import VolumeConfigService
from backend.infrastructure.services.volume_storage_service import VolumeStorageService
from backend.infrastructure.services.direct_volume_handler import DirectVolumeHandler
from backend.infrastructure.services.multiroom_volume_handler import MultiroomVolumeHandler


class VolumeService:
    """
    System volume management service.

    Volume is ALWAYS controlled via CamillaDSP in dB (-80 to 0).
    - Local mode: Direct CamillaDSP control
    - Multiroom mode: DSP volume propagated to all clients

    ALSA Digital mixer is set to 100% passthrough and never changed.
    """

    BROADCAST_DELAY_MS = 30

    def __init__(self, state_machine, snapcast_service, settings_service=None, camilladsp_service=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = settings_service if settings_service is not None else SettingsService()
        self._dsp_service = camilladsp_service
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        # Initialize sub-services
        self._config_service = VolumeConfigService(self.settings_service)
        self._converter = VolumeConverterService()
        self._storage = VolumeStorageService()

        # Direct handler - only for ALSA passthrough
        self._direct_handler = DirectVolumeHandler()

        # Multiroom handler - handles DSP volume for all clients
        self._multiroom_handler = MultiroomVolumeHandler(
            self._converter,
            self.snapcast_service,
            self.state_machine,
            self._dsp_service,
        )

        # State tracking (all in dB)
        self._current_volume_db: float = -30.0
        self._adjustment_counter = 0
        self._pending_volume_broadcast = False
        self._pending_show_bar = False
        self._broadcast_task = None

    # ============================================================================
    # EXPOSED SUB-SERVICES
    # ============================================================================

    @property
    def converter(self) -> VolumeConverterService:
        """Access to volume converter service."""
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
    # MODE DETECTION
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

    def _is_dsp_available(self) -> bool:
        """Check if CamillaDSP is connected and available for volume control."""
        if not self._dsp_service:
            return False
        return self._dsp_service.is_volume_control_available()

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        await self._config_service.load()
        self._converter.update_limits(
            self._config_service.config.limit_min_db,
            self._config_service.config.limit_max_db
        )

    def _save_last_volume(self, volume_db: float) -> None:
        """Save last volume in background."""
        self._storage.save(volume_db, self.config.config.restore_last_volume)

    def _determine_startup_volume_db(self) -> float:
        """Determine startup volume in dB (restored or default)."""
        return self._storage.get_startup_volume(
            self.config.config.startup_volume_db,
            self.config.config.restore_last_volume
        )

    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        try:
            current_db = self._current_volume_db
            old_min_db, old_max_db = await self._config_service.reload_limits()

            self._converter.update_limits(
                self._config_service.config.limit_min_db,
                self._config_service.config.limit_max_db
            )

            # No change, nothing to do
            if (old_min_db == self.config.config.limit_min_db and
                    old_max_db == self.config.config.limit_max_db):
                return True

            self.invalidate_client_caches()

            # Check if current volume is outside new limits
            new_min = self.config.config.limit_min_db
            new_max = self.config.config.limit_max_db

            if current_db < new_min or current_db > new_max:
                # Move to center of new range
                center_db = (new_min + new_max) / 2.0
                await self.set_volume_db(center_db, show_bar=False)
            else:
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

    async def initialize_new_client_volume(self, client_id: str) -> bool:
        """Initialize new client and apply current volume in multiroom mode."""
        if self._is_multiroom_enabled():
            return await self._multiroom_handler.initialize_new_client_volume(
                client_id,
                self._determine_startup_volume_db,
            )
        return True

    async def sync_existing_client_from_snapcast(self, client_id: str) -> bool:
        """Synchronize existing client from Snapcast."""
        return await self._multiroom_handler.sync_existing_client_from_snapcast(client_id)

    async def sync_client_volume_from_external(self, client_id: str, volume_db: float) -> None:
        """Sync client volume from external change (e.g., MultiroomModal)."""
        if self._is_multiroom_enabled():
            await self._multiroom_handler.sync_client_volume_from_external(
                client_id,
                volume_db,
                self._adjustment_counter,
                self._schedule_broadcast,
            )

    async def sync_all_clients_from_dsp(self) -> bool:
        """
        Sync all client volumes from their DSP state.
        Called when multiroom is enabled to initialize volume offsets.
        """
        if self._is_multiroom_enabled():
            return await self._multiroom_handler.sync_all_clients_from_dsp()
        return True

    async def push_volume_to_all_clients(self) -> bool:
        """
        Push current local volume to all multiroom clients.
        Called when multiroom is activated to ensure uniform volume.
        """
        try:
            current_volume = self._current_volume_db
            self.logger.info(f"Pushing local volume {current_volume:.1f} dB to all clients")
            return await self._multiroom_handler.push_volume_to_all_clients(current_volume)
        except Exception as e:
            self.logger.error(f"Error pushing volume to clients: {e}")
            return False

    def update_client_volume_db(self, client_id: str, volume_db: float) -> None:
        """Update client volume in dB (called from API routes)."""
        self._multiroom_handler.update_client_volume_db(client_id, volume_db)

    # ============================================================================
    # SERVICE INITIALIZATION
    # ============================================================================

    async def initialize(self) -> bool:
        """
        Initialize volume service.

        Sets ALSA to 100% passthrough and initializes DSP volume.
        """
        try:
            await self._load_volume_config()

            # Set ALSA to 100% passthrough - permanent
            await self._direct_handler.set_alsa_passthrough()
            self.logger.info("ALSA set to 100% passthrough mode")

            # Determine startup volume from settings/storage
            startup_db = self._determine_startup_volume_db()
            self._current_volume_db = startup_db

            # Initialize multiroom handler with startup volume
            self._multiroom_handler.set_global_volume_db(startup_db)

            self.logger.info(f"Volume initialized: {startup_db:.1f} dB")
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
    # PUBLIC API (all in dB)
    # ============================================================================

    async def get_volume_db(self) -> float:
        """Get current volume in dB."""
        return self._current_volume_db

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        """
        Set volume to specific level in dB.

        Args:
            volume_db: Target volume in dB (-80 to 0)
            show_bar: Whether to show volume bar in UI

        Returns:
            True if successful
        """
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1
                        clamped_db = self._converter.clamp_db(volume_db)

                        # Check DSP availability
                        if not self._is_dsp_available():
                            self.logger.warning("DSP not available, volume change blocked")
                            await self.state_machine.broadcast_event("volume", "volume_error", {
                                "error": "CamillaDSP not available",
                                "dsp_available": False
                            })
                            self._adjustment_counter = max(0, self._adjustment_counter - 1)
                            return False

                        # Apply volume
                        success = await self._apply_volume_db(clamped_db)

                        if success:
                            self._current_volume_db = clamped_db
                            self._save_last_volume(clamped_db)
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

    async def _apply_volume_db(self, volume_db: float) -> bool:
        """Apply volume to DSP (local or multiroom)."""
        try:
            if self._is_multiroom_enabled():
                # MULTIROOM: Calculate delta and apply to all clients
                old_db = self._multiroom_handler.get_global_volume_db()
                delta_db = volume_db - old_db
                return await self._multiroom_handler.apply_delta_db(delta_db)
            else:
                # LOCAL: Direct CamillaDSP control
                return await self._dsp_service.set_volume(volume_db)

        except Exception as e:
            self.logger.error(f"Error applying volume: {e}")
            return False

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """
        Adjust volume by delta in dB.

        Args:
            delta_db: Volume change in dB (positive = louder, negative = quieter)
            show_bar: Whether to show volume bar in UI

        Returns:
            True if successful
        """
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1

                        # Check DSP availability
                        if not self._is_dsp_available():
                            self.logger.warning("DSP not available, volume change blocked")
                            await self.state_machine.broadcast_event("volume", "volume_error", {
                                "error": "CamillaDSP not available",
                                "dsp_available": False
                            })
                            self._adjustment_counter = max(0, self._adjustment_counter - 1)
                            return False

                        # Apply delta
                        success = await self._apply_delta_db(delta_db)

                        if success:
                            self._save_last_volume(self._current_volume_db)
                            await self._schedule_broadcast(show_bar)

                        asyncio.create_task(self._mark_adjustment_done())
                        return success
                    except Exception as e:
                        self.logger.error(f"Error adjusting volume: {e}")
                        self._adjustment_counter = max(0, self._adjustment_counter - 1)
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    async def _apply_delta_db(self, delta_db: float) -> bool:
        """Apply volume delta in dB."""
        try:
            if self._is_multiroom_enabled():
                # MULTIROOM: Apply delta to all clients
                success = await self._multiroom_handler.apply_delta_db(delta_db)
                if success:
                    new_db = self._converter.clamp_db(self._current_volume_db + delta_db)
                    self._current_volume_db = new_db
            else:
                # LOCAL: Apply delta to CamillaDSP
                new_db = self._converter.clamp_db(self._current_volume_db + delta_db)
                success = await self._dsp_service.set_volume(new_db)
                if success:
                    self._current_volume_db = new_db

            return success

        except Exception as e:
            self.logger.error(f"Error applying delta: {e}")
            return False

    # ============================================================================
    # WEBSOCKET BROADCASTING
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

            # In multiroom mode, display average volume of all clients
            multiroom = self._is_multiroom_enabled()
            if multiroom:
                display_volume = self._multiroom_handler.get_average_volume_db()
            else:
                display_volume = self._current_volume_db

            event_data = {
                "volume_db": display_volume,
                "show_bar": show_bar,
                "multiroom_enabled": multiroom,
                "step_mobile_db": self.config.config.step_mobile_db
            }

            # Include client volumes for real-time slider updates
            if multiroom:
                event_data["client_volumes"] = dict(self._multiroom_handler._client_volume_db)

            await self.state_machine.broadcast_event("volume", "volume_changed", event_data)
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

            status = {
                "volume_db": self._current_volume_db,
                "multiroom_enabled": multiroom,
                "dsp_available": self._is_dsp_available(),
                "config": self.get_volume_config_public()
            }

            if multiroom:
                multiroom_status = await self._multiroom_handler.get_detailed_status()
                status.update(multiroom_status)

            return status
        except Exception as e:
            self.logger.error(f"Error status: {e}")
            return {"volume_db": -30.0, "error": str(e)}

    async def cleanup(self) -> None:
        """Clean up and wait for pending tasks to complete."""
        try:
            if self._broadcast_task and not self._broadcast_task.done():
                await self._broadcast_task

            await self._storage.cleanup()

            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")
