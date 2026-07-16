# backend/tests/test_radio_source.py
"""
Unit tests for RadioSource (features/radio/source.py).

Tests cover:
- BaseAudioSource compliance
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


class TestStationDataPersistence:
    """WI-6: fail-loud loading — never a silent wipe of favorites."""

    def _service(self, tmp_path):
        from pathlib import Path
        service = StationDataService()
        service._data_file = Path(tmp_path) / "radio_data.json"
        return service

    @pytest.mark.asyncio
    async def test_fresh_install_seeds_versioned_defaults(self, tmp_path):
        """Missing file → defaults written with schema_version stamped."""
        import json
        service = self._service(tmp_path)

        await service.initialize()

        assert service._favorites == []
        assert service._manual_stations == {}
        on_disk = json.loads(service._data_file.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == StationDataService.SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_valid_file_loads_favorites(self, tmp_path):
        """A well-formed versioned file loads its favorites intact."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION,
            "favorites": ["s1", "s2"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        await service.initialize()

        assert service._favorites == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_schema_mismatch_fails_loud(self, tmp_path):
        """A file with the wrong schema_version raises instead of wiping."""
        import json
        from backend.shared.persistence import SchemaVersionMismatch
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION + 1,
            "favorites": ["keep-me"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_missing_schema_version_fails_loud(self, tmp_path):
        """A pre-versioning file (no schema_version) fails loud, never wiped."""
        import json
        from backend.shared.persistence import SchemaVersionMismatch
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "favorites": ["keep-me"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_corrupt_json_fails_loud(self, tmp_path):
        """Invalid JSON raises rather than silently returning empty favorites."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text("{ this is not valid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_missing_required_key_fails_loud(self, tmp_path):
        """A versioned file missing a required top-level key raises."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION,
            "favorites": ["keep-me"],
        }), encoding="utf-8")

        with pytest.raises(RuntimeError, match="missing required keys"):
            await service.initialize()


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


