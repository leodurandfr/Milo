# backend/tests/test_qobuz_source.py
"""
Unit tests for the Qobuz Connect source.

Covers the one thing QobuzSource decides on its own: how a qobuz-proxy speaker
snapshot becomes a published state. Everything else about this source is the
proxy's job — Family B has no commands, no local control channel and no
artwork route.

The snapshots below stand in for the outside world (qobuz-proxy's GET
/api/status, parsed by QobuzMonitor and handed to _on_status). What is asserted
is what the source pushed to the state machine in response.
"""
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.qobuz.source import (
    QobuzSource,
    _IDLE_GRACE_TICKS,
    _TRACKLESS_GRACE_TICKS,
)


NOW_PLAYING = {
    "title": "Nightcall",
    "artist": "Kavinsky",
    "album": "OutRun",
    "album_art_url": "https://static.qobuz.com/cover.jpg",
    "position_ms": 12000,
    "duration_ms": 258000,
}


@pytest.fixture
def qobuz():
    """A Qobuz source wired to a state machine that records what it publishes."""
    source = QobuzSource()
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_state = AsyncMock()
    state_machine.system_state = Mock(active_source=AudioSource.QOBUZ)
    source.state_machine = state_machine
    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    return source, state_machine.update_source_state


def published_state(publish):
    """The (state, metadata) of the last push to the state machine."""
    source, state, metadata = publish.call_args.args
    return state, metadata


class TestStatusToState:
    """qobuz-proxy snapshot → published state."""

    @pytest.mark.asyncio
    async def test_a_reported_track_is_published_active(self, qobuz):
        source, publish = qobuz

        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)

        state, metadata = published_state(publish)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == NOW_PLAYING["title"]
        assert metadata["duration"] == NOW_PLAYING["duration_ms"]

    @pytest.mark.asyncio
    async def test_a_track_change_without_a_length_yet_keeps_the_bar(self, qobuz):
        """The playhead is carried across the track rebuild, not dropped with it.

        The sidecar types its duration `int = 0` and milo-qobuz injects the
        position/duration pair whenever there is a now_playing at all, so the
        keys are there from a track's first tick and carry 0 until its length
        resolves. Published, that zero does not correct the bar: ProgressBar
        renders under `duration > 0` and carries a mount animation, so it
        removes it and replays its entrance on the way back — the defect
        6d4df23d fixed on DLNA, arriving here by a different road.
        """
        source, publish = qobuz
        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)

        await source._on_status({"status": "playing", "now_playing": {
            **NOW_PLAYING, "title": "Odd Look", "position_ms": 0, "duration_ms": 0,
        }}, True)

        _, metadata = published_state(publish)
        assert metadata["title"] == "Odd Look"
        assert metadata["duration"] == NOW_PLAYING["duration_ms"]

    @pytest.mark.asyncio
    async def test_only_the_length_is_carried_across_a_track_change(self, qobuz):
        """The *length* is what a rebuild may inherit, never the playhead.

        The length resolves a beat after the track and a bar with none is
        removed rather than corrected; the playhead has no such excuse — the
        sidecar reports it from the track's first tick. Carried, it drew 3:45 of
        3:50 on a song that had just started, and interpolated past its own end.
        """
        source, publish = qobuz
        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)

        await source._on_status({"status": "playing", "now_playing": {
            **NOW_PLAYING, "title": "Odd Look", "position_ms": 0, "duration_ms": 0,
        }}, True)

        _, metadata = published_state(publish)
        assert metadata["duration"] == NOW_PLAYING["duration_ms"], "the bar was removed"
        assert metadata["position"] == 0, "the previous track's playhead was republished"

    @pytest.mark.asyncio
    async def test_the_length_lands_as_soon_as_the_sidecar_has_it(self, qobuz):
        """And the carry is a bridge, not a memory: the pair is overwritten the
        moment a real one arrives, which on an ordinary track change is the very
        tick that announced the track."""
        source, publish = qobuz
        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)

        await source._on_status({"status": "playing", "now_playing": {
            **NOW_PLAYING, "title": "Odd Look", "position_ms": 0, "duration_ms": 0,
        }}, True)
        await source._on_status({"status": "playing", "now_playing": {
            **NOW_PLAYING, "title": "Odd Look", "position_ms": 300, "duration_ms": 224000,
        }}, True)

        _, metadata = published_state(publish)
        assert metadata["duration"] == 224000
        assert metadata["position"] == 300

    @pytest.mark.asyncio
    async def test_a_session_without_a_track_is_held(self, qobuz):
        """qobuz-proxy reports 'playing' before it has a track to report. The
        card can only draw that as its idle line — over audio that is starting
        — so the source holds rather than publishing a trackless ACTIVE."""
        source, publish = qobuz

        await source._on_status({"status": "playing", "now_playing": {}}, True)

        publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_hold_ends_after_the_grace_window(self, qobuz):
        """Bounded, not indefinite: a proxy that never delivers a track must not
        wedge the source in READY while it plays."""
        source, publish = qobuz
        trackless = {"status": "playing", "now_playing": {}}

        for _ in range(_TRACKLESS_GRACE_TICKS):
            await source._on_status(trackless, True)
        assert publish.call_count == 0

        await source._on_status(trackless, True)

        assert published_state(publish)[0] == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_the_between_tracks_blip_keeps_the_current_track(self, qobuz):
        """An empty now_playing mid-session is a track change, not a stop: the
        previous track stays on screen instead of blanking to the fallback."""
        source, publish = qobuz
        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)

        await source._on_status({"status": "playing", "now_playing": {}}, True)

        state, metadata = published_state(publish)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == NOW_PLAYING["title"]

    @pytest.mark.asyncio
    async def test_a_new_session_is_held_again_after_a_stop(self, qobuz):
        """The window is per-session: a stop must re-arm it, or the first
        trackless tick of the next session commits immediately."""
        source, publish = qobuz
        trackless = {"status": "playing", "now_playing": {}}
        for _ in range(_TRACKLESS_GRACE_TICKS + 1):
            await source._on_status(trackless, True)

        # A real stop persists past the idle grace window.
        for _ in range(10):
            await source._on_status({"status": "idle"}, True)
        assert published_state(publish)[0] == SourceState.READY
        publish.reset_mock()

        await source._on_status(trackless, True)

        publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_settled_idle_stops_republishing(self, qobuz):
        """The ~1 Hz poll is the progress feed of a *playing* session. Once READY
        is out, the next tick has nothing to add — and this source stays selected
        for hours, so each one was a full_state to every connected client."""
        source, publish = qobuz
        await source._on_status({"status": "playing", "now_playing": NOW_PLAYING}, True)
        for _ in range(_IDLE_GRACE_TICKS + 1):
            await source._on_status({"status": "idle"}, True)
        assert published_state(publish)[0] == SourceState.READY
        publish.reset_mock()

        for _ in range(5):
            await source._on_status({"status": "idle"}, True)

        publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_logout_while_idle_is_still_published(self, qobuz):
        """The login state is the one thing that moves while idle, and the idle
        card's 'connect account' CTA is drawn from it."""
        source, publish = qobuz
        for _ in range(_IDLE_GRACE_TICKS + 1):
            await source._on_status({"status": "idle"}, True)
        publish.reset_mock()

        await source._on_status({"status": "idle"}, False)

        state, metadata = published_state(publish)
        assert state == SourceState.READY
        assert metadata["account_authenticated"] is False
