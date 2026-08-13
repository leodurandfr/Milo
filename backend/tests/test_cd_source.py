# backend/tests/test_cd_source.py
"""Unit tests for CdSource (sources/cd/source.py).

Covers the behaviors whose regression would be silent: disc insertion/
ejection detection via the permanent watcher, TOC -> sector-offset math,
state transitions (READY/ACTIVE + idle vs playing metadata projection),
command dispatch (play/pause/resume/next/prev/seek/eject), the monitor
tick's auto-advance/album-end detection, and the MusicBrainz-unreachable
offline fallback. The ioctl reader thread and mpv IPC are mocked — no real
device, FIFO, or subprocess is touched.
"""
import threading

import pytest
from unittest.mock import AsyncMock, Mock, call, patch

from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.cd.data import CDS_DISC_OK, CDS_DRIVE_NOT_READY, CdDataService
from backend.sources.cd.models import DiscInfo, PlayTrackParams, SeekParams, TrackInfo
from backend.sources.cd.reader import SECTORS_PER_SECOND
from backend.sources.cd.source import (
    CD_PREV_RESTART_THRESHOLD_S,
    TOC_READ_ATTEMPTS,
    CdSource,
)


@pytest.fixture
def config():
    return {"mpv_socket": "/tmp/test-cd-ipc.sock"}


@pytest.fixture
def source(config):
    """CdSource with a mocked service manager. No state_machine by default —
    tests that need watcher broadcasts attach one explicitly via _with_state_machine.
    """
    src = CdSource(config)
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    return src


def _mpv(**overrides):
    """An mpv mock with async transport methods used by the reader+mpv orchestration."""
    mpv = Mock()
    mpv.is_connected = True
    mpv.connect = AsyncMock(return_value=True)
    mpv.ensure_connected = AsyncMock(return_value=True)
    mpv.load_stream = AsyncMock(return_value=True)
    mpv.set_property = AsyncMock(return_value=True)
    mpv.wait_until_advancing = AsyncMock(return_value=True)
    mpv.get_property = AsyncMock(return_value=None)
    mpv.pause = AsyncMock(return_value=True)
    mpv.resume = AsyncMock(return_value=True)
    mpv.stop = AsyncMock(return_value=True)
    mpv.disconnect = AsyncMock(return_value=True)
    for key, value in overrides.items():
        setattr(mpv, key, value)
    return mpv


def _with_state_machine(source, active=AudioSource.CD):
    """Attach a state_machine mock + close-coroutine _bg spy, mirroring the
    pattern in test_radio_source.py — avoids leaking un-awaited background
    tasks from set_state()'s fire-and-forget update_source_state spawn.
    """
    source.state_machine = Mock()
    source.state_machine.broadcast = AsyncMock()
    source.state_machine.system_state = Mock(active_source=active)
    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    return source.state_machine


TRACKS = [
    TrackInfo(number=1, title="One", duration=200),
    TrackInfo(number=2, title="Two", duration=150),
    TrackInfo(number=3, title="Three", duration=180),
]

DISC = DiscInfo(
    disc_id="disc-1",
    album="Album",
    artist="Artist",
    year="2000",
    cover_url="/api/cd/cover/disc-1",
    track_count=3,
    total_duration=530,
    tracks=TRACKS,
)


class TestCompliance:
    def test_identity(self, source):
        assert source.source_id == "cd"
        assert source.service_name == "milo-cd.service"

    def test_commands_registered(self, source):
        for cmd in ("play_track", "pause", "resume", "next",
                    "prev", "seek", "eject"):
            assert cmd in source.COMMANDS

