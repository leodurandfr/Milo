# backend/tests/test_music_library_source.py
"""Unit tests for MusicLibrarySource playback + queue (P1-6).

Covers the play_context → gapless mpv playlist path, transport commands
(pause/resume/next/prev/seek/play_index/set_shuffle/stop), the now-playing WS
metadata projection (title/artist/album/art + queue/index/shuffle), the live
shuffle toggle, resume-on-return, and the monitor's gapless auto-advance +
end-of-queue detection. mpv IPC and the Navidrome client are mocked — no service,
socket, or daemon is touched.
"""
import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.core.models.audio_state import SourceState
from backend.sources.music_library.source import MusicLibrarySource


@pytest.fixture
def config():
    return {"mpv_socket": "/tmp/test-music-library-ipc.sock"}


@pytest.fixture
def source(config):
    """MusicLibrarySource with a mocked service manager and Navidrome client.

    The StorageManager is constructed (cheap, fail-open — no udev touched) but
    never initialized, so no monitor thread starts.
    """
    src = MusicLibrarySource(config)

    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)

    # Navidrome client: only stream_url is used at play time.
    src.get_navidrome_client = AsyncMock(
        return_value=Mock(stream_url=lambda song_id: f"http://nav/stream/{song_id}")
    )
    return src


def _mpv(**overrides):
    """An mpv mock with async transport methods and a get_property stub."""
    mpv = Mock()
    mpv.is_connected = True
    mpv.load_playlist = AsyncMock(return_value=True)
    mpv.set_playlist_pos = AsyncMock(return_value=True)
    mpv.seek = AsyncMock(return_value=True)
    mpv.pause = AsyncMock(return_value=True)
    mpv.resume = AsyncMock(return_value=True)
    mpv.stop = AsyncMock(return_value=True)
    mpv.disconnect = AsyncMock(return_value=True)
    mpv.set_property = AsyncMock(return_value=True)
    mpv.replace_playlist_tail = AsyncMock(return_value=True)
    mpv.get_property = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(mpv, key, value)
    return mpv


def _mpv_with_props(props):
    """An mpv mock whose get_property reads from a dict (monitor-tick tests)."""
    mpv = _mpv()

    async def _get(name):
        return props.get(name)

    mpv.get_property = AsyncMock(side_effect=_get)
    return mpv


TRACKS = [
    {"id": "s1", "title": "One", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 100},
    {"id": "s2", "title": "Two", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 200},
    {"id": "s3", "title": "Three", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 300},
]


class TestCompliance:
    def test_identity(self, source):
        assert source.source_id == "music_library"
        assert source.service_name == "milo-music-library.service"

    def test_default_socket(self):
        assert MusicLibrarySource()._mpv_socket == "/run/milo/music_library-ipc.sock"

    def test_commands_registered(self, source):
        for cmd in ("play_context", "play_index", "pause", "resume", "next", "prev", "seek", "stop"):
            assert cmd in source.COMMANDS

class TestPlayContext:
    @pytest.mark.asyncio
    async def test_builds_gapless_queue(self, source):
        source._mpv = _mpv()

        result = await source.command("play_context", {"tracks": TRACKS, "start_index": 0})

        assert result["success"] is True
        assert source._queue == TRACKS
        assert source._queue_index == 0
        assert source._is_playing is True
        assert source.state == SourceState.ACTIVE
        # One native playlist built from the per-id stream URLs, starting at 0.
        urls, start = source._mpv.load_playlist.await_args.args
        assert urls == [f"http://nav/stream/{t['id']}" for t in TRACKS]
        assert start == 0

    @pytest.mark.asyncio
    async def test_start_index_respected(self, source):
        source._mpv = _mpv()

        await source.command("play_context", {"tracks": TRACKS, "start_index": 2})

        assert source._queue_index == 2
        assert source._duration == 300
        assert source._mpv.load_playlist.await_args.args[1] == 2

    @pytest.mark.asyncio
    async def test_shuffle_keeps_picked_track_first(self, source):
        source._mpv = _mpv()

        # Deterministic shuffle: freeze the order so only the pick-to-front move shows.
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: None):
            await source.command(
                "play_context", {"tracks": TRACKS, "start_index": 1, "shuffle": True}
            )

        assert source._shuffle is True
        assert source._queue[0] == TRACKS[1]          # picked track pinned first
        assert source._queue_index == 0
        assert source._mpv.load_playlist.await_args.args[1] == 0

    @pytest.mark.asyncio
    async def test_missing_id_rejected(self, source):
        source._mpv = _mpv()
        result = await source.command("play_context", {"tracks": [{"title": "x"}]})
        assert result["success"] is False
        source._mpv.load_playlist.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_active_mpv(self, source):
        source._mpv = None
        result = await source.command("play_context", {"tracks": TRACKS})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_requires_catalog(self, source):
        source._mpv = _mpv()
        source.get_navidrome_client = AsyncMock(return_value=None)
        result = await source.command("play_context", {"tracks": TRACKS})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_load_failure_resets_to_waiting(self, source):
        source._mpv = _mpv(load_playlist=AsyncMock(return_value=False))
        result = await source.command("play_context", {"tracks": TRACKS})
        assert result["success"] is False
        assert source._queue == []
        assert source.state == SourceState.WAITING


