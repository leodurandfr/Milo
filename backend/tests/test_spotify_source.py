# backend/tests/test_spotify_source.py
"""
Unit tests for SpotifySource (features/spotify/source.py).

Tests cover:
- BaseAudioSource compliance
- Lifecycle (start, stop, restart)
- WebSocket event handling
- Metadata refresh
- Command handling
- Auto-stop timer

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
from backend.sources.spotify.models import NextPrevParams
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.models.ws_events import SourceError, SourceErrorCleared


# A go-librespot GET /status body for a live session, shaped like the daemon's.
TRACK_STATUS = {
    "track": {
        "name": "Breathe",
        "artist_names": ["Telepopmusik"],
        "album_name": "Genetic World",
        "album_cover_url": "https://i.scdn.co/image/cover",
        "duration": 275000,
        "position": 42000,
    },
    "paused": False,
}


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


def deaf_daemon_clock():
    """A monotonic source that expires the readiness poll's cap on the 2nd read.

    `_do_start` calls `_wait_for_playback_ready()` with its production
    defaults, so a daemon that never answers is 10 s of real polling. Only
    `backend.sources.spotify.source`'s own module-global `time` is replaced —
    never the process-wide module, which the event loop reads.
    """
    values = iter([0.0, 1.0, 999.0])
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

    Yields (publish, spawned): `publish` records every state the source pushes
    to the machine, `spawned` holds the coroutines it handed to
    BackgroundTaskSet, so a test can run the deferred status retry on demand.
    """
    spotify_source._api_url = "http://localhost:3678"
    spotify_source.auto_stop_enabled = False  # no stray 10s timer task
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_state = AsyncMock()
    state_machine.system_state = Mock(active_source=AudioSource.SPOTIFY)
    spotify_source.state_machine = state_machine

    spawned = []
    spotify_source._bg = Mock()
    spotify_source._bg.spawn = Mock(side_effect=lambda coro, **kw: spawned.append(coro))

    yield state_machine.update_source_state, spawned

    for coro in spawned:
        coro.close()


