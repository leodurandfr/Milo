# backend/core/equalizer/service.py
"""
CamillaDSP service for Milo - WebSocket client for CamillaDSP daemon.
Replaces alsaequal with full parametric EQ capabilities.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional
from enum import Enum

from backend.core.equalizer.presets import get_builtin_presets, get_preset_by_id, DEFAULT_CUSTOM_GAINS, DEFAULT_EQ_FREQS
from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.shared.persistence import (
    SchemaVersionMismatch,
    load_versioned_json,
    save_versioned_json,
)


class CamillaDspState(str, Enum):
    """CamillaDSP daemon states"""
    DISCONNECTED = "disconnected"
    INACTIVE = "inactive"  # Connected but not processing
    RUNNING = "running"    # Processing audio
    PAUSED = "paused"      # Paused (no audio flow)


class FilterType(str, Enum):
    """Supported filter types for parametric EQ"""
    PEAKING = "Peaking"
    LOWSHELF = "Lowshelf"
    HIGHSHELF = "Highshelf"
    LOWPASS = "Lowpass"
    HIGHPASS = "Highpass"
    NOTCH = "Notch"
    ALLPASS = "Allpass"


class CamillaDSPService:
    """
    CamillaDSP WebSocket client service.

    Manages connection to CamillaDSP daemon and provides methods for:
    - Parametric EQ configuration (10 bands)
    - Real-time filter updates
    - Compressor and loudness compensation
    - Volume control
    - Preset management
    - Status monitoring
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 1234
    RECONNECT_DELAY = 5.0
    MAX_RECONNECT_DELAY = 30.0

    # Dedicated persistence file for filters / compressor / loudness / mono /
    # active_preset / custom_gains.
    STORAGE_PATH = Path("/var/lib/milo/equalizer.json")
    SCHEMA_VERSION: int = 2
    PERSIST_DEBOUNCE_S = 1.0

    def __init__(self, settings_service=None, host: str = None, port: int = None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.host = host or self.DEFAULT_HOST
        self.port = port or self.DEFAULT_PORT

        self._client = None
        self._state = CamillaDspState.DISCONNECTED
        self._lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._connected = False
        self._running = True
        self._connection_ready = asyncio.Event()  # Signaled when first connected to CamillaDSP

        # State machine reference (set by container)
        self.state_machine = None

        # Callback for volume restoration after reconnection (set by dependencies.py)
        self._on_reconnect_callback = None

        # Single-thread executor for pycamilladsp sync calls (serializes all DSP commands
        # to prevent concurrent access to the non-thread-safe CamillaClient)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camilladsp")

        # Initialize with 10 default flat bands so get_filters() never returns
        # an empty list before equalizer.json is loaded (fresh install) or
        # before restore_effects has had a chance to write to the daemon
        # (post-restart window, since milo-camilladsp.service is PartOf
        # milo-backend.service and thus resets to its config.yml defaults).
        self._filters: List[Dict[str, Any]] = [
            {
                "id": f"eq_band_{i:02d}",
                "type": "Peaking",
                "freq": float(freq),
                "gain": 0.0,
                "q": 1.41,
                "enabled": True,
            }
            for i, freq in enumerate(DEFAULT_EQ_FREQS)
        ]
        self._loop = None  # Cached event loop

        # Advanced equalizer settings cache
        self._compressor: Dict[str, Any] = {
            "enabled": False,
            "threshold": -20.0,
            "ratio": 4.0,
            "attack": 10.0,
            "release": 100.0,
            "makeup_gain": 0.0
        }
        self._loudness: Dict[str, Any] = {
            "enabled": False,
            "high_boost": 5.0,
            "low_boost": 8.0
        }
        self._mono: bool = False
        self._volume: Dict[str, Any] = {
            "main": 0.0,  # dB
            "mute": False
        }

        # Preset / custom-gains state (formerly in settings.json under equalizer.*)
        self._active_preset: Optional[str] = None
        self._custom_gains: List[float] = list(DEFAULT_CUSTOM_GAINS)

        # Debounced persistence for equalizer.json (EQ rotary is a hot path)
        self._persist_debounce_task: Optional[asyncio.Task] = None
        self._bg = BackgroundTaskSet(self.logger, "equalizer")

        # Owned state: equalizer effects on/off. Loaded from
        # routing.equalizer_effects_enabled in settings.json. Read by
        # AudioStateMachine.broadcast_event when aggregating full_state, and by
        # AudioRoutingService via property.
        self._effects_enabled: bool = False

    def set_state_machine(self, state_machine) -> None:
        self.state_machine = state_machine

    def set_on_reconnect_callback(self, callback) -> None:
        """Set async callback invoked after successful reconnection (used for volume restore)."""
        self._on_reconnect_callback = callback

    async def _run(self, func):
        """Run sync pycamilladsp call in executor. Marks disconnected on failure."""
        if not self._loop:
            self._loop = asyncio.get_running_loop()
        try:
            return await self._loop.run_in_executor(self._executor, func)
        except Exception:
            if self._connected:
                self.logger.warning("CamillaDSP command failed, marking disconnected")
                self._connected = False
                self._state = CamillaDspState.DISCONNECTED
                self._client = None
            raise

    @property
    def state(self) -> CamillaDspState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def effects_enabled(self) -> bool:
        """Whether equalizer effects (EQ, compressor, loudness) are active."""
        return self._effects_enabled

    def set_effects_enabled(self, value: bool) -> None:
        """Update the in-memory effects-enabled cache.

        AudioRoutingService is the orchestrator: it calls bypass_effects/
        restore_effects and persists to settings.json. This setter only updates
        the cache so the broadcaster (AudioStateMachine) reads the right value.
        """
        self._effects_enabled = bool(value)

    async def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """
        Wait for CamillaDSP connection to be established.

        This is used by VolumeService to ensure CamillaDSP is ready before applying
        startup volume/mute state. Services initialize in parallel, so we need
        to wait for the connection before sending commands.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if connected within timeout, False otherwise
        """
        if self._connected:
            return True

        try:
            await asyncio.wait_for(self._connection_ready.wait(), timeout=timeout)
            # Event was set, check actual connection status
            return self._connected
        except asyncio.TimeoutError:
            self.logger.warning(f"CamillaDSP connection wait timed out after {timeout}s")
            return False

    def is_volume_control_available(self) -> bool:
        """
        Check if CamillaDSP can be used for volume control.
        Returns True when connected and ready (inactive, running, or paused).
        Volume can be set even when no audio is playing (inactive state).
        """
        return self._connected and self._state in (CamillaDspState.INACTIVE, CamillaDspState.RUNNING, CamillaDspState.PAUSED)

    async def initialize(self) -> bool:
        try:
            self.logger.info("Initializing CamillaDSP service...")

            # Load saved configuration from settings
            await self._load_saved_config()

            # Start the connection loop (handles initial connect + reconnection)
            self._reconnect_task = asyncio.create_task(self._connection_loop())

            return True

        except SchemaVersionMismatch:
            # Let dependencies.py::init_async log the banner + SystemExit(1)
            self._connection_ready.set()
            raise
        except Exception as e:
            self.logger.error(f"Error initializing CamillaDSP service: {e}")
            # Signal event on error so waiters don't block forever
            self._connection_ready.set()
            return False

    async def connect(self) -> bool:
        """Public connect method for external callers (e.g., routing.py startup)."""
        return await self._connect_once()

    async def _connection_loop(self) -> None:
        """Connection loop with exponential backoff reconnection.

        Connects to CamillaDSP, restores state, then idles until _run()
        detects a command failure and clears _connected. Follows the same
        pattern as SnapcastWebSocketService._connection_loop().
        """
        reconnect_delay = self.RECONNECT_DELAY

        while self._running:
            try:
                connected = await self._connect_once()

                if connected:
                    reconnect_delay = self.RECONNECT_DELAY
                    await self._restore_after_reconnect()

                    # Idle until a command failure marks us disconnected
                    while self._running and self._connected:
                        await asyncio.sleep(self.RECONNECT_DELAY)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Connection loop error: {e}")

            if self._running:
                if not self._connection_ready.is_set():
                    self._connection_ready.set()

                self.logger.info(f"Reconnecting to CamillaDSP in {reconnect_delay:.0f}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, self.MAX_RECONNECT_DELAY)

    async def _connect_once(self) -> bool:
        """Attempt a single connection to CamillaDSP. Returns True on success."""
        async with self._lock:
            if self._connected:
                return True

            try:
                try:
                    from camilladsp import CamillaClient
                except ImportError as e:
                    self.logger.error(f"pycamilladsp not installed: {e}")
                    return False

                self._client = CamillaClient(self.host, self.port)

                # Use run_in_executor directly to avoid reactive detection in _run()
                # during connection establishment
                if not self._loop:
                    self._loop = asyncio.get_running_loop()
                await self._loop.run_in_executor(self._executor, self._client.connect)

                self._connected = True
                self._state = await self._get_daemon_state()

                self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}, state: {self._state}")

                self._connection_ready.set()
                await self._broadcast_event("state_changed", {"state": self._state.value})

                return True

            except Exception as e:
                self._connected = False
                self._state = CamillaDspState.DISCONNECTED
                self._client = None
                self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
                return False

    async def _restore_after_reconnect(self) -> None:
        """Restore full DSP state after reconnection."""
        try:
            # Restore volume first so audio level is correct before effects are applied
            if self._on_reconnect_callback:
                await self._on_reconnect_callback()

            # Check if equalizer effects are enabled or bypassed
            if self._effects_enabled:
                self.logger.info("Reconnected: restoring equalizer effects from settings")
                await self.restore_effects()
            else:
                self.logger.info("Reconnected: effects disabled, bypassing")
                await self.bypass_effects()

            # Mono is a spatial setting, not an effect — restore independently of bypass
            if self._mono:
                await self.set_mono(enabled=True, persist=False, broadcast=False)

        except Exception as e:
            self.logger.error(f"Error restoring state after reconnect: {e}")

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client:
                try:
                    await self._run(self._client.disconnect)
                except Exception as e:
                    self.logger.warning(f"Error disconnecting from CamillaDSP: {e}")

                self._client = None

            self._connected = False
            self._state = CamillaDspState.DISCONNECTED

            # Broadcast state change event (frontend listens for 'state_changed')
            await self._broadcast_event("state_changed", {"state": self._state.value})

    @handle_errors(default=CamillaDspState.DISCONNECTED)
    async def _get_daemon_state(self) -> CamillaDspState:
        if not self._client:
            return CamillaDspState.DISCONNECTED

        # pycamilladsp v3 API: general.state() returns ProcessingState enum
        state = await self._run(self._client.general.state)

        # Map ProcessingState enum to our CamillaDspState
        state_str = str(state).split('.')[-1].upper()
        state_map = {
            "RUNNING": CamillaDspState.RUNNING,
            "PAUSED": CamillaDspState.PAUSED,
            "INACTIVE": CamillaDspState.INACTIVE,
        }

        return state_map.get(state_str, CamillaDspState.INACTIVE)

    async def get_status(self) -> Dict[str, Any]:
        try:
            if not self._connected:
                return {
                    "available": False,
                    "state": CamillaDspState.DISCONNECTED.value,
                    "message": "CamillaDSP not connected"
                }

            state = await self._get_daemon_state()
            self._state = state

            status = {
                "available": True,
                "state": state.value,
                "host": self.host,
                "port": self.port,
                "filters": await self.get_filters(),
                "compressor": self._compressor,
                "loudness": self._loudness,
                "mono": self._mono,
                "volume": await self.get_volume(),
            }

            # Add rate/buffer info if running
            if state == CamillaDspState.RUNNING:
                try:
                    # pycamilladsp v3 API: rate.capture()
                    rate = await self._run(self._client.rate.capture)
                    status["sample_rate"] = rate
                except Exception:
                    pass

            return status

        except Exception as e:
            self.logger.error(f"Error getting CamillaDSP status: {e}")
            return {
                "available": False,
                "state": CamillaDspState.DISCONNECTED.value,
                "error": str(e)
            }

    # === Config Helper ===

    async def _get_config(self) -> Dict[str, Any]:
        """Get config from active or file if inactive. Always returns a valid config dict."""
        config = await self._run(self._client.config.active)
        if config is None:
            path = await self._run(self._client.config.file_path)
            if path:
                config = await self._run(lambda: self._client.config.read_and_parse_file(path))
        if config is None:
            config = {}
        config.setdefault("filters", {})
        config.setdefault("pipeline", [])
        return config

    async def _set_config(self, config: Dict) -> None:
        """Apply config to CamillaDSP"""
        await self._run(lambda: self._client.config.set_active(config))

    # === Filter Management ===

    async def get_filters(self) -> List[Dict[str, Any]]:
        """Get current EQ filters from the in-memory persisted state.

        We deliberately do NOT read from the CamillaDSP daemon here. The
        daemon's runtime state is briefly out of sync with the source of
        truth (equalizer.json) right after a milo-backend restart: the
        daemon resets to its config.yml defaults (all 0 dB) and stays that
        way until `restore_effects()` finishes writing back the file's
        values. Reading from the daemon during that window would return 0s
        and clobber the correct in-memory state. All writes go through
        `set_filter()`, which keeps the two stores synchronized in the
        steady state.
        """
        return list(self._filters)

    def _parse_filters(self, filters_config: Dict) -> List[Dict[str, Any]]:
        result = []

        for name, filter_data in filters_config.items():
            # Only include EQ band filters (skip advanced filters like loudness, compressor)
            if not name.startswith("eq_band_"):
                continue

            filter_type = filter_data.get("type", "")
            parameters = filter_data.get("parameters", {})

            # Handle Biquad filters (most common for EQ)
            if filter_type == "Biquad":
                biquad_type = parameters.get("type", "")

                filter_info = {
                    "id": name,
                    "type": biquad_type,
                    "freq": parameters.get("freq", 1000),
                    "gain": parameters.get("gain", 0),
                    "q": parameters.get("q", 1.0),
                    "enabled": True
                }
                result.append(filter_info)

        # Sort filters by ID to maintain consistent order (eq_band_00, eq_band_01, etc.)
        result.sort(key=lambda f: f["id"])

        return result

    @handle_errors(default=False)
    async def set_filter(self, filter_id: str, freq: float, gain: float,
                         q: float, filter_type: str = "Peaking",
                         enabled: bool = True, persist: bool = True,
                         broadcast: bool = True) -> bool:
        """
        Update a single filter band.

        Args:
            persist: Set to False during bypass operations
            broadcast: Set to False to suppress WebSocket broadcast (useful for batch updates)
        """
        if not self._connected:
            self.logger.warning("Cannot set filter: not connected")
            return False

        # Build filter configuration
        filter_config = {
            "type": "Biquad",
            "parameters": {
                "type": filter_type,
                "freq": freq,
                "gain": gain,
                "q": q
            }
        }

        config = await self._get_config()
        config["filters"][filter_id] = filter_config
        await self._set_config(config)

        # Update local cache
        for f in self._filters:
            if f["id"] == filter_id:
                f.update({
                    "type": filter_type,
                    "freq": freq,
                    "gain": gain,
                    "q": q,
                    "enabled": enabled
                })
                break

        # Broadcast update (can be suppressed for batch updates)
        if broadcast:
            await self._broadcast_event("filter_changed", {
                "id": filter_id,
                "freq": freq,
                "gain": gain,
                "q": q,
                "type": filter_type
            })

        # Persist filters to settings (skip during bypass operations)
        if persist:
            await self._save_filters()

        return True

    @handle_errors(default=False)
    async def reset_filters(self) -> bool:
        if not self._connected:
            return False

        # Suppress per-filter broadcasts during reset (filters_reset event handles it)
        for f in self._filters:
            await self.set_filter(
                filter_id=f["id"],
                freq=f["freq"],
                gain=0,
                q=f.get("q", 1.0),
                filter_type=f.get("type", "Peaking"),
                broadcast=False
            )

        await self._broadcast_event("filters_reset", {})

        return True

    # === Volume Control ===

    async def get_volume(self) -> Dict[str, Any]:
        """Get current volume state. Returns cached value on error."""
        if not self._connected:
            return self._volume

        try:
            volume = await self._run(self._client.volume.main_volume)
            mute = await self._run(self._client.volume.main_mute)
            self._volume = {"main": volume, "mute": mute}
            return self._volume
        except Exception as e:
            self.logger.debug(f"Error in get_volume: {e}")
            return self._volume

    @handle_errors(default=False)
    async def set_volume(self, volume: float) -> bool:
        """Set main volume in dB"""
        if not self._connected:
            self.logger.warning(f"set_volume({volume:.1f}dB) rejected: CamillaDSP not connected")
            return False
        await self._run(lambda: self._client.volume.set_main_volume(volume))
        self._volume["main"] = volume
        return True

    @handle_errors(default=False)
    async def set_mute(self, muted: bool) -> bool:
        if not self._connected:
            self.logger.warning(f"set_mute({muted}) rejected: CamillaDSP not connected")
            return False
        await self._run(lambda: self._client.volume.set_main_mute(muted))
        self._volume["mute"] = muted
        return True

    # === Pipeline Management ===

    def _add_filter_to_pipeline(self, config: Dict, name: str, channels: List[int] = None) -> None:
        pipeline = config.setdefault("pipeline", [])
        for ch in (channels or [0, 1]):
            step = next((s for s in pipeline if s.get("type") == "Filter" and ch in s.get("channels", [])), None)
            if step:
                if name not in step.get("names", []):
                    step["names"].append(name)
            else:
                pipeline.append({"type": "Filter", "channels": [ch], "names": [name]})

    def _remove_filter_from_pipeline(self, config: Dict, name: str) -> None:
        for step in config.get("pipeline", []):
            if step.get("type") == "Filter" and name in step.get("names", []):
                step["names"].remove(name)

    def _add_processor_to_pipeline(self, config: Dict, name: str) -> None:
        pipeline = config.setdefault("pipeline", [])
        if not any(s.get("type") == "Processor" and s.get("name") == name for s in pipeline):
            pipeline.append({"type": "Processor", "name": name})

    def _remove_processor_from_pipeline(self, config: Dict, name: str) -> None:
        config["pipeline"] = [s for s in config.get("pipeline", [])
                              if not (s.get("type") == "Processor" and s.get("name") == name)]

    # === Compressor ===

    async def get_compressor(self) -> Dict[str, Any]:
        return self._compressor.copy()

    async def set_compressor(
        self,
        enabled: bool = None,
        threshold: float = None,
        ratio: float = None,
        attack: float = None,
        release: float = None,
        makeup_gain: float = None,
        persist: bool = True,
        broadcast: bool = True
    ) -> bool:
        """
        Update compressor settings.

        Args:
            persist: Set to False during bypass operations
            broadcast: Set to False to suppress WebSocket event (for batch zone updates)
        """
        if not self._connected:
            self.logger.warning("Cannot set compressor: not connected")
            return False

        # Update local cache after connection check
        if enabled is not None:
            self._compressor["enabled"] = enabled
        if threshold is not None:
            self._compressor["threshold"] = threshold
        if ratio is not None:
            self._compressor["ratio"] = ratio
        if attack is not None:
            self._compressor["attack"] = attack
        if release is not None:
            self._compressor["release"] = release
        if makeup_gain is not None:
            self._compressor["makeup_gain"] = makeup_gain

        return await self._apply_compressor_config(persist=persist, broadcast=broadcast)

    @handle_errors(default=False)
    async def _apply_compressor_config(self, persist: bool, broadcast: bool) -> bool:
        """Apply compressor configuration to CamillaDSP daemon"""
        config = await self._get_config()

        # Compressor is a Processor in CamillaDSP, not a Filter
        if not config.get("processors"):
            config["processors"] = {}

        if self._compressor["enabled"]:
            compressor_config = {
                "type": "Compressor",
                "parameters": {
                    "channels": 2,
                    "threshold": self._compressor["threshold"],
                    "factor": self._compressor["ratio"],
                    "attack": self._compressor["attack"] / 1000.0,  # ms to s
                    "release": self._compressor["release"] / 1000.0,
                    "makeup_gain": self._compressor["makeup_gain"]
                }
            }
            config["processors"]["compressor"] = compressor_config
            # Add compressor to pipeline as Processor type
            self._add_processor_to_pipeline(config, "compressor")
        else:
            # Remove compressor from processors and pipeline when disabled
            if "compressor" in config.get("processors", {}):
                del config["processors"]["compressor"]
            self._remove_processor_from_pipeline(config, "compressor")

        await self._set_config(config)

        # Broadcast change event (can be suppressed for batch zone updates)
        if broadcast:
            await self._broadcast_event("compressor_changed", self._compressor)

        # Persist compressor settings (skip during bypass operations)
        if persist:
            self._schedule_persist()

        return True

    # === Loudness Compensation ===

    async def get_loudness(self) -> Dict[str, Any]:
        return self._loudness.copy()

    async def set_loudness(
        self,
        enabled: bool = None,
        high_boost: float = None,
        low_boost: float = None,
        persist: bool = True,
        broadcast: bool = True
    ) -> bool:
        """
        Update loudness compensation settings.

        Args:
            persist: Set to False during bypass operations
            broadcast: Set to False to suppress WebSocket event (for batch zone updates)
        """
        if not self._connected:
            self.logger.warning("Cannot set loudness: not connected")
            return False

        # Update local cache after connection check
        if enabled is not None:
            self._loudness["enabled"] = enabled
        if high_boost is not None:
            self._loudness["high_boost"] = high_boost
        if low_boost is not None:
            self._loudness["low_boost"] = low_boost

        return await self._apply_loudness_config(persist=persist, broadcast=broadcast)

    @handle_errors(default=False)
    async def _apply_loudness_config(self, persist: bool, broadcast: bool) -> bool:
        """Apply loudness configuration to CamillaDSP daemon"""
        config = await self._get_config()

        if self._loudness["enabled"]:
            # Loudness is implemented via low and high shelf filters
            # adjusted based on current volume vs reference level
            config["filters"]["loudness_low"] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Lowshelf",
                    "freq": 100,
                    "gain": self._loudness["low_boost"],
                    "slope": 6.0
                }
            }

            config["filters"]["loudness_high"] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Highshelf",
                    "freq": 8000,
                    "gain": self._loudness["high_boost"],
                    "slope": 6.0
                }
            }
            # Add loudness filters to pipeline for both channels
            self._add_filter_to_pipeline(config, "loudness_low")
            self._add_filter_to_pipeline(config, "loudness_high")
        else:
            # Remove loudness filters from filters and pipeline
            if "loudness_low" in config["filters"]:
                del config["filters"]["loudness_low"]
            if "loudness_high" in config["filters"]:
                del config["filters"]["loudness_high"]
            self._remove_filter_from_pipeline(config, "loudness_low")
            self._remove_filter_from_pipeline(config, "loudness_high")

        await self._set_config(config)

        # Broadcast change event (can be suppressed for batch zone updates)
        if broadcast:
            await self._broadcast_event("loudness_changed", self._loudness)

        # Persist loudness settings (skip during bypass operations)
        if persist:
            self._schedule_persist()

        return True

    # === Mono Mixing ===

    async def get_mono(self) -> bool:
        return self._mono

    async def set_mono(
        self,
        enabled: bool,
        persist: bool = True,
        broadcast: bool = True
    ) -> bool:
        """
        Switch between stereo passthrough and mono summing in CamillaDSP.

        Swaps the pipeline's Mixer step between "stereo" (passthrough) and
        "mono" (L+R summed at -6dB to both outputs).

        Args:
            persist: Set to False during batch zone updates
            broadcast: Set to False to suppress WebSocket event
        """
        if not self._connected:
            self.logger.warning("Cannot set mono: not connected")
            return False

        self._mono = enabled
        return await self._apply_mono_config(persist=persist, broadcast=broadcast)

    @handle_errors(default=False)
    async def _apply_mono_config(self, persist: bool, broadcast: bool) -> bool:
        """Apply mono/stereo mixer configuration to CamillaDSP daemon."""
        config = await self._get_config()
        config.setdefault("mixers", {})

        # Ensure mono mixer definition exists (backwards compat for old configs)
        if "mono" not in config["mixers"]:
            config["mixers"]["mono"] = {
                "channels": {"in": 2, "out": 2},
                "mapping": [
                    {"dest": 0, "sources": [
                        {"channel": 0, "gain": -6, "inverted": False},
                        {"channel": 1, "gain": -6, "inverted": False}
                    ]},
                    {"dest": 1, "sources": [
                        {"channel": 0, "gain": -6, "inverted": False},
                        {"channel": 1, "gain": -6, "inverted": False}
                    ]}
                ]
            }

        # Swap the pipeline's Mixer step name
        target_name = "mono" if self._mono else "stereo"
        for step in config.get("pipeline", []):
            if step.get("type") == "Mixer":
                step["name"] = target_name
                break

        await self._set_config(config)

        if broadcast:
            await self._broadcast_event("mono_changed", {"enabled": self._mono})

        if persist:
            self._schedule_persist()

        return True

    # === Crossover Filters ===

    @handle_errors(default=False)
    async def _set_passband_filter(self, filter_name: str, filter_type: str,
                                    enabled: bool, freq: float, q: float, event: str) -> bool:
        """Internal helper for highpass/lowpass filters"""
        if not self._connected:
            return False

        config = await self._get_config()

        if enabled:
            config["filters"][filter_name] = {
                "type": "Biquad",
                "parameters": {"type": filter_type, "freq": freq, "q": q}
            }
            self._add_filter_to_pipeline(config, filter_name)
        else:
            if filter_name in config["filters"]:
                del config["filters"][filter_name]
            self._remove_filter_from_pipeline(config, filter_name)

        await self._set_config(config)
        await self._broadcast_event(event, {"enabled": enabled, "frequency": freq, "q": q})
        return True

    async def get_crossover_filter(self) -> Dict[str, Any]:
        if not self._connected:
            return {"enabled": False, "frequency": 80, "q": 0.707}
        try:
            config = await self._get_config()
            if "crossover_highpass" in config["filters"]:
                params = config["filters"]["crossover_highpass"].get("parameters", {})
                return {"enabled": True, "frequency": params.get("freq", 80), "q": params.get("q", 0.707)}
            return {"enabled": False, "frequency": 80, "q": 0.707}
        except Exception:
            return {"enabled": False, "frequency": 80, "q": 0.707}

    async def set_crossover_filter(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """Apply highpass filter to remove bass from speakers (for subwoofer setups)"""
        return await self._set_passband_filter("crossover_highpass", "Highpass", enabled, frequency, q, "crossover_changed")

    async def set_lowpass_filter(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """Apply lowpass filter to send only bass to subwoofer."""
        return await self._set_passband_filter("crossover_lowpass", "Lowpass", enabled, frequency, q, "lowpass_changed")

    # === Level Monitoring ===

    @handle_errors(default={"available": False}, level='debug')
    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak/RMS)"""
        if not self._connected:
            return {"available": False}

        capture = await self._run(self._client.levels.capture_peak)
        playback = await self._run(self._client.levels.playback_peak)
        return {"available": True, "input_peak": capture, "output_peak": playback}

    # === Preset Management ===

    def get_presets(self) -> List[Dict]:
        return get_builtin_presets()

    async def _apply_gains(self, gains: List[float]) -> None:
        """Apply gain values to EQ bands"""
        for i, gain in enumerate(gains):
            filter_id = f"eq_band_{i:02d}"
            existing = next((f for f in self._filters if f["id"] == filter_id), None)
            if existing:
                # Suppress per-filter broadcasts during preset load (preset_loaded event handles it)
                await self.set_filter(filter_id, existing["freq"], gain,
                                       existing.get("q", 1.41), existing.get("type", "Peaking"),
                                       broadcast=False)
            else:
                # Filter doesn't exist in cache - use default frequency
                freq = DEFAULT_EQ_FREQS[i] if i < len(DEFAULT_EQ_FREQS) else 1000
                self.logger.info(f"Filter {filter_id} not in cache, creating with freq={freq}")
                await self.set_filter(filter_id, freq, gain, 1.41, "Peaking",
                                       broadcast=False)

    async def _get_preset_gains(self, preset_id: str) -> Optional[List[float]]:
        """Get gains for a preset ID (builtin or custom)"""
        if preset_id == "custom":
            return await self.get_custom_gains()
        preset = get_preset_by_id(preset_id)
        return preset["gains"] if preset else None

    @handle_errors(default=False)
    async def load_preset(self, preset_id: str) -> bool:
        """Load a builtin or custom preset"""
        # Early return if already on the same preset (avoids overwriting current values)
        current = await self.get_active_preset()
        if preset_id == current:
            self.logger.debug(f"Already on preset {preset_id}, skipping")
            return True

        gains = await self._get_preset_gains(preset_id)
        if gains is None:
            self.logger.warning(f"Preset not found: {preset_id}")
            return False

        await self._apply_gains(gains)

        self._active_preset = preset_id
        self._schedule_persist()
        self.logger.info(f"Saved active preset: {preset_id}")
        await self._broadcast_event("preset_loaded", {"id": preset_id})
        return True

    async def save_custom_gains(self) -> None:
        self._custom_gains = [float(f.get("gain", 0)) for f in self._filters[:10]]
        self._schedule_persist()

    async def get_custom_gains(self) -> List[float]:
        if self._custom_gains and len(self._custom_gains) >= 10:
            return list(self._custom_gains)
        return list(DEFAULT_CUSTOM_GAINS)

    async def get_active_preset(self) -> Optional[str]:
        return self._active_preset

    async def set_active_preset(self, preset_id: str) -> None:
        """Update the active preset id (used by API custom-save flow)."""
        self._active_preset = preset_id
        self._schedule_persist()

    # === Effects Bypass/Restore (for equalizer toggle) ===

    @handle_errors(default=False)
    async def bypass_effects(self) -> bool:
        """
        Bypass all equalizer effects while keeping volume control active.

        Pipeline-only bypass: removes EQ / compressor / loudness references
        from CamillaDSP's pipeline so the daemon stops applying them. The
        in-memory cache (`self._filters`, `self._compressor`, `self._loudness`)
        is the source of truth for user intent and is **never** mutated here —
        that is what lets `restore_effects()` re-push the exact pre-bypass
        values without going through disk.

        Filter / processor definitions stay intact in `config["filters"]` and
        `config["processors"]`; only their pipeline references are removed.
        Crossover filters share `config["pipeline"]` but use different names
        (`crossover_*`) so they are untouched.
        """
        if not self._connected:
            self.logger.warning("Cannot bypass effects: not connected")
            return False

        self.logger.info("Bypassing equalizer effects (pipeline-only)")

        config = await self._get_config()

        for f in self._filters:
            self._remove_filter_from_pipeline(config, f["id"])

        self._remove_processor_from_pipeline(config, "compressor")
        self._remove_filter_from_pipeline(config, "loudness_low")
        self._remove_filter_from_pipeline(config, "loudness_high")

        await self._set_config(config)

        self.logger.info("Equalizer effects bypassed (volume unchanged)")
        return True

    @handle_errors(default=False)
    async def restore_effects(self) -> bool:
        """
        Restore all equalizer effects from the in-memory cache.

        Pushes the user's saved EQ / compressor / loudness state from
        `self._filters`, `self._compressor`, `self._loudness` to the daemon
        as a single config write: writes the definitions into
        `config["filters"]` / `config["processors"]` and (re)adds their
        pipeline references. Compressor and loudness are only added to the
        pipeline if their `enabled` flag is True in the cache — preserving
        the user's per-effect on/off choice across a master bypass/restore
        cycle.
        """
        if not self._connected:
            self.logger.warning("Cannot restore effects: not connected")
            return False

        self.logger.info("Restoring equalizer effects from cache")

        config = await self._get_config()
        config.setdefault("filters", {})
        config.setdefault("processors", {})

        for f in self._filters:
            config["filters"][f["id"]] = {
                "type": "Biquad",
                "parameters": {
                    "type": f.get("type", "Peaking"),
                    "freq": f["freq"],
                    "gain": f.get("gain", 0),
                    "q": f.get("q", 1.0),
                },
            }
            self._add_filter_to_pipeline(config, f["id"])

        if self._compressor.get("enabled"):
            config["processors"]["compressor"] = {
                "type": "Compressor",
                "parameters": {
                    "channels": 2,
                    "threshold": self._compressor["threshold"],
                    "factor": self._compressor["ratio"],
                    "attack": self._compressor["attack"] / 1000.0,
                    "release": self._compressor["release"] / 1000.0,
                    "makeup_gain": self._compressor["makeup_gain"],
                },
            }
            self._add_processor_to_pipeline(config, "compressor")

        if self._loudness.get("enabled"):
            config["filters"]["loudness_low"] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Lowshelf",
                    "freq": 100,
                    "gain": self._loudness["low_boost"],
                    "slope": 6.0,
                },
            }
            config["filters"]["loudness_high"] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Highshelf",
                    "freq": 8000,
                    "gain": self._loudness["high_boost"],
                    "slope": 6.0,
                },
            }
            self._add_filter_to_pipeline(config, "loudness_low")
            self._add_filter_to_pipeline(config, "loudness_high")

        await self._set_config(config)

        self.logger.info("Equalizer effects restored")
        return True

    # === Configuration Persistence ===

    async def _load_saved_config(self) -> None:
        """Load persisted equalizer state from /var/lib/milo/equalizer.json.

        Raises SchemaVersionMismatch on version drift (caller in dependencies.py
        logs the banner and SystemExit(1)s). The effects-enabled toggle lives
        in settings.json under routing.equalizer_effects_enabled.
        """
        if self.settings_service:
            saved_effects_enabled = await self.settings_service.get_setting(
                "routing.equalizer_effects_enabled"
            )
            if saved_effects_enabled is not None:
                self._effects_enabled = bool(saved_effects_enabled)
                self.logger.info(f"Loaded saved effects_enabled: {self._effects_enabled}")

        data = await load_versioned_json(self.STORAGE_PATH, self.SCHEMA_VERSION)
        if not data:
            return  # Fresh install — in-memory defaults stay

        saved_filters = data.get("filters")
        if isinstance(saved_filters, list) and saved_filters:
            self._filters = saved_filters

        saved_compressor = data.get("compressor")
        if isinstance(saved_compressor, dict):
            self._compressor.update(saved_compressor)

        saved_loudness = data.get("loudness")
        if isinstance(saved_loudness, dict):
            self._loudness.update(saved_loudness)

        saved_mono = data.get("mono")
        if isinstance(saved_mono, bool):
            self._mono = saved_mono

        saved_preset = data.get("active_preset")
        if isinstance(saved_preset, str):
            self._active_preset = saved_preset

        saved_gains = data.get("custom_gains")
        if isinstance(saved_gains, list) and len(saved_gains) >= 10:
            self._custom_gains = [float(g) for g in saved_gains]

        self.logger.info(
            f"Loaded equalizer.json: {len(self._filters)} filters, "
            f"preset={self._active_preset}, mono={self._mono}"
        )

    @handle_errors(default=None)
    async def _save_filters(self) -> None:
        """Persist filters (used by set_filter for auto-persistence)."""
        self._schedule_persist()

    def _schedule_persist(self) -> None:
        """Schedule a debounced persist (~1s after last change). Safe to call rapidly."""
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()

        async def _debounced():
            try:
                await asyncio.sleep(self.PERSIST_DEBOUNCE_S)
                await self._persist_state_async()
            except asyncio.CancelledError:
                pass

        self._persist_debounce_task = self._bg.spawn(_debounced(), label="persist_state")

    async def _persist_state_async(self) -> None:
        """Write current equalizer state to /var/lib/milo/equalizer.json atomically."""
        try:
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "active_preset": self._active_preset,
                "custom_gains": list(self._custom_gains),
                "mono": self._mono,
                "filters": self._filters,
                "compressor": dict(self._compressor),
                "loudness": dict(self._loudness),
            }

            await save_versioned_json(self.STORAGE_PATH, data, self.SCHEMA_VERSION)
            self.logger.debug(
                f"Persisted equalizer state: {len(self._filters)} filters, preset={self._active_preset}"
            )

        except Exception as e:
            self.logger.error(f"Error persisting equalizer state: {e}", exc_info=True)

    # === Event Broadcasting ===

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast equalizer event via state machine (WebSocket)."""
        if self.state_machine:
            await self.state_machine.broadcast_event("equalizer", event_type, data)

    # === Cleanup ===

    async def cleanup(self) -> None:
        """Clean up resources"""
        self.logger.info("Cleaning up CamillaDSP service...")

        # Flush any pending debounced persist before shutdown
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()
            await self._persist_state_async()

        # Stop the connection loop
        self._running = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect from daemon
        await self.disconnect()

        # Shut down the dedicated executor
        self._executor.shutdown(wait=False)

        self.logger.info("CamillaDSP service cleanup complete")
