# backend/tests/test_music_library_failure_arms.py
"""What the source, the reconciler and the scanner do when something refuses.

The happy paths of all three are held elsewhere. What had never run is the other
side of each: the arms that fire when mpv will not connect, when a saved session
cannot be reloaded, when Navidrome is not answering yet, or when a scan is still
going after ten minutes.

They matter because every one of them is the difference between a failure the
user can see and a screen that lies:

* a **resume that fails** must take back the session it announced, or the
  now-playing screen draws a playing track over an mpv that has nothing loaded;
* a **reconcile that fails** must schedule its retry, or every storage space
  keeps a null library id for the rest of the session and the frontend drops
  them all — an empty library, no message;
* a **transport command that raises** must answer the failure rather than let
  the exception reach the route as a 500 the UI cannot explain.

Nothing here spawns or connects: `MpvController` is replaced (by a Mock for the
start arms, by the measured simulation of `LibraryRig` for playback), the mount
helper is wired to explode, and `shares.request_scan` is stubbed — `_do_start`
spawns that rescan as a background task, and left real it has been measured
reaching this appliance's live Navidrome after the test ended.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from backend.sources.music_library import source as library_module
from backend.sources.music_library import storage as storage_mod
from backend.sources.music_library.libraries import NavidromeLibraryService
from backend.sources.music_library.navidrome_client import ScanRequest, ScanStatus
from backend.sources.music_library.source import MusicLibrarySource
from backend.sources.music_library.storage import StorageManager
from backend.tests.golden.harness import settle
from backend.tests.golden.test_wire_music_library import FakeNavidrome
from backend.tests.test_mpv_sessions import LibraryRig
from backend.tests.test_music_library_source import (
    TRACKS, NoCredFile, anchor_ms, details, lengths, loads_since, phase, play, session,
    session_ends,
)


def _mpv(**overrides):
    mpv = Mock()
    mpv.is_connected = True
    mpv.connect = AsyncMock(return_value=True)
    mpv.subscribe = Mock()
    mpv.observe = AsyncMock(return_value=1)
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
    src.shares.request_scan = AsyncMock()
    return src


class ClosableNavidrome(FakeNavidrome):
    """The client as `invalidate_navidrome_client` closes it."""

    async def close(self):
        return None


@pytest.fixture
def rig(monkeypatch):
    """Playback on a real state machine with mpv simulated (LibraryRig)."""
    rig = LibraryRig(monkeypatch)
    monkeypatch.setattr(library_module, "NavidromeClient", ClosableNavidrome)
    lengths(rig, TRACKS)
    return rig


@pytest.fixture
def idle_rig(monkeypatch):
    """The same, with a pause timeout that ends a paused session at once."""
    rig = LibraryRig(monkeypatch, settings={"audio.auto_stop_delay": 1})
    monkeypatch.setattr(library_module, "NavidromeClient", ClosableNavidrome)
    lengths(rig, TRACKS)
    return rig


async def catalog_goes_away(rig, monkeypatch):
    """The routes' auth-rejection path drops the client, and the cred file is
    gone when the next request tries to rebuild it."""
    monkeypatch.setattr(library_module, "NavidromeClient", NoCredFile)
    await rig.source.invalidate_navidrome_client()


# =============================================================================
# Opening the source
# =============================================================================

class TestDoStart:

    async def test_an_mpv_that_will_not_answer_its_socket_is_a_failed_start(
        self, source
    ):
        """`_do_start` returning True with no IPC leaves the service running and
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
        source._settings_service = Mock(get_setting=AsyncMock(return_value=["music_library"]))
        source._service_manager.start = AsyncMock(return_value=True)

        with patch.object(MusicLibrarySource.__bases__[0], "initialize",
                          AsyncMock(return_value=True)):
            assert await source.initialize() is True

        source._shares.initialize.assert_awaited_once()