class TestInbandTrackParsing:
    """Test _parse_inband_track (WI-1)."""

    def test_empty_metadata(self):
        from backend.sources.radio.source import _parse_inband_track
        assert _parse_inband_track({}) is None
        assert _parse_inband_track({"icy-name": "Some Station"}) is None
        assert _parse_inband_track({"icy-title": "   "}) is None

    def test_artist_title_split(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Jay-Z - Empire State of Mind"})
        assert track == {
            "title": "Empire State of Mind",
            "artist": "Jay-Z",
            "artwork": None,
        }

    def test_title_only_when_no_separator(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"streamtitle": "Morning News"})
        assert track == {"title": "Morning News", "artist": "", "artwork": None}

    def test_strips_station_promo_suffix(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Bill Evans - Waltz for Debby - WALM Radio on walmradio.com"}
        )
        assert track["artist"] == "Bill Evans"
        assert track["title"] == "Waltz for Debby"

    def test_icy_title_preferred_over_name(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Artist - Song", "icy-name": "Station"}
        )
        assert track["title"] == "Song"

    def test_title_by_artist_split(self):
        # walmradio format: "Title by Artist" (title first, "by" separator).
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Grant's Tune by Grant Green - Adroit Jazz on walmradio.com"}
        )
        assert track["title"] == "Grant's Tune"
        assert track["artist"] == "Grant Green"

    def test_strips_trailing_vinyl_marker(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "So What (Vinyl) by Miles Davis"})
        assert track["title"] == "So What"
        assert track["artist"] == "Miles Davis"

    def test_dash_takes_precedence_over_by(self):
        # A legit "Artist - Title" whose title contains "by" must split on " - ".
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Metallica - Killed by Death"})
        assert track["artist"] == "Metallica"
        assert track["title"] == "Killed by Death"

    def test_meaningful_parenthetical_kept_in_title(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Bill Evans - Waltz (with Trio)"})
        assert track["title"] == "Waltz (with Trio)"

    def test_by_not_split_when_both_sides_single_word(self):
        # " by " is ambiguous with titles that literally contain it. With a
        # single word on each side ("Stand by Me"), keep the title whole rather
        # than invent a wrong artist (a wrong artist is worse than none).
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Stand by Me"})
        assert track["title"] == "Stand by Me"
        assert track["artist"] == ""


class TestResumePlayback:
    """Test resume_playback passes a typed param, not a raw dict (WI-5)."""

    @pytest.mark.asyncio
    async def test_resume_no_last_station(self, radio_source):
        radio_source._last_station = None
        result = await radio_source._handle_resume_playback()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_resume_passes_play_station_params(self, radio_source):
        from backend.sources.radio.models import PlayStationParams
        radio_source._last_station = {"id": "s1", "name": "Resumed"}
        radio_source._handle_play_station = AsyncMock(return_value={"success": True})

        result = await radio_source._handle_resume_playback()

        assert result["success"] is True
        (params,), _ = radio_source._handle_play_station.call_args
        assert isinstance(params, PlayStationParams)
        assert params.station_id == "s1"
        assert params.station == {"id": "s1", "name": "Resumed"}

    @pytest.mark.asyncio
    async def test_resume_last_station_without_id(self, radio_source):
        radio_source._last_station = {"name": "No ID"}
        result = await radio_source._handle_resume_playback()
        assert result["success"] is False


class TestInbandShazamArbitration:
    """Test in-band metadata vs Shazam arbitration (WI-1/WI-2)."""

    @pytest.mark.asyncio
    async def test_inband_overrides_and_stops_shazam(self, radio_source):
        radio_source._current_station = {"id": "s1", "name": "Vinyl", "url": "http://x"}
        radio_source._is_playing = True
        radio_source._station_data = Mock()
        radio_source._station_data.is_favorite = Mock(return_value=False)
        radio_source._mpv = Mock()
        radio_source._mpv.get_metadata = AsyncMock(
            return_value={"icy-title": "Miles Davis - So What"}
        )
        radio_source._shazam = Mock()
        radio_source._shazam.is_running = True
        radio_source._shazam.stop = AsyncMock()
        radio_source._shazam.current_track = None
        radio_source._update_connection_state = Mock()
        # Artwork resolution is spawned here; close the coroutine so it is not
        # left un-awaited (we assert arbitration, not artwork).
        radio_source._bg = Mock()
        radio_source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())

        # Poll only reads every _INBAND_POLL_TICKS ticks.
        from backend.sources.radio.source import _INBAND_POLL_TICKS
        for _ in range(_INBAND_POLL_TICKS):
            await radio_source._poll_inband_metadata()

        assert radio_source._inband_seen is True
        assert radio_source._inband_track["title"] == "So What"
        radio_source._shazam.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recognition_disabled_suppresses_inband(self, radio_source):
        # Per-station opt-out must hide the title even for an in-band station.
        radio_source._current_station = {"id": "s1", "name": "Ambient", "url": "http://x"}
        radio_source._is_playing = True
        radio_source._recognition_enabled = False
        radio_source._mpv = Mock()
        radio_source._mpv.get_metadata = AsyncMock(
            return_value={"icy-title": "Some Artist - Some Song"}
        )
        radio_source._update_connection_state = Mock()
        radio_source._bg = Mock()

        from backend.sources.radio.source import _INBAND_POLL_TICKS
        for _ in range(_INBAND_POLL_TICKS * 2):
            await radio_source._poll_inband_metadata()

        assert radio_source._inband_track is None       # no track resolved
        assert radio_source._inband_seen is False
        radio_source._mpv.get_metadata.assert_not_called()  # gated before IPC read
        radio_source._bg.spawn.assert_not_called()          # no artwork lookup

    @pytest.mark.asyncio
    async def test_shazam_fallback_after_grace(self, radio_source):
        from backend.sources.radio.source import _INBAND_POLL_TICKS, _SHAZAM_GRACE_TICKS
        radio_source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        radio_source._is_playing = True
        radio_source._shazam_candidate = True
        radio_source._mpv = Mock()
        radio_source._mpv.get_metadata = AsyncMock(return_value={})  # no in-band
        radio_source._shazam = Mock()
        radio_source._shazam.is_running = False
        radio_source._start_shazam_fallback = Mock(return_value="coro-sentinel")
        radio_source._bg = Mock()

        # Enough ticks to cross the grace window.
        total_ticks = _INBAND_POLL_TICKS * (_SHAZAM_GRACE_TICKS + 1)
        for _ in range(total_ticks):
            await radio_source._poll_inband_metadata()

        assert radio_source._shazam_candidate is False  # consumed
        radio_source._start_shazam_fallback.assert_called_once_with("http://x")
        radio_source._bg.spawn.assert_called_once()
        assert radio_source._bg.spawn.call_args.args[0] == "coro-sentinel"

    @pytest.mark.asyncio
    async def test_no_shazam_when_not_candidate(self, radio_source):
        from backend.sources.radio.source import _INBAND_POLL_TICKS, _SHAZAM_GRACE_TICKS
        radio_source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        radio_source._is_playing = True
        radio_source._shazam_candidate = False
        radio_source._mpv = Mock()
        radio_source._mpv.get_metadata = AsyncMock(return_value={})
        radio_source._shazam = Mock()
        radio_source._shazam.is_running = False
        radio_source._start_shazam_fallback = Mock(return_value="coro-sentinel")
        radio_source._bg = Mock()

        for _ in range(_INBAND_POLL_TICKS * (_SHAZAM_GRACE_TICKS + 1)):
            await radio_source._poll_inband_metadata()

        radio_source._start_shazam_fallback.assert_not_called()
        radio_source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_inband_stale_clears_after_sustained_silence(self, radio_source):
        # A brief gap keeps the last in-band title; sustained empty metadata
        # (ad/talk/dead air) clears it, so no phantom title stays pinned (#3).
        from backend.sources.radio.source import (
            _INBAND_POLL_TICKS,
            _INBAND_STALE_CLEAR_POLLS,
        )
        radio_source._current_station = {"id": "s1", "name": "Vinyl", "url": "http://x"}
        radio_source._is_playing = True
        radio_source._station_data = Mock()
        radio_source._station_data.is_favorite = Mock(return_value=False)
        radio_source._shazam = None
        radio_source._update_connection_state = Mock()
        radio_source._bg = Mock()
        radio_source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
        radio_source._mpv = Mock()
        radio_source._mpv.get_metadata = AsyncMock(
            return_value={"icy-title": "Miles Davis - So What"}
        )

        # Phase 1: a title arrives and is pinned.
        for _ in range(_INBAND_POLL_TICKS):
            await radio_source._poll_inband_metadata()
        assert radio_source._inband_track["title"] == "So What"

        # Phase 2: empty metadata. A short gap keeps the last title...
        radio_source._mpv.get_metadata = AsyncMock(return_value={})
        for _ in range(_INBAND_POLL_TICKS * (_INBAND_STALE_CLEAR_POLLS - 1)):
            await radio_source._poll_inband_metadata()
        assert radio_source._inband_track is not None

        # ...but sustained silence clears it (still an in-band station).
        for _ in range(_INBAND_POLL_TICKS):
            await radio_source._poll_inband_metadata()
        assert radio_source._inband_track is None
        assert radio_source._inband_seen is True
