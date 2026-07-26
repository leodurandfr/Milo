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

from backend.core.models.audio_state import AudioSource, SourceState, SystemAudioState
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
    """

    TRANSITION_TIMEOUT = 10.0
    INACTIVITY_TIMEOUT = 43200  # 12 hours in seconds

    # Only "source"/"system" categories include full_state (used by unifiedAudioStore).
    # Other categories (volume, equalizer, multiroom, settings) send only
    # their specific data — their frontend stores don't read full_state.
    _FULL_STATE_CATEGORIES = frozenset({"source", "system"})

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
        #   routing_service    → multiroom_enabled
        #   camilladsp_service → effects_enabled (DSP plane; named for what it
        #                        holds — EQ is just one of its effects)
        self.ws_manager = None
        self.routing_service = None
        self.camilladsp_service = None

    def register_source(self, source: AudioSource, instance: BaseAudioSource) -> None:
        """Register an audio source implementation."""
        if source in self.sources:
            self.sources[source] = instance
            logger.info(f"Source registered: {source.value}")

    def get_source(self, source: AudioSource) -> Optional[BaseAudioSource]:
        """Get audio source implementation for a specific source."""
        return self.sources.get(source)

    def get_current_state(self) -> Dict[str, Any]:
        """Return current system state as dict.

        Mirrors the full_state aggregation in `broadcast()`: pulls multiroom_enabled
        from routing_service and equalizer_effects_enabled from camilladsp_service
        so the wire payload (notably the initial_state on WS connect) carries
        both global flags.
        """
        state = self.system_state.to_dict()
        state["multiroom_enabled"] = (
            self.routing_service.multiroom_enabled if self.routing_service else False
        )
        state["equalizer_effects_enabled"] = (
            self.camilladsp_service.effects_enabled if self.camilladsp_service else False
        )
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

            if self.system_state.active_source == target_source and \
               self.system_state.source_state != SourceState.ERROR:
                logger.info(f"Already on source {target_source.value}")
                return True

            if target_source != AudioSource.NONE and target_source not in self.sources:
                logger.error(f"No source registered for: {target_source.value}")
                return False

            try:
                async with asyncio.timeout(self.TRANSITION_TIMEOUT):
                    async with self._state_lock:
                        old_source = self.system_state.active_source
                        self.system_state.transitioning = True
                        self.system_state.active_source = target_source
                        self.system_state.source_state = (
                            SourceState.STARTING if target_source != AudioSource.NONE
                            else SourceState.WAITING
                        )
                        self.system_state.metadata = {}

                    await self.broadcast(SystemTransitionStart())

                    # Stop old source
                    if old_source != AudioSource.NONE:
                        await self._stop_source(old_source)

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
                                self.system_state.source_state = SourceState.WAITING

                    await self.broadcast(SystemTransitionComplete())

                    # Reset inactivity timer on source change
                    self._last_activity_time = monotonic()

                    logger.info(f"Transition completed: {target_source.value}")
                    return True

            except asyncio.TimeoutError:
                logger.error(f"Transition timeout after {self.TRANSITION_TIMEOUT}s")
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.error = "Transition timeout"

                # Broadcast the error before the reset clears state
                await self.broadcast(SystemErrorEvent(
                    source=target_source.value,
                    error="Transition timeout",
                    message=f"Transition timeout after {self.TRANSITION_TIMEOUT}s"
                ))

                await self._reset_after_failed_transition(target_source)
                return False

            except Exception as e:
                logger.error(f"Transition error: {e}")
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.error = str(e)

                # Broadcast the error before the reset clears state
                await self.broadcast(SystemErrorEvent(
                    source=target_source.value,
                    error=str(e),
                    message=str(e)
                ))

                await self._reset_after_failed_transition(target_source)
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
            # (title/artist/uri…) must not survive a partial WAITING payload.
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
        """Refresh metadata from the active source."""
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

    async def _reset_after_failed_transition(self, target_source: AudioSource) -> None:
        """Stop the source whose start failed, then reset to idle and broadcast.

        Only the target is stopped: the previous source was already stopped
        above, and the one-active-source invariant means nothing else is
        running. The target still needs it — a start can fail after its systemd
        unit came up (e.g. mpv started, IPC connect failed). Stopping every
        registered source instead would run Bluetooth's unconditional teardown
        (bluetoothctl + bluealsa/bluetooth.service, no is-running guard) on a
        source the failed transition never touched.
        """
        if target_source != AudioSource.NONE:
            await self._stop_source(target_source)

        async with self._state_lock:
            self.system_state.active_source = AudioSource.NONE
            self.system_state.source_state = SourceState.WAITING
            self.system_state.metadata = {}
            self.system_state.error = None

        # Broadcast the reset state so frontend knows system is stable again.
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

                # Atomic snapshot under lock
                async with self._state_lock:
                    source = self.system_state.active_source
                    source_state = self.system_state.source_state
                    transitioning = self.system_state.transitioning

                if (
                    source != AudioSource.NONE
                    and source_state == SourceState.WAITING
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
        (backend/core/models/ws_events.py); full_state is injected for
        source/system events unless the event class opts out.
        """
        if not self.ws_manager:
            return

        event_payload = event.wire_data()
        if event.INCLUDE_FULL_STATE and event.CATEGORY in self._FULL_STATE_CATEGORIES:
            event_payload["full_state"] = self.get_current_state()

        await self.ws_manager.broadcast_dict(event.to_envelope(event_payload))