class TestResumeThatCannotBeRestored:
    """A saved queue the next open, or a play press, cannot reopen."""

    async def test_a_restore_the_catalog_cannot_serve_loads_nothing(self, rig, monkeypatch):
        """Every entry's URL comes from the Navidrome client; without one the
        queue would be loaded as a list of dead URLs. What is kept is the
        saved queue: the open shows it, and a press once the catalog is back
        reopens it. Breaks: mpv handed unplayable URLs, or the resume view
        (frontend player, Milo-Mac) lost to a catalog that was briefly away."""
        await rig.select()
        await play(rig, start_index=1)
        await rig.tick()
        await rig.leave()
        await catalog_goes_away(rig, monkeypatch)
        mark = len(rig.mpv.sent)

        await rig.select()

        assert loads_since(rig, mark) == []
        assert session(rig) is None
        assert details(rig)["track_id"] == "s2"
        assert rig.state()["resume"]["title"] == "Two"

        monkeypatch.setattr(library_module, "NavidromeClient", ClosableNavidrome)
        result = await rig.command("resume")

        assert result["success"] is True
        assert phase(rig) == "playing"
        assert details(rig)["track_id"] == "s2"

    async def test_an_mpv_that_cannot_be_reached_reopens_nothing(self, idle_rig):
        """mpv gone when the press comes (crashed, systemd not done bringing it
        back): the press answers the failure and the saved queue stays for the
        next one. Breaks: the rotary's press answered OK over silence, or the
        resume view erased by a failure that was not the queue's."""
        await idle_rig.select()
        await play(idle_rig)
        await idle_rig.tick()
        await idle_rig.command("pause")           # idle timeout: the resume view
        idle_rig.mpv.connect = AsyncMock(return_value=False)
        await idle_rig.mpv.dies()
        await settle()
        mark = len(idle_rig.mpv.sent)

        result = await idle_rig.command("resume")

        assert result["success"] is False
        assert loads_since(idle_rig, mark) == []
        assert session(idle_rig) is None
        assert details(idle_rig)["track_id"] == "s1"

    async def test_a_restore_mpv_refuses_takes_its_own_announcement_back(self, idle_rig):
        """The restore shows the saved track at once and loads underneath. When
        mpv refuses the load, that session announced a queue with nothing
        behind it. Breaks: the screen and the lock screen (Milo-iOS) stay on a
        playing track over silence, with no banner."""
        await idle_rig.select()
        await play(idle_rig)
        await idle_rig.tick()
        await idle_rig.command("pause")
        idle_rig.mpv.loadfile = AsyncMock(return_value=None)
        mark = len(idle_rig.recorder.envelopes)

        result = await idle_rig.command("resume")

        assert result["success"] is False
        announced = [
            e["data"]["session"] is not None
            for e in idle_rig.recorder.envelopes[mark:]
            if (e["category"], e["type"]) == ("source", "state")
        ]
        assert announced == [True, False]
        assert session(idle_rig) is None
        assert session_ends(idle_rig)[-1] == "load_failed"
        assert idle_rig.errors() == ["playback_failed"]

    async def test_a_load_that_raises_is_ended_by_the_loading_watchdog(self, idle_rig):
        """A controller call that raises mid-load leaves a session that never
        heard a file open. The command answers the failure, and the loading
        watchdog ends that session with a banner. Breaks: a restore stuck
        LOADING for ever — a buffering spinner nothing will stop."""
        await idle_rig.select()
        await play(idle_rig)
        await idle_rig.tick()
        await idle_rig.command("pause")
        idle_rig.mpv.loadfile = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await idle_rig.command("resume")
        await settle()

        assert result["success"] is False
        assert session(idle_rig) is None
        assert session_ends(idle_rig)[-1] == "load_failed"
        assert idle_rig.errors() == ["playback_failed"]


# =============================================================================
# Transport commands that hit a broken link
# =============================================================================

