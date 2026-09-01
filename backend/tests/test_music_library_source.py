# backend/tests/test_music_library_source.py
"""Unit tests for MusicLibrarySource playback + queue (P1-6).

Covers the play_context → gapless mpv playlist path, transport commands
(pause/resume/next/prev/seek/play_index/set_shuffle/stop), the now-playing WS
metadata projection (title/artist/album/art + queue/index/shuffle), the live
shuffle toggle, resume-on-return, the monitor's gapless auto-advance +
end-of-queue detection, and the whole-catalog album walk the alphabetical grid
is paged from. mpv IPC and the Navidrome client are mocked — no service, socket,
or daemon is touched.
"""
import asyncio
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
    async def test_load_failure_resets_to_ready(self, source):
        source._mpv = _mpv(load_playlist=AsyncMock(return_value=False))
        result = await source.command("play_context", {"tracks": TRACKS})
        assert result["success"] is False
        assert source._queue == []
        assert source.state == SourceState.READY


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
        assert source.state == SourceState.READY
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

    def test_state_ready_without_queue(self, source):
        source._queue = []
        source._update_connection_state()
        assert source.state == SourceState.READY

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
        assert source.state == SourceState.READY

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
        assert source.state == SourceState.READY

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
    async def test_toggle_off_keeps_a_track_the_queue_lists_twice(self, source):
        """A repeated track id must survive shuffle OFF, minus the played copies.

        The pristine order was consumed by set membership, so a queue holding the
        same id twice — an album with a reprise, a hand-built playlist — lost
        *both* copies as soon as the first had played: the track silently
        disappeared from the rest of the session.
        """
        reprise = dict(TRACKS[0], title="One (reprise)")
        pristine = [TRACKS[0], TRACKS[1], reprise, TRACKS[2]]
        source._mpv = _mpv()
        # Shuffled: the first copy of s1 has played, everything else is upcoming.
        source._queue = [TRACKS[0], TRACKS[2], reprise, TRACKS[1]]
        source._queue_unshuffled = pristine
        source._queue_index = 0
        source._shuffle = True

        result = await source.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        # One copy of s1 played, so exactly one is dropped — the second returns
        # to its pristine place between s2 and s3.
        assert [t["id"] for t in source._queue] == ["s1", "s2", "s1", "s3"]
        assert source._queue[2]["title"] == "One (reprise)"
        _, urls = source._mpv.replace_playlist_tail.await_args.args
        assert urls == [
            "http://nav/stream/s2", "http://nav/stream/s1", "http://nav/stream/s3",
        ]

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


