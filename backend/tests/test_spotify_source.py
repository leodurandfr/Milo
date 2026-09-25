# backend/tests/test_spotify_source.py
"""
Unit tests for SpotifySource (sources/spotify/source.py).

Tests cover:
- BaseAudioSource compliance
- Lifecycle (start, stop)
- Commands, as they reach go-librespot
- /status as the one writer of the session (unreadable reads, retries, /events reconnects)
- Metadata transform
- The multiroom reroute
- The managed config and the journal bridge

The session itself (a phone transferring, pausing, leaving; the daemon dying;
the idle end request) is driven in test_spotify_sessions.py. Every test here
that needs a live session uses the same `SpotifyWorld` (tests/spotify_world.py):
go-librespot's HTTP API and /events as measured, systemd, and a virtual clock.

The start path is driven through its real `_wait_for_playback_ready`,
`_start_websocket` and `_start_log_monitor`: the `go_librespot` helper below
stands in for the three things outside the source — the daemon's HTTP API, its
/events WebSocket and journalctl — and nothing else. Patching those three steps
out instead removed exactly what a start is for, and the suite could not see a
Spotify source that opened no WebSocket at all.
"""
import asyncio
import contextlib
import logging
import time
from types import SimpleNamespace

import aiohttp
import pytest
import yaml
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.sources.spotify.source import SpotifySource
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.core.models.audio_state import AudioSource
from backend.core.models.ws_events import SourceErrorReason, SourceError, SourceErrorCleared
from backend.tests.golden.harness import AsyncioProxy, instant_short_sleep
from backend.tests.spotify_world import (
    ACCOUNT, LE_CHEMIN, PARAPLUIE, SpotifyWorld, track,
)

DELAY = 120   # make_settings' audio.auto_stop_delay, which SpotifyWorld uses


def librespot_api(payload, status=200):
    """Stand in for go-librespot's HTTP API — the outside world this source reads.

    Returns a session whose GET yields `payload` under `status`, so a test can
    say "the daemon answered 500" without touching the source's own methods.
    """
    response = Mock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = Mock()
    session.get = Mock(return_value=context)
    return session


class JournalDouble:
    """journalctl, as `follow_unit` hands it to the source: a stream of lines.

    Records which unit was followed and yields `lines` (none by default), so a
    test can state that the monitor watches go-librespot's own unit without a
    subprocess and without replacing the source's `_start_log_monitor`.
    """

    def __init__(self, lines=()):
        self._lines = list(lines)
        self.units = []

    def __call__(self, unit, **_kwargs):
        self.units.append(unit)
        return self._stream()

    async def _stream(self):
        for line in self._lines:
            yield line


@contextlib.asynccontextmanager
async def go_librespot(source, session, journal=None):
    """Everything `_do_start` reaches outside the source, and nothing else.

    Three boundaries: the daemon's HTTP API (`session`), its /events WebSocket
    (`LibrespotWebSocket`) and journalctl (`follow_unit`). With those three
    stood in for, the readiness poll, the WS wiring and the monitor start run
    for real — patching the source's own `_wait_for_playback_ready` /
    `_start_websocket` / `_start_log_monitor` replaced the steps this asserts
    the order and the arguments of.

    Yields the patched WebSocket class. On exit the log-monitor task the source
    spawned is stopped, so it cannot outlive the test's event loop.
    """
    with patch('aiohttp.ClientSession', return_value=session), \
         patch('backend.sources.spotify.source.LibrespotWebSocket', autospec=True) as ws_cls, \
         patch('backend.sources.spotify.source.follow_unit', journal or JournalDouble()):
        try:
            yield ws_cls
        finally:
            source._stop_log_monitor()


def deaf_daemon_clock(polls=1):
    """A monotonic source that expires the readiness poll's cap after `polls` reads.

    `_do_start` calls `_wait_for_playback_ready()` with its production
    defaults, so a daemon that never answers is 10 s of real polling. Only
    `backend.sources.spotify.source`'s own module-global `time` is replaced —
    never the process-wide module, which the event loop reads.
    """
    values = iter([0.0, *range(1, polls + 1), 999.0])
    last = [0.0]

    def monotonic():
        last[0] = next(values, last[0])
        return last[0]

    return SimpleNamespace(monotonic=monotonic, time=time.time)


def refusing_session():
    """A session whose every GET is refused, the way a daemon not yet listening
    refuses one. `_wait_for_playback_ready` suppresses ClientOSError and polls
    on until its cap."""
    session = MagicMock()
    session.get = Mock(side_effect=aiohttp.ClientOSError("connection refused"))
    return session


@pytest.fixture
def config(tmp_path):
    """Default Spotify source config with temp config file."""
    # Create a temporary config file
    config_file = tmp_path / "config.yml"
    config_file.write_text("""
server:
  address: localhost
  port: 3678
audio_device: milo_spotify
""")
    return {
        "config_path": str(config_file)
    }


