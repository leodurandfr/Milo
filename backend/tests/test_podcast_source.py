# backend/tests/test_podcast_source.py
"""
Unit tests for PodcastSource (features/podcast/source.py).

Tests cover:
- BaseAudioSource compliance
- Lifecycle (start, stop, restart)
- Status format
- Command handling (play, pause, seek, speed)
- Data service operations
"""
import json

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.data import PodcastDataService
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState
from backend.shared.persistence import SchemaVersionMismatch


@pytest.fixture
def config():
    """Default Podcast source config."""
    return {
        "mpv_socket": "/tmp/test-podcast-ipc.sock",
        "taddy_user_id": "test-user",
        "taddy_api_key": "test-key"
    }


@pytest.fixture
def podcast_source(config):
    """Create PodcastSource with mocked components."""
    source = PodcastSource(config)

    # Mock service manager
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)

    # Mock podcast data service so tests don't touch the real /var/lib/milo file.
    # Individual tests can override these methods or replace _podcast_data entirely.
    source._podcast_data = AsyncMock()
    source._podcast_data.get_setting = AsyncMock(return_value=1.0)

    return source


class TestBaseClassCompliance:
    """Test that PodcastSource extends BaseAudioSource correctly."""

    def test_extends_base_audio_source(self, podcast_source):
        """Test PodcastSource extends BaseAudioSource."""
        assert isinstance(podcast_source, BaseAudioSource)

    def test_has_required_attributes(self, podcast_source):
        """Test required attributes exist."""
        assert podcast_source.source_id == "podcast"
        assert podcast_source.service_name == "milo-podcast.service"

    def test_has_required_methods(self, podcast_source):
        """Test required methods exist."""
        assert hasattr(podcast_source, 'start')
        assert hasattr(podcast_source, 'stop')
        assert hasattr(podcast_source, 'status')
        assert hasattr(podcast_source, 'command')


class TestPodcastSourceConfig:
    """Test PodcastSource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = PodcastSource()

        assert source._mpv_socket == "/run/milo/podcast-ipc.sock"
        assert source._taddy_api.user_id == ""
        assert source._taddy_api.api_key == ""

    def test_custom_config(self):
        """Test custom configuration."""
        config = {
            "mpv_socket": "/custom/socket.sock",
        }
        mock_settings = MagicMock()
        mock_settings.get_setting_sync = lambda key: {
            "podcast.taddy_user_id": "custom-user",
            "podcast.taddy_api_key": "custom-key"
        }.get(key, "")

        source = PodcastSource(config, settings_service=mock_settings)

        assert source._mpv_socket == "/custom/socket.sock"
        assert source._taddy_api.user_id == "custom-user"
        assert source._taddy_api.api_key == "custom-key"


class TestPodcastSourceLifecycle:
    """Test PodcastSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self, podcast_source):
        """Test successful start."""
        # Mock dependencies
        with patch.object(podcast_source, '_start_service', return_value=True):
            with patch('backend.sources.podcast.source.PodcastDataService') as mock_data_class:
                mock_data = AsyncMock()
                mock_data.get_setting = AsyncMock(return_value=1.0)
                mock_data_class.return_value = mock_data

                with patch('backend.sources.podcast.source.TaddyAPI') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.sources.podcast.source.MpvController') as mock_mpv_class:
                        mock_mpv = Mock()
                        mock_mpv.connect = AsyncMock(return_value=True)
                        mock_mpv.is_connected = True
                        mock_mpv_class.return_value = mock_mpv

                        result = await podcast_source.start()

        assert result is True

    @pytest.mark.asyncio
    async def test_start_mpv_connection_failure(self, podcast_source):
        """Test start fails if MPV connection fails."""
        with patch.object(podcast_source, '_start_service', return_value=True):
            with patch('backend.sources.podcast.source.PodcastDataService') as mock_data_class:
                mock_data = AsyncMock()
                mock_data.get_setting = AsyncMock(return_value=1.0)
                mock_data_class.return_value = mock_data

                with patch('backend.sources.podcast.source.TaddyAPI') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.sources.podcast.source.MpvController') as mock_mpv_class:
                        mock_mpv = Mock()
                        mock_mpv.connect = AsyncMock(return_value=False)
                        mock_mpv.disconnect = AsyncMock()
                        mock_mpv_class.return_value = mock_mpv

                        with patch.object(podcast_source, '_cleanup', new_callable=AsyncMock):
                            result = await podcast_source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, podcast_source):
        """Test successful stop."""
        # Setup mocked state
        podcast_source._mpv = Mock()
        podcast_source._mpv.disconnect = AsyncMock()
        podcast_source._taddy_api = Mock()
        podcast_source._taddy_api.close = AsyncMock()
        podcast_source._podcast_data = Mock()
        podcast_source._monitor_task = None
        podcast_source._progress_save_task = None
        podcast_source._current_episode = None

        with patch.object(podcast_source, '_stop_service', return_value=True):
            result = await podcast_source.stop()

        assert result is True