class TestTransport:
    async def _play(self, source):
        source._mpv = _mpv()
        await source.command("play_context", {"tracks": TRACKS, "start_index": 0})
        return source

    @pytest.mark.asyncio
    async def test_pause(self, source):
        await self._play(source)
        result = await source.command("pause", {})
        assert result["success"] is True
        assert source._is_playing is False
        assert source.state == SourceState.ACTIVE  # queue still loaded → paused
        source._mpv.pause.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume(self, source):
        await self._play(source)
        source._is_playing = False
        result = await source.command("resume", {})
        assert result["success"] is True
        assert source._is_playing is True
        source._mpv.resume.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seek(self, source):
        await self._play(source)
        result = await source.command("seek", {"position_ms": 42000})
        assert result["success"] is True
        assert source._position == 42
        source._mpv.seek.assert_awaited_with(42)

    @pytest.mark.asyncio
    async def test_next(self, source):
        await self._play(source)
        result = await source.command("next", {})
        assert result["success"] is True
        assert source._queue_index == 1
        source._mpv.set_playlist_pos.assert_awaited_with(1)

    @pytest.mark.asyncio
    async def test_next_at_end_is_noop(self, source):
        await self._play(source)
        source._queue_index = len(TRACKS) - 1
        result = await source.command("next", {})
        assert result["success"] is True
        assert source._queue_index == len(TRACKS) - 1
        source._mpv.set_playlist_pos.assert_not_called()

    @pytest.mark.asyncio
    async def test_prev_restarts_current_when_past_threshold(self, source):
        await self._play(source)
        source._queue_index = 1
        source._mpv.get_property = AsyncMock(return_value=5)  # 5s in → restart
        result = await source.command("prev", {})
        assert result["success"] is True
        assert source._queue_index == 1
        source._mpv.seek.assert_awaited_with(0)
        source._mpv.set_playlist_pos.assert_not_called()

    @pytest.mark.asyncio
    async def test_prev_steps_back_when_early(self, source):
        await self._play(source)
        source._queue_index = 1
        source._mpv.get_property = AsyncMock(return_value=1)  # 1s in → previous track
        result = await source.command("prev", {})
        assert result["success"] is True
        assert source._queue_index == 0
        source._mpv.set_playlist_pos.assert_awaited_with(0)

    @pytest.mark.asyncio
    async def test_play_index(self, source):
        await self._play(source)
        result = await source.command("play_index", {"index": 2})
        assert result["success"] is True
        assert source._queue_index == 2
        source._mpv.set_playlist_pos.assert_awaited_with(2)

    @pytest.mark.asyncio
    async def test_play_index_out_of_range(self, source):
        await self._play(source)
        result = await source.command("play_index", {"index": 9})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_stop_clears_queue(self, source):
        await self._play(source)
        result = await source.command("stop", {})
        assert result["success"] is True
        assert source._queue == []
        assert source._is_playing is False
        assert source.state == SourceState.WAITING
        source._mpv.stop.assert_awaited_once()


