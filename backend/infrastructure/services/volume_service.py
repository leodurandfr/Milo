# backend/infrastructure/services/volume_service.py
"""
Volume management service with async I/O optimization.
Handles centralized volume control for both direct and multiroom modes.
"""
import asyncio
import logging
import alsaaudio
import re
import time
from typing import Optional, Dict, Any, List

from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.volume_converter_service import VolumeConverterService
from backend.infrastructure.services.volume_config_service import VolumeConfigService
from backend.infrastructure.services.volume_storage_service import VolumeStorageService


class VolumeService:
    """System volume management service with centralized multiroom support."""

    def __init__(self, state_machine, snapcast_service, settings_service=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        # Use dependency injection or fallback to local instance
        self.settings_service = settings_service if settings_service is not None else SettingsService()
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        # Initialize extracted services
        self._config_service = VolumeConfigService(self.settings_service)
        self._converter = VolumeConverterService()
        self._storage = VolumeStorageService()

        self._precise_display_volume = 0.0
        self._multiroom_volume = 50.0
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0
        self._adjustment_counter = 0  # Counter for managing concurrent adjustments

        self._client_display_states = {}
        self._client_states_initialized = False

        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
        self.SNAPCAST_CACHE_MS = 50

        self._pending_volume_broadcast = False
        self._pending_show_bar = False
        self._broadcast_task = None
        self.BROADCAST_DELAY_MS = 30

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
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        await self._config_service.load()
        # Update converter with new limits
        self._converter.update_limits(
            self._config_service.config.alsa_min,
            self._config_service.config.alsa_max
        )

    def _save_last_volume(self, display_volume: int) -> None:
        """Save last volume in background (async write to prevent blocking)."""
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

            # Update converter with new limits
            self._converter.update_limits(
                self._config_service.config.alsa_min,
                self._config_service.config.alsa_max
            )

            # No change in limits - nothing to do
            if old_min == self.config.config.alsa_min and old_max == self.config.config.alsa_max:
                return True

            self._invalidate_all_caches()

            if not self._is_multiroom_enabled():
                old_alsa_volume = self.converter.display_to_alsa_with_old_limits(old_display_volume, old_min, old_max)
                new_display_volume = self.converter.alsa_to_display(old_alsa_volume)

                if old_alsa_volume < self.config.config.alsa_min or old_alsa_volume > self.config.config.alsa_max:
                    center_volume = (self.config.config.alsa_min + self.config.config.alsa_max) // 2
                    safe_display_volume = self.converter.alsa_to_display(center_volume)
                    await self.set_display_volume(safe_display_volume, show_bar=False)
                else:
                    self._precise_display_volume = float(new_display_volume)
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

    def _invalidate_all_caches(self) -> None:
        """Invalidate all internal caches."""
        self._client_display_states = {}
        self._client_states_initialized = False
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0

    def invalidate_client_caches(self) -> None:
        """Invalidate client caches (called when toggling multiroom)."""
        self._invalidate_all_caches()

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT
    # ============================================================================

    async def initialize_new_client_volume(self, client_id: str, client_alsa_volume: int) -> bool:
        """Initialize new client and apply current volume in multiroom mode."""
        try:
            if client_id in self._client_display_states:
                await self.sync_client_volume_from_external(client_id, client_alsa_volume)
                return True

            is_multiroom = self._is_multiroom_enabled()

            if is_multiroom:
                # Calculate target volume from existing clients or use startup volume
                if len(self._client_display_states) > 0:
                    existing_volumes = list(self._client_display_states.values())
                    target_display = sum(existing_volumes) / len(existing_volumes)
                else:
                    target_display = self._determine_startup_volume()

                target_alsa = self.converter.display_to_alsa_precise(target_display)
                target_alsa_clamped = self.converter.clamp_alsa(target_alsa)

                success = await self.snapcast_service.set_volume(client_id, target_alsa_clamped)

                if success:
                    self._client_display_states[client_id] = target_display
                    await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                        "client_id": client_id,
                        "volume": VolumeConverterService.round_half_up(target_display),
                        "muted": False,
                        "source": "new_client_multiroom_sync"
                    })
                    self.logger.debug(f"New client {client_id} synced to {VolumeConverterService.round_half_up(target_display)}%")
                    return True
                else:
                    self.logger.error(f"Failed to set volume for new client {client_id}")
                    return False
            else:
                display_volume = float(self.converter.alsa_to_display(client_alsa_volume))
                self._client_display_states[client_id] = display_volume

            return True

        except Exception as e:
            self.logger.error(f"Error initializing client {client_id}: {e}")
            return False

    async def sync_existing_client_from_snapcast(self, client_id: str, snapcast_alsa_volume: int) -> bool:
        """Synchronize existing client from Snapcast WITHOUT modifying its volume."""
        try:
            display_volume = self.converter.alsa_to_display(snapcast_alsa_volume)
            self._client_display_states[client_id] = float(display_volume)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}")
            return False

    async def _initialize_client_display_states(self) -> None:
        """Initialize client display states from Snapcast."""
        if self._client_states_initialized:
            return

        try:
            clients = await self._get_snapcast_clients_cached()
            total = 0.0
            count = 0

            for client in clients:
                client_id = client["id"]
                alsa_vol = client.get("volume", 0)
                display_vol = self.converter.alsa_to_display(alsa_vol)
                self._client_display_states[client_id] = float(display_vol)
                total += display_vol
                count += 1

            if count > 0:
                self._multiroom_volume = total / count

            self._client_states_initialized = True
        except Exception as e:
            self.logger.error(f"Error initializing states: {e}")

    def _set_client_display_volume(self, client_id: str, display_volume: float) -> None:
        """Set client display volume (internal state)."""
        clamped = self.converter.clamp_display(display_volume)
        self._client_display_states[client_id] = clamped

    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Sync client volume from external change (e.g., snapcast UI)."""
        if self._adjustment_counter > 0:
            return

        old = self._client_display_states.get(client_id, "unknown")
        new_display = float(self.converter.alsa_to_display(new_alsa_volume))

        if isinstance(old, float) and abs(old - new_display) < 0.5:
            return

        self._client_display_states[client_id] = new_display

        try:
            await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": VolumeConverterService.round_half_up(new_display),
                "muted": False,
                "source": "external_sync"
            })
        except Exception as e:
            self.logger.error(f"Error broadcasting sync: {e}")

        if self._is_multiroom_enabled():
            await self._recalculate_multiroom_volume()
            await self._schedule_broadcast(show_bar=False)

    async def _recalculate_multiroom_volume(self) -> None:
        """Recalculate multiroom volume average and save if changed."""
        try:
            clients = await self._get_snapcast_clients_cached()
            active = [c for c in clients if not c.get("muted", False)]

            if not active:
                return

            old_volume = self._multiroom_volume
            volumes = []

            for client in active:
                client_id = client["id"]
                precise = self._client_display_states.get(client_id)

                if precise is None:
                    alsa = client.get("volume", 0)
                    precise = float(self.converter.alsa_to_display(alsa))
                    self._client_display_states[client_id] = precise

                volumes.append(precise)

            self._multiroom_volume = sum(volumes) / len(volumes)

            if abs(self._multiroom_volume - old_volume) > 0.5:
                rounded = VolumeConverterService.round_half_up(self._multiroom_volume)
                self._save_last_volume(rounded)

        except Exception as e:
            self.logger.error(f"Error recalculating: {e}")

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

            if self._is_multiroom_enabled():
                await self._set_startup_volume_multiroom(startup_alsa)
            else:
                await self._set_startup_volume_direct(startup_alsa)

            self._precise_display_volume = float(startup_display)
            self._multiroom_volume = float(startup_display)
            self._last_alsa_volume = startup_alsa
            self._alsa_cache_time = time.time()

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

    def _is_multiroom_enabled(self) -> bool:
        """Check if multiroom mode is currently enabled."""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service'):
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.get('multiroom_enabled', False)
        except Exception:
            return False

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    async def get_display_volume(self) -> int:
        """Get current centralized volume (0-100%)."""
        if self._is_multiroom_enabled():
            return VolumeConverterService.round_half_up(self._multiroom_volume)
        else:
            return VolumeConverterService.round_half_up(self._precise_display_volume)

    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Set volume to specific level (0-100%) with timeout protection."""
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1
                        clamped = self.converter.clamp_display(float(display_volume))

                        if self._is_multiroom_enabled():
                            delta = int(clamped) - self._multiroom_volume
                            success = await self._apply_multiroom_delta(delta, fallback_volume=float(clamped))
                        else:
                            alsa = self.converter.display_to_alsa(int(clamped))
                            success = await self._set_amixer_volume_fast(alsa)
                            if success:
                                self._precise_display_volume = clamped

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

                        if self._is_multiroom_enabled():
                            success = await self._apply_multiroom_delta(delta)
                        else:
                            success = await self._adjust_volume_direct(delta)

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

    async def _adjust_volume_direct(self, delta: int) -> bool:
        """Adjust volume in direct mode (no multiroom)."""
        try:
            current = self._precise_display_volume
            new_precise = self.converter.clamp_display(current + delta)
            new_display = VolumeConverterService.round_half_up(new_precise)
            new_alsa = self.converter.display_to_alsa(new_display)

            success = await self._set_amixer_volume_fast(new_alsa)
            if success:
                self._precise_display_volume = new_precise
            return success
        except Exception as e:
            self.logger.error(f"Error direct adjust: {e}")
            return False

    async def _apply_multiroom_delta(self, delta: float, fallback_volume: float = None) -> bool:
        """Apply delta to all multiroom clients with precise per-client tracking.

        Args:
            delta: Volume change to apply to each client
            fallback_volume: Volume to set when no active clients (used by set_display_volume)
        """
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False

            await self._initialize_client_display_states()

            operations = []
            events = []
            new_volumes = []

            for client in clients:
                client_id = client["id"]
                current = self._client_display_states.get(client_id)

                if current is None:
                    alsa = client.get("volume", 0)
                    current = float(self.converter.alsa_to_display(alsa))
                    self._client_display_states[client_id] = current

                new_precise = self.converter.clamp_display(current + delta)
                self._client_display_states[client_id] = new_precise

                alsa = self.converter.display_to_alsa_precise(new_precise)
                alsa_clamped = self.converter.clamp_alsa(alsa)

                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": VolumeConverterService.round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })

                if not client.get("muted", False):
                    new_volumes.append(new_precise)

            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            elif fallback_volume is not None:
                self._multiroom_volume = fallback_volume

            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )

            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom"
                })

            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0

            return True

        except Exception as e:
            self.logger.error(f"Error applying multiroom delta: {e}")
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

    async def _set_amixer_volume_fast(self, alsa_volume: int) -> bool:
        """Fast amixer write (async subprocess)."""
        try:
            limited = self.converter.clamp_alsa(alsa_volume)

            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()

            self._last_alsa_volume = limited
            self._alsa_cache_time = time.time()
            return proc.returncode == 0
        except Exception:
            return False

    async def _get_snapcast_clients_cached(self):
        """Get snapcast clients with short-term caching."""
        current = time.time() * 1000

        if (current - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache

        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current
            return clients
        except Exception:
            return self._snapcast_clients_cache

    async def _mark_adjustment_done(self):
        await asyncio.sleep(0.15)
        self._adjustment_counter = max(0, self._adjustment_counter - 1)

    async def _set_startup_volume_direct(self, alsa_volume: int) -> bool:
        """Set startup volume in direct mode."""
        try:
            success = await self._set_amixer_volume_fast(alsa_volume)
            if success:
                display = self.converter.alsa_to_display(alsa_volume)
                self.logger.info(f"Direct startup: {display}%")
            return success
        except Exception as e:
            self.logger.error(f"Error startup direct: {e}")
            return False

    async def _set_startup_volume_multiroom(self, alsa_volume: int) -> bool:
        """Set startup volume in multiroom mode (all clients)."""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return True

            display = self.converter.alsa_to_display(alsa_volume)
            operations = []

            for client in clients:
                client_id = client["id"]
                operations.append((client_id, alsa_volume))
                self._set_client_display_volume(client_id, float(display))

            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )

            self._multiroom_volume = float(display)
            self._client_states_initialized = True
            self.logger.info(f"Multiroom startup: {display}%")
            return True
        except Exception as e:
            self.logger.error(f"Error startup multiroom: {e}")
            return False

    # ============================================================================
    # PUBLIC API - ADDITIONAL METHODS
    # ============================================================================

    def update_client_display_volume(self, client_id: str, display_volume: int) -> None:
        """Update client display volume (called from API routes)."""
        try:
            clamped = self.converter.clamp_display(float(display_volume))
            self._client_display_states[client_id] = clamped
            self.logger.debug(f"Updated client {client_id} display volume to {display_volume}%")
        except Exception as e:
            self.logger.error(f"Error updating client volume: {e}")

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
                clients = await self._get_snapcast_clients_cached()
                details = []

                for client in clients:
                    cid = client["id"]
                    precise = self._client_display_states.get(cid, "not_cached")

                    details.append({
                        "id": cid,
                        "name": client.get("name", "Unknown"),
                        "alsa_volume": client.get("volume", 0),
                        "display_volume_precise": precise,
                        "display_volume_rounded": VolumeConverterService.round_half_up(precise) if isinstance(precise, float) else "N/A",
                        "muted": client.get("muted", False)
                    })

                status.update({
                    "mode": "multiroom",
                    "multiroom_volume": VolumeConverterService.round_half_up(self._multiroom_volume),
                    "client_count": len(clients),
                    "clients": details
                })
            else:
                status.update({
                    "mode": "direct",
                    "precise_display_volume": self._precise_display_volume
                })

            return status
        except Exception as e:
            self.logger.error(f"Error status: {e}")
            return {"volume": 50, "mode": "error", "error": str(e)}

    async def cleanup(self) -> None:
        """Clean up and wait for pending tasks to complete."""
        try:
            # Wait for broadcast task if still running
            if self._broadcast_task and not self._broadcast_task.done():
                await self._broadcast_task

            # Wait for storage cleanup
            await self._storage.cleanup()

            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")
