# backend/tests/test_podcast_playback.py
"""The podcast playback path: `_handle_play_episode`, `_wait_and_seek`,
`refresh_metadata`, the mpv-disconnect hook and the periodic progress save.

`test_podcast_source.py` covers the command surface and episode-end detection;
what it never entered is the body that actually starts an episode. Measured at
39ff9daf: `_handle_play_episode` 19 uncovered lines and `_wait_and_seek` all 17
— the resume seek, the single most visible thing this source does, had never
run.

Three invariants live in here and nowhere else:

* **`_loading` is armed before the outgoing episode is stopped.** The comment in
  the source names the failure: `await self._mpv.stop()` makes mpv idle while
  `_is_playing` and `_current_episode` still point at the episode being left, a
  monitor tick landing in that window reads it as "finished", and the outgoing
  episode is marked completed and dropped from the queue with nothing wrong on
  screen. `test_podcast_source.py::TestSwitchingEpisodesGuardsTheOutgoingOne`
  proves the tick is harmless *given* the flag; this proves the flag is set at
  the right moment.
* **a stream that fails to load rolls the state all the way back.** Anything
  left behind — `_loading`, `_is_buffering`, `_current_episode` — leaves the UI
  on a spinner for an episode that is not playing, and `_loading` in particular
  makes every later monitor tick return early.
* **the resume seek waits for the stream, and gives up.** Seeking before mpv
  knows the duration seeks nothing; waiting forever wedges `play_episode`.

The mpv double answers property reads by name, the way the real IPC controller
does, so a test cannot ask for a property the production code never requests.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, Mock

from backend.core.models.audio_state import SourceState
from backend.sources.podcast.models import PlayEpisodeParams
from backend.sources.podcast.source import PodcastSource

EPISODE = {
    "uuid": "ep-1",
    "name": "Episode One",
    "description": "About things",
    "image_url": "http://img/ep1.jpg",
    "duration": 1800,
    "audio_url": "http://cdn/ep1.mp3",
    "podcast": {"uuid": "feed-1", "name": "Show One"},
}


class FakeMpv:
    """Stands in for `MpvController`.

    Property reads are served from a dict keyed by the exact mpv property name,
    so a test that pins a property the source does not actually poll fails on
    the lookup instead of quietly passing.
    """

    def __init__(self, **properties):
        self.props = {"duration": 1800.0, "playback-time": 0.0,
                      "pause": False, "idle-active": False}
        self.props.update(properties)
        self.is_connected = True
        self.calls = []
        self.load_ok = True

    async def connect(self):
        self.calls.append(("connect",))
        return True

    async def disconnect(self):
        self.calls.append(("disconnect",))

    async def get_property(self, name):
        self.calls.append(("get", name))
        return self.props[name]

    async def set_property(self, name, value):
        self.calls.append(("set", name, value))
        self.props[name] = value
        return True

    async def load_stream(self, url):
        self.calls.append(("load_stream", url))
        return self.load_ok

    async def stop(self):
        self.calls.append(("stop",))
        return True

    async def seek(self, position):
        self.calls.append(("seek", position))
        self.props["playback-time"] = float(position)
        return True

    async def pause(self):
        self.calls.append(("pause",))
        return True

    async def resume(self):
        self.calls.append(("resume",))
        return True

    def verbs(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def source(tmp_path):
    # The socket path stays inside pytest's own tmpdir: a boot test that loses
    # its MpvController double would otherwise attempt a connect on a path the
    # rule-5 probe reports as reaching outside the sandbox.
    src = PodcastSource({"mpv_socket": str(tmp_path / "podcast-ipc.sock")})
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    src._podcast_data = AsyncMock()
    src._podcast_data.get_setting = AsyncMock(return_value=1.0)
    src._podcast_data.get_playback_progress = AsyncMock(return_value=None)
    src._podcast_api = AsyncMock()
    src._podcast_api.get_episode = AsyncMock(return_value=dict(EPISODE))
    src._mpv = FakeMpv()
    src.broadcast_error = Mock()
    src.broadcast_error_cleared = Mock()
    src.set_state = Mock()
    src.emit_connection_state = Mock()
    src._start_progress_save = Mock()
    src._stop_progress_save = Mock()
    return src


@pytest.fixture
def instant_sleep(monkeypatch):
    """Collapse the module's waits.

    `_wait_and_seek` polls 50 times at 0.2 s plus a 0.3 s read-back per attempt,
    so a test of the give-up path costs ten wall-clock seconds and one of a
    retry costs half a second per turn. `source.py` does a bare `import
    asyncio`, so the double lands on the shared module and would eat this
    test's own awaits too — it therefore delegates to the real sleep with a
    zero delay rather than returning without yielding.
    """
    real = asyncio.sleep

    async def now(_delay):
        await real(0)

    monkeypatch.setattr("backend.sources.podcast.source.asyncio.sleep", now)


async def play(source, uuid="ep-1"):
    return await source._handle_play_episode(PlayEpisodeParams(episode_uuid=uuid))


class TestStartingAnEpisode:
    async def test_a_playable_episode_reports_success_and_names_it(self, source):
        """Non-triviality: this path can succeed, so every refusal below is the
        guard and not a broken double."""
        result = await play(source)

        assert result["success"] is True
        assert "Episode One" in result["message"]

    async def test_the_audio_url_from_the_catalogue_is_what_mpv_loads(self, source):
        """The only thing that decides which bytes play. The catalogue answer is
        looked up by uuid at play time — the UI carries no URL."""
        await play(source)

        assert ("load_stream", "http://cdn/ep1.mp3") in source._mpv.calls
        assert source._podcast_api.get_episode.await_args.args[0] == "ep-1"

    async def test_an_episode_the_catalogue_cannot_serve_is_a_refusal(self, source):
        source._podcast_api.get_episode.return_value = None

        result = await play(source, "gone")

        assert result["success"] is False
        assert "gone" in result["error"]
        assert "load_stream" not in source._mpv.verbs()

    async def test_an_episode_with_no_audio_url_is_a_refusal_not_a_crash(self, source):
        """Podcast Index serves entries whose enclosure is missing; loading
        `None` into mpv is an error with no episode name in it."""
        source._podcast_api.get_episode.return_value = {"uuid": "ep-1", "name": "One"}

        result = await play(source)

        assert result["success"] is False
        assert "load_stream" not in source._mpv.verbs()

    async def test_the_state_is_published_buffering_before_the_stream_loads(
        self, source
    ):
        """The spinner. `_update_connection_state` runs once before
        `load_stream` and again after; without the first the card stays on the
        previous episode for the whole buffering window."""
        states = []
        source.emit_connection_state = Mock(
            side_effect=lambda active, core, extras: states.append(
                (core.is_buffering, core.is_playing)
            )
        )

        await play(source)

        assert states[0] == (True, False)
        assert states[-1] == (False, True)

    async def test_the_saved_speed_is_applied_to_the_new_stream(self, source):
        """mpv's speed is per-stream: loading a new file resets it to 1.0, so
        the stored speed has to be pushed again on every episode or the setting
        silently applies only to the episode it was changed on."""
        source._playback_speed = 1.5

        await play(source)

        assert ("set", "speed", 1.5) in source._mpv.calls

    async def test_a_stream_mpv_reports_paused_after_load_is_unpaused(self, source):
        """go-through for the observed go-mpv behaviour: `load_stream` can land
        with `pause=True`, and nothing else would ever clear it — the episode
        would sit silent with the UI saying it plays."""
        source._mpv = FakeMpv(pause=True)

        await play(source)

        assert ("set", "pause", False) in source._mpv.calls

    async def test_a_stream_already_running_is_not_unpaused_again(self, source):
        source._mpv = FakeMpv(pause=False)

        await play(source)

        assert ("set", "pause", False) not in source._mpv.calls

    async def test_the_periodic_progress_save_is_armed_once_playing(self, source):
        """Without it nothing persists between the start of an episode and its
        end, so a power cut loses the whole listen."""
        await play(source)

        source._start_progress_save.assert_called_once()

    async def test_a_successful_start_clears_the_error_banner(self, source):
        """A previous failure leaves the WebSocket error banner up; only a
        successful start takes it down."""
        await play(source)

        source.broadcast_error_cleared.assert_called_once()


class TestTheOutgoingEpisode:
    async def test_switching_saves_the_outgoing_position_before_stopping_mpv(
        self, source
    ):
        """Order is the assertion: `mpv.stop()` zeroes the playhead, so a save
        after it stores 0 and the previous episode restarts from the top."""
        order = []
        source._current_episode = {"uuid": "ep-0", "name": "Zero", "podcast": {}}
        source._position = 640
        source._duration = 1800
        source._is_playing = True
        source._podcast_data.update_playback_progress = AsyncMock(
            side_effect=lambda **kw: order.append(("save", kw["position"]))
        )
        real_stop = source._mpv.stop
        source._mpv.stop = lambda: (order.append(("stop",)), real_stop())[1]

        await play(source)

        assert order == [("save", 640), ("stop",)]

    async def test_the_loading_flag_is_armed_before_the_outgoing_stop(self, source):
        """The documented race. A monitor tick that lands between `mpv.stop()`
        and the new stream reads mpv as idle while `_current_episode` still
        names the outgoing episode, and completes it. `_loading` is what makes
        `_on_monitor_tick` return early, so it must already be set when `stop`
        is called — not after.

        Measured by making the double observe the flag at the moment of the
        call rather than after the fact."""
        seen = {}
        source._current_episode = {"uuid": "ep-0", "podcast": {}}
        source._position = 10
        source._is_playing = True
        real_stop = source._mpv.stop

        async def watching_stop():
            seen["loading"] = source._loading
            return await real_stop()

        source._mpv.stop = watching_stop

        await play(source)

        assert seen["loading"] is True

    async def test_nothing_is_stopped_when_no_episode_was_playing(self, source):
        source._is_playing = False

        await play(source)

        assert "stop" not in source._mpv.verbs()


class TestResumingWhereTheOwnerLeftOff:
    async def test_a_stored_position_past_the_threshold_is_seeked_to(self, source):
        source._podcast_data.get_playback_progress.return_value = {"position": 640}

        await play(source)

        assert ("seek", 640) in source._mpv.calls

    async def test_a_position_inside_the_first_ten_seconds_is_not_a_resume(
        self, source
    ):
        """Resuming from 4 s is worse than starting over: the player jumps, and
        the listener hears the intro twice."""
        source._podcast_data.get_playback_progress.return_value = {"position": 4}

        await play(source)

        assert "seek" not in source._mpv.verbs()

    async def test_an_episode_never_started_is_not_seeked(self, source):
        source._podcast_data.get_playback_progress.return_value = None

        await play(source)

        assert "seek" not in source._mpv.verbs()

    async def test_the_resume_position_is_published_before_the_stream_loads(
        self, source
    ):
        """The progress bar has to open at the resume point, not at 0:00 — it is
        published with the buffering state, before mpv has the file."""
        source._podcast_data.get_playback_progress.return_value = {"position": 640}
        first = {}
        source.emit_connection_state = Mock(
            side_effect=lambda active, core, extras: first.setdefault(
                "position", core.position
            )
        )

        await play(source)

        assert first["position"] == 640_000


class TestWaitingForTheStreamBeforeSeeking:
    """`_wait_and_seek` — 17 lines, none of which had ever run."""

    async def test_no_seek_is_issued_until_mpv_knows_the_duration(
        self, source, instant_sleep
    ):
        """A seek on a stream mpv has not opened yet is discarded, and the
        episode plays from 0:00 with the UI showing the resume point."""
        mpv = FakeMpv(duration=None)
        source._mpv = mpv
        durations = [None, None, 1800.0]

        async def get_property(name):
            mpv.calls.append(("get", name))
            if name == "duration":
                return durations.pop(0) if durations else 1800.0
            return mpv.props[name]

        mpv.get_property = get_property

        await source._wait_and_seek(640)

        # Nothing but duration reads until mpv finally answers with one.
        assert mpv.calls == [
            ("get", "duration"), ("get", "duration"), ("get", "duration"),
            ("seek", 640), ("get", "playback-time"),
        ]

    async def test_a_zero_duration_is_not_a_ready_stream(self, source, instant_sleep):
        """mpv reports `duration=0` for a stream whose header has not been
        parsed. Treating 0 as ready seeks into nothing."""
        mpv = FakeMpv(duration=0.0)
        source._mpv = mpv

        await asyncio.wait_for(source._wait_and_seek(640), timeout=5)

        assert "seek" not in mpv.verbs()

    async def test_a_stream_that_never_becomes_ready_gives_up(
        self, source, instant_sleep
    ):
        """The bound is what keeps `play_episode` from hanging forever on a dead
        CDN — the request would never answer and the UI would spin."""
        mpv = FakeMpv(duration=None)
        source._mpv = mpv

        await asyncio.wait_for(source._wait_and_seek(640), timeout=5)

        assert "seek" not in mpv.verbs()

    async def test_the_seek_is_confirmed_by_reading_the_playhead_back(
        self, source, instant_sleep
    ):
        """mpv accepts a seek command and can still land elsewhere; the
        read-back is the only confirmation there is."""
        await source._wait_and_seek(640)

        assert ("get", "playback-time") in source._mpv.calls

    async def test_the_read_back_only_feeds_a_log_and_never_retries(
        self, source, instant_sleep
    ):
        """Measured behaviour, pinned because the code reads as the opposite.

        The comment says "Verify seek succeeded" and the read looks like a
        confirmation, but the `return` underneath it sits at the same
        indentation as the `if` — so it fires whether or not `playback-time`
        could be read, and the branch's whole body is a log line. A playhead
        mpv will not report is therefore not a retry, it is one wasted 0.3 s
        read on every resume.

        Left as it is rather than "fixed": `MpvController.get_property` is
        fail-open (it answers None, it does not raise), and mpv has the file
        open by the time `duration > 0`, so the unconfirmed case is a transient
        with nothing to recover. Turning the read into a real retry would
        change resume timing on the appliance for a failure nobody has
        measured. What is worth having is that the next reader finds the
        behaviour written down instead of trusting the comment."""
        mpv = FakeMpv()
        source._mpv = mpv

        async def get_property(name):
            mpv.calls.append(("get", name))
            return None if name == "playback-time" else mpv.props[name]

        mpv.get_property = get_property

        await asyncio.wait_for(source._wait_and_seek(640), timeout=5)

        assert len([c for c in mpv.calls if c[0] == "seek"]) == 1


class TestAStreamThatWillNotLoad:
    async def test_a_refused_load_is_reported_as_a_failure(self, source):
        source._mpv.load_ok = False

        result = await play(source)

        assert result["success"] is False

    async def test_a_refused_load_raises_the_error_banner(self, source):
        source._mpv.load_ok = False

        await play(source)

        source.broadcast_error.assert_called_once()
        source.broadcast_error_cleared.assert_not_called()

    async def test_a_refused_load_leaves_no_episode_behind(self, source):
        """`_current_episode` still set is a card for an episode that is not
        playing, and `_loading` still set makes every later monitor tick return
        early — the source goes deaf until the next start."""
        source._mpv.load_ok = False

        await play(source)

        assert source._current_episode is None
        assert source._loading is False
        assert source._is_buffering is False

    async def test_a_crash_mid_start_is_reported_and_clears_the_loading_flag(
        self, source
    ):
        """Same reasoning as above for the outer handler: anything thrown below
        `_loading = True` has to put it back."""
        source._mpv.load_stream = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await play(source)

        assert result["success"] is False
        assert "ipc gone" in result["error"]
        assert source._loading is False
        assert source._is_buffering is False
        source.broadcast_error.assert_called_once()


class TestTheHandshakePlayhead:
    """`refresh_metadata` — what a (re)connecting client is told the playhead is.

    Called from the WebSocket handshake via `state.refresh_active_metadata()`.
    Without it the client gets the last periodic broadcast, up to
    POSITION_SYNC_INTERVAL seconds stale.
    """

    async def test_the_live_playhead_replaces_the_cached_one(self, source):
        source._current_episode = dict(EPISODE)
        source._position = 10
        source._mpv = FakeMpv(**{"playback-time": 642.0, "duration": 1800.0})

        assert await source.refresh_metadata() is True
        assert source._position == 642
        assert source._metadata["position"] == 642_000

    async def test_the_play_state_is_re_read_from_mpv_not_trusted_from_cache(
        self, source
    ):
        """A reconnect racing a pause still in flight would otherwise stamp a
        stale flag, and the client opens showing a pause button on a paused
        episode."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._mpv = FakeMpv(pause=True)

        await source.refresh_metadata()

        assert source._is_playing is False

    async def test_a_buffering_stream_keeps_its_play_state(self, source):
        """mpv answers `pause=False` before the stream is ready, so trusting it
        while buffering flips the card to "playing" on a silent stream."""
        source._current_episode = dict(EPISODE)
        source._is_buffering = True
        source._is_playing = False
        source._mpv = FakeMpv(pause=False)

        await source.refresh_metadata()

        assert source._is_playing is False

    async def test_a_property_mpv_will_not_answer_leaves_the_value_alone(self, source):
        """A cache stall makes `playback-time` read None; overwriting the
        position with it would send the client back to 0:00."""
        source._current_episode = dict(EPISODE)
        source._position = 642
        source._duration = 1800
        source._mpv = FakeMpv(**{"playback-time": None, "duration": None})

        await source.refresh_metadata()

        assert (source._position, source._duration) == (642, 1800)

    async def test_nothing_is_refreshed_with_no_episode(self, source):
        source._current_episode = None

        assert await source.refresh_metadata() is False

    async def test_nothing_is_refreshed_once_mpv_is_gone(self, source):
        source._current_episode = dict(EPISODE)
        source._mpv = None

        assert await source.refresh_metadata() is False

    async def test_nothing_is_refreshed_over_a_dead_link(self, source):
        source._current_episode = dict(EPISODE)
        source._mpv.is_connected = False

        assert await source.refresh_metadata() is False


