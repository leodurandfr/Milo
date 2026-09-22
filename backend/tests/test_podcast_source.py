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
import asyncio
import json

import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.data import PodcastDataService
from backend.core.models.audio_state import SourceState
from backend.shared.persistence import SchemaVersionMismatch


@pytest.fixture
def config():
    """Default Podcast source config."""
    return {
        "mpv_socket": "/tmp/test-podcast-ipc.sock",
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


class TestPodcastSourceConfig:
    """Test PodcastSource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = PodcastSource()

        assert source._mpv_socket == "/run/milo/podcast-ipc.sock"

    def test_custom_config(self):
        """Test custom configuration."""
        source = PodcastSource({"mpv_socket": "/custom/socket.sock"})

        assert source._mpv_socket == "/custom/socket.sock"


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

                with patch('backend.sources.podcast.source.PodcastCatalog') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.shared.mpv_audio_source.MpvController') as mock_mpv_class:
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

                with patch('backend.sources.podcast.source.PodcastCatalog') as mock_api_class:
                    mock_api = AsyncMock()
                    mock_api_class.return_value = mock_api

                    with patch('backend.shared.mpv_audio_source.MpvController') as mock_mpv_class:
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
        podcast_source._podcast_api = Mock()
        podcast_source._podcast_api.close = AsyncMock()
        podcast_source._podcast_data = Mock()
        podcast_source._monitor_task = None
        podcast_source._progress_save_task = None
        podcast_source._current_episode = None

        with patch.object(podcast_source, '_stop_service', return_value=True):
            result = await podcast_source.stop()

        assert result is True


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
        podcast_source._podcast_api = Mock()
        podcast_source._podcast_api.get_episode = AsyncMock(return_value={
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
    async def test_auto_stop_clears_playback(self, podcast_source):
        """The pause-timeout stop saves progress and drops the episode.

        Driven through _auto_stop_action(), the only remaining entry point:
        there is no user-facing stop command (the UI has no stop button).
        """
        podcast_source._mpv = Mock()
        podcast_source._mpv.stop = AsyncMock()
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._position = 300
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.update_playback_progress = AsyncMock(return_value=True)
        podcast_source._progress_save_task = None

        await podcast_source._auto_stop_action()

        assert podcast_source._current_episode is None
        assert podcast_source._is_playing is False
        podcast_source._podcast_data.update_playback_progress.assert_awaited()

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

    @pytest.mark.asyncio
    async def test_resubscribing_refreshes_metadata_without_losing_the_row(
        self, tmp_path
    ):
        """Opening a followed podcast re-posts the subscription with fresh
        metadata. The row must be updated in place — a second row would list the
        podcast twice, and a reset `added_at` would reorder the list under the
        owner."""
        service = PodcastDataService()
        service._data_file = tmp_path / "podcast_data.json"
        await service.initialize()

        await service.add_subscription("1556250107", "Underscore_", "img", "h1")
        first = (await service.get_subscriptions())[0]
        await service.add_subscription("1556250107", "Underscore_ (v2)", "img2", "h2")

        subscriptions = await service.get_subscriptions()
        assert len(subscriptions) == 1
        assert subscriptions[0]["name"] == "Underscore_ (v2)"
        assert subscriptions[0]["children_hash"] == "h2"
        assert subscriptions[0]["added_at"] == first["added_at"]


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_episode(self, podcast_source):
        """Test state is READY with no episode."""
        podcast_source._current_episode = None
        podcast_source._update_connection_state()

        assert podcast_source.state == SourceState.READY

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


class TestTheCommonFloor:
    """Podcast fills title/artist/album/album_art_url like every other source.

    It filled none of them, and unlike radio it was in no consumer's fallback
    either: `core/push/payloads.py` and Milo-iOS both read
    `title | track_title | station_name`, and podcast publishes `episode_name`.
    A playing episode therefore reached the lock screen as a session whose
    every field was null — a card with no track at all. Two copies of one
    display rule, and both of them missed a source; that is what moving the
    rule into the producer is for.
    """

    EPISODE = {
        "uuid": "e1", "name": "Episode 12", "image_url": "https://cdn/ep.jpg",
        "podcast": {"uuid": "p1", "name": "Le Code a changé"},
    }

    def test_a_playing_episode_fills_the_floor(self, podcast_source):
        podcast_source._current_episode = dict(self.EPISODE)
        podcast_source._is_playing = True

        meta = podcast_source._build_playback_metadata()

        assert meta["title"] == "Episode 12"
        assert meta["artist"] == meta["podcast_name"]
        assert meta["album"] == meta["podcast_name"]
        assert meta["album_art_url"] == meta["image_url"]

    def test_a_stopped_episode_publishes_where_it_would_resume(self, podcast_source):
        """An auto-stop leaves an episode and a second to come back to, and
        both belong in the state — the resume point is what a play press uses,
        so publishing 0:00 for a source that resumes at 12:34 would be the
        state disagreeing with the next press."""
        podcast_source._current_episode = dict(self.EPISODE)
        podcast_source._position = 754
        podcast_source._duration = 2100
        podcast_source._remember_for_resume()
        podcast_source._current_episode = None
        podcast_source._position = 0
        podcast_source._duration = 0
        podcast_source._is_playing = False

        meta = podcast_source._build_playback_metadata()

        assert meta["episode_uuid"] == "e1"
        assert meta["title"] == "Episode 12"
        assert meta["is_playing"] is False
        assert meta["position"] == 754_000
        assert meta["duration"] == 2_100_000

    def test_an_episode_that_ended_leaves_nothing_to_resume(self, podcast_source):
        """The distinction the payload has to carry: a stop is a pause that
        gave up, an ending is an ending. The frontend flips the finished card
        to "already listened" off the ending's own keys, and must not also be
        offered it as the thing a play press resumes."""
        podcast_source._current_episode = dict(self.EPISODE)
        podcast_source._remember_for_resume()

        podcast_source._forget_resume()
        podcast_source._current_episode = None

        assert podcast_source._build_playback_metadata() == {}
        assert podcast_source._idle_metadata() == {
            "is_playing": False, "is_buffering": False
        }


class TestEpisodeEndDetection:
    """Test end-of-episode detection in _on_monitor_tick.

    mpv runs with keep-open=no + --idle=yes, so at EOF it unloads the file and
    returns to idle: playback-time → None and idle-active → True. Detection must
    key off idle-active (authoritative) rather than a position-vs-duration
    heuristic, which breaks whenever the reported duration overshoots the real
    end-of-audio (common with VBR podcast MP3s / early-terminated HTTP streams).
    """

    def _mpv_with_props(self, props):
        mpv = Mock()
        mpv.is_connected = True

        async def _get(name):
            return props.get(name)

        mpv.get_property = AsyncMock(side_effect=_get)
        return mpv

    @pytest.mark.asyncio
    async def test_episode_ends_on_idle_even_if_position_short_of_duration(self, podcast_source):
        """EOF (mpv idle) returns to READY and persists completion even when the
        last observed position is far short of the reported duration — the original
        'stuck at the end' bug. The explicit mark_episode_completed forces the
        'already listened' state despite the position-vs-duration heuristic failing
        on the over-reported duration."""
        podcast_source._current_episode = {"uuid": "ep1", "name": "Ep"}
        podcast_source._is_playing = True
        podcast_source._loading = False
        podcast_source._position = 3000   # real end of audio
        podcast_source._duration = 3600   # over-reported duration
        podcast_source._progress_save_task = None
        podcast_source._mpv = self._mpv_with_props(
            {"playback-time": None, "duration": None, "pause": False, "idle-active": True}
        )

        await podcast_source._on_monitor_tick()

        assert podcast_source._current_episode is None
        assert podcast_source._is_playing is False
        assert podcast_source.state == SourceState.READY
        # Final row is saved (so a short clip has a row), then forced completed.
        assert podcast_source._podcast_data.update_playback_progress.await_count == 1
        podcast_source._podcast_data.mark_episode_completed.assert_awaited_once_with("ep1")

    @pytest.mark.asyncio
    async def test_transient_position_none_does_not_end_episode(self, podcast_source):
        """A momentary playback-time=None during a mid-stream cache stall (file
        still loaded → idle-active False) must NOT be mistaken for EOF."""
        podcast_source._current_episode = {"uuid": "ep1", "name": "Ep"}
        podcast_source._is_playing = True
        podcast_source._loading = False
        podcast_source._position = 1800
        podcast_source._duration = 3600
        podcast_source._mpv = self._mpv_with_props(
            {"playback-time": None, "duration": None, "pause": False, "idle-active": False}
        )

        await podcast_source._on_monitor_tick()

        assert podcast_source._current_episode is not None
        assert podcast_source._is_playing is True

    @pytest.mark.asyncio
    async def test_idle_active_during_loading_does_not_end_episode(self, podcast_source):
        """idle-active is True while mpv is still in the load window (before
        _loading is cleared). The _loading guard must return early so the EOF
        path can't fire prematurely — protects against a future refactor moving
        _loading = False ahead of the stream actually playing."""
        podcast_source._current_episode = {"uuid": "ep1", "name": "Ep"}
        podcast_source._is_playing = False
        podcast_source._loading = True
        podcast_source._mpv = self._mpv_with_props(
            {"playback-time": None, "duration": None, "pause": False, "idle-active": True}
        )

        await podcast_source._on_monitor_tick()

        assert podcast_source._current_episode is not None

    @pytest.mark.asyncio
    async def test_normal_end_returns_to_ready(self, podcast_source):
        """Well-behaved file: position reaches duration, mpv idles → READY."""
        podcast_source._current_episode = {"uuid": "ep1", "name": "Ep"}
        podcast_source._is_playing = True
        podcast_source._loading = False
        podcast_source._position = 3599
        podcast_source._duration = 3600
        podcast_source._progress_save_task = None
        podcast_source._mpv = self._mpv_with_props(
            {"playback-time": None, "duration": None, "pause": False, "idle-active": True}
        )

        await podcast_source._on_monitor_tick()

        assert podcast_source._current_episode is None
        assert podcast_source.state == SourceState.READY
        podcast_source._podcast_data.mark_episode_completed.assert_awaited_once_with("ep1")


class TestSwitchingEpisodesGuardsTheOutgoingOne:
    """The _loading guard must be armed before mpv is stopped.

    `await self._mpv.stop()` makes mpv idle while _is_playing and
    _current_episode still point at the outgoing episode. A 1 Hz monitor tick
    landing there used to read that as EOF and mark_episode_completed() the
    outgoing uuid — dropping it from the in-progress queue. Nothing looks
    wrong on screen: the new episode's state is written a few lines later.
    """

    @pytest.mark.asyncio
    async def test_a_tick_during_the_stop_does_not_complete_the_outgoing_episode(
        self, podcast_source
    ):
        entered_stop = asyncio.Event()
        release_stop = asyncio.Event()

        async def gated_stop():
            entered_stop.set()
            await release_stop.wait()

        podcast_source._mpv = Mock()
        podcast_source._mpv.stop = gated_stop
        podcast_source._mpv.load_stream = AsyncMock(return_value=True)
        # What mpv reports once stopped: file unloaded, back to idle.
        podcast_source._mpv.get_property = AsyncMock(side_effect=lambda name: {
            "playback-time": None, "duration": None,
            "pause": False, "idle-active": True,
        }.get(name))
        podcast_source._mpv.set_property = AsyncMock()
        podcast_source._podcast_data.get_playback_progress = AsyncMock(return_value=None)
        podcast_source._podcast_api = Mock()
        podcast_source._podcast_api.get_episode = AsyncMock(return_value={
            "uuid": "incoming", "name": "Incoming", "audio_url": "http://s", "duration": 1200,
        })

        podcast_source._current_episode = {"uuid": "outgoing", "name": "Outgoing"}
        podcast_source._is_playing = True
        podcast_source._loading = False
        podcast_source._position = 300
        podcast_source._duration = 1800

        play = asyncio.create_task(
            podcast_source._handle_play_episode(Mock(episode_uuid="incoming"))
        )
        await asyncio.wait_for(entered_stop.wait(), timeout=1)

        await podcast_source._on_monitor_tick()

        podcast_source._podcast_data.mark_episode_completed.assert_not_awaited()
        assert podcast_source._current_episode is not None

        release_stop.set()
        result = await asyncio.wait_for(play, timeout=1)
        podcast_source._stop_progress_save()

        assert result["success"] is True
        assert podcast_source._current_episode["uuid"] == "incoming"
        assert podcast_source._loading is False


class TestProperties:
    """Test public properties."""

    def test_is_playing_property(self, podcast_source):
        """Test is_playing property."""
        podcast_source._is_playing = True
        assert podcast_source.is_playing is True

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


class TestMpvRefusesTheTransportCommand:
    """mpv answers False whenever its IPC socket is down, and says so only at
    debug level.

    If these fail, a pause/resume/seek/speed the daemon never took is answered
    with `success`, the source flips its own flags and broadcasts them: the UI
    draws a play button over an episode that is still playing, and the progress
    saved on that pause is written for a stream that kept advancing.
    """

    @pytest.fixture
    def refusing(self, podcast_source):
        """A playing episode over an mpv that refuses every transport command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.pause = AsyncMock(return_value=False)
        podcast_source._mpv.resume = AsyncMock(return_value=False)
        podcast_source._mpv.seek = AsyncMock(return_value=False)
        podcast_source._mpv.set_property = AsyncMock(return_value=False)
        podcast_source._current_episode = {"uuid": "test", "name": "Test"}
        podcast_source._is_playing = True
        podcast_source._position = 300
        podcast_source._playback_speed = 1.0
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.update_playback_progress = AsyncMock(return_value=True)
        podcast_source._podcast_data.set_setting = AsyncMock(return_value=True)
        # Broadcasts go through set_state -> _bg.spawn; spy it so "nothing was
        # published" is observable, and close the coroutine so none leaks.
        podcast_source.state_machine = Mock()
        podcast_source._bg = Mock()
        podcast_source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
        return podcast_source

    @pytest.mark.asyncio
    async def test_pause_refused_keeps_the_episode_playing(self, refusing):
        result = await refusing.command("pause", {})

        assert result["success"] is False
        assert refusing._is_playing is True
        refusing._podcast_data.update_playback_progress.assert_not_called()
        refusing._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_refused_keeps_the_episode_paused(self, refusing):
        refusing._is_playing = False

        result = await refusing.command("resume", {})

        assert result["success"] is False
        assert refusing._is_playing is False
        refusing._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_seek_refused_keeps_the_position(self, refusing):
        result = await refusing.command("seek", {"position": 42})

        assert result["success"] is False
        assert refusing._position == 300
        refusing._podcast_data.update_playback_progress.assert_not_called()
        refusing._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_speed_refused_keeps_the_speed_unpersisted(self, refusing):
        result = await refusing.command("set_speed", {"speed": 1.5})

        assert result["success"] is False
        assert refusing._playback_speed == 1.0
        refusing._podcast_data.set_setting.assert_not_called()
        refusing._bg.spawn.assert_not_called()


class TestTransportOnAnIdleSource:
    """Three commands answered a client wrongly while nothing was playing.

    `command()` turns anything `_handle_command` raises into the 400 body, so an
    unguarded `self._mpv` reached the client as "'NoneType' object has no
    attribute 'seek'" — a crash indistinguishable from a refusal without string
    matching. `resume` was the opposite failure: it fell through its own guard
    and reported success while the player stayed silent, which a client cannot
    detect at all.
    """

    @pytest.mark.asyncio
    async def test_resume_with_nothing_ever_played_is_a_refusal_not_a_success(
        self, podcast_source
    ):
        """There is no end state that makes "Resumed" true with nothing loaded
        and nothing to reload. Same phrasing family as radio's "No station to
        resume"."""
        assert podcast_source._current_episode is None
        assert podcast_source._last_episode is None

        result = await podcast_source.command("resume", {})

        assert result["success"] is False
        assert "resume" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_resume_after_an_auto_stop_reopens_the_episode(self, podcast_source):
        """A play press from READY is the case the rotary and the IR remote
        send, and the only name they know is `resume` — playback_dispatch maps
        every non-Spotify transport onto it. Refusing here answered "No episode
        to resume" on a source that was publishing the episode it would resume,
        for every idle timeout. Goes through the play path, so the position
        comes from the durable row rather than a second copy of it.
        """
        podcast_source._last_episode = {"uuid": "e1", "name": "Episode 12"}
        podcast_source._handle_play_episode = AsyncMock(
            return_value={"success": True}
        )

        result = await podcast_source.command("resume", {})

        assert result["success"] is True
        assert podcast_source._handle_play_episode.await_args.args[0].episode_uuid == "e1"

    @pytest.mark.asyncio
    async def test_seek_with_no_session_answers_a_domain_error(self, podcast_source):
        """Unlike pause and stop, a seek has no idempotent reading — there is no
        position to move to — so it refuses, but in words a client can act on."""
        assert podcast_source._mpv is None

        result = await podcast_source.command("seek", {"position": 30})

        assert result["success"] is False
        assert "NoneType" not in str(result)

    @pytest.mark.asyncio
    async def test_set_speed_off_playback_is_stored_for_the_next_episode(self, podcast_source):
        """The speed is a preference re-applied at every play, so setting it with
        nothing loaded is legitimate — only the push to a live session is not.
        This is what lets a widget set the speed without starting an episode."""
        assert podcast_source._mpv is None

        result = await podcast_source.command("set_speed", {"speed": 1.5})

        assert result["success"] is True
        assert podcast_source._playback_speed == 1.5
        podcast_source._podcast_data.set_setting.assert_awaited_once_with(
            "playback_speed", 1.5
        )
