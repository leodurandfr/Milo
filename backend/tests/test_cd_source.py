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
import asyncio
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


class TestPauseDuringARestart:
    """The playback lock must cover the gesture, not just the restart.

    _playback_lock was taken by _restart_reader_and_mpv alone and by no command
    handler, so a pause arriving mid-restart set _is_playing=False /
    _is_paused=True on the mpv the restart was about to replace — and the
    restart's own set_property("pause", False) then un-paused the new one. The
    disc plays behind a UI showing paused, and _on_monitor_tick's first line
    (`if not self._is_playing: return`) means nothing ever corrects it. Up to
    8 s wide: reader.wait_ready 5 s + wait_until_advancing 3 s.
    """

    @pytest.mark.asyncio
    async def test_a_pause_pressed_during_a_restart_is_deferred_not_lost(self, source):
        _with_state_machine(source)

        # Ordered log of every pause-state change that reaches mpv.
        events = []

        async def set_property(name, value):
            if name == "pause":
                events.append("pause" if value else "unpause")
            return True

        async def pause():
            events.append("pause")
            return True

        source._mpv = _mpv(
            set_property=AsyncMock(side_effect=set_property),
            pause=AsyncMock(side_effect=pause),
        )
        source._tracks = TRACKS
        source._sector_offsets = [0, 15000, 33000]
        source._disc_end_lba = 45000

        # Block inside the reader's own wait_ready — the real 5 s window, on the
        # thread it really runs on, so the event loop stays free meanwhile.
        entered = threading.Event()
        finish = threading.Event()

        def wait_ready(timeout):
            entered.set()
            return finish.wait(5)

        source._reader = Mock(wait_ready=wait_ready, start=Mock(), stop=Mock())

        play = asyncio.create_task(
            source._handle_play_track(PlayTrackParams(track_number=1))
        )
        # Bounded: if the restart ever stops waiting on the reader, this spin
        # would never end and the mutation that removed the wait would hang the
        # run instead of reddening it.
        for _ in range(300):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), "the restart never waited for the reader"

        paused = asyncio.create_task(source._handle_pause())
        await asyncio.sleep(0.05)  # the press is in, mid-restart

        finish.set()
        assert (await asyncio.wait_for(play, timeout=5))["success"] is True
        assert (await asyncio.wait_for(paused, timeout=5))["success"] is True

        assert source._is_paused is True
        assert source._is_playing is False
        source._mpv.pause.assert_awaited()  # the press was deferred, not swallowed
        assert events[-1] == "pause", (
            f"published paused while mpv was left un-paused: {events}"
        )


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


class TestMpvRefusesTheTransportCommand:
    """mpv answers False whenever its IPC socket is down, and says so only at
    debug level.

    If these fail, a pause or resume the daemon never took is answered with
    `success` and the source flips its own flags: the UI draws a play button
    over a disc that is still spinning, and `_is_paused` parks the source in a
    state `_handle_resume` will try to un-pause in place.
    """

    @pytest.mark.asyncio
    async def test_pause_refused_keeps_the_disc_playing(self, source):
        source._mpv = _mpv(pause=AsyncMock(return_value=False))
        source._is_playing = True
        _with_state_machine(source)

        result = await source._handle_pause()

        assert result["success"] is False
        assert source._is_playing is True
        assert source._is_paused is False
        source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_refused_leaves_the_source_paused(self, source):
        source._mpv = _mpv(resume=AsyncMock(return_value=False))
        source._is_paused = True
        _with_state_machine(source)

        result = await source._handle_resume()

        assert result["success"] is False
        assert source._is_playing is False
        assert source._is_paused is True
        # The bar was frozen before the refusal; it must not stay frozen.
        assert source._is_buffering is False
        source._mpv.wait_until_advancing.assert_not_called()


# =============================================================================
# Activation, preload, and the reader+mpv handshake.
#
# The preload path had never run: the `_bg.spawn` double above CLOSES the
# coroutine it is handed, which is right for tests that only want to keep an
# un-awaited task from leaking, and wrong for the four methods whose entire job
# happens inside one. `_running_bg` below runs them instead.
# =============================================================================