class TestMpvGoingAwayUnderPlayback:
    async def test_the_position_is_persisted_before_the_state_is_cleared(self, source):
        """The order is the whole value of the hook: clearing first loses the
        listen, and mpv dying mid-episode is exactly when the owner most wants
        to come back to where they were."""
        source._current_episode = dict(EPISODE)
        source._position = 640
        source._duration = 1800

        await source._on_mpv_disconnect()

        saved = source._podcast_data.update_playback_progress.await_args.kwargs
        assert (saved["episode_uuid"], saved["position"]) == ("ep-1", 640)
        assert source._current_episode is None
        assert source._position == 0

    async def test_an_episode_never_started_saves_nothing(self, source):
        """Position 0 would overwrite a real stored position with a zero row and
        drop the episode out of the in-progress queue."""
        source._current_episode = dict(EPISODE)
        source._position = 0

        await source._on_mpv_disconnect()

        source._podcast_data.update_playback_progress.assert_not_awaited()

    async def test_the_periodic_save_is_stopped_with_the_link(self, source):
        """It would otherwise keep firing every 10 s against a dead controller
        for the life of the process."""
        source._current_episode = dict(EPISODE)
        source._position = 640

        await source._on_mpv_disconnect()

        source._stop_progress_save.assert_called_once()

    async def test_the_metadata_is_emptied_so_no_card_survives(self, source):
        source._current_episode = dict(EPISODE)
        source._position = 640
        source._metadata = {"episode_name": "Episode One"}

        await source._on_mpv_disconnect()

        assert source._metadata == {}


