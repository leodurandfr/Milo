# backend/infrastructure/services/volume_service.py
"""
Volume management service with async I/O optimization.
Handles centralized volume control for both direct and multiroom modes.
"""
import asyncio
import logging
import alsaaudio
import re
import json
import os
import aiofiles
from typing import Optional, Dict, Any, List
import time
from pathlib import Path
from backend.infrastructure.services.settings_service import SettingsService

class VolumeService:
    """System volume management service with centralized multiroom support."""
    
    LAST_VOLUME_FILE = Path("/var/lib/milo/last_volume.json")
    
    def __init__(self, state_machine, snapcast_service, settings_service=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        # Use dependency injection or fallback to local instance
        self.settings_service = settings_service if settings_service is not None else SettingsService()
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        self._alsa_min_volume = 0
        self._alsa_max_volume = 65
        self._default_startup_display_volume = 37
        self._restore_last_volume = False
        self._mobile_volume_steps = 5
        self._rotary_volume_steps = 2

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

        self._save_volume_task = None

        self._ensure_data_directory()
    
    def _ensure_data_directory(self) -> None:
        """Create data directory if it doesn't exist."""
        try:
            self.LAST_VOLUME_FILE.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create data directory: {e}")
    
    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}
            self._alsa_min_volume = volume_config.get("alsa_min", 0)
            self._alsa_max_volume = volume_config.get("alsa_max", 65)
            self._default_startup_display_volume = volume_config.get("startup_volume", 37)
            self._restore_last_volume = volume_config.get("restore_last_volume", False)
            self._mobile_volume_steps = volume_config.get("mobile_volume_steps", 5)
            self._rotary_volume_steps = volume_config.get("rotary_volume_steps", 2)
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
    
    def _save_last_volume(self, display_volume: int) -> None:
        """Save last volume in background (async write to prevent blocking)."""
        if not self._restore_last_volume:
            return

        async def save_async():
            try:
                data = {"last_volume": display_volume, "timestamp": time.time()}
                temp_file = self.LAST_VOLUME_FILE.with_suffix('.tmp')
                async with aiofiles.open(temp_file, 'w') as f:
                    content = json.dumps(data)
                    await f.write(content)
                    await f.flush()
                temp_file.replace(self.LAST_VOLUME_FILE)
            except Exception as e:
                self.logger.error(f"Failed to save last volume: {e}")

        # Keep reference to prevent task from being garbage collected
        self._save_volume_task = asyncio.create_task(save_async())
    
    def _load_last_volume(self) -> Optional[int]:
        """Load last saved volume from persistent storage."""
        try:
            if not self.LAST_VOLUME_FILE.exists():
                return None
            
            with open(self.LAST_VOLUME_FILE, 'r') as f:
                data = json.load(f)
            
            last_volume = data.get('last_volume')
            timestamp = data.get('timestamp', 0)
            age_days = (time.time() - timestamp) / (24 * 3600)
            
            if age_days > 7 or not (0 <= last_volume <= 100):
                return None
            
            self.logger.info(f"Restored last volume: {last_volume}%")
            return last_volume
        except Exception:
            return None
    
    def _determine_startup_volume(self) -> int:
        """Determine startup volume (restored or default)."""
        if self._restore_last_volume:
            last_volume = self._load_last_volume()
            if last_volume is not None:
                return last_volume
        return self._default_startup_display_volume
    
    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        try:
            old_display_volume = await self.get_display_volume()
            old_alsa_min = self._alsa_min_volume
            old_alsa_max = self._alsa_max_volume

            await self._load_volume_config()

            if old_alsa_min == self._alsa_min_volume and old_alsa_max == self._alsa_max_volume:
                return True

            self._invalidate_all_caches()

            if not self._is_multiroom_enabled():
                old_alsa_volume = self._display_to_alsa_old_limits(old_display_volume, old_alsa_min, old_alsa_max)
                new_display_volume = self._alsa_to_display(old_alsa_volume)

                if old_alsa_volume < self._alsa_min_volume or old_alsa_volume > self._alsa_max_volume:
                    center_volume = (self._alsa_min_volume + self._alsa_max_volume) // 2
                    safe_display_volume = self._alsa_to_display(center_volume)
                    await self.set_display_volume(safe_display_volume, show_bar=False)
                else:
                    self._precise_display_volume = float(new_display_volume)
                    await self._schedule_broadcast(show_bar=False)

            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume limits: {e}")
            return False

    async def _reload_volume_setting(self, setting_key: str, default_value: Any) -> Any:
        """Helper to reload a specific volume config setting."""
        self.settings_service.invalidate_cache()
        volume_config = await self.settings_service.get_setting('volume') or {}
        return volume_config.get(setting_key, default_value)

    async def reload_startup_config(self) -> bool:
        """Reload startup configuration."""
        try:
            self._default_startup_display_volume = await self._reload_volume_setting("startup_volume", 37)
            self._restore_last_volume = await self._reload_volume_setting("restore_last_volume", False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def reload_volume_steps_config(self) -> bool:
        """Reload volume step configuration."""
        try:
            self._mobile_volume_steps = await self._reload_volume_setting("mobile_volume_steps", 5)
            await self._schedule_broadcast(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume steps: {e}")
            return False

    async def reload_rotary_steps_config(self) -> bool:
        """Reload rotary encoder step configuration."""
        try:
            self._rotary_volume_steps = await self._reload_volume_setting("rotary_volume_steps", 2)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading rotary steps: {e}")
            return False

    def _display_to_alsa_old_limits(self, display_volume: int, old_min: int, old_max: int) -> int:
        """Convert display volume using old ALSA limits (for limit change migration)."""
        old_alsa_range = old_max - old_min
        return round((display_volume / 100) * old_alsa_range) + old_min
    
    # ============================================================================
    # VOLUME CONVERSIONS
    # ============================================================================

    def _alsa_to_display(self, alsa_volume: int) -> int:
        """Convert ALSA raw volume to display percentage (0-100%)."""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        normalized = alsa_volume - self._alsa_min_volume
        return round((normalized / alsa_range) * 100)

    def _display_to_alsa(self, display_volume: int) -> int:
        """Convert display percentage to ALSA raw volume."""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100) * alsa_range) + self._alsa_min_volume
    
    def _display_to_alsa_precise(self, display_volume: float) -> int:
        """Convert precise display volume (float) to ALSA raw volume."""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100.0) * alsa_range) + self._alsa_min_volume
    
    def _round_half_up(self, value: float) -> int:
        """Standard mathematical rounding (half-up)."""
        return int(value + 0.5)

    def _clamp_display_volume(self, display_volume: float) -> float:
        """Clamp display volume to valid range (0-100%)."""
        return max(0.0, min(100.0, display_volume))

    def _clamp_alsa_volume(self, alsa_volume: int) -> int:
        """Clamp ALSA volume to configured min/max range."""
        return max(self._alsa_min_volume, min(self._alsa_max_volume, alsa_volume))
    
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
            self.logger.info(f"🔵 initialize_new_client_volume: {client_id}, alsa={client_alsa_volume}")
            
            self.logger.info(f"  Current _client_display_states: {list(self._client_display_states.keys())}")
            
            if client_id in self._client_display_states:
                self.logger.info(f"⚪ Client {client_id} already known")
                await self.sync_client_volume_from_external(client_id, client_alsa_volume)
                return True
            
            self.logger.info(f"🟢 Client {client_id} is NEW")

            is_multiroom = self._is_multiroom_enabled()
            self.logger.info(f"  is_multiroom: {is_multiroom}")
            self.logger.info(f"  _multiroom_volume: {self._multiroom_volume}")

            if is_multiroom:
                # Calculate target volume from existing clients only (exclude the new one)
                if len(self._client_display_states) > 0:
                    existing_volumes = list(self._client_display_states.values())
                    target_display = sum(existing_volumes) / len(existing_volumes)
                    self.logger.info(f"  Calculated average from {len(existing_volumes)} existing clients: {target_display}%")
                else:
                    # No existing clients - use configured startup volume
                    target_display = self._determine_startup_volume()
                    self.logger.info(f"  No existing clients, using startup volume: {target_display}%")

                self.logger.info(f"  target_display: {target_display}")
                
                target_alsa = self._display_to_alsa_precise(target_display)
                self.logger.info(f"  target_alsa (before clamp): {target_alsa}")
                
                target_alsa_clamped = self._clamp_alsa_volume(target_alsa)
                self.logger.info(f"  target_alsa_clamped: {target_alsa_clamped}")
                
                self.logger.info(
                    f"🎯 Applying multiroom volume {self._round_half_up(target_display)}% "
                    f"(was {self._alsa_to_display(client_alsa_volume)}%)"
                )
                
                self.logger.info(f"  Calling snapcast_service.set_volume...")
                success = await self.snapcast_service.set_volume(client_id, target_alsa_clamped)
                self.logger.info(f"  snapcast set_volume returned: {success}")
                
                if success:
                    self._client_display_states[client_id] = target_display
                    
                    await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                        "client_id": client_id,
                        "volume": self._round_half_up(target_display),
                        "muted": False,
                        "source": "new_client_multiroom_sync"
                    })
                    
                    self.logger.info(f"✅ Client {client_id} synced")
                    return True
                else:
                    self.logger.error(f"❌ Failed to set volume for {client_id}")
                    return False
            else:
                self.logger.info(f"  Multiroom NOT enabled, using direct mode")
                display_volume = float(self._alsa_to_display(client_alsa_volume))
                self._client_display_states[client_id] = display_volume
                self.logger.info(f"⚪ Direct mode, keeping {self._round_half_up(display_volume)}%")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error initializing client: {e}", exc_info=True)
            return False

    async def sync_existing_client_from_snapcast(self, client_id: str, snapcast_alsa_volume: int) -> bool:
        """Synchronize existing client from Snapcast WITHOUT modifying its volume."""
        try:
            display_volume = self._alsa_to_display(snapcast_alsa_volume)
            self._client_display_states[client_id] = float(display_volume)
            self.logger.info(f"✅ Synced existing client {client_id}: {self._round_half_up(display_volume)}% (from Snapcast)")
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
                display_vol = self._alsa_to_display(alsa_vol)
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
        clamped = self._clamp_display_volume(display_volume)
        self._client_display_states[client_id] = clamped
    
    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Sync client volume from external change (e.g., snapcast UI)."""
        if self._adjustment_counter > 0:
            return
        
        old = self._client_display_states.get(client_id, "unknown")
        new_display = float(self._alsa_to_display(new_alsa_volume))
        
        if isinstance(old, float) and abs(old - new_display) < 0.5:
            return
        
        self._client_display_states[client_id] = new_display
        
        try:
            await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": self._round_half_up(new_display),
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
                    precise = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = precise
                
                volumes.append(precise)
            
            self._multiroom_volume = sum(volumes) / len(volumes)
            
            if abs(self._multiroom_volume - old_volume) > 0.5:
                rounded = self._round_half_up(self._multiroom_volume)
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
            startup_alsa = self._display_to_alsa(startup_display)

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
            return self._round_half_up(self._multiroom_volume)
        else:
            return self._round_half_up(self._precise_display_volume)
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Set volume to specific level (0-100%) with timeout protection."""
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1
                        clamped = self._clamp_display_volume(float(display_volume))

                        if self._is_multiroom_enabled():
                            success = await self._set_multiroom_volume_centralized(int(clamped))
                        else:
                            alsa = self._display_to_alsa(int(clamped))
                            success = await self._apply_alsa_volume_direct(alsa)
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
                            success = await self._adjust_multiroom_volume_centralized(delta)
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
            new_precise = self._clamp_display_volume(current + delta)
            new_display = self._round_half_up(new_precise)
            new_alsa = self._display_to_alsa(new_display)
            
            success = await self._apply_alsa_volume_direct(new_alsa)
            if success:
                self._precise_display_volume = new_precise
            return success
        except Exception as e:
            self.logger.error(f"Error direct adjust: {e}")
            return False
    
    async def _adjust_multiroom_volume_centralized(self, delta: int) -> bool:
        """Adjust multiroom volume with precise per-client tracking."""
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
                    current = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = current
                
                new_precise = self._clamp_display_volume(current + delta)
                self._client_display_states[client_id] = new_precise
                
                alsa = self._display_to_alsa_precise(new_precise)
                alsa_clamped = self._clamp_alsa_volume(alsa)
                
                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": self._round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })
                
                if not client.get("muted", False):
                    new_volumes.append(new_precise)
            
            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )
            
            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom_precise"
                })
            
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error multiroom adjust: {e}")
            return False
    
    async def _set_multiroom_volume_centralized(self, target: int) -> bool:
        """Set multiroom volume with uniform delta across all clients."""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            await self._initialize_client_display_states()
            old = self._multiroom_volume
            delta = target - old
            
            operations = []
            events = []
            new_volumes = []
            
            for client in clients:
                client_id = client["id"]
                current = self._client_display_states.get(client_id)
                
                if current is None:
                    alsa = client.get("volume", 0)
                    current = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = current
                
                new_precise = self._clamp_display_volume(current + delta)
                self._client_display_states[client_id] = new_precise
                
                alsa = self._display_to_alsa_precise(new_precise)
                alsa_clamped = self._clamp_alsa_volume(alsa)
                
                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": self._round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })
                
                if not client.get("muted", False):
                    new_volumes.append(new_precise)
            
            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            else:
                self._multiroom_volume = float(target)
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )
            
            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom_uniform"
                })
            
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error set multiroom: {e}")
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
                "mobile_steps": self._mobile_volume_steps
            })
        except Exception as e:
            self.logger.error(f"Error broadcast: {e}")
    
    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    async def increase_display_volume(self, delta: int = None) -> bool:
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(step)

    async def decrease_display_volume(self, delta: int = None) -> bool:
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(-step)
    
    async def _apply_alsa_volume_direct(self, alsa_volume: int) -> bool:
        """Apply ALSA volume in direct mode (uses amixer)."""
        try:
            return await self._set_amixer_volume_fast(alsa_volume)
        except Exception:
            return False
    
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Fast amixer read with caching to avoid excessive subprocess calls."""
        current_time = time.time()

        if self._adjustment_counter > 0 and (current_time - self._alsa_cache_time) < 0.01:
            return self._last_alsa_volume
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "get", "Digital",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0:
                return self._last_alsa_volume
            
            output = stdout.decode()
            match = re.search(r'\[(\d+)%\]', output)
            
            if match:
                volume = int(match.group(1))
                self._last_alsa_volume = volume
                self._alsa_cache_time = current_time
                return volume
            else:
                return self._last_alsa_volume
        except Exception:
            return self._last_alsa_volume
    
    async def _set_amixer_volume_fast(self, alsa_volume: int) -> bool:
        """Fast amixer write (async subprocess)."""
        try:
            limited = self._clamp_alsa_volume(alsa_volume)
            
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
                display = self._alsa_to_display(alsa_volume)
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
            
            display = self._alsa_to_display(alsa_volume)
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
            clamped = self._clamp_display_volume(float(display_volume))
            self._client_display_states[client_id] = clamped
            self.logger.debug(f"Updated client {client_id} display volume to {display_volume}%")
        except Exception as e:
            self.logger.error(f"Error updating client volume: {e}")

    def convert_alsa_to_display(self, alsa_volume: int) -> int:
        """Convert ALSA to display volume (public wrapper)."""
        return self._alsa_to_display(alsa_volume)

    def convert_display_to_alsa(self, display_volume: int) -> int:
        """Convert display to ALSA volume (public wrapper)."""
        return self._display_to_alsa(display_volume)
    
    def get_volume_config_public(self) -> Dict[str, Any]:
        """Get current volume configuration."""
        return {
            "alsa_min": self._alsa_min_volume,
            "alsa_max": self._alsa_max_volume,
            "startup_volume": self._default_startup_display_volume,
            "restore_last_volume": self._restore_last_volume,
            "mobile_steps": self._mobile_volume_steps,
            "rotary_steps": self._rotary_volume_steps
        }
    
    def get_rotary_step(self) -> int:
        """Get current rotary encoder step size."""
        return self._rotary_volume_steps
    
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
                        "display_volume_rounded": self._round_half_up(precise) if isinstance(precise, float) else "N/A",
                        "muted": client.get("muted", False)
                    })

                status.update({
                    "mode": "multiroom",
                    "multiroom_volume": self._round_half_up(self._multiroom_volume),
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

            # Wait for volume save task if still running
            if self._save_volume_task and not self._save_volume_task.done():
                await self._save_volume_task

            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")