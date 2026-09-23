# backend/shared/mpv_audio_source.py
"""
MpvAudioSource - Intermediate base class for mpv-based audio sources.

Two ways to follow mpv live here side by side, and the second replaces the
first as the sources migrate (docs: source architecture). Radio, Podcast and
Music Library listen to mpv's events (`_listen_to_mpv`): the phase of their
session is what mpv announces — playback-restart, pause, paused-for-cache,
end-file with its reason and entry id — and the one-second loop only reads the
playhead while sound plays. The CD still polls through `_monitor_loop()` and
its hooks (`_on_monitor_tick`, `_on_mpv_disconnect`) until it migrates in
phase 2, which deletes them.
"""
import asyncio
from dataclasses import dataclass
from backend.core.models.ws_events import SourceErrorReason
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState
from backend.core.models.session import (
    EndReason, IdlePolicy, Phase, PhaseEvent, Session,
)
from backend.shared.mpv import LINK_LOST, MpvController


@dataclass(eq=False)
class MpvSession(Session):
    """A session played by mpv. `link` and `entry` are its generation tokens:
    an event from another link, or about another playlist entry, is not about
    this session. `opened` is whether mpv has restarted playback on the
    current entry since it started (playback-restart)."""
    link: Any = None
    entry: Optional[int] = None
    started: bool = False    # mpv has started one of this session's own entries
    opened: bool = False
    position: int = 0        # seconds into the current entry
    duration: int = 0        # seconds; 0 while unknown
    ticks: int = 0           # one-second ticks while sound plays