class TestDiscWatcher:
    """Insertion/ejection detection via the permanent _check_drive_and_disc poll."""

    @pytest.mark.asyncio
    async def test_drive_disconnect_clears_disc_state(self, source):
        sm = _with_state_machine(source, active=AudioSource.CD)
        source._drive_connected = True
        source._disc_present = True
        source._current_disc = DISC
        source._tracks = TRACKS
        source._last_disc_id = "disc-1"
        source._data_service = Mock()
        source._data_service.probe_drive_and_disc = Mock(return_value=(False, -1))

        await source._check_drive_and_disc()

        assert source._drive_connected is False
        assert source._disc_present is False
        assert source._current_disc is None
        assert source._tracks == []
        assert source._last_disc_id is None
        sm.broadcast.assert_awaited()

    @pytest.mark.asyncio
    async def test_disc_detected_phase1_shows_loading_indicator(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._drive_connected = True
        source._disc_present = False
        source._data_service = Mock()
        source._data_service.probe_drive_and_disc = Mock(
            return_value=(True, CDS_DRIVE_NOT_READY)
        )

        await source._check_drive_and_disc()

        assert source._disc_present is True
        assert source._disc_ready is False
        assert source.state == SourceState.READY
        source.state_machine.broadcast.assert_awaited()

    @pytest.mark.asyncio
    async def test_disc_removed_clears_state(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._drive_connected = True
        source._disc_present = True
        source._current_disc = DISC
        source._tracks = TRACKS
        source._last_disc_id = "disc-1"
        source._sector_offsets = [0, 15000]
        source._disc_end_lba = 30000
        source._data_service = Mock()
        # status 1 == disc absent (not NOT_READY, not DISC_OK)
        source._data_service.probe_drive_and_disc = Mock(return_value=(True, 1))

        await source._check_drive_and_disc()

        assert source._disc_present is False
        assert source._current_disc is None
        assert source._tracks == []
        assert source._last_disc_id is None
        assert source._sector_offsets == []
        assert source._disc_end_lba == 0

    @pytest.mark.asyncio
    async def test_transient_toc_read_failure_is_retried_next_tick(self, source):
        """The drive can report CDS_DISC_OK while a TOC read still fails —
        it is only settling. Latching `_disc_ready` before the read succeeded
        left the disc marked as handled with no sector offsets: shown as
        present, never playable, until the user ejected and reinserted it.
        """
        _with_state_machine(source, active=None)
        source._drive_connected = True
        source._data_service = Mock()
        source._data_service.probe_drive_and_disc = Mock(
            return_value=(True, CDS_DISC_OK)
        )
        source._data_service.read_disc = AsyncMock(
            side_effect=[None, ("disc-1", "toc", [{"offset": 0}, {"offset": 15000}], 30000)]
        )

        await source._check_drive_and_disc()
        assert source._disc_ready is False
        assert source._sector_offsets == []

        await source._check_drive_and_disc()
        assert source._disc_ready is True
        assert source._sector_offsets == [0, 15000]

    @pytest.mark.asyncio
    async def test_toc_read_gives_up_after_max_attempts(self, source):
        """A genuinely unreadable disc must stop re-spinning the drive every
        tick for as long as it sits in the tray — the retry above is capped.
        """
        _with_state_machine(source, active=None)
        source._drive_connected = True
        source._data_service = Mock()
        source._data_service.probe_drive_and_disc = Mock(
            return_value=(True, CDS_DISC_OK)
        )
        source._data_service.read_disc = AsyncMock(return_value=None)

        for _ in range(TOC_READ_ATTEMPTS + 3):
            await source._check_drive_and_disc()

        assert source._data_service.read_disc.await_count == TOC_READ_ATTEMPTS
        assert source._disc_ready is True


class TestDiscReadyFlow:
    """_handle_disc_ready: TOC read always happens; MusicBrainz lookup is
    deferred to activation; races with disc removal must not repopulate state."""

    @pytest.mark.asyncio
    async def test_same_disc_reinserted_reuses_cache_no_network_call(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._last_disc_id = "disc-1"
        source._current_disc = DISC
        source._disc_present = True
        source._mpv = _mpv()
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-1", "toc", [{"number": 1, "duration": 200, "offset": 0}], 15000)
        )
        source._data_service.lookup_metadata = AsyncMock()
        source._auto_play_track_1 = AsyncMock()

        await source._handle_disc_ready()

        source._data_service.lookup_metadata.assert_not_called()
        source._auto_play_track_1.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_disc_while_inactive_defers_metadata_lookup(self, source):
        _with_state_machine(source, active=AudioSource.SPOTIFY)
        source._disc_present = True
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(return_value=(
            "disc-2", "toc",
            [{"number": 1, "duration": 200, "offset": 0}, {"number": 2, "duration": 150, "offset": 15000}],
            30000,
        ))
        source._data_service.lookup_metadata = AsyncMock()

        await source._handle_disc_ready()

        assert source._sector_offsets == [0, 15000]
        assert source._disc_end_lba == 30000
        assert source._last_disc_id == "disc-2"
        source._data_service.lookup_metadata.assert_not_called()
        assert source._current_disc is None

    @pytest.mark.asyncio
    async def test_new_disc_while_active_fetches_metadata_and_autoplays(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._disc_present = True
        source._mpv = _mpv()
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(return_value=(
            "disc-3", "toc",
            [{"number": 1, "duration": 200, "offset": 0}],
            15000,
        ))
        source._data_service.lookup_metadata = AsyncMock(return_value=DISC)
        source._auto_play_track_1 = AsyncMock()

        await source._handle_disc_ready()

        assert source._current_disc == DISC
        assert source._tracks == TRACKS
        source._auto_play_track_1.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offline_fallback_marks_metadata_retry_pending(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._disc_present = True
        source._mpv = _mpv()
        fallback = DiscInfo(disc_id="disc-4", track_count=1, total_duration=200,
                             tracks=[TrackInfo(number=1, title="Track 1", duration=200)])
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-4", "toc", [{"number": 1, "duration": 200, "offset": 0}], 15000)
        )
        source._data_service.lookup_metadata = AsyncMock(return_value=fallback)
        source._auto_play_track_1 = AsyncMock()

        await source._handle_disc_ready()

        assert source._metadata_retry_pending is True

    @pytest.mark.asyncio
    async def test_disc_ejected_during_lookup_is_not_repopulated(self, source):
        """Race guard: the disc may be pulled out while the MusicBrainz await
        is in flight — the callback must not resurrect state for a disc that's
        no longer in the drive."""
        _with_state_machine(source, active=AudioSource.CD)
        source._disc_present = True
        source._mpv = _mpv()
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(return_value=(
            "disc-5", "toc", [{"number": 1, "duration": 200, "offset": 0}], 15000,
        ))

        async def _lookup_then_eject(*args, **kwargs):
            source._disc_present = False
            return DISC

        source._data_service.lookup_metadata = AsyncMock(side_effect=_lookup_then_eject)
        source._auto_play_track_1 = AsyncMock()

        await source._handle_disc_ready()

        assert source._current_disc is None
        source._auto_play_track_1.assert_not_called()


class TestMetadataRetry:
    """_retry_metadata_if_pending: throttled MusicBrainz retry after a fallback lookup."""

    @pytest.mark.asyncio
    async def test_noop_when_nothing_pending(self, source):
        source._metadata_retry_pending = False
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock()

        await source._retry_metadata_if_pending()

        source._data_service.read_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_source_not_active(self, source):
        _with_state_machine(source, active=AudioSource.SPOTIFY)
        source._metadata_retry_pending = True
        source._last_disc_id = "disc-1"
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock()

        await source._retry_metadata_if_pending()

        source._data_service.read_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_while_throttled(self, source):
        from time import monotonic
        _with_state_machine(source, active=AudioSource.CD)
        source._metadata_retry_pending = True
        source._last_disc_id = "disc-1"
        source._metadata_retry_last_attempt = monotonic()  # just happened
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock()

        await source._retry_metadata_if_pending()

        source._data_service.read_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_aborts_when_disc_swapped_meanwhile(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._metadata_retry_pending = True
        source._last_disc_id = "disc-1"
        source._metadata_retry_last_attempt = 0.0
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-2", "toc", [], 0)
        )
        source._data_service.lookup_metadata = AsyncMock()

        await source._retry_metadata_if_pending()

        source._data_service.lookup_metadata.assert_not_called()
        assert source._metadata_retry_pending is True

    @pytest.mark.asyncio
    async def test_success_clears_pending_and_updates_disc(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._disc_present = True
        source._metadata_retry_pending = True
        source._last_disc_id = "disc-1"
        source._metadata_retry_last_attempt = 0.0
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-1", "toc", [], 0)
        )
        source._data_service.lookup_metadata = AsyncMock(return_value=DISC)

        await source._retry_metadata_if_pending()

        assert source._metadata_retry_pending is False
        assert source._current_disc == DISC

    @pytest.mark.asyncio
    async def test_still_fallback_keeps_pending(self, source):
        _with_state_machine(source, active=AudioSource.CD)
        source._metadata_retry_pending = True
        source._last_disc_id = "disc-1"
        source._metadata_retry_last_attempt = 0.0
        source._current_disc = None
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-1", "toc", [], 0)
        )
        source._data_service.lookup_metadata = AsyncMock(
            return_value=DiscInfo(disc_id="disc-1", track_count=0, total_duration=0)
        )

        await source._retry_metadata_if_pending()

        assert source._metadata_retry_pending is True
        assert source._current_disc is None


class TestLbaMath:
    """Pure sector<->position math underlying all navigation (play/seek/prev)."""

    def test_time_pos_to_lba(self, source):
        source._play_start_lba = 1000
        assert source._time_pos_to_lba(2.0) == 1000 + int(2.0 * SECTORS_PER_SECOND)

    def test_lba_to_track_boundaries(self, source):
        source._sector_offsets = [0, 15000, 33000]
        assert source._lba_to_track(0) == 1
        assert source._lba_to_track(14999) == 1
        assert source._lba_to_track(15000) == 2
        assert source._lba_to_track(32999) == 2
        assert source._lba_to_track(33000) == 3
        assert source._lba_to_track(40000) == 3

    def test_lba_to_track_position(self, source):
        source._sector_offsets = [0, 15000, 33000]
        assert source._lba_to_track_position(15750, track=2) == 10.0

    def test_lba_to_track_position_out_of_range_track_is_zero(self, source):
        source._sector_offsets = [0, 15000]
        assert source._lba_to_track_position(15750, track=0) == 0
        assert source._lba_to_track_position(15750, track=5) == 0

    def test_track_position_to_lba_mid_disc(self, source):
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000
        # track 2, 10s in -> 15000 + 750, clamped below track 3's start (33000)
        assert source._track_position_to_lba(2, 10) == 15750

    def test_track_position_to_lba_last_track_clamps_to_disc_end(self, source):
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000
        # way past the end of the last track -> clamped to disc_end_lba - 1
        assert source._track_position_to_lba(3, 1000) == 44999


class TestMetadataAndState:
    def test_build_metadata_without_disc_has_no_album_fields(self, source):
        source._current_disc = None
        meta = source._build_metadata()
        assert "album" not in meta
        assert meta["disc_present"] is False
        assert meta["cache_ready"] is False

    def test_build_metadata_idle_projection_hides_progress(self, source):
        source._current_disc = DISC
        source._tracks = TRACKS
        source._current_track = 1
        source._is_playing = False
        source._is_paused = False

        meta = source._build_metadata()

        assert meta["title"] == "One"
        assert meta["position"] == 0
        assert meta["duration"] == 0

    def test_build_metadata_playing_projection_shows_progress(self, source):
        source._current_disc = DISC
        source._tracks = TRACKS
        source._current_track = 2
        source._track_position = 42.5
        source._is_playing = True

        meta = source._build_metadata()

        assert meta["title"] == "Two"
        assert meta["position"] == 42500
        assert meta["duration"] == 150000

    def test_update_connection_state_ready_when_idle(self, source):
        source._is_playing = False
        source._is_paused = False
        source._is_buffering = False
        source._update_connection_state()
        assert source.state == SourceState.READY

    def test_update_connection_state_active_when_playing(self, source):
        source._is_playing = True
        source._update_connection_state()
        assert source.state == SourceState.ACTIVE

    def test_update_connection_state_active_when_paused(self, source):
        source._is_playing = False
        source._is_paused = True
        source._update_connection_state()
        assert source.state == SourceState.ACTIVE


class TestPlayTrackCommand:
    @pytest.mark.asyncio
    async def test_requires_active_mpv(self, source):
        source._mpv = None
        result = await source._handle_play_track(PlayTrackParams(track_number=1))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_invalid_track_number(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._sector_offsets = [0, 15000, 33000]
        result = await source._handle_play_track(PlayTrackParams(track_number=99))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_requires_toc_read(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._sector_offsets = []
        result = await source._handle_play_track(PlayTrackParams(track_number=1))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_updates_playback_state(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._sector_offsets = [0, 15000, 33000]
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        result = await source._handle_play_track(PlayTrackParams(track_number=2))

        assert result["success"] is True
        assert source._current_track == 2
        assert source._is_playing is True
        assert source._is_paused is False
        assert source._is_buffering is False

    @pytest.mark.asyncio
    async def test_restart_failure_rolls_back_to_previous_track(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._sector_offsets = [0, 15000, 33000]
        source._current_track = 1
        source._track_duration = 200
        source._track_position = 50
        source._restart_reader_and_mpv = AsyncMock(return_value=False)

        result = await source._handle_play_track(PlayTrackParams(track_number=2))

        assert result["success"] is False
        assert source._current_track == 1
        assert source._track_duration == 200
        assert source._track_position == 50


class TestPauseResumeCommands:
    @pytest.mark.asyncio
    async def test_pause_requires_mpv(self, source):
        source._mpv = None
        result = await source._handle_pause()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_pause_success(self, source):
        source._mpv = _mpv()
        source._is_playing = True
        result = await source._handle_pause()
        assert result["success"] is True
        assert source._is_playing is False
        assert source._is_paused is True
        source._mpv.pause.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume_requires_mpv(self, source):
        source._mpv = None
        result = await source._handle_resume()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_resume_from_paused_unblocks_reader(self, source):
        source._mpv = _mpv()
        source._is_paused = True
        result = await source._handle_resume()
        assert result["success"] is True
        assert source._is_playing is True
        assert source._is_paused is False
        source._mpv.resume.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume_already_playing_is_noop(self, source):
        source._mpv = _mpv()
        source._is_playing = True
        source._is_paused = False
        result = await source._handle_resume()
        assert result["success"] is True
        source._mpv.resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_idle_restarts_at_saved_position(self, source):
        source._mpv = _mpv()
        source._is_playing = False
        source._is_paused = False
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000
        source._tracks = TRACKS
        source._current_track = 2
        source._track_position = 10
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        result = await source._handle_resume()

        assert result["success"] is True
        source._restart_reader_and_mpv.assert_awaited_once_with(15750)
        assert source._current_track == 2

    @pytest.mark.asyncio
    async def test_resume_idle_overflowing_track_resets_to_1(self, source):
        source._mpv = _mpv()
        source._is_playing = False
        source._is_paused = False
        source._sector_offsets = [0]
        source._disc_end_lba = 15000
        source._tracks = [TRACKS[0]]
        source._current_track = 5  # stale from a previous, longer disc
        source._track_position = 0
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        result = await source._handle_resume()

        assert result["success"] is True
        assert source._current_track == 1

    @pytest.mark.asyncio
    async def test_resume_idle_without_toc_errors(self, source):
        source._mpv = _mpv()
        source._is_playing = False
        source._is_paused = False
        source._sector_offsets = []
        source._tracks = []
        result = await source._handle_resume()
        assert result["success"] is False


class TestNextPrevTrackCommands:
    @pytest.mark.asyncio
    async def test_next_requires_loaded_disc(self, source):
        source._mpv = _mpv()
        source._current_track = None
        result = await source._handle_next_track()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_next_at_last_track_is_noop(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._current_track = 3
        source._handle_play_track = AsyncMock()
        result = await source._handle_next_track()
        assert result["success"] is True
        source._handle_play_track.assert_not_called()

    @pytest.mark.asyncio
    async def test_next_advances_one_track(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._current_track = 1
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._handle_next_track()

        (params,), _ = source._handle_play_track.call_args
        assert params.track_number == 2

    @pytest.mark.asyncio
    async def test_prev_requires_loaded_disc(self, source):
        source._mpv = _mpv()
        source._current_track = None
        result = await source._handle_prev_track()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_prev_past_threshold_restarts_current_track(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._current_track = 2

        async def _sync():
            source._track_position = CD_PREV_RESTART_THRESHOLD_S + 1

        source._sync_position_from_mpv = AsyncMock(side_effect=_sync)
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._handle_prev_track()

        (params,), _ = source._handle_play_track.call_args
        assert params.track_number == 2

    @pytest.mark.asyncio
    async def test_prev_within_threshold_steps_back(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._current_track = 2

        async def _sync():
            source._track_position = 1

        source._sync_position_from_mpv = AsyncMock(side_effect=_sync)
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._handle_prev_track()

        (params,), _ = source._handle_play_track.call_args
        assert params.track_number == 1

    @pytest.mark.asyncio
    async def test_prev_floors_at_track_1(self, source):
        source._mpv = _mpv()
        source._tracks = TRACKS
        source._current_track = 1

        async def _sync():
            source._track_position = 0

        source._sync_position_from_mpv = AsyncMock(side_effect=_sync)
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._handle_prev_track()

        (params,), _ = source._handle_play_track.call_args
        assert params.track_number == 1


class TestSeekCommand:
    @pytest.mark.asyncio
    async def test_requires_track_playing(self, source):
        source._current_track = None
        result = await source._handle_seek(SeekParams(position_ms=1000))
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_restarts_at_target_lba(self, source):
        source._mpv = _mpv()
        source._current_track = 2
        source._is_playing = True
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        result = await source._handle_seek(SeekParams(position_ms=10000))

        assert result["success"] is True
        source._restart_reader_and_mpv.assert_awaited_once_with(15750, autostart=True)
        assert source._track_position == 10

    @pytest.mark.asyncio
    async def test_failure_reports_error(self, source):
        source._mpv = _mpv()
        source._current_track = 1
        source._sector_offsets = [0, 15000]
        source._disc_end_lba = 30000
        source._restart_reader_and_mpv = AsyncMock(return_value=False)

        result = await source._handle_seek(SeekParams(position_ms=5000))

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_while_paused_moves_the_playhead_without_starting_audio(self, source):
        """Seeking from the paused state — including the preload's parked
        state, where the user has never pressed play — must reload the reader
        at the target with mpv left paused. With the default autostart the disc
        played while the published metadata still said is_playing=False, and the
        monitor tick (gated on _is_playing) left the progress bar frozen.
        """
        source._mpv = _mpv(get_property=AsyncMock(return_value=0.0))
        source._reader = Mock(wait_ready=Mock(return_value=True), is_running=True)
        source._tracks = TRACKS
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000
        source._current_track = 1
        source._is_playing = False
        source._is_paused = True

        result = await source._handle_seek(SeekParams(position_ms=60000))

        assert result["success"] is True
        # Loaded paused and never un-paused: no ("pause", False) on the wire.
        assert source._mpv.set_property.await_args_list == [call("pause", True)]
        assert source._is_paused is True
        assert source._is_playing is False
        assert source._build_metadata()["is_playing"] is False
        assert source._track_position == 60

    @pytest.mark.asyncio
    async def test_while_paused_failure_leaves_the_paused_state(self, source):
        """A failed paused-seek has nothing loaded any more, so the source must
        leave `_is_paused` — otherwise the next play tap takes _handle_resume's
        paused branch and un-pauses a dead mpv instead of restarting the reader.
        """
        source._mpv = _mpv()
        source._current_track = 1
        source._sector_offsets = [0, 15000]
        source._disc_end_lba = 30000
        source._is_playing = False
        source._is_paused = True
        source._restart_reader_and_mpv = AsyncMock(return_value=False)

        result = await source._handle_seek(SeekParams(position_ms=5000))

        assert result["success"] is False
        assert source._is_paused is False
        assert source._is_playing is False


class TestEjectCommand:
    @pytest.mark.asyncio
    async def test_success_clears_disc_but_flag_stays_until_watcher_confirms(self, source):
        """`_ejecting` is intentionally left True on a successful physical
        eject — only the watcher's next poll (disc no longer detected) clears
        it via _reset_playback_state. Resetting it here would make the UI
        flash back to a loaded-disc view before the tray finishes opening."""
        source._mpv = _mpv()
        source._is_playing = True
        source._current_disc = DISC
        source._tracks = TRACKS
        source._stop_reader_and_mpv = AsyncMock()
        proc = Mock()
        proc.wait = AsyncMock()
        proc.returncode = 0

        with patch(
            "backend.sources.cd.source.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ):
            result = await source._handle_eject()

        assert result["success"] is True
        assert source._current_disc is None
        assert source._tracks == []
        assert source._ejecting is True
        source._stop_reader_and_mpv.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_resets_ejecting_flag(self, source):
        source._mpv = _mpv()
        source._is_playing = False
        source._is_paused = False
        proc = Mock()
        proc.wait = AsyncMock()
        proc.returncode = 1
        proc.stderr = Mock()
        proc.stderr.read = AsyncMock(return_value=b"tray busy")

        with patch(
            "backend.sources.cd.source.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ):
            result = await source._handle_eject()

        assert result["success"] is False
        assert source._ejecting is False


class TestTeardownOrder:
    @pytest.mark.asyncio
    async def test_cleanup_lets_mpv_go_before_stopping_the_reader(self, source):
        """The stop path has one order, and this is it.

        A paused mpv holds the FIFO read end open without draining it, so the
        reader thread is blocked in write() and reader.stop()'s 3 s join
        expires — measured on a unit as a 3.1 s switch away from a paused CD,
        plus `CD reader thread did not stop within timeout`. mpv.stop() closes
        the read end and the writer gets its BrokenPipeError, which is why
        _stop_reader_and_mpv (seek, eject) never had the problem.
        """
        order = []
        source._mpv = _mpv(stop=AsyncMock(side_effect=lambda: order.append("mpv")))
        source._reader = Mock(stop=Mock(side_effect=lambda: order.append("reader")))

        await source._cleanup()

        assert order == ["mpv", "reader"]


class TestReaderHandshake:
    @pytest.mark.asyncio
    async def test_the_reader_wait_never_runs_on_the_event_loop_thread(self, source):
        """`wait_ready` blocks for up to 5 s and must block a worker, not the loop.

        It sits on the play path — and on every seek and track change, which is
        where it hurts: on the loop thread it freezes every WS broadcast, HTTP
        handler and monitor tick of the whole appliance for its whole duration.
        Recorded from inside the call, so it states where the work happened
        rather than which helper was used to get there.
        """
        loop_thread = threading.current_thread()
        ran_on = []

        def wait_ready(timeout):
            ran_on.append(threading.current_thread())
            return True

        source._mpv = _mpv()
        source._reader = Mock(wait_ready=wait_ready, start=Mock(), stop=Mock())
        source._disc_end_lba = 45000

        assert await source._start_reader_and_mpv(0, autostart=False) is True

        assert ran_on and ran_on[0] is not loop_thread


class TestMonitorTick:
    @pytest.mark.asyncio
    async def test_noop_when_not_playing(self, source):
        source._is_playing = False
        source._mpv = _mpv()
        await source._on_monitor_tick()
        source._mpv.get_property.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_while_restarting(self, source):
        source._is_playing = True
        source._restarting = True
        source._mpv = _mpv()
        await source._on_monitor_tick()
        source._mpv.get_property.assert_not_called()

    @pytest.mark.asyncio
    async def test_album_finished_on_last_track_eof(self, source):
        source._is_playing = True
        source._is_paused = False
        source._tracks = TRACKS
        source._current_track = 3
        source._mpv = _mpv(get_property=AsyncMock(return_value=None))
        source._reader = Mock(is_running=False)

        await source._on_monitor_tick()

        assert source._is_playing is False
        assert source._current_track == 1
        assert source._track_position == 0
        assert source.state == SourceState.READY

    @pytest.mark.asyncio
    async def test_auto_advances_on_mid_album_reader_eof(self, source):
        source._is_playing = True
        source._tracks = TRACKS
        source._current_track = 1
        source._mpv = _mpv(get_property=AsyncMock(return_value=None))
        source._reader = Mock(is_running=False)
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._on_monitor_tick()

        (params,), _ = source._handle_play_track.call_args
        assert params.track_number == 2

    @pytest.mark.asyncio
    async def test_a_dropped_link_is_not_a_mid_album_track_end(self, source):
        """CD is the only source whose tick *acts* on a failed read.

        A None time-pos means "the disc ran out" only while mpv is there to say
        so. When the link died under the read, auto-advancing restarts the drive
        and mpv against a socket nobody is listening on. The two tests above pin
        the live-mpv EOF paths; the pair states the discriminator.
        """
        source._is_playing = True
        source._tracks = TRACKS
        source._current_track = 1
        source._mpv = _mpv(is_connected=False, get_property=AsyncMock(return_value=None))
        source._reader = Mock(is_running=False)
        source._handle_play_track = AsyncMock(return_value={"success": True})

        await source._on_monitor_tick()

        source._handle_play_track.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_dropped_link_is_not_an_album_end(self, source):
        """...and on the last track it must not settle the source either.

        Settling to READY here is what made CD the one source that never showed
        "Audio stream disconnected": _monitor_loop gates its fallback on
        _state == ACTIVE, and the tick had already shut that gate one pass
        earlier. Consumer: the disconnect banner in the frontend.
        """
        source._is_playing = True
        source._is_paused = False
        source._state = SourceState.ACTIVE
        source._tracks = TRACKS
        source._current_track = 3
        source._mpv = _mpv(is_connected=False, get_property=AsyncMock(return_value=None))
        source._reader = Mock(is_running=False)

        await source._on_monitor_tick()

        assert source._is_playing is True
        assert source.state is SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_buffering_clears_once_time_pos_advances(self, source):
        source._is_playing = True
        source._is_buffering = True
        source._tracks = TRACKS
        source._current_track = 1
        source._sector_offsets = [0, 15000, 33000]
        source._play_start_lba = 0
        source._mpv = _mpv(get_property=AsyncMock(return_value=5.0))

        await source._on_monitor_tick()

        assert source._is_buffering is False
        assert source._track_position == 5.0

    @pytest.mark.asyncio
    async def test_track_boundary_crossed_updates_current_track(self, source):
        source._is_playing = True
        source._tracks = TRACKS
        source._current_track = 1
        source._sector_offsets = [0, 15000, 33000]
        source._play_start_lba = 0
        # time_pos maps past the second track's start LBA
        time_pos = 15100 / SECTORS_PER_SECOND
        source._mpv = _mpv(get_property=AsyncMock(return_value=time_pos))

        await source._on_monitor_tick()

        assert source._current_track == 2
        assert source._track_duration == TRACKS[1].duration

    @pytest.mark.asyncio
    async def test_steady_playback_broadcasts_position_when_due(self, source):
        source._is_playing = True
        source._is_buffering = False
        source._tracks = TRACKS
        source._current_track = 1
        source._sector_offsets = [0, 15000, 33000]
        source._play_start_lba = 0
        source._mpv = _mpv(get_property=AsyncMock(return_value=1.0))
        source._position_sync_due = Mock(return_value=True)
        source.broadcast_position_update = Mock()

        await source._on_monitor_tick()

        source.broadcast_position_update.assert_called_once()


class TestMusicBrainzOfflineFallback:
    """CdDataService.lookup_metadata must degrade to generic track names
    instead of raising when MusicBrainz can't be reached."""

    @pytest.mark.asyncio
    async def test_unreachable_musicbrainz_falls_back_to_toc_only(self):
        service = CdDataService()
        service._cache = {}
        service._lookup_musicbrainz_sync = Mock(
            side_effect=OSError("network unreachable")
        )
        toc_tracks = [
            {"number": 1, "duration": 180, "offset": 0},
            {"number": 2, "duration": 200, "offset": 13500},
        ]

        result = await service.lookup_metadata("disc-offline", "toc-str", toc_tracks)

        assert isinstance(result, DiscInfo)
        assert result.album is None
        assert result.artist is None
        assert result.track_count == 2
        assert result.tracks[0].title == "Track 1"
        assert result.tracks[1].duration == 200

    @pytest.mark.asyncio
    async def test_cache_hit_skips_network_entirely(self):
        service = CdDataService()
        service._cache = {
            "disc-cached": {
                "album": "Cached Album",
                "artist": "Cached Artist",
                "year": "1999",
                "has_cover": False,
                "tracks": [{"number": 1, "title": "T1", "duration": 100}],
            }
        }
        service._lookup_musicbrainz_sync = Mock(
            side_effect=AssertionError("must not hit the network on a cache hit")
        )

        result = await service.lookup_metadata("disc-cached", "toc-str", [])

        assert result.album == "Cached Album"


class _AudioChain:
    """What actually reaches the speakers: the reader thread feeds the FIFO,
    mpv reads it, and mpv's pause property gates the output. Audio flows only
    when all three agree — the module docstring's "NEVER start reader without
    corresponding mpv loadfile" rule, expressed as state.
    """

    def __init__(self, reader_running, loaded, paused):
        self.reader_running = reader_running
        self.loaded = loaded
        self.paused = paused

    @property
    def audible(self):
        return self.reader_running and self.loaded and not self.paused


def _audio_chain(source, reader_running, loaded, paused):
    """Wire an _AudioChain into `source`'s mpv + reader and return it."""
    chain = _AudioChain(reader_running, loaded, paused)

    async def set_property(name, value):
        if name == "pause":
            chain.paused = value
        return True

    async def get_property(name):
        # mpv reports no time-pos once the FIFO read end is closed (EOF).
        return 5.0 if chain.loaded else None

    def reader_start(start_lba, end_lba):
        chain.reader_running = True
        source._reader.is_running = True

    def reader_stop():
        chain.reader_running = False
        source._reader.is_running = False

    def load(_path):
        chain.loaded = True
        return True

    def unload():
        chain.loaded = False
        return True

    def set_paused(value):
        def _apply():
            chain.paused = value
            return True
        return _apply

    source._mpv = _mpv(
        set_property=AsyncMock(side_effect=set_property),
        get_property=AsyncMock(side_effect=get_property),
        load_stream=AsyncMock(side_effect=load),
        stop=AsyncMock(side_effect=unload),
        pause=AsyncMock(side_effect=set_paused(True)),
        resume=AsyncMock(side_effect=set_paused(False)),
    )
    source._reader = Mock(
        start=Mock(side_effect=reader_start),
        stop=Mock(side_effect=reader_stop),
        wait_ready=Mock(return_value=True),
        is_running=reader_running,
    )
    return chain


# Every state a CD source can be sitting in when a command arrives, as
# (is_playing, is_paused, reader running, mpv loaded, mpv paused).
START_STATES = {
    "idle_with_disc": (False, False, False, False, True),
    "preloaded_paused": (False, True, True, True, True),
    "playing": (True, False, True, True, False),
    "paused_mid_track": (False, True, True, True, True),
}

MATRIX_COMMANDS = [
    ("play_track", PlayTrackParams(track_number=2)),
    ("pause", None),
    ("resume", None),
    ("next", None),
    ("prev", None),
    ("seek", SeekParams(position_ms=30000)),
    ("eject", None),
]


def _source_in_state(source, state_name):
    """A CD source with a disc loaded, parked in one of START_STATES."""
    _with_state_machine(source, active=AudioSource.CD)
    is_playing, is_paused, running, loaded, paused = START_STATES[state_name]
    source._current_disc = DISC
    source._tracks = TRACKS
    source._last_disc_id = DISC.disc_id
    source._sector_offsets = [0, 15000, 33000]
    source._disc_end_lba = 45000
    source._drive_connected = True
    source._disc_present = True
    source._disc_ready = True
    source._current_track = 2
    source._track_position = 20
    source._track_duration = TRACKS[1].duration
    source._is_playing = is_playing
    source._is_paused = is_paused
    return _audio_chain(source, running, loaded, paused)


class TestPlaybackFlagMatrix:
    """The audible state and the published state must agree, after every
    command, from every state a disc can be sitting in.

    Seven of this file's commits are one recurring failure: the playback flags
    disagreeing with what the drive is actually doing — a frozen progress bar,
    ACTIVE while idle, a player showing paused while the disc plays. The last
    of those shipped: `seek` restarted the reader with the default autostart
    and never touched the flags.

    These are *relations*, not copies of the handlers' expressions — the test
    knows which flags exist but nothing about which handler sets which. A
    handler added later that forgets them fails here without anyone thinking
    to write a test for it.
    """

    @pytest.mark.parametrize("state_name", list(START_STATES))
    @pytest.mark.parametrize(
        "command,params", MATRIX_COMMANDS, ids=[c for c, _ in MATRIX_COMMANDS]
    )
    @pytest.mark.asyncio
    async def test_published_state_matches_the_audio_chain(
        self, source, state_name, command, params
    ):
        chain = _source_in_state(source, state_name)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc:
            proc.return_value = Mock(wait=AsyncMock(), returncode=0)
            await source._handle_command(command, params)

        published = source._build_metadata()["is_playing"]
        assert published == chain.audible, (
            f"{state_name} + {command}: metadata says is_playing={published} "
            f"while the audio chain is "
            f"{'audible' if chain.audible else 'silent'}"
        )

    @pytest.mark.parametrize("state_name", list(START_STATES))
    @pytest.mark.parametrize(
        "command,params", MATRIX_COMMANDS, ids=[c for c, _ in MATRIX_COMMANDS]
    )
    @pytest.mark.asyncio
    async def test_paused_always_has_something_to_unpause(
        self, source, state_name, command, params
    ):
        """`_handle_resume`'s paused branch un-pauses mpv in place instead of
        restarting the reader, so parking the source in `_is_paused` with no
        stream loaded makes the next play tap a silent no-op that still reports
        playing. No command may leave that state.
        """
        chain = _source_in_state(source, state_name)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc:
            proc.return_value = Mock(wait=AsyncMock(), returncode=0)
            await source._handle_command(command, params)

        assert not source._is_paused or chain.loaded, (
            f"{state_name} + {command}: left _is_paused with nothing loaded"
        )
        assert not (source._is_playing and source._is_paused)
