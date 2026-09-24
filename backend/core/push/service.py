# backend/core/push/service.py
"""Decides WHEN Milō pushes, and to which tokens.

Sits on the one emission point every state change already goes through —
`AudioStateMachine.broadcast` — so there is no second place to remember to
notify. `on_event` is synchronous and does nothing but mark the state dirty:
a slow or unreachable APNs must never delay the WebSocket broadcast the UI
depends on.

There is a second way in, and only one: `align_session_to_playback`, called by
the device's own report of the sessions it holds. It emits nothing on its own —
it opens or closes the session when the bus, which ticks on events rather than
on the absence of them, has left the two out of step. Read its docstring before
adding a third entry point.

**Throughput is the real hazard of pushing from an appliance.** APNs is not a
WebSocket. Apple throttles frequent pushes and an abused budget degrades
delivery for the whole app, durably — which is a state no code change here can
undo. Three rules keep it bounded:

  * `SourcePositionUpdate` triggers nothing. Position is sent as a value plus
    a timestamp inside a push that was going to happen anyway, and iOS
    extrapolates. Streaming it would be several pushes a second.
  * The Now Playing push is capped at one per second, last-state-wins: a turn
    of the volume knob emits a burst of `volume_changed`, and the burst
    collapses into one push carrying the level it ended on.
  * The widget push is rarer still, and is NOT on that one-second cap. It
    fires only when what a widget actually displays changes, which is one
    thing — whether Milō can be driven at all, drawn as the logo's opacity.
    Apple budgets these separately and delivers them opportunistically, so
    pushing one per second would spend a day's budget in a minute. See
    `_signature`: the track and the level are not merely too frequent to be
    worth a push, they are drawn nowhere a push can reach.
"""
import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.core.models.ws_events import (
    SourceStateChanged,
    SystemStateChanged,
    SystemTransitionComplete,
    VolumeChanged,
    WsEvent,
)
from backend.core.push.models import PushTokenKind
from backend.core.push.payloads import (
    NowPlayingDevice,
    build_attributes,
    now_playing_payload,
    widget_payload,
)
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger("core.push.service")

# One push per second at most. Chosen against the burst that actually happens
# here — a rotary turn emits a volume_changed per detent — not against a limit
# Apple publishes, because Apple publishes none.
MIN_PUSH_INTERVAL_S = 1.0

# The events worth a push. Everything else on the bus — position ticks,
# favourites, settings — either changes nothing a lock screen shows or changes
# it too often to be worth a push.
TRIGGERS: Tuple[type, ...] = (
    VolumeChanged, SourceStateChanged, SystemStateChanged, SystemTransitionComplete,
)
# `SystemTransitionComplete` is here because nothing else announces the end of a
# source change to this loop, and that is the one moment the active source is
# different. `transition_to_source` holds `transitioning` across the whole
# switch, which suppresses the per-source broadcasts inside it, and the two
# events it does emit — Start and Complete — were in neither this tuple nor any
# other path here. Measured on the appliance 2026-09-22: source left from the
# UI, `Transition completed: none` in the journal, and the session still open a
# minute later; a volume nudge woke the loop and it ended on the spot, which is
# what proved the logic was right and the wake-up missing.
#
# This is not the widening `_signature` warns against. That warning is about
# events which change nothing a widget draws; a completed transition changes the
# active source, which is the whole of what the Now Playing card draws.

# How long a session Milō just started is trusted before the device has
# registered its token. Generous on purpose: the round trip is an HTTP call the
# extension makes on a process the system may terminate first, so it can take
# several wakes. Nothing is lost by waiting — updates to a session with no token
# go nowhere either way.
START_REPORT_GRACE_S = 60.0

