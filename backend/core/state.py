# backend/core/state.py
"""
Audio State Machine - single source of truth for audio state.

Owns the selection (which source, whether a switch is under way) and the
service axis (running, starting, failed), composes them with what the selected
source publishes (its view) and with every source's availability into the one
`AudioState` the wire carries (docs: "Développeurs : le fil"), and broadcasts
it as `source/state` whenever it changes.

Usage:
    from backend.core.state import AudioStateMachine

    state_machine = AudioStateMachine()

    # Activate a source
    await state_machine.transition_to_source(AudioSource.RADIO)
"""
import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.core.models.audio_state import (
    AudioSource,
    ConnectivityLevel,
    NetworkRequirement,
    NetworkUnavailable,
)
from backend.core.models.audio_wire import (
    AudioState,
    Availability,
    PositionAnchor,
    ServiceError,
    SourceView,
)
from backend.core.models.session import ServiceState
from backend.core.models.ws_events import (
    AudioStateChanged,
    SourcePosition,
    SourceSessionEnded,
    WsEvent,
)
from backend.core.audio_source import BaseAudioSource
from backend.shared.decorators import handle_errors

logger = logging.getLogger(__name__)


@dataclass
class SystemAudioState:
    """The state machine's own record. What the wire shows is composed from
    it by `AudioStateMachine.state()`."""
    active_source: AudioSource = AudioSource.NONE
    # A source switch is running: the target's publishes are dropped until the
    # post-start resync reads its view.
    transitioning: bool = False
    # Multiroom toggles in flight (AudioRoutingService, `multiroom_switch()`).
    multiroom_switches: int = 0
    # The last start failed (sticky until a start succeeds): `service: failed`.
    service_error: Optional[ServiceError] = None
    # What the active source last published.
    view: SourceView = field(default_factory=SourceView)

    @property
    def switching(self) -> bool:
        return self.transitioning or self.multiroom_switches > 0


def _without_position(state: Dict[str, Any]) -> Dict[str, Any]:
    """The wire state minus the position axis, for "did it change"."""
    session = state.get("session")
    if not session or session.get("position") is None:
        return state
    return {**state, "session": {**session, "position": True}}


