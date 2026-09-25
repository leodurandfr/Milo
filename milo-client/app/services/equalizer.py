"""
CamillaDSP service for Milo Client.

Controls local CamillaDSP daemon via WebSocket for:
- Parametric EQ (filters)
- Compressor
- Loudness compensation
- Volume/mute control
- Crossover filters (highpass/lowpass)
"""
import asyncio
import functools
import logging
import os
import time
import yaml
from typing import Dict, Any, Optional, List

from services.camilladsp_client import CamillaDspClient

# Constants
CAMILLADSP_HOST = "127.0.0.1"
CAMILLADSP_PORT = 1234
CONFIG_FILE = "/var/lib/milo-client/camilladsp/config.yml"
RECONNECT_DELAY = 5.0
MAX_RECONNECT_DELAY = 30.0

# What the main fader holds the moment CamillaDSP starts, set by `--gain` in
# milo-client-camilladsp.service. Mirrored here because a systemd unit and a
# Python module cannot share a declaration: the unit is the authority — it is
# what actually configures the daemon — and this is where the volume cache
# starts, which the connection loop pushes on every connect, the first one
# included. backend/tests/architecture/test_camilladsp_startup_floor.py fails
# the moment the two disagree, in either direction, and also the moment either
# one rises above the backend's MIN_VOLUME_DB — a floor is only a floor while
# it stays at the bottom.
STARTUP_GAIN_DB = -80.0


def serialised_config_write(method):
    """Hold the config lock for a whole read-modify-write.

    Every setter below reads the live config, mutates its own corner of it and
    writes the whole document back, so two of them running at once lose one
    mutation silently — reachable because the server pushes a record from a
    background task (the reconnection sync) while a targeted write from the UI
    can land at the same moment. Decorated methods must never call each other:
    the lock is not reentrant.
    """
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._config_lock:
            return await method(self, *args, **kwargs)
    return wrapper