class TestMetadata:
    def test_empty_without_queue(self, source):
        assert source._build_playback_metadata() == {}

    def test_now_playing_projection(self, source):
        source._queue = TRACKS
        source._queue_index = 1
        source._position = 60
        source._duration = 200
        source._is_playing = True
        source._shuffle = True

        meta = source._build_playback_metadata()

        assert meta["title"] == "Two"
        assert meta["artist"] == "DP"
        assert meta["album"] == "Disc"
        assert meta["album_art_url"] == "/api/music-library/cover/al1"
        # position/duration in ms (shared wire convention)
        assert meta["position"] == 60000
        assert meta["duration"] == 200000
        assert meta["queue"] == TRACKS
        assert meta["queue_index"] == 1
        assert meta["shuffle"] is True
        # `repeat` was removed (dead scaffolding) — it must not reappear.
        assert "repeat" not in meta
        assert meta["track_id"] == "s2"

    def test_cover_url_falls_back_to_album_id(self, source):
        assert source._cover_url({"albumId": "ab9"}) == "/api/music-library/cover/ab9"
        assert source._cover_url({}) is None

    def test_state_waiting_without_queue(self, source):
        source._queue = []
        source._update_connection_state()
        assert source.state == SourceState.WAITING

    def test_state_active_with_queue(self, source):
        source._queue = TRACKS
        source._queue_index = 0
        source._update_connection_state()
        assert source.state == SourceState.ACTIVE


class TestMonitor:
    @pytest.mark.asyncio
    async def test_queue_finished_on_idle(self, source):
        source._queue = list(TRACKS)
        source._queue_index = 2
        source._loading = False
        source._mpv = _mpv_with_props({"idle-active": True})

        await source._on_monitor_tick()

        assert source._queue == []
        assert source.state == SourceState.WAITING

    @pytest.mark.asyncio
    async def test_gapless_auto_advance(self, source):
        source._queue = list(TRACKS)
        source._queue_index = 0
        source._loading = False
        source._is_playing = True
        source._mpv = _mpv_with_props(
            {"idle-active": False, "playlist-pos": 1, "time-pos": 2, "duration": 200, "pause": False}
        )

        await source._on_monitor_tick()

        assert source._queue_index == 1
        assert source._duration == 200

    @pytest.mark.asyncio
    async def test_tick_noop_without_queue(self, source):
        source._queue = []
        source._mpv = _mpv_with_props({"idle-active": True})
        # No queue → guarded out before any end-of-queue handling.
        await source._on_monitor_tick()
        assert source.state == SourceState.WAITING

    @pytest.mark.asyncio
    async def test_buffering_clears_when_playhead_moves(self, source):
        source._queue = list(TRACKS)
        source._queue_index = 0
        source._loading = False
        source._is_playing = True
        source._is_buffering = True
        source._mpv = _mpv_with_props(
            {"idle-active": False, "playlist-pos": 0, "time-pos": 3, "duration": 100, "pause": False}
        )

        await source._on_monitor_tick()

        assert source._is_buffering is False


class TestSetShuffle:
    """Live shuffle toggle: reorders only the upcoming tracks (current keeps
    playing), rebuilding the mpv tail in place."""

    @pytest.mark.asyncio
    async def test_toggle_on_rebuilds_tail_keeps_head(self, source):
        source._mpv = _mpv()
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 0
        source._shuffle = False

        # No-op shuffle so the mechanics show without randomness.
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: None):
            result = await source.command("set_shuffle", {"shuffle": True})

        assert result["success"] is True
        assert source._shuffle is True
        assert source._queue[0]["id"] == "s1"  # current/head untouched
        keep, urls = source._mpv.replace_playlist_tail.await_args.args
        assert keep == 1  # everything from index+1 is the rebuilt tail
        assert urls == ["http://nav/stream/s2", "http://nav/stream/s3"]

    @pytest.mark.asyncio
    async def test_toggle_off_restores_original_order(self, source):
        source._mpv = _mpv()
        # A shuffled queue whose pristine order is TRACKS.
        source._queue = [TRACKS[0], TRACKS[2], TRACKS[1]]
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 0
        source._shuffle = True

        result = await source.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        assert source._shuffle is False
        # Head (s1) kept; tail restored to pristine order s2, s3.
        assert [t["id"] for t in source._queue] == ["s1", "s2", "s3"]
        keep, urls = source._mpv.replace_playlist_tail.await_args.args
        assert keep == 1
        assert urls == ["http://nav/stream/s2", "http://nav/stream/s3"]

    @pytest.mark.asyncio
    async def test_noop_when_already_in_target_state(self, source):
        source._mpv = _mpv()
        source._queue = list(TRACKS)
        source._shuffle = False

        result = await source.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        source._mpv.replace_playlist_tail.assert_not_called()
        assert source._shuffle is False

    @pytest.mark.asyncio
    async def test_requires_active_queue(self, source):
        source._mpv = _mpv()
        source._queue = []
        result = await source.command("set_shuffle", {"shuffle": True})
        assert result["success"] is False
        source._mpv.replace_playlist_tail.assert_not_called()


