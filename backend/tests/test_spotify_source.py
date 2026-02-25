# backend/tests/test_spotify_source.py
"""
Unit tests for SpotifySource (features/spotify/source.py).

Tests cover:
- AudioSource Protocol compliance
- Lifecycle (start, stop, restart)
- WebSocket event handling
- Metadata refresh
- EventBus integration
- Command handling
- Auto-disconnect timer
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import os

from backend.features.spotify.source import SpotifySource
from backend.features.spotify.websocket import LibrespotWebSocket
from backend.core.events import EventBus, Events
from backend.core.audio_source import AudioSource, SourceState


@pytest.fixture
def event_bus():
    """Create EventBus for tests."""
    return EventBus(debug=True)


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
def spotify_source(event_bus, config):
    """Create SpotifySource with mocked components."""
    source = SpotifySource(event_bus, config)

    # Mock service manager
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)

    return source


class TestProtocolCompliance:
    """Test AudioSource Protocol compliance."""

    def test_implements_protocol(self, spotify_source):
        """Test SpotifySource implements AudioSource protocol."""
        assert isinstance(spotify_source, AudioSource)

    def test_has_required_attributes(self, spotify_source):
        """Test required attributes exist."""
        assert spotify_source.source_id == "spotify"
        assert spotify_source.service_name == "milo-spotify.service"

    def test_has_required_methods(self, spotify_source):
        """Test required methods exist."""
        assert hasattr(spotify_source, 'start')
        assert hasattr(spotify_source, 'stop')
        assert hasattr(spotify_source, 'restart')
        assert hasattr(spotify_source, 'status')
        assert hasattr(spotify_source, 'command')


class TestSpotifySourceConfig:
    """Test SpotifySource configuration."""

    def test_default_config(self, event_bus):
        """Test default configuration values."""
        source = SpotifySource(event_bus)

        assert source.auto_disconnect_enabled is True
        assert source.pause_disconnect_delay == 10.0

    def test_custom_config(self, event_bus, tmp_path):
        """Test custom configuration."""
        config_file = tmp_path / "config.yml"
        config_file.write_text("""
server:
  address: 192.168.1.100
  port: 5000
""")
        config = {"config_path": str(config_file)}
        source = SpotifySource(event_bus, config)

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

            # Mock WebSocket and log monitor to avoid real subprocess
            with patch.object(spotify_source, '_start_websocket', new_callable=AsyncMock):
                with patch.object(spotify_source, '_start_log_monitor'):
                    result = await spotify_source.start()

        assert result is True

    @pytest.mark.asyncio
    async def test_start_no_config_file(self, event_bus):
        """Test start fails if config file doesn't exist."""
        source = SpotifySource(event_bus, {"config_path": "/nonexistent/path"})
        source._service_manager = Mock()
        source._service_manager.start = AsyncMock(return_value=True)

        result = await source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, spotify_source):
        """Test successful stop."""
        spotify_source._session = AsyncMock()
        spotify_source._session.close = AsyncMock()
        spotify_source._ws_client = AsyncMock()
        spotify_source._ws_client.stop = AsyncMock()
        spotify_source._device_connected = True

        result = await spotify_source.stop()

        assert result is True
        assert spotify_source._device_connected is False
        assert spotify_source._session is None


class TestSpotifySourceStatus:
    """Test SpotifySource status method."""

    @pytest.mark.asyncio
    async def test_status_no_device(self, spotify_source):
        """Test status with no device connected."""
        status = await spotify_source.status()

        assert "state" in status
        assert status["device_connected"] is False
        assert status["is_playing"] is False
        assert status["metadata"] == {}

    @pytest.mark.asyncio
    async def test_status_with_device(self, spotify_source):
        """Test status with connected device."""
        spotify_source._device_connected = True
        spotify_source._is_playing = True
        spotify_source._metadata = {
            "title": "Test Song",
            "artist": "Test Artist"
        }

        status = await spotify_source.status()

        assert status["device_connected"] is True
        assert status["is_playing"] is True
        assert status["metadata"]["title"] == "Test Song"