@pytest.fixture
def spotify_source(config):
    """Create SpotifySource with mocked components."""
    source = SpotifySource(config)

    # Mock service manager
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)

    return source


@pytest.fixture
def wired(spotify_source):
    """Source wired to a state machine, with its background spawns captured.

    Yields `spawned`, the coroutines the source handed to BackgroundTaskSet
    (closed unrun at teardown); what the source broadcast is read off
    `state_machine.broadcast`.
    """
    spotify_source._api_url = "http://localhost:3678"
    spotify_source.auto_stop_enabled = False  # no stray 10s timer task
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_view = AsyncMock()
    state_machine.system_state = Mock(active_source=AudioSource.SPOTIFY)
    spotify_source.state_machine = state_machine

    spawned = []
    spotify_source._bg = Mock()
    spotify_source._bg.spawn = Mock(side_effect=lambda coro, **kw: spawned.append(coro))

    yield spawned

    for coro in spawned:
        coro.close()


@pytest.fixture
async def world(monkeypatch, tmp_path):
    """Spotify selected on a real state machine, go-librespot up, no phone yet."""
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


def commands_sent(world):
    """The names of the POST /player/* the source issued, in order."""
    return [command for command, _ in world.daemon.posted]


class TestSpotifySourceConfig:
    """Test SpotifySource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = SpotifySource()

        assert source.auto_stop_enabled is True
        assert source.auto_stop_delay == 10.0

    def test_custom_config(self, tmp_path):
        """Test custom configuration."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("""
server:
  address: 192.168.1.100
  port: 5000
""")
        config = {"config_path": str(config_file)}
        source = SpotifySource(config)

        # Config is loaded on start, so just verify the path is set
        assert source._config_path == str(config_file)


class TestSpotifySourceLifecycle:
    """Test SpotifySource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self, monkeypatch, tmp_path):
        """A start over a daemon that answers opens /events and follows the journal.

        The steps `_do_start` ends on are the ones a start exists for: the
        /events socket opened on the config's URL through the daemon's own HTTP
        session, what it says reaching the screen, and the monitor following
        go-librespot's unit.
        """
        world = SpotifyWorld(monkeypatch, tmp_path)
        journal = JournalDouble()
        monkeypatch.setattr("backend.sources.spotify.source.follow_unit", journal)
        opened = []
        connect = world.daemon.ws_connect

        def ws_connect(url, *args, **kwargs):
            opened.append(url)
            return connect(url, *args, **kwargs)

        monkeypatch.setattr(world.daemon, "ws_connect", ws_connect)
        try:
            await world.select()

            assert world.state()["source"] == "spotify"
            assert world.state()["service"] == "running"
            assert opened == ["ws://localhost:3678/events"]
            assert journal.units == ["milo-spotify"]
            await world.phone_plays(PARAPLUIE)
            assert world.playing()
        finally:
            await world.source.shutdown()

    @pytest.mark.asyncio
    async def test_start_no_config_file(self):
        """Test start fails if config file doesn't exist."""
        source = SpotifySource({"config_path": "/nonexistent/path"})
        source._service_manager = Mock()
        source._service_manager.start = AsyncMock(return_value=True)

        result = await source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, world):
        """Stop posts /player/stop (graceful) before the service stop."""
        await world.phone_plays(PARAPLUIE)
        stops_at_unit_stop = []
        unit_stop = world.systemd.stop.side_effect

        async def stop(*args):
            stops_at_unit_stop.append(world.stops_sent())
            return await unit_stop(*args)

        world.systemd.stop.side_effect = stop

        await world.leave()

        assert stops_at_unit_stop == [1]
        world.systemd.stop.assert_awaited_once_with("milo-spotify.service")
        assert world.session() is None
        assert world.state()["source"] == "none"

    @pytest.mark.asyncio
    async def test_wait_for_playback_ready_returns_on_200(self, spotify_source, caplog):
        """Readiness poll returns as soon as GET / answers 200, regardless of
        the playback_ready flag (false in zeroconf with no session at start)."""
        session = librespot_api({"playback_ready": False})

        async with go_librespot(spotify_source, session) as ws_cls:
            with caplog.at_level(logging.WARNING):
                assert await spotify_source.start() is True

        assert [call.args[0] for call in session.get.call_args_list] == ["http://localhost:3678/"]
        assert "not reachable" not in caplog.text
        ws_cls.return_value.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wait_for_playback_ready_gives_up_after_the_cap(
        self, spotify_source, caplog, monkeypatch
    ):
        """A daemon that refuses every connection is polled again until the
        cap, which ends the poll with a warning — it must not wedge the start."""
        session = refusing_session()
        monkeypatch.setattr(
            "backend.sources.spotify.source.asyncio", AsyncioProxy(instant_short_sleep)
        )

        async with go_librespot(spotify_source, session):
            with patch('backend.sources.spotify.source.time', deaf_daemon_clock(polls=3)), \
                    caplog.at_level(logging.WARNING):
                result = await spotify_source.start()

        assert result is True
        assert session.get.call_count == 3
        assert "not reachable" in caplog.text

    @pytest.mark.asyncio
    async def test_a_daemon_that_never_answers_is_reported_at_error(
        self, spotify_source, caplog
    ):
        """The poll only warns, and `_do_start` dropped its verdict — so the
        source went on to report itself up over a daemon that never answered,
        with nothing above warning to say why the first phone finds nothing.

        Not fatal: the WS loop reconnects on its own, so the start still succeeds.
        """
        session = refusing_session()

        async with go_librespot(spotify_source, session) as ws_cls:
            with patch('backend.sources.spotify.source.time', deaf_daemon_clock()), \
                    caplog.at_level(logging.ERROR):
                result = await spotify_source.start()

        assert result is True
        # The poll really ran against the daemon — the clock only ends its cap.
        assert session.get.call_count == 1
        assert "never answered" in caplog.text
        # Still not fatal: the WS was started anyway, which is what recovers.
        ws_cls.return_value.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_daemon_that_answers_says_nothing(self, spotify_source, caplog):
        session = librespot_api({"playback_ready": True})

        async with go_librespot(spotify_source, session):
            with caplog.at_level(logging.ERROR):
                await spotify_source.start()

        assert caplog.text == ""