class EqualizerService:
    """
    CamillaDSP control service.

    Manages connection to local CamillaDSP daemon and provides methods for:
    - EQ filter configuration
    - Compressor settings
    - Loudness compensation
    - Level trim (speaker balance)
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
        self._config_lock = asyncio.Lock()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._running = True

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
        self._gain_db: float = 0.0
        self._volume = {"main": STARTUP_GAIN_DB, "mute": True}  # Matches CamillaDSP's -m + --gain start
        self._crossover = {"enabled": False, "frequency": 80.0, "q": 0.707}
        self._lowpass = {"enabled": False, "frequency": 80.0, "q": 0.707}
        self._mono: bool = False
        self._equalizer_enabled = True

    @property
    def connected(self) -> bool:
        """Returns whether CamillaDSP is connected."""
        return self._connected

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
    def gain_db(self) -> float:
        """Level trim in dB (a fixed Gain stage, never the volume fader)."""
        return self._gain_db

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

    async def _connect_once(self) -> bool:
        """Single connection attempt, guarded by lock to prevent concurrent connects.

        `_connection_loop` is the only caller, and it is what makes a connection
        usable: it follows a successful attempt with `_load_state_from_config()`
        and `_restore_after_reconnect()`, in that order, outside this lock. A
        second caller would hand out a connection to a daemon still sitting at
        its `--gain` floor — which is the whole of the 2026-09-20 incident.
        """
        async with self._reconnect_lock:
            if self._connected:
                return True

            try:
                # The client this one replaces may still be holding an open
                # socket: a command the daemon *refused* clears _connected the
                # same way a dead one does, and releasing the reference does not
                # close it — the client's read and ping tasks hold it alive.
                # Left behind it would ping the local daemon every 20 s for ever.
                await self._drop_client()
                self._client = CamillaDspClient(self.host, self.port, timeout=RECONNECT_DELAY)
                await self._client.connect()
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
        if self._client:
            await self._client.disconnect()

    async def _connection_loop(self) -> None:
        """Background reconnection loop with exponential backoff and periodic probe.

        Follows the same pattern as CamillaDSPService._connection_loop() in the
        main backend, with an added periodic probe since the satellite receives
        infrequent commands and needs proactive disconnection detection.
        """
        reconnect_delay = RECONNECT_DELAY

        while self._running:
            usable = False
            try:
                connected = await self._connect_once()

                if connected:

                    # Volume and mute first, config second, and the order is
                    # load-bearing twice over. `_connect_once` publishes
                    # `_connected` before either runs, so whatever comes first
                    # is the window in which a command can reach a daemon still
                    # on its `--gain` floor — a `GetConfig` round trip is orders
                    # of magnitude wider than the hop between these two lines.
                    # And `_load_state_from_config` goes through `_exec`, which
                    # no longer reconnects: a failure there clears `_connected`,
                    # so anything after it is skipped. Behind it, that meant the
                    # cached level never reached the daemon and the speaker sat
                    # muted at the floor until the next pass.
                    if await self._restore_after_reconnect():
                        # A session that reached the daemon: the backoff has
                        # served its purpose and starts over.
                        reconnect_delay = RECONNECT_DELAY
                        usable = True
                        await self._load_state_from_config()

                        # Idle: periodically probe CamillaDSP to detect silent disconnections
                        while self._running and self._connected:
                            await asyncio.sleep(RECONNECT_DELAY)
                            if self._connected:
                                await self._probe_connection()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Connection loop error: {e}")

            # Outside the try, so no path can skip it — but not on every pass.
            # A session that worked and then lost its socket is retried at once,
            # because `_exec` no longer reconnects and every `/equalizer/*` call
            # answers 400 until the loop is back: waiting there would spend the
            # server's sync retries on a daemon that is probably already up.
            # What must back off is the connect-then-fail pass — `-w` holding an
            # invalid config, or a GetConfig timing out under CPU starvation —
            # which otherwise churns a fresh websocket client and three log lines
            # every RECONNECT_DELAY for ever.
            if self._running and not usable:
                self.logger.info(f"Reconnecting to CamillaDSP in {reconnect_delay:.0f}s...")
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
            await client.get_state()
        except Exception as e:
            self.logger.warning(f"CamillaDSP connection lost (detected by probe): {e}")
            self._connected = False
            await self._drop_client()

    async def _restore_after_reconnect(self) -> bool:
        """Restore volume/mute from cache after CamillaDSP reconnection, reporting.

        The loop needs the outcome, not just the attempt: a connection whose
        restore failed is not a usable one, and treating it as one is what let
        a refusing daemon churn a fresh client every backoff for ever.

        CamillaDSP starts muted (-m flag). DSP effects (EQ, compressor, loudness,
        crossover) are already restored from the config file on disk. Only volume
        and mute are runtime-only parameters that need explicit restoration.
        """
        try:
            volume = self._volume["main"]
            mute = self._volume["mute"]
            await self._exec(lambda c: c.set_volume(volume))
            await self._exec(lambda c: c.set_mute(mute))
            self.logger.info(
                f"Restored volume after reconnect: {volume:.1f} dB, mute={mute}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Error restoring volume after reconnect: {e}")
            return False

    async def _drop_client(self) -> None:
        """Let go of the client, closing the socket it may still be holding.

        Awaited rather than spawned: both callers are the connection loop or a
        reconnect, neither of which is on a request's critical path, and the
        satellite has no task set to hand it to.
        """
        stale, self._client = self._client, None
        if stale is not None:
            await stale.disconnect()

    async def _exec(self, call):
        """Await one daemon call, marking the service disconnected on failure.

        It never opens a connection. `_connection_loop` owns every attempt, the
        first one included, because a connection is only usable once the cached
        volume and mute have been pushed over the floor CamillaDSP starts at
        (`-m` and `--gain=STARTUP_GAIN_DB`), and the loop is the only thing that
        does that.

        Measured on the fleet, 2026-09-20 01:43:23: an app update restarted the
        daemon, the loop was asleep in its 5 s backoff, and the server's
        `PUT /equalizer/mute` opened the connection here and applied
        `set_mute(False)` to a bare 0 dB fader — 3 s of full-scale music in a
        room set to -52.8 dB. A refusal costs nothing here: `set_volume` and
        `set_mute` write the cache before calling, so the loop replays them in
        the right order, and the server retries the sync six times at 3 s.

        Mirror of `CamillaDSPService._run` in the backend. `call` is handed the
        live client rather than closing over one, because a reconnect builds a
        new one.
        """
        if not self._connected or self._client is None:
            raise ConnectionError("Not connected to CamillaDSP")
        try:
            return await call(self._client)
        except Exception:
            self.logger.warning("CamillaDSP command failed, marking disconnected")
            self._connected = False
            raise

    async def _load_state_from_config(self):
        """Load compressor/loudness/trim state from current CamillaDSP config."""
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

            # Check for the level trim (absent filter == no trim)
            if "filters" in config:
                if "gain_trim" in config["filters"]:
                    self._gain_db = config["filters"]["gain_trim"].get("parameters", {}).get("gain", 0.0)
                    self.logger.info(f"Loaded level trim from config: {self._gain_db:+.1f} dB")
                else:
                    self._gain_db = 0.0

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
            state = await self._exec(lambda c: c.get_state())
            state_str = str(state).lower()

            return {
                "available": True,
                "state": state_str,
                "filters": await self.get_filters(),
                "compressor": self._compressor,
                "loudness": self._loudness,
                "mono": self._mono,
                "equalizer_enabled": self._equalizer_enabled
            }
        except Exception as e:
            self.logger.error(f"Error getting equalizer status: {e}")
            return {"available": False, "error": str(e)}

    async def _get_config(self) -> Optional[Dict[str, Any]]:
        """Get CamillaDSP config."""
        config = await self._exec(lambda c: c.get_config())
        if config is None:
            config_path = await self._exec(lambda c: c.get_config_file_path())
            if config_path:
                config = await self._exec(lambda c: c.read_config_file(config_path))
        return config

    async def _apply_config(self, config: Dict[str, Any]) -> None:
        """Apply config to CamillaDSP, then persist it.

        set_active first — the audible effect must not wait on the disk — and the
        persist leg raises rather than reporting success: a satellite that
        answered 200 with nothing written comes back on its old EQ at the next
        reboot, and only a second physical unit shows it.
        """
        await self._exec(lambda c: c.set_config(config))
        await self._save_config_to_file(config)

    async def _save_config_to_file(self, config: Dict[str, Any]) -> None:
        """Persist config to disk atomically. Raises if it did not land."""
        config_yaml = yaml.dump(config, default_flow_style=False, allow_unicode=True)
        await asyncio.get_running_loop().run_in_executor(
            None, self._write_atomic, self.config_file, config_yaml
        )
        self.logger.info("Config saved to disk")

    @staticmethod
    def _write_atomic(path: str, content: str) -> None:
        """tmp + fsync + rename.

        A power cut during a plain overwrite leaves a truncated config.yml, and
        the recovery path re-reads that same file (_get_config falls back to
        read_and_parse_file), so the room stays silent until someone deletes it.
        """
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

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

    @serialised_config_write
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

    @serialised_config_write
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

    @serialised_config_write
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

    @serialised_config_write
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

    @serialised_config_write
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

    @serialised_config_write
    async def set_gain(self, gain_db: float) -> bool:
        """Set the level trim — a fixed Gain stage on both channels, in dB.

        This is a calibration, not an effect: it compensates this speaker's
        sensitivity against the rest of the system so every client can sit at
        the same volume. Two consequences it must keep.

        It is deliberately NOT named eq_band_* / loudness_* / compressor, which
        is what keeps `set_equalizer_enabled` from stripping it: a master bypass
        that unbalanced the room would be a bug, not a bypass.

        And it is not the volume fader, which keeps its own full range — that
        separation is the whole point, since a shared level change can then move
        every client without one of them reaching a limit before the others.

        A trim of 0 removes the filter entirely rather than writing a 0 dB one,
        so the config of an untrimmed speaker is the config it always had.
        """
        self._gain_db = max(-12.0, min(12.0, gain_db))

        try:
            config = await self._get_config()
            if not config:
                return False

            if "filters" not in config:
                config["filters"] = {}

            # A push that changes nothing must not reload the pipeline: the
            # server re-pushes the trim on every admission, and the common case
            # is a fleet where nothing was ever trimmed.
            current = config["filters"].get("gain_trim", {}).get("parameters", {}).get("gain")
            if current == (self._gain_db if self._gain_db != 0.0 else None):
                return True

            if self._gain_db != 0.0:
                config["filters"]["gain_trim"] = {
                    "type": "Gain",
                    "parameters": {"gain": self._gain_db, "inverted": False, "mute": False}
                }
                self._add_filter_to_pipeline(config, "gain_trim")
            else:
                if "gain_trim" in config.get("filters", {}):
                    del config["filters"]["gain_trim"]
                self._remove_filter_from_pipeline(config, "gain_trim")

            await self._apply_config(config)
            self.logger.info(f"Level trim set to {self._gain_db:+.1f} dB")
            return True
        except Exception as e:
            self.logger.error(f"Error setting gain: {e}")
            return False

    async def get_volume(self) -> Dict[str, Any]:
        """Get current equalizer volume settings."""
        if self._connected and self._client:
            try:
                volume = await self._exec(lambda c: c.get_volume())
                mute = await self._exec(lambda c: c.get_mute())
                self._volume["main"] = volume
                self._volume["mute"] = mute
            except Exception as e:
                self.logger.warning(f"Error getting volume from CamillaDSP: {e}")
        return self._volume

    async def get_levels(self) -> Dict[str, Any]:
        """Get current audio levels (peak values for input/output)."""
        try:
            capture_levels = await self._exec(lambda c: c.get_capture_peak())
            playback_levels = await self._exec(lambda c: c.get_playback_peak())
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
            await self._exec(lambda c: c.set_volume(self._volume["main"]))
            self.logger.info(f"[{time.time():.3f}] VOLUME_SET: Volume set to {self._volume['main']:.1f} dB")
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, muted: bool) -> bool:
        """Set equalizer mute state."""
        self._volume["mute"] = muted

        try:
            await self._exec(lambda c: c.set_mute(muted))
            self.logger.info(f"[{time.time():.3f}] MUTE_SET: Mute set to {muted}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False

    @serialised_config_write
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

    @serialised_config_write
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

    @serialised_config_write
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
