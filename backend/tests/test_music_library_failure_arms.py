# backend/tests/test_music_library_failure_arms.py
"""What the source, the reconciler and the scanner do when something refuses.

The happy paths of all three are held elsewhere. What had never run is the other
side of each: the arms that fire when mpv will not connect, when a saved session
cannot be reloaded, when Navidrome is not answering yet, or when a scan is still
going after ten minutes.

They matter because every one of them is the difference between a failure the
user can see and a screen that lies:

* a **resume that fails** must clear the queue, or the now-playing screen draws
  a track list over an mpv that has nothing loaded;
* a **reconcile that fails** must schedule its retry, or every storage space
  keeps a null library id for the rest of the session and the frontend drops
  them all — an empty library, no message;
* a **transport command that raises** must answer the failure rather than let
  the exception reach the route as a 500 the UI cannot explain.

Nothing here spawns or connects: `MpvController` is replaced, the mount helper
is wired to explode, and `shares.request_scan` is stubbed — `_do_start` spawns
that rescan as a background task, and left real it has been measured reaching
this appliance's live Navidrome after the test ended.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from backend.sources.music_library import storage as storage_mod
from backend.sources.music_library.libraries import NavidromeLibraryService
from backend.sources.music_library.source import MusicLibrarySource
from backend.sources.music_library.storage import StorageManager

TRACKS = [
    {"id": "s1", "title": "One", "duration": 100},
    {"id": "s2", "title": "Two", "duration": 200},
]


def _mpv(**overrides):
    mpv = Mock()
    mpv.is_connected = True
    mpv.connect = AsyncMock(return_value=True)
    mpv.load_playlist = AsyncMock(return_value=True)
    mpv.set_playlist_pos = AsyncMock(return_value=True)
    mpv.replace_playlist_tail = AsyncMock(return_value=True)
    mpv.seek = AsyncMock(return_value=True)
    mpv.pause = AsyncMock(return_value=True)
    mpv.resume = AsyncMock(return_value=True)
    mpv.stop = AsyncMock(return_value=True)
    mpv.disconnect = AsyncMock(return_value=True)
    mpv.get_property = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(mpv, key, value)
    return mpv


@pytest.fixture
def source():
    src = MusicLibrarySource({"mpv_socket": "/tmp/test-music-library-ipc.sock"})
    src._service_manager = Mock()
    src.get_navidrome_client = AsyncMock(
        return_value=Mock(stream_url=lambda song_id: f"http://nav/stream/{song_id}")
    )
    src._start_service_and_wait = AsyncMock(return_value=True)
    src._load_auto_stop_config = AsyncMock()
    src._start_monitor = Mock()
    src.shares.request_scan = AsyncMock()
    return src


# =============================================================================
# Opening the source
# =============================================================================

class TestDoStart:

    async def test_an_mpv_that_will_not_answer_its_socket_is_a_failed_start(
        self, source
    ):
        """`_do_start` returning True with no IPC leaves the source ACTIVE and
        every later transport command answering "no active queue" instead of
        the state machine reporting a source that could not start."""
        mpv = _mpv(connect=AsyncMock(return_value=False))

        with patch("backend.shared.mpv_audio_source.MpvController", return_value=mpv):
            assert await source._do_start() is False

    async def test_a_service_that_never_comes_up_is_a_failed_start(self, source):
        source._start_service_and_wait = AsyncMock(return_value=False)

        with patch("backend.shared.mpv_audio_source.MpvController", return_value=_mpv()):
            assert await source._do_start() is False

    async def test_an_unexpected_failure_tears_down_what_it_started(self, source):
        """Without the cleanup, a half-started source keeps an mpv process and a
        monitor task alive that nothing will ever stop — and the next start
        connects to the old socket."""
        source._load_auto_stop_config = AsyncMock(side_effect=RuntimeError("boom"))
        source._cleanup = AsyncMock()

        with patch("backend.shared.mpv_audio_source.MpvController", return_value=_mpv()):
            assert await source._do_start() is False

        source._cleanup.assert_awaited_once()

    async def test_the_storage_layer_comes_up_before_the_source_does(self, source):
        """`initialize` is what mounts the configured shares and starts the USB
        watcher; a source initialised without it has no storage space at all."""
        source._shares.initialize = AsyncMock()

        with patch.object(MusicLibrarySource.__bases__[0], "initialize",
                          AsyncMock(return_value=True)):
            assert await source.initialize() is True

        source._shares.initialize.assert_awaited_once()


class TestResumeThatCannotBeRestored:

    def _session(self, **overrides):
        session = {
            "queue": list(TRACKS), "queue_unshuffled": list(TRACKS),
            "queue_index": 0, "position": 0, "shuffle": False,
        }
        session.update(overrides)
        return session

    async def test_a_saved_session_is_consumed_even_when_it_cannot_be_restored(
        self, source
    ):
        """Kept, it would be retried on every open — the same failure, for ever.

        The catalog is asserted *unasked* rather than the return value alone:
        without the `self._mpv` half of the guard the restore runs on to the
        load, blows up on None, and lands in the same `except` — so a False and
        a consumed session are what both versions produce."""
        source._resume = self._session()
        source._mpv = None

        assert await source._restore_resume_session() is False
        assert source._resume is None
        source.get_navidrome_client.assert_not_awaited()

    async def test_an_empty_saved_queue_restores_nothing(self, source):
        source._mpv = _mpv()
        source._resume = self._session(queue=[])

        assert await source._restore_resume_session() is False

    async def test_a_catalog_that_is_not_ready_cannot_build_stream_urls(self, source):
        """Every entry's URL comes from the client; without one the queue would
        be loaded as a list of empty strings."""
        source._mpv = _mpv()
        source.get_navidrome_client = AsyncMock(return_value=None)
        source._resume = self._session()

        assert await source._restore_resume_session() is False
        source._mpv.load_playlist.assert_not_awaited()

    async def test_a_load_that_mpv_refuses_leaves_no_queue_behind(self, source):
        """The state is written *before* the load so the restored track shows at
        once; the reset is what undoes it. Without it the now-playing screen
        draws a track list over an mpv holding nothing."""
        source._mpv = _mpv(load_playlist=AsyncMock(return_value=False))
        source._resume = self._session()

        assert await source._restore_resume_session() is False
        assert source._queue == []
        assert source._loading is False

    async def test_a_load_that_raises_leaves_no_queue_behind(self, source):
        source._mpv = _mpv(load_playlist=AsyncMock(side_effect=RuntimeError("ipc gone")))
        source._resume = self._session()

        assert await source._restore_resume_session() is False
        assert source._queue == []
        assert source._loading is False

    async def test_a_restored_session_comes_back_paused_at_its_position(self, source):
        """The whole point: reopening the library shows where you were, stopped,
        rather than starting to play in a room nobody asked."""
        mpv = _mpv()
        mpv.get_property = AsyncMock(return_value=100)
        source._mpv = mpv
        source._resume = self._session(queue_index=1, position=42)

        assert await source._restore_resume_session() is True

        assert source._is_playing is False
        assert source._queue_index == 1
        mpv.pause.assert_awaited_once()
        mpv.seek.assert_awaited_once_with(42)


# =============================================================================
# Transport commands that hit a broken link
# =============================================================================

class TestTransportFailsWithAnAnswer:

    @pytest.fixture
    def playing(self, source):
        source._mpv = _mpv()
        source._queue = list(TRACKS)
        source._queue_unshuffled = list(TRACKS)
        source._queue_index = 1
        source._is_playing = True
        return source

    async def test_a_seek_over_a_dead_link_answers_the_failure(self, playing):
        """The behaviour, not the arm: `_handle_seek`'s own `except Exception`
        is measured redundant with `BaseAudioSource.command`, which already
        catches, logs at ERROR and answers `error_response(str(e))` — and unlike
        its two siblings below it restores no state, so no test can separate the
        two. Its siblings earn their keep by resetting `_loading`, which is what
        those tests assert."""
        playing._mpv.seek = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await playing.command("seek", {"position_ms": 5000})

        assert result["success"] is False

    async def test_a_track_switch_over_a_dead_link_answers_the_failure(self, playing):
        playing._queue_index = 0  # `next` at the last entry never reaches mpv
        playing._mpv.set_playlist_pos = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await playing.command("next", {})

        assert result["success"] is False
        assert playing._loading is False, "a failed switch left the source loading"

    async def test_a_restart_over_a_dead_link_answers_the_failure(self, playing):
        """`prev` near the start of a track restarts it rather than stepping
        back; that is the arm the exception falls in."""
        playing._position = 0
        playing._queue_index = 0
        playing._mpv.seek = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await playing.command("prev", {})

        assert result["success"] is False

    async def test_a_shuffle_toggle_over_a_dead_link_answers_the_failure(self, playing):
        playing._mpv.replace_playlist_tail = AsyncMock(side_effect=RuntimeError("ipc"))

        result = await playing.command("set_shuffle", {"shuffle": True})

        assert result["success"] is False
        assert playing._loading is False
        assert playing._shuffle is False, "shuffle was reported on over a failed reorder"

    async def test_a_reorder_mpv_refuses_leaves_the_queue_as_it_was(self, playing):
        """The queue is only rewritten after mpv accepted the new tail; writing
        it first would leave Milō's list and mpv's playlist disagreeing, and the
        next track would be the wrong one.

        Driven shuffle-OFF and from a three-entry queue on purpose: it is the
        deterministic direction (the tail is the pristine order minus what has
        played), and it is the only shape where the rewritten queue actually
        differs from the current one — with the playhead on the last entry the
        tail is empty and `head + tail` reproduces the queue exactly, so the
        regression would be invisible."""
        s1, s2, s3 = ({"id": f"s{n}", "duration": 100} for n in (1, 2, 3))
        playing._queue_unshuffled = [s1, s2, s3]
        playing._queue = [s2, s3, s1]
        playing._queue_index = 0
        playing._shuffle = True
        playing._mpv.replace_playlist_tail = AsyncMock(return_value=False)

        result = await playing.command("set_shuffle", {"shuffle": False})

        assert result["success"] is False
        assert playing._queue == [s2, s3, s1]
        assert playing._shuffle is True

    async def test_shuffle_needs_the_catalog_to_rebuild_the_tail(self, playing):
        """Every reordered entry needs a fresh stream URL."""
        playing.get_navidrome_client = AsyncMock(return_value=None)

        result = await playing.command("set_shuffle", {"shuffle": True})

        assert result["success"] is False
        playing._mpv.replace_playlist_tail.assert_not_awaited()

    async def test_transport_on_an_empty_queue_is_refused_not_crashed(self, playing):
        playing._queue = []

        for command, payload in (
            ("seek", {"position_ms": 1000}), ("prev", {}),
            ("next", None), ("set_shuffle", {"shuffle": True}),
        ):
            result = await playing.command(command, payload)
            assert result["success"] is False, command


# =============================================================================
# The reconciler's plumbing
# =============================================================================

class TestReconcilerPlumbing:

    @pytest.fixture
    def service(self):
        return NavidromeLibraryService()

    async def test_the_admin_client_is_built_once_and_late(self, service):
        """The cred file is the same one the Subsonic client waits for, and
        first-boot provisioning may not have written it when the service is
        constructed."""
        built = Mock()
        with patch("backend.sources.music_library.libraries."
                   "NavidromeAdminClient.from_cred_file", return_value=built) as factory:
            assert await service._get_admin() is built
            assert await service._get_admin() is built

        factory.assert_called_once()

    async def test_no_cred_file_yet_means_no_reconcile_and_a_retry(self, service):
        """Not a silent success: without the retry every storage space keeps a
        null library id until the next mount change, and the frontend drops
        those — an empty library for the whole session."""
        service._get_admin = AsyncMock(return_value=None)
        service._schedule_retry = Mock()

        assert await service.reconcile({"/media/milo/nas": "NAS"}, {"/media/milo/nas"}) is False
        service._schedule_retry.assert_called_once()

    async def test_a_second_failure_does_not_start_a_second_retry_loop(self, service):
        """Two mount events during a Navidrome outage would otherwise each spawn
        a loop, and each loop reconciles the same set for ever."""
        service._get_admin = AsyncMock(return_value=None)
        service._bg = MagicMock()

        await service.reconcile({"/media/milo/nas": "NAS"}, set())
        await service.reconcile({"/media/milo/nas": "NAS"}, set())

        assert service._bg.spawn.call_count == 1

    async def test_cleanup_stops_the_retry_loop_and_closes_the_session(self, service):
        """cleanup() runs from the lifespan teardown; a retry left running holds
        an aiohttp session open against a sidecar that is going down with us."""
        admin = Mock()
        admin.close = AsyncMock()
        service._admin = admin
        service._bg = MagicMock()
        service._bg.cancel_all = AsyncMock()

        await service.cleanup()

        service._bg.cancel_all.assert_awaited_once()
        admin.close.assert_awaited_once()
        assert service._admin is None

    async def test_cleanup_before_any_client_was_built_is_harmless(self, service):
        service._bg = MagicMock()
        service._bg.cancel_all = AsyncMock()

        await service.cleanup()

        assert service._admin is None


# =============================================================================
# The scan the mount is owed
# =============================================================================

class TestDeferredScan:

    @pytest.fixture
    def navidrome(self):
        """A scanner that refuses to be polled for ever.

        Deliberate, and the reason it is not a plain `return_value`: with the
        ceiling removed the wait loop never ends, so the mutation that proves
        the ceiling matters would make the suite *hang* rather than fail — it
        held a core for two and a half minutes before this bound existed. A
        double that runs out of answers turns that into a red in milliseconds."""
        client = AsyncMock()
        client.start_scan = AsyncMock(return_value=True)
        polls = iter([{"scanning": True}] * 6)

        async def _status():
            try:
                return next(polls)
            except StopIteration:
                raise AssertionError("polled past the ceiling — it never fired")

        client.get_scan_status = AsyncMock(side_effect=_status)
        return client

    @pytest.fixture
    def manager(self, navidrome, monkeypatch):
        def _never(*args, **kwargs):
            raise AssertionError(f"a mount helper was spawned: {args}")

        monkeypatch.setattr(storage_mod.asyncio, "create_subprocess_exec", _never)
        monkeypatch.setattr(storage_mod, "_SCAN_WAIT_POLL_S", 0.01)
        monkeypatch.setattr(storage_mod, "_SCAN_WAIT_CEILING_S", 0.03)
        return StorageManager(AsyncMock(return_value=navidrome), AsyncMock())

    async def test_a_scan_that_never_ends_is_left_to_the_scheduled_pass(
        self, manager, navidrome
    ):
        """Giving up at the ceiling costs latency, never a catalog: Navidrome's
        own incremental pass sees the mount regardless. Waiting for ever instead
        would hold the task and the poll open for the life of the process."""
        await manager._scan_when_idle()

        navidrome.start_scan.assert_not_awaited()

    async def test_the_owed_scan_runs_as_soon_as_the_scanner_goes_idle(
        self, manager, navidrome
    ):
        navidrome.get_scan_status = AsyncMock(
            side_effect=[{"scanning": True}, {"scanning": False}]
        )

        await manager._scan_when_idle()

        navidrome.start_scan.assert_awaited_once()

    async def test_a_navidrome_that_is_not_there_is_nothing_to_report(
        self, navidrome, monkeypatch, caplog
    ):
        """Not a failure — the sidecar simply has not been reached yet, and the
        mount is left to the scheduled pass.

        Asserted on the log rather than on "it did not raise": without the guard
        the wait falls straight into `_start_scan(None)`, whose AttributeError
        lands in the same `except` and is *also* not a raise. What separates
        them is that one is silent and the other files a warning an operator
        reads."""
        monkeypatch.setattr(storage_mod, "_SCAN_WAIT_POLL_S", 0.01)
        manager = StorageManager(AsyncMock(return_value=None), AsyncMock())

        with caplog.at_level("WARNING", logger="source.music_library.storage"):
            await manager._scan_when_idle()

        assert caplog.records == []

    async def test_a_refused_scan_is_logged_rather_than_raised(self, manager, navidrome):
        """`request_scan` is called inline from the mount path — an exception
        here is a mount that reports failure over a scan."""
        navidrome.start_scan = AsyncMock(side_effect=RuntimeError("boom"))

        await manager._start_scan(navidrome)

    async def test_a_helper_that_cannot_be_spawned_is_reported_as_no_mountpoint(
        self, navidrome, monkeypatch
    ):
        """A missing sudoers rule or an absent milo-mount must degrade to "not
        mounted", never raise into the udev callback."""
        manager = StorageManager(AsyncMock(return_value=navidrome), AsyncMock())
        monkeypatch.setattr(
            storage_mod.asyncio, "create_subprocess_exec",
            AsyncMock(side_effect=PermissionError("sudo: a password is required")),
        )

        assert await manager._run_helper("/usr/local/bin/milo-mount", "/dev/sda1",
                                         capture=True) is None