class MpvAudioSource(BaseAudioSource):
    """
    Base class for audio sources that use mpv for playback.

    Extends BaseAudioSource with:
    - mpv controller and socket management
    - Shared _monitor_loop() with disconnect/tick hooks
    - Common _is_buffering state
    """

    # Interval (in ticks) between position-only broadcasts.
    # Frontend interpolates locally, so this is just drift correction.
    POSITION_SYNC_INTERVAL = 30

    # Seconds between two _on_monitor_tick() calls. Declared here because it is
    # the loop below that sets it, and a subclass counting elapsed playback in
    # ticks (music_library's scrobble threshold) needs the same number.
    MONITOR_TICK_S = 1.0

    # How long a session may stay LOADING before it ends. Measured on mpv 0.40
    # with the radio unit's options: a stream that stops delivering sets
    # paused-for-cache and then announces nothing for 155 s (a dead server) or
    # ever (a silent socket), while a server that comes back is picked up
    # within 12 s. Past this, LOAD_FAILED if no sound left yet, STREAM_LOST if
    # it did.
    STALL_TIMEOUT_S = 30.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mpv_socket = self._config.get(
            "mpv_socket", f"/run/milo/{self.source_id}-ipc.sock"
        )
        self._mpv: Optional[MpvController] = None
        self._is_buffering = False
        self._position_ticks: int = 0

        # Auto-stop on mpv pause (effective enable controlled by
        # the global delay: 0 means disabled, see _load_auto_stop_config).
        self.auto_stop_enabled = True
        # Last observed pause state, used by _handle_pause_change to debounce
        # repeated same-state calls from a subclass's monitor tick.
        self._was_paused: bool = False
        # What mpv last announced of its two global properties the phase is
        # computed from (observed; mpv sends the current value on observe).
        self._mpv_paused = False
        self._mpv_stalled = False
        self._position_task: Optional[asyncio.Task] = None
        self._tick_posted = False

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._is_buffering = False
        self._position_ticks = 0
        self._was_paused = False
        self._cancel_pause_timer()

    def _position_sync_due(self) -> bool:
        """Return True every POSITION_SYNC_INTERVAL ticks, then reset."""
        self._position_ticks += 1
        if self._position_ticks >= self.POSITION_SYNC_INTERVAL:
            self._position_ticks = 0
            return True
        return False

    async def _attach_mpv(self) -> bool:
        """Build this source's mpv controller and open its IPC link.

        The one attach for the four mpv sources, across five call sites (CD does
        it twice: once for playback, once for its pre-start warm-up). It replaces
        four copies of the same three lines, whose failure report had drifted
        into two spellings of the same sentence at two different levels — and
        which said nothing `connect()` had not already said.

        Silent on failure on purpose: connect() logs what it waited for and how
        long, and `start()` turns this False into the source's single report. A
        line here would be the second one in the journal for one event.

        The caller starts the unit itself, and the gap it leaves before calling
        this is free — mpv publishes its socket some time after exec (0.269s on
        this unit warm, 1.081s with its 51 MB of libraries evicted, past 7s when
        something else is reading the same card), so work done in between is
        time not spent waiting. Radio loads its station data there.
        """
        self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
        return await self._mpv.connect()

    def mpv_refused(self, action: str) -> Dict[str, Any]:
        """Error response for a transport command mpv did not take.

        MpvController answers False (and logs at debug) whenever the IPC link is
        down, so the bool is the only channel this failure has. Return this
        *before* flipping any playback flag: the flip is what makes the UI draw
        a play button over audio that is still running.
        """
        self._logger.error(f"mpv did not take '{action}' (IPC link down?)")
        return self.error_response(f"mpv did not take: {action}")

    # === Monitor skeleton ===

    async def _monitor_loop(self) -> None:
        """Monitor mpv connection and playback state."""
        mpv_was_up = False
        try:
            while True:
                await asyncio.sleep(self.MONITOR_TICK_S)

                # Per-pass, not around the loop: one raising hook used to end
                # the monitor for the rest of the session — no disconnect
                # banner, no idle publish, nothing polling again. CancelledError
                # is a BaseException, so it still reaches the exit below.
                try:
                    if not self._mpv or not self._mpv.is_connected:
                        # Latched here rather than read off _is_playing: the tick
                        # below queries the dying mpv one pass earlier and clears
                        # that flag itself (radio does `_is_playing = await
                        # is_playing()`), so by the time is_connected flips, the
                        # flag the fallback used to gate on is already False and
                        # the source stays ACTIVE with a frozen card forever.
                        dropped, mpv_was_up = mpv_was_up, False
                        if (
                            dropped
                            and self._state == SourceState.ACTIVE
                            and not self._mpv_swap_in_progress()
                        ):
                            self._logger.error("mpv disconnected unexpectedly during playback")
                            await self._on_mpv_disconnect()
                            # Before the banner: SourceError carries full_state,
                            # and the hook above only cleared the source's own
                            # fields.
                            await self._publish_idle()
                            self.broadcast_error(SourceErrorReason.STREAM_DISCONNECTED)
                        continue

                    mpv_was_up = True
                    await self._on_monitor_tick()
                except Exception as e:
                    self._logger.error(f"Monitor error: {e}")

        except asyncio.CancelledError:
            pass

    def _mpv_swap_in_progress(self) -> bool:
        """True while the source is deliberately tearing mpv down to start another.

        Such a window looks exactly like a crash from the loop — mpv gone with
        the source still ACTIVE — so a source that swaps mpv under itself (CD,
        on every seek and prev-restart) must say so.
        """
        return False

    # === Auto-stop on pause ===

    def _handle_pause_change(self, is_paused: bool) -> None:
        """Arm or cancel the auto-stop timer on a pause-state edge.

        Called by subclasses with whatever pause signal they already track:
        an mpv property they polled in their tick (radio, podcast) or an
        explicit user command (CD play/pause/resume/stop). Edge-tracking via
        `_was_paused` keeps repeated same-state calls cheap.
        """
        if not self.auto_stop_enabled:
            return
        if is_paused == self._was_paused:
            return
        self._was_paused = is_paused
        if is_paused:
            self._start_pause_timer()
        else:
            self._cancel_pause_timer()

    async def _on_auto_stop(self) -> None:
        """Stop playback in-source after pause timeout.

        Keeps active_source unchanged (source_state → READY). The
        AudioPlayer hides while the user's source-tab stays open; the 12h
        INACTIVITY_TIMEOUT in AudioStateMachine handles the final source
        close.

        Uses a CAS guard so a user who switched away between the timer
        firing and the method body running doesn't trigger a stop on the
        freshly-activated source.

        Subclasses implement `_auto_stop_action()` to perform their
        source-specific stop sequence (clear current item, stop mpv,
        broadcast READY).
        """
        if (
            self.state_machine
            and self.state_machine.system_state.active_source != self.source
        ):
            return
        if self.RESUME_POLICY is not None:
            # On the session model: the idle timeout ends the paused session.
            # (The CD still has its own stop sequence until it migrates.)
            if self._session is not None:
                await self._before_idle_end(self._session)
                await self._end_playback(EndReason.IDLE_TIMEOUT)
            return
        await self._auto_stop_action()

    async def _auto_stop_action(self) -> None:
        """Source-specific in-source stop. Override in subclass."""
        raise NotImplementedError

    async def _on_mpv_disconnect(self) -> None:
        """Hook: called on unexpected mpv disconnect during playback.

        Override to save state and clear source-specific fields.
        """

    async def _on_monitor_tick(self) -> None:
        """Hook: called each monitor cycle when mpv is connected.

        Override to check playback state, update position, etc.
        """


    # =========================================================================
    # SESSION-DRIVEN (Radio, Podcast, Music Library)
    # =========================================================================

    async def _listen_to_mpv(self) -> None:
        """Subscribe to the controller just attached and observe the two
        properties the phase is computed from. The subscription outlives links;
        the controller re-observes on every reconnect."""
        # A new controller: nothing it has not announced yet is known.
        self._mpv_paused = self._mpv_stalled = False
        self._mpv.subscribe(self._post_feed_pair)
        await self._mpv.observe("pause")
        await self._mpv.observe("paused-for-cache")
        if self._position_task is None:
            self._position_task = asyncio.create_task(self._position_loop())

    def _post_feed_pair(self, event: Dict[str, Any], link: Any) -> None:
        """The controller's subscriber: runs on its reader task, so it posts."""
        self._post_feed((event, link))

    async def _detach_mpv(self) -> None:
        """Close the IPC link and the playhead loop (the session is over)."""
        if self._position_task is not None:
            self._position_task.cancel()
            self._position_task = None
        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

    def _phase_from_mpv(self, session: MpvSession) -> Phase:
        if self._mpv_paused:
            return Phase.PAUSED
        if session.opened and not self._mpv_stalled:
            return Phase.PLAYING
        return Phase.LOADING

    def _sync_phase(self, session: MpvSession, cause: PhaseEvent = PhaseEvent.STALLED) -> None:
        """Move the session to the phase mpv's facts give, through the table.

        `cause` names why a playing session went back to LOADING (a stall, a
        track change); the other moves have one cause each.
        """
        target = self._phase_from_mpv(session)
        before = session.phase
        if target is before:
            return
        if target is Phase.PAUSED:
            session.advance(PhaseEvent.PAUSED)
        elif before is Phase.PAUSED:
            # Unpaused onto a file not open yet, or a dry cache: no sound left,
            # so never through PLAYING (which would count it as heard).
            session.advance(PhaseEvent.RESUMED if target is Phase.PLAYING else PhaseEvent.STALLED)
        elif target is Phase.PLAYING:
            session.advance(PhaseEvent.SOUND_STARTED)
        else:
            session.advance(cause)
        self._on_phase_changed(session, before)

    def _on_phase_changed(self, session: MpvSession, before: Phase) -> None:
        """Timers follow the phase: PAUSED arms the idle timeout, LOADING the
        loading watchdog; leaving either disarms it."""
        phase = session.phase
        if phase is Phase.PAUSED and self.IDLE_POLICY is IdlePolicy.AUTO_STOP:
            if self.auto_stop_enabled:
                self._arm_timer("idle", self.auto_stop_delay, session)
        elif before is Phase.PAUSED:
            self._disarm_timer("idle")
        if phase is Phase.LOADING:
            self._arm_timer("stall", self.STALL_TIMEOUT_S, session)
        elif before is Phase.LOADING:
            self._disarm_timer("stall")
        if phase is Phase.PLAYING:
            self.broadcast_error_cleared()

    def open_session(self, session: Session) -> Session:
        """The opener states the phase (LOADING, or PAUSED for a paused
        restore): what mpv last announced may predate the load being set up."""
        super().open_session(session)
        self._tick_posted = False
        if isinstance(session, MpvSession):
            self._on_phase_changed(session, Phase.CONNECTED)
        return session

    async def _mpv_ready(self) -> bool:
        """Re-attach before a load: systemd puts a new mpv on the same socket a
        few seconds after a crash, and every command on the dead link would be
        dropped — starting playback is the act that picks it back up."""
        return self._mpv is not None and await self._mpv.ensure_connected()

    async def _attempt(self, step: Callable[[], Awaitable[Any]]) -> Any:
        """Run the mpv part of a load; an exception reads as a refusal (None).

        A session is already open and published when its load runs: an
        exception escaping here would leave it LOADING until the watchdog, and
        report one failure twice.
        """
        try:
            return await step()
        except Exception as e:
            self._logger.error(f"Loading into mpv failed: {e}")
            return None

    async def _set_mpv_pause(self, paused: bool) -> bool:
        """Put mpv's global pause where the next load needs it. Always sent:
        the last announced value can be stale — a pause still in the mailbox,
        a new mpv behind a reroute that holds the mailbox. The fact is recorded
        at once; the property-change event only confirms it."""
        if not await self._mpv.set_property("pause", paused):
            return False
        self._mpv_paused = paused
        return True

    async def _handle_feed(self, events: List[Tuple[Dict[str, Any], Any]]) -> None:
        # Consumed one by one: a stop that cuts this handler hands what is
        # left back to the next Feed (BaseAudioSource._dispatch).
        while events:
            event, link = events.pop(0)
            name = event.get("event")
            if name == LINK_LOST and (self._mpv is None or self._mpv.link in (None, link)):
                # A new mpv starts unpaused, with an empty cache; a link opened
                # since has already observed the real values.
                self._mpv_paused = self._mpv_stalled = False
            if name == "property-change" and link is (self._mpv.link if self._mpv else None):
                if event.get("name") == "pause":
                    self._mpv_paused = bool(event.get("data"))
                elif event.get("name") == "paused-for-cache":
                    self._mpv_stalled = bool(event.get("data"))
            session = self._session
            if not isinstance(session, MpvSession) or link is not session.link:
                continue
            if name == LINK_LOST:
                await self._mpv_lost(session)
                continue
            cause = PhaseEvent.STALLED
            if name == "start-file":
                await self._entry_started(session, event.get("playlist_entry_id"))
                cause = PhaseEvent.TRACK_CHANGE
            elif name == "playback-restart":
                # No entry id on this one: it counts only once mpv started an
                # entry of this session (a restart left over from the previous
                # session's entry, on the same link, is not its sound).
                if session.started:
                    session.opened = True
            elif name == "end-file":
                await self._entry_ended(session, event)
                continue
            if self._session is session:
                self._sync_phase(session, cause)

    async def _entry_started(self, session: MpvSession, entry: Optional[int]) -> None:
        """mpv started a playlist entry. Default: only the session's own entry
        counts; a queue source maps entries to its tracks."""
        if entry == session.entry:
            session.started = True
            session.opened = False

    async def _entry_ended(self, session: MpvSession, event: Dict[str, Any]) -> None:
        """mpv ended a playlist entry. Default (one entry per session): the
        entry's end is the session's end, by mpv's reason — a stop is Milō's
        own doing and ends nothing."""
        if event.get("playlist_entry_id") != session.entry:
            return
        reason = event.get("reason")
        if reason == "eof" and session.heard:
            await self._content_finished(session)
        elif reason in ("eof", "error"):
            await self._end_playback(
                EndReason.STREAM_LOST if session.heard else EndReason.LOAD_FAILED,
                detail=event.get("file_error") or reason,
            )

    async def _content_finished(self, session: MpvSession) -> None:
        """The session's content played to its end (EOF)."""
        await self._end_playback(EndReason.EOF)

    async def _mpv_lost(self, session: MpvSession) -> None:
        """The IPC link died under a live session: systemd brings a new mpv
        up, but it knows nothing of this session."""
        self._logger.error("mpv disconnected unexpectedly during playback")
        await self._end_playback(EndReason.DAEMON_DIED, stop_mpv=False)

    async def _end_playback(
        self, reason: EndReason, *, detail: Optional[str] = None, stop_mpv: bool = True,
        extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        """End the session for `reason` and publish READY — the one ending for
        every cause that is not a lifecycle step (those tear mpv down too).

        A failure is reported once, as a banner after the state it leaves
        (SourceError carries full_state). A stop of mpv is best effort: a
        session ends even when mpv cannot be told.
        """
        if stop_mpv and self._mpv is not None:
            await self._mpv.stop()
        await self.end_session(reason)
        self._update_connection_state(extras)
        banner = self._failure_banner(reason)
        if banner is not None:
            self._logger.error(f"Playback ended: {reason.value}" + (f" ({detail})" if detail else ""))
            self.broadcast_error(banner)

    def _failure_banner(self, reason: EndReason) -> Optional[str]:
        """The banner an end for `reason` raises, if any."""
        if reason is EndReason.LOAD_FAILED:
            return SourceErrorReason.STREAM_LOAD_FAILED
        if reason in (EndReason.STREAM_LOST, EndReason.DAEMON_DIED):
            return SourceErrorReason.STREAM_DISCONNECTED
        return None

    async def _on_timer(self, name: str, token: object) -> None:
        if name == "stall":
            session = self._session
            if session is token and session.phase is Phase.LOADING:
                await self._end_playback(
                    EndReason.STREAM_LOST if session.heard else EndReason.LOAD_FAILED,
                    detail=f"still loading after {self.STALL_TIMEOUT_S:.0f} s",
                )

    async def _before_idle_end(self, session: MpvSession) -> None:
        """Hook: what a source saves before its idle end (Podcast: progress)."""

    async def _position_loop(self) -> None:
        """One tick a second while sound plays, handled in the actor."""
        try:
            while True:
                await asyncio.sleep(self.MONITOR_TICK_S)
                session = self._session
                if (
                    isinstance(session, MpvSession)
                    and session.phase is Phase.PLAYING
                    and not self._tick_posted
                ):
                    self._tick_posted = True
                    self._post_result(lambda s=session: self._tick(s), token=session)
        except asyncio.CancelledError:
            pass

    async def _tick(self, session: MpvSession) -> None:
        self._tick_posted = False
        if session.phase is Phase.PLAYING:
            session.ticks += 1
            await self._on_playing_tick(session)

    async def _on_playing_tick(self, session: MpvSession) -> None:
        """Hook: one second of sound (read the playhead, push it)."""

    async def _read_playhead(self, session: MpvSession) -> Tuple[Optional[float], bool]:
        """Read time-pos and duration into the session. Returns (the raw
        position, whether the duration just became known)."""
        position = await self._mpv.get_property("time-pos")
        duration = await self._mpv.get_property("duration")
        if position is not None:
            session.position = int(position)
        just_known = False
        if duration is not None:
            just_known = session.duration == 0 and int(duration) > 0
            session.duration = int(duration)
        return position, just_known

    def _sync_bar(self, session: MpvSession, just_known: bool) -> None:
        """The periodic drift correction, and at once when the length becomes
        known (a feed that publishes none leaves the bar hidden until then)."""
        if just_known:
            self.broadcast_position_update(session.position * 1000, session.duration * 1000)
        elif session.ticks % self.POSITION_SYNC_INTERVAL == 0:
            self.broadcast_position_update(session.position * 1000, session.duration * 1000)

    @staticmethod
    def _flags(phase: Optional[Phase]) -> Tuple[bool, bool]:
        """The old wire's (is_playing, is_buffering) for a phase."""
        return phase is Phase.PLAYING, phase is Phase.LOADING