def _running_bg(source):
    """A `_bg` that actually awaits what it is given, and hands back the tasks.

    Bounded by the caller with `asyncio.wait_for`: every target here can park on
    an mpv double that never answers, and an unbounded await turns a mutation
    into a hang instead of a red.
    """
    # `set_state()` also spawns `update_source_state`, and a plain Mock returns
    # a Mock rather than a coroutine — which the closing double above swallows
    # silently and this one cannot.
    if isinstance(getattr(source, "state_machine", None), Mock):
        source.state_machine.update_source_state = AsyncMock()

    tasks = []

    def spawn(coro, **_kw):
        task = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=spawn)
    source._bg.cancel_all = AsyncMock()
    return tasks


async def _settle(tasks, timeout=2.0):
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)


def _ready_to_play(source, mpv=None):
    """A source holding a disc it has already read: TOC offsets, tracks, mpv."""
    source._mpv = mpv or _mpv()
    source._disc_present = True
    source._last_disc_id = "disc-1"
    source._current_disc = DISC
    source._tracks = TRACKS
    source._sector_offsets = [150, 20000, 40000]
    source._disc_end_lba = 60000
    source._reader = Mock()
    source._reader.start = Mock()
    source._reader.stop = Mock()
    source._reader.wait_ready = Mock(return_value=True)
    source._reader.is_running = True
    return source


class TestActivation:
    """`_do_start` — what happens when the user opens the CD source."""

    async def test_a_pre_started_mpv_connection_is_reused(self, source):
        """`_pre_start_service` runs on insertion, before the user has opened
        the source. Starting the service again would tear down the connection
        the insertion just warmed up, which is the whole point of pre-start."""
        _with_state_machine(source)
        _running_bg(source)
        source._mpv = _mpv()
        source._disc_present = False
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        source._start_service_and_wait = AsyncMock(return_value=True)

        assert await source._do_start() is True
        source._start_service_and_wait.assert_not_awaited()
        source._start_monitor.assert_called_once()

    async def test_a_cold_start_brings_up_the_service_then_connects_ipc(self, source, monkeypatch):
        _with_state_machine(source)
        _running_bg(source)
        source._mpv = None
        source._disc_present = False
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        source._start_service_and_wait = AsyncMock(return_value=True)
        made = _mpv()
        monkeypatch.setattr("backend.sources.cd.source.MpvController", Mock(return_value=made))

        assert await source._do_start() is True
        source._start_service_and_wait.assert_awaited_once()
        made.connect.assert_awaited_once()
        assert source._mpv is made

    async def test_a_service_that_will_not_start_gives_up_before_touching_mpv(
            self, source, monkeypatch):
        _with_state_machine(source)
        _running_bg(source)
        source._mpv = None
        source._start_service_and_wait = AsyncMock(return_value=False)
        controller = Mock()
        monkeypatch.setattr("backend.sources.cd.source.MpvController", controller)

        assert await source._do_start() is False
        controller.assert_not_called()

    async def test_an_ipc_connection_that_fails_aborts_the_start(self, source, monkeypatch):
        _with_state_machine(source)
        _running_bg(source)
        source._mpv = None
        source._start_service_and_wait = AsyncMock(return_value=True)
        monkeypatch.setattr("backend.sources.cd.source.MpvController",
                            Mock(return_value=_mpv(connect=AsyncMock(return_value=False))))

        assert await source._do_start() is False

    async def test_a_disc_already_in_the_tray_is_preloaded_in_the_background(self, source):
        """After `_do_start` returns, not during: the transition has to complete
        so the frontend can show its loader while the drive spins up."""
        _with_state_machine(source)
        tasks = _running_bg(source)
        _ready_to_play(source)
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        source._preload_on_start = AsyncMock()

        assert await source._do_start() is True
        await _settle(tasks)
        source._preload_on_start.assert_awaited_once()

    async def test_an_empty_tray_preloads_nothing(self, source):
        """Settled before asserting: the preload is *spawned*, so a check made
        while its task is still pending cannot tell "never scheduled" from
        "scheduled and not run yet" — and would pass either way."""
        _with_state_machine(source)
        tasks = _running_bg(source)
        source._mpv = _mpv()
        source._disc_present = False
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        source._preload_on_start = AsyncMock()

        assert await source._do_start() is True
        await _settle(tasks)
        source._preload_on_start.assert_not_awaited()

    async def test_a_start_that_blows_up_tears_down_rather_than_half_starting(self, source):
        """Left half-started, the next activation reuses an mpv that is not
        there and the source is dead until the backend restarts."""
        _with_state_machine(source)
        _running_bg(source)
        source._mpv = _mpv()
        source._load_auto_stop_config = AsyncMock(side_effect=RuntimeError("settings gone"))
        source._cleanup = AsyncMock()

        assert await source._do_start() is False
        source._cleanup.assert_awaited_once()


