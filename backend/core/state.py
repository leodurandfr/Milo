# backend/core/state.py
"""
Audio State Machine - single source of truth for audio state.

Manages audio source transitions and broadcasts state changes
to WebSocket clients via WebSocketManager.

Usage:
    from backend.core.state import AudioStateMachine

    state_machine = AudioStateMachine()

    # Activate a source
    await state_machine.transition_to_source(AudioSource.RADIO)
"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from time import monotonic
from typing import Dict, Any, Optional

from backend.core.models.audio_state import (
    AudioSource,
    ConnectivityLevel,
    NetworkRequirement,
    NetworkUnavailable,
    SourceState,
    SystemAudioState,
)
from backend.core.models.ws_events import (
    SourceStateChanged,
    SystemErrorEvent,
    SystemStateChanged,
    SystemTransitionComplete,
    SystemTransitionStart,
    WsEvent,
)
from backend.core.audio_source import BaseAudioSource
from backend.shared.decorators import handle_errors

logger = logging.getLogger(__name__)


class AudioStateMachine:
    """
    Audio state machine - WebSocketManager for frontend broadcasting.

    Lock order: `_transition_lock` → `_state_lock`, never the reverse. The
    transition lock serializes whole source lifecycles (stop old / start new,
    seconds long); the state lock guards individual `system_state` writes and
    is held for microseconds. Taking the transition lock while holding the
    state lock would deadlock against transition_to_source(), which holds the
    former across every acquisition of the latter. `core/multiroom/routing.py`
    obeys the same order through exclusive_transition().
    """

    TRANSITION_TIMEOUT = 10.0
    INACTIVITY_TIMEOUT = 43200  # 12 hours in seconds

    # States a source can sit in without ever producing audio, so the ones the
    # inactivity sweep deactivates. ERROR is one of them because a failed
    # transition leaves its source selected: without it, an errored source would
    # stay selected forever — the one outcome the 12 h sweep exists to prevent.
    IDLE_STATES = (SourceState.READY, SourceState.ERROR)

    def __init__(self):
        self.system_state = SystemAudioState()
        self.sources: Dict[AudioSource, Optional[BaseAudioSource]] = {
            source: None for source in AudioSource
            if source != AudioSource.NONE
        }
        self._transition_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

        # Inactivity monitor
        self._last_activity_time: float = monotonic()
        self._inactivity_monitor_task: Optional[asyncio.Task] = None

        # Set after creation in dependencies.py (circular dependency resolution).
        # Ownership map of the back-references aggregated into full_state:
        #   routing_service      → multiroom_enabled
        #   camilladsp_service   → effects_enabled (DSP plane; named for what it
        #                          holds — EQ is just one of its effects)
        #   connectivity_service → network_unavailable, crossed with the active
        #                          source's NETWORK_REQUIREMENT
        self.ws_manager = None
        self.routing_service = None
        self.camilladsp_service = None
        self.connectivity_service = None

    def register_source(self, source: AudioSource, instance: BaseAudioSource) -> None:
        """Register an audio source implementation."""
        if source in self.sources:
            self.sources[source] = instance
            logger.info(f"Source registered: {source.value}")

    def get_source(self, source: AudioSource) -> Optional[BaseAudioSource]:
        """Get audio source implementation for a specific source."""
        return self.sources.get(source)

    def _network_unavailable(self, source: Optional[AudioSource] = None) -> Optional[str]:
        """Whether a source is blocked by the current link, and how.

        Defaults to the *active* source, which is what full_state reports. The
        explicit argument is for the transition path, which needs the answer for
        the source it is moving *to* before that source is the active one.

        Two axes, both of which must say so: what NetworkManager reports, and
        what the selected source needs. A LAN-only link breaks Spotify and
        leaves AirPlay untouched; nothing breaks Bluetooth. Reporting on the
        level alone is what made the old banner fire while playing a CD.

        None whenever the source can work — including on UNKNOWN, the fail-open
        level, and for AudioSource.NONE, which needs nothing.
        """
        if self.connectivity_service is None:
            return None

        level = self.connectivity_service.level
        if level in (ConnectivityLevel.FULL, ConnectivityLevel.UNKNOWN):
            return None

        target = source if source is not None else self.system_state.active_source
        instance = self.sources.get(target)
        requirement = instance.NETWORK_REQUIREMENT if instance else NetworkRequirement.NONE
        if requirement == NetworkRequirement.NONE:
            return None

        if level == ConnectivityLevel.NONE:
            return NetworkUnavailable.NO_NETWORK.value

        # PORTAL / LIMITED: the LAN is up, so only internet sources are blocked.
        if requirement == NetworkRequirement.INTERNET:
            return NetworkUnavailable.NO_INTERNET.value
        return None

    def get_current_state(self) -> Dict[str, Any]:
        """Return current system state as dict.

        Mirrors the full_state aggregation in `broadcast()`: pulls multiroom_enabled
        from routing_service, equalizer_effects_enabled from camilladsp_service and
        the connectivity level from connectivity_service, so the wire payload
        (notably the initial_state on WS connect) carries all three.
        """
        state = self.system_state.to_dict()
        state["multiroom_enabled"] = (
            self.routing_service.multiroom_enabled if self.routing_service else False
        )
        state["equalizer_effects_enabled"] = (
            self.camilladsp_service.effects_enabled if self.camilladsp_service else False
        )
        state["network_unavailable"] = self._network_unavailable()
        return state

    @asynccontextmanager
    async def exclusive_transition(self):
        """Hold the transition lock for an externally-orchestrated source
        lifecycle (e.g. the multiroom reroute), mutually exclusive with
        transition_to_source(). It does NOT set system_state.transitioning,
        so update_source_state() calls inside the block broadcast live — the
        reroute relies on this to push its STARTING state to the UI. No
        in-transition buffer exists, here or anywhere; none is needed."""
        async with self._transition_lock:
            yield

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

            # Re-selecting the active source is a no-op — unless it is the one
            # in ERROR, where the same gesture is the retry: a failed transition
            # leaves its source selected, so this is the path back.
            if self.system_state.active_source == target_source and \
               self.system_state.source_state != SourceState.ERROR:
                logger.info(f"Already on source {target_source.value}")
                return True

            if target_source != AudioSource.NONE and target_source not in self.sources:
                logger.error(f"No source registered for: {target_source.value}")
                return False

            # Holds the previous source while its teardown is in flight. The
            # timeout below can fire inside that stop and cancel it half-done,
            # and the unwind is then the only place left to finish it.
            unstopped_source = AudioSource.NONE

            try:
                async with asyncio.timeout(self.TRANSITION_TIMEOUT):
                    async with self._state_lock:
                        old_source = self.system_state.active_source
                        self.system_state.transitioning = True
                        self.system_state.active_source = target_source
                        self.system_state.source_state = (
                            SourceState.STARTING if target_source != AudioSource.NONE
                            else SourceState.READY
                        )
                        self.system_state.metadata = {}
                        # A retry of an errored source starts from a clean slate:
                        # the message settled by the previous attempt must not
                        # ride along in full_state while this one is STARTING.
                        self.system_state.error = None

                    await self.broadcast(SystemTransitionStart())

                    # Stop old source
                    if old_source != AudioSource.NONE:
                        unstopped_source = old_source
                        await self._stop_source(old_source)
                        unstopped_source = AudioSource.NONE

                    # Start new source
                    if target_source != AudioSource.NONE:
                        success = await self._start_source(target_source)
                        if not success:
                            raise ValueError(f"Failed to start {target_source.value}")

                    async with self._state_lock:
                        self.system_state.transitioning = False
                        if target_source != AudioSource.NONE:
                            # Resync from the source's actual post-start state.
                            # This recovers any update_source_state dropped while
                            # transitioning (_do_start may have set CONNECTED with
                            # metadata) — there is no buffer/replay, just this re-read.
                            source = self.sources.get(target_source)
                            if source:
                                self.system_state.source_state = source.state
                                self.system_state.metadata = source.metadata
                            else:
                                self.system_state.source_state = SourceState.READY

                    await self.broadcast(SystemTransitionComplete())

                    # Reset inactivity timer on source change
                    self._last_activity_time = monotonic()

                    # The one place the two axes are worth recording: a source
                    # that started fine and still cannot work. Without it, "the
                    # card showed the wrong screen" is unfalsifiable from the
                    # logs — nothing else prints what full_state carried.
                    blocked = self._network_unavailable(target_source)
                    logger.info(
                        "Transition completed: %s%s",
                        target_source.value,
                        f" (unavailable: {blocked})" if blocked else "",
                    )
                    return True

            except Exception as e:
                # A timeout only earns its own message; both failures settle
                # identically (asyncio.TimeoutError is a builtin Exception).
                if isinstance(e, asyncio.TimeoutError):
                    error = "Transition timeout"
                    message = f"Transition timeout after {self.TRANSITION_TIMEOUT}s"
                else:
                    error = message = str(e)

                blocked = self._network_unavailable(target_source)
                # WARNING, never ERROR — and not only when the link explains it.
                # This module's logger is under the `backend` hierarchy, which
                # WebSocketLogHandler forwards to the notification banner
                # wholesale, so an ERROR here is a *second* user-facing report of
                # one failure: the raw log line races the SystemErrorEvent below
                # for App.vue's single-slot banner and, being emitted from a
                # background task, usually lands last — replacing "Spotify ·
                # error" with "Backend error". One failure, one notification: the
                # event when the source is at fault, the status card alone when
                # the link is. errors.log and the journal keep WARNING and above.
                logger.warning(
                    "Transition failed: %s%s",
                    message,
                    f" (link is {blocked})" if blocked else "",
                )
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.error = error

                # No banner when the link already explains it. The status card
                # says "no internet" and offers the network settings, which is
                # both more accurate and more actionable than a raw
                # "Network is unreachable" over the top of it — and two
                # notifications for one cause is what made this look broken.
                if not blocked:
                    await self.broadcast(SystemErrorEvent(
                        source=target_source.value,
                        error=error,
                        message=message
                    ))

                await self._settle_failed_transition(target_source, error, unstopped_source)
                return False

    async def update_source_state(
        self,
        source: AudioSource,
        new_state: SourceState,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update source state and broadcast via WebSocket."""
        async with self._state_lock:
            if source != self.system_state.active_source:
                logger.debug(f"Ignoring state update from inactive source: {source.value}")
                return

            # Dropped, not buffered: updates from _do_start during a transition
            # are recovered by the post-start resync in transition_to_source().
            if self.system_state.transitioning:
                logger.debug(f"Ignoring state update during transition: {source.value}")
                return

            self.system_state.source_state = new_state

            # Replace, don't merge: a state transition supplies the authoritative
            # metadata for the new state, so stale fields from the previous track
            # (title/artist/uri…) must not survive a partial READY payload.
            # metadata=None means a state-only change — leave metadata untouched
            # (e.g. AudioRoutingService flipping to STARTING during a reroute,
            # which keeps the current track visible). Live position/duration are
            # not affected: they flow through broadcast_position_update, never here.
            if metadata is not None:
                self.system_state.metadata = dict(metadata)

            if new_state == SourceState.ERROR:
                self.system_state.error = metadata.get("error") if metadata else "Unknown"
            else:
                self.system_state.error = None

            # Reset inactivity timer when source becomes active
            if new_state == SourceState.ACTIVE:
                self._last_activity_time = monotonic()

        await self.broadcast(SourceStateChanged(
            source=source.value,
            new_state=new_state.value,
            metadata=metadata
        ))

    async def update_position_metadata(
        self, source: AudioSource, position: int, duration: int
    ) -> None:
        """Sync live position/duration into system_state.metadata (so a new WS
        connection's initial_state carries them). Only the active source may
        write; the write stays here so state mutation lives in the state machine."""
        async with self._state_lock:
            sm = self.system_state
            if sm.metadata is not None and sm.active_source == source:
                sm.metadata["position"] = position
                sm.metadata["duration"] = duration

    @handle_errors(default=False, level='warning')
    async def refresh_active_metadata(self) -> bool:
        """Refresh metadata from the active source (GET /api/audio/state, WS handshake).

        Metadata only — unlike the post-start resync in transition_to_source(),
        which re-reads `source.state` as well. The difference is deliberate but
        narrow: a source that changes state re-publishes through
        update_source_state() on its own, so there is nothing here to copy. Five
        sources implement the hook (Spotify, CD, Podcast, Music Library,
        Bluetooth) and four of them cannot move state inside it — their state is
        derived from a session this call does not touch. Spotify is the one that
        can: a /status read finding no track clears its metadata without
        publishing, so a client connecting in that window is handed ACTIVE with
        an empty record. The window is bounded by _reconcile_on_connect(), which
        is what actually repairs an ended session; widening this method to paper
        over it would put a second reconciliation path next to the real one.
        """
        if self.system_state.active_source == AudioSource.NONE:
            return False

        source = self.sources.get(self.system_state.active_source)
        if not source:
            return False

        if await source.refresh_metadata():
            async with self._state_lock:
                self.system_state.metadata = source.metadata
            return True

        return False

    @handle_errors(default=None)
    async def _stop_source(self, source: AudioSource) -> None:
        """Stop specified source."""
        instance = self.sources.get(source)
        if instance:
            await instance.stop()

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
        self, target_source: AudioSource, error: str, unstopped_source: AudioSource
    ) -> None:
        """Stop the source whose start failed, then settle it in ERROR.

        The source stays *selected*: "this source is in error" is exactly what
        happened, and dropping back to "no source" would throw that away — plus
        it is what makes the retry above reachable, since re-selecting a source
        only restarts it while its state is ERROR. `error` is kept in
        system_state so full_state carries the message the card reads; the
        banner rides on the SystemErrorEvent emitted just before.

        The target is always stopped: a start can fail after its systemd unit
        came up (e.g. mpv started, IPC connect failed). `unstopped_source` is
        the previous source *only* on the one branch where the timeout fired
        inside its teardown and cancelled it — it is NONE on every other path,
        because a stop that returned already ran and re-running an unguarded
        teardown is its own bug (Bluetooth's tears down bluetoothctl +
        bluealsa/bluetooth.service with no is-running guard). Left running, it
        keeps the ALSA device and every later start fails until reboot.
        """
        if unstopped_source not in (AudioSource.NONE, target_source):
            logger.warning(
                "Teardown of %s was cut short by the transition timeout — retrying it",
                unstopped_source.value,
            )
            await self._stop_source(unstopped_source)

        if target_source != AudioSource.NONE:
            await self._stop_source(target_source)

        async with self._state_lock:
            self.system_state.active_source = target_source
            self.system_state.source_state = (
                SourceState.ERROR if target_source != AudioSource.NONE
                else SourceState.READY
            )
            self.system_state.metadata = {}
            self.system_state.error = error

        # Broadcast the settled state so the frontend knows the system is
        # stable again — and which source it is stable on.
        await self.broadcast(SystemStateChanged(source="system"))

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
        """Deactivate source after inactivity timeout without ACTIVE state."""
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
        """One inactivity tick: deactivate the source if it has idled too long."""
        # Atomic snapshot under lock
        async with self._state_lock:
            source = self.system_state.active_source
            source_state = self.system_state.source_state
            transitioning = self.system_state.transitioning

        if (
            source != AudioSource.NONE
            and source_state in self.IDLE_STATES
            and not transitioning
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

    # === WebSocket Broadcasting ===

    async def broadcast(self, event: WsEvent) -> None:
        """
        Broadcast a typed event to all connected WebSocket clients.

        Sole emission API — envelope {category, type, origin, data, timestamp}.
        Payload shape and consumers are documented on the event model
        (backend/core/models/ws_events.py), which also decides — alone — whether
        full_state rides along, via its INCLUDE_FULL_STATE flag.
        """
        if not self.ws_manager:
            return

        event_payload = event.wire_data()
        if event.INCLUDE_FULL_STATE:
            event_payload["full_state"] = self.get_current_state()

        await self.ws_manager.broadcast_dict(event.to_envelope(event_payload))