class TestResume:
    """Resume-on-return: snapshot on source-switch / idle auto-stop, restore
    PAUSED on the next activation; forget it on explicit Stop / queue end."""

    @pytest.mark.asyncio
    async def test_capture_snapshots_live_session(self, source):
        source._mpv = _mpv_with_props({"time-pos": 42})
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 1
        source._shuffle = True

        await source._capture_resume_session()

        assert source._resume is not None
        assert source._resume["queue"] == TRACKS
        assert source._resume["queue_index"] == 1
        assert source._resume["position"] == 42  # live playhead, not the last tick
        assert source._resume["shuffle"] is True

    @pytest.mark.asyncio
    async def test_capture_is_noop_without_queue(self, source):
        source._mpv = _mpv()
        source._queue = []
        source._resume = {"stale": True}
        await source._capture_resume_session()
        assert source._resume is None

    @pytest.mark.asyncio
    async def test_auto_stop_saves_session(self, source):
        source._mpv = _mpv_with_props({"time-pos": 30})
        source._queue = list(TRACKS)
        source._queue_index = 2

        await source._auto_stop_action()

        assert source.state == SourceState.WAITING
        assert source._resume is not None
        assert source._resume["queue_index"] == 2

    @pytest.mark.asyncio
    async def test_explicit_stop_forgets_session(self, source):
        source._mpv = _mpv()
        source._queue = list(TRACKS)
        source._resume = {"stale": True}

        await source.command("stop", {})

        assert source.state == SourceState.WAITING
        assert source._resume is None

    @pytest.mark.asyncio
    async def test_queue_finished_forgets_session(self, source):
        source._resume = {"stale": True}
        await source._handle_queue_finished()
        assert source._resume is None

    @pytest.mark.asyncio
    async def test_new_context_forgets_session(self, source):
        source._mpv = _mpv()
        source._resume = {"stale": True}
        await source.command("play_context", {"tracks": TRACKS, "start_index": 0})
        assert source._resume is None

    @pytest.mark.asyncio
    async def test_restore_loads_paused_at_saved_position(self, source):
        source._mpv = _mpv_with_props({"duration": 200})
        source._resume = {
            "queue": list(TRACKS),
            "queue_unshuffled": list(TRACKS),
            "queue_index": 1,
            "position": 60,
            "shuffle": False,
        }

        ok = await source._restore_resume_session()

        assert ok is True
        assert source._queue == TRACKS
        assert source._queue_index == 1
        assert source._is_playing is False          # restored PAUSED
        assert source.state == SourceState.ACTIVE    # active but paused
        source._mpv.load_playlist.assert_awaited()
        source._mpv.pause.assert_awaited()
        source._mpv.seek.assert_awaited_with(60)
        assert source._resume is None                # consumed

    @pytest.mark.asyncio
    async def test_restore_fails_without_catalog(self, source):
        source._mpv = _mpv()
        source.get_navidrome_client = AsyncMock(return_value=None)
        source._resume = {"queue": list(TRACKS), "queue_index": 0, "position": 0}

        ok = await source._restore_resume_session()

        assert ok is False
        assert source._resume is None
        source._mpv.load_playlist.assert_not_called()

    @pytest.mark.asyncio
    async def test_do_start_restores_saved_session(self, source):
        source._resume = {
            "queue": list(TRACKS),
            "queue_unshuffled": list(TRACKS),
            "queue_index": 0,
            "position": 0,
            "shuffle": False,
        }
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        mpv = _mpv_with_props({"duration": 100})
        mpv.connect = AsyncMock(return_value=True)

        with patch("backend.sources.music_library.source.MpvController", return_value=mpv):
            ok = await source._do_start()

        assert ok is True
        assert source.state == SourceState.ACTIVE
        assert source._is_playing is False  # resumed paused, not auto-playing
        mpv.load_playlist.assert_awaited()


