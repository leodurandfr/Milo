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
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, Optional

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
    former across every acquisition of the latter. The multiroom reroute obeys
    the same order: reroute_active_source() takes the transition lock and
    AudioRoutingService holds only its own `_routing_lock` above it.
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
        self.push_service = None

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
        receives meanwhile wait their turn in its mailbox. `transitioning` is
        deliberately not set: the STARTING published first must reach the UI
        live. Every path that does not end in the source publishing its own
        start — `apply_mode` raising, ACQUIRE answering False or raising —
        republishes the source's real state, so STARTING is never the last word.
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

            # State-only change: the current track stays visible during the
            # reroute (a payload here would replace it).
            await self.update_source_state(
                source=active, new_state=SourceState.STARTING, metadata=None
            )

            try:
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
                if not reacquired:
                    await self.update_source_state(
                        source=active, new_state=instance.state, metadata=instance.metadata
                    )
                elif self.system_state.source_state == SourceState.ERROR:
                    # The reacquire is a full start that succeeded: the one thing
                    # that lifts ERROR, which no publish of the source's may do.
                    await self._resync_after_start(active, instance)
            except Exception:
                await self.update_source_state(
                    source=active, new_state=instance.state, metadata=instance.metadata
                )
                raise

    async def _resync_after_start(self, active: AudioSource, instance) -> None:
        """Take the source's own state as the machine's, after a start succeeded."""
        async with self._state_lock:
            if self.system_state.active_source != active:
                return
            self.system_state.source_state = instance.state
            self.system_state.metadata = instance.metadata
            self.system_state.error = None
        await self.broadcast(SourceStateChanged(
            source=active.value, new_state=instance.state.value, metadata=instance.metadata,
        ))

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
                # ERROR lands with `transitioning` cleared, in the same write:
                # the banner below carries full_state, and a STARTING in it drew
                # a "starting" card under the error, Dock re-enabled, for as
                # long as the failed target took to stop (E03).
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.source_state = (
                        SourceState.ERROR if target_source != AudioSource.NONE
                        else SourceState.READY
                    )
                    self.system_state.metadata = {}
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

                await self._settle_failed_transition(target_source, error, teardown)
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

            # ERROR is the answer to a failed start, and only a start that
            # succeeds replaces it — through the resync of a transition or of a
            # reroute, never through here. A source with a feed that outlives
            # its start (the CD's disc watcher) otherwise published READY over
            # it: the card lost "Retry" and re-selecting became a no-op (E07).
            if (self.system_state.source_state == SourceState.ERROR
                    and new_state != SourceState.ERROR):
                logger.debug(f"Ignoring {new_state.value} from {source.value}: it is in error")
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
        connection's initial_state carries them). The write stays here so state
        mutation lives in the state machine.

        Guarded exactly like update_source_state, plus ACTIVE: a playhead is a
        claim about a live session, and every producer only ticks while it is
        publishing one. Without the last two, a producer that awaits its
        hardware between reading the playhead and pushing it (Bluetooth's AVRCP
        read is the one that does) could stamp a position onto a payload that
        has already gone idle. What is closed here is the cached record — the
        one `initial_state` hands a connecting client; the live
        SourcePositionUpdate is a separate event every consumer already gates
        on is_playing, which is why nothing drew either of them."""
        async with self._state_lock:
            sm = self.system_state
            if sm.active_source != source or sm.transitioning:
                return
            if sm.source_state is not SourceState.ACTIVE:
                return
            if sm.metadata is not None:
                sm.metadata["position"] = position
                sm.metadata["duration"] = duration

    @handle_errors(default=False, level='warning')
    async def refresh_active_metadata(self) -> bool:
        """Refresh metadata from the active source (GET /api/audio/state, WS handshake).

        Metadata only — unlike the post-start resync in transition_to_source(),
        which re-reads `source.state` as well. The difference is deliberate but
        narrow: a source that changes state re-publishes through
        update_source_state() on its own, so there is nothing here to copy. Six
        sources implement the hook (Spotify, Qobuz, CD, Podcast, Music Library,
        Bluetooth). Spotify's reads go-librespot's /status and follows it through
        the same reconcile() its /events handler uses, publishing any change
        itself — this copy only carries the playhead it read.
        """
        active = self.system_state.active_source
        if active == AudioSource.NONE:
            return False

        source = self.sources.get(active)
        if not source:
            return False

        if not await source.refresh_when_idle():
            return False

        # The hook awaited the source's daemon, and a transition may have run
        # inside that await: a switch away makes this the outgoing source's
        # record, and one still in flight owns the record until its post-start
        # resync. Either way the read is stale — the same guard as
        # update_position_metadata().
        async with self._state_lock:
            if self.system_state.active_source != active or self.system_state.transitioning:
                return False
            self.system_state.metadata = source.metadata
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
        self, target_source: AudioSource, error: str, teardown: Optional[asyncio.Task]
    ) -> None:
        """Let the old teardown finish, stop the source whose start failed, then
        settle it in ERROR.

        The source stays *selected*: "this source is in error" is exactly what
        happened, and dropping back to "no source" would throw that away — plus
        it is what makes the retry above reachable, since re-selecting a source
        only restarts it while its state is ERROR. `error` is kept in
        system_state so full_state carries the message the card reads; the
        banner rides on the SystemErrorEvent emitted just before.

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
        (backend/core/models/ws_events.py), which also decides — alone — whether
        full_state rides along, via its INCLUDE_FULL_STATE flag.
        """
        if not self.ws_manager:
            return

        event_payload = event.wire_data()
        if event.INCLUDE_FULL_STATE:
            event_payload["full_state"] = self.get_current_state()

        await self.ws_manager.broadcast_dict(event.to_envelope(event_payload))

        # APNs fan-out rides the same single emission point, so there is no
        # second place to remember to notify. Synchronous and non-raising by
        # contract: it only marks state dirty, and the push happens on the
        # service's own loop — a slow Apple must never delay this broadcast.
        if self.push_service:
            self.push_service.on_event(event)
