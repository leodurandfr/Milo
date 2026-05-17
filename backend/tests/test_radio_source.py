# backend/tests/test_radio_source.py
"""
Unit tests for RadioSource (features/radio/source.py).

Tests cover:
- AudioSource Protocol compliance
- Lifecycle (start, stop, restart)
- Status format
- Command handling
- Station data operations
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.radio.source import RadioSource
from backend.sources.radio.data import StationDataService, ImageManager
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState


@pytest.fixture
def config():
    """Default Radio source config."""
    return {
        "mpv_socket": "/tmp/test-radio-ipc.sock"
    }


@pytest.fixture
def radio_source(config):
    """Create RadioSource with mocked components."""
    source = RadioSource(config)

    # Mock service manager
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)

    return source


class TestBaseClassCompliance:
    """Test that RadioSource extends BaseAudioSource correctly."""

    def test_extends_base_audio_source(self, radio_source):
        """Test RadioSource extends BaseAudioSource."""
        assert isinstance(radio_source, BaseAudioSource)

    def test_has_required_attributes(self, radio_source):
        """Test required attributes exist."""
        assert radio_source.source_id == "radio"
        assert radio_source.service_name == "milo-radio.service"

    def test_has_required_methods(self, radio_source):
        """Test required methods exist."""
        assert hasattr(radio_source, 'start')
        assert hasattr(radio_source, 'stop')
        assert hasattr(radio_source, 'restart')
        assert hasattr(radio_source, 'status')
        assert hasattr(radio_source, 'command')


class TestRadioSourceConfig:
    """Test RadioSource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = RadioSource()

        assert source._mpv_socket == "/run/milo/radio-ipc.sock"

    def test_custom_config(self):
        """Test custom configuration."""
        config = {"mpv_socket": "/custom/socket.sock"}
        source = RadioSource(config)

        assert source._mpv_socket == "/custom/socket.sock"


class TestRadioSourceLifecycle:
    """Test RadioSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self, radio_source):
        """Test successful start."""
        # Mock dependencies
        with patch.object(radio_source, '_start_service', return_value=True):
            with patch('backend.sources.radio.source.StationDataService') as mock_data_class:
                mock_data = AsyncMock()
                mock_data.initialize = AsyncMock()
                mock_data.get_stats = Mock(return_value={'favorites_count': 0})
                mock_data_class.return_value = mock_data

                with patch('backend.sources.radio.source.RadioBrowserAPI') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.sources.radio.source.MpvController') as mock_mpv_class:
                        mock_mpv = Mock()
                        mock_mpv.connect = AsyncMock(return_value=True)
                        mock_mpv.is_connected = True
                        mock_mpv_class.return_value = mock_mpv

                        result = await radio_source.start()

        assert result is True

    @pytest.mark.asyncio
    async def test_start_mpv_connection_failure(self, radio_source):
        """Test start fails if MPV connection fails."""
        with patch.object(radio_source, '_start_service', return_value=True):
            with patch('backend.sources.radio.source.StationDataService') as mock_data_class:
                mock_data = AsyncMock()
                mock_data.initialize = AsyncMock()
                mock_data_class.return_value = mock_data

                with patch('backend.sources.radio.source.RadioBrowserAPI') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.sources.radio.source.MpvController') as mock_mpv_class:
                        mock_mpv = Mock()
                        mock_mpv.connect = AsyncMock(return_value=False)
                        mock_mpv.disconnect = AsyncMock()
                        mock_mpv_class.return_value = mock_mpv

                        with patch.object(radio_source, '_cleanup', new_callable=AsyncMock):
                            result = await radio_source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, radio_source):
        """Test successful stop."""
        # Setup mocked state
        radio_source._mpv = Mock()
        radio_source._mpv.disconnect = AsyncMock()
        radio_source._radio_api = Mock()
        radio_source._radio_api.close = AsyncMock()
        radio_source._station_data = Mock()
        radio_source._monitor_task = None

        with patch.object(radio_source, '_stop_service', return_value=True):
            result = await radio_source.stop()

        assert result is True


class TestRadioSourceStatus:
    """Test RadioSource status method."""

    @pytest.mark.asyncio
    async def test_status_no_playback(self, radio_source):
        """Test status with no playback."""
        radio_source._mpv = None
        radio_source._station_data = Mock()
        radio_source._station_data.get_stats = Mock(return_value={
            'favorites_count': 5
        })

        status = await radio_source.status()

        assert "state" in status
        assert status["mpv_connected"] is False
        assert status["is_playing"] is False
        assert status["favorites_count"] == 5

    @pytest.mark.asyncio
    async def test_status_with_playback(self, radio_source):
        """Test status with active playback."""
        radio_source._mpv = Mock()
        radio_source._mpv.is_connected = True
        radio_source._mpv.is_playing = AsyncMock(return_value=True)
        radio_source._current_station = {
            "id": "test-station",
            "name": "Test Radio"
        }
        radio_source._is_playing = True
        radio_source._station_data = Mock()
        radio_source._station_data.get_stats = Mock(return_value={
            'favorites_count': 0
        })

        status = await radio_source.status()

        assert status["mpv_connected"] is True
        assert status["is_playing"] is True
        assert status["current_station"]["name"] == "Test Radio"


class TestRadioSourceCommands:
    """Test RadioSource command handling."""

    @pytest.mark.asyncio
    async def test_play_station_command(self, radio_source):
        """Test play_station command."""
        radio_source._mpv = Mock()
        radio_source._mpv.load_stream = AsyncMock(return_value=True)
        radio_source._station_data = Mock()
        radio_source._station_data.is_favorite = Mock(return_value=False)
        radio_source._radio_api = Mock()
        radio_source._radio_api.get_station_by_id = AsyncMock(return_value={
            "id": "test-id",
            "name": "Test Station",
            "url": "http://stream.url"
        })
        radio_source._radio_api.increment_station_clicks = AsyncMock()
        radio_source._radio_api.find_alternative_urls = AsyncMock(return_value=[])

        result = await radio_source.command("play_station", {"station_id": "test-id"})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_stop_playback_command(self, radio_source):
        """Test stop_playback command."""
        radio_source._mpv = Mock()
        radio_source._mpv.stop = AsyncMock(return_value=True)
        radio_source._current_station = {"name": "Test"}

        result = await radio_source.command("stop_playback", {})

        assert result["success"] is True
        assert radio_source._current_station is None
        assert radio_source._is_playing is False

    @pytest.mark.asyncio
    async def test_add_favorite_command(self, radio_source):
        """Test add_favorite command."""
        radio_source._station_data = Mock()
        radio_source._station_data.add_favorite = AsyncMock(return_value=True)
        radio_source._radio_api = Mock()
        radio_source._radio_api.get_station_by_id = AsyncMock(return_value={
            "id": "test-id",
            "name": "Test Station"
        })

        result = await radio_source.command("add_favorite", {"station_id": "test-id"})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_remove_favorite_command(self, radio_source):
        """Test remove_favorite command."""
        radio_source._station_data = Mock()
        radio_source._station_data.remove_favorite = AsyncMock(return_value=True)

        result = await radio_source.command("remove_favorite", {"station_id": "test-id"})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_unknown_command(self, radio_source):
        """Test unknown command returns error."""
        result = await radio_source.command("unknown_cmd", {})

        assert result["success"] is False
        assert "error" in result


class TestStationDataService:
    """Test StationDataService."""

    @pytest.mark.asyncio
    async def test_initial_state(self):
        """Test initial state of StationDataService."""
        service = StationDataService()

        assert service._favorites == []
        assert service._manual_stations == {}

    @pytest.mark.asyncio
    async def test_is_favorite(self):
        """Test is_favorite method."""
        service = StationDataService()
        service._favorites = ["station-1", "station-2"]

        assert service.is_favorite("station-1") is True
        assert service.is_favorite("station-3") is False

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test get_stats method."""
        service = StationDataService()
        service._favorites = ["s1", "s2", "s3"]
        service._manual_stations = {"c1": {}, "c2": {}}
        service._modified_metadata = {"m1": {}}

        stats = service.get_stats()

        assert stats["favorites_count"] == 3
        assert stats["manual_stations_count"] == 2
        assert stats["modified_metadata_count"] == 1

    @pytest.mark.asyncio
    async def test_enrich_with_favorite_status(self):
        """Test enrich_with_favorite_status method."""
        service = StationDataService()
        service._favorites = ["fav-1"]

        stations = [
            {"id": "fav-1", "name": "Favorite"},
            {"id": "other-1", "name": "Other"}
        ]

        enriched = service.enrich_with_favorite_status(stations)

        assert enriched[0]["is_favorite"] is True
        assert enriched[1]["is_favorite"] is False