class TestMergedAlbumCache:
    """The whole-catalog walk behind the alphabetical grid. It is cached for a
    TTL, so what lands in the cache has to be the catalog — a walk cut short by a
    failed page would otherwise BE the catalog until it expires, and the albums
    past the break read as deleted."""

    @staticmethod
    def _client(pages):
        """A Navidrome client answering the paged walk with ``pages`` in order."""
        return Mock(get_album_list=AsyncMock(side_effect=list(pages)))

    @pytest.mark.asyncio
    async def test_a_failed_page_is_served_but_never_cached(self, source):
        source.get_navidrome_client = AsyncMock(
            return_value=self._client([[{"id": "a1"}, {"id": "a2"}], None])
        )

        with patch("backend.sources.music_library.source._ALBUM_PAGE", 2):
            albums = await source.get_merged_albums([2])

        # This call still answers with what it got — a partial grid beats none.
        assert [a["id"] for a in albums] == ["a1", "a2"]
        assert source._album_cache == {}

    @pytest.mark.asyncio
    async def test_a_walk_that_reached_the_end_is_cached(self, source):
        client = self._client([[{"id": "a1"}, {"id": "a2"}], [{"id": "a3"}]])
        source.get_navidrome_client = AsyncMock(return_value=client)

        with patch("backend.sources.music_library.source._ALBUM_PAGE", 2):
            albums = await source.get_merged_albums([2])
            again = await source.get_merged_albums([2])

        assert [a["id"] for a in albums] == ["a1", "a2", "a3"]
        assert again == albums
        # The short second page ended the walk; the second call asked nothing.
        assert client.get_album_list.await_count == 2


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
    async def test_capture_without_queue_keeps_the_saved_session(self, source):
        """Capturing with nothing loaded must not forget an earlier snapshot.

        The idle auto-stop saves a session and then empties the queue, so the
        source switch that follows captures again on an empty queue — clearing
        there loses the session the auto-stop just took.
        """
        source._mpv = _mpv()
        source._queue = []
        saved = {"queue": list(TRACKS), "queue_index": 1, "position": 30}
        source._resume = saved
        await source._capture_resume_session()
        assert source._resume is saved

    @pytest.mark.asyncio
    async def test_auto_stop_then_source_switch_keeps_the_session(self, source):
        """The documented resume case, end to end: pause long enough for the idle
        auto-stop, then switch to another source — coming back must still resume.
        """
        source._mpv = _mpv_with_props({"time-pos": 30})
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 2

        await source._auto_stop_action()       # idle timeout: saves, clears queue
        await source._do_stop()                # user switches to another source

        assert source._resume is not None
        assert source._resume["queue_index"] == 2
        assert source._resume["position"] == 30

    @pytest.mark.asyncio
    async def test_auto_stop_saves_session(self, source):
        source._mpv = _mpv_with_props({"time-pos": 30})
        source._queue = list(TRACKS)
        source._queue_index = 2

        await source._auto_stop_action()

        assert source.state == SourceState.READY
        assert source._resume is not None
        assert source._resume["queue_index"] == 2

    @pytest.mark.asyncio
    async def test_explicit_stop_forgets_session(self, source):
        source._mpv = _mpv()
        source._queue = list(TRACKS)
        source._resume = {"stale": True}

        await source.command("stop", {})

        assert source.state == SourceState.READY
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
    async def test_a_resumed_queue_still_knows_which_key_it_came_from(self, source):
        """Capture → restore → the storage-gone guard must still fire.

        `_stop_if_storage_gone` returns early on a queue attributed to no space,
        so a snapshot that drops `queue_library_id` disarms it for the whole
        resumed session: the user unplugs the key and gets a silent
        fast-forward through unreachable tracks instead of a stop. Driven
        end-to-end rather than asserting the dict key, since the round trip is
        what broke — the capture and the restore are two separate sites.
        """
        source._mpv = _mpv_with_props({"time-pos": 10, "duration": 200})
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 1
        source._queue_library_id = 3

        await source._capture_resume_session()
        source._reset_playback_state()
        assert await source._restore_resume_session() is True

        source.state_machine = Mock()
        source.state_machine.update_source_state = AsyncMock()
        await source._stop_if_storage_gone([{"library_id": 3, "mounted": False}])

        assert source._queue == []

    @pytest.mark.asyncio
    async def test_a_storage_gone_stop_leaves_nothing_to_resume(self, source):
        """The storage-gone stop must not snapshot the queue it just condemned.

        `_do_stop` captures a resume session for every stop it sees, and this
        is a caller its docstring did not anticipate. Left in place, reopening
        the library restores a now-playing pointing at an absent device: the
        titles scroll silently for a second or two before the load fails.
        """
        source._mpv = _mpv_with_props({"time-pos": 10})
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 1
        source._queue_library_id = 3
        source.state_machine = Mock()
        source.state_machine.update_source_state = AsyncMock()

        await source._stop_if_storage_gone([{"library_id": 3, "mounted": False}])

        assert source._queue == []
        assert source._resume is None

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
        # This repo is checked out ON the appliance, and _do_start spawns the
        # open-the-library rescan as a background task. Left real, it raced the
        # end of the test and reached the live Navidrome on 127.0.0.1:4533 —
        # measured, intermittently, in a full run. TestRescanOnOpen already
        # stubs it for the same reason.
        source.shares.request_scan = AsyncMock()
        mpv = _mpv_with_props({"duration": 100})
        mpv.connect = AsyncMock(return_value=True)

        with patch("backend.shared.mpv_audio_source.MpvController", return_value=mpv):
            ok = await source._do_start()

        assert ok is True
        assert source.state == SourceState.ACTIVE
        assert source._is_playing is False  # resumed paused, not auto-playing
        mpv.load_playlist.assert_awaited()