def published_state(publish):
    """The (state, metadata) of the last push to the state machine."""
    source, state, metadata = publish.call_args.args
    return state, metadata


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
    async def test_start_success(self, spotify_source):
        """A start over a daemon that answers wires the WS and the journal.

        The three steps `_do_start` ends on are the ones a start exists for:
        the /events socket built on the config's URL and the source's own
        handlers, sharing the HTTP session, and the monitor following
        go-librespot's unit.
        """
        session = librespot_api({"playback_ready": True})
        journal = JournalDouble()

        async with go_librespot(spotify_source, session, journal) as ws_cls:
            result = await spotify_source.start()
            await asyncio.sleep(0)  # let the monitor task reach follow_unit

            assert result is True
            ws_cls.assert_called_once()
            kwargs = ws_cls.call_args.kwargs
            assert kwargs["ws_url"] == "ws://localhost:3678/events"
            assert kwargs["session"] is session
            assert kwargs["on_event"] == spotify_source._handle_ws_event
            assert kwargs["on_connect"] == spotify_source._reconcile_on_connect
            ws_cls.return_value.start.assert_awaited_once()
            assert journal.units == ["milo-spotify"]

    @pytest.mark.asyncio
    async def test_start_no_config_file(self):
        """Test start fails if config file doesn't exist."""
        source = SpotifySource({"config_path": "/nonexistent/path"})
        source._service_manager = Mock()
        source._service_manager.start = AsyncMock(return_value=True)

        result = await source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, spotify_source):
        """Stop posts /player/stop (graceful) before cleanup + service stop."""
        spotify_source._api_url = "http://localhost:3678"
        spotify_source._session = MagicMock()
        spotify_source._session.close = AsyncMock()

        # Mock the POST /player/stop async context manager
        mock_response = MagicMock()
        mock_response.status = 200
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.post.return_value = mock_cm
        post_mock = spotify_source._session.post

        spotify_source._ws_client = AsyncMock()
        spotify_source._ws_client.stop = AsyncMock()
        spotify_source._device_connected = True

        result = await spotify_source.stop()

        assert result is True
        assert spotify_source._device_connected is False
        assert spotify_source._session is None
        # Graceful /player/stop was sent, then the service was stopped
        post_mock.assert_called_once()
        assert "/player/stop" in post_mock.call_args.args[0]
        spotify_source._service_manager.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_for_playback_ready_returns_on_200(self, spotify_source):
        """Readiness poll returns as soon as GET / answers 200, regardless of
        the playback_ready flag (false in zeroconf with no session at start)."""
        spotify_source._api_url = "http://localhost:3678"
        spotify_source._session = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"playback_ready": False})
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.get.return_value = mock_cm

        result = await spotify_source._wait_for_playback_ready(timeout=1.0, interval=0.01)

        assert result is True
        assert spotify_source._session.get.call_args.args[0].endswith("/")

    @pytest.mark.asyncio
    async def test_wait_for_playback_ready_gives_up_after_the_cap(
        self, spotify_source, caplog
    ):
        """A daemon that refuses every connection ends the poll at the cap, with
        a warning and a False verdict — it must not wedge the start."""
        spotify_source._api_url = "http://localhost:3678"
        spotify_source._session = refusing_session()

        with caplog.at_level(logging.WARNING):
            result = await spotify_source._wait_for_playback_ready(
                timeout=0.05, interval=0.01
            )

        assert result is False
        assert spotify_source._session.get.call_count > 1
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
    """Test SpotifySource command handling."""

    @pytest.mark.asyncio
    async def test_playpause_command(self, spotify_source):
        """The hardware toggle passes straight through to go-librespot."""
        spotify_source._session = MagicMock()
        spotify_source._api_url = "http://localhost:3678"

        mock_response = MagicMock()
        mock_response.status = 200

        # Properly mock async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.post.return_value = mock_cm

        result = await spotify_source.command("playpause", {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_seek_command(self, spotify_source):
        """Test seek command."""
        spotify_source._session = MagicMock()
        spotify_source._api_url = "http://localhost:3678"

        mock_response = MagicMock()
        mock_response.status = 200

        # Properly mock async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.post.return_value = mock_cm

        result = await spotify_source.command("seek", {"position_ms": 30000})

        assert result["success"] is True

class TestNextPrevCommands:
    """`next` / `prev`, the one command arm the suite never entered.

    NextPrevParams carries an optional target URI, and the two payload shapes it
    produces sat at 0% of lines. Sending `{"uri": null}` instead of `{}` is the
    failure this pins: go-librespot reads the key, so a null target is not the
    same request as no target.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["next", "prev"])
    async def test_a_bare_skip_carries_no_target(self, spotify_source, cmd):
        """No URI given, no URI sent — not a null one."""
        session = mock_librespot_api(spotify_source)

        result = await spotify_source._handle_command(cmd, NextPrevParams())

        assert result["success"] is True
        assert posted_commands(session) == [(cmd, {})]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd", ["next", "prev"])
    async def test_a_targeted_skip_carries_the_uri(self, spotify_source, cmd):
        """The queue view jumps to a track by URI through this same command."""
        session = mock_librespot_api(spotify_source)
        uri = "spotify:track:0eGsygTp906u18L0Oimnem"

        await spotify_source._handle_command(cmd, NextPrevParams(uri=uri))

        assert posted_commands(session) == [(cmd, {"uri": uri})]

    @pytest.mark.asyncio
    async def test_an_unknown_command_is_refused_rather_than_forwarded(self, spotify_source):
        """COMMANDS gates dispatch, so this arm is only reachable by a new
        entry someone forgot to serve — it must not reach the daemon."""
        session = mock_librespot_api(spotify_source)

        result = await spotify_source._handle_command("shuffle", None)

        assert result["success"] is False
        assert posted_commands(session) == []


class TestWebSocketEvents:
    """Test WebSocket event handling."""

    @pytest.mark.asyncio
    async def test_device_active_event(self, spotify_source, wired):
        """A device_active event publishes the track go-librespot reports."""
        publish, _ = wired
        spotify_source._session = librespot_api(TRACK_STATUS)

        await spotify_source._on_device_active()

        assert spotify_source._device_connected is True
        state, metadata = published_state(publish)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == TRACK_STATUS["track"]["name"]

    @pytest.mark.asyncio
    async def test_device_inactive_event(self, spotify_source):
        """Test handling device inactive event."""
        spotify_source._device_connected = True
        spotify_source._is_playing = True
        spotify_source._metadata = {"title": "Test"}

        await spotify_source._on_device_inactive()

        assert spotify_source._device_connected is False
        assert spotify_source._is_playing is False
        # Metadata is cleared in _on_device_inactive
        assert "title" not in spotify_source._metadata or spotify_source._metadata.get("title") is None

    @pytest.mark.asyncio
    async def test_playback_playing_event(self, spotify_source, wired):
        """Test handling playing event."""
        publish, _ = wired
        spotify_source._session = librespot_api(TRACK_STATUS)

        await spotify_source._on_playback_state(True)

        assert spotify_source._is_playing is True
        assert spotify_source._device_connected is True
        assert published_state(publish)[0] == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_playback_paused_event(self, spotify_source, wired):
        """Test handling paused event."""
        spotify_source._session = librespot_api({**TRACK_STATUS, "paused": True})

        await spotify_source._on_playback_state(False)

        assert spotify_source._is_playing is False

    @pytest.mark.asyncio
    async def test_seek_event(self, spotify_source, wired):
        """A seek republishes the playhead go-librespot reports."""
        publish, _ = wired
        seeked = {**TRACK_STATUS, "track": {**TRACK_STATUS["track"], "position": 45000}}
        spotify_source._session = librespot_api(seeked)

        await spotify_source._on_seek()

        assert published_state(publish)[1]["position"] == seeked["track"]["position"]


class TestProducerTruth:
    """ACTIVE must mean "there is a track".

    The four event handlers set _device_connected optimistically — a
    go-librespot event *is* a session — and then ask the daemon what is
    playing. When that answer never arrives, publishing anyway announces a
    session with no title, and the status card, having nothing to render, draws
    its idle line over playing audio. These pin that it doesn't.
    """

    @pytest.mark.asyncio
    async def test_unreadable_status_publishes_nothing(self, spotify_source, wired):
        """A daemon answering 500 teaches us nothing: keep the last published
        state rather than announcing a session we cannot describe."""
        publish, _ = wired
        spotify_source._session = librespot_api(TRACK_STATUS)
        await spotify_source._on_device_active()
        before = published_state(publish)
        publish.reset_mock()

        spotify_source._session = librespot_api({}, status=500)
        await spotify_source._on_metadata_update()

        publish.assert_not_called()
        assert spotify_source.state == before[0]
        assert spotify_source.metadata["title"] == before[1]["title"]

    @pytest.mark.asyncio
    async def test_unreadable_status_retries(self, spotify_source, wired):
        """The daemon emits an event only on change, so a refresh we failed to
        read is never re-announced — the source must go back for it itself."""
        publish, spawned = wired
        spotify_source.STATUS_RETRY_DELAY = 0
        spotify_source._session = librespot_api({}, status=500)

        await spotify_source._on_device_active()
        publish.assert_not_called()

        # The daemon is answering again by the time the retry fires.
        spotify_source._session = librespot_api(TRACK_STATUS)
        await asyncio.gather(*spawned)

        state, metadata = published_state(publish)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == TRACK_STATUS["track"]["name"]

    @pytest.mark.asyncio
    async def test_session_without_a_track_title_publishes_ready(
        self, spotify_source, wired
    ):
        """A readable status whose track carries no name is a session with
        nothing to draw — publish the idle state, not a titleless ACTIVE."""
        publish, _ = wired
        untitled = {"track": {"artist_names": ["Telepopmusik"]}, "paused": False}
        spotify_source._session = librespot_api(untitled)

        await spotify_source._on_device_active()

        assert published_state(publish)[0] == SourceState.READY


class TestReconcileOnConnect:
    """Reconciliation against the daemon after an un-commanded WS drop."""

    @pytest.mark.asyncio
    async def test_reconcile_on_connect_idle_daemon_resets_to_ready(self, spotify_source):
        """On (re)connect to an idle daemon (crash + systemd restart), reconcile
        pulls GET /status, finds no session, and resets the stale 'now playing'
        state to READY (also dropping any leftover pause timer)."""
        # Stale 'playing' snapshot left over from before the daemon died
        spotify_source._device_connected = True
        spotify_source._is_playing = True
        spotify_source._metadata = {"title": "Breathe", "is_playing": True}
        spotify_source._update_connection_state()
        assert spotify_source.state == SourceState.ACTIVE

        async def idle_refresh():
            # Mirrors refresh_metadata against an empty GET /status (no track)
            spotify_source._device_connected = False
            spotify_source._metadata = {}
            return True

        timer = asyncio.create_task(asyncio.sleep(3600))
        spotify_source._pause_timer = timer

        with patch.object(spotify_source, 'refresh_metadata', side_effect=idle_refresh):
            await spotify_source._reconcile_on_connect()

        assert spotify_source._device_connected is False
        assert spotify_source.state == SourceState.READY
        # The leftover auto-stop is gone, not merely forgotten.
        assert spotify_source._pause_timer is None
        with pytest.raises(asyncio.CancelledError):
            await timer

    @pytest.mark.asyncio
    async def test_reconcile_on_connect_live_session_stays_active(self, spotify_source):
        """On reconnect with a live session, reconcile refreshes metadata and the
        source stays ACTIVE (also heals any events missed during the gap)."""
        async def live_refresh():
            spotify_source._device_connected = True
            spotify_source._metadata = {"title": "Breathe", "is_playing": True}
            return True

        with patch.object(spotify_source, 'refresh_metadata', side_effect=live_refresh):
            await spotify_source._reconcile_on_connect()

        assert spotify_source._device_connected is True
        assert spotify_source.state == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_reconcile_on_connect_unreachable_resets_defensively(self, spotify_source):
        """If GET /status is unreachable on reconnect (daemon API not up yet after
        a crash+restart), refresh_metadata returns False without clearing the
        flags. Reconcile must still reset defensively to READY rather than
        re-affirm the stale 'now playing' (the WS loop retries in 2s)."""
        spotify_source._device_connected = True
        spotify_source._metadata = {"title": "Breathe", "is_playing": True}

        timer = asyncio.create_task(asyncio.sleep(3600))
        spotify_source._pause_timer = timer

        with patch.object(spotify_source, 'refresh_metadata', new_callable=AsyncMock, return_value=False):
            await spotify_source._reconcile_on_connect()

        assert spotify_source._device_connected is False
        assert "title" not in spotify_source._metadata  # ghost track cleared
        assert spotify_source.state == SourceState.READY
        assert spotify_source._pause_timer is None
        with pytest.raises(asyncio.CancelledError):
            await timer


class TestAutoStop:
    """Test auto-stop timer functionality."""

    def test_cancel_pause_timer(self, spotify_source):
        """Test canceling pause timer."""
        mock_timer = Mock()
        mock_timer.cancel = Mock()
        spotify_source._pause_timer = mock_timer

        spotify_source._cancel_pause_timer()

        mock_timer.cancel.assert_called_once()
        assert spotify_source._pause_timer is None

    def test_start_pause_timer_disabled(self, spotify_source):
        """Test timer not started when disabled."""
        spotify_source.auto_stop_enabled = False

        spotify_source._start_pause_timer()

        assert spotify_source._pause_timer is None

    @pytest.mark.asyncio
    async def test_on_auto_stop_posts_player_stop(self, spotify_source):
        """Auto-stop ends the session via /player/stop, no process bounce."""
        spotify_source._api_url = "http://localhost:3678"
        spotify_source._session = MagicMock()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.post.return_value = mock_cm

        await spotify_source._on_auto_stop()

        spotify_source._session.post.assert_called_once()
        assert "/player/stop" in spotify_source._session.post.call_args.args[0]
        # Daemon stays alive — no systemctl restart/stop on auto-stop
        spotify_source._service_manager.restart.assert_not_called()
        spotify_source._service_manager.stop.assert_not_called()

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


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_device(self, spotify_source):
        """Test state is READY with no device."""
        spotify_source._device_connected = False
        spotify_source._update_connection_state()

        assert spotify_source.state == SourceState.READY

    def test_update_state_with_device(self, spotify_source):
        """Test state is ACTIVE with device."""
        spotify_source._device_connected = True
        spotify_source._is_playing = True
        spotify_source._metadata = {"title": "Test"}
        spotify_source._update_connection_state()

        assert spotify_source.state == SourceState.ACTIVE


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
    async def test_transform_track_metadata(self, spotify_source):
        """Test metadata transformation from API response."""
        spotify_source._session = MagicMock()
        spotify_source._api_url = "http://localhost:3678"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "track": {
                "name": "Test Song",
                "artist_names": ["Artist 1", "Artist 2"],
                "album_name": "Test Album",
                "album_cover_url": "https://example.com/cover.jpg",
                "duration": 180000,
                "position": 45000
            },
            "paused": False
        })

        # Properly mock async context manager for get
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.get.return_value = mock_cm

        result = await spotify_source.refresh_metadata()

        assert result is True
        assert spotify_source._metadata["title"] == "Test Song"
        assert spotify_source._metadata["artist"] == "Artist 1, Artist 2"
        assert spotify_source._metadata["album"] == "Test Album"
        assert spotify_source._metadata["duration"] == 180000
        assert spotify_source._metadata["is_playing"] is True


def mock_librespot_api(source, *, paused=True, post_status=200):
    """Stand in for go-librespot's HTTP API — the outside world of this source.

    A small stateful fake rather than a fixed answer: /status reports what the
    POSTs did to it, so the source has to actually pause the daemon to observe a
    paused daemon. A canned `paused` would let a release that never pauses pass.
    `post_status` != 200 simulates a daemon that cannot be driven.
    """
    state = {"paused": paused}

    source._session = MagicMock()
    source._session.close = AsyncMock()  # awaited by _cleanup on the fallback path
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
    source._session.get.return_value = get_cm

    post_response = MagicMock()
    post_response.status = post_status
    post_cm = AsyncMock()
    post_cm.__aenter__.return_value = post_response

    def post(url, json=None):
        command = url.rsplit("/player/", 1)[-1]
        if post_status == 200 and command in ("pause", "resume"):
            state["paused"] = command == "pause"
        return post_cm

    source._session.post = MagicMock(side_effect=post)

    return source._session


def posted_commands(session):
    """(command, payload) for every POST /player/* the source issued."""
    return [
        (call.args[0].rsplit("/player/", 1)[-1], call.kwargs.get("json"))
        for call in session.post.call_args_list
    ]


class TestMultiroomReroute:
    """Keeping the Connect session across a multiroom toggle.

    AudioRoutingService._apply_transition releases the source, reconciles
    snapcast, then re-acquires it. Before go-librespot 0.8.0 that meant a full
    daemon bounce: the phone lost the speaker and playback stopped. These hooks
    park the output instead — and the order they do it in is load-bearing, so
    each rule below pins one thing measured on the unit rather than reasoned.
    """

    @pytest.mark.asyncio
    async def test_release_pauses_before_parking_the_output(self, spotify_source):
        """Pause must land BEFORE the switch, and the service must stay up.

        RELEASE_DEVICE does not rate-limit: switching to it while playing runs
        the track to its end in seconds (measured on the unit). Pausing after
        the switch would be too late.
        """
        session = mock_librespot_api(spotify_source, paused=False)

        assert await spotify_source.release_for_reroute() is True

        assert posted_commands(session) == [
            ("pause", {}),
            ("output", {"device": "null"}),
        ]
        spotify_source._service_manager.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_of_a_paused_session_skips_the_pause(self, spotify_source):
        """Nothing to pause: the output is parked straight away."""
        session = mock_librespot_api(spotify_source, paused=True)

        assert await spotify_source.release_for_reroute() is True

        assert posted_commands(session) == [("output", {"device": "null"})]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["direct", "multiroom"])
    async def test_acquire_reopens_on_the_device_of_the_new_mode(
        self, spotify_source, monkeypatch, mode
    ):
        """The explicit device is what makes the reroute work without a restart.

        The `milo_spotify` alias resolves MILO_MODE from the daemon's own
        environment, frozen at its start — it would still name the old mode.
        """
        monkeypatch.setenv("MILO_MODE", mode)
        session = mock_librespot_api(spotify_source)
        spotify_source._soft_reroute = True
        spotify_source._reroute_was_playing = False

        assert await spotify_source.acquire_after_reroute() is True

        assert posted_commands(session) == [("output", {"device": f"milo_spotify_{mode}"})]
        spotify_source._service_manager.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_acquire_resumes_only_what_was_playing(self, spotify_source, monkeypatch):
        """A session paused by the user must not come back playing."""
        monkeypatch.setenv("MILO_MODE", "direct")
        session = mock_librespot_api(spotify_source, paused=False)
        spotify_source._soft_reroute = True
        spotify_source._reroute_was_playing = True

        await spotify_source.acquire_after_reroute()

        assert posted_commands(session)[-1] == ("resume", {})

    @pytest.mark.asyncio
    async def test_unreachable_daemon_falls_back_to_a_full_stop(self, spotify_source):
        """A source still holding the loopback would block snapclient.

        So a daemon that cannot be driven must lose its session rather than keep
        the device: the fallback is the base stop(), not a silent no-op.
        """
        mock_librespot_api(spotify_source, paused=True, post_status=500)

        assert await spotify_source.release_for_reroute() is True

        spotify_source._service_manager.stop.assert_called_once_with("milo-spotify.service")
        assert spotify_source._soft_reroute is False

    @pytest.mark.asyncio
    async def test_a_hard_release_is_re_acquired_by_a_full_start(self, spotify_source):
        """After the fallback there is no session left to reopen an output on."""
        spotify_source._soft_reroute = False

        with patch.object(spotify_source, 'start', new_callable=AsyncMock, return_value=True) as start:
            assert await spotify_source.acquire_after_reroute() is True

        start.assert_awaited_once()


class TestManagedConfig:
    """The go-librespot config key Milō owns.

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


class TestWebSocketEventDispatch:
    """go-librespot's event vocabulary, mapped to the handlers that serve it.

    The handlers below each had a test; the map that reaches them had none. The
    only test that named `_handle_ws_event` asserted it was *wired* as the
    socket's `on_event` — `TestSpotifySourceLifecycle` still does — and the
    event tests call `_on_playback_state(True)` and friends directly, so all
    eight of these wire names sat at 0% of lines. A name go-librespot renames,
    or an arm typed `not-playing` for `not_playing`, would reach nothing and
    fail in silence: the daemon keeps streaming and the screen keeps showing
    whatever it last published.

    Two handlers hang off this map alone (`_on_stopped`, `_on_not_playing`) and
    were unreachable for the whole suite as a result.

    The spies WRAP the real handlers rather than replace them, so each case
    still runs the production handler; what they add is *which* one ran. And
    every case demands exactly one — several arms leave the same trace
    (`stopped`, `not_playing` and `paused` all clear `_is_playing`), so an
    assertion on the trace alone would not tell them apart.
    """

    HANDLERS = [
        "_on_device_active", "_on_device_inactive", "_on_playback_state",
        "_on_metadata_update", "_on_seek", "_on_stopped", "_on_not_playing",
    ]

    @contextlib.contextmanager
    def _spies(self, source):
        with contextlib.ExitStack() as stack:
            yield {
                name: stack.enter_context(
                    patch.object(source, name, wraps=getattr(source, name))
                )
                for name in self.HANDLERS
            }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wire_name, handler, args", [
        ("active", "_on_device_active", ()),
        ("inactive", "_on_device_inactive", ()),
        ("playing", "_on_playback_state", (True,)),
        ("paused", "_on_playback_state", (False,)),
        ("metadata", "_on_metadata_update", ()),
        ("seek", "_on_seek", ()),
        ("stopped", "_on_stopped", ()),
        ("not_playing", "_on_not_playing", ()),
    ])
    async def test_each_wire_event_reaches_its_own_handler(
        self, spotify_source, wired, wire_name, handler, args
    ):
        """One event name in, exactly one handler out, with the arguments it owes.

        `playing` and `paused` share a handler and differ only by the flag, which
        is why the argument is asserted and not just the call.
        """
        spotify_source._session = librespot_api(TRACK_STATUS)

        with self._spies(spotify_source) as spies:
            await spotify_source._handle_ws_event({"type": wire_name})

        assert spies[handler].await_args.args == args
        assert [n for n, spy in spies.items() if spy.await_count] == [handler]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event", [
        {"type": "volume"},
        {"type": "not_playing_yet"},
        {},
    ])
    async def test_an_unhandled_event_reaches_no_handler_at_all(
        self, spotify_source, wired, event
    ):
        """The fall-through is silence, not a wrong guess.

        go-librespot sends more than Milō consumes (volume rides CamillaDSP, not
        this socket), and a missing `type` is what a truncated frame looks like.
        Neither may be routed to a handler by accident — `not_playing_yet` is
        here because a prefix match would take it for `not_playing`.
        """
        with self._spies(spotify_source) as spies:
            await spotify_source._handle_ws_event(event)

        assert [n for n, spy in spies.items() if spy.await_count] == []


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
        spotify_source.broadcast_error("go-librespot is unreachable")
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
        assert event.message == "failed loading current track: context has no tracks"

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