class TestPodcastSourceStatus:
    """Test PodcastSource status method."""

    @pytest.mark.asyncio
    async def test_status_no_playback(self, podcast_source):
        """Test status with no playback."""
        podcast_source._mpv = None
        podcast_source._podcast_data = Mock()

        status = await podcast_source.status()

        assert "state" in status
        assert status["mpv_connected"] is False
        assert status["is_playing"] is False
        assert status["position"] == 0
        assert status["duration"] == 0

    @pytest.mark.asyncio
    async def test_status_with_playback(self, podcast_source):
        """Test status with active playback."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.is_connected = True
        podcast_source._mpv.is_playing = AsyncMock(return_value=True)
        podcast_source._current_episode = {
            "uuid": "test-episode",
            "name": "Test Episode"
        }
        podcast_source._is_playing = True
        podcast_source._position = 120
        podcast_source._duration = 3600
        podcast_source._playback_speed = 1.5
        podcast_source._podcast_data = Mock()

        status = await podcast_source.status()

        assert status["mpv_connected"] is True
        assert status["is_playing"] is True
        assert status["current_episode"]["name"] == "Test Episode"
        assert status["position"] == 120
        assert status["duration"] == 3600
        assert status["playback_speed"] == 1.5


class TestPodcastSourceCommands:
    """Test PodcastSource command handling."""

    @pytest.mark.asyncio
    async def test_play_episode_command(self, podcast_source):
        """Test play_episode command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.load_stream = AsyncMock(return_value=True)
        podcast_source._mpv.get_property = AsyncMock(return_value=False)
        podcast_source._mpv.set_property = AsyncMock()
        podcast_source._mpv.is_playing = AsyncMock(return_value=False)
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.get_playback_progress = AsyncMock(return_value=None)
        podcast_source._podcast_data.set_setting = AsyncMock(return_value=True)
        podcast_source._taddy_api = Mock()
        podcast_source._taddy_api.get_episode = AsyncMock(return_value={
            "uuid": "test-uuid",
            "name": "Test Episode",
            "audio_url": "http://stream.url",
            "duration": 3600
        })

        result = await podcast_source.command("play_episode", {"episode_uuid": "test-uuid"})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_pause_command(self, podcast_source):
        """Test pause command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.pause = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._is_playing = True
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.update_playback_progress = AsyncMock(return_value=True)

        result = await podcast_source.command("pause", {})

        assert result["success"] is True
        assert podcast_source._is_playing is False

    @pytest.mark.asyncio
    async def test_resume_command(self, podcast_source):
        """Test resume command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.resume = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._is_playing = False

        result = await podcast_source.command("resume", {})

        assert result["success"] is True
        assert podcast_source._is_playing is True

    @pytest.mark.asyncio
    async def test_seek_command(self, podcast_source):
        """Test seek command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.seek = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.update_playback_progress = AsyncMock(return_value=True)

        result = await podcast_source.command("seek", {"position": 300})

        assert result["success"] is True
        assert podcast_source._position == 300

    @pytest.mark.asyncio
    async def test_stop_playback_command(self, podcast_source):
        """Test stop command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.stop = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.update_playback_progress = AsyncMock(return_value=True)
        podcast_source._progress_save_task = None

        result = await podcast_source.command("stop", {})

        assert result["success"] is True
        assert podcast_source._current_episode is None
        assert podcast_source._is_playing is False

    @pytest.mark.asyncio
    async def test_set_speed_command(self, podcast_source):
        """Test set_speed command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.set_property = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.set_setting = AsyncMock(return_value=True)

        result = await podcast_source.command("set_speed", {"speed": 1.5})

        assert result["success"] is True
        assert podcast_source._playback_speed == 1.5

    @pytest.mark.asyncio
    async def test_set_speed_invalid_rounds_to_nearest(self, podcast_source):
        """Test set_speed with invalid value rounds to nearest."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.set_property = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.set_setting = AsyncMock(return_value=True)

        result = await podcast_source.command("set_speed", {"speed": 1.3})

        assert result["success"] is True
        assert podcast_source._playback_speed == 1.25  # Nearest valid

    @pytest.mark.asyncio
    async def test_unknown_command(self, podcast_source):
        """Test unknown command returns error."""
        result = await podcast_source.command("unknown_cmd", {})

        assert result["success"] is False
        assert "error" in result


