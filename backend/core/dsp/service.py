# backend/core/dsp/service.py
"""
CamillaDSP service for Milo - WebSocket client for CamillaDSP daemon.
Replaces alsaequal with full parametric EQ capabilities.
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional
from enum import Enum

from backend.core.events import EventBus, get_event_bus
from backend.core.dsp.presets import get_builtin_presets, get_preset_by_id, DEFAULT_MANUAL_GAINS


class DspState(str, Enum):
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
    COMMAND_TIMEOUT = 5.0

    def __init__(self, settings_service=None, host: str = None, port: int = None,
                 event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.host = host or self.DEFAULT_HOST
        self.port = port or self.DEFAULT_PORT

        # EventBus integration
        self.event_bus = event_bus or get_event_bus()

        self._client = None
        self._state = DspState.DISCONNECTED
        self._lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._connected = False
        self._connection_ready = asyncio.Event()  # Signaled when connected to CamillaDSP

        # State machine reference (set by container)
        self.state_machine = None

        # Current configuration cache
        self._current_config: Dict[str, Any] = {}
        self._filters: List[Dict[str, Any]] = []
        self._loop = None  # Cached event loop

        # Advanced DSP settings cache
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
            "reference_level": 80,
            "high_boost": 5.0,
            "low_boost": 8.0
        }
        self._volume: Dict[str, Any] = {
            "main": 0.0,  # dB
            "mute": False
        }

    def set_state_machine(self, state_machine) -> None:
        self.state_machine = state_machine

    async def _run(self, func):
        """Run sync pycamilladsp call in executor"""
        if not self._loop:
            self._loop = asyncio.get_event_loop()
        return await self._loop.run_in_executor(None, func)

    @property
    def state(self) -> DspState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None

    async def wait_for_connection(self, timeout: float = 10.0) -> bool:
        """
        Wait for CamillaDSP connection to be established.

        This is used by VolumeService to ensure DSP is ready before applying
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
        Check if DSP can be used for volume control.
        Returns True when connected and ready (inactive, running, or paused).
        Volume can be set even when no audio is playing (inactive state).
        """
        return self._connected and self._state in (DspState.INACTIVE, DspState.RUNNING, DspState.PAUSED)

    async def initialize(self) -> bool:
        try:
            self.logger.info("Initializing CamillaDSP service...")

            # Load saved configuration from settings
            await self._load_saved_config()

            # Attempt initial connection
            connected = await self.connect()

            if connected:
                self.logger.info("CamillaDSP service initialized and connected")

                # Apply saved preset to daemon on startup
                await self._apply_saved_preset()
            else:
                self.logger.warning("CamillaDSP service initialized but not connected (daemon may not be running)")
                # Signal event even on failure so waiters don't block forever
                self._connection_ready.set()

            return True

        except Exception as e:
            self.logger.error(f"Error initializing CamillaDSP service: {e}")
            # Signal event on error so waiters don't block forever
            self._connection_ready.set()
            return False

    async def connect(self) -> bool:
        async with self._lock:
            if self._connected:
                return True

            try:
                # Import pycamilladsp here to handle cases where it's not installed
                try:
                    from camilladsp import CamillaClient
                except ImportError as e:
                    self.logger.error(f"pycamilladsp not installed. Run: pip install camilladsp (error: {e})")
                    return False

                self._client = CamillaClient(self.host, self.port)

                # pycamilladsp is synchronous, wrap in executor
                await self._run(self._client.connect)

                self._connected = True
                self._state = await self._get_daemon_state()

                self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}, state: {self._state}")

                # Signal that connection is ready (VolumeService waits for this at startup)
                self._connection_ready.set()

                # Broadcast state change event (frontend listens for 'state_changed')
                await self._broadcast_event("state_changed", {"state": self._state.value})

                return True

            except Exception as e:
                self._connected = False
                self._state = DspState.DISCONNECTED
                self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
                return False

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client:
                try:
                    await self._run(self._client.disconnect)
                except Exception as e:
                    self.logger.warning(f"Error disconnecting from CamillaDSP: {e}")

                self._client = None

            self._connected = False
            self._state = DspState.DISCONNECTED

            # Broadcast state change event (frontend listens for 'state_changed')
            await self._broadcast_event("state_changed", {"state": self._state.value})

    async def _get_daemon_state(self) -> DspState:
        if not self._client:
            return DspState.DISCONNECTED

        try:
            # pycamilladsp v3 API: general.state() returns ProcessingState enum
            state = await self._run(self._client.general.state)

            # Map ProcessingState enum to our DspState
            state_str = str(state).split('.')[-1].upper()
            state_map = {
                "RUNNING": DspState.RUNNING,
                "PAUSED": DspState.PAUSED,
                "INACTIVE": DspState.INACTIVE,
            }

            return state_map.get(state_str, DspState.INACTIVE)

        except Exception as e:
            self.logger.error(f"Error getting daemon state: {e}")
            return DspState.DISCONNECTED

    async def get_status(self) -> Dict[str, Any]:
        try:
            if not self._connected:
                return {
                    "available": False,
                    "state": DspState.DISCONNECTED.value,
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
                "volume": await self.get_volume(),
            }

            # Add rate/buffer info if running
            if state == DspState.RUNNING:
                try:
                    # pycamilladsp v3 API: rate.capture()
                    rate = await self._run(self._client.rate.capture)
                    status["sample_rate"] = rate
                except Exception:
                    pass

            return status

        except Exception as e:
            self.logger.error(f"Error getting DSP status: {e}")
            return {
                "available": False,
                "state": DspState.DISCONNECTED.value,
                "error": str(e)
            }

    # === Config Helper ===

    async def _get_config(self) -> Optional[Dict[str, Any]]:
        """Get config from active or file if inactive"""
        config = await self._run(self._client.config.active)
        if config is None:
            path = await self._run(self._client.config.file_path)
            if path:
                config = await self._run(lambda: self._client.config.read_and_parse_file(path))
        return config

    async def _set_config(self, config: Dict) -> None:
        """Apply config to CamillaDSP"""
        await self._run(lambda: self._client.config.set_active(config))

    # === Filter Management ===

    async def get_filters(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return self._filters

        try:
            config = await self._get_config()

            if config and "filters" in config:
                self._filters = self._parse_filters(config["filters"])

            return self._filters

        except Exception as e:
            self.logger.error(f"Error getting filters: {e}")
            return self._filters

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

    async def set_filter(self, filter_id: str, freq: float, gain: float,
                         q: float, filter_type: str = "Peaking",
                         enabled: bool = True, persist: bool = True,
                         from_preset: bool = False) -> bool:
        """
        Update a single filter band.

        Args:
            persist: Set to False during bypass operations
            from_preset: Set to True when loading a preset (don't switch to manual)
        """
        if not self._connected:
            self.logger.warning("Cannot set filter: not connected")
            return False

        try:
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

            # Get config (from active or file if inactive)
            config = await self._get_config()

            if config:
                if "filters" not in config:
                    config["filters"] = {}
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

            # Broadcast update
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

                # If user manually modified a filter while on a predefined preset,
                # save current gains as manual and switch to manual mode
                if not from_preset and self.settings_service:
                    current_preset = await self.get_active_preset()
                    if current_preset and current_preset != "manual":
                        await self._save_manual_gains()
                        await self.settings_service.set_setting("dsp.active_preset", "manual")
                        await self._broadcast_event("preset_loaded", {"id": "manual"})

            return True

        except Exception as e:
            self.logger.error(f"Error setting filter {filter_id}: {e}")
            return False

    async def add_filter(self, filter_id: str, freq: float = 1000,
                         gain: float = 0, q: float = 1.0,
                         filter_type: str = "Peaking") -> bool:
        if not self._connected:
            return False

        try:
            filter_config = {
                "type": "Biquad",
                "parameters": {
                    "type": filter_type,
                    "freq": freq,
                    "gain": gain,
                    "q": q
                }
            }

            # Get current config and add filter
            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            if "filters" not in config:
                config["filters"] = {}

            config["filters"][filter_id] = filter_config

            await self._set_config(config)

            # Update local cache
            self._filters.append({
                "id": filter_id,
                "type": filter_type,
                "freq": freq,
                "gain": gain,
                "q": q,
                "enabled": True
            })

            await self._broadcast_event("filter_added", {"id": filter_id})

            return True

        except Exception as e:
            self.logger.error(f"Error adding filter: {e}")
            return False

    async def remove_filter(self, filter_id: str) -> bool:
        if not self._connected:
            return False

        try:
            config = await self._get_config()

            if config and "filters" in config and filter_id in config["filters"]:
                del config["filters"][filter_id]

                await self._set_config(config)

                # Update local cache
                self._filters = [f for f in self._filters if f["id"] != filter_id]

                await self._broadcast_event("filter_removed", {"id": filter_id})

                return True

            return False

        except Exception as e:
            self.logger.error(f"Error removing filter: {e}")
            return False

    async def reset_filters(self) -> bool:
        if not self._connected:
            return False

        try:
            for f in self._filters:
                await self.set_filter(
                    filter_id=f["id"],
                    freq=f["freq"],
                    gain=0,
                    q=f.get("q", 1.0),
                    filter_type=f.get("type", "Peaking")
                )

            await self._broadcast_event("filters_reset", {})

            return True

        except Exception as e:
            self.logger.error(f"Error resetting filters: {e}")
            return False

    # === Volume Control ===

    async def get_volume(self) -> Dict[str, Any]:
        if not self._connected:
            return self._volume

        try:
            volume = await self._run(self._client.volume.main_volume)
            mute = await self._run(self._client.volume.main_mute)
            self._volume = {"main": volume, "mute": mute}
            return self._volume
        except Exception as e:
            self.logger.debug(f"Error getting volume: {e}")
            return self._volume

    async def set_volume(self, volume: float) -> bool:
        """Set main volume in dB"""
        if not self._connected:
            return False
        try:
            await self._run(lambda: self._client.volume.set_main_volume(volume))
            self._volume["main"] = volume
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, muted: bool) -> bool:
        if not self._connected:
            return False
        try:
            await self._run(lambda: self._client.volume.set_main_mute(muted))
            self._volume["mute"] = muted
            await self._broadcast_event("mute_changed", {"muted": muted})
            return True
        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False

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
        persist: bool = True
    ) -> bool:
        """Update compressor settings. Set persist=False during bypass operations."""
        if not self._connected:
            # Update local cache even when not connected
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
            return True

        try:
            # Update local cache
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

            # Get config
            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            if not config.get("filters"):
                config["filters"] = {}

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

            await self._broadcast_event("compressor_changed", self._compressor)

            # Persist compressor settings (skip during bypass operations)
            if persist and self.settings_service:
                await self.settings_service.set_setting("dsp.compressor", self._compressor)

            return True

        except Exception as e:
            self.logger.error(f"Error setting compressor: {e}")
            return False

    # === Loudness Compensation ===

    async def get_loudness(self) -> Dict[str, Any]:
        return self._loudness.copy()

    async def set_loudness(
        self,
        enabled: bool = None,
        reference_level: int = None,
        high_boost: float = None,
        low_boost: float = None,
        persist: bool = True
    ) -> bool:
        """Update loudness compensation settings. Set persist=False during bypass operations."""
        # Update local cache
        if enabled is not None:
            self._loudness["enabled"] = enabled
        if reference_level is not None:
            self._loudness["reference_level"] = reference_level
        if high_boost is not None:
            self._loudness["high_boost"] = high_boost
        if low_boost is not None:
            self._loudness["low_boost"] = low_boost

        if not self._connected:
            return True

        try:
            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            if "filters" not in config:
                config["filters"] = {}

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

            await self._broadcast_event("loudness_changed", self._loudness)

            # Persist loudness settings (skip during bypass operations)
            if persist and self.settings_service:
                await self.settings_service.set_setting("dsp.loudness", self._loudness)

            return True

        except Exception as e:
            self.logger.error(f"Error setting loudness: {e}")
            return False

    # === Crossover Filters ===

    async def _set_passband_filter(self, filter_name: str, filter_type: str,
                                    enabled: bool, freq: float, q: float, event: str) -> bool:
        """Internal helper for highpass/lowpass filters"""
        if not self._connected:
            return False
        try:
            config = await self._get_config() or {"filters": {}, "pipeline": []}
            if "filters" not in config:
                config["filters"] = {}

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
        except Exception as e:
            self.logger.error(f"Error setting {filter_name}: {e}")
            return False

    async def get_crossover_filter(self) -> Dict[str, Any]:
        if not self._connected:
            return {"enabled": False, "frequency": 80, "q": 0.707}
        try:
            config = await self._get_config()
            if config and "filters" in config and "crossover_highpass" in config["filters"]:
                params = config["filters"]["crossover_highpass"].get("parameters", {})
                return {"enabled": True, "frequency": params.get("freq", 80), "q": params.get("q", 0.707)}
            return {"enabled": False, "frequency": 80, "q": 0.707}
        except Exception:
            return {"enabled": False, "frequency": 80, "q": 0.707}

    async def set_crossover_filter(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """Apply highpass filter to remove bass from speakers (for subwoofer setups)"""
        return await self._set_passband_filter("crossover_highpass", "Highpass", enabled, frequency, q, "crossover_changed")

    async def set_lowpass_filter(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """Apply lowpass filter to send only bass to subwoofer"""
        return await self._set_passband_filter("crossover_lowpass", "Lowpass", enabled, frequency, q, "lowpass_changed")

    # === Level Monitoring ===

    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak/RMS)"""
        if not self._connected:
            return {"available": False}

        try:
            capture = await self._run(self._client.levels.capture_peak)
            playback = await self._run(self._client.levels.playback_peak)
            return {"available": True, "input_peak": capture, "output_peak": playback}
        except Exception as e:
            self.logger.debug(f"Error getting levels: {e}")
            return {"available": False}

    # === Preset Management ===

    def get_presets(self) -> List[Dict]:
        return get_builtin_presets()

    async def _apply_gains(self, gains: List[float]) -> None:
        """Apply gain values to EQ bands"""
        # Ensure filters are loaded from CamillaDSP before applying gains
        if not self._filters:
            await self.get_filters()

        # Default EQ frequencies if filters still not available
        DEFAULT_EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

        for i, gain in enumerate(gains):
            filter_id = f"eq_band_{i:02d}"
            existing = next((f for f in self._filters if f["id"] == filter_id), None)
            if existing:
                await self.set_filter(filter_id, existing["freq"], gain,
                                       existing.get("q", 1.41), existing.get("type", "Peaking"),
                                       from_preset=True)
            else:
                # Filter doesn't exist in cache - use default frequency
                freq = DEFAULT_EQ_FREQS[i] if i < len(DEFAULT_EQ_FREQS) else 1000
                self.logger.warning(f"Filter {filter_id} not in cache, creating with freq={freq}")
                await self.set_filter(filter_id, freq, gain, 1.41, "Peaking", from_preset=True)

    async def _get_preset_gains(self, preset_id: str) -> Optional[List[float]]:
        """Get gains for a preset ID (builtin or manual)"""
        if preset_id == "manual":
            if self.settings_service:
                saved = await self.settings_service.get_setting("dsp.manual_gains")
                if saved and len(saved) >= 10:
                    return saved
            return DEFAULT_MANUAL_GAINS
        preset = get_preset_by_id(preset_id)
        return preset["gains"] if preset else None

    async def load_preset(self, preset_id: str) -> bool:
        """Load a builtin or manual preset"""
        # Early return if already on the same preset (avoids overwriting current values)
        current = await self.get_active_preset()
        if preset_id == current:
            self.logger.debug(f"Already on preset {preset_id}, skipping")
            return True

        gains = await self._get_preset_gains(preset_id)
        if gains is None:
            self.logger.warning(f"Preset not found: {preset_id}")
            return False

        try:
            # Save current as manual before switching
            if current in ("manual", None) and preset_id != "manual":
                await self._save_manual_gains()

            await self._apply_gains(gains)

            if self.settings_service:
                await self.settings_service.set_setting("dsp.active_preset", preset_id)
                self.logger.info(f"Saved active preset: {preset_id}")
            await self._broadcast_event("preset_loaded", {"id": preset_id})
            return True
        except Exception as e:
            self.logger.error(f"Error loading preset: {e}")
            return False

    async def _save_manual_gains(self) -> None:
        if self.settings_service:
            gains = [f.get("gain", 0) for f in self._filters[:10]]
            await self.settings_service.set_setting("dsp.manual_gains", gains)

    async def get_manual_gains(self) -> List[float]:
        if self.settings_service:
            gains = await self.settings_service.get_setting("dsp.manual_gains")
            if gains and len(gains) >= 10:
                return gains
        return DEFAULT_MANUAL_GAINS

    async def get_active_preset(self) -> Optional[str]:
        if not self.settings_service:
            return None
        return await self.settings_service.get_setting("dsp.active_preset")

    async def clear_active_preset(self) -> None:
        if self.settings_service:
            await self.settings_service.set_setting("dsp.active_preset", None)

    # === Effects Bypass/Restore (for DSP toggle) ===

    async def bypass_effects(self) -> bool:
        """
        Bypass all DSP effects while keeping volume control active.

        This is called when user disables "DSP" toggle. CamillaDSP keeps running
        but all audio processing (EQ, compressor, loudness) is bypassed.
        """
        if not self._connected:
            self.logger.warning("Cannot bypass effects: not connected")
            return False

        try:
            self.logger.info("Bypassing all DSP effects...")

            # Save current config before bypassing (filters, compressor, loudness)
            await self.save_current_config()

            # 1. Reset all EQ filters to 0 dB gain (persist=False to keep saved values)
            for f in self._filters:
                await self.set_filter(
                    filter_id=f["id"],
                    freq=f["freq"],
                    gain=0,  # Bypass = 0 dB gain
                    q=f.get("q", 1.0),
                    filter_type=f.get("type", "Peaking"),
                    persist=False  # Don't overwrite saved settings
                )

            # 2. Disable compressor (persist=False to keep settings for restore)
            await self.set_compressor(enabled=False, persist=False)

            # 3. Disable loudness (persist=False to keep settings for restore)
            await self.set_loudness(enabled=False, persist=False)

            self.logger.info("DSP effects bypassed (volume unchanged)")
            await self._broadcast_event("effects_bypassed", {"bypassed": True})
            return True

        except Exception as e:
            self.logger.error(f"Error bypassing effects: {e}")
            return False

    async def restore_effects(self) -> bool:
        """
        Restore all DSP effects from saved settings.

        This is called when user enables "DSP" toggle. Restores EQ filters,
        compressor, and loudness from saved settings.
        """
        if not self._connected:
            self.logger.warning("Cannot restore effects: not connected")
            return False

        try:
            self.logger.info("Restoring DSP effects from settings...")

            # 1. Restore EQ filters from settings
            if self.settings_service:
                saved_filters = await self.settings_service.get_setting("dsp.filters")
                if saved_filters:
                    for f in saved_filters:
                        await self.set_filter(
                            filter_id=f["id"],
                            freq=f["freq"],
                            gain=f.get("gain", 0),
                            q=f.get("q", 1.0),
                            filter_type=f.get("type", "Peaking")
                        )
                    self._filters = saved_filters

                # 2. Restore compressor settings
                saved_compressor = await self.settings_service.get_setting("dsp.compressor")
                if saved_compressor:
                    await self.set_compressor(**saved_compressor)

                # 3. Restore loudness settings
                saved_loudness = await self.settings_service.get_setting("dsp.loudness")
                if saved_loudness:
                    await self.set_loudness(**saved_loudness)

            self.logger.info("DSP effects restored from settings")
            await self._broadcast_event("effects_restored", {"bypassed": False})
            return True

        except Exception as e:
            self.logger.error(f"Error restoring effects: {e}")
            return False

    # === Configuration Persistence ===

    async def _load_saved_config(self) -> None:
        """Load saved DSP configuration from settings"""
        if not self.settings_service:
            return

        try:
            # Load filters
            saved_filters = await self.settings_service.get_setting("dsp.filters")
            if saved_filters:
                self._filters = saved_filters
                self.logger.info(f"Loaded {len(self._filters)} saved DSP filters")

            # Load compressor
            saved_compressor = await self.settings_service.get_setting("dsp.compressor")
            if saved_compressor:
                self._compressor.update(saved_compressor)
                self.logger.info("Loaded saved compressor settings")

            # Load loudness
            saved_loudness = await self.settings_service.get_setting("dsp.loudness")
            if saved_loudness:
                self._loudness.update(saved_loudness)
                self.logger.info("Loaded saved loudness settings")

        except Exception as e:
            self.logger.error(f"Error loading saved config: {e}")

    async def _apply_saved_preset(self) -> None:
        """Apply saved preset on startup"""
        if not self.settings_service:
            return
        try:
            preset_id = await self.settings_service.get_setting("dsp.active_preset")
            if preset_id:
                gains = await self._get_preset_gains(preset_id)
                if gains:
                    await self._apply_gains(gains)
        except Exception as e:
            self.logger.error(f"Error applying saved preset: {e}")

    async def save_current_config(self) -> bool:
        """Save current configuration to settings"""
        if not self.settings_service:
            return False

        try:
            await self.settings_service.set_setting("dsp.filters", self._filters)
            await self.settings_service.set_setting("dsp.compressor", self._compressor)
            await self.settings_service.set_setting("dsp.loudness", self._loudness)
            return True
        except Exception as e:
            self.logger.error(f"Error saving config: {e}")
            return False

    async def _save_filters(self) -> None:
        """Save filters to settings (used by set_filter for auto-persistence)"""
        if self.settings_service:
            try:
                await self.settings_service.set_setting("dsp.filters", self._filters)
            except Exception as e:
                self.logger.error(f"Error saving filters: {e}")

    # === Event Broadcasting ===

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast DSP event via state machine and EventBus"""
        # Broadcast via state_machine for WebSocket clients
        if self.state_machine:
            await self.state_machine.broadcast_event("dsp", event_type, data)

        # Also emit via EventBus for internal subscribers
        if self.event_bus:
            self.event_bus.emit(f"dsp.{event_type}", data)

    # === Cleanup ===

    async def cleanup(self) -> None:
        """Clean up resources"""
        self.logger.info("Cleaning up CamillaDSP service...")

        # Cancel reconnect task if running
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect from daemon
        await self.disconnect()

        self.logger.info("CamillaDSP service cleanup complete")
