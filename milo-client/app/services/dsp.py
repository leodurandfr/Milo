"""
CamillaDSP service for Milo Client.

Controls local CamillaDSP daemon via WebSocket for:
- Parametric EQ (filters)
- Compressor
- Loudness compensation
- Channel delay
- Volume/mute control
- Crossover filters (highpass/lowpass)
"""
import asyncio
import aiofiles
import logging
import time
import yaml
from typing import Dict, Any, Optional, List

# Try to import CamillaDSP client
try:
    from camilladsp import CamillaClient
    CAMILLADSP_AVAILABLE = True
except ImportError:
    CAMILLADSP_AVAILABLE = False

# Constants
CAMILLADSP_HOST = "127.0.0.1"
CAMILLADSP_PORT = 1234
CONFIG_FILE = "/var/lib/milo-client/camilladsp/config.yml"


class DSPService:
    """
    CamillaDSP control service.

    Manages connection to local CamillaDSP daemon and provides methods for:
    - EQ filter configuration
    - Compressor settings
    - Loudness compensation
    - Channel delay
    - Volume/mute control
    - Crossover filters
    """

    def __init__(self, host: str = None, port: int = None, config_file: str = None):
        self.logger = logging.getLogger(f"{__name__}.DSPService")
        self.host = host or CAMILLADSP_HOST
        self.port = port or CAMILLADSP_PORT
        self.config_file = config_file or CONFIG_FILE

        self._client = None
        self._connected = False

        # Cached state
        self._filters: List[Dict[str, Any]] = []
        self._compressor = {
            "enabled": False,
            "threshold": -20.0,
            "ratio": 4.0,
            "attack": 10.0,
            "release": 100.0,
            "makeup_gain": 0.0
        }
        self._loudness = {
            "enabled": False,
            "high_boost": 5.0,
            "low_boost": 8.0
        }
        self._delay = {"left": 0.0, "right": 0.0}
        self._volume = {"main": 0.0, "mute": True}  # Matches CamillaDSP startup state (-m flag)
        self._crossover = {"enabled": False, "frequency": 80.0, "q": 0.707}
        self._lowpass = {"enabled": False, "frequency": 80.0, "q": 0.707}

    @property
    def connected(self) -> bool:
        """Returns whether CamillaDSP is connected."""
        return self._connected

    @property
    def available(self) -> bool:
        """Returns whether CamillaDSP client library is available."""
        return CAMILLADSP_AVAILABLE

    @property
    def compressor(self) -> Dict[str, Any]:
        """Returns compressor state."""
        return self._compressor

    @property
    def loudness(self) -> Dict[str, Any]:
        """Returns loudness state."""
        return self._loudness

    @property
    def delay(self) -> Dict[str, Any]:
        """Returns delay state."""
        return self._delay

    @property
    def volume_state(self) -> Dict[str, Any]:
        """Returns volume state."""
        return self._volume

    @property
    def crossover(self) -> Dict[str, Any]:
        """Returns crossover state."""
        return self._crossover

    @property
    def lowpass(self) -> Dict[str, Any]:
        """Returns lowpass state."""
        return self._lowpass

    async def connect(self) -> bool:
        """Connect to local CamillaDSP."""
        if not CAMILLADSP_AVAILABLE:
            self.logger.warning("CamillaDSP client library not available")
            return False

        try:
            self._client = CamillaClient(self.host, self.port)
            await asyncio.get_event_loop().run_in_executor(
                None, self._client.connect
            )
            self._connected = True
            self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}")

            # Load current state from CamillaDSP config
            await self._load_state_from_config()

            return True
        except Exception as e:
            self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
            self._connected = False
            return False

    async def _load_state_from_config(self):
        """Load compressor/loudness/delay state from current CamillaDSP config."""
        try:
            config = await self._get_config()
            if not config:
                return

            # Check for compressor in processors
            if "processors" in config and "compressor" in config["processors"]:
                proc = config["processors"]["compressor"]
                params = proc.get("parameters", {})
                self._compressor = {
                    "enabled": True,
                    "threshold": params.get("threshold", -20.0),
                    "ratio": params.get("factor", 4.0),
                    "attack": params.get("attack", 0.01) * 1000,  # Convert to ms
                    "release": params.get("release", 0.1) * 1000,
                    "makeup_gain": params.get("makeup_gain", 0.0)
                }
                self.logger.info("Loaded compressor state from config")
            else:
                self._compressor["enabled"] = False

            # Check for loudness filters
            if "filters" in config:
                has_loudness_low = "loudness_low" in config["filters"]
                has_loudness_high = "loudness_high" in config["filters"]

                if has_loudness_low and has_loudness_high:
                    self._loudness["enabled"] = True
                    low_params = config["filters"]["loudness_low"].get("parameters", {})
                    high_params = config["filters"]["loudness_high"].get("parameters", {})
                    self._loudness["low_boost"] = low_params.get("gain", 8.0)
                    self._loudness["high_boost"] = high_params.get("gain", 5.0)
                    self.logger.info("Loaded loudness state from config")
                else:
                    self._loudness["enabled"] = False

                # Check for delay filters
                if "delay_left" in config["filters"]:
                    delay_params = config["filters"]["delay_left"].get("parameters", {})
                    delay_samples = delay_params.get("delay", 0)
                    self._delay["left"] = delay_samples * 1000 / 48000  # Convert to ms
                else:
                    self._delay["left"] = 0.0

                if "delay_right" in config["filters"]:
                    delay_params = config["filters"]["delay_right"].get("parameters", {})
                    delay_samples = delay_params.get("delay", 0)
                    self._delay["right"] = delay_samples * 1000 / 48000
                else:
                    self._delay["right"] = 0.0

                if self._delay["left"] > 0 or self._delay["right"] > 0:
                    self.logger.info(
                        f"Loaded delay state from config: L={self._delay['left']:.1f}ms R={self._delay['right']:.1f}ms"
                    )

        except Exception as e:
            self.logger.warning(f"Could not load state from config: {e}")

    async def get_status(self) -> Dict[str, Any]:
        """Get DSP status."""
        if not self._connected:
            await self.connect()

        if not self._connected:
            return {"available": False, "message": "CamillaDSP not connected"}

        try:
            state = await asyncio.get_event_loop().run_in_executor(
                None, self._client.general.state
            )
            state_str = str(state).split('.')[-1].lower()

            return {
                "available": True,
                "state": state_str,
                "filters": await self.get_filters(),
                "compressor": self._compressor,
                "loudness": self._loudness,
                "delay": self._delay
            }
        except Exception as e:
            self.logger.error(f"Error getting DSP status: {e}")
            return {"available": False, "error": str(e)}

    async def _get_config(self) -> Optional[Dict[str, Any]]:
        """Get CamillaDSP config."""
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

    async def _save_config_to_file(self, config: Dict[str, Any]) -> bool:
        """Save config to disk for persistence."""
        try:
            config_yaml = yaml.dump(config, default_flow_style=False, allow_unicode=True)

            async with aiofiles.open(self.config_file, 'w') as f:
                await f.write(config_yaml)

            self.logger.info("Config saved to disk")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save config to disk: {e}")
            return False

    async def get_filters(self) -> List[Dict[str, Any]]:
        """Get current EQ filter configuration."""
        if not self._connected:
            return self._filters

        try:
            config = await self._get_config()
            if config and "filters" in config:
                self._filters = []
                for name, filter_data in config["filters"].items():
                    if not name.startswith("eq_band_"):
                        continue
                    params = filter_data.get("parameters", {})
                    self._filters.append({
                        "id": name,
                        "type": params.get("type", "Peaking"),
                        "freq": params.get("freq", 1000),
                        "gain": params.get("gain", 0),
                        "q": params.get("q", 1.0),
                        "enabled": True
                    })
                self._filters.sort(key=lambda f: f["id"])
            return self._filters
        except Exception as e:
            self.logger.error(f"Error getting filters: {e}")
            return self._filters

    async def set_filter(self, filter_id: str, gain: float,
                         freq: float = None, q: float = None) -> bool:
        """Update a filter band."""
        if not self._connected:
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

            if "filters" not in config or filter_id not in config["filters"]:
                return False

            params = config["filters"][filter_id]["parameters"]
            params["gain"] = gain
            if freq is not None:
                params["freq"] = freq
            if q is not None:
                params["q"] = q

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            # Save to disk for persistence
            await self._save_config_to_file(config)

            return True
        except Exception as e:
            self.logger.error(f"Error setting filter {filter_id}: {e}")
            return False

    async def set_filters_batch(self, filters: List[dict]) -> dict:
        """
        Update multiple filters in one operation with a single disk save.

        Args:
            filters: List of filter dicts with keys: id, gain, freq (optional), q (optional)

        Returns:
            dict with success status and number of filters applied
        """
        if not self._connected:
            return {"success": False, "applied": 0}

        try:
            config = await self._get_config()
            if not config or "filters" not in config:
                return {"success": False, "applied": 0}

            applied = 0
            for f in filters:
                filter_id = f.get("id")
                if filter_id and filter_id in config["filters"]:
                    params = config["filters"][filter_id]["parameters"]
                    if "gain" in f:
                        params["gain"] = f["gain"]
                    if "freq" in f:
                        params["freq"] = f["freq"]
                    if "q" in f:
                        params["q"] = f["q"]
                    applied += 1

            # Apply to CamillaDSP
            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            # Single disk save
            await self._save_config_to_file(config)

            return {"success": True, "applied": applied}
        except Exception as e:
            self.logger.error(f"Error in batch filter update: {e}")
            return {"success": False, "applied": 0, "error": str(e)}

    async def set_compressor(self, enabled: bool = None, threshold: float = None,
                             ratio: float = None, attack: float = None,
                             release: float = None, makeup_gain: float = None) -> bool:
        """Update compressor settings."""
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

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set compressor: CamillaDSP not connected")
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

            if not config.get("processors"):
                config["processors"] = {}

            if self._compressor["enabled"]:
                config["processors"]["compressor"] = {
                    "type": "Compressor",
                    "parameters": {
                        "channels": 2,
                        "threshold": self._compressor["threshold"],
                        "factor": self._compressor["ratio"],
                        "attack": self._compressor["attack"] / 1000.0,
                        "release": self._compressor["release"] / 1000.0,
                        "makeup_gain": self._compressor["makeup_gain"]
                    }
                }
                self._add_processor_to_pipeline(config, "compressor")
            else:
                if "compressor" in config.get("processors", {}):
                    del config["processors"]["compressor"]
                self._remove_processor_from_pipeline(config, "compressor")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            # Save to disk for persistence
            await self._save_config_to_file(config)

            return True
        except Exception as e:
            self.logger.error(f"Error setting compressor: {e}")
            return False

    async def set_loudness(self, enabled: bool = None,
                           high_boost: float = None, low_boost: float = None) -> bool:
        """Update loudness settings."""
        if enabled is not None:
            self._loudness["enabled"] = enabled
        if high_boost is not None:
            self._loudness["high_boost"] = high_boost
        if low_boost is not None:
            self._loudness["low_boost"] = low_boost

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set loudness: CamillaDSP not connected")
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

            if "filters" not in config:
                config["filters"] = {}

            if self._loudness["enabled"]:
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
                self._add_filter_to_pipeline(config, "loudness_low")
                self._add_filter_to_pipeline(config, "loudness_high")
            else:
                for name in ["loudness_low", "loudness_high"]:
                    if name in config.get("filters", {}):
                        del config["filters"][name]
                    self._remove_filter_from_pipeline(config, name)

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting loudness: {e}")
            return False

    async def set_delay(self, left: float = None, right: float = None) -> bool:
        """Set channel delay in milliseconds."""
        if left is not None:
            self._delay["left"] = max(0, min(50, left))
        if right is not None:
            self._delay["right"] = max(0, min(50, right))

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set delay: CamillaDSP not connected")
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

            sample_rate = 48000

            if "filters" not in config:
                config["filters"] = {}

            if self._delay["left"] > 0:
                left_samples = int(self._delay["left"] * sample_rate / 1000)
                config["filters"]["delay_left"] = {
                    "type": "Delay",
                    "parameters": {"delay": left_samples, "unit": "samples"}
                }
                self._add_filter_to_pipeline(config, "delay_left", channels=[0])
            else:
                if "delay_left" in config.get("filters", {}):
                    del config["filters"]["delay_left"]
                self._remove_filter_from_pipeline(config, "delay_left")

            if self._delay["right"] > 0:
                right_samples = int(self._delay["right"] * sample_rate / 1000)
                config["filters"]["delay_right"] = {
                    "type": "Delay",
                    "parameters": {"delay": right_samples, "unit": "samples"}
                }
                self._add_filter_to_pipeline(config, "delay_right", channels=[1])
            else:
                if "delay_right" in config.get("filters", {}):
                    del config["filters"]["delay_right"]
                self._remove_filter_from_pipeline(config, "delay_right")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting delay: {e}")
            return False

    async def get_volume(self) -> Dict[str, Any]:
        """Get current DSP volume settings."""
        if self._connected and self._client:
            try:
                volume = await asyncio.get_event_loop().run_in_executor(
                    None, self._client.volume.main_volume
                )
                mute = await asyncio.get_event_loop().run_in_executor(
                    None, self._client.volume.main_mute
                )
                self._volume["main"] = volume
                self._volume["mute"] = mute
            except Exception as e:
                self.logger.warning(f"Error getting volume from CamillaDSP: {e}")
        return self._volume

    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak values for input/output)."""
        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected or not self._client:
            return {"available": False}

        try:
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

    async def set_volume(self, volume: float) -> bool:
        """Set DSP volume in dB."""
        self._volume["main"] = max(-80, min(0, volume))

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set volume: CamillaDSP not connected")
            return False

        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda v=self._volume["main"]: self._client.volume.set_main_volume(v)
            )
            self.logger.info(f"[{time.time():.3f}] VOLUME_SET: Volume set to {self._volume['main']:.1f} dB")
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, muted: bool) -> bool:
        """Set DSP mute state."""
        self._volume["mute"] = muted

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set mute: CamillaDSP not connected")
            return False

        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda m=muted: self._client.volume.set_main_mute(m)
            )
            self.logger.info(f"[{time.time():.3f}] MUTE_SET: Mute set to {muted}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False

    async def set_crossover(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """
        Set crossover highpass filter for subwoofer integration.

        When enabled, applies a Butterworth highpass filter at the specified
        frequency to remove bass from speakers (bass handled by subwoofer).

        Args:
            enabled: Whether to enable the highpass filter
            frequency: Crossover frequency in Hz (default 80)
            q: Filter Q factor (default 0.707 = Butterworth)

        Returns:
            True if successful, False otherwise
        """
        self._crossover["enabled"] = enabled
        self._crossover["frequency"] = frequency
        self._crossover["q"] = q

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set crossover: CamillaDSP not connected")
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

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
                self._add_filter_to_pipeline(config, "crossover_highpass")
                self.logger.info(f"Crossover highpass filter enabled at {frequency} Hz (Q={q})")
            else:
                # Remove crossover filter
                if "crossover_highpass" in config.get("filters", {}):
                    del config["filters"]["crossover_highpass"]
                self._remove_filter_from_pipeline(config, "crossover_highpass")
                self.logger.info("Crossover highpass filter disabled")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            # Save to disk for persistence
            await self._save_config_to_file(config)

            return True

        except Exception as e:
            self.logger.error(f"Error setting crossover: {e}")
            return False

    async def set_lowpass(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """
        Set lowpass filter for subwoofer.

        When enabled, applies a Butterworth lowpass filter at the specified
        frequency to send only bass to the subwoofer.

        Args:
            enabled: Whether to enable the lowpass filter
            frequency: Cutoff frequency in Hz (default 80)
            q: Filter Q factor (default 0.707 = Butterworth)

        Returns:
            True if successful, False otherwise
        """
        self._lowpass["enabled"] = enabled
        self._lowpass["frequency"] = frequency
        self._lowpass["q"] = q

        # Try to connect if not connected
        if not self._connected:
            await self.connect()

        if not self._connected:
            self.logger.warning("Cannot set lowpass: CamillaDSP not connected")
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

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
                self._add_filter_to_pipeline(config, "crossover_lowpass")
                self.logger.info(f"Lowpass filter enabled at {frequency} Hz (Q={q})")
            else:
                # Remove lowpass filter
                if "crossover_lowpass" in config.get("filters", {}):
                    del config["filters"]["crossover_lowpass"]
                self._remove_filter_from_pipeline(config, "crossover_lowpass")
                self.logger.info("Lowpass filter disabled")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )

            # Save to disk for persistence
            await self._save_config_to_file(config)

            return True

        except Exception as e:
            self.logger.error(f"Error setting lowpass: {e}")
            return False

    def _add_filter_to_pipeline(self, config: Dict, filter_name: str,
                                channels: List[int] = None) -> None:
        """Add a filter to the pipeline."""
        if "pipeline" not in config:
            config["pipeline"] = []

        if channels is None:
            channels = [0, 1]

        for channel in channels:
            for step in config["pipeline"]:
                if step.get("type") == "Filter" and channel in step.get("channels", []):
                    if filter_name not in step.get("names", []):
                        step["names"].append(filter_name)
                    break  # Continue to next channel, not return

    def _remove_filter_from_pipeline(self, config: Dict, filter_name: str) -> None:
        """Remove a filter from the pipeline."""
        if "pipeline" not in config:
            return
        for step in config["pipeline"]:
            if step.get("type") == "Filter" and "names" in step:
                if filter_name in step["names"]:
                    step["names"].remove(filter_name)

    def _add_processor_to_pipeline(self, config: Dict, processor_name: str) -> None:
        """Add a processor to the pipeline."""
        if "pipeline" not in config:
            config["pipeline"] = []
        for step in config["pipeline"]:
            if step.get("type") == "Processor" and step.get("name") == processor_name:
                return
        config["pipeline"].append({"type": "Processor", "name": processor_name})

    def _remove_processor_from_pipeline(self, config: Dict, processor_name: str) -> None:
        """Remove a processor from the pipeline."""
        if "pipeline" not in config:
            return
        config["pipeline"] = [
            step for step in config["pipeline"]
            if not (step.get("type") == "Processor" and step.get("name") == processor_name)
        ]