class TestRescanOnOpen:
    """Opening the library asks Navidrome to re-index.

    It is the only moment Milō can infer that freshness matters: music copied
    straight onto a NAS raises no event this appliance can observe — inotify
    crosses neither a network mount nor a mount itself — so without this the
    catalog moves only on the 6-hourly scheduled pass, which is deliberately
    slow so a sleeping NAS is not woken 24 times a day.
    """

    @staticmethod
    def _ready(source):
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._load_auto_stop_config = AsyncMock()
        source._start_monitor = Mock()
        source.shares.request_scan = AsyncMock()
        mpv = _mpv_with_props({})
        mpv.connect = AsyncMock(return_value=True)
        return mpv

    @pytest.mark.asyncio
    async def test_opening_the_library_requests_a_rescan(self, source):
        mpv = self._ready(source)
        with patch("backend.shared.mpv_audio_source.MpvController", return_value=mpv):
            assert await source._do_start() is True
        await asyncio.sleep(0)  # let the spawned task reach its await
        await source._bg.cancel_all()
        source.shares.request_scan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_wedged_catalog_cannot_delay_the_source(self, source):
        """The scan is spawned, not awaited. A Navidrome that never answers must
        cost the user nothing — the source is up for playback either way, and the
        request is the layer below's problem (it defers on a busy scanner)."""
        mpv = self._ready(source)
        never = asyncio.Event()
        source.shares.request_scan = AsyncMock(side_effect=lambda: never.wait())

        with patch("backend.shared.mpv_audio_source.MpvController", return_value=mpv):
            ok = await asyncio.wait_for(source._do_start(), timeout=1)

        assert ok is True
        await source._bg.cancel_all()

    @pytest.mark.asyncio
    async def test_a_failed_start_asks_for_nothing(self, source):
        """No mpv, no library on screen — nothing to refresh for."""
        self._ready(source)
        source._start_service_and_wait = AsyncMock(return_value=False)

        assert await source._do_start() is False
        source.shares.request_scan.assert_not_awaited()


class TestMpvRefusesTheTransportCommand:
    """mpv answers False whenever its IPC socket is down, and says so only at
    debug level.

    If these fail, a transport command the daemon never took is answered with
    `success` and the source flips its own flags: the UI draws a play button
    over a track that is still playing, or moves its now-playing to a track mpv
    never switched to.
    """

    async def _playing(self, source):
        """A loaded queue, then an mpv that refuses every transport command.

        The refusal is installed after play_context so the setup itself still
        succeeds, and the state_machine/_bg spy is attached last so only the
        refused command's broadcasts are observed.
        """
        source._mpv = _mpv()
        await source.command("play_context", {"tracks": TRACKS, "start_index": 1})
        source._mpv.pause = AsyncMock(return_value=False)
        source._mpv.resume = AsyncMock(return_value=False)
        source._mpv.seek = AsyncMock(return_value=False)
        source._mpv.set_playlist_pos = AsyncMock(return_value=False)
        source.state_machine = Mock()
        source._bg = Mock()
        source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
        return source

    @pytest.mark.asyncio
    async def test_pause_refused_keeps_the_track_playing(self, source):
        await self._playing(source)

        result = await source.command("pause", {})

        assert result["success"] is False
        assert source._is_playing is True
        source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_refused_keeps_the_track_paused(self, source):
        await self._playing(source)
        source._is_playing = False

        result = await source.command("resume", {})

        assert result["success"] is False
        assert source._is_playing is False
        source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_seek_refused_keeps_the_position(self, source):
        await self._playing(source)
        source._position = 12

        result = await source.command("seek", {"position_ms": 42000})

        assert result["success"] is False
        assert source._position == 12
        source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_track_switch_refused_keeps_the_queue_index(self, source):
        """next/play_index/prev-to-previous all land in _switch_to_index."""
        await self._playing(source)

        result = await source.command("next", {})

        assert result["success"] is False
        assert source._queue_index == 1
        assert source._loading is False  # the switch cleared its own guard
        source._bg.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_prev_restart_refused_keeps_the_playhead(self, source):
        """Past the threshold, prev restarts the current track in place."""
        await self._playing(source)
        source._mpv.get_property = AsyncMock(return_value=5)  # 5s in → restart
        source._position = 5

        result = await source.command("prev", {})

        assert result["success"] is False
        assert source._position == 5
        assert source._queue_index == 1
        source._bg.spawn.assert_not_called()