class TestSpotifySourceCommands:
    """Test SpotifySource command handling: what reaches go-librespot."""

    @pytest.mark.asyncio
    async def test_playpause_command(self, world):
        """The hardware toggle passes straight through to go-librespot, which
        resolves the edge."""
        await world.phone_plays(PARAPLUIE)

        result = await world.command("playpause")

        assert result["success"] is True
        assert world.daemon.posted == [("playpause", {})]
        assert world.active() and not world.playing()

    @pytest.mark.asyncio
    async def test_seek_command(self, world):
        """Test seek command."""
        await world.phone_plays(PARAPLUIE)

        result = await world.command("seek", {"position_ms": 30000})

        assert result["success"] is True
        assert world.daemon.posted == [("seek", {"position": 30000})]

    @pytest.mark.asyncio
    async def test_a_seek_past_the_end_of_the_track_is_refused(self, world):
        """The bound is the duration go-librespot reported for the track on
        screen; a position past it never reaches the daemon."""
        await world.phone_plays(PARAPLUIE)

        result = await world.command("seek", {"position_ms": PARAPLUIE["duration"] + 1})

        assert result["success"] is False
        assert world.daemon.posted == []

    @pytest.mark.asyncio
    async def test_a_command_with_no_session_never_reaches_the_daemon(self, world):
        """Nothing plays until a phone picks the speaker: a transport press has
        nothing to act on, and is refused before go-librespot is asked."""
        result = await world.command("playpause")

        assert result["success"] is False
        assert world.daemon.posted == []


