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
from concurrent.futures import ThreadPoolExecutor
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
RECONNECT_DELAY = 5.0
MAX_RECONNECT_DELAY = 30.0


class EqualizerService:
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
        self.logger = logging.getLogger(f"{__name__}.EqualizerService")
        self.host = host or CAMILLADSP_HOST
        self.port = port or CAMILLADSP_PORT
        self.config_file = config_file or CONFIG_FILE

        self._client = None
        self._connected = False
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._running = True

        # Single-thread executor for pycamilladsp sync calls (serializes all DSP
        # commands to prevent concurrent access to the non-thread-safe CamillaClient)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camilladsp")

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
        self._mono: bool = False
        self._equalizer_enabled = True

    @property
    def connected(self) -> bool:
        """Returns whether CamillaDSP is connected."""
        return self._connected

    @property
    def available(self) -> bool:
        """Returns whether CamillaDSP client library is available."""
        return CAMILLADSP_AVAILABLE

    @property
    def equalizer_enabled(self) -> bool:
        """Returns equalizer effects enabled state."""
        return self._equalizer_enabled

    @property
    def compressor(self) -> Dict[str, Any]:
        """Returns compressor state."""
        return self._compressor

    @property
    def loudness(self) -> Dict[str, Any]:
        """Returns loudness state."""
        return self._loudness

    @property
    def mono(self) -> bool:
        """Returns mono state."""
        return self._mono

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
        """Connect to local CamillaDSP (public entry point for startup)."""
        result = await self._connect_once()
        if result:
            await self._load_state_from_config()
        return result

    async def _connect_once(self) -> bool:
        """Single connection attempt, guarded by lock to prevent concurrent connects.

        Does NOT call _load_state_from_config() — callers must do so after
        the lock is released to avoid deadlock (_load_state_from_config calls
        _exec which may re-enter _connect_once on failure).
        """
        async with self._reconnect_lock:
            if self._connected:
                return True

            if not CAMILLADSP_AVAILABLE:
                self.logger.warning("CamillaDSP client library not available")
                return False

            try:
                self._client = CamillaClient(self.host, self.port)
                await asyncio.get_running_loop().run_in_executor(
                    self._executor, self._client.connect
                )
                # Set socket timeout so recv() doesn't block forever when
                # CamillaDSP shuts down — allows the probe to detect the failure
                if self._client._ws and self._client._ws.sock:
                    self._client._ws.sock.settimeout(RECONNECT_DELAY)
                self._connected = True
                self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}")
                return True
            except Exception as e:
                self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
                self._connected = False
                self._client = None
                return False

    def start_connection_loop(self) -> None:
        """Start background connection monitoring task."""
        self._reconnect_task = asyncio.create_task(self._connection_loop())

    async def stop_connection_loop(self) -> None:
        """Stop background connection monitoring and clean up."""
        self._running = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=True)

    async def _connection_loop(self) -> None:
        """Background reconnection loop with exponential backoff and periodic probe.

        Follows the same pattern as CamillaDSPService._connection_loop() in the
        main backend, with an added periodic probe since the satellite receives
        infrequent commands and needs proactive disconnection detection.
        """
        reconnect_delay = RECONNECT_DELAY

        while self._running:
            try:
                if not self._connected:
                    connected = await self._connect_once()
                    if connected:
                        reconnect_delay = RECONNECT_DELAY
                        await self._load_state_from_config()
                        await self._restore_after_reconnect()
                    else:
                        if self._running:
                            self.logger.info(f"Reconnecting to CamillaDSP in {reconnect_delay:.0f}s...")
                            await asyncio.sleep(reconnect_delay)
                            reconnect_delay = min(reconnect_delay * 1.5, MAX_RECONNECT_DELAY)
                        continue

                # Idle: periodically probe CamillaDSP to detect silent disconnections
                while self._running and self._connected:
                    await asyncio.sleep(RECONNECT_DELAY)
                    if self._connected:
                        await self._probe_connection()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Connection loop error: {e}")
                if self._running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, MAX_RECONNECT_DELAY)

    async def _probe_connection(self) -> None:
        """Probe CamillaDSP connection to detect silent disconnections.

        Unlike the main backend (which gets frequent commands from the frontend),
        the satellite may go long periods without any _exec() call. This probe
        ensures _connected stays accurate for the /health endpoint.
        """
        client = self._client
        if client is None:
            self._connected = False
            return
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, lambda: client.general.state()
            )
        except Exception as e:
            self.logger.warning(f"CamillaDSP connection lost (detected by probe): {e}")
            self._connected = False
            self._client = None

    async def _restore_after_reconnect(self) -> None:
        """Restore volume/mute from cache after CamillaDSP reconnection.

        CamillaDSP starts muted (-m flag). DSP effects (EQ, compressor, loudness,
        crossover) are already restored from the config file on disk. Only volume
        and mute are runtime-only parameters that need explicit restoration.
        """
        try:
            volume = self._volume["main"]
            mute = self._volume["mute"]
            await self._exec(lambda: self._client.volume.set_main_volume(volume))
            await self._exec(lambda: self._client.volume.set_main_mute(mute))
            self.logger.info(
                f"Restored volume after reconnect: {volume:.1f} dB, mute={mute}"
            )
        except Exception as e:
            self.logger.error(f"Error restoring volume after reconnect: {e}")

    async def _exec(self, func):
        """
        Execute a CamillaDSP operation with auto-reconnect on connection loss.

        On first failure, resets connection state and retries once after reconnecting.
        Uses lambdas to ensure the new client is used after reconnection.
        """
        for attempt in range(2):
            if not self._connected:
                await self._connect_once()
            if not self._connected:
                raise ConnectionError("Not connected to CamillaDSP")
            try:
                return await asyncio.get_running_loop().run_in_executor(self._executor, func)
            except Exception:
                self._connected = False
                if attempt == 0:
                    self.logger.warning("CamillaDSP connection lost, reconnecting...")
                    continue
                raise

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

            # Check for mono mixer (pipeline's Mixer step name)
            for step in config.get("pipeline", []):
                if step.get("type") == "Mixer":
                    self._mono = step.get("name") == "mono"
                    if self._mono:
                        self.logger.info("Loaded mono state from config")
                    break

            # Derive master equalizer-enabled state from the persisted pipeline.
            # set_equalizer_enabled() bypasses by removing eq_band_* from the pipeline
            # while keeping their definitions, so "bands defined but none piped" means
            # effects are bypassed. This makes the bypass state survive a restart.
            eq_band_defs = [n for n in config.get("filters", {}) if n.startswith("eq_band_")]
            if eq_band_defs:
                piped = set()
                for step in config.get("pipeline", []):
                    if step.get("type") == "Filter":
                        piped.update(step.get("names", []))
                self._equalizer_enabled = any(name in piped for name in eq_band_defs)

        except Exception as e:
            self.logger.warning(f"Could not load state from config: {e}")

    async def get_status(self) -> Dict[str, Any]:
        """Get equalizer status."""
        try:
            state = await self._exec(lambda: self._client.general.state())
            state_str = str(state).split('.')[-1].lower()

            return {
                "available": True,
                "state": state_str,
                "filters": await self.get_filters(),
                "compressor": self._compressor,
                "loudness": self._loudness,
                "delay": self._delay,
                "mono": self._mono,
                "equalizer_enabled": self._equalizer_enabled
            }
        except Exception as e:
            self.logger.error(f"Error getting equalizer status: {e}")
            return {"available": False, "error": str(e)}

    async def _get_config(self) -> Optional[Dict[str, Any]]:
        """Get CamillaDSP config."""
        config = await self._exec(lambda: self._client.config.active())
        if config is None:
            config_path = await self._exec(lambda: self._client.config.file_path())
            if config_path:
                config = await self._exec(
                    lambda: self._client.config.read_and_parse_file(config_path)
                )
        return config

    async def _apply_config(self, config: Dict[str, Any]) -> None:
        """Apply config to CamillaDSP and save to disk."""
        await self._exec(lambda: self._client.config.set_active(config))
        await self._save_config_to_file(config)

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
                # A band is enabled iff it is referenced in a Filter pipeline step
                # (per-band disable / master bypass both work by un-piping the band).
                piped = set()
                for step in config.get("pipeline", []):
                    if step.get("type") == "Filter":
                        piped.update(step.get("names", []))
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
                        "enabled": name in piped
                    })
                self._filters.sort(key=lambda f: f["id"])
            return self._filters
        except Exception as e:
            self.logger.error(f"Error getting filters: {e}")
            return self._filters

    async def set_filter(self, filter_id: str, gain: float,
                         freq: float = None, q: float = None,
                         filter_type: str = None) -> bool:
        """Update a filter band's tuning.

        Mutates the Biquad parameters only — the band's presence in the pipeline
        is owned by set_equalizer_enabled(), so editing a band never un-bypasses
        a bypassed client. Mirrors the server's CamillaDSPService.set_filter().
        """
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
            if filter_type is not None:
                params["type"] = filter_type

            await self._apply_config(config)
            return True
        except Exception as e:
            self.logger.error(f"Error setting filter {filter_id}: {e}")
            return False

    async def set_filters_batch(self, filters: List[dict]) -> dict:
        """
        Update multiple filters in one operation with a single disk save.

        Applies the same tuning keys as set_filter(), so a whole-record push and
        a single-band push cannot leave the client in different states.

        Args:
            filters: List of filter dicts with keys: id, gain, freq (optional),
                q (optional), filter_type (optional)

        Returns:
            dict with success status and number of filters applied
        """
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
                    if f.get("filter_type") is not None:
                        params["type"] = f["filter_type"]
                    applied += 1

            await self._apply_config(config)
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

            await self._apply_config(config)
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

            await self._apply_config(config)
            return True
        except Exception as e:
            self.logger.error(f"Error setting loudness: {e}")
            return False

    async def set_mono(self, enabled: bool) -> bool:
        """Switch between stereo passthrough and mono summing in CamillaDSP."""
        self._mono = enabled
        try:
            config = await self._get_config()
            if not config:
                return False

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
            target_name = "mono" if enabled else "stereo"
            for step in config.get("pipeline", []):
                if step.get("type") == "Mixer":
                    step["name"] = target_name
                    break

            await self._apply_config(config)
            self.logger.info(f"Mono {'enabled' if enabled else 'disabled'}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting mono: {e}")
            return False

    async def set_delay(self, left: float = None, right: float = None) -> bool:
        """Set channel delay in milliseconds."""
        if left is not None:
            self._delay["left"] = max(0, min(50, left))
        if right is not None:
            self._delay["right"] = max(0, min(50, right))

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

            await self._apply_config(config)
            return True
        except Exception as e:
            self.logger.error(f"Error setting delay: {e}")
            return False

    async def get_volume(self) -> Dict[str, Any]:
        """Get current equalizer volume settings."""
        if self._connected and self._client:
            try:
                volume = await self._exec(lambda: self._client.volume.main_volume())
                mute = await self._exec(lambda: self._client.volume.main_mute())
                self._volume["main"] = volume
                self._volume["mute"] = mute
            except Exception as e:
                self.logger.warning(f"Error getting volume from CamillaDSP: {e}")
        return self._volume

    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak values for input/output)."""
        try:
            capture_levels = await self._exec(lambda: self._client.levels.capture_peak())
            playback_levels = await self._exec(lambda: self._client.levels.playback_peak())
            return {
                "available": True,
                "input_peak": capture_levels,
                "output_peak": playback_levels
            }
        except Exception as e:
            self.logger.debug(f"Error getting levels: {e}")
            return {"available": False}

    async def set_volume(self, volume: float) -> bool:
        """Set equalizer volume in dB."""
        self._volume["main"] = max(-80, min(0, volume))

        try:
            await self._exec(
                lambda: self._client.volume.set_main_volume(self._volume["main"])
            )
            self.logger.info(f"[{time.time():.3f}] VOLUME_SET: Volume set to {self._volume['main']:.1f} dB")
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, muted: bool) -> bool:
        """Set equalizer mute state."""
        self._volume["mute"] = muted

        try:
            await self._exec(lambda: self._client.volume.set_main_mute(muted))
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

            await self._apply_config(config)
            return True

        except Exception as e:
            self.logger.error(f"Error setting crossover: {e}")
            return False

    async def set_lowpass(self, enabled: bool, frequency: float = 80.0, q: float = 0.707) -> bool:
        """
        Set lowpass filter for subwoofer.

        When enabled, applies a Butterworth lowpass filter at the specified
        frequency to send only bass to the subwoofer. Also enables dither to
        prevent amp settling during quiet passages (ploc fix).

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

            await self._apply_config(config)
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

    async def set_equalizer_enabled(self, enabled: bool) -> bool:
        """
        Master toggle for equalizer effects (EQ bands + compressor + loudness).

        Pipeline-only bypass, mirroring the main backend's bypass_effects/
        restore_effects (backend/core/equalizer/service.py): the effect
        *definitions* in config["filters"]/["processors"] are never touched —
        only their pipeline references are removed (disable) or re-added
        (enable). This keeps the exact tuning so restore is lossless, lets the
        bypass state survive a restart (derived from the persisted pipeline in
        _load_state_from_config), and leaves volume/mute and crossover_* alone.
        Idempotent: re-applying the current state is safe (used by reconnect sync).
        """
        try:
            config = await self._get_config()
            if not config:
                return False

            eq_bands = [n for n in config.get("filters", {}) if n.startswith("eq_band_")]

            if enabled:
                for name in eq_bands:
                    self._add_filter_to_pipeline(config, name)
                # Compressor/loudness only return to the pipeline if individually on,
                # preserving the user's per-effect choice across a master toggle.
                if self._compressor["enabled"]:
                    self._add_processor_to_pipeline(config, "compressor")
                if self._loudness["enabled"]:
                    self._add_filter_to_pipeline(config, "loudness_low")
                    self._add_filter_to_pipeline(config, "loudness_high")
            else:
                for name in eq_bands:
                    self._remove_filter_from_pipeline(config, name)
                self._remove_processor_from_pipeline(config, "compressor")
                self._remove_filter_from_pipeline(config, "loudness_low")
                self._remove_filter_from_pipeline(config, "loudness_high")

            await self._apply_config(config)
            self._equalizer_enabled = enabled
            self.logger.info(f"Equalizer effects {'restored' if enabled else 'bypassed'} (volume unchanged)")
            return True

        except Exception as e:
            self.logger.error(f"Error setting equalizer enabled: {e}")
            return False