class AudioStateMachine:
    """
    Audio state machine - WebSocketManager for frontend broadcasting.

    Lock order: `_transition_lock` → `_state_lock`, never the reverse. The
    transition lock serializes whole source lifecycles (stop old / start new,
    seconds long); the state lock guards individual `system_state` writes and
    is held for microseconds. Taking the transition lock while holding the
    state lock would deadlock against transition_to_source(), which holds the
    former across every acquisition of the latter. The multiroom reroute obeys
    the same order: reroute_active_source() takes the transition lock and
    AudioRoutingService holds only its own `_routing_lock` above it.

    `_publish_lock` is taken last and alone: it orders what goes on the wire,
    so a state composed later is never sent before one composed earlier.
    """

    # Above one SystemdServiceManager call, deliberately not above a whole
    # transition. Dominating one is not achievable with a single number: a
    # transition costs 2 systemd calls on an mpv source and 4 on Bluetooth,
    # whose _do_start and _do_stop drive three units each — sizing for that
    # case puts the guard past 60s, and `_transition_lock` is held throughout,
    # so the appliance would ignore every source press for a minute on any
    # switch that wedges. The guard exists to stop the UI hanging forever on a
    # leaf that has no bound of its own (_save_progress writing to the SD card,
    # a _do_start reading the network), which argues for a short number.
    #
    # At 10.0 it sat below a single one of those calls, so it fired on bounded
    # work still legitimately in flight — measured on the unit, a stop issued
    # at 22:31:28.650 reached PID 1 only at 22:31:40.745 while the guard cut at
    # 22:31:38.4. 15.0 clears one call and nothing more; what happens *after*
    # it fires (a blind second stop, a "would not stop" nobody verified) is the
    # part that misreports, and it is not addressed here.
    TRANSITION_TIMEOUT = 15.0
    # How much longer a failed transition waits for a teardown the budget cut,
    # before giving the lock back with the unit possibly still stopping.
    TEARDOWN_GRACE = 15.0
    # Given to ALSA to release the device between a source's RELEASE and the
    # snapcast reconcile that reopens it.
    ALSA_RELEASE_SETTLE_S = 0.5
    INACTIVITY_TIMEOUT = 43200  # 12 hours in seconds

    def __init__(self):
        self.system_state = SystemAudioState()
        self.sources: Dict[AudioSource, Optional[BaseAudioSource]] = {
            source: None for source in AudioSource
            if source != AudioSource.NONE
        }
        self._transition_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()
        # The last state broadcast, position axis aside, and the last anchor
        # sent (in that state or on its own).
        self._last_published: Optional[Dict[str, Any]] = None
        self._last_position: Optional[Dict[str, Any]] = None
        # Session ends not yet on the wire: each goes out ahead of the next
        # publish, so no client reads it after the state it led to.
        self._ended_sessions: List[SourceSessionEnded] = []

        # Inactivity monitor
        self._last_activity_time: float = monotonic()
        self._inactivity_monitor_task: Optional[asyncio.Task] = None

        # Set after creation in dependencies.py (circular dependency resolution).
        # Ownership map of the back-references composed into the state:
        #   routing_service      → multiroom_enabled
        #   camilladsp_service   → effects_enabled (DSP plane; named for what it
        #                          holds — EQ is just one of its effects)
        #   connectivity_service → availability, crossed with each source's
        #                          NETWORK_REQUIREMENT
        self.ws_manager = None
        self.routing_service = None
        self.camilladsp_service = None
        self.connectivity_service = None
        self.push_service = None

    def register_source(self, source: AudioSource, instance: BaseAudioSource) -> None:
        """Register an audio source implementation."""
        if source in self.sources:
            self.sources[source] = instance
            logger.info(f"Source registered: {source.value}")

    def get_source(self, source: AudioSource) -> Optional[BaseAudioSource]:
        """Get audio source implementation for a specific source."""
        return self.sources.get(source)

    # === The state ===

    def _connectivity_reason(self, source: AudioSource) -> Optional[str]:
        """Whether `source` is blocked by the current link, and how.

        Two axes, both of which must say so: what NetworkManager reports, and
        what the source needs. A LAN-only link breaks Spotify and leaves
        AirPlay untouched; nothing breaks Bluetooth. Reporting on the level
        alone is what made the old banner fire while playing a CD.

        None whenever the source can work — including on UNKNOWN, the
        fail-open level.
        """
        if self.connectivity_service is None:
            return None

        level = self.connectivity_service.level
        if level in (ConnectivityLevel.FULL, ConnectivityLevel.UNKNOWN):
            return None

        instance = self.sources.get(source)
        requirement = instance.NETWORK_REQUIREMENT if instance else NetworkRequirement.NONE
        if requirement == NetworkRequirement.NONE:
            return None

        if level == ConnectivityLevel.NONE:
            return NetworkUnavailable.NO_NETWORK.value

        # PORTAL / LIMITED: the LAN is up, so only internet sources are blocked.
        if requirement == NetworkRequirement.INTERNET:
            return NetworkUnavailable.NO_INTERNET.value
        return None

    def availability_of(self, source: AudioSource) -> Optional[str]:
        """Why `source` cannot work now, or None: the link first, then the
        source's own reason (docs: "le fil", §3)."""
        reason = self._connectivity_reason(source)
        if reason is not None:
            return reason
        instance = self.sources.get(source)
        if instance is None:
            return None
        try:
            return instance.availability()
        except Exception as e:
            logger.error(f"{source.value} could not say whether it is available: {e}")
            return None

    def _service(self) -> ServiceState:
        sm = self.system_state
        if sm.active_source == AudioSource.NONE:
            return ServiceState.STOPPED
        if sm.transitioning:
            return ServiceState.STARTING
        if sm.service_error is not None:
            return ServiceState.FAILED
        if sm.switching:
            return ServiceState.STARTING
        return ServiceState.RUNNING

    def state(self) -> AudioState:
        """The whole state, as the wire carries it."""
        sm = self.system_state
        service = self._service()
        view = sm.view
        return AudioState(
            source=sm.active_source,
            switching=sm.switching,
            service=service,
            service_error=sm.service_error if service is ServiceState.FAILED else None,
            availability=Availability(**{
                source.value: self.availability_of(source)
                for source in AudioSource if source != AudioSource.NONE
            }),
            session=view.session,
            controls=list(view.controls) if service is ServiceState.RUNNING else [],
            resume=view.resume if view.session is None else None,
            details=view.details,
            multiroom_enabled=(
                self.routing_service.multiroom_enabled if self.routing_service else False
            ),
            equalizer_effects_enabled=(
                self.camilladsp_service.effects_enabled if self.camilladsp_service else False
            ),
        )

    def get_current_state(self) -> Dict[str, Any]:
        """The state as a JSON-ready dict (GET /api/audio/state, initial_state)."""
        return self.state().wire()

    async def publish_state(self) -> None:
        """Broadcast what moved since the last broadcast: `source/state` when
        anything but the playhead did, else `source/position` when the
        playhead alone did (a seek, a speed change, a drift past the
        tolerance), else nothing.

        Every writer of anything the state is composed of calls it: the
        transitions, a source's publish, a source's availability, the
        connectivity service, the multiroom and equalizer toggles.
        """
        async with self._publish_lock:
            while self._ended_sessions:
                await self.broadcast(self._ended_sessions.pop(0))
            state = self.state()
            wire = state.wire()
            key = _without_position(wire)
            session = wire["session"]
            position = session["position"] if session else None
            if key != self._last_published:
                self._last_published = key
                self._last_position = position
                await self.broadcast(AudioStateChanged(**state.model_dump()))
            elif position is not None and position != self._last_position:
                self._last_position = position
                await self.broadcast(SourcePosition(
                    source=wire["source"], session_id=session["id"],
                    position=PositionAnchor(**position),
                ))

    def session_ended(self, event: SourceSessionEnded) -> None:
        """Queue a session's end for the next publish, ahead of its state.

        Synchronous on purpose: a source's own background tasks are cancelled
        by the STOP that can follow an end at once, and a clients' podcast card
        marks an episode listened only while its state still names the session
        the end names (frontend podcastStore.handleSessionEnded)."""
        self._ended_sessions.append(event)

    @contextlib.asynccontextmanager
    async def multiroom_switch(self):
        """Hold `switching` for a whole multiroom toggle (AudioRoutingService):
        from its start to the end of its volume sync. Its end is the first
        state where `switching` is false again (docs: "le fil", D7)."""
        async with self._state_lock:
            self.system_state.multiroom_switches += 1
        await self.publish_state()
        try:
            yield
        finally:
            async with self._state_lock:
                self.system_state.multiroom_switches -= 1
            await self.publish_state()

    async def reroute_active_source(
        self, apply_mode: Callable[[], Awaitable[None]]
    ) -> None:
        """Carry the active source across a multiroom MILO_MODE change.

        Under the transition lock, so it is mutually exclusive with
        transition_to_source(). The source to carry is read *inside* that lock,
        never handed in: a caller's read predates the lock, and a source picked
        in that window would be rerouted as the one it replaced — restarting a
        stopped source next to the new one (E04).

        Posts RELEASE, runs `apply_mode` (snapcast reconcile + routing.env,
        owned by AudioRoutingService), then posts ACQUIRE. Messages the source
        receives meanwhile wait their turn in its mailbox. What the source
        publishes meanwhile reaches the wire live: `switching` (held by the
        caller, `multiroom_switch()`) already says the service is starting.
        ACQUIRE follows RELEASE even when `apply_mode` raised: the mode in force
        is then the old one, and a source left released would stay silent under
        it (E08: Spotify's output parked on `null`). `apply_mode` raising is
        re-raised after that; an ACQUIRE failure is not, since the mode itself is
        committed and the user can retry the source.
        """
        async with self._transition_lock:
            active = self.system_state.active_source
            instance = self.sources.get(active) if active != AudioSource.NONE else None

            if instance is None:
                await apply_mode()
                return

            # Held across the three steps: a command, a daemon's message or
            # a stop arriving meanwhile waits for the whole of it, instead of
            # landing on a released source or being undone by the reacquire.
            async with instance.hold_mailbox():
                # The device is freed first: in direct mode the source holds
                # CamillaDSP's input, in multiroom mode snapclient needs it.
                logger.info("Releasing source %s to free the ALSA device", active.value)
                await instance.release_for_reroute()
                await asyncio.sleep(self.ALSA_RELEASE_SETTLE_S)

                switch_failed: Optional[BaseException] = None
                try:
                    await apply_mode()
                except Exception as e:
                    switch_failed = e

                logger.info("Re-acquiring source %s", active.value)
                reacquired = False
                try:
                    reacquired = await instance.acquire_after_reroute()
                    if not reacquired:
                        logger.warning(
                            "Source %s re-acquire returned False after the reroute "
                            "(the reroute itself stands)", active.value
                        )
                except Exception as e:
                    logger.warning(
                        "Source %s re-acquire failed after the reroute (non-fatal): %s",
                        active.value, e,
                    )
                if switch_failed is not None:
                    raise switch_failed
            if reacquired and self.system_state.service_error is not None:
                # The reacquire is a full start that succeeded: the one thing
                # that lifts a failed start, which no publish of the source's may do.
                await self._resync_after_start(active, instance)

    async def _resync_after_start(self, active: AudioSource, instance) -> None:
        """Take the source's own view as the machine's, after a start succeeded."""
        async with self._state_lock:
            if self.system_state.active_source != active:
                return
            self.system_state.view = instance.view
            self.system_state.service_error = None
        await self.publish_state()

    async def transition_to_source(
        self,
        target_source: AudioSource,
        expected_source: Optional[AudioSource] = None
    ) -> bool:
        """Perform transition to new source with timeout.

        Args:
            target_source: The source to transition to.
            expected_source: If set, the transition is skipped when the current
                active source no longer matches (CAS guard for the inactivity
                monitor — prevents deactivating a source that a user just
                activated between the decision and the lock acquisition).
        """
        async with self._transition_lock:
            logger.debug(
                "START TRANSITION: %s -> %s",
                self.system_state.active_source.value,
                target_source.value
            )

            # CAS guard: abort if active source changed since caller's decision
            if expected_source is not None and self.system_state.active_source != expected_source:
                logger.info(
                    "Transition skipped: expected %s but active is %s",
                    expected_source.value,
                    self.system_state.active_source.value
                )
                return False

            # Re-selecting the active source is a no-op — unless its last start
            # failed, where the same gesture is the retry: a failed transition
            # leaves its source selected, so this is the path back.
            if self.system_state.active_source == target_source and \
               self.system_state.service_error is None:
                logger.info(f"Already on source {target_source.value}")
                return True

            # The key always exists — self.sources is built with every AudioSource
            # as a key — so it is the value that says whether a source was
            # registered. Checked here, before the old source is stopped.
            if target_source != AudioSource.NONE and not self.sources.get(target_source):
                logger.error(f"No source registered for: {target_source.value}")
                return False

            # The previous source's teardown, while it runs. The timeout below
            # cuts the *wait* for it, never the teardown itself (it is shielded,
            # and a source's stop runs in its own actor): a failed transition
            # waits for it to end before settling, so nothing — not even the
            # user's retry, queued on the lock — starts over a source still
            # letting go of the device.
            teardown: Optional[asyncio.Task] = None

            try:
                async with asyncio.timeout(self.TRANSITION_TIMEOUT):
                    async with self._state_lock:
                        old_source = self.system_state.active_source
                        self.system_state.transitioning = True
                        self.system_state.active_source = target_source
                        self.system_state.view = SourceView()
                        # A retry of a failed source starts from a clean slate.
                        self.system_state.service_error = None

                    await self.publish_state()

                    # Stop old source
                    if old_source != AudioSource.NONE:
                        teardown = asyncio.ensure_future(self._stop_source(old_source))
                        teardown.set_name(old_source.value)
                        await asyncio.shield(teardown)

                    # Start new source
                    if target_source != AudioSource.NONE:
                        success = await self._start_source(target_source)
                        if not success:
                            raise ValueError(f"Failed to start {target_source.value}")

                    async with self._state_lock:
                        self.system_state.transitioning = False
                        # Resync from the source's actual post-start view. This
                        # recovers any publish dropped while transitioning
                        # (_do_start may have opened a session) — there is no
                        # buffer/replay, just this re-read.
                        source = self.sources.get(target_source)
                        self.system_state.view = source.view if source else SourceView()

                    await self.publish_state()

                    # Reset inactivity timer on source change
                    self._last_activity_time = monotonic()

                    # The one place the two axes are worth recording: a source
                    # that started fine and still cannot work. Without it, "the
                    # card showed the wrong screen" is unfalsifiable from the
                    # logs — nothing else prints what the state carried.
                    blocked = (
                        self.availability_of(target_source)
                        if target_source != AudioSource.NONE else None
                    )
                    logger.info(
                        "Transition completed: %s%s",
                        target_source.value,
                        f" (unavailable: {blocked})" if blocked else "",
                    )
                    return True

            except Exception as e:
                # A timeout only earns its own reason; both failures settle
                # identically (asyncio.TimeoutError is a builtin Exception).
                if isinstance(e, asyncio.TimeoutError):
                    error = ServiceError(
                        reason="start_timeout",
                        message=f"Transition timeout after {self.TRANSITION_TIMEOUT}s",
                    )
                else:
                    error = ServiceError(reason="start_failed", message=str(e))

                blocked = self._connectivity_reason(target_source)
                # WARNING, never ERROR. This module's logger is under the
                # `backend` hierarchy, which WebSocketLogHandler forwards to the
                # notification banner wholesale, so an ERROR here would be a
                # second user-facing report of one failure — the state already
                # says `service: failed`, and the card offers the retry.
                # errors.log and the journal keep WARNING and above.
                logger.warning(
                    "Transition failed: %s%s",
                    error.message,
                    f" (link is {blocked})" if blocked else "",
                )
                # The failure lands with `transitioning` cleared, in the same
                # write: a "starting" left in it drew a spinner under the
                # failure for as long as the failed target took to stop (E03).
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.view = SourceView()
                    self.system_state.service_error = (
                        error if target_source != AudioSource.NONE else None
                    )
                await self.publish_state()

                await self._settle_failed_transition(target_source, error, teardown)
                return False

    async def update_source_view(self, source: AudioSource, view: SourceView) -> None:
        """Take what `source` published as the state's, and broadcast what
        moved (publish_state)."""
        async with self._state_lock:
            if source != self.system_state.active_source:
                logger.debug(f"Ignoring a publish from inactive source: {source.value}")
                return

            # Dropped, not buffered: publishes from _do_start during a transition
            # are recovered by the post-start resync in transition_to_source().
            if self.system_state.transitioning:
                logger.debug(f"Ignoring a publish during transition: {source.value}")
                return

            # A failed start is only lifted by a start that succeeds — through
            # the resync of a transition or of a reroute, never through here. A
            # source with a feed that outlives its start (the CD's disc watcher)
            # otherwise published over it: the card lost "Retry" and
            # re-selecting became a no-op (E07).
            if self.system_state.service_error is not None:
                logger.debug(f"Ignoring a publish from {source.value}: its start failed")
                return

            self.system_state.view = view

            # Reset inactivity timer while a session is live
            if view.session is not None:
                self._last_activity_time = monotonic()

        await self.publish_state()

    @handle_errors(default=False, level='warning')
    async def refresh_active_view(self) -> bool:
        """Re-read the active source's player before the state is read
        (GET /api/audio/state, WS handshake), so the anchor it carries is the
        player's own. A move beyond the tolerance is published by the source
        itself; this copy only makes the read current.
        """
        active = self.system_state.active_source
        if active == AudioSource.NONE:
            return False

        source = self.sources.get(active)
        if not source:
            return False

        if not await source.refresh_when_idle():
            return False

        # The hook awaited the source's player, and a transition may have run
        # inside that await: a switch away makes this the outgoing source's
        # view, and one still in flight owns the record until its post-start
        # resync. Either way the read is stale.
        async with self._state_lock:
            sm = self.system_state
            if sm.active_source != active or sm.transitioning or sm.service_error is not None:
                return False
            sm.view = source.view
        return True

    @handle_errors(default=None)
    async def _stop_source(self, source: AudioSource) -> None:
        """Stop specified source, and say so when it refuses.

        `stop()` reports its own refusal at warning — journal-only — and the
        transition then started the next source over one still holding the ALSA
        device, with nothing in the banner to explain the silence that follows.

        The verdict is deliberately *not* propagated. Refusing the transition
        would turn every unclean stop into a source selection the user cannot
        make, while `_start_source` already fails on its own when the device is
        genuinely still held — and that path settles with a message. The two
        unwind callers could not act on it either. So the trace is the whole
        fix; a bool nobody reads would just move this finding up one frame.
        """
        instance = self.sources.get(source)
        if not instance or await instance.stop():
            return

        # stop() answers False for two different facts: the unit refused, or
        # the call ran out of budget with nothing confirmed. Only the first
        # justifies a line that reaches the banner — the second used to borrow
        # its wording and accuse a source that had already stopped.
        still_up = await instance.probe_service_active()
        if still_up is True:
            logger.error("%s would not stop; starting over it", source.value)
        elif still_up is None:
            logger.warning(
                "%s stop went unconfirmed — the unit may still hold the device",
                source.value,
            )
        else:
            logger.info(
                "%s stop was reported failed, but its unit is down", source.value
            )

    @handle_errors(default=False)
    async def _start_source(self, source: AudioSource) -> bool:
        """Start specified source."""
        instance = self.sources.get(source)
        if not instance:
            return False

        if not instance.is_initialized and not await instance.initialize():
            return False

        return await instance.start()

    async def _settle_failed_transition(
        self, target_source: AudioSource, error: ServiceError, teardown: Optional[asyncio.Task]
    ) -> None:
        """Let the old teardown finish, stop the source whose start failed, then
        settle it failed.

        The source stays *selected*: "this source's start failed" is exactly
        what happened, and dropping back to "no source" would throw that away —
        plus it is what makes the retry above reachable, since re-selecting a
        source only restarts it while its start is the one that failed.

        `teardown` is the previous source's stop when the timeout cut the wait
        for it: it is still running, it is awaited here and never re-issued (a
        second teardown is its own bug — Bluetooth's has no is-running guard).
        The target is always stopped: a start can fail after its systemd unit
        came up (e.g. mpv started, IPC connect failed), and a start the timeout
        gave up on is still running in the source's actor, which this stop cuts
        short.
        """
        if teardown is not None and not teardown.done():
            logger.info("Waiting for the previous source's teardown to finish")
            # Bounded: past TEARDOWN_GRACE, a teardown that never ends would
            # hold the transition lock — every source press, IR key and
            # multiroom toggle — for good. It goes on in its own actor.
            try:
                await asyncio.wait_for(asyncio.shield(teardown), self.TEARDOWN_GRACE)
            except asyncio.TimeoutError:
                logger.error(
                    "%s is still stopping %.0fs past the transition budget; "
                    "no longer waiting for it", teardown.get_name(), self.TEARDOWN_GRACE,
                )

        if target_source != AudioSource.NONE:
            await self._stop_source(target_source)

        async with self._state_lock:
            self.system_state.active_source = target_source
            self.system_state.view = SourceView()
            self.system_state.service_error = (
                error if target_source != AudioSource.NONE else None
            )

        # The settled state, once the target let go — republished only if it
        # moved (a publish from the stopping source was dropped meanwhile).
        await self.publish_state()

    # === Inactivity Monitor ===

    def start_inactivity_monitor(self) -> None:
        """Start the background task that deactivates idle sources."""
        if self._inactivity_monitor_task is None:
            self._inactivity_monitor_task = asyncio.create_task(
                self._monitor_inactivity()
            )
            logger.info("Inactivity monitor started (timeout: %ds)", self.INACTIVITY_TIMEOUT)

    async def reload_auto_stop_for_all_sources(self) -> bool:
        """Refresh the auto-stop delay on every registered source.

        Invoked by the settings API after `audio.auto_stop_delay`
        is updated so each live source picks up the new value without
        a restart. Failures on individual sources are logged but do not
        abort the rest of the fan-out.
        """
        all_ok = True
        for source, instance in self.sources.items():
            if instance is None:
                continue
            try:
                await instance.reload_auto_stop_config()
            except Exception as e:
                all_ok = False
                logger.error(
                    "Failed to reload auto-stop for %s: %s",
                    source.value, e
                )
        return all_ok

    async def _monitor_inactivity(self) -> None:
        """Deactivate the source after the inactivity timeout with no session."""
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await asyncio.sleep(60)
                # Per-iteration guard: transition_to_source() raising (a source
                # whose stop() blew up) must not kill the monitor for the rest
                # of the process's life — the next tick retries.
                try:
                    await self._check_inactivity()
                except Exception as e:
                    logger.error(f"Inactivity check failed: {e}")

    async def _check_inactivity(self) -> None:
        """One inactivity tick: deactivate the source if it has idled too long.

        Idle is a selected source with no session — nothing it could ever
        produce audio from, a failed start included: without it, a failed
        source would stay selected forever — the one outcome the 12 h sweep
        exists to prevent.
        """
        # Atomic snapshot under lock
        async with self._state_lock:
            sm = self.system_state
            source = sm.active_source
            idle = sm.view.session is None
            switching = sm.switching

        if (
            source != AudioSource.NONE
            and idle
            and not switching
            and (monotonic() - self._last_activity_time) >= self.INACTIVITY_TIMEOUT
        ):
            elapsed = monotonic() - self._last_activity_time
            logger.info(
                "Deactivating idle source %s after %.0fs of inactivity",
                source.value,
                elapsed
            )
            # CAS guard: if active source changed between snapshot
            # and lock acquisition, transition_to_source will skip
            await self.transition_to_source(
                AudioSource.NONE, expected_source=source
            )

    def cleanup(self) -> None:
        """Cancel background tasks."""
        if self._inactivity_monitor_task:
            self._inactivity_monitor_task.cancel()
            self._inactivity_monitor_task = None

    async def shutdown_sources(self) -> None:
        """End every registered source's mailbox (backend teardown, main.py)."""
        for instance in self.sources.values():
            if instance is not None:
                await instance.shutdown()

    # === WebSocket Broadcasting ===

    async def broadcast(self, event: WsEvent) -> None:
        """
        Broadcast a typed event to all connected WebSocket clients.

        Sole emission API — envelope {category, type, origin, data, timestamp}.
        Payload shape and consumers are documented on the event model
        (backend/core/models/ws_events.py).
        """
        if not self.ws_manager:
            return

        await self.ws_manager.broadcast_dict(event.to_envelope())

        # APNs fan-out rides the same single emission point, so there is no
        # second place to remember to notify. Synchronous and non-raising by
        # contract: it only marks state dirty, and the push happens on the
        # service's own loop — a slow Apple must never delay this broadcast.
        if self.push_service:
            self.push_service.on_event(event)
