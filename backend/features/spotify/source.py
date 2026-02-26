# backend/features/spotify/source.py
"""
Spotify audio source using go-librespot.

This source handles streaming audio from Spotify Connect via go-librespot.
It provides real-time metadata updates through WebSocket connection and
supports playback control via REST API.

Features:
- WebSocket for real-time events (playing, paused, metadata, etc.)
- REST API for playback commands (play, pause, seek, etc.)
- Auto-disconnect timer after pause (configurable)
- Metadata tracking with album art and position
"""
import asyncio
import os
import re
import time
import yaml
from typing import Dict, Any, Optional

import aiohttp

from backend.core.audio_source import BaseAudioSource, SourceState
from backend.core.events import EventBus
from backend.features.spotify.websocket import LibrespotWebSocket


class SpotifySource(BaseAudioSource):
    """
    Spotify audio source using go-librespot.

    Implements AudioSource Protocol with:
    - start(): Start go-librespot service and WebSocket
    - stop(): Stop service and cleanup
    - restart(): Restart service with state reset
    - status(): Get current status with metadata
    - command(): Handle playback commands

    Events emitted:
    - source.started: Spotify source activated
    - source.stopped: Spotify source deactivated
    - source.state_changed: State changed (READY/CONNECTED)
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        """
        Initialize Spotify source.

        Args:
            event_bus: EventBus for state notifications
            config: Optional configuration dict with:
                - config_path: Path to go-librespot config
                - api_host: API host (default from config)
                - api_port: API port (default from config)
            state_machine: Optional state machine for state synchronization
            settings_service: Optional settings service for auto-disconnect config
            systemd_manager: Optional SystemdServiceManager (injected via DI)
        """
        super().__init__(
            source_id="spotify",
            service_name="milo-spotify.service",
            event_bus=event_bus,
            state_machine=state_machine,
            systemd_manager=systemd_manager
        )

        config = config or {}
        self._config_path = os.path.expanduser(config.get("config_path", ""))
        self._settings_service = settings_service

        # API endpoints (loaded from config file)
        self._api_url: Optional[str] = None
        self._ws_url: Optional[str] = None

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket client
        self._ws_client: Optional[LibrespotWebSocket] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._ws_connected = False

        # Auto-disconnect
        self.auto_disconnect_enabled = True
        self.pause_disconnect_delay = 10.0
        self._pause_timer: Optional[asyncio.Task] = None

        # Log monitor for error detection
        self._log_monitor_task: Optional[asyncio.Task] = None
        self._connection_error_count = 0
        self._last_error_time = 0.0

    async def _do_start(self) -> bool:
        """Start go-librespot service and WebSocket."""
        try:
            # 1. Load config
            if not await self._load_config():
                return False

            # 2. Start service
            if not await self._start_service():
                return False

            await asyncio.sleep(0.5)  # Wait for service

            # 3. Reset state
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._cancel_pause_timer()

            # 4. Create HTTP session
            self._session = aiohttp.ClientSession()

            # 5. Start WebSocket
            await self._start_websocket()

            # 6. Start log monitor for error detection
            self._start_log_monitor()

            # 7. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """Stop WebSocket and service."""
        try:
            await self._cleanup()
            return await self._stop_service()
        except Exception as e:
            self._logger.error(f"Stop failed: {e}")
            return False

    async def _do_restart(self) -> bool:
        """Restart service with state reset."""
        try:
            self._logger.info("Restarting Spotify source")

            # Cancel timer
            self._cancel_pause_timer()

            # Reset state
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}

            # Stop WebSocket
            if self._ws_client:
                await self._ws_client.stop()

            # Restart service
            if not await self._restart_service():
                return False

            await asyncio.sleep(0.5)

            # Reconnect WebSocket
            await self._start_websocket()

            # Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Restart failed: {e}")
            return False

    async def _get_status(self) -> Dict[str, Any]:
        """Get Spotify-specific status."""
        return {
            "device_connected": self._device_connected,
            "ws_connected": self._ws_connected,
            "is_playing": self._is_playing,
            "metadata": self._metadata,
            "auto_disconnect_config": {
                "enabled": self.auto_disconnect_enabled,
                "delay": self.pause_disconnect_delay,
                "timer_active": self._pause_timer is not None and not self._pause_timer.done()
            }
        }

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Spotify-specific commands."""
        if cmd == "restart_service":
            success = await self._do_restart()
            return (
                self.success_response("Service restarted")
                if success else self.error_response("Restart failed")
            )

        if cmd == "refresh_metadata":
            success = await self._refresh_metadata()
            return self.success_response(
                "Metadata refreshed" if success else "Refresh failed",
                metadata=self._metadata
            )

        if cmd == "seek":
            position = data.get("position_ms")
            if position is None:
                return self.error_response("position_ms required")
            if not isinstance(position, (int, float)) or position < 0:
                return self.error_response("position_ms must be a non-negative number")
            duration = self._metadata.get("duration", 0)
            if duration > 0 and position > duration:
                return self.error_response(f"position_ms ({position}) exceeds duration ({duration}ms)")
            return await self._send_api_command("seek", {"position": int(position)})

        if cmd in ["play", "pause", "resume", "playpause"]:
            return await self._send_api_command(cmd)

        if cmd in ["next", "prev"]:
            payload = {"uri": data.get("uri")} if data.get("uri") else {}
            return await self._send_api_command(cmd, payload)

        return self.error_response(f"Unknown command: {cmd}")

    # === Config Loading ===

    async def _load_config(self) -> bool:
        """Load configuration from go-librespot config file."""
        try:
            if not self._config_path or not os.path.exists(self._config_path):
                self._logger.error(f"Config file not found: {self._config_path}")
                return False

            with open(self._config_path, 'r') as f:
                config = yaml.safe_load(f)

            server = config.get('server', {})
            addr = server.get('address', 'localhost')
            port = server.get('port', 3678)

            self._api_url = f"http://{addr}:{port}"
            self._ws_url = f"ws://{addr}:{port}/events"

            # Load auto-disconnect config from settings
            await self._load_auto_disconnect_config()

            self._logger.info(f"Config loaded: API={self._api_url}")
            return True

        except Exception as e:
            self._logger.error(f"Config load failed: {e}")
            return False

    async def _load_auto_disconnect_config(self) -> None:
        """Load auto-disconnect config from settings service."""
        if not self._settings_service:
            return

        try:
            delay = await self._settings_service.get_setting('spotify.auto_disconnect_delay')
            if delay is not None:
                if delay == 0:
                    self.auto_disconnect_enabled = False
                    self.pause_disconnect_delay = 10.0
                else:
                    self.auto_disconnect_enabled = True
                    self.pause_disconnect_delay = float(delay)

            self._logger.info(
                f"Auto-disconnect: enabled={self.auto_disconnect_enabled}, "
                f"delay={self.pause_disconnect_delay}s"
            )
        except Exception as e:
            self._logger.error(f"Settings load failed: {e}")

    # === WebSocket ===

    async def _start_websocket(self) -> None:
        """Start WebSocket connection."""
        if not self._session or not self._ws_url:
            return

        self._ws_client = LibrespotWebSocket(
            ws_url=self._ws_url,
            session=self._session,
            on_event=self._handle_ws_event
        )
        await self._ws_client.start()

    async def _handle_ws_event(self, event: Dict[str, Any]) -> None:
        """Handle WebSocket event from go-librespot.

        go-librespot sends flat events (fields at root level, no "data" wrapper):
        {"type": "seek", "position": 12345, "uri": "spotify:track:..."}
        """
        event_type = event.get("type")

        self._ws_connected = True

        if event_type == "active":
            await self._on_device_active()

        elif event_type == "inactive":
            await self._on_device_inactive()

        elif event_type == "playing":
            await self._on_playback_state(True)

        elif event_type == "paused":
            await self._on_playback_state(False)

        elif event_type == "metadata":
            await self._on_metadata_update()

        elif event_type == "seek":
            await self._on_seek()

        elif event_type == "stopped":
            await self._on_stopped()

        elif event_type == "not_playing":
            await self._on_not_playing()

    async def _on_device_active(self) -> None:
        """Handle device active event."""
        self._device_connected = True
        await self._refresh_metadata()
        self._update_connection_state()

    async def _on_device_inactive(self) -> None:
        """Handle device inactive event."""
        self._cancel_pause_timer()
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        self._update_connection_state()

    async def _on_playback_state(self, is_playing: bool) -> None:
        """Handle playback state change."""
        self._is_playing = is_playing
        self._device_connected = True

        if is_playing:
            self._cancel_pause_timer()
        else:
            self._start_pause_timer()

        await self._refresh_metadata()
        self._metadata["is_playing"] = is_playing
        self._update_connection_state()

    async def _on_metadata_update(self) -> None:
        """Handle metadata update event."""
        await self._refresh_metadata()
        self._update_connection_state()

    async def _on_seek(self) -> None:
        """Handle seek event."""
        await self._refresh_metadata()
        self._update_connection_state()

    async def _on_stopped(self) -> None:
        """Handle stopped event - context ended, nothing more to play."""
        self._logger.info("Playback stopped - context ended")
        self._is_playing = False
        self._cancel_pause_timer()
        self._update_connection_state()

    async def _on_not_playing(self) -> None:
        """Handle not_playing event - track finished naturally."""
        self._logger.debug("Track finished playing")
        self._is_playing = False
        self._start_pause_timer()
        self._update_connection_state()

    # === REST API ===

    async def _refresh_metadata(self) -> bool:
        """Refresh metadata from go-librespot API."""
        if not self._session or not self._api_url:
            return False

        try:
            async with self._session.get(f"{self._api_url}/status") as resp:
                if resp.status != 200:
                    return False

                data = await resp.json()

                self._device_connected = bool(data.get("track"))
                self._is_playing = not data.get("paused", True)

                if data.get("track"):
                    track = data["track"]
                    self._metadata = {
                        "title": track.get("name"),
                        "artist": ", ".join(track.get("artist_names", [])),
                        "album": track.get("album_name"),
                        "album_art_url": track.get("album_cover_url"),
                        "duration": track.get("duration", 0),
                        "position": track.get("position", 0),
                        "uri": track.get("uri"),
                        "is_playing": self._is_playing
                    }
                else:
                    self._metadata = {}

                return True

        except (aiohttp.ClientConnectorError, aiohttp.ClientOSError):
            self._logger.debug("Metadata refresh skipped: go-librespot not reachable")
            return False
        except Exception as e:
            self._logger.error(f"Metadata refresh failed: {e}")
            return False

    async def _send_api_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send command to go-librespot API."""
        if not self._session or not self._api_url:
            return self.error_response("Session not active")

        try:
            async with self._session.post(
                f"{self._api_url}/player/{command}",
                json=payload or {}
            ) as resp:
                return self.success_response() if resp.status == 200 else self.error_response("Command failed")

        except Exception as e:
            return self.error_response(str(e))

    # === Auto-Disconnect Timer ===

    def _cancel_pause_timer(self) -> None:
        """Cancel auto-disconnect timer."""
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_timer(self) -> None:
        """Start auto-disconnect timer after pause."""
        if not self.auto_disconnect_enabled:
            return

        self._cancel_pause_timer()

        async def disconnect_after_delay():
            try:
                await asyncio.sleep(self.pause_disconnect_delay)
                self._logger.info(
                    f"Auto-disconnecting after {self.pause_disconnect_delay}s pause"
                )
                await self._do_restart()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._logger.error(f"Auto-disconnect failed: {e}")

        self._pause_timer = asyncio.create_task(disconnect_after_delay())

    # === Log Monitor ===

    def _start_log_monitor(self) -> None:
        """Start monitoring journalctl logs for go-librespot errors."""
        if self._log_monitor_task:
            return
        self._log_monitor_task = asyncio.create_task(self._monitor_logs())

    def _stop_log_monitor(self) -> None:
        """Stop log monitoring."""
        if self._log_monitor_task:
            self._log_monitor_task.cancel()
            self._log_monitor_task = None

    async def _monitor_logs(self) -> None:
        """Monitor journalctl for go-librespot errors."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "journalctl", "-u", "milo-spotify", "-f", "-n", "0",
                "--output", "cat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                text = line.decode('utf-8', errors='ignore').strip()
                await self._handle_log_line(text)

        except asyncio.CancelledError:
            if process:
                process.terminate()
                await process.wait()
        except Exception as e:
            self._logger.error(f"Log monitor error: {e}")

    async def _handle_log_line(self, line: str) -> None:
        """Parse and handle a log line from go-librespot."""
        # Success: connection established - clear any error
        if "authenticated AP" in line or "authenticated Login5" in line:
            self.broadcast_error_cleared()
            self._connection_error_count = 0
            return

        # Success: track loaded - clear any error
        if "loaded track" in line:
            self.broadcast_error_cleared()
            return

        # Critical error: track loading failed - show raw log message
        if "failed loading current track" in line:
            self.broadcast_error(self._extract_log_message(line))
            return

        # Connection error: show after 3 consecutive failures within 10s
        if "failed connecting to accesspoint" in line:
            now = time.time()
            if now - self._last_error_time < 10:
                self._connection_error_count += 1
            else:
                self._connection_error_count = 1
            self._last_error_time = now

            if self._connection_error_count >= 3:
                self.broadcast_error(self._extract_log_message(line))
                self._connection_error_count = 0

        # Ignore normal WebSocket closures (StatusNormalClosure)
        # These are expected when stopping the service

    def _extract_log_message(self, line: str) -> str:
        """
        Extract the msg and error fields from a go-librespot log line.

        Log format: level=warning msg="..." error="..."
        Returns the message exactly as it appears in the logs.
        """
        # Extract msg="..."
        msg_match = re.search(r'msg="([^"]+)"', line)
        msg = msg_match.group(1) if msg_match else ""

        # Extract error="..." (may not exist)
        error_match = re.search(r'error="([^"]+)"', line)
        error = error_match.group(1) if error_match else ""

        # Return exactly what's in the log
        if error:
            return f'{msg}: {error}'
        return msg if msg else "Unknown error"

    # === Helpers ===

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._cancel_pause_timer()
        self._stop_log_monitor()

        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None

        if self._session:
            await self._session.close()
            self._session = None

        self._ws_connected = False
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}

    def _update_connection_state(self) -> None:
        """Update state based on device connection."""
        if self._device_connected:
            self.set_state(SourceState.CONNECTED, {
                **self._metadata,
                "device_connected": True,
                "is_playing": self._is_playing
            })
        else:
            self.set_state(SourceState.READY, {
                "device_connected": False,
                "is_playing": False
            })

    # === Public API ===

    @property
    def api_url(self) -> Optional[str]:
        """Get API URL."""
        return self._api_url

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get current metadata."""
        return dict(self._metadata)

    @property
    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._is_playing

    @property
    def device_connected(self) -> bool:
        """Check if device is connected."""
        return self._device_connected

    @property
    def has_active_session(self) -> bool:
        """Check if HTTP session is active."""
        return self._session is not None

    async def set_auto_disconnect_config(
        self,
        enabled: bool,
        delay: Optional[float] = None,
        save_to_settings: bool = True
    ) -> bool:
        """
        Configure auto-disconnect behavior.

        Args:
            enabled: Whether auto-disconnect is enabled
            delay: Delay in seconds (0 = disabled)
            save_to_settings: Save to settings service

        Returns:
            True if configuration succeeded
        """
        old_enabled = self.auto_disconnect_enabled
        old_delay = self.pause_disconnect_delay

        # Handle delay = 0 as disabled
        if delay is not None and delay == 0:
            self.auto_disconnect_enabled = False
            self.pause_disconnect_delay = 10.0
        elif delay is not None:
            self.auto_disconnect_enabled = enabled
            self.pause_disconnect_delay = max(1.0, delay)
        else:
            self.auto_disconnect_enabled = enabled

        # Save to settings
        if save_to_settings and self._settings_service:
            try:
                save_value = 0.0 if not self.auto_disconnect_enabled else self.pause_disconnect_delay
                success = await self._settings_service.set_setting(
                    'spotify.auto_disconnect_delay', save_value
                )
                if not success:
                    self.auto_disconnect_enabled = old_enabled
                    self.pause_disconnect_delay = old_delay
                    return False
            except Exception as e:
                self._logger.error(f"Settings save failed: {e}")
                self.auto_disconnect_enabled = old_enabled
                self.pause_disconnect_delay = old_delay
                return False

        # Manage running timer
        if self._pause_timer and not self._pause_timer.done():
            self._cancel_pause_timer()
            if self.auto_disconnect_enabled and self._device_connected and not self._is_playing:
                self._start_pause_timer()

        return True
