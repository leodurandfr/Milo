# backend/infrastructure/services/camilladsp_service.py
"""
CamillaDSP service for Milo - WebSocket client for CamillaDSP daemon
Replaces alsaequal with full parametric EQ capabilities
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional
from enum import Enum


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
    CamillaDSP WebSocket client service

    Manages connection to CamillaDSP daemon and provides methods for:
    - Parametric EQ configuration
    - Real-time filter updates
    - Status monitoring
    - Preset management
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 1234
    RECONNECT_DELAY = 5.0
    COMMAND_TIMEOUT = 5.0

    def __init__(self, settings_service=None, host: str = None, port: int = None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.host = host or self.DEFAULT_HOST
        self.port = port or self.DEFAULT_PORT

        self._client = None
        self._state = DspState.DISCONNECTED
        self._lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._connected = False

        # State machine reference (set by container)
        self.state_machine = None

        # Current configuration cache
        self._current_config: Dict[str, Any] = {}
        self._filters: List[Dict[str, Any]] = []

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
        self._delay: Dict[str, Any] = {
            "enabled": False,
            "left": 0.0,
            "right": 0.0
        }
        self._volume: Dict[str, Any] = {
            "main": 0.0,  # dB
            "mute": False
        }

    def set_state_machine(self, state_machine) -> None:
        """Sets reference to UnifiedAudioStateMachine for event broadcasting"""
        self.state_machine = state_machine

    @property
    def state(self) -> DspState:
        """Current DSP state"""
        return self._state

    @property
    def connected(self) -> bool:
        """Whether we have an active connection to CamillaDSP"""
        return self._connected and self._client is not None

    def is_volume_control_available(self) -> bool:
        """
        Check if DSP can be used for volume control.
        Returns True when connected and ready (inactive, running, or paused).
        Volume can be set even when no audio is playing (inactive state).
        """
        return self._connected and self._state in (DspState.INACTIVE, DspState.RUNNING, DspState.PAUSED)

    async def initialize(self) -> bool:
        """Initialize the CamillaDSP service"""
        try:
            self.logger.info("Initializing CamillaDSP service...")

            # Load saved configuration from settings
            await self._load_saved_config()

            # Attempt initial connection
            connected = await self.connect()

            if connected:
                self.logger.info("CamillaDSP service initialized and connected")
            else:
                self.logger.warning("CamillaDSP service initialized but not connected (daemon may not be running)")

            return True

        except Exception as e:
            self.logger.error(f"Error initializing CamillaDSP service: {e}")
            return False

    async def connect(self) -> bool:
        """Connect to CamillaDSP daemon via WebSocket"""
        async with self._lock:
            if self._connected:
                return True

            try:
                # Import pycamilladsp here to handle cases where it's not installed
                try:
                    from camilladsp import CamillaClient
                except ImportError:
                    self.logger.error("pycamilladsp not installed. Run: pip install camilladsp")
                    return False

                self._client = CamillaClient(self.host, self.port)

                # pycamilladsp is synchronous, wrap in executor
                await asyncio.get_event_loop().run_in_executor(
                    None, self._client.connect
                )

                self._connected = True
                self._state = await self._get_daemon_state()

                self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}, state: {self._state}")

                # Broadcast state change event (frontend listens for 'state_changed')
                await self._broadcast_event("state_changed", {"state": self._state.value})

                return True

            except Exception as e:
                self._connected = False
                self._state = DspState.DISCONNECTED
                self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
                return False

    async def disconnect(self) -> None:
        """Disconnect from CamillaDSP daemon"""
        async with self._lock:
            if self._client:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._client.disconnect
                    )
                except Exception as e:
                    self.logger.warning(f"Error disconnecting from CamillaDSP: {e}")

                self._client = None

            self._connected = False
            self._state = DspState.DISCONNECTED

            # Broadcast state change event (frontend listens for 'state_changed')
            await self._broadcast_event("state_changed", {"state": self._state.value})

    async def _get_daemon_state(self) -> DspState:
        """Get current daemon state"""
        if not self._client:
            return DspState.DISCONNECTED

        try:
            # pycamilladsp v3 API: general.state() returns ProcessingState enum
            state = await asyncio.get_event_loop().run_in_executor(
                None, self._client.general.state
            )

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
        """Get comprehensive DSP status"""
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
                "delay": self._delay,
                "volume": await self.get_volume(),
            }

            # Add rate/buffer info if running
            if state == DspState.RUNNING:
                try:
                    # pycamilladsp v3 API: rate.capture()
                    rate = await asyncio.get_event_loop().run_in_executor(
                        None, self._client.rate.capture
                    )
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
        """Get CamillaDSP config from active or file if inactive"""
        config = await asyncio.get_event_loop().run_in_executor(
            None, self._client.config.active
        )
        if config is None:
            config_path = await asyncio.get_event_loop().run_in_executor(
                None, self._client.config.file_path
            )
            if config_path:
                config = await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=config_path: self._client.config.read_and_parse_file(p)
                )
        return config

    # === Filter Management ===

    async def get_filters(self) -> List[Dict[str, Any]]:
        """Get current filter configuration"""
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
        """Parse CamillaDSP filter config to simplified format"""
        result = []

        for name, filter_data in filters_config.items():
            # Only include EQ band filters (skip advanced filters like loudness, compressor, delay)
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
                         enabled: bool = True, persist: bool = True) -> bool:
        """Update a single filter band. Set persist=False during bypass operations."""
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
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda c=config: self._client.config.set_active(c)
                )

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

            return True

        except Exception as e:
            self.logger.error(f"Error setting filter {filter_id}: {e}")
            return False

    async def add_filter(self, filter_id: str, freq: float = 1000,
                         gain: float = 0, q: float = 1.0,
                         filter_type: str = "Peaking") -> bool:
        """Add a new filter band"""
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

            # Set updated config (pycamilladsp v3 API)
            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

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
        """Remove a filter band"""
        if not self._connected:
            return False

        try:
            config = await self._get_config()

            if config and "filters" in config and filter_id in config["filters"]:
                del config["filters"][filter_id]

                await asyncio.get_event_loop().run_in_executor(
                    None, lambda c=config: self._client.config.set_active(c)
                )

                # Update local cache
                self._filters = [f for f in self._filters if f["id"] != filter_id]

                await self._broadcast_event("filter_removed", {"id": filter_id})

                return True

            return False

        except Exception as e:
            self.logger.error(f"Error removing filter: {e}")
            return False

    async def reset_filters(self) -> bool:
        """Reset all filters to flat (0 dB gain)"""
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
        """Get current volume settings"""
        if not self._connected:
            return self._volume

        try:
            # pycamilladsp v3 API
            volume = await asyncio.get_event_loop().run_in_executor(
                None, self._client.volume.main_volume
            )
            mute = await asyncio.get_event_loop().run_in_executor(
                None, self._client.volume.main_mute
            )

            self._volume = {"main": volume, "mute": mute}
            return self._volume

        except Exception as e:
            self.logger.debug(f"Error getting volume: {e}")
            return self._volume

    async def set_volume(self, volume: float) -> bool:
        """Set main volume in dB (-100 to 0)"""
        if not self._connected:
            return False

        try:
            # pycamilladsp v3 API
            await asyncio.get_event_loop().run_in_executor(
                None, lambda v=volume: self._client.volume.set_main_volume(v)
            )

            self._volume["main"] = volume
            # Note: Volume broadcast is handled by VolumeService._schedule_broadcast()
            # to avoid duplicate broadcasts and ensure unified volume:volume_changed events

            return True

        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, muted: bool) -> bool:
        """Set mute state"""
        if not self._connected:
            return False

        try:
            # pycamilladsp v3 API
            await asyncio.get_event_loop().run_in_executor(
                None, lambda m=muted: self._client.volume.set_main_mute(m)
            )

            self._volume["mute"] = muted
            await self._broadcast_event("mute_changed", {"muted": muted})

            return True

        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False

    # === Pipeline Management ===

    def _add_filter_to_pipeline(self, config: Dict, filter_name: str, channels: List[int] = None) -> None:
        """Add a filter to the pipeline for specified channels (both if None)"""
        if "pipeline" not in config:
            config["pipeline"] = []

        if channels is None:
            channels = [0, 1]

        for channel in channels:
            # Find existing Filter step for this channel
            filter_step = None
            for step in config["pipeline"]:
                if step.get("type") == "Filter" and channel in step.get("channels", []):
                    filter_step = step
                    break

            if filter_step:
                # Add filter to existing step if not already present
                if filter_name not in filter_step.get("names", []):
                    filter_step["names"].append(filter_name)
            else:
                # Create new Filter step for this channel
                config["pipeline"].append({
                    "type": "Filter",
                    "channels": [channel],
                    "names": [filter_name]
                })

    def _remove_filter_from_pipeline(self, config: Dict, filter_name: str) -> None:
        """Remove a filter from all pipeline steps"""
        if "pipeline" not in config:
            return

        for step in config["pipeline"]:
            if step.get("type") == "Filter" and "names" in step:
                if filter_name in step["names"]:
                    step["names"].remove(filter_name)

    def _add_processor_to_pipeline(self, config: Dict, processor_name: str) -> None:
        """Add a processor to the pipeline"""
        if "pipeline" not in config:
            config["pipeline"] = []

        # Check if processor already in pipeline
        for step in config["pipeline"]:
            if step.get("type") == "Processor" and step.get("name") == processor_name:
                return

        # Add processor at the end of pipeline (after filters)
        config["pipeline"].append({
            "type": "Processor",
            "name": processor_name
        })

    def _remove_processor_from_pipeline(self, config: Dict, processor_name: str) -> None:
        """Remove a processor from the pipeline"""
        if "pipeline" not in config:
            return

        config["pipeline"] = [
            step for step in config["pipeline"]
            if not (step.get("type") == "Processor" and step.get("name") == processor_name)
        ]

    # === Compressor ===

    async def get_compressor(self) -> Dict[str, Any]:
        """Get compressor settings"""
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

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

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
        """Get loudness compensation settings"""
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

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            await self._broadcast_event("loudness_changed", self._loudness)

            # Persist loudness settings (skip during bypass operations)
            if persist and self.settings_service:
                await self.settings_service.set_setting("dsp.loudness", self._loudness)

            return True

        except Exception as e:
            self.logger.error(f"Error setting loudness: {e}")
            return False

    # === Crossover Filter (for Subwoofer Integration) ===

    async def get_crossover_filter(self) -> Dict[str, Any]:
        """Get crossover highpass filter settings"""
        if not self._connected:
            return {"enabled": False, "frequency": 80, "q": 0.707}

        try:
            config = await self._get_config()

            if config and "filters" in config and "crossover_highpass" in config["filters"]:
                filter_data = config["filters"]["crossover_highpass"]
                params = filter_data.get("parameters", {})
                return {
                    "enabled": True,
                    "frequency": params.get("freq", 80),
                    "q": params.get("q", 0.707)
                }

            return {"enabled": False, "frequency": 80, "q": 0.707}

        except Exception as e:
            self.logger.error(f"Error getting crossover filter: {e}")
            return {"enabled": False, "frequency": 80, "q": 0.707}

    async def set_crossover_filter(
        self,
        enabled: bool,
        frequency: float = 80.0,
        q: float = 0.707
    ) -> bool:
        """
        Apply or remove crossover highpass filter for subwoofer integration.

        When enabled, applies a Butterworth highpass filter at the specified
        frequency to remove bass from speakers (bass handled by subwoofer).

        Args:
            enabled: Whether to enable the highpass filter
            frequency: Crossover frequency in Hz (default 80, typical range 40-200)
            q: Filter Q factor (default 0.707 = Butterworth, flattest passband)

        Returns:
            True if successful, False otherwise
        """
        if not self._connected:
            self.logger.warning("Cannot set crossover filter: not connected")
            return False

        try:
            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            if "filters" not in config:
                config["filters"] = {}

            if enabled:
                # Add highpass crossover filter
                config["filters"]["crossover_highpass"] = {
                    "type": "Biquad",
                    "parameters": {
                        "type": "Highpass",
                        "freq": frequency,
                        "q": q
                    }
                }
                # Add to pipeline for both channels
                self._add_filter_to_pipeline(config, "crossover_highpass")
                self.logger.info(f"Crossover highpass filter enabled at {frequency} Hz (Q={q})")
            else:
                # Remove crossover filter
                if "crossover_highpass" in config["filters"]:
                    del config["filters"]["crossover_highpass"]
                self._remove_filter_from_pipeline(config, "crossover_highpass")
                self.logger.info("Crossover highpass filter disabled")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            await self._broadcast_event("crossover_changed", {
                "enabled": enabled,
                "frequency": frequency,
                "q": q
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting crossover filter: {e}")
            return False

    async def set_lowpass_filter(
        self,
        enabled: bool,
        frequency: float = 80.0,
        q: float = 0.707
    ) -> bool:
        """
        Apply or remove lowpass filter for subwoofer.

        When enabled, applies a Butterworth lowpass filter at the specified
        frequency to send only bass to the subwoofer.

        Args:
            enabled: Whether to enable the lowpass filter
            frequency: Cutoff frequency in Hz (default 80, typical range 40-200)
            q: Filter Q factor (default 0.707 = Butterworth, flattest passband)

        Returns:
            True if successful, False otherwise
        """
        if not self._connected:
            self.logger.warning("Cannot set lowpass filter: not connected")
            return False

        try:
            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            if "filters" not in config:
                config["filters"] = {}

            if enabled:
                # Add lowpass filter for subwoofer
                config["filters"]["crossover_lowpass"] = {
                    "type": "Biquad",
                    "parameters": {
                        "type": "Lowpass",
                        "freq": frequency,
                        "q": q
                    }
                }
                # Add to pipeline for both channels
                self._add_filter_to_pipeline(config, "crossover_lowpass")
                self.logger.info(f"Lowpass filter enabled at {frequency} Hz (Q={q})")
            else:
                # Remove lowpass filter
                if "crossover_lowpass" in config["filters"]:
                    del config["filters"]["crossover_lowpass"]
                self._remove_filter_from_pipeline(config, "crossover_lowpass")
                self.logger.info("Lowpass filter disabled")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            await self._broadcast_event("lowpass_changed", {
                "enabled": enabled,
                "frequency": frequency,
                "q": q
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting lowpass filter: {e}")
            return False

    # === Channel Delay ===

    async def get_delay(self) -> Dict[str, float]:
        """Get channel delay settings in milliseconds"""
        return self._delay.copy()

    async def set_delay(self, enabled: bool = None, left: float = None, right: float = None, persist: bool = True) -> bool:
        """Set channel delay in milliseconds (0-50ms). Set persist=False during bypass operations."""
        if enabled is not None:
            self._delay["enabled"] = enabled
        if left is not None:
            self._delay["left"] = max(0, min(50, left))
        if right is not None:
            self._delay["right"] = max(0, min(50, right))

        if not self._connected:
            return True

        try:
            # CamillaDSP uses sample count for delay
            # At 48kHz, 1ms = 48 samples
            sample_rate = 48000

            config = await self._get_config()

            if config is None:
                config = {"filters": {}, "pipeline": []}

            # Add delay filters if needed
            if "filters" not in config:
                config["filters"] = {}

            # Only apply delay if enabled
            if self._delay["enabled"] and self._delay["left"] > 0:
                left_samples = int(self._delay["left"] * sample_rate / 1000)
                config["filters"]["delay_left"] = {
                    "type": "Delay",
                    "parameters": {
                        "delay": left_samples,
                        "unit": "samples"
                    }
                }
                # Add delay_left to pipeline for channel 0 only
                self._add_filter_to_pipeline(config, "delay_left", channels=[0])
            else:
                if "delay_left" in config.get("filters", {}):
                    del config["filters"]["delay_left"]
                self._remove_filter_from_pipeline(config, "delay_left")

            if self._delay["enabled"] and self._delay["right"] > 0:
                right_samples = int(self._delay["right"] * sample_rate / 1000)
                config["filters"]["delay_right"] = {
                    "type": "Delay",
                    "parameters": {
                        "delay": right_samples,
                        "unit": "samples"
                    }
                }
                # Add delay_right to pipeline for channel 1 only
                self._add_filter_to_pipeline(config, "delay_right", channels=[1])
            else:
                if "delay_right" in config.get("filters", {}):
                    del config["filters"]["delay_right"]
                self._remove_filter_from_pipeline(config, "delay_right")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            await self._broadcast_event("delay_changed", self._delay)

            # Persist delay settings (skip during bypass operations)
            if persist and self.settings_service:
                await self.settings_service.set_setting("dsp.delay", self._delay)

            return True

        except Exception as e:
            self.logger.error(f"Error setting delay: {e}")
            return False

    # === Level Monitoring ===

    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak/RMS)"""
        if not self._connected:
            return {"available": False}

        try:
            # pycamilladsp v3 API
            capture_levels = await asyncio.get_event_loop().run_in_executor(
                None, self._client.levels.capture_peak
            )

            playback_levels = await asyncio.get_event_loop().run_in_executor(
                None, self._client.levels.playback_peak
            )

            return {
                "available": True,
                "input_peak": capture_levels,
                "output_peak": playback_levels
            }

        except Exception as e:
            self.logger.debug(f"Error getting levels: {e}")
            return {"available": False}

    # === Preset Management ===

    async def save_preset(self, name: str) -> bool:
        """Save current configuration as a preset"""
        if not self.settings_service:
            return False

        try:
            preset_data = {
                "name": name,
                "filters": self._filters.copy()
            }

            # Get existing presets
            presets = await self.settings_service.get_setting("dsp.presets") or {}
            presets[name] = preset_data

            await self.settings_service.set_setting("dsp.presets", presets)

            self.logger.info(f"Saved DSP preset: {name}")
            return True

        except Exception as e:
            self.logger.error(f"Error saving preset: {e}")
            return False

    async def load_preset(self, name: str) -> bool:
        """Load a preset configuration"""
        if not self.settings_service:
            return False

        try:
            presets = await self.settings_service.get_setting("dsp.presets") or {}

            if name not in presets:
                self.logger.warning(f"Preset not found: {name}")
                return False

            preset_data = presets[name]

            # Apply filters from preset
            for filter_data in preset_data.get("filters", []):
                await self.set_filter(
                    filter_id=filter_data["id"],
                    freq=filter_data["freq"],
                    gain=filter_data["gain"],
                    q=filter_data.get("q", 1.0),
                    filter_type=filter_data.get("type", "Peaking")
                )

            # Update active preset in settings
            await self.settings_service.set_setting("dsp.active_preset", name)

            await self._broadcast_event("preset_loaded", {"name": name})

            self.logger.info(f"Loaded DSP preset: {name}")
            return True

        except Exception as e:
            self.logger.error(f"Error loading preset: {e}")
            return False

    async def list_presets(self) -> List[str]:
        """List available preset names"""
        if not self.settings_service:
            return []

        try:
            presets = await self.settings_service.get_setting("dsp.presets") or {}
            return list(presets.keys())
        except Exception:
            return []

    async def delete_preset(self, name: str) -> bool:
        """Delete a preset"""
        if not self.settings_service:
            return False

        try:
            presets = await self.settings_service.get_setting("dsp.presets") or {}

            if name in presets:
                del presets[name]
                await self.settings_service.set_setting("dsp.presets", presets)

                # Clear active preset if it was deleted
                active = await self.settings_service.get_setting("dsp.active_preset")
                if active == name:
                    await self.settings_service.set_setting("dsp.active_preset", None)

                return True

            return False

        except Exception as e:
            self.logger.error(f"Error deleting preset: {e}")
            return False

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

            # 4. Reset delay to 0 (persist=False to keep settings for restore)
            await self.set_delay(left=0, right=0, persist=False)

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

                # 4. Restore delay settings
                saved_delay = await self.settings_service.get_setting("dsp.delay")
                if saved_delay:
                    await self.set_delay(**saved_delay)

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

            # Load delay
            saved_delay = await self.settings_service.get_setting("dsp.delay")
            if saved_delay:
                self._delay.update(saved_delay)
                self.logger.info("Loaded saved delay settings")

        except Exception as e:
            self.logger.error(f"Error loading saved config: {e}")

    async def save_current_config(self) -> bool:
        """Save current configuration to settings"""
        if not self.settings_service:
            return False

        try:
            await self.settings_service.set_setting("dsp.filters", self._filters)
            await self.settings_service.set_setting("dsp.compressor", self._compressor)
            await self.settings_service.set_setting("dsp.loudness", self._loudness)
            await self.settings_service.set_setting("dsp.delay", self._delay)
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
        """Broadcast DSP event via state machine"""
        if self.state_machine:
            await self.state_machine.broadcast_event("dsp", event_type, data)

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
