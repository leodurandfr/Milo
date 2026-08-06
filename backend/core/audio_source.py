# backend/core/audio_source.py
"""BaseAudioSource - base class for all audio sources."""
from typing import Dict, Any, Optional, Type
from abc import ABC, abstractmethod
import asyncio
import logging

from pydantic import BaseModel, ValidationError

from backend.core.models.audio_state import AudioSource, NetworkRequirement, SourceState
from backend.core.models.source_metadata import PlaybackMetadata
from backend.core.models.ws_events import (
    SourceError,
    SourceErrorCleared,
    SourcePositionUpdate,
)
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)


def _format_validation_error(cmd: str, error: ValidationError) -> str:
    """Flatten a Pydantic ValidationError into a single human-readable line.

    Drops the pydantic doc URL / input echo / model name that str(error) carries,
    keeping only the offending field(s) and reason — used as the command's error
    message (surfaced by run_source_command as the HTTP 400 detail, and logged).
    """
    details = "; ".join(
        f"{'.'.join(map(str, err['loc'])) or '(root)'}: {err['msg']}"
        for err in error.errors()
    )
    return f"Invalid parameters for '{cmd}': {details}"


class BaseAudioSource(ABC):
    """
    Base implementation for audio sources.

    Provides common functionality:
    - Systemd service management
    - WebSocket broadcasting via state machine
    - Standard response formatting
    - Lifecycle management (initialize, start, stop)

    Subclasses must implement:
    - _do_start(): Source-specific startup logic
    - _do_stop(): Source-specific shutdown logic

    Optional overrides:
    - _do_restart(): Custom restart logic (default: stop + start)
    - _handle_command(): Source-specific commands
    - release_for_reroute() / acquire_after_reroute(): lighter device
      release/re-acquire for a multiroom MILO_MODE change (default: stop()
      / start()). Override only when the upstream link is held by a separate
      process from the ALSA writer (e.g. Bluetooth: bluez/bluealsa hold the
      link, bluealsa-aplay is the writer).

    Example:
        class RadioSource(BaseAudioSource):
            async def _do_start(self) -> bool:
                # Start mpv, connect to stream, etc.
                return True

            async def _do_stop(self) -> bool:
                # Stop mpv, cleanup
                return True

            COMMANDS = {"tune": TuneParams, "stop": None}

            async def _handle_command(self, cmd, params) -> Dict:
                if cmd == "tune":
                    return self.success_response(f"Tuned to {params.station}")
                ...
    """

    # Per-command parameter contract: command name -> Pydantic model (or None for
    # no-param commands). command() validates raw input against this before dispatch,
    # so _handle_command() receives a validated model. Override per source; an empty
    # map (the default) makes command() reject every command as unknown.
    COMMANDS: Dict[str, Optional[Type[BaseModel]]] = {}

    # What this source needs from the network to work at all. The state machine
    # crosses it with NetworkManager's connectivity level to decide whether the
    # active source is blocked (full_state.network_unavailable), so a link
    # problem is reported to the user only when it actually breaks what they
    # selected. NONE is the safe default: it reports nothing.
    NETWORK_REQUIREMENT: NetworkRequirement = NetworkRequirement.NONE

    def __init__(
        self,
        source_id: str,
        service_name: str,
        state_machine=None,
        systemd_manager=None,
        settings_service=None,
        config=None
    ):
        """
        Initialize the audio source.

        Args:
            source_id: Unique identifier (e.g., "radio", "spotify")
            service_name: Systemd service name (e.g., "milo-radio")
            state_machine: Optional state machine for state synchronization
            systemd_manager: Optional SystemdServiceManager (injected via DI)
            settings_service: Optional SettingsService for persisting configuration
            config: Optional source-specific configuration dict
        """
        self.source_id = source_id
        self.service_name = service_name
        self.state_machine = state_machine

        self._state = SourceState.READY
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._error: Optional[str] = None
        self._error_active = False
        self._initialized = False

        self._service_manager = systemd_manager
        self._settings_service = settings_service
        self._config = config or {}
        self._logger = logging.getLogger(f"source.{source_id}")
        self._bg = BackgroundTaskSet(self._logger, f"source.{source_id}")

        # Auto-stop timer (opt-in, subclasses override _on_auto_stop)
        self.auto_stop_enabled: bool = False
        self.auto_stop_delay: float = 10.0
        self._pause_timer: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> SourceState:
        """Current state of the source."""
        return self._state

    @property
    def metadata(self) -> Dict[str, Any]:
        """Current metadata."""
        return self._metadata.copy()

    @property
    def is_playing(self) -> bool:
        """Whether the source is currently playing."""
        return self._is_playing

    @property
    def source(self) -> AudioSource:
        """AudioSource enum for this source."""
        return AudioSource(self.source_id)

    @property
    def is_initialized(self) -> bool:
        """Whether initialize() has already run (set by initialize() itself)."""
        return self._initialized

    async def start(self) -> bool:
        """
        Start the audio source.

        Calls _do_start() for source-specific logic.

        Returns:
            True if start successful
        """
        self._logger.info(f"Starting {self.source_id}")
        self._state = SourceState.STARTING
        self._error = None

        try:
            success = await self._do_start()

            if success:
                # State should be set by _do_start (READY or ACTIVE)
                if self._state == SourceState.STARTING:
                    self._state = SourceState.READY

                self._logger.info(f"{self.source_id} started successfully")
            else:
                self._state = SourceState.ERROR
                self._error = "Start failed"

            return success

        except Exception as e:
            self._logger.error(f"Error starting {self.source_id}: {e}")
            self._state = SourceState.ERROR
            self._error = str(e)
            return False

    async def stop(self) -> bool:
        """
        Stop the audio source.

        Calls _do_stop() for source-specific logic.

        Returns:
            True if stop successful
        """
        self._logger.info(f"Stopping {self.source_id}")
        self._cancel_pause_timer()
        # Drain any stale in-flight broadcasts from the previous state.
        # Done before _do_stop so the broadcasts it emits (e.g. set_state(READY))
        # run to completion after stop() returns.
        await self._bg.cancel_all()

        try:
            success = await self._do_stop()

            if success:
                self._state = SourceState.READY
                self._metadata = {}
                self._error = None

                self._logger.info(f"{self.source_id} stopped successfully")
            else:
                self._logger.warning(f"Failed to stop {self.source_id}")

            return success

        except Exception as e:
            self._logger.error(f"Error stopping {self.source_id}: {e}")
            return False

    async def release_for_reroute(self) -> bool:
        """Release the ALSA output device for a MILO_MODE (direct↔multiroom)
        change while keeping any upstream sender connection alive.

        Called by AudioRoutingService._apply_transition instead of stop() so a
        multiroom toggle does not tear down the sender link. Default = full
        stop() (correct when the connection and the ALSA writer are the same
        process). Override when the connection is held by a separate process
        from the writer (Bluetooth: bluez/bluealsa hold the A2DP link,
        bluealsa-aplay is the writer).
        """
        return await self.stop()

    async def acquire_after_reroute(self) -> bool:
        """Re-acquire the ALSA output device after routing.env was regenerated
        with the new MILO_MODE. Mirror of release_for_reroute(); default start().
        """
        return await self.start()

    async def command(self, cmd: str, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate and execute a source-specific command.

        Single validation boundary for every producer (generic control route,
        run_source_command, hardware playback dispatch): the command name is
        checked against COMMANDS, then its params are validated against the
        registered Pydantic model before _handle_command() runs on typed input.

        Always returns a response dict (never raises) so run_source_command maps
        bad input to HTTP 400, not 500.

        Args:
            cmd: Command name
            data: Raw command parameters (may be None from the public route)

        Returns:
            Response dict with success, message/error, and custom data
        """
        self._logger.debug(f"Command: {cmd} with data: {data}")

        payload = data or {}
        if cmd not in self.COMMANDS:
            return self.error_response(f"Unknown command: {cmd}")

        model = self.COMMANDS[cmd]
        try:
            params = model.model_validate(payload) if model else None
        except ValidationError as e:
            self._logger.warning(f"Invalid params for '{cmd}': {e}")
            return self.error_response(_format_validation_error(cmd, e))

        try:
            return await self._handle_command(cmd, params)
        except Exception as e:
            self._logger.error(f"Error handling command {cmd}: {e}")
            return self.error_response(str(e))

    # === Abstract methods for subclasses ===

    @abstractmethod
    async def _do_start(self) -> bool:
        """
        Source-specific startup implementation.

        Should:
        - Start systemd service if needed
        - Establish connections
        - Set self._state to READY or ACTIVE
        - Update self._metadata with initial data

        Returns:
            True if startup successful
        """
        pass

    async def _cleanup(self) -> None:
        """
        Source-specific resource cleanup (connections, tasks, state).

        Override in subclasses to clean up before service stop.
        Called by the default _do_stop() and can be called from _do_start()
        on failure. The outer stop() method handles exceptions.
        """
        pass

    def _reset_playback_state(self) -> None:
        """Reset playback state to idle defaults.

        Subclasses should call super()._reset_playback_state() then clear
        their own fields (e.g. _is_buffering, _device_connected, _current_station).
        """
        self._is_playing = False
        self._metadata = {}

    async def _do_stop(self) -> bool:
        """
        Stop the source: cleanup resources then stop the service.

        Default implementation calls _cleanup() then _stop_service().
        Override for custom shutdown logic (e.g., saving state before cleanup).
        The outer stop() method handles exceptions.

        Returns:
            True if shutdown successful
        """
        await self._cleanup()
        return await self._stop_service()

    async def _do_restart(self) -> bool:
        """
        Source-specific restart implementation.

        Sole entry point is the default _on_auto_stop() (auto-stop timer);
        there is no public restart() wrapper. Default: stop + start.
        Override for custom restart logic (e.g., preserve state) — AirPlay does.
        A source that instead wants a different auto-stop *action* overrides
        _on_auto_stop() (Spotify, DLNA, and the shared MpvAudioSource).

        Returns:
            True if restart successful
        """
        if not await self.stop():
            return False
        return await self.start()

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """
        Source-specific command handling.

        Override to implement source-specific commands. command() has already
        rejected unknown commands and validated params against COMMANDS[cmd], so
        params is the validated model (or None for no-param commands).

        Args:
            cmd: Command name (guaranteed present in COMMANDS)
            params: Validated parameter model, or None

        Returns:
            Response dict
        """
        return self.error_response(f"Unhandled command: {cmd}")

    async def refresh_metadata(self) -> bool:
        """Re-read metadata from the underlying player into self._metadata.

        Called by AudioStateMachine.refresh_active_metadata() on the active
        source (GET /api/audio/state, WS reconnect). Default: no-op for sources
        whose metadata is pushed by an event feed rather than polled.

        Returns:
            True if self._metadata was refreshed.
        """
        return False

    # === Auto-Stop Timer ===

    def _cancel_pause_timer(self) -> None:
        """Cancel auto-stop timer."""
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_timer(self) -> None:
        """Start auto-stop timer after pause/inactivity."""
        if not self.auto_stop_enabled:
            return

        self._cancel_pause_timer()

        async def stop_after_delay():
            try:
                await asyncio.sleep(self.auto_stop_delay)
            except asyncio.CancelledError:
                return
            # Detach the task ref so re-entrant _cancel_pause_timer() calls
            # (e.g. from stop() inside _on_auto_stop) become no-ops.
            self._pause_timer = None
            self._logger.info(
                f"Auto-stopping after {self.auto_stop_delay}s pause"
            )
            try:
                await self._on_auto_stop()
            except Exception as e:
                self._logger.error(f"Auto-stop failed: {e}")

        self._pause_timer = asyncio.create_task(stop_after_delay())

    async def _on_auto_stop(self) -> None:
        """
        Called when the auto-stop timer fires.

        Default: restart the source. Override for custom behavior.
        """
        await self._do_restart()

    AUTO_STOP_SETTINGS_KEY = "audio.auto_stop_delay"

    async def _load_auto_stop_config(self) -> None:
        """Load the global auto-stop delay from settings."""
        if not self._settings_service:
            return

        try:
            delay = await self._settings_service.get_setting(self.AUTO_STOP_SETTINGS_KEY)
            if delay is not None:
                if delay == 0:
                    self.auto_stop_enabled = False
                    self.auto_stop_delay = 10.0
                else:
                    self.auto_stop_enabled = True
                    self.auto_stop_delay = float(delay)

            self._logger.info(
                f"Auto-stop: enabled={self.auto_stop_enabled}, "
                f"delay={self.auto_stop_delay}s"
            )
        except Exception as e:
            self._logger.error(f"Auto-stop settings load failed: {e}")

    async def reload_auto_stop_config(self) -> bool:
        """
        Reload the global auto-stop delay and refresh any running timer.

        Called from the settings API when the global delay changes so live
        sources pick up the new value without a restart.
        """
        await self._load_auto_stop_config()

        # Refresh a pending timer so the new delay takes effect immediately.
        if self._pause_timer and not self._pause_timer.done():
            self._cancel_pause_timer()
            if self.auto_stop_enabled:
                self._start_pause_timer()

        return True

    # === Monitor task ===

    def _start_monitor(self) -> None:
        """Start the monitor loop task. Subclasses must implement _monitor_loop()."""
        if self._monitor_task:
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    def _stop_monitor(self) -> None:
        """Stop the monitor loop task."""
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    # === Helper methods ===

    async def _start_service(self, service_name: str = None) -> bool:
        """Start a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.start(name)
        except Exception as e:
            self._logger.error(f"Failed to start service {name}: {e}")
            return False

    async def _stop_service(self, service_name: str = None) -> bool:
        """Stop a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.stop(name)
        except Exception as e:
            self._logger.error(f"Failed to stop service {name}: {e}")
            return False

    async def _restart_service(self, service_name: str = None) -> bool:
        """Restart a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.restart(name)
        except Exception as e:
            self._logger.error(f"Failed to restart service {name}: {e}")
            return False

    async def _is_service_active(self, service_name: str = None) -> bool:
        """Check if a systemd service is active (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.is_active(name)
        except Exception as e:
            self._logger.error(f"Failed to check if service '{name}' is active: {e}")
            return False

    async def _start_service_and_wait(self, settle: float = 0.5) -> bool:
        """Start the systemd service and wait for it to settle."""
        if not await self._start_service():
            return False
        await asyncio.sleep(settle)
        return True

    async def _restart_service_and_wait(self, settle: float = 0.5) -> bool:
        """Restart the systemd service and wait for it to settle."""
        if not await self._restart_service():
            return False
        await asyncio.sleep(settle)
        return True

    async def initialize(self) -> bool:
        """
        Initialize the audio source.

        Called during application startup for sources that need
        early initialization (e.g., loading station data for API access).
        """
        self._initialized = True
        return True

    def set_state(self, state: SourceState, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Set state and optionally replace metadata.

        Syncs with state_machine if available (active sources only).

        Args:
            state: New state (SourceState enum)
            metadata: Authoritative metadata for the new state, or None for a
                state-only change (leaves the current metadata untouched).
        """
        self._state = state
        # Replace, don't merge — same rule as update_source_state(), so the
        # source's copy and the machine's cannot diverge. A source that wants
        # a field kept re-emits it (the four accumulator sources hand their own
        # dict back through emit_connection_state, which round-trips it).
        if metadata is not None:
            self._metadata = dict(metadata)

        if self.state_machine:
            self._bg.spawn(
                self.state_machine.update_source_state(
                    self.source, state, metadata
                ),
                label="set_state",
            )

    def emit_connection_state(
        self,
        connected: bool,
        playback: Optional[PlaybackMetadata] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish the source's connection/playback state — the single path
        that replaces per-source active/idle metadata dicts.

        - ``connected`` selects ACTIVE vs READY.
        - ``playback`` is the typed projection consumed by the shared player
          (None for mute receivers). Its is_playing/is_buffering always emit;
          on READY they are forced off and the media fields (title/artist/
          album/album_art_url/position/duration) are dropped so a stale track
          can't linger.
        - ``extras`` are source-specific fields (station/episode/disc/device);
          they pass through in both states, so a source that wants device or
          disc status visible while idle includes it (e.g. CD drive state).
          ``None`` values are dropped, the same rule ``exclude_none`` applies to
          the typed half — one record, one convention. Nothing can read the
          difference anyway: every consumer tests the field for truthiness, so a
          key present-and-null says exactly what an absent key says while
          costing a line on the wire. Dropping it here rather than per source is
          what stops the next `extras["x"] = self._maybe_none` from putting one
          back. Safe because metadata is *replaced* on every state update
          (`update_source_state`), never merged — an absent key cannot leave a
          stale value behind.
        """
        if connected:
            meta: Dict[str, Any] = (
                playback.model_dump(exclude_none=True) if playback is not None else {}
            )
        else:
            meta = {"is_playing": False, "is_buffering": False} if playback is not None else {}
        if extras:
            meta.update({k: v for k, v in extras.items() if v is not None})
        self.set_state(SourceState.ACTIVE if connected else SourceState.READY, meta)

    def broadcast_position_update(self, position: int, duration: int) -> None:
        """Broadcast a lightweight position update without full_state.

        Used during steady playback where the frontend interpolates
        locally and only needs periodic drift correction.

        Also keeps system_state.metadata in sync so that initial_state
        sent on new WebSocket connections contains the live position.

        Args:
            position: Current position in milliseconds.
            duration: Total duration in milliseconds.
        """
        if not self.state_machine:
            return

        self._bg.spawn(
            self._push_position(position, duration),
            label="broadcast_position_update",
        )

    async def _push_position(self, position: int, duration: int) -> None:
        """Sync then broadcast the position. Both steps are awaited here so the
        system_state write goes through the state machine's lock like every
        other state mutation (it cannot be taken from the sync caller above)."""
        await self.state_machine.update_position_metadata(self.source, position, duration)
        await self.state_machine.broadcast(SourcePositionUpdate(
            source=self.source.value,
            position=position,
            duration=duration,
        ))

    def broadcast_error(self, error_message: str) -> None:
        """
        Broadcast a failed *operation* to the UI notification banner.

        Bypasses the active-source filter so errors are always shown
        regardless of which source is currently active.

        The source itself stays operational — a station that will not tune
        leaves the browser perfectly usable. A source that is genuinely down
        publishes SourceState.ERROR instead (the state machine does it for a
        failed transition); the two never ride on the same event.
        """
        if not self.state_machine:
            return

        self._error_active = True
        self._bg.spawn(
            self.state_machine.broadcast(SourceError(
                source=self.source.value,
                message=error_message,
            )),
            label="broadcast_error",
        )

    def broadcast_error_cleared(self) -> None:
        """
        Clear any displayed error for this source.

        No-op if no error was previously broadcast — call sites can invoke
        this unconditionally on successful operations without producing wire
        noise. The UI dismisses the banner when the event is emitted.
        """
        if not self.state_machine or not self._error_active:
            return

        self._error_active = False
        self._bg.spawn(
            self.state_machine.broadcast(
                SourceErrorCleared(source=self.source.value)
            ),
            label="broadcast_error_cleared",
        )

    def success_response(self, message: str = None, **kwargs) -> Dict[str, Any]:
        """
        Create a success response for commands.

        Args:
            message: Optional success message
            **kwargs: Additional fields

        Returns:
            Response dict with success=True
        """
        response = {"success": True}
        if message:
            response["message"] = message
        return {**response, **kwargs}

    def error_response(self, error: str, **kwargs) -> Dict[str, Any]:
        """
        Create an error response for commands.

        Args:
            error: Error message
            **kwargs: Additional fields

        Returns:
            Response dict with success=False
        """
        return {"success": False, "error": error, **kwargs}