class TestNextPrevCommands:
    """`next` / `prev`, the one command arm the suite never entered.

    NextPrevParams carries an optional target URI, and the two payload shapes it
    produces sat at 0% of lines. Sending `{"uri": null}` instead of `{}` is the
    failure this pins: go-librespot reads the key, so a null target is not the
    same request as no target.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["next", "prev"])
    async def test_a_bare_skip_carries_no_target(self, world, cmd):
        """No URI given, no URI sent — not a null one."""
        await world.phone_plays(PARAPLUIE)

        result = await world.command(cmd)

        assert result["success"] is True
        assert world.daemon.posted == [(cmd, {})]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["next", "prev"])
    async def test_a_targeted_skip_carries_the_uri(self, world, cmd):
        """The queue view jumps to a track by URI through this same command."""
        await world.phone_plays(PARAPLUIE)
        uri = "spotify:track:0eGsygTp906u18L0Oimnem"

        await world.command(cmd, {"uri": uri})

        assert world.daemon.posted == [(cmd, {"uri": uri})]

    @pytest.mark.asyncio
    async def test_an_unknown_command_is_refused_rather_than_forwarded(self, spotify_source):
        """COMMANDS gates dispatch, so this arm is only reachable by a new
        entry someone forgot to serve — it must not reach the daemon."""
        session = mock_librespot_api(spotify_source)

        result = await spotify_source._handle_command("shuffle", None)

        assert result["success"] is False
        assert posted_commands(session) == []


class TestProducerTruth:
    """ACTIVE must mean "there is a track", and only /status says there is.

    Every /events burst sends the source to read GET /status, and that read is
    the one writer of the session. When the answer never arrives, publishing
    anyway announces a change nobody can describe; a status with no title is a
    session with nothing to draw, and the status card, having nothing to
    render, would draw its idle line over playing audio. These pin that it
    doesn't.
    """

    @pytest.mark.asyncio
    async def test_unreadable_status_publishes_nothing(self, world):
        """A daemon answering 503 teaches us nothing: keep the last published
        state rather than announcing a change we cannot describe."""
        await world.phone_plays(PARAPLUIE)
        before = world.published()
        world.daemon.status_answers = False
        world.daemon.track = dict(LE_CHEMIN)

        await world._says({"type": "metadata", "uri": LE_CHEMIN["uri"]})

        assert world.published() == before
        assert world.playing()
        assert world.session()["title"] == "Parapluie"

    @pytest.mark.asyncio
    async def test_unreadable_status_retries(self, world):
        """The daemon emits an event only on change, so a status we failed to
        read is never re-announced — the source must go back for it itself."""
        d = world.daemon
        d.session, d.account, d.track, d.paused, d.buffering = True, ACCOUNT, dict(PARAPLUIE), False, False
        d.status_answers = False
        await world._says({"type": "active"}, {"type": "metadata", "uri": PARAPLUIE["uri"]},
                          {"type": "playing"})
        assert not world.active()

        # The daemon is answering again by the time the retry fires.
        d.status_answers = True
        await world.advance(SpotifySource.STATUS_RETRY_DELAY + 0.1)

        assert world.playing()
        assert world.session()["title"] == "Parapluie"

    @pytest.mark.asyncio
    async def test_session_without_a_track_title_publishes_ready(self, world):
        """A readable status whose track carries no name is a session with
        nothing to draw — no session is published, not a titleless one."""
        d = world.daemon
        d.session, d.account, d.paused, d.buffering = True, ACCOUNT, False, False
        d.track = {"uri": "spotify:track:untitled", "artist_names": ["Telepopmusik"],
                   "duration": 275000, "position": 0}

        await world._says({"type": "active"}, {"type": "playing"})

        assert world.session() is None


class TestEventsReconnect:
    """/events coming back after a drop the source did not ask for.

    go-librespot emits events only on change, so what happened in the gap is
    learned from GET /status, read on every (re)connection.
    """

    @pytest.mark.asyncio
    async def test_a_session_that_ended_in_the_gap_resets_to_ready(self, world):
        """The phone left while /events was down: the reconnect reads 204, and
        the stale "now playing" goes — and with it the pause's end request, so
        it cannot fire /player/stop on whatever session the phone opens next."""
        await world.phone_plays(PARAPLUIE)
        await world.phone_pauses()
        d = world.daemon
        d.session, d.track = False, None      # its `inactive` went unheard

        await world.events_blip()

        assert world.session() is None
        await world.advance(DELAY + 1)
        assert world.stops_sent() == 0

    @pytest.mark.asyncio
    async def test_a_live_session_is_healed_on_reconnect(self, world):
        """A live session is kept, and what the gap hid (here, a skip) is read
        back."""
        await world.phone_plays(PARAPLUIE)
        world.daemon.track = dict(LE_CHEMIN)  # skipped while nobody listened

        await world.events_blip()

        assert world.playing()
        assert world.session()["title"] == "Le Chemin"


class TestAutoStop:
    """Test auto-stop timer functionality.

    The pause that outlives the delay, and the resume that withdraws it, are
    driven in test_spotify_sessions.py (the control for the test below).
    """

    @pytest.mark.asyncio
    async def test_no_auto_stop_when_the_delay_is_zero(self, monkeypatch, tmp_path):
        """0 in Settings means off: a paused session stays on the phone's
        speaker list as the selected output, however long it waits."""
        world = SpotifyWorld(monkeypatch, tmp_path, settings={"audio.auto_stop_delay": 0})
        try:
            await world.select()
            await world.phone_plays(PARAPLUIE)
            await world.phone_pauses()

            await world.advance(3600)

            assert world.stops_sent() == 0
            assert world.active()
        finally:
            await world.source.shutdown()

    @pytest.mark.asyncio
    async def test_reload_auto_stop_config_disabled(self, spotify_source):
        """Reloading with delay=0 disables auto-stop."""
        spotify_source._settings_service = Mock()
        spotify_source._settings_service.get_setting = AsyncMock(return_value=0)
        result = await spotify_source.reload_auto_stop_config()

        assert result is True
        assert spotify_source.auto_stop_enabled is False

    @pytest.mark.asyncio
    async def test_reload_auto_stop_config_enabled(self, spotify_source):
        """Reloading with positive delay enables auto-stop."""
        spotify_source._settings_service = Mock()
        spotify_source._settings_service.get_setting = AsyncMock(return_value=30.0)
        result = await spotify_source.reload_auto_stop_config()

        assert result is True
        assert spotify_source.auto_stop_enabled is True
        assert spotify_source.auto_stop_delay == 30.0


class TestLibrespotWebSocket:
    """Test LibrespotWebSocket component."""

    def test_initial_state(self):
        """Test initial WebSocket state."""
        mock_session = Mock()
        ws = LibrespotWebSocket(
            ws_url="ws://localhost:3678/events",
            session=mock_session,
            on_event=AsyncMock()
        )

        assert ws.connected is False

    @pytest.mark.asyncio
    async def test_stop_not_started(self):
        """Test stopping when not started."""
        mock_session = Mock()
        ws = LibrespotWebSocket(
            ws_url="ws://localhost:3678/events",
            session=mock_session,
            on_event=AsyncMock()
        )

        await ws.stop()  # Should not raise

        assert ws.connected is False


class TestMetadataTransform:
    """Test metadata transformation."""

    @pytest.mark.asyncio
    async def test_transform_track_metadata(self, world):
        """go-librespot's track, as a state request (GET /api/audio/state)
        hands it to the screen."""
        song = track("Test Song", 180000, artists=("Artist 1", "Artist 2"), album="Test Album")
        await world.phone_plays(song)

        session = (await world.get_state())["session"]

        assert session["title"] == "Test Song"
        assert session["artist"] == "Artist 1, Artist 2"
        assert session["album"] == "Test Album"
        assert session["artwork"] == song["album_cover_url"]
        assert session["duration_ms"] == 180000
        assert session["phase"] == "playing"
        # The account is an identity, never a name to show.
        assert session["senders"] == []


def mock_librespot_api(source, *, paused=True, post_status=200):
    """Stand in for go-librespot's HTTP API — the outside world of this source.

    A small stateful fake rather than a fixed answer: /status reports what the
    POSTs did to it, so the source has to actually pause the daemon to observe a
    paused daemon. `post_status` != 200 simulates a daemon that cannot be driven.
    """
    state = {"paused": paused}

    source._http = MagicMock()
    source._http.close = AsyncMock()  # awaited by _cleanup on the fallback path
    source._api_url = "http://localhost:3678"

    async def status():
        return {
            "paused": state["paused"],
            "track": {
                "name": "Track", "artist_names": ["Artist"], "album_name": "Album",
                "album_cover_url": None, "duration": 200000, "position": 76611,
            },
        }

    get_response = MagicMock()
    get_response.status = 200
    get_response.json = AsyncMock(side_effect=status)
    get_cm = AsyncMock()
    get_cm.__aenter__.return_value = get_response
    source._http.get.return_value = get_cm

    post_response = MagicMock()
    post_response.status = post_status
    post_cm = AsyncMock()
    post_cm.__aenter__.return_value = post_response

    def post(url, json=None):
        command = url.rsplit("/player/", 1)[-1]
        if post_status == 200 and command in ("pause", "resume"):
            state["paused"] = command == "pause"
        return post_cm

    source._http.post = MagicMock(side_effect=post)

    return source._http


def posted_commands(session):
    """(command, payload) for every POST /player/* the source issued."""
    return [
        (call.args[0].rsplit("/player/", 1)[-1], call.kwargs.get("json"))
        for call in session.post.call_args_list
    ]


class TestMultiroomReroute:
    """Keeping the Connect session across a multiroom toggle.

    AudioStateMachine.reroute_active_source releases the source, reconciles
    snapcast, then re-acquires it. Before go-librespot 0.8.0 that meant a full
    daemon bounce: the phone lost the speaker and playback stopped. These hooks
    park the output instead — and the order they do it in is load-bearing, so
    each rule below pins one thing measured on the unit rather than reasoned.
    """

    @pytest.mark.asyncio
    async def test_release_pauses_before_parking_the_output(self, world, monkeypatch):
        """Pause must land BEFORE the switch, and the service must stay up.

        RELEASE_DEVICE does not rate-limit: switching to it while playing runs
        the track to its end in seconds (measured on the unit). Pausing after
        the switch would be too late.
        """
        monkeypatch.setenv("MILO_MODE", "direct")
        await world.phone_plays(PARAPLUIE)

        await world.reroute()

        assert world.daemon.posted == [
            ("pause", {}),
            ("output", {"device": "null"}),
            ("output", {"device": "milo_spotify_direct"}),
            ("resume", {}),
        ]
        world.systemd.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_of_a_paused_session_skips_the_pause(self, world):
        """Nothing to pause: the output is parked straight away."""
        await world.phone_plays(PARAPLUIE)
        await world.phone_pauses()

        await world.reroute()

        assert world.daemon.posted[0] == ("output", {"device": "null"})
        assert "pause" not in commands_sent(world)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["direct", "multiroom"])
    async def test_acquire_reopens_on_the_device_of_the_new_mode(
        self, world, monkeypatch, mode
    ):
        """The explicit device is what makes the reroute work without a restart.

        The `milo_spotify` alias resolves MILO_MODE from the daemon's own
        environment, frozen at its start — it would still name the old mode.
        """
        monkeypatch.setenv("MILO_MODE", mode)
        await world.phone_plays(PARAPLUIE)
        pid = world.pid

        await world.reroute()

        assert world.daemon.output == f"milo_spotify_{mode}"
        assert world.pid == pid
        assert world.restarts == []
        assert world.playing()

    @pytest.mark.asyncio
    async def test_acquire_resumes_only_what_was_playing(self, world):
        """A session paused by the user must not come back playing."""
        await world.phone_plays(PARAPLUIE)
        await world.phone_pauses()

        await world.reroute()

        assert "resume" not in commands_sent(world)
        assert world.active() and not world.playing()

    @pytest.mark.asyncio
    async def test_unreachable_daemon_falls_back_to_a_full_stop(self, world):
        """A source still holding the loopback would block snapclient.

        So a daemon that cannot be read must lose its session rather than keep
        the device: the fallback is the full stop, not a silent no-op — and the
        re-acquire then starts a fresh daemon, there being no session left to
        reopen an output on.
        """
        await world.phone_plays(PARAPLUIE)
        world.daemon.status_answers = False
        pid = world.pid

        await world.reroute()

        world.systemd.stop.assert_awaited_once_with("milo-spotify.service")
        assert ("output", {"device": "null"}) not in world.daemon.posted
        assert world.pid not in (None, pid)
        assert not world.active()


class TestManagedConfig:
    """The go-librespot config keys Milō owns.

    go-librespot parses config.yml once, at process start, so a crossfade the
    settings page stored only ever reaches the daemon through this write. If it
    silently dropped the key — or clobbered one of the baked ones, like
    zeroconf_backend — Spotify would come back up misconfigured with nothing in
    the logs to say so.
    """

    @staticmethod
    def _read(config_path):
        return yaml.safe_load(Path(config_path).read_text())

    @pytest.mark.asyncio
    async def test_writes_the_managed_key_and_leaves_the_rest_alone(self, spotify_source):
        """Crossfade lands in the file; the baked keys survive."""
        await spotify_source._apply_managed_config()

        written = self._read(spotify_source._config_path)
        assert written["crossfade_duration"] == 0
        # Written by provisioning/go-librespot.sh — this function must not own them.
        assert written["audio_device"] == "milo_spotify"
        assert written["server"]["port"] == 3678

    @pytest.mark.asyncio
    async def test_never_writes_flac_enabled(self, spotify_source):
        """Turning FLAC on costs Spotify entirely, so it must stay out.

        The released go-librespot binaries exit at boot with "FLAC playback
        requires a PlapPlay implementation" (measured on the unit 2026-08-03) —
        the daemon never comes up, so this is not a quality trade-off to revisit
        casually.
        """
        await spotify_source._apply_managed_config()

        assert "flac_enabled" not in self._read(spotify_source._config_path)

    @pytest.mark.asyncio
    async def test_crossfade_comes_from_settings(self, spotify_source):
        """The stored setting is what reaches the daemon's config."""
        spotify_source._settings_service = Mock()
        spotify_source._settings_service.get_setting = AsyncMock(return_value=6000)

        await spotify_source._apply_managed_config()

        assert self._read(spotify_source._config_path)["crossfade_duration"] == 6000

    @pytest.mark.asyncio
    @pytest.mark.parametrize("allowed, external", [(False, True), (True, False)])
    async def test_app_volume_decides_external_volume(self, spotify_source, allowed, external):
        """external_volume is the inverse of the setting. Inverted, the Spotify
        app's slider would scale the samples on a unit set to leave CamillaDSP
        the only volume authority — a quiet speaker nobody can explain from
        Milō's own volume display."""
        stored = {"spotify.crossfade_duration": 0, "spotify.allow_app_volume": allowed}
        spotify_source._settings_service = Mock()
        spotify_source._settings_service.get_setting = AsyncMock(side_effect=stored.get)

        await spotify_source._apply_managed_config()

        assert self._read(spotify_source._config_path)["external_volume"] is external

    @pytest.mark.asyncio
    async def test_is_idempotent(self, spotify_source):
        """Re-applying an unchanged config yields the same file, byte for byte."""
        await spotify_source._apply_managed_config()
        first = Path(spotify_source._config_path).read_text()

        await spotify_source._apply_managed_config()

        assert Path(spotify_source._config_path).read_text() == first

    @pytest.mark.asyncio
    async def test_missing_config_file_does_not_raise(self):
        """Fails open: no config to patch must not block Spotify from starting."""
        source = SpotifySource({"config_path": "/nonexistent/config.yml"})

        await source._apply_managed_config()  # must not raise

    @pytest.mark.asyncio
    async def test_settings_change_restarts_only_when_asked(self, spotify_source):
        """`apply_now` is the whole difference between the two write paths.

        Without it a settings write must never bounce the daemon — that would
        drop a live Connect session from a screen the user thinks is passive.
        """
        assert await spotify_source.on_spotify_settings_changed(apply_now=False) is True
        spotify_source._service_manager.restart.assert_not_called()

        assert await spotify_source.on_spotify_settings_changed(apply_now=True) is True
        spotify_source._service_manager.restart.assert_called_once_with("milo-spotify.service")

    @pytest.mark.asyncio
    async def test_apply_now_never_starts_a_stopped_daemon(self, spotify_source):
        """`systemctl restart` on an inactive unit STARTS it.

        go-librespot is only running while Spotify is the active source, so an
        apply_now reaching a stopped unit would raise a Connect speaker named
        after this house while another source plays — and outside the state
        machine, which never ran _do_start, so nothing monitors or stops it.
        The same shape did exactly that to roc-recv through the Mac panel.

        Leaving the unit alone is not a dropped setting: config.yml is written
        unconditionally (pinned by the sibling test above) and parsed at every
        start, so the value is live the moment Spotify is next selected.
        """
        spotify_source._service_manager.is_active = AsyncMock(return_value=False)

        assert await spotify_source.on_spotify_settings_changed(apply_now=True) is True

        spotify_source._service_manager.restart.assert_not_called()
        spotify_source._service_manager.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_settings_change_always_reaches_config_yml(self, spotify_source):
        """The write is unconditional; only the restart is not.

        Measured 2026-08-24: deleting `_apply_managed_config()` from
        on_spotify_settings_changed left the whole suite green. The method's own
        test asserts both restart branches, so eviscerating the method was red
        and the missing write hid behind that red — the class of gap only a
        statement-level mutation reaches.

        What it costs when it breaks: the settings page reports success, the
        daemon is not restarted (apply_now=False), and the value is gone at the
        next boot too, because config.yml is the only place it was going to live.
        """
        spotify_source._settings_service = Mock()
        spotify_source._settings_service.get_setting = AsyncMock(return_value=6000)

        assert await spotify_source.on_spotify_settings_changed(apply_now=False) is True

        assert self._read(spotify_source._config_path)["crossfade_duration"] == 6000


class TestTheEventThatIsNotRead:
    """go-librespot's event vocabulary, as the source reads it.

    Every burst of /events is a reason to read GET /status, whatever its
    names, so a name go-librespot renames can no longer reach nothing and fail
    in silence. One name is taken at its word: `inactive` is the daemon saying
    the session is over, and /status may fail right after it.
    """

    @pytest.mark.asyncio
    async def test_inactive_ends_the_session_even_when_status_cannot_be_read(self, world):
        """Read instead, a failed /status keeps the session — and a phone that
        picked another output would leave its track on screen."""
        await world.phone_plays(PARAPLUIE)
        world.daemon.status_answers = False

        await world._says({"type": "inactive"}, {"type": "stopped"})

        assert world.session() is None


class TestLogBridge:
    """go-librespot's journal, read as the Spotify error banner.

    This is the only thing that puts a Spotify failure on screen, and it works
    by matching literal strings out of another project's log output — the most
    perishable coupling in the source, and it was at 0% of lines. `JournalDouble`
    above already takes the lines to feed it; nothing ever passed any.

    The banner is deliberately slow to appear on connection failures: zeroconf
    crashes and restarts every ~5-15 s, so a single failure means nothing and
    three inside a minute mean an outage.
    """

    @staticmethod
    def _broadcast(source):
        """The WsEvent the source last handed to the state machine, if any."""
        broadcast = source.state_machine.broadcast
        return broadcast.call_args.args[0] if broadcast.call_args else None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("line", [
        'level=info msg="authenticated AP with stored credentials"',
        'level=info msg="authenticated Login5 with stored credentials"',
        'level=info msg="loaded track ..."',
    ])
    async def test_a_success_line_dismisses_a_standing_banner(
        self, spotify_source, wired, line
    ):
        """Every line go-librespot logs on success clears the error it fixed."""
        spotify_source.broadcast_error(SourceErrorReason.SERVICE_UNREACHABLE)
        assert spotify_source._error_active is True

        await spotify_source._handle_log_line(line)

        assert spotify_source._error_active is False
        assert isinstance(self._broadcast(spotify_source), SourceErrorCleared)

    @pytest.mark.asyncio
    async def test_authentication_also_forgives_the_failures_that_preceded_it(
        self, spotify_source, wired
    ):
        """A daemon that authenticates starts its next outage from zero.

        Without the reset, two failures from an outage an hour ago would leave
        the banner one line away on the next hiccup.
        """
        spotify_source._connection_error_count = 2

        await spotify_source._handle_log_line('level=info msg="authenticated AP"')

        assert spotify_source._connection_error_count == 0

    @pytest.mark.asyncio
    async def test_a_track_that_will_not_load_is_shown_at_once(
        self, spotify_source, wired
    ):
        """No throttle here: the user pressed play and nothing happened."""
        await spotify_source._handle_log_line(
            'level=error msg="failed loading current track" error="context has no tracks"'
        )

        event = self._broadcast(spotify_source)
        assert isinstance(event, SourceError)
        assert event.reason == SourceErrorReason.TRACK_LOAD_FAILED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("line", [
        'level=warning msg="failed connecting to accesspoint" error="dial tcp: timeout"',
        'level=warning msg="failed running zeroconf" error="listen udp :5353: in use"',
    ])
    async def test_a_connection_failure_is_shown_only_on_the_third_inside_a_minute(
        self, spotify_source, wired, line
    ):
        """Two failures are a restart; three inside 60 s are an outage."""
        await spotify_source._handle_log_line(line)
        await spotify_source._handle_log_line(line)

        assert spotify_source._error_active is False
        assert self._broadcast(spotify_source) is None

        await spotify_source._handle_log_line(line)

        assert isinstance(self._broadcast(spotify_source), SourceError)
        # Reset, so the next outage needs its own three rather than riding these.
        assert spotify_source._connection_error_count == 0

    @pytest.mark.asyncio
    async def test_failures_more_than_a_minute_apart_never_accumulate(
        self, spotify_source, wired, monkeypatch
    ):
        """The window is what makes three mean an outage rather than an uptime.

        Only this module's `time` is replaced, never the process-wide one the
        event loop reads.
        """
        clock = iter([0.0, 3600.0, 7200.0])
        monkeypatch.setattr(
            "backend.sources.spotify.source.time",
            SimpleNamespace(time=lambda: next(clock), monotonic=time.monotonic),
        )
        line = 'level=warning msg="failed connecting to accesspoint" error="timeout"'

        for _ in range(3):
            await spotify_source._handle_log_line(line)

        assert spotify_source._connection_error_count == 1
        assert self._broadcast(spotify_source) is None

    @pytest.mark.asyncio
    async def test_an_unremarkable_line_is_left_alone(self, spotify_source, wired):
        """The journal is mostly noise; only the five patterns above may fire."""
        await spotify_source._handle_log_line(
            'level=debug msg="websocket closed" error="StatusNormalClosure"'
        )

        assert self._broadcast(spotify_source) is None
        assert spotify_source._connection_error_count == 0

    @pytest.mark.parametrize("line, expected", [
        ('msg="failed loading current track" error="no tracks"',
         "failed loading current track: no tracks"),
        ('msg="failed loading current track"', "failed loading current track"),
        ('level=error something entirely different', "Unknown error"),
    ])
    def test_the_banner_text_is_the_daemon_own_words(self, spotify_source, line, expected):
        """What reaches the screen is go-librespot's message, not a paraphrase.

        The last case is the one that matters: a log format that stops matching
        must still produce a banner, not an empty one.
        """
        assert spotify_source._extract_log_message(line) == expected


class TestLibrespotWebSocketTeardown:
    """`LibrespotWebSocket.stop` — letting go of the go-librespot socket.

    Green in the Lot A eviscration sweep. `stop` is reached when the Spotify
    source stops or the unit switches source, and its job is to cancel the
    reconnection loop. Neutralised it cancels nothing: the loop keeps trying to
    reach a daemon that has been stopped, and the next start adds a second one
    beside it.

    A lifecycle method is never "entered" by a test that exercises behaviour,
    which is exactly why nothing held it.
    """

    @pytest.fixture
    def ws(self):
        return LibrespotWebSocket(
            ws_url="ws://127.0.0.1:3678/events",
            session=Mock(),
            on_event=AsyncMock(),
        )

    async def test_stop_cancels_the_connection_loop_and_drops_it(self, ws):
        started = asyncio.Event()

        async def never_ending():
            started.set()
            await asyncio.sleep(3600)

        ws._task = asyncio.create_task(never_ending())
        await started.wait()

        await ws.stop()

        assert ws._task is None
        assert ws._connected is False

    async def test_stop_marks_the_client_stopping_so_the_loop_does_not_reconnect(self, ws):
        await ws.stop()
        assert ws._stopping is True

    async def test_stopping_twice_is_harmless(self, ws):
        """`_do_stop` runs on a source that never started, and on teardown."""
        await ws.stop()
        await ws.stop()
        assert ws._task is None