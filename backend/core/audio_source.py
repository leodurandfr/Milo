# backend/core/audio_source.py
"""BaseAudioSource - base class for all audio sources."""
from typing import Dict, Any, Optional, Tuple, Type
from abc import ABC, abstractmethod
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
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


# === The actor's mail ===
#
# Everything that touches a source's state is a message, handled one at a time
# by the source's own task. A check made before an await is then still true
# after it — the property four separate races lacked (E04, E05, E48, E49).
# Feeds and results from work done elsewhere join the same mailbox as their
# sources migrate onto it.

class LifecycleStep(str, Enum):
    START = "start"
    STOP = "stop"
    RELEASE = "release"   # multiroom reroute: let go of the ALSA device
    ACQUIRE = "acquire"   # multiroom reroute: take it back under the new mode


@dataclass(eq=False)
class Command:
    name: str
    params: Optional[BaseModel]


@dataclass(eq=False)
class Lifecycle:
    step: LifecycleStep


@dataclass(eq=False)
class Timer:
    """The pause timer's expiry. `token` is the timer it came from: a timer
    disarmed or re-armed since makes the message stale, and it is dropped."""
    token: object


@dataclass(eq=False)
class Query:
    """A metadata re-read for a state request. Posted only to an idle source."""


# Which running message a lifecycle message cuts short. Stop beats ordered:
# a load still waiting on the network, or a start the transition gave up on,
# does not get to finish over a source that was told to stop.
_PREEMPTS = {
    LifecycleStep.STOP: (Command, Timer, Query, LifecycleStep.ACQUIRE,
                         LifecycleStep.RELEASE),
    LifecycleStep.RELEASE: (Command, Timer, Query),
}


class _MailboxClosed(Exception):
    """A hold still waiting for the mailbox when the source was shut down."""


@dataclass(eq=False)
class _Lease:
    """The mailbox held for a message its caller runs in its own task (START).

    The actor grants it in turn and then waits for its release, so nothing else
    runs meanwhile; the caller runs the handler without the task hop a posted
    message costs. That hop is what START cannot afford: the state machine
    clears `transitioning` in the same stretch that START returns in, and a
    single yielded turn in between lets every publish the start spawned run
    first — and be dropped — where they have always landed just after
    transition_complete. The transition's own timeout cuts a leased START, as
    it always has; the stop that follows is a posted STOP and is never cut.
    """
    message: object
    granted: asyncio.Future
    released: asyncio.Event

# The message being handled, its source, and the one task running it. A post
# made from that task (the auto-stop's stop + start) runs inline: queueing it
# behind the handler that awaits it would be a deadlock. The task is part of the
# key because a context is copied into every task created meanwhile — a feed or a
# monitor started by the handler — and those must queue like anyone else.
_handling: ContextVar[Optional[Tuple["BaseAudioSource", object, asyncio.Task]]] = ContextVar(
    "source_actor_handling", default=None
)

# Fields that travel on the position axis (broadcast_position_update), left out
# of the projection compare so a playhead moving between two publishes is not
# a state change.
_POSITION_FIELDS = ("position", "duration")


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
    - _do_release() / _do_acquire(): lighter device release/re-acquire for a
      multiroom MILO_MODE change (default: the whole stop / start). Override
      only when the upstream link is held by a separate process from the ALSA
      writer (e.g. Bluetooth: bluez/bluealsa hold the link, bluealsa-aplay is
      the writer).

    The actor: start(), stop(), release_for_reroute(), acquire_after_reroute(),
    command(), the pause timer's expiry and refresh_when_idle() are messages to
    one mailbox, handled one at a time by one task (created on the first post,
    ended by shutdown()). A caller that stops waiting — the transition's 15 s
    budget — never cuts the handler. STOP and RELEASE cut the handler in
    flight instead of queueing behind it (see _PREEMPTS).

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

    # Whether the global auto-stop delay applies to this source at all. False
    # for the two receivers that arm no pause timer (Bluetooth, Mac), and it has
    # to be declared rather than left to their `auto_stop_enabled` default:
    # reload_auto_stop_for_all_sources() fans out over *every* registered
    # source, so editing audio.auto_stop_delay used to switch their flag back on
    # behind the comment saying it is off. Nothing was armed by it — neither
    # calls _start_pause_timer — which is why it could stay wrong.
    AUTO_STOP_SUPPORTED: bool = True

    # A receiver with no playback concept at all: it carries `extras` and
    # nothing else — no transport, no media fields, nothing a shared player
    # could draw (Mac; see core/models/source_metadata.py). Declared on the
    # class rather than inferred from a `playback=None` argument at a call site:
    # inferred, a media source that forgot the typed half silently published a
    # bare {} with no is_playing key in it, which is what two of Music
    # Library's stop paths did. Declared, that same call publishes the source's
    # own idle projection instead.
    MUTE_RECEIVER: bool = False

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
        self._pause_token: Optional[object] = None
        self._monitor_task: Optional[asyncio.Task] = None

        # The actor (see the class docstring).
        self._actor_urgent: deque = deque()
        self._actor_inbox: deque = deque()
        self._actor_mail: Optional[asyncio.Event] = None
        self._actor_task: Optional[asyncio.Task] = None
        self._actor_current: Optional[object] = None
        self._actor_handler: Optional[asyncio.Task] = None
        self._actor_closed = False
        # The projection at the last publish, for the net after a command.
        self._published: Optional[Tuple[SourceState, Dict[str, Any]]] = None

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

    # === Public entry points: each one is a message to the actor ===

    async def start(self) -> bool:
        """Start the source (START, leased — see _Lease). True when it came up."""
        return await self._run_leased(Lifecycle(LifecycleStep.START))

    async def stop(self) -> bool:
        """Stop the source (STOP), cutting short whatever it was doing."""
        return await self._submit(Lifecycle(LifecycleStep.STOP))

    async def release_for_reroute(self) -> bool:
        """Release the ALSA output device for a MILO_MODE (direct↔multiroom)
        change while keeping any upstream sender connection alive (RELEASE).

        Run by AudioStateMachine.reroute_active_source() instead of stop() so a
        multiroom toggle does not tear down the sender link. What it does is
        `_do_release()`.
        """
        return await self._submit(Lifecycle(LifecycleStep.RELEASE))

    async def acquire_after_reroute(self) -> bool:
        """Re-acquire the ALSA output device after routing.env was regenerated
        with the new MILO_MODE (ACQUIRE). Mirror of release_for_reroute()."""
        return await self._submit(Lifecycle(LifecycleStep.ACQUIRE))

    async def command(self, cmd: str, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate and execute a source-specific command.

        Single validation boundary for every producer (generic control route,
        run_source_command, hardware playback dispatch): the command name is
        checked against COMMANDS, then its params are validated against the
        registered Pydantic model — here, outside the actor — before the
        command is posted and _handle_command() runs on typed input.

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

        return await self._submit(Command(cmd, params))

    async def refresh_when_idle(self) -> bool:
        """Re-read metadata for a state request, unless the source is busy.

        GET /api/audio/state and the WS handshake come through here. A read
        never waits behind a handler — Milo-iOS gives up after 3 s, a podcast
        resume can hold its handler for ten — and never runs inside one either,
        so it cannot overwrite what a command in flight is changing. Busy, the
        caller keeps the record already published, which is what that handler
        will replace when it is done.

        Returns:
            True if self._metadata was refreshed.
        """
        if self._actor_closed or self._actor_current is not None or self._actor_urgent or self._actor_inbox:
            return False
        return await self._submit(Query())

    async def shutdown(self) -> None:
        """End the mailbox for good: backend teardown (main.py). Pending callers
        get the answer an interrupted message gets; later posts are refused."""
        self._actor_closed = True
        task, self._actor_task = self._actor_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for queue in (self._actor_urgent, self._actor_inbox):
            while queue:
                message, future = queue.popleft()
                if isinstance(message, _Lease):
                    if not message.granted.done():
                        message.granted.set_exception(_MailboxClosed())
                    continue
                if not future.done():
                    future.set_result(self._interrupted_result(message, "shutdown"))
        self._cancel_pause_timer()
        await self._bg.cancel_all()

    # === The actor ===

    def _is_handling_here(self) -> bool:
        """Whether this task is the one running the source's current message."""
        current = _handling.get()
        return (
            current is not None
            and current[0] is self
            and current[1] is self._actor_current
            and current[2] is asyncio.current_task()
        )

    async def _submit(self, message) -> Any:
        """Post `message` and wait for its result.

        The wait is shielded: a caller that gives up (a timeout, a client that
        hung up) stops waiting, and the handler goes on to the end.
        """
        if self._is_handling_here():
            return await self._dispatch(message)
        return await asyncio.shield(self._post(message))

    def _post(self, message) -> asyncio.Future:
        """Queue `message` without waiting (the pause timer's expiry)."""
        future = asyncio.get_running_loop().create_future()
        if self._actor_closed:
            future.set_result(self._interrupted_result(message, "shutdown"))
            return future
        if self._actor_mail is None:
            self._actor_mail = asyncio.Event()
        if self._actor_task is None or self._actor_task.done():
            if self._actor_task is not None:
                self._logger.error("The mailbox task had ended; starting a new one")
            self._actor_task = asyncio.create_task(
                self._run_actor(), name=f"source.{self.source_id}.actor"
            )
        step = message.step if isinstance(message, Lifecycle) else None
        if step is LifecycleStep.STOP:
            # What was queued before the stop targets the session it ends: it is
            # answered as interrupted rather than run against a stopped source.
            kept = deque()
            while self._actor_inbox:
                queued, queued_future = self._actor_inbox.popleft()
                if isinstance(queued, (Command, Timer, Query)):
                    if not queued_future.done():
                        queued_future.set_result(self._interrupted_result(queued, "stop"))
                else:
                    kept.append((queued, queued_future))
            self._actor_inbox = kept
        if step in _PREEMPTS:
            self._actor_urgent.append((message, future))
            if self._actor_current is not None and self._actor_handler is not None:
                running = self._actor_current
                key = running.step if isinstance(running, Lifecycle) else type(running)
                # A lease has no handler task to cut (see _Lease).
                if key in _PREEMPTS[step]:
                    self._logger.info(
                        "%s cuts short %s", step.value, self._describe(running)
                    )
                    self._actor_handler.cancel()
        else:
            self._actor_inbox.append((message, future))
        self._actor_mail.set()
        return future

    @asynccontextmanager
    async def hold_mailbox(self):
        """Hold this source's mailbox for the length of the block.

        Nothing queued runs meanwhile; this task's own posts run inline. What
        the multiroom reroute holds across RELEASE, the output switch and
        ACQUIRE, so a command or a stop arriving in between waits for the whole
        of it — a resume does not land on an output parked on `null`, and a stop
        is not undone by the reacquire. A hold cannot be preempted: there is no
        handler task to cut, and a stop posted meanwhile runs right after it.
        """
        if self._is_handling_here():
            yield
            return
        if self._actor_closed:
            raise _MailboxClosed()
        lease = _Lease(None, asyncio.get_running_loop().create_future(), asyncio.Event())
        self._post(lease)
        try:
            await lease.granted
        except BaseException:
            lease.released.set()
            raise
        token = _handling.set((self, lease, asyncio.current_task()))
        try:
            yield
        finally:
            _handling.reset(token)
            lease.released.set()

    async def _run_leased(self, message) -> Any:
        """Run `message` in this task while holding the mailbox (see _Lease)."""
        try:
            async with self.hold_mailbox():
                return await self._dispatch(message)
        except _MailboxClosed:
            return self._interrupted_result(message, "shutdown")

    async def _run_actor(self) -> None:
        while True:
            if not self._actor_urgent and not self._actor_inbox:
                self._actor_mail.clear()
                await self._actor_mail.wait()
                continue
            message, future = (self._actor_urgent or self._actor_inbox).popleft()
            if isinstance(message, _Lease):
                self._actor_current = message
                try:
                    if not message.granted.done():
                        message.granted.set_result(None)
                        await message.released.wait()
                finally:
                    self._actor_current = None
                if not future.done():
                    future.set_result(None)
                continue
            self._actor_current = message
            self._actor_handler = asyncio.create_task(
                self._handle_in_context(message),
                name=f"source.{self.source_id}.handler",
            )
            try:
                await asyncio.wait({self._actor_handler})
            except asyncio.CancelledError:
                self._actor_handler.cancel()
                if not future.done():
                    future.set_result(self._interrupted_result(message, "shutdown"))
                raise
            finally:
                handler, self._actor_handler, self._actor_current = self._actor_handler, None, None
            if handler.cancelled():
                result = self._interrupted_result(message, "preempted")
            elif handler.exception() is not None:
                exc = handler.exception()
                self._logger.error(
                    f"{self._describe(message)} raised: {exc}", exc_info=exc
                )
                result = self._interrupted_result(message, str(exc))
            else:
                result = handler.result()
                if isinstance(message, (Command, Timer)):
                    # Per message, like any loop body: a projection that raises
                    # costs this republish, never the mailbox.
                    try:
                        self._republish_if_moved()
                    except Exception as e:
                        self._logger.error(f"Republishing after {self._describe(message)} failed: {e}")
            if not future.done():
                future.set_result(result)

    async def _handle_in_context(self, message) -> Any:
        _handling.set((self, message, asyncio.current_task()))
        return await self._dispatch(message)

    async def _dispatch(self, message) -> Any:
        if isinstance(message, Command):
            return await self._run_command(message.name, message.params)
        if isinstance(message, Lifecycle):
            if message.step is LifecycleStep.START:
                return await self._run_start()
            if message.step is LifecycleStep.STOP:
                return await self._run_stop()
            if message.step is LifecycleStep.RELEASE:
                return await self._do_release()
            return await self._do_acquire()
        if isinstance(message, Timer):
            return await self._run_timer(message.token)
        if isinstance(message, Query):
            return await self.refresh_metadata()
        raise TypeError(f"not a source message: {message!r}")

    def _interrupted_result(self, message, why: str) -> Any:
        """What a caller gets back for a message that did not run to its end."""
        if isinstance(message, _Lease):
            return None
        if isinstance(message, Command):
            return self.error_response(f"'{message.name}' interrupted ({why})")
        if isinstance(message, Timer):
            return None
        return False

    @staticmethod
    def _describe(message) -> str:
        if isinstance(message, Command):
            return f"command '{message.name}'"
        if isinstance(message, Lifecycle):
            return message.step.value
        return type(message).__name__.lower()

    # === Handlers ===

    async def _run_start(self) -> bool:
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

    async def _run_stop(self) -> bool:
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

    async def _do_release(self) -> bool:
        """What RELEASE does. Default: the whole stop — correct when the
        connection and the ALSA writer are the same process. Override when the
        connection is held by a separate process from the writer (Bluetooth:
        bluez/bluealsa hold the A2DP link, bluealsa-aplay is the writer)."""
        return await self._run_stop()

    async def _do_acquire(self) -> bool:
        """What ACQUIRE does. Default: the whole start."""
        return await self._run_start()

    async def _run_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        try:
            return await self._handle_command(cmd, params)
        except Exception as e:
            self._logger.error(f"Error handling command {cmd}: {e}")
            return self.error_response(str(e))

    async def _run_timer(self, token: object) -> None:
        if token is not self._pause_token:
            self._logger.debug("Dropping a pause-timer expiry that was disarmed since")
            return None
        self._pause_token = None
        self._pause_timer = None
        self._logger.info(f"Auto-stopping after {self.auto_stop_delay}s pause")
        try:
            await self._on_auto_stop()
        except Exception as e:
            self._logger.error(f"Auto-stop failed: {e}")
        return None

    # === Publication net ===

    def _connection_state(
        self,
    ) -> Optional[Tuple[bool, Optional[PlaybackMetadata], Optional[Dict[str, Any]]]]:
        """(connected, playback, extras) as the source would publish them now.

        Pure: no side effect, nothing sent. Every source's
        `_update_connection_state()` publishes exactly this (plus the fields of
        one transition), which is what lets the actor tell, after a command,
        whether the source changed without saying so. None: no projection
        (the net is off for this source).
        """
        return None

    def _project(self) -> Optional[Tuple[SourceState, Dict[str, Any]]]:
        """The old wire's (state, metadata) for the source's current fields,
        minus the position axis."""
        args = self._connection_state()
        if args is None:
            return None
        state, meta = self._compose(*args)
        for key in _POSITION_FIELDS:
            meta.pop(key, None)
        return state, meta

    def _republish_if_moved(self) -> None:
        """The net after a command or an auto-stop: a handler that changed the
        source and published nothing (mpv refusing a load — E41) is published
        anyway. Compared, not repeated: a handler that did publish costs no
        second envelope."""
        if self._published is None:
            return
        projection = self._project()
        if projection is not None and projection != self._published:
            self._logger.debug("State changed without a publish — publishing it")
            self._update_connection_state()

    def _update_connection_state(self) -> None:
        """The source's one publish site (overridden by every source)."""

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

    def _idle_metadata(self) -> Dict[str, Any]:
        """The metadata that describes this source stopped, for _publish_idle().

        Default is the pair every player reads. A source whose idle view still
        has something to show overrides it (CD keeps the loaded disc visible).
        """
        return {"is_playing": False, "is_buffering": False}

    def _idle_payload(self) -> Dict[str, Any]:
        """`_idle_metadata()` as it goes on the wire — the one definition.

        Two things happen here and nowhere else. Nones are dropped, the same
        rule `exclude_none` applies to the typed half (a key present-and-null
        says what an absent key says, at the cost of a line on the wire) — the
        two idle routes disagreed on exactly this, `emit_connection_state`
        filtering and `_publish_idle` not, so one state had two shapes
        depending on which path published it. And the inert pair is forced:
        READY *means* not playing, while three of the four overrides project
        from live fields (radio's station, podcast's episode, CD's disc), so a
        stale True has a path onto a payload that denies it. Forcing it is the
        definition of the state, not a guard against a caller.
        """
        payload = {k: v for k, v in self._idle_metadata().items() if v is not None}
        payload["is_playing"] = False
        payload["is_buffering"] = False
        return payload

    async def _publish_idle(self) -> None:
        """Drop to READY and publish it — awaited, not spawned.

        For the paths that clear playback themselves and never announce it: the
        mpv disconnect hooks, the storage-gone stop. Both emit something else
        right after (an error banner, a storages payload), and those events carry
        INCLUDE_FULL_STATE — so without this the client is told playback broke
        *and* handed a state saying the track is still running, permanently
        (IDLE_STATES excludes ACTIVE, so the 12 h inactivity sweep never repairs
        it either).

        Awaited rather than set_state() precisely because of that following
        event: the order between _bg-spawned tasks is guaranteed nowhere.

        Called from those paths and never hoisted into the stop: the stop is
        also the default RELEASE, which AudioStateMachine.reroute_active_source()
        posts with no `transitioning` flag set and active_source still the
        running one — so both guards in update_source_state() would pass and
        the card would blank on every multiroom toggle.
        """
        self._state = SourceState.READY
        self._metadata = self._idle_payload()
        try:
            self._published = self._project()
        except Exception as e:
            self._logger.error(f"Projecting the idle state failed: {e}")
            self._published = None
        if self.state_machine:
            await self.state_machine.update_source_state(
                self.source, SourceState.READY, self._metadata
            )

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

        Run by refresh_when_idle() for AudioStateMachine.refresh_active_metadata()
        on the active source (GET /api/audio/state, WS reconnect), as a message
        to the actor. Default: no-op for sources
        whose metadata is pushed by an event feed rather than polled.

        Returns:
            True if self._metadata was refreshed.
        """
        return False

    # === Auto-Stop Timer ===

    def _cancel_pause_timer(self) -> None:
        """Disarm the auto-stop timer. An expiry already in the mailbox carries
        this timer's token and is dropped when its turn comes."""
        self._pause_token = None
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_timer(self) -> None:
        """Arm the auto-stop timer after a pause.

        The expiry is posted, not run: it takes its turn behind whatever the
        source is doing, and a play handled first disarms it (E49), while a
        stop cuts it short if it is already running (E06).
        """
        if not self.auto_stop_enabled:
            return

        self._cancel_pause_timer()
        token = object()

        async def expire():
            try:
                await asyncio.sleep(self.auto_stop_delay)
            except asyncio.CancelledError:
                return
            self._post(Timer(token))

        self._pause_token = token
        self._pause_timer = asyncio.create_task(expire())

    async def _on_auto_stop(self) -> None:
        """
        Called when the auto-stop timer fires.

        Default: restart the source. Override for custom behavior.
        """
        await self._do_restart()

    AUTO_STOP_SETTINGS_KEY = "audio.auto_stop_delay"

    async def _load_auto_stop_config(self) -> None:
        """Load the global auto-stop delay from settings."""
        if not self._settings_service or not self.AUTO_STOP_SUPPORTED:
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

    async def _service_main_pid(self) -> Optional[int]:
        """The pid of this source's daemon, or None when it cannot be named.

        For a source whose *session* belongs to a daemon rather than to a link:
        `Restart=` puts a unit back to active within seconds of a crash under a
        new process that knows nothing of the old session, so "is the unit up"
        is not the question. None on any failure — a dev host injects no
        manager, and a source that cannot name its daemon must claim nothing
        rather than declare the session dead.
        """
        if not self.service_name:
            return None

        try:
            return await self._service_manager.main_pid(self.service_name)
        except Exception as e:
            self._logger.warning(f"Could not read the main pid of {self.service_name}: {e}")
            return None

    async def probe_service_active(self) -> Optional[bool]:
        """Whether this source's unit is still up, or None when it cannot be told.

        Distinct from `_is_service_active`, which answers True for a source
        owning no unit — right for "is my service up", wrong for the question
        the state machine asks after a stop came back False: is something still
        holding the device? A source with no unit is holding nothing.
        """
        if not self.service_name:
            return False
        return await self._service_manager.probe_active(self.service_name)

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
        - ``playback`` is the typed projection consumed by the shared player.
          Its is_playing/is_buffering always emit; on READY the payload is
          ``_idle_payload()`` instead, so the media fields
          (title/artist/album/album_art_url/position/duration) are dropped by
          default and a stale track can't linger. A source whose idle view
          still has something to show overrides ``_idle_metadata()`` and
          publishes its resume projection there — the same hook, and the same
          definition of "stopped", as ``_publish_idle()``. Omitting it is not
          how a source says it has no transport: that is ``MUTE_RECEIVER``, on
          the class. Here it only means this call had nothing to project, and
          the inert pair goes out regardless.
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
        state, meta = self._compose(connected, playback, extras)
        self._published = self._project()
        self.set_state(state, meta)

    def _compose(
        self,
        connected: bool,
        playback: Optional[PlaybackMetadata] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Tuple[SourceState, Dict[str, Any]]:
        """The (state, metadata) `emit_connection_state` puts on the wire — pure."""
        if self.MUTE_RECEIVER:
            # Carries extras and nothing else — there is no transport to state.
            meta: Dict[str, Any] = {}
        elif connected:
            meta = (playback or PlaybackMetadata()).model_dump(exclude_none=True)
        else:
            meta = self._idle_payload()
        if extras:
            meta.update({k: v for k, v in extras.items() if v is not None})
        return SourceState.ACTIVE if connected else SourceState.READY, meta

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

    def broadcast_error(self, reason: str) -> None:
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
                reason=reason,
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
