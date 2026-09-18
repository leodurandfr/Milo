# backend/core/push/service.py
"""Decides WHEN Milō pushes, and to which tokens.

Sits on the one emission point every state change already goes through —
`AudioStateMachine.broadcast` — so there is no second place to remember to
notify. `on_event` is synchronous and does nothing but mark the state dirty:
a slow or unreachable APNs must never delay the WebSocket broadcast the UI
depends on.

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
    fires only when what a widget actually displays changes — source, track,
    play state — because a volume tick does not move a widget, and Apple
    budgets these separately and delivers them opportunistically. Pushing one
    per second would spend a day's budget in a minute.
"""
import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.core.models.ws_events import (
    SourceStateChanged,
    SystemStateChanged,
    VolumeChanged,
    WsEvent,
)
from backend.core.push.models import PushTokenKind
from backend.core.push.payloads import (
    NowPlayingDevice,
    build_attributes,
    normalize_volume,
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
TRIGGERS: Tuple[type, ...] = (VolumeChanged, SourceStateChanged, SystemStateChanged)


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
        self._bg = BackgroundTaskSet(logger, "push")
        self._session_id: Optional[str] = None
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
        await self._publish_widget(state)

    # =========================================================================
    # NOW PLAYING
    # =========================================================================

    async def _publish_now_playing(self, state: Dict[str, Any]) -> None:
        """Start, update or end the session, depending on what is playing.

        A session survives a pause — ending it would remove the play button at
        the moment someone reaches for it — and survives a source change: the
        session is "Milō is playing something", not "Milō is playing Spotify",
        so its id stays stable while the track and the source underneath move.
        """
        playing = self._has_active_source(state)

        if not playing:
            if self._session_id:
                await self._end_session()
            return

        if self._session_id is None:
            await self._start_session(state)
        else:
            await self._update_session(state)

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
            logger.info(f"Now Playing session {session_id} started")

    async def _update_session(self, state: Dict[str, Any]) -> None:
        targets = self._registry.token_for_session(self._session_id)
        if targets is None:
            # The app has not reported this session's token yet. Not an error:
            # `start` was accepted and the registration is a second round trip.
            return
        payload = now_playing_payload(
            "update", self._session_id,
            await self._build_attributes(self._session_id, state),
        )
        await self._send_all([targets], payload, "nowplaying")

    async def _end_session(self) -> None:
        session_id, self._session_id = self._session_id, None
        target = self._registry.token_for_session(session_id)
        if target is not None:
            await self._send_all(
                [target], now_playing_payload("end", session_id), "nowplaying"
            )
        logger.info(f"Now Playing session {session_id} ended")

    async def _build_attributes(self, session_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        return build_attributes(
            session_id=session_id,
            metadata=state.get("metadata"),
            devices=await self._devices(),
            active_source=str(state.get("active_source") or "none"),
        )

    async def _devices(self) -> List[NowPlayingDevice]:
        """One entry per snapcast client, so the lock screen gets one slider per room.

        The level is normalized here rather than on the phone: only this side
        knows `volume_limits`, and they move.
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
                volume=normalize_volume(
                    client.volume_db, config.limit_min_db, config.limit_max_db
                ),
            )
            for mac, client in sorted(volume_state.clients.items())
        ]

    # =========================================================================
    # WIDGET
    # =========================================================================

    async def _publish_widget(self, state: Dict[str, Any]) -> None:
        """Push only when what a widget shows has actually changed.

        A widget draws the source, the track and the play state. It does not
        draw the volume, so a knob turn must not spend a push — Apple budgets
        these per day and delivers them when it chooses.
        """
        signature = self._signature(state)
        if signature == self._widget_signature:
            return
        self._widget_signature = signature

        targets = self._registry.tokens_for(PushTokenKind.WIDGET)
        if targets:
            await self._send_all(targets, widget_payload(), "widgets", priority=5)

    @staticmethod
    def _signature(state: Dict[str, Any]) -> tuple:
        metadata = state.get("metadata") or {}
        return (
            state.get("active_source"),
            state.get("source_state"),
            metadata.get("title"),
            metadata.get("artist"),
            metadata.get("is_playing"),
        )

    # =========================================================================
    # DELIVERY
    # =========================================================================

    async def _send_all(self, targets, payload: Dict[str, Any], push_type: str,
                        priority: int = 10) -> bool:
        """Send to each target, purge the ones APNs called dead. True if any landed."""
        results = await asyncio.gather(*(
            self._apns.send(t, payload, push_type, priority=priority) for t in targets
        ))

        delivered = False
        for target, result in zip(targets, results):
            if result.ok:
                delivered = True
            elif result.dead:
                await self._registry.purge(
                    target.token, invalidated_at=result.invalidated_at
                )
        return delivered

    @staticmethod
    def _has_active_source(state: Dict[str, Any]) -> bool:
        """Something is playing when a real source is active.

        Read off the same two fields the UI reads, so "the lock screen shows a
        session" and "the screen shows a player" cannot disagree.
        """
        source = state.get("active_source")
        return bool(source) and str(source) != "none" and state.get("source_state") == "active"