class TestPreloadOnActivation:
    """Track 1 is loaded and *parked paused* when the source is opened, so the
    first tap on play resumes instead of paying the drive spin-up.

    The loader must be held for the whole of it — the metadata lookup and the
    preload — or the play button flashes from loader to play and back.
    """

    async def test_the_loader_is_held_from_activation_through_the_preload(self, source):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        seen = []
        source._update_connection_state = Mock(
            side_effect=lambda: seen.append(source._is_buffering))
        source._preload_track_1 = AsyncMock()

        await asyncio.wait_for(source._preload_on_start(), 2.0)

        assert seen and seen[0] is True, "the loader was not raised on activation"
        source._preload_track_1.assert_awaited_once()

    async def test_a_disc_whose_metadata_is_not_read_yet_is_read_first(self, source):
        """Insertion while the source was closed leaves `_last_disc_id` set and
        `_current_disc` empty; without this the preload has no track list."""
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._current_disc = None
        source._load_disc_metadata = AsyncMock()
        source._preload_track_1 = AsyncMock()

        await asyncio.wait_for(source._preload_on_start(), 2.0)
        source._load_disc_metadata.assert_awaited_once()

    async def test_a_disc_already_read_is_not_read_again(self, source):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._load_disc_metadata = AsyncMock()
        source._preload_track_1 = AsyncMock()

        await asyncio.wait_for(source._preload_on_start(), 2.0)
        source._load_disc_metadata.assert_not_awaited()

    async def test_a_user_who_pressed_play_first_is_not_interrupted(self, source):
        """The lookup takes seconds. A preload landing on top of a track the
        user already started would restart the reader at track 1."""
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._is_playing = True
        source._preload_track_1 = AsyncMock()

        await asyncio.wait_for(source._preload_on_start(), 2.0)

        source._preload_track_1.assert_not_awaited()
        assert source._is_buffering is False, "the loader was left up for ever"

    async def test_leaving_the_source_during_the_lookup_cancels_the_preload(self, source):
        _with_state_machine(source, active=AudioSource.SPOTIFY)
        _running_bg(source)
        _ready_to_play(source)
        source._preload_track_1 = AsyncMock()

        await asyncio.wait_for(source._preload_on_start(), 2.0)

        source._preload_track_1.assert_not_awaited()
        assert source._is_buffering is False

    async def test_a_preload_that_fails_takes_the_loader_down_with_it(self, source):
        """`is_buffering` is what draws the play button as a spinner; left up,
        the source looks permanently busy and the button cannot be pressed."""
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._preload_track_1 = AsyncMock(side_effect=RuntimeError("drive gone"))

        await asyncio.wait_for(source._preload_on_start(), 2.0)
        assert source._is_buffering is False


class TestPreloadingTrackOne:
    async def test_track_one_is_loaded_paused_and_never_un_paused(self, source):
        """The whole contract: a preload that un-pauses emits audio from a
        source the user has not asked to play."""
        _with_state_machine(source)
        _running_bg(source)
        mpv = _mpv()
        _ready_to_play(source, mpv)
        restarts = []
        source._restart_reader_and_mpv = AsyncMock(
            side_effect=lambda lba, autostart=True: restarts.append((lba, autostart)) or True)

        await asyncio.wait_for(source._preload_track_1(), 2.0)

        assert restarts == [(150, False)], "the preload started playback"
        assert source._is_paused is True
        assert source._is_playing is False
        assert source._is_buffering is False
        assert source._current_track == 1
        assert source._track_duration == TRACKS[0].duration

    async def test_a_preload_that_cannot_load_clears_the_loader(self, source):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._restart_reader_and_mpv = AsyncMock(return_value=False)

        await asyncio.wait_for(source._preload_track_1(), 2.0)

        assert source._is_buffering is False
        assert source._is_paused is False, "a failed preload parked the source paused"

    async def test_a_disc_with_no_toc_yet_is_not_preloaded(self, source):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._sector_offsets = []
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        await asyncio.wait_for(source._preload_track_1(), 2.0)
        source._restart_reader_and_mpv.assert_not_awaited()