class TestTransportFailsWithAnAnswer:
    """A controller call that raises reaches the base's command boundary,
    which answers the failure; the queue on screen must not have moved."""

    @pytest.fixture
    async def playing(self, rig):
        await rig.select()
        await play(rig, start_index=1)
        await rig.tick()
        return rig

    async def test_a_seek_over_a_dead_link_answers_the_failure(self, playing):
        """Breaks: the route answers a 500 the UI cannot explain, or the bar
        jumps to a second mpv never went to."""
        playing.mpv.seek = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await playing.command("seek", {"position_ms": 5000})

        assert result["success"] is False
        assert anchor_ms(playing) == 0

    async def test_a_track_switch_over_a_dead_link_answers_the_failure(self, playing):
        """Breaks: the player moves to the next track over mpv still playing
        the current one."""
        playing.mpv.play_index = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await playing.command("next")

        assert result["success"] is False
        assert details(playing)["queue_index"] == 1
        assert phase(playing) == "playing"

    async def test_a_restart_over_a_dead_link_answers_the_failure(self, rig):
        """`prev` near the start of the first track restarts it rather than
        stepping back; that is the arm the exception falls in."""
        await rig.select()
        await play(rig)
        await rig.tick()
        rig.mpv.seek = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await rig.command("prev")

        assert result["success"] is False
        assert details(rig)["track_id"] == "s1"
        assert phase(rig) == "playing"

    async def test_a_shuffle_toggle_over_a_dead_link_answers_the_failure(self, playing):
        """Breaks: shuffle reported on over a queue that was never reordered."""
        playing.mpv.remove_entry = AsyncMock(side_effect=RuntimeError("ipc"))

        result = await playing.command("set_shuffle", {"shuffle": True})

        assert result["success"] is False
        assert details(playing)["shuffle"] is False, "shuffle was reported on over a failed reorder"
        assert [t["id"] for t in details(playing)["queue"]] == ["s1", "s2", "s3"]

    async def test_a_reorder_mpv_refuses_leaves_the_queue_as_it_was(self, rig):
        """The queue is only rewritten after mpv accepted the new tail; writing
        it first would leave Milō's list and mpv's playlist disagreeing, and the
        next track would be the wrong one.

        Driven shuffle-OFF from a three-entry queue on purpose: it is the
        deterministic direction (the tail is the pristine order minus what has
        played), and with the playhead on the first entry the rewritten queue
        differs from the current one — on the last entry the tail is empty and
        a regression would be invisible."""
        await rig.select()
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: seq.reverse()):
            await play(rig, shuffle=True)
        await rig.tick()
        rig.mpv.accept = False

        result = await rig.command("set_shuffle", {"shuffle": False})

        assert result["success"] is False
        assert [t["id"] for t in details(rig)["queue"]] == ["s1", "s3", "s2"]
        assert details(rig)["shuffle"] is True

    async def test_shuffle_needs_the_catalog_to_rebuild_the_tail(self, playing, monkeypatch):
        """Every reordered entry needs a fresh stream URL. Breaks: the tail is
        removed from mpv and nothing put back — the queue ends after this
        track."""
        await catalog_goes_away(playing, monkeypatch)
        mark = len(playing.mpv.sent)

        result = await playing.command("set_shuffle", {"shuffle": True})

        assert result["success"] is False
        assert playing.mpv.sent[mark:] == []

    async def test_transport_on_an_empty_queue_is_refused_not_crashed(self, rig):
        """With nothing playing, a session command is refused once, at the
        command boundary. Breaks: the rotary or a stale UI reaches mpv (or an
        index into an empty queue) and the route answers 500."""
        await rig.select()
        mark = len(rig.mpv.sent)

        for command, payload in (
            ("seek", {"position_ms": 1000}), ("prev", {}), ("pause", None),
            ("next", None), ("set_shuffle", {"shuffle": True}),
        ):
            result = await rig.command(command, payload)
            assert result["success"] is False, command
        assert rig.mpv.sent[mark:] == []


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
        client.start_scan = AsyncMock(return_value=ScanRequest.STARTED)
        polls = iter([ScanStatus(available=True, scanning=True)] * 6)

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
            side_effect=[ScanStatus(available=True, scanning=True),
                         ScanStatus(available=True, scanning=False)]
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