# How long nothing may be playing before the session is closed.
#
# A gap is not an ending. A source change passes through "nothing is playing" on
# its way to the next source, and so does a station change: `Stopping radio` at
# 17:17:27, `Transition completed` at 17:17:28. Ending on the first idle cycle
# turned each of those into an `end` and a `start` two seconds apart — the card
# left the Lock Screen, and the session that replaced it was one no app had
# asked to be primary, so nothing came back until the app was relaunched.
#
# Minutes rather than seconds, because the gap is only as short as the person is
# quick. Switching to the music library and taking half a minute to choose an
# album is an ordinary thing to do, and twenty seconds made that ordinary thing
# cost the card — and the app relaunch needed to bring it back, since only a
# foreground app can claim the screen for a session.
#
# What it costs: after playback really stops, the card lingers, showing paused.
# That is what every other player does, and it is the side of the trade whose
# failure is merely untidy rather than a feature that stops working.
SESSION_IDLE_GRACE_S = 300.0


class PushService:
    """Coalesces state changes into APNs pushes and owns the session lifecycle."""

    def __init__(self, token_registry, apns_client, volume_service=None,
                 client_registry_service=None):
        self.logger = logger
        self._registry = token_registry
        self._apns = apns_client
        self._volume_service = volume_service
        self._client_registry = client_registry_service
        self._state_machine = None

        self._dirty = asyncio.Event()
        # Serializes the two seams. The coalescer used to be the only thing that
        # touched the session, and a loop is single file; the device's report is
        # a second caller on the same loop, and both decide on `_session_id`
        # BEFORE awaiting APNs. Interleaved, each would read None and mint a
        # session of its own — the phone holding two, the system showing one and
        # this service feeding the other, which is the failure the whole area
        # exists to avoid.
        self._session_lock = asyncio.Lock()
        self._bg = BackgroundTaskSet(logger, "push")
        self._session_id: Optional[str] = None
        self._session_started_at: float = 0.0
        self._session_renewed_at: float = 0.0
        self._session_cleared_at: float = 0.0
        self._idle_since: float = 0.0
        # The card last sent, and the source it was built under. Only read when
        # the state cannot answer — see `_publish_paused`.
        self._last_attributes: Optional[Dict[str, Any]] = None
        self._last_source: Optional[str] = None
        self._widget_signature: Optional[tuple] = None

    def set_state_machine(self, state_machine) -> None:
        """Wired in dependencies.py STEP 2 — the machine calls back into on_event."""
        self._state_machine = state_machine

    async def initialize(self) -> None:
        """Start the coalescing loop. Cheap even with no key: it idles on an event."""
        self._bg.spawn(self._loop(), label="coalescer")

    async def cleanup(self) -> None:
        await self._bg.cancel_all()

    # =========================================================================
    # THE BUS SEAM
    # =========================================================================

    def on_event(self, event: WsEvent) -> None:
        """Mark the state dirty. Synchronous, and never raises into broadcast().

        Called from `AudioStateMachine.broadcast` beside the WebSocket fan-out.
        It must stay free of I/O: the UI's latency is measured from here, and a
        push that blocked it would trade the thing that works for the thing
        that is best-effort.
        """
        if isinstance(event, TRIGGERS):
            self._dirty.set()

    # =========================================================================
    # THE DEVICE'S REPORT SEAM
    # =========================================================================

    async def align_session_to_playback(self, device_id: str) -> None:
        """Open or close the card, on the report the device just filed.

        Called from `POST /api/push/sessions`, after the registry has retired
        the session tokens the device no longer names. The bus seam above is an
        emitter; this one is a reconciler, and it exists because the two ends of
        the session lifecycle each have a case the bus cannot reach:

        * **No `start`.** A session is opened on a playback EVENT. Music that is
          already playing when no session exists produces no event, so nothing
          opens one — Control Center reads "stopped" until the next track
          change.
        * **No `end` in time.** Measured 2026-09-20: change source, and the
          state goes to `ready` with no metadata while the card keeps the
          previous track, paused, offering the transport of a source that has
          nothing to play. `_consider_ending` does end it, but only after
          `SESSION_IDLE_GRACE_S`, because from the bus a gap and an ending look
          identical — see its docstring for what acting on the first idle cycle
          cost.

        A report is different evidence, and that is the justification for ending
        here without waiting out the grace: it only arrives while the app is
        running, which is the one condition under which a card that turns out to
        have been closed too early can be reopened and claim the screen —
        `RemoteMediaSession.requestToBecomeSystemPrimary` is the app's to call
        and nobody else's. When the app is not running, no report arrives and
        the grace still governs, untouched.

        **What that argument turns on is the card being empty**, which it no
        longer always is. A source that stops with something to resume publishes
        it, so an idle state now comes in two kinds and they want opposite
        answers: a card with nothing behind it is closed at once, as before,
        while one still naming what a play press would bring back is held and
        left to the grace — closing it would put the phone back to the very
        "nothing ever played" this state exists to tell apart. The evidence a
        report carries is about the DEVICE; which of the two this is comes from
        the state, and only the state can say.

        Sends at most one push per report, and at most one per session in each
        direction: `_session_id` is the guard, set by the `start` and cleared by
        the `end`. That is what keeps a route the app calls every couple of
        seconds off the APNs budget the module docstring is about.

        Nothing during `transitioning` — that is the beat of a source change,
        and closing on it would drop the card and raise it again at every
        switch.

        """
        if not self._apns.available or self._state_machine is None:
            return

        state = self._state_machine.get_current_state()
        if state.get("transitioning"):
            return

        async with self._session_lock:
            self._adopt_reported_session()

            if self._has_active_source(state):
                if self._session_id is None and self._device_can_be_started(device_id):
                    await self._start_session(state)
                return

            if self._session_device() != device_id:
                return
            if self._displays_something(state):
                await self._consider_ending(state)
            else:
                await self._end_session()

    def _device_can_be_started(self, device_id: str) -> bool:
        """Does the reporting device hold a token a `start` can be sent to?

        Scoped to the reporter rather than read off the registry as a whole: a
        phone that cannot receive a `start` must not be what makes Milō send one
        to another phone, which would open a session the reporter then adopts
        nothing of.
        """
        return any(
            token.device_id == device_id
            for token in self._registry.tokens_for(PushTokenKind.PUSH_TO_START)
        )

    def _session_device(self) -> Optional[str]:
        """Which device the live session's `end` would be addressed to.

        A session with no token registered for it belongs to nobody this side
        can name, and an `end` for it would reach no one — so it is not this
        device's to close. A phone knows its own sessions and nobody else's,
        exactly as `drop_sessions_absent_from` is scoped.
        """
        if self._session_id is None:
            return None
        target = self._registry.token_for_session(self._session_id)
        return target.device_id if target is not None else None

    # =========================================================================
    # THE LOOP
    # =========================================================================

    async def _loop(self) -> None:
        while True:
            try:
                await self._dirty.wait()
                self._dirty.clear()
                # Sleep FIRST, publish after: events arriving inside the window
                # re-set the flag and are absorbed into the single push at the
                # end of it. That is what makes this last-state-wins rather
                # than first-state-wins.
                await asyncio.sleep(MIN_PUSH_INTERVAL_S)
                await self._publish()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Push cycle failed: {e}", exc_info=True)

    async def _publish(self) -> None:
        if not self._apns.available or self._state_machine is None:
            return

        state = self._state_machine.get_current_state()
        await self._publish_now_playing(state)
        await self._publish_widget()

    # =========================================================================
    # NOW PLAYING
    # =========================================================================

    async def _publish_now_playing(self, state: Dict[str, Any]) -> None:
        """Start, update or end the session, depending on what is playing.

        A session survives a pause — ending it would remove the play button at
        the moment someone reaches for it — and survives a source change: the
        session is "Milō is playing something", not "Milō is playing Spotify",
        so its id stays stable while the track and the source underneath move.

        It does NOT survive the source being left. `align_session_to_playback`
        has always ended on the spot when the state named nothing, and this
        path waited out the whole grace for the same state — so the card
        cleared at once if the app happened to be open to report, and hung on
        for five minutes if it was not. The Lock Screen is the surface that
        matters when the app is closed, which is exactly where it behaved worst.
        """
        async with self._session_lock:
            if not self._has_active_source(state):
                # Both guards are `_consider_ending`'s own, and skipping
                # either was measured on the appliance: without the session
                # check `_end_session` ran on every publish with no session to
                # end — `Now Playing session None ended` in the journal, and
                # `_session_cleared_at` pushed forward each time — and without
                # `transitioning` a source change reads as nothing playing
                # while it is in flight, which empties the Lock Screen on every
                # switch.
                if (self._session_id is not None
                        and not state.get("transitioning")
                        and not self._a_source_is_selected(state)):
                    await self._end_session()
                else:
                    await self._consider_ending(state)
                return

            self._idle_since = 0.0
            self._adopt_reported_session()

            if self._session_id is None:
                await self._start_session(state)
            else:
                await self._update_session(state)

    async def _consider_ending(self, state: Dict[str, Any]) -> None:
        """End only once nothing has been playing for a while.

        A gap is not an ending. Changing source, and changing station within
        radio, both pass through a moment where no source is active — the one
        thing the paragraph above promises a session survives. Acting on the
        first idle cycle broke that promise twice over: the card left the Lock
        Screen, and the `start` that followed two seconds later opened a session
        the app had never asked to be primary, which the system therefore did
        not show. Nothing came back until the app was relaunched.

        `transitioning` says so precisely but says it too briefly: the coalescer
        sleeps a second before publishing, and by then the transition is over.
        It is kept because when it IS visible it is certain, and the delay
        covers the rest.

        The re-check has to be scheduled. This loop only runs on a bus event,
        and the event that mattered — the source going quiet — has already
        happened; without waking ourselves, a session would linger until
        something unrelated happened to stir the bus.

        The grace is what a running app can shorten: `align_session_to_playback`
        ends on the report instead of waiting, because a report only arrives
        while the app is there to reopen the card.
        """
        if self._session_id is None:
            return
        if state.get("transitioning"):
            return

        now = time.time()
        if self._idle_since == 0.0:
            self._idle_since = now
            self._bg.spawn(self._wake_after(SESSION_IDLE_GRACE_S), label="idle-recheck")
            await self._publish_paused(state)
            return
        if now - self._idle_since < SESSION_IDLE_GRACE_S:
            return

        await self._end_session()

    async def _publish_paused(self, state: Dict[str, Any]) -> None:
        """Say the music stopped, without saying the session did.

        Keeping the session through the gap is what stops the card from
        disappearing — but on its own it left the card claiming the previous
        source was still playing, because nothing is published while the state
        reads idle. The last attributes are re-sent with `isPlaying` false, so
        the card holds its place and tells the truth while the next source
        starts.

        **The track it holds comes from the state wherever the state has one.**
        A source that stopped with something to resume publishes what it would
        resume, so the card is rebuilt from it: a station change holds the
        station that was there, which is the least flicker, and a source change
        shows the new source's own idle identity. Both used to be guessed from
        the copy below, and the guess was measured wrong once (radio → spotify,
        2026-09-20: the Lock Screen kept the radio track, paused, under a source
        that had nothing to play, for the whole grace).

        **The copy survives for the sources whose state cannot answer.** Only
        the four mpv sources override `_idle_metadata()`; the receivers —
        AirPlay, Qobuz, Bluetooth — and Spotify/Tidal publish the inert
        pair alone when their sender goes, so rebuilding from that state gives
        a card with every field null, which on the phone is a media card with
        nothing in it. An AirPlay sender that disconnects and comes straight
        back is the ordinary case, and blanking through it is worse than
        holding what was there. Scoped to the SAME source, because a track
        belonging to a source nobody selected any more is the lie above.

        **Not a debt, and not waiting on those six to publish an idle identity:
        they have none to publish.** All six are driven from the other end — the
        four receivers by their sender, Spotify and Tidal by a Connect session
        that is gone by the time the source reads READY — so nothing over there
        can be resumed from here, and "what would come back" is not a fact their
        producers are withholding. What this holds is the last card across a
        GAP, which is a different problem from the one the mpv sources solved by
        publishing what a play press would reopen. Extending that change here
        would mean inventing a "last played" for sources where it means nothing.

        Ending instead of emptying would be the wrong trade and it has been
        measured: a session opened afterwards by a push has never been in the
        foreground, so it cannot ask to be system primary, and nothing comes
        back until the app is relaunched. See `_consider_ending`.

        Rebuilding refreshes the timestamp, where holding the copy keeps the old
        one. Harmless either way: `isPlaying` is forced off below, and iOS
        extrapolates a position from the timestamp only while playing. This also
        fires once per idle window — the second consideration onwards returns
        early in `_consider_ending` — so a fresh stamp costs no extra push.
        """
        if self._session_id is None:
            return
        target = self._registry.token_for_session(self._session_id)
        if target is None:
            return

        holds_over = (
            self._last_attributes is not None
            and not self._displays_something(state)
            and str(state.get("active_source") or "none") == self._last_source
        )
        if holds_over:
            attributes = {**self._last_attributes, "isPlaying": False}
        else:
            attributes = await self._build_attributes(self._session_id, state)
        # Forced, never read off the state. This function has one thing to say
        # and a source that is not active can still carry `is_playing` —
        # Bluetooth's AVRCP feed publishes a transport whether or not BlueALSA
        # calls the source active — which would put a card the music has left
        # back into playing, with a position iOS extrapolates.
        attributes["isPlaying"] = False

        await self._send_all(
            [target],
            now_playing_payload("update", self._session_id, attributes),
            "nowplaying",
        )

    async def _wake_after(self, delay: float) -> None:
        """Stir the coalescer once, later. See `_consider_ending`."""
        await asyncio.sleep(delay)
        self._dirty.set()

    def _adopt_reported_session(self) -> None:
        """Follow the session the device holds, whoever opened it.

        A session has two possible origins — a push to the push-to-start token,
        or `RemoteMediaSession.start` in the app while it runs — and only one of
        them can be the session the system shows. Minting an id here and never
        looking again made this side certain of a session the phone had already
        replaced: `token_for_session` answered None on every cycle and
        `_update_session` returned in silence, for as long as playback lasted.
        Measured 2026-09-19 — fifteen session tokens registered, not one of them
        for an id this file had minted, and not one `update` push ever sent.

        The rule is the freshest registration wins, because that is the most
        recent thing the device has said about which session exists. Three
        guards keep it from following a ghost:

        * nothing registered before the last ending is followed. `_end_session`
          clears the id, and the token it could be adopted from outlives it by
          a moment — so the very next cycle re-adopted the session this service
          had itself just closed. Measured 2026-09-19: "session 96D8F08F
          ended" at 17:04:06, "session 96D8F08F adopted (was None)" at
          17:04:08, and never a `start` again;
        * a session whose token is registered more recently than the newest is
          kept — ours is the live one and the other is a leftover;
        * a session we have just started is kept for `START_REPORT_GRACE_S`
          even though nothing is registered for it yet. Its token takes a
          second round trip to come back, and without this window every start
          would be overwritten by the previous session's leftover token before
          the device had a chance to answer.
        """
        newest = self._registry.newest_session_token()
        if newest is None or newest.session_id == self._session_id:
            return

        if newest.registered_at < self._session_cleared_at:
            return

        if self._session_id is not None:
            ours = self._registry.token_for_session(self._session_id)
            if ours is not None and ours.registered_at >= newest.registered_at:
                return
            if ours is None and time.time() - self._session_started_at < START_REPORT_GRACE_S:
                return

        logger.info(
            f"Now Playing session {newest.session_id} adopted "
            f"(was {self._session_id})"
        )
        self._session_id = newest.session_id
        self._session_started_at = newest.registered_at
        self._session_renewed_at = 0.0

    async def _start_session(self, state: Dict[str, Any]) -> None:
        """Wake a session on the phone through the push-to-start token.

        The id is minted here because the backend is what knows a session began;
        the app learns it from this payload and reports back the session token
        that `update` and `end` then use.
        """
        targets = self._registry.tokens_for(PushTokenKind.PUSH_TO_START)
        if not targets:
            return

        session_id = str(uuid.uuid4())
        payload = now_playing_payload(
            "start", session_id, await self._build_attributes(session_id, state)
        )
        if await self._send_all(targets, payload, "nowplaying"):
            self._session_id = session_id
            self._session_started_at = time.time()
            self._session_renewed_at = 0.0
            logger.info(f"Now Playing session {session_id} started")

    async def _update_session(self, state: Dict[str, Any]) -> None:
        targets = self._registry.token_for_session(self._session_id)
        if targets is None:
            if self._registry.was_lost_to_reboot(self._session_id):
                logger.info(
                    f"Now Playing session {self._session_id} died with the phone "
                    "that held it — starting a new one"
                )
                self._session_id = None
                self._session_started_at = 0.0
                self._session_cleared_at = time.time()
                return

            # No token yet. Keep the session, and knock again.
            #
            # Dropping it is wrong — that was tried, and it turned a late
            # registration into a second, rival session: `8a3983dc` abandoned at
            # 18:15:23 on 2026-09-19, `95a95ca2` started two seconds later, the
            # phone holding both, the system showing one and this service
            # feeding the other.
            #
            # But waiting in silence is wrong too, and for a reason that only
            # showed up once the whole path finally ran. **The wait can never
            # end on its own.** The extension is woken by `update` pushes; Milō
            # sends none without a token; only the woken extension can supply
            # one. Measured 2026-09-19:
            #
            #     18:52:58  extension  SESSION CONSTRUITE a0297239-…
            #     18:53:00  extension  token de session : Milō injoignable
            #        …      98 seconds, no push of any kind
            #     18:54:38  app        POST /api/push/tokens   ← app came forward
            #
            # The extension's own attempt is one mDNS lookup inside a three
            # second budget, and it misses often enough to matter. The fallback
            # — the extension leaves the token in the shared container and the
            # app posts it — only runs while the app is in the foreground, which
            # on a locked phone is never. Between the two, the card stayed dark
            # for as long as nobody opened the app.
            #
            # `start` is the one message that reaches the phone without a
            # session token, so it is what breaks the deadlock: re-sent to the
            # push-to-start token, it wakes the extension again, and each wake is
            # a fresh chance for the registration to land. Carrying the SAME
            # session id is what makes it a retry rather than a second session —
            # the phone rebuilds that session, and this service keeps addressing
            # the one it already knows.
            await self._renew_start(state)
            return
        payload = now_playing_payload(
            "update", self._session_id,
            await self._build_attributes(self._session_id, state),
        )
        await self._send_all([targets], payload, "nowplaying")

    async def _renew_start(self, state: Dict[str, Any]) -> None:
        """Re-send `start` for the session we hold, to shake a token loose.

        Spaced rather than sent every cycle: each one wakes an app extension and
        spends APNs budget, and the token it is fishing for needs a moment to
        come back. `START_REPORT_GRACE_S` is the same window `_adopt_reported_
        session` gives a fresh start before letting a leftover overrule it, so a
        renewal cannot be mistaken for a session the device chose.
        """
        now = time.time()
        waited_since = max(self._session_renewed_at, self._session_started_at)
        if now - waited_since < START_REPORT_GRACE_S:
            return

        targets = self._registry.tokens_for(PushTokenKind.PUSH_TO_START)
        if not targets:
            return

        self._session_renewed_at = now
        payload = now_playing_payload(
            "start", self._session_id,
            await self._build_attributes(self._session_id, state),
        )
        if await self._send_all(targets, payload, "nowplaying"):
            logger.info(
                f"Now Playing session {self._session_id} re-announced — still no "
                "token for it"
            )

    async def _end_session(self) -> None:
        """Close the session, and drop the token that could only address it.

        A session token dies with its session — it is the one kind of token
        whose death this side witnesses rather than learns from a 410. Leaving
        it behind is what let fifteen of them pile up in the registry, and what
        made `_adopt_reported_session` able to follow a session that no longer
        existed.
        """
        session_id, self._session_id = self._session_id, None
        self._session_started_at = 0.0
        self._session_renewed_at = 0.0
        self._session_cleared_at = time.time()
        self._idle_since = 0.0
        target = self._registry.token_for_session(session_id)
        if target is not None:
            await self._send_all(
                [target], now_playing_payload("end", session_id), "nowplaying"
            )
            await self._registry.unregister(target.token)
        logger.info(f"Now Playing session {session_id} ended")

    async def _build_attributes(self, session_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """Project the published state into the attributes the card draws.

        Keeps a copy, for the one case the state cannot answer — see
        `_publish_paused`.
        """
        self._last_source = str(state.get("active_source") or "none")
        self._last_attributes = build_attributes(
            session_id=session_id,
            metadata=state.get("metadata"),
            devices=await self._devices(),
            active_source=self._last_source,
        )
        return self._last_attributes

    async def _devices(self) -> List[NowPlayingDevice]:
        """One slider per room the phone can actually move, and no others.

        The level is normalized here rather than on the phone: only this side
        knows `volume_limits`, and they move.

        `available` and `volume_control` are the SAME two filters
        `global_volume_db` averages over, and they are applied for that reason.
        A speaker that is off, or a client driving an external amp, has a level
        Milō does not count; drawing it anyway gave the lock screen a handle
        that moves nothing and, worse, a different set from the one Milō calls
        the house volume — and the phone reconstructs a master gesture by
        averaging the sliders it was given, so the two averages named different
        numbers. It also put the app at odds with itself: awake, it builds this
        list from `/api/volume/state` and filters on exactly these two flags,
        so a sleeping phone was shown speakers a waking one dropped.
        """
        if not self._volume_service:
            return []

        volume_state = await self._volume_service.get_volume_state()
        config = self._volume_service.volume_config
        names = (
            {mac: c.name for mac, c in self._client_registry.get_all_clients().items()}
            if self._client_registry else {}
        )
        return [
            NowPlayingDevice(
                id=mac,
                name=names.get(mac, mac),
                volume=config.normalize(client.volume_db),
            )
            for mac, client in sorted(volume_state.clients.items())
            if client.available and client.volume_control
        ]

    # =========================================================================
    # WIDGET
    # =========================================================================

    async def _publish_widget(self) -> None:
        """Push only when what a widget draws has actually changed.

        A widget draws one thing that depends on Milō: the logo, dimmed while
        Milō is not both reachable and driveable. It draws neither the source,
        the track nor the play state — `MiloWidgetEntry` carries a
        `MiloWidgetData` and a `showVolume` flag, and nothing else reaches the
        view. `MiloWidgetData.sourceName`/`.availableSources` were deleted
        upstream as dead on 2026-09-20, and this side kept spending a push per
        track and per ACTIVE/READY flip for them.

        Nor does it draw the level, and that has to be stated precisely because
        it reads as a budget question and is not one. `showVolume` is true only
        inside a 5 s window, and the only writers of that window are the
        widget's own +/- buttons. A push arriving because a knob turned in the
        room re-renders the logo, not the digits — so pushing the level buys no
        pixel at any budget. The press that WOULD show digits reloads the
        timeline itself and re-reads the level from Milō before drawing it.
        """
        signature = await self._signature()
        if signature == self._widget_signature:
            return

        targets = self._registry.tokens_for(PushTokenKind.WIDGET)
        if not targets:
            self._widget_signature = signature
            return

        # Recorded only once Apple has taken it. This used to be stamped before
        # the send and the result dropped, which was survivable while the
        # signature moved on every track change — the next track retried it by
        # accident. It no longer moves on anything but a rare flip, so a push
        # lost to a transient failure would be lost for good, and the thing it
        # was carrying is a logo left in the wrong state until WidgetKit's own
        # refresh. The retry costs nothing extra in the normal case: it is the
        # same coalesced cycle, and `_send_all` already purges a token Apple
        # calls dead rather than retrying it forever.
        if await self._send_all(targets, widget_payload(), "widgets", priority=5):
            self._widget_signature = signature

    async def _signature(self) -> tuple:
        """Is the logo lit — the whole of what a widget draws from this state.

        `any_volume_control` is the half of the widget's own
        `isReady = isConnected && canControlVolume` that this side can observe.
        Note the short-circuit it is read through: while the local client holds
        volume control the value is pinned True whatever the satellites do, so
        on a unit that is not a DAC this signature is constant and the only
        widget push left is the one the paragraph below describes. It moves on
        a DAC-configured unit, where it is also the only thing that can dim the
        logo for a reason Milō knows.

        What wakes the coalescer for it is NOT complete, and the gap is on the
        dim side. `VolumeChanged` is broadcast when a client reconnects
        (`_sync_reconnecting_client_volume`), so the relight is seen; it is NOT
        broadcast when one drops (`set_client_online(mac, False)` reaches
        `VolumeStateStore.set_client_availability` and stops there) nor by
        `PATCH /api/multiroom/clients/{mac}` changing `volume_control`. Those
        two flips therefore reach no push and wait for the widget's own
        timeline. The missing broadcasts are a defect in those paths — the Dock
        reads `any_volume_control` too and goes stale on the same event — and
        not something to work around here by widening `TRIGGERS`, which would
        wake this loop for events that change nothing a widget draws.

        The other half cannot be pushed while it is false: a unit that is not
        reachable is not sending anything either. Coming back needs no hook and
        deliberately does not get one — `_widget_signature` starts None, so the
        first publish after a restart pushes whatever it finds and the logo
        relights without waiting out the widget's 5-minute unreachable retry. A
        boot push would be delivered opportunistically, which is a weaker
        guarantee than the retry it would be racing.

        Read through the volume service, because the broadcast state does not
        carry it. Wired without one, the signature is constant and nothing is
        pushed past the first cycle — the same fail-open this service already
        applies to a missing signing key.
        """
        if self._volume_service is None:
            return ()
        volume_state = await self._volume_service.get_volume_state()
        return (volume_state.any_volume_control,)

    # =========================================================================
    # DELIVERY
    # =========================================================================

    async def _send_all(self, targets, payload: Dict[str, Any], push_type: str,
                        priority: int = 10) -> bool:
        """Send to each target, purge the ones APNs called dead. True if any landed."""
        results = await asyncio.gather(*(
            self._apns.send(t, payload, push_type, priority=priority) for t in targets
        ))

        delivered = []
        for target, result in zip(targets, results):
            if result.ok:
                delivered.append(target.token)
            elif result.dead:
                await self._registry.purge(
                    target.token, invalidated_at=result.invalidated_at
                )

        # Stamped only for the ones Apple accepted. `last_push_at` is the only
        # way an operator reading push_tokens.json can tell a token that is
        # idle from one that is broken, and stamping a refused send would make
        # it say the opposite of what happened.
        if delivered:
            await self._registry.mark_pushed(delivered)
        return bool(delivered)

    @staticmethod
    def _displays_something(state: Dict[str, Any]) -> bool:
        """Would the card drawn from this state name anything at all?

        Read off `title`, because that is the field the card leads with and the
        one `build_attributes` projects — asking a second question of a second
        field is how "the lock screen shows a session" and "the state says there
        is one" come to disagree. Every source fills it whenever it has
        something to show, playing or stopped-and-resumable; the inert
        {is_playing, is_buffering} pair is what an ending publishes.
        """
        metadata = state.get("metadata") or {}
        return bool(metadata.get("title"))

    @staticmethod
    def _a_source_is_selected(state: Dict[str, Any]) -> bool:
        """Is any source selected at all — playing, warming up or idle?

        Strictly weaker than `_has_active_source`, and the two are not
        interchangeable: this is true wherever that one is, AND wherever a
        source is selected without being active — every source just switched
        to and not yet started, and every source that has gone quiet. Only
        `none` means nobody chose anything.

        This is what lets a session end the moment the source is LEFT while a
        source merely going quiet keeps its grace. The distinction is safe
        because `transition_to_source` assigns the target in the same locked
        block that raises `transitioning`, so a change from one source to
        another never passes through `none` — measured 2026-09-22, sampling
        /api/audio/state across radio -> spotify: `spotify/starting` then
        `spotify/ready`, and `none` in no sample. Were that ever to change,
        leaving a source and changing source would become indistinguishable
        here and the card would blink on every switch.
        """
        source = state.get("active_source")
        return bool(source) and str(source) != "none"

    @staticmethod
    def _has_active_source(state: Dict[str, Any]) -> bool:
        """Something is playing when a real source is active.

        Read off the same two fields the UI reads, so "the lock screen shows a
        session" and "the screen shows a player" cannot disagree.
        """
        source = state.get("active_source")
        return bool(source) and str(source) != "none" and state.get("source_state") == "active"