class TestTheReaderAndMpvHandshake:
    """`_start_reader_and_mpv` is the sequence the drive and mpv must follow.

    Its order is load-bearing at three points, each of which is a separate
    audible failure if it moves.
    """

    async def test_a_down_mpv_link_is_caught_before_the_drive_spins(self, source):
        """Two costs avoided: a `set_property` dropped on a down link lets mpv
        load UNPAUSED and emit through the handshake, and the reader would
        otherwise open the drive and wait 5 s for an mpv that is not there."""
        mpv = _mpv(ensure_connected=AsyncMock(return_value=False))
        _ready_to_play(source, mpv)

        assert await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0) is False
        source._reader.start.assert_not_called()
        mpv.load_stream.assert_not_awaited()

    async def test_mpv_is_paused_before_the_stream_is_loaded(self, source):
        """mpv must not emit audio during the loadfile/FIFO handshake, and the
        pause also clears a leftover pause from the previous track."""
        mpv = _mpv()
        _ready_to_play(source, mpv)
        order = []
        mpv.set_property = AsyncMock(side_effect=lambda k, v: order.append((k, v)))
        mpv.load_stream = AsyncMock(side_effect=lambda p: order.append(("load", p)) or True)

        assert await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0) is True

        assert order[0] == ("pause", True)
        assert order[1][0] == "load"
        assert ("pause", False) in order[2:], "playback was never started"

    async def test_the_reader_is_ready_before_mpv_is_told_to_open_the_fifo(self, source):
        """mpv opening the FIFO first blocks it on a writer that is not there."""
        mpv = _mpv()
        _ready_to_play(source, mpv)
        order = []
        source._reader.start = Mock(side_effect=lambda a, b: order.append("reader.start"))
        source._reader.wait_ready = Mock(
            side_effect=lambda t: order.append("wait_ready") or True)
        mpv.load_stream = AsyncMock(side_effect=lambda p: order.append("load") or True)

        await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0)
        assert order == ["reader.start", "wait_ready", "load"]

    async def test_a_reader_that_never_reports_ready_is_stopped(self, source):
        """Left running it holds the drive and blocks on a FIFO write for ever;
        the next track's reader then fights it for the same device."""
        mpv = _mpv()
        _ready_to_play(source, mpv)
        source._reader.wait_ready = Mock(return_value=False)

        assert await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0) is False
        source._reader.stop.assert_called_once()
        mpv.load_stream.assert_not_awaited()

    async def test_an_mpv_that_cannot_open_the_fifo_stops_the_reader_too(self, source):
        mpv = _mpv(load_stream=AsyncMock(return_value=False))
        _ready_to_play(source, mpv)

        assert await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0) is False
        source._reader.stop.assert_called_once()

    async def test_the_reader_is_started_at_the_requested_lba_up_to_the_leadout(self, source):
        mpv = _mpv()
        _ready_to_play(source, mpv)
        await asyncio.wait_for(source._start_reader_and_mpv(20000), 2.0)
        source._reader.start.assert_called_once_with(20000, 60000)
        assert source._play_start_lba == 20000

    async def test_a_preload_leaves_mpv_paused_and_does_not_wait_for_audio(self, source):
        mpv = _mpv()
        _ready_to_play(source, mpv)

        assert await asyncio.wait_for(
            source._start_reader_and_mpv(150, autostart=False), 2.0) is True

        assert mpv.set_property.await_args_list == [call("pause", True)]
        mpv.wait_until_advancing.assert_not_awaited()

    async def test_playback_that_never_advances_is_reported_but_not_fatal(self, source, caplog):
        """The ~1 s output-startup latency is real; a disc that is merely slow
        must still end up playing rather than being called a failure."""
        mpv = _mpv(wait_until_advancing=AsyncMock(return_value=False))
        _ready_to_play(source, mpv)

        with caplog.at_level("WARNING", logger=source._logger.name):
            assert await asyncio.wait_for(source._start_reader_and_mpv(150), 2.0) is True
        assert any("did not advance" in r.message for r in caplog.records)