class TestSpotifySourceCommands:
    """Test SpotifySource command handling."""

    @pytest.mark.asyncio
    async def test_restart_service_command(self, spotify_source):
        """Test restart_service command."""
        with patch.object(spotify_source, '_do_restart', return_value=True):
            result = await spotify_source.command("restart_service", {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_refresh_metadata_command(self, spotify_source):
        """Test refresh_metadata command."""
        with patch.object(spotify_source, '_refresh_metadata', return_value=True):
            result = await spotify_source.command("refresh_metadata", {})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_play_command(self, spotify_source):
        """Test play command."""
        spotify_source._session = MagicMock()
        spotify_source._api_url = "http://localhost:3678"

        mock_response = MagicMock()
        mock_response.status = 200

        # Properly mock async context manager
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.post.return_value = mock_cm

        result = await spotify_source.command("play", {})

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

    @pytest.mark.asyncio
    async def test_unknown_command(self, spotify_source):
        """Test unknown command returns error."""
        result = await spotify_source.command("unknown_cmd", {})

        assert result["success"] is False
        assert "error" in result


class TestSpotifySourceEventBus:
    """Test SpotifySource EventBus integration."""

    @pytest.mark.asyncio
    async def test_start_emits_event(self, spotify_source, event_bus):
        """Test start emits SOURCE_STARTED event."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STARTED, handler)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_session.close = AsyncMock()

            with patch.object(spotify_source, '_start_websocket', new_callable=AsyncMock):
                with patch.object(spotify_source, '_start_log_monitor'):
                    await spotify_source.start()

        assert len(received) == 1
        assert received[0]["source"] == "spotify"

    @pytest.mark.asyncio
    async def test_stop_emits_event(self, spotify_source, event_bus):
        """Test stop emits SOURCE_STOPPED event."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STOPPED, handler)

        await spotify_source.stop()

        assert len(received) == 1
        assert received[0]["source"] == "spotify"


class TestWebSocketEvents:
    """Test WebSocket event handling."""

    @pytest.mark.asyncio
    async def test_device_active_event(self, spotify_source):
        """Test handling device active event."""
        spotify_source._session = AsyncMock()

        with patch.object(spotify_source, '_refresh_metadata', return_value=True):
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

        with patch.object(spotify_source, '_refresh_metadata', return_value=True):
            await spotify_source._on_playback_state(True)

        assert spotify_source._is_playing is True
        assert spotify_source._device_connected is True

    @pytest.mark.asyncio
    async def test_playback_paused_event(self, spotify_source):
        """Test handling paused event."""
        spotify_source._session = AsyncMock()
        spotify_source.auto_disconnect_enabled = False  # Disable to simplify test

        with patch.object(spotify_source, '_refresh_metadata', return_value=True):
            await spotify_source._on_playback_state(False)

        assert spotify_source._is_playing is False

    @pytest.mark.asyncio
    async def test_seek_event(self, spotify_source):
        """Test handling seek event refreshes metadata."""
        spotify_source._metadata = {"title": "Test", "position": 0}

        with patch.object(spotify_source, '_refresh_metadata', new_callable=AsyncMock) as mock_refresh:
            async def set_position():
                spotify_source._metadata["position"] = 45000
            mock_refresh.side_effect = set_position

            await spotify_source._on_seek()

        assert spotify_source._metadata["position"] == 45000


class TestAutoDisconnect:
    """Test auto-disconnect timer functionality."""

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
        spotify_source.auto_disconnect_enabled = False

        spotify_source._start_pause_timer()

        assert spotify_source._pause_timer is None

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_disabled(self, spotify_source):
        """Test setting delay to 0 disables auto-disconnect."""
        result = await spotify_source.set_auto_disconnect_config(
            enabled=True,
            delay=0,
            save_to_settings=False
        )

        assert result is True
        assert spotify_source.auto_disconnect_enabled is False

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_enabled(self, spotify_source):
        """Test setting delay enables auto-disconnect."""
        result = await spotify_source.set_auto_disconnect_config(
            enabled=True,
            delay=30.0,
            save_to_settings=False
        )

        assert result is True
        assert spotify_source.auto_disconnect_enabled is True
        assert spotify_source.pause_disconnect_delay == 30.0


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_device(self, spotify_source):
        """Test state is READY with no device."""
        spotify_source._device_connected = False
        spotify_source._update_connection_state()

        assert spotify_source.state == SourceState.READY

    def test_update_state_with_device(self, spotify_source):
        """Test state is CONNECTED with device."""
        spotify_source._device_connected = True
        spotify_source._is_playing = True
        spotify_source._metadata = {"title": "Test"}
        spotify_source._update_connection_state()

        assert spotify_source.state == SourceState.CONNECTED


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
                "position": 45000,
                "uri": "spotify:track:abc123"
            },
            "paused": False
        })

        # Properly mock async context manager for get
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        spotify_source._session.get.return_value = mock_cm

        result = await spotify_source._refresh_metadata()

        assert result is True
        assert spotify_source._metadata["title"] == "Test Song"
        assert spotify_source._metadata["artist"] == "Artist 1, Artist 2"
        assert spotify_source._metadata["album"] == "Test Album"
        assert spotify_source._metadata["duration"] == 180000
        assert spotify_source._metadata["is_playing"] is True