class TestImageManager:
    """Test ImageManager."""

    def test_allowed_extensions(self):
        """Test allowed image extensions."""
        manager = ImageManager()

        assert ".jpg" in manager.ALLOWED_EXTENSIONS
        assert ".jpeg" in manager.ALLOWED_EXTENSIONS
        assert ".png" in manager.ALLOWED_EXTENSIONS
        assert ".webp" in manager.ALLOWED_EXTENSIONS
        assert ".gif" in manager.ALLOWED_EXTENSIONS

    def test_max_file_size(self):
        """Test max file size configuration."""
        manager = ImageManager()

        assert manager.MAX_FILE_SIZE_MB == 5
        assert manager.MAX_FILE_SIZE_BYTES == 5 * 1024 * 1024


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_station(self, radio_source):
        """Test state is WAITING with no station."""
        radio_source._current_station = None
        radio_source._update_connection_state()

        assert radio_source.state == SourceState.WAITING

    def test_update_state_with_station(self, radio_source):
        """Test state is ACTIVE with station."""
        radio_source._current_station = {"id": "test", "name": "Test"}
        radio_source._is_playing = True
        radio_source._station_data = Mock()
        radio_source._station_data.is_favorite = Mock(return_value=False)
        radio_source._update_connection_state()

        assert radio_source.state == SourceState.ACTIVE


class TestPlaybackMetadata:
    """Test playback metadata building."""

    def test_build_metadata_no_station(self, radio_source):
        """Test metadata is empty with no station."""
        radio_source._current_station = None

        metadata = radio_source._build_playback_metadata()

        assert metadata == {}

    def test_build_metadata_with_station(self, radio_source):
        """Test metadata includes station info."""
        radio_source._current_station = {
            "id": "test-id",
            "name": "Test Station",
            "url": "http://stream.url",
            "country": "France",
            "genre": "Rock"
        }
        radio_source._is_playing = True
        radio_source._is_buffering = False
        radio_source._station_data = Mock()
        radio_source._station_data.is_favorite = Mock(return_value=True)

        metadata = radio_source._build_playback_metadata()

        assert metadata["station_id"] == "test-id"
        assert metadata["station_name"] == "Test Station"
        assert metadata["country"] == "France"
        assert metadata["genre"] == "Rock"
        assert metadata["is_playing"] is True
        assert metadata["is_buffering"] is False
        assert metadata["is_favorite"] is True