class TestTheLightTeardown:
    """`_auto_stop_action` releases the drive after the pause timeout without
    losing where the user was."""

    async def test_the_drive_is_released_but_the_resume_point_is_kept(self, source):
        mpv = _mpv()
        _ready_to_play(source, mpv)
        source._current_track = 2
        source._track_position = 42
        source._is_paused = True

        await asyncio.wait_for(source._auto_stop_action(), 2.0)

        mpv.stop.assert_awaited_once()
        source._reader.stop.assert_called_once()
        assert (source._is_playing, source._is_paused, source._is_buffering) == \
            (False, False, False)
        assert (source._current_track, source._track_position) == (2, 42)

    async def test_mpv_stays_connected_for_a_cheap_restart(self, source):
        mpv = _mpv()
        _ready_to_play(source, mpv)
        await asyncio.wait_for(source._auto_stop_action(), 2.0)
        assert source._mpv is mpv
        mpv.disconnect.assert_not_awaited()


class TestPreStartOnInsertion:
    """`_pre_start_service` warms mpv up while MusicBrainz is being asked, so a
    disc that is inserted while the source is open plays as soon as it is read.

    Every failure is a warning and nothing else: this runs off a disc insertion,
    not a user action, and there is nobody to report an error to.
    """

    async def test_an_already_connected_mpv_is_left_alone(self, source):
        source._mpv = _mpv()
        source._start_service_and_wait = AsyncMock(return_value=True)
        await asyncio.wait_for(source._pre_start_service(), 2.0)
        source._start_service_and_wait.assert_not_awaited()

    async def test_it_starts_the_service_and_connects(self, source, monkeypatch):
        source._mpv = None
        source._start_service_and_wait = AsyncMock(return_value=True)
        made = _mpv()
        monkeypatch.setattr("backend.sources.cd.source.MpvController", Mock(return_value=made))

        await asyncio.wait_for(source._pre_start_service(), 2.0)
        assert source._mpv is made
        made.connect.assert_awaited_once()

    async def test_a_service_that_will_not_start_warns_and_leaves_mpv_unset(
            self, source, caplog, monkeypatch):
        source._mpv = None
        source._start_service_and_wait = AsyncMock(return_value=False)
        controller = Mock()
        monkeypatch.setattr("backend.sources.cd.source.MpvController", controller)

        with caplog.at_level("WARNING", logger=source._logger.name):
            await asyncio.wait_for(source._pre_start_service(), 2.0)

        controller.assert_not_called()
        assert source._mpv is None
        assert any("Pre-start" in r.message for r in caplog.records)

    async def test_a_connect_that_fails_leaves_a_controller_do_start_can_replace(
            self, source, monkeypatch, caplog):
        """`_do_start` reuses `self._mpv` only `if self._mpv.is_connected`, so an
        unconnected one here is not a trap — it is what makes the cold path run."""
        source._mpv = None
        source._start_service_and_wait = AsyncMock(return_value=True)
        dead = _mpv(connect=AsyncMock(return_value=False), is_connected=False)
        monkeypatch.setattr("backend.sources.cd.source.MpvController", Mock(return_value=dead))

        with caplog.at_level("WARNING", logger=source._logger.name):
            await asyncio.wait_for(source._pre_start_service(), 2.0)

        assert source._mpv.is_connected is False
        assert any("connect failed" in r.message for r in caplog.records)

    async def test_a_pre_start_that_raises_is_swallowed(self, source, caplog):
        """It runs from the disc watcher's loop body; an exception escaping here
        is caught one level up, but the warning is what names the cause."""
        source._mpv = None
        source._start_service_and_wait = AsyncMock(side_effect=RuntimeError("systemd busy"))

        with caplog.at_level("WARNING", logger=source._logger.name):
            await asyncio.wait_for(source._pre_start_service(), 2.0)
        assert any("systemd busy" in r.message for r in caplog.records)