class TestThePeriodicProgressSave:
    async def test_a_failing_save_does_not_kill_the_loop(self, source, monkeypatch):
        """The loop-body guard the doctrine requires. Without it one transient
        disk error stops every later save until the backend restarts — and
        nothing says so."""
        source._is_playing = True
        source._current_episode = dict(EPISODE)
        source._position = 100
        ticks = []

        async def no_wait(_delay):
            ticks.append(1)
            if len(ticks) > 3:
                raise asyncio.CancelledError

        monkeypatch.setattr("backend.sources.podcast.source.asyncio.sleep", no_wait)
        source._save_progress = AsyncMock(side_effect=OSError("disk full"))

        await source._progress_save_loop()

        assert source._save_progress.await_count == 3

    async def test_a_playhead_still_at_zero_is_never_written(self, source):
        """`_save_progress`'s own guard, reached through the one caller that
        does not repeat it. The periodic loop fires every 10 s from the moment
        an episode starts, so the first tick of a slow-starting stream has
        `_position == 0` — writing that row overwrites a real stored position
        with zero, and `get_in_progress_episodes` drops the episode out of the
        queue for a listen that is under way.

        Reached directly because `_on_mpv_disconnect` and `_do_stop` each carry
        the same test above them and would shadow this one."""
        source._current_episode = dict(EPISODE)
        source._position = 0
        source._duration = 1800

        await source._save_progress()

        source._podcast_data.update_playback_progress.assert_not_awaited()

    async def test_a_paused_episode_is_not_re_saved_every_tick(self, source, monkeypatch):
        """A paused episode's row is already written by the pause handler;
        rewriting it every 10 s is a write to `/var/lib/milo` per tick for as
        long as the pause lasts."""
        source._is_playing = False
        source._current_episode = dict(EPISODE)
        ticks = []

        async def no_wait(_delay):
            ticks.append(1)
            if len(ticks) > 2:
                raise asyncio.CancelledError

        monkeypatch.setattr("backend.sources.podcast.source.asyncio.sleep", no_wait)
        source._save_progress = AsyncMock()

        await source._progress_save_loop()

        source._save_progress.assert_not_awaited()

    async def test_arming_the_save_twice_stops_the_first_loop(self, source):
        """`_handle_play_episode` arms this on every episode. Without the
        cancel, each switch leaves another loop behind writing the same file
        every 10 s — three episodes in and `podcast_data.json` has three
        writers racing each other.

        Probed with `done()` rather than awaited: `_progress_save_loop` catches
        `CancelledError` and returns normally, so awaiting a cancelled one
        cannot tell a stopped loop from a running one (the 20th blind spot,
        `wait_for` does not bound a coroutine that suppresses the cancel)."""
        src = PodcastSource({"mpv_socket": str(source._mpv_socket)})
        src._podcast_data = AsyncMock()
        src._start_progress_save()
        first = src._progress_save_task
        src._start_progress_save()
        second = src._progress_save_task
        try:
            for _ in range(5):
                if first.done():
                    break
                await asyncio.sleep(0)

            assert first.done()
            assert not second.done()
        finally:
            first.cancel()
            second.cancel()


