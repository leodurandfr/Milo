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
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.sources.spotify.source import SpotifySource
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.core.models.audio_state import SourceState


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


class TestBaseClassCompliance:
    """Test that SpotifySource extends BaseAudioSource correctly."""

    def test_has_required_attributes(self, spotify_source):
        """Test required attributes exist."""
        assert spotify_source.source_id == "spotify"
        assert spotify_source.service_name == "milo-spotify.service"

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
        """Test successful start."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_session.close = AsyncMock()

            # Mock WebSocket, log monitor and readiness poll to avoid real I/O
            with patch.object(spotify_source, '_wait_for_playback_ready', new_callable=AsyncMock, return_value=True):
                with patch.object(spotify_source, '_start_websocket', new_callable=AsyncMock):
                    with patch.object(spotify_source, '_start_log_monitor'):
                        result = await spotify_source.start()

        assert result is True

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

class TestWebSocketEvents:
    """Test WebSocket event handling."""

    @pytest.mark.asyncio
    async def test_device_active_event(self, spotify_source):
        """Test handling device active event."""
        spotify_source._session = AsyncMock()

        with patch.object(spotify_source, 'refresh_metadata', return_value=True):
            await spotify_source._on_device_active()

        assert spotify_source._device_connected is True

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
    async def test_playback_playing_event(self, spotify_source):
        """Test handling playing event."""
        spotify_source._session = AsyncMock()

        with patch.object(spotify_source, 'refresh_metadata', return_value=True):
            await spotify_source._on_playback_state(True)

        assert spotify_source._is_playing is True
        assert spotify_source._device_connected is True

    @pytest.mark.asyncio
    async def test_playback_paused_event(self, spotify_source):
        """Test handling paused event."""
        spotify_source._session = AsyncMock()
        spotify_source.auto_stop_enabled = False  # Disable to simplify test

        with patch.object(spotify_source, 'refresh_metadata', return_value=True):
            await spotify_source._on_playback_state(False)

        assert spotify_source._is_playing is False

    @pytest.mark.asyncio
    async def test_seek_event(self, spotify_source):
        """Test handling seek event refreshes metadata."""
        spotify_source._metadata = {"title": "Test", "position": 0}

        with patch.object(spotify_source, 'refresh_metadata', new_callable=AsyncMock) as mock_refresh:
            async def set_position():
                spotify_source._metadata["position"] = 45000
            mock_refresh.side_effect = set_position

            await spotify_source._on_seek()

        assert spotify_source._metadata["position"] == 45000

    @pytest.mark.asyncio
    async def test_reconcile_on_connect_idle_daemon_resets_to_waiting(self, spotify_source):
        """On (re)connect to an idle daemon (crash + systemd restart), reconcile
        pulls GET /status, finds no session, and resets the stale 'now playing'
        state to WAITING (also dropping any leftover pause timer)."""
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

        with patch.object(spotify_source, 'refresh_metadata', side_effect=idle_refresh), \
             patch.object(spotify_source, '_cancel_pause_timer') as mock_cancel:
            await spotify_source._reconcile_on_connect()

        assert spotify_source._device_connected is False
        assert spotify_source.state == SourceState.WAITING
        mock_cancel.assert_called_once()

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
        flags. Reconcile must still reset defensively to WAITING rather than
        re-affirm the stale 'now playing' (the WS loop retries in 2s)."""
        spotify_source._device_connected = True
        spotify_source._metadata = {"title": "Breathe", "is_playing": True}

        with patch.object(spotify_source, 'refresh_metadata', new_callable=AsyncMock, return_value=False), \
             patch.object(spotify_source, '_cancel_pause_timer') as mock_cancel:
            await spotify_source._reconcile_on_connect()

        assert spotify_source._device_connected is False
        assert "title" not in spotify_source._metadata  # ghost track cleared
        assert spotify_source.state == SourceState.WAITING
        mock_cancel.assert_called_once()


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
        """Test state is WAITING with no device."""
        spotify_source._device_connected = False
        spotify_source._update_connection_state()

        assert spotify_source.state == SourceState.WAITING

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