class TestOfflineShareNames:
    """The mount-health gate for the full-scan/purge route: purging while a share
    is offline would wrongly drop its still-valid tracks, so the route refuses
    when offline_share_names() is non-empty."""

    @pytest.mark.asyncio
    async def test_lists_only_unmounted_shares(self, source):
        source._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "name": "NAS-Leo", "host": "10.0.0.2"},
            {"id": "b", "name": "Studio", "host": "10.0.0.3"},
        ])
        source._storage.get_mounted_share_ids = Mock(return_value={"a"})

        assert await source.offline_share_names() == ["Studio"]

    @pytest.mark.asyncio
    async def test_empty_when_all_mounted(self, source):
        source._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "name": "NAS-Leo", "host": "10.0.0.2"},
        ])
        source._storage.get_mounted_share_ids = Mock(return_value={"a"})

        assert await source.offline_share_names() == []

    @pytest.mark.asyncio
    async def test_name_falls_back_to_host_then_id(self, source):
        source._data.list_shares = AsyncMock(return_value=[
            {"id": "a", "host": "10.0.0.2"},   # no name → host
            {"id": "b"},                        # no name/host → id
        ])
        source._storage.get_mounted_share_ids = Mock(return_value=set())

        assert await source.offline_share_names() == ["10.0.0.2", "b"]


class TestBootRemountRetry:
    """Boot remount of configured network shares + the bounded catch-up retry
    for a NAS that was still offline when the backend booted (reboot race)."""

    @pytest.mark.asyncio
    async def test_all_mounted_spawns_no_retry(self, source):
        source._data.list_shares = AsyncMock(return_value=[
            {"id": "a"}, {"id": "b"},
        ])
        source._storage.mount_share = AsyncMock(return_value="/media/milo/x")

        await source._mount_configured_shares()

        assert source._storage.mount_share.await_count == 2
        assert source._share_retry_task is None

    @pytest.mark.asyncio
    async def test_offline_share_spawns_retry(self, source):
        source._data.list_shares = AsyncMock(return_value=[{"id": "a"}])
        source._storage.mount_share = AsyncMock(return_value=None)  # offline

        with patch(
            "backend.sources.music_library.source._SHARE_REMOUNT_RETRY_DELAYS_S", ()
        ):
            await source._mount_configured_shares()
            assert source._share_retry_task is not None
            await source._share_retry_task  # exhausted schedule → gives up cleanly

    @pytest.mark.asyncio
    async def test_retry_remounts_when_nas_comes_up(self, source):
        share = {"id": "a"}
        # Offline on the first two attempts, then reachable.
        source._storage.mount_share = AsyncMock(
            side_effect=[None, None, "/media/milo/a"]
        )

        with patch(
            "backend.sources.music_library.source.asyncio.sleep", AsyncMock()
        ), patch(
            "backend.sources.music_library.source._SHARE_REMOUNT_RETRY_DELAYS_S",
            (1, 1, 1),
        ):
            await source._retry_offline_shares([share])

        # Two retry rounds, the second one connects → stops early (3rd delay unused).
        assert source._storage.mount_share.await_count == 3

    @pytest.mark.asyncio
    async def test_try_mount_share_is_fail_open(self, source):
        source._storage.mount_share = AsyncMock(side_effect=OSError("boom"))
        assert await source._try_mount_share({"id": "a"}) is False