class TestBootFailureArms:
    async def test_a_service_that_will_not_start_stops_the_boot(
        self, source, monkeypatch
    ):
        """Everything below the guard is made to succeed, so False can only
        come from the guard itself — otherwise the real MpvController fails to
        connect and the test passes for the wrong reason."""
        monkeypatch.setattr(
            "backend.shared.mpv_audio_source.MpvController", lambda **kw: FakeMpv()
        )
        source._start_monitor = Mock()
        source._load_auto_stop_config = AsyncMock()
        source._start_service_and_wait = AsyncMock(return_value=False)

        assert await source._do_start() is False

    async def test_an_mpv_that_will_not_answer_stops_the_boot(self, source, monkeypatch):
        """A source reported started with no IPC link accepts commands that
        cannot reach mpv, and every one of them fails individually."""
        source._start_service_and_wait = AsyncMock(return_value=True)
        dead = FakeMpv()
        dead.connect = AsyncMock(return_value=False)
        monkeypatch.setattr(
            "backend.shared.mpv_audio_source.MpvController", lambda **kw: dead
        )

        assert await source._do_start() is False

    async def test_the_stored_speed_is_restored_at_boot(self, source, monkeypatch):
        """The speed control is a setting, not a per-session choice; without
        this every restart silently drops the owner back to 1.0×."""
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._podcast_data.get_setting = AsyncMock(return_value=1.5)
        monkeypatch.setattr(
            "backend.shared.mpv_audio_source.MpvController", lambda **kw: FakeMpv()
        )
        source._start_monitor = Mock()
        source._load_auto_stop_config = AsyncMock()

        assert await source._do_start() is True
        assert source._playback_speed == 1.5

    async def test_a_crash_during_boot_tears_down_what_was_built(self, source, monkeypatch):
        """Half a start is worse than none: the mpv link and the monitor task
        would outlive the failure with no owner."""
        source._start_service_and_wait = AsyncMock(return_value=True)
        monkeypatch.setattr(
            "backend.shared.mpv_audio_source.MpvController", lambda **kw: FakeMpv()
        )
        source._load_auto_stop_config = AsyncMock(side_effect=RuntimeError("no settings"))
        source._cleanup = AsyncMock()

        assert await source._do_start() is False
        source._cleanup.assert_awaited_once()