class TestPodcastDataService:
    """Test PodcastDataService."""

    @pytest.mark.asyncio
    async def test_initial_structure(self):
        """Test initial data structure."""
        service = PodcastDataService()
        structure = service._get_default_structure()

        assert "subscriptions" in structure
        assert "playback_progress" in structure
        assert "settings" in structure
        assert structure["subscriptions"] == []
        assert structure["playback_progress"] == {}

    def test_validate_required_keys_passes_on_full_structure(self):
        """Validation accepts a complete top-level shape."""
        service = PodcastDataService()
        service._validate_required_keys(service._get_default_structure())

    def test_validate_required_keys_raises_on_missing_keys(self):
        """Validation fails loud when a top-level key is missing."""
        service = PodcastDataService()
        with pytest.raises(RuntimeError, match="missing required keys"):
            service._validate_required_keys({"subscriptions": []})

    @pytest.mark.asyncio
    async def test_initialize_seeds_defaults_on_fresh_install(self, tmp_path):
        """Fresh install (no file): initialize() seeds defaults stamped with schema_version."""
        service = PodcastDataService()
        service._data_file = tmp_path / "podcast_data.json"

        await service.initialize()

        assert service._data_file.exists()
        payload = json.loads(service._data_file.read_text())
        assert payload["schema_version"] == PodcastDataService.SCHEMA_VERSION
        assert payload["subscriptions"] == []
        assert payload["playback_progress"] == {}
        assert payload["settings"]["playback_speed"] == 1.0

    @pytest.mark.asyncio
    async def test_initialize_raises_on_schema_mismatch(self, tmp_path):
        """Existing file without schema_version triggers SchemaVersionMismatch."""
        service = PodcastDataService()
        service._data_file = tmp_path / "podcast_data.json"
        service._data_file.write_text(json.dumps({"subscriptions": []}))

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_settings_defaults(self):
        """Test default settings."""
        service = PodcastDataService()
        structure = service._get_default_structure()

        assert structure["settings"]["playback_speed"] == 1.0


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_episode(self, podcast_source):
        """Test state is WAITING with no episode."""
        podcast_source._current_episode = None
        podcast_source._update_connection_state()

        assert podcast_source.state == SourceState.WAITING

    def test_update_state_with_episode(self, podcast_source):
        """Test state is ACTIVE with episode."""
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._is_playing = True
        podcast_source._position = 60
        podcast_source._duration = 3600
        podcast_source._podcast_data = Mock()
        podcast_source._update_connection_state()

        assert podcast_source.state == SourceState.ACTIVE


class TestPlaybackMetadata:
    """Test playback metadata building."""

    def test_build_metadata_no_episode(self, podcast_source):
        """Test metadata is empty with no episode."""
        podcast_source._current_episode = None

        metadata = podcast_source._build_playback_metadata()

        assert metadata == {}

    def test_build_metadata_with_episode(self, podcast_source):
        """Test metadata includes episode info."""
        podcast_source._current_episode = {
            "uuid": "test-uuid",
            "name": "Test Episode",
            "description": "Test description",
            "image_url": "http://image.url",
            "podcast": {
                "uuid": "podcast-uuid",
                "name": "Test Podcast"
            }
        }
        podcast_source._is_playing = True
        podcast_source._is_buffering = False
        podcast_source._position = 120
        podcast_source._duration = 3600
        podcast_source._playback_speed = 1.5

        metadata = podcast_source._build_playback_metadata()

        assert metadata["episode_uuid"] == "test-uuid"
        assert metadata["episode_name"] == "Test Episode"
        assert metadata["podcast_name"] == "Test Podcast"
        # position/duration emitted in milliseconds (shared wire convention).
        assert metadata["position"] == 120000
        assert metadata["duration"] == 3600000
        assert metadata["is_playing"] is True
        assert metadata["is_buffering"] is False
        assert metadata["playback_speed"] == 1.5


class TestProperties:
    """Test public properties."""

    def test_current_episode_property(self, podcast_source):
        """Test current_episode property."""
        podcast_source._current_episode = {"uuid": "test"}
        assert podcast_source.current_episode == {"uuid": "test"}

    def test_is_playing_property(self, podcast_source):
        """Test is_playing property."""
        podcast_source._is_playing = True
        assert podcast_source.is_playing is True

    def test_is_buffering_property(self, podcast_source):
        """Test is_buffering property."""
        podcast_source._is_buffering = True
        assert podcast_source.is_buffering is True

    def test_position_property(self, podcast_source):
        """Test position property."""
        podcast_source._position = 120
        assert podcast_source.position == 120

    def test_duration_property(self, podcast_source):
        """Test duration property."""
        podcast_source._duration = 3600
        assert podcast_source.duration == 3600

    def test_playback_speed_property(self, podcast_source):
        """Test playback_speed property."""
        podcast_source._playback_speed = 1.5
        assert podcast_source.playback_speed == 1.5