class TestTheDiscWatcherLoop:
    """The watcher runs for the life of the unit, in a bare task nobody
    supervises. How it survives a bad poll IS the behaviour: a poll that raises
    and kills it means disc insertion stops being noticed until a reboot."""

    async def test_a_poll_that_raises_does_not_kill_the_watcher(self, source, caplog):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("drive vanished mid-poll")

        source._check_drive_and_disc = flaky
        source._retry_metadata_if_pending = AsyncMock()

        with patch("backend.sources.cd.source.DISC_POLL_INTERVAL_S", 0.01), \
                caplog.at_level("ERROR", logger=source._logger.name):
            task = asyncio.create_task(source._disc_watcher_loop())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if len(calls) >= 3:
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert len(calls) >= 3, "the watcher stopped polling after one failure"
        assert any("Disc watcher error" in r.message for r in caplog.records)

    async def test_the_metadata_retry_runs_on_every_poll(self, source):
        """It is throttled inside itself, not by the loop — skipping it here is
        what leaves a disc whose first lookup failed unnamed for ever."""
        source._check_drive_and_disc = AsyncMock()
        source._retry_metadata_if_pending = AsyncMock()

        with patch("backend.sources.cd.source.DISC_POLL_INTERVAL_S", 0.01):
            task = asyncio.create_task(source._disc_watcher_loop())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if source._retry_metadata_if_pending.await_count >= 2:
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert source._retry_metadata_if_pending.await_count >= 2

    async def test_cancellation_ends_the_watcher_instead_of_being_logged(self, source, caplog):
        """Shutdown cancels it. Caught by the generic arm it would log an error
        on every clean stop, and the banner would fire on a normal restart."""
        source._check_drive_and_disc = AsyncMock()
        source._retry_metadata_if_pending = AsyncMock()

        with patch("backend.sources.cd.source.DISC_POLL_INTERVAL_S", 0.01), \
                caplog.at_level("ERROR", logger=source._logger.name):
            task = asyncio.create_task(source._disc_watcher_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2.0)

        assert caplog.records == []


class TestReadingADiscInTheBackground:
    """`_load_disc_metadata` — the lookup that runs when the source is opened on
    a disc that was inserted while it was closed."""

    async def test_the_toc_offsets_and_the_metadata_both_land(self, source):
        _with_state_machine(source)
        source._disc_present = True
        source._last_disc_id = "disc-1"
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(return_value=(
            "disc-1", "toc",
            [{"number": 1, "duration": 200, "offset": 150},
             {"number": 2, "duration": 150, "offset": 20000}],
            60000,
        ))
        source._data_service.lookup_metadata = AsyncMock(return_value=DISC)

        await asyncio.wait_for(source._load_disc_metadata(), 2.0)

        assert source._sector_offsets == [150, 20000]
        assert source._disc_end_lba == 60000
        assert source._current_disc is DISC
        assert source._tracks == TRACKS
        assert source._metadata_retry_pending is False

    async def test_a_disc_ejected_during_the_lookup_is_not_written_back(self, source):
        """The lookup reaches the network and takes seconds; a disc swapped in
        the meantime would be captioned with the previous one's album."""
        _with_state_machine(source)
        source._disc_present = True
        source._last_disc_id = "disc-1"
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-1", "toc", [{"number": 1, "duration": 200, "offset": 150}], 60000))

        async def eject_then_answer(*_a):
            source._disc_present = False
            return DISC

        source._data_service.lookup_metadata = eject_then_answer
        await asyncio.wait_for(source._load_disc_metadata(), 2.0)

        assert source._current_disc is None
        assert source._tracks == []

    async def test_an_unknown_disc_arms_the_retry(self, source):
        """`album is None` is the fallback DiscInfo; the watcher re-asks
        MusicBrainz later, which is how a disc read with no internet gets named
        once the link is back."""
        _with_state_machine(source)
        source._disc_present = True
        source._last_disc_id = "disc-1"
        unknown = DiscInfo(disc_id="disc-1", track_count=1, total_duration=200,
                           tracks=[TrackInfo(number=1, title="Track 1", duration=200)])
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(
            return_value=("disc-1", "toc", [{"number": 1, "duration": 200, "offset": 150}], 60000))
        source._data_service.lookup_metadata = AsyncMock(return_value=unknown)

        await asyncio.wait_for(source._load_disc_metadata(), 2.0)
        assert source._metadata_retry_pending is True

    async def test_a_disc_that_cannot_be_read_leaves_state_untouched(self, source):
        _with_state_machine(source)
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(return_value=None)
        source._data_service.lookup_metadata = AsyncMock()

        await asyncio.wait_for(source._load_disc_metadata(), 2.0)
        source._data_service.lookup_metadata.assert_not_awaited()
        assert source._sector_offsets == []

    async def test_a_lookup_that_raises_is_logged_and_contained(self, source, caplog):
        """It runs from a background task; escaping, it would be swallowed by
        BackgroundTaskSet with no line naming the disc."""
        _with_state_machine(source)
        source._data_service = Mock()
        source._data_service.read_disc = AsyncMock(side_effect=RuntimeError("libdiscid segfault"))

        with caplog.at_level("ERROR", logger=source._logger.name):
            await asyncio.wait_for(source._load_disc_metadata(), 2.0)
        assert any("libdiscid segfault" in r.message for r in caplog.records)