class TestStopping:
    async def test_a_playing_episode_is_persisted_on_the_way_out(self, source):
        source._current_episode = dict(EPISODE)
        source._position = 640
        source._duration = 1800
        source._cleanup = AsyncMock()
        source._stop_service = AsyncMock(return_value=True)

        assert await source._do_stop() is True
        assert source._podcast_data.update_playback_progress.await_args.kwargs[
            "position"
        ] == 640

    async def test_an_episode_at_the_very_start_is_not_persisted(self, source):
        source._current_episode = dict(EPISODE)
        source._position = 0
        source._cleanup = AsyncMock()
        source._stop_service = AsyncMock(return_value=True)

        await source._do_stop()

        source._podcast_data.update_playback_progress.assert_not_awaited()


class TestBoot:
    async def test_the_data_file_is_read_at_boot_so_a_schema_drift_fails_loud(
        self, source
    ):
        """The whole point of `initialize` here: a v1 `podcast_data.json` must
        raise the banner at boot, not on the first subscription read hours
        later."""
        source._podcast_data.initialize = AsyncMock(
            side_effect=RuntimeError("missing required keys")
        )

        with pytest.raises(RuntimeError, match="missing required keys"):
            await source.initialize()


class TestTheEndOfAnEpisode:
    async def test_a_persistence_failure_does_not_strand_the_source_as_playing(
        self, source
    ):
        """The guard the source's own comment asks for. If marking completion
        threw, `_is_playing` would stay True with no episode behind it and the
        card would never come down."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._position = 1790
        source._duration = 1800
        source._mpv = FakeMpv(**{"idle-active": True, "playback-time": 1790.0})
        source._podcast_data.mark_episode_completed = AsyncMock(
            side_effect=OSError("disk full")
        )

        await source._on_monitor_tick()

        assert source._is_playing is False
        assert source._current_episode is None
        assert source.set_state.call_args.args[0] is SourceState.READY

    async def test_the_duration_becoming_known_is_broadcast_at_once(self, source):
        """Podcast Index serves `duration: null` often enough that the progress
        bar would otherwise be missing for up to a whole sync interval."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._duration = 0
        source._mpv = FakeMpv(duration=1800.0, **{"playback-time": 12.0})
        source.broadcast_position_update = Mock()

        await source._on_monitor_tick()

        source.broadcast_position_update.assert_called_once_with(12_000, 1_800_000)

    async def test_a_duration_already_known_is_not_re_broadcast(self, source):
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._duration = 1800
        source._position_sync_due = Mock(return_value=False)
        source._mpv = FakeMpv(duration=1800.0, **{"playback-time": 12.0})
        source.broadcast_position_update = Mock()

        await source._on_monitor_tick()

        source.broadcast_position_update.assert_not_called()

    async def test_the_eager_duration_push_is_not_paired_with_a_periodic_one(
        self, source
    ):
        """The one tick where both broadcasts want to fire: the duration became
        known *and* the periodic sync is due. Counting, not presence — two
        identical position events on the same tick is exactly what a membership
        assertion cannot see (12th blind spot)."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._duration = 0
        source._position_sync_due = Mock(return_value=True)
        source._mpv = FakeMpv(duration=1800.0, **{"playback-time": 12.0})
        source.broadcast_position_update = Mock()

        await source._on_monitor_tick()

        assert source.broadcast_position_update.call_count == 1

    async def test_a_paused_stream_past_the_start_is_left_paused(self, source):
        """The other half of the wedge guard. mpv pauses itself on a buffer
        underrun mid-episode; force-resuming there fights the underrun instead
        of letting the cache refill. Only the stuck-at-zero case is a wedge —
        which is the whole reason `position == 0.0` is in the condition."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._duration = 1800
        source._mpv = FakeMpv(pause=True, **{"playback-time": 640.0})

        await source._on_monitor_tick()

        assert ("set", "pause", False) not in source._mpv.calls

    async def test_a_stream_wedged_at_zero_and_paused_is_forced_back_open(self, source):
        """Observed mpv state after a slow start: playing, position 0, paused.
        Nothing else clears it and the episode never begins."""
        source._current_episode = dict(EPISODE)
        source._is_playing = True
        source._duration = 1800
        source._mpv = FakeMpv(pause=True, **{"playback-time": 0.0})

        await source._on_monitor_tick()

        assert ("set", "pause", False) in source._mpv.calls