class TestResumingFromIdle:
    """After the auto-stop teardown nothing is loaded any more: mpv holds no
    stream and the reader is stopped. A play tap has to take the full-restart
    path and land back where the user left off, not un-pause a dead mpv."""

    def _idle(self, source, track=2, position=42):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._is_playing = False
        source._is_paused = False
        source._current_track = track
        source._track_position = position
        return source

    async def test_it_restarts_the_reader_where_the_track_left_off(self, source):
        self._idle(source)
        restarts = []
        source._restart_reader_and_mpv = AsyncMock(
            side_effect=lambda lba, **kw: restarts.append(lba) or True)
        source._sync_position_from_mpv = AsyncMock()

        result = await asyncio.wait_for(source._handle_resume(), 2.0)

        assert result["success"] is True
        # Track 2 starts at 20000; 42 s in at 75 sectors/second.
        assert restarts == [20000 + 42 * SECTORS_PER_SECOND]
        assert source._is_playing is True
        assert source._track_duration == TRACKS[1].duration

    async def test_a_track_number_past_the_disc_falls_back_to_the_first(self, source):
        """The album-finished reset leaves `_current_track` at 1, but a disc
        swapped for a shorter one would index past `_tracks` and raise."""
        self._idle(source, track=99, position=0)
        source._restart_reader_and_mpv = AsyncMock(return_value=True)
        source._sync_position_from_mpv = AsyncMock()

        assert (await asyncio.wait_for(source._handle_resume(), 2.0))["success"] is True
        assert source._current_track == 1

    async def test_a_disc_whose_toc_is_gone_refuses_cleanly(self, source, caplog):
        """Both versions answer failure — without the guard the LBA maths raises
        and the outer handler catches it — so `success is False` is not the
        discriminator. What separates them is HOW: the guard names the reason,
        while the exception path logs at ERROR and puts an IndexError on the
        WebSocket error banner for a disc that was merely ejected.
        """
        self._idle(source)
        source._sector_offsets = []
        source._tracks = []
        source._restart_reader_and_mpv = AsyncMock(return_value=True)

        with caplog.at_level("ERROR", logger=source._logger.name):
            result = await asyncio.wait_for(source._handle_resume(), 2.0)

        assert result["success"] is False
        assert result["error"] == "Disc not ready"
        assert caplog.records == [], \
            f"an expected refusal reached the error banner: {[r.message for r in caplog.records]}"
        source._restart_reader_and_mpv.assert_not_awaited()

    async def test_a_restart_that_fails_reports_it_instead_of_showing_playing(self, source):
        """`_settle_after_restart` clears `_is_playing`; without the error the UI
        keeps a play button that has nothing behind it."""
        self._idle(source)
        source._restart_reader_and_mpv = AsyncMock(return_value=False)

        result = await asyncio.wait_for(source._handle_resume(), 2.0)
        assert result["success"] is False
        assert source._is_playing is False
        assert source._is_buffering is False

    async def test_resuming_what_is_already_playing_is_not_an_error(self, source):
        self._idle(source)
        source._is_playing = True
        source._restart_reader_and_mpv = AsyncMock()

        result = await asyncio.wait_for(source._handle_resume(), 2.0)
        assert result["success"] is True
        source._restart_reader_and_mpv.assert_not_awaited()


class TestTheAccessorsRoutesUse:
    """`routes.py` and `dependencies.py` reach the source through these. Each is
    a plain read, and that is the point: a forwarding method per service call is
    the second API surface CLAUDE.md forbids."""

    def test_the_data_service_is_exposed_not_proxied(self, source):
        assert source.data_service is source._data_service
        assert isinstance(source.data_service, CdDataService)

    def test_the_track_list_is_the_one_the_disc_read_produced(self, source):
        source._tracks = TRACKS
        assert source.tracks == TRACKS

    def test_the_drive_and_disc_flags_are_what_the_watcher_set(self, source):
        source._drive_connected = True
        source._disc_present = True
        assert (source.drive_connected, source.disc_present) == (True, True)
        source._disc_present = False
        assert source.disc_present is False

    def test_a_stopped_source_still_publishes_the_disc_it_holds(self, source):
        """`_idle_metadata` is what the state machine reads on the way to READY.
        An empty projection there blanks the disc the moment playback stops,
        even though it is still in the tray."""
        _with_state_machine(source)
        source._disc_present = True
        source._current_disc = DISC
        source._tracks = TRACKS

        idle = source._idle_metadata()
        assert idle["disc_present"] is True
        assert idle["album"] == DISC.album
        assert len(idle["tracks"]) == len(TRACKS)

    async def test_refresh_metadata_republishes_the_live_projection(self, source):
        """A WebSocket connecting mid-track seeds its player from
        `initial_state`; a stale projection there shows the previous track."""
        _with_state_machine(source)
        source._current_disc = DISC
        source._tracks = TRACKS
        source._metadata = {}

        assert await source.refresh_metadata() is True
        assert source._metadata["album"] == DISC.album


class TestLbaToTrack:
    def test_an_lba_maps_to_the_track_it_falls_inside(self, source):
        source._sector_offsets = [150, 20000, 40000]
        assert source._lba_to_track(150) == 1
        assert source._lba_to_track(19999) == 1
        assert source._lba_to_track(20000) == 2
        assert source._lba_to_track(45000) == 3

    def test_an_lba_before_the_first_track_belongs_to_none(self, source):
        """The pre-gap. Answered as track 1 it would let the monitor tick report
        a negative position inside it."""
        source._sector_offsets = [150, 20000]
        assert source._lba_to_track(0) is None

    def test_a_disc_with_no_toc_maps_nothing(self, source):
        source._sector_offsets = []
        assert source._lba_to_track(20000) is None


class TestTeardownAndFailureArms:
    async def test_initialize_starts_the_watcher_that_never_stops(self, source):
        """The watcher is what notices a disc at all; started from `initialize`
        rather than `_do_start` precisely so insertion is seen while the source
        is closed."""
        source._data_service = Mock(initialize=AsyncMock())
        with patch.object(CdSource, "_disc_watcher_loop", AsyncMock()):
            assert await asyncio.wait_for(source.initialize(), 2.0) is True

        source._data_service.initialize.assert_awaited_once()
        assert source._disc_watcher_task is not None
        source._disc_watcher_task.cancel()
        await asyncio.gather(source._disc_watcher_task, return_exceptions=True)

    async def test_a_data_service_that_cannot_start_does_not_take_the_source_down(self, source):
        """`initialize` is decorated fail-open: a corrupt cd_data.json must not
        stop the backend from booting."""
        source._data_service = Mock(initialize=AsyncMock(side_effect=OSError("disk full")))
        assert await asyncio.wait_for(source.initialize(), 2.0) is False

    async def test_stopping_releases_the_drive_and_the_service(self, source):
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._cleanup = AsyncMock()
        source._stop_service = AsyncMock(return_value=True)
        source._current_track = 2

        assert await asyncio.wait_for(source._do_stop(), 2.0) is True
        source._cleanup.assert_awaited_once()
        source._stop_service.assert_awaited_once()
        assert source._current_track is None, "the resume point survived a full stop"

    async def test_an_mpv_that_dies_stops_the_reader_with_it(self, source):
        """The reader holds /dev/sr0 and blocks writing into a FIFO nobody
        drains; left running, the drive is never released."""
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)
        source._is_playing = True

        await asyncio.wait_for(source._on_mpv_disconnect(), 2.0)

        source._reader.stop.assert_called_once()
        assert source._is_playing is False

    async def test_an_eject_the_drive_refuses_is_reported_and_unlatches_the_ui(self, source):
        """`_ejecting` drives the "ejecting" state on screen. Left set after a
        refusal — a drive with the tray locked, or a disc still mounted — the
        source shows it for ever."""
        _with_state_machine(source)
        _running_bg(source)
        _ready_to_play(source)

        proc = Mock()
        proc.wait = AsyncMock(return_value=1)
        proc.returncode = 1
        proc.stderr = Mock(read=AsyncMock(return_value=b"eject: unable to eject"))

        with patch("backend.sources.cd.source.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=proc)):
            result = await asyncio.wait_for(source._handle_eject(), 2.0)

        assert result["success"] is False
        assert "unable to eject" in result["error"]
        assert source._ejecting is False
