# backend/tests/test_podcast_playback.py
"""The podcast playback path: starting an episode, resuming it where the owner
left it, the progress file, the handshake playhead, mpv dying, the boot, and
what an episode's end publishes.

`test_podcast_source.py` covers the command surface and how an episode ends;
this file covers the body that starts one and what happens around it. All of
it is driven through the outside world only — MpvSim (tests/mpv_sim.py) for
mpv, a real AudioStateMachine for the wire, an in-memory progress file and a
fake catalogue (the PodcastRig of test_mpv_sessions.py) — and asserted on what
mpv was sent, what reached the wire and what reached the progress file.

Three invariants live in here and nowhere else:

* **the second to start at rides on the load.** One `loadfile` carrying
  `start=`, never a load followed by a seek (E57): a seek sent before mpv has
  opened the file is refused, and the episode plays from 0:00.
* **a stream that will not load leaves nothing loading.** A spinner left
  behind is a card for an episode that is not playing; the episode itself is
  kept to resume, since it was never listened to.
* **the progress file is written from what mpv says the playhead is**, every
  ten seconds of sound and at every pause, seek, switch and stop — never a
  zero, and never while nothing plays.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.core import audio_source
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.podcast.source import PodcastSource
from backend.tests.golden import test_wire_podcast as golden
from backend.tests.golden.harness import AsyncioProxy, VirtualClock, settle
from backend.tests.golden.test_wire_podcast import EPISODE_A, EPISODE_B
from backend.tests.test_mpv_sessions import PodcastRig


@pytest.fixture
def rig(monkeypatch):
    return PodcastRig(monkeypatch)


@pytest.fixture
def slow_watchdog(monkeypatch, rig):
    """A loading watchdog that does not fire during the test, for scenarios
    that hold a session in LOADING on purpose. Takes `rig` so it lands after
    the rig's own (short) watchdog."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)


def count_saves(rig) -> list:
    """Record every progress write, in order, as (uuid, position)."""
    saves = []
    real = rig.data.update_playback_progress

    async def counting(episode_uuid, position, duration, **kw):
        saves.append((episode_uuid, position))
        return await real(episode_uuid, position, duration, **kw)

    rig.data.update_playback_progress = counting
    return saves


class TestStartingAnEpisode:
    async def test_a_playable_episode_reports_success_and_names_it(self, rig):
        """Non-triviality: this path can succeed, so every refusal below is the
        guard and not a broken double. The name is what the store's toast
        reads (podcastStore.playEpisode)."""
        await rig.select()

        result = await rig.play(EPISODE_A)

        assert result["success"] is True
        assert EPISODE_A["name"] in result["message"]

    async def test_the_audio_url_from_the_catalogue_is_what_mpv_loads(self, rig):
        """The only thing that decides which bytes play. The catalogue answer is
        looked up by uuid at play time — the UI carries no URL. If it fails,
        the episode card plays another episode's audio, or nothing."""
        await rig.select()

        await rig.play(EPISODE_B)

        assert [load[1] for load in rig.loads()] == [EPISODE_B["audio_url"]]

    async def test_an_episode_the_catalogue_cannot_serve_is_a_refusal(self, rig):
        """If it fails, POST /api/podcast/play answers success over silence, or
        mpv is asked to load nothing."""
        await rig.select()

        result = await rig.command("play_episode", {"episode_uuid": "gone"})

        assert result["success"] is False
        assert "gone" in result["error"]
        assert rig.loads() == []
        assert not rig.active()

    async def test_an_episode_with_no_audio_url_is_a_refusal_not_a_crash(
        self, rig, monkeypatch
    ):
        """A feed can carry an item whose enclosure is missing; loading `None`
        into mpv is an error with no episode name in it, and a session opened
        on it would sit on a spinner (AudioPlayer.vue) until the watchdog."""
        no_enclosure = {**EPISODE_A, "uuid": "1200361736:none", "audio_url": None}
        monkeypatch.setitem(golden.EPISODES, no_enclosure["uuid"], no_enclosure)
        await rig.select()

        result = await rig.command("play_episode", {"episode_uuid": no_enclosure["uuid"]})

        assert result["success"] is False
        assert rig.loads() == []
        assert not rig.active()

    async def test_the_state_is_published_buffering_before_the_stream_loads(
        self, rig, slow_watchdog
    ):
        """The spinner: the new episode has a loading session from the play,
        and playing only once mpv says sound started. Without the first publish
        the card (AudioPlayer.vue) stays on the previous episode for the whole
        buffering window; without the second it spins over audible sound."""
        rig.mpv.auto_open = False
        await rig.select()

        await rig.play(EPISODE_A)
        assert rig.phase() == "loading"
        assert rig.episode() == EPISODE_A["uuid"]
        assert rig.session()["title"] == EPISODE_A["name"]

        await rig.mpv.opens()
        await settle()
        assert rig.phase() == "playing"

    async def test_the_speed_set_while_idle_is_applied_to_the_next_episode(self, rig):
        """The speed is a preference: set with nothing playing (the speed menu,
        a widget), it has to reach mpv when the next episode loads, or the
        setting silently applies to nothing."""
        await rig.select()
        await rig.command("set_speed", {"speed": 1.5})

        await rig.play(EPISODE_A)

        speed_sets = [c for c in rig.mpv.sent if c[:2] == ("set_property", "speed")]
        assert speed_sets and speed_sets[-1][2] == 1.5
        assert rig.mpv.sent.index(speed_sets[-1]) > rig.mpv.sent.index(rig.loads()[-1])
        assert rig.mpv.speed == 1.5

    async def test_a_paused_mpv_is_unpaused_before_the_next_load(self, rig):
        """mpv's pause is global: an episode paused and then replaced by
        another would load the new one paused, silent, under a card saying it
        plays. The unpause goes out before the load."""
        await rig.select()
        await rig.play(EPISODE_A)
        await rig.command("pause")

        await rig.play(EPISODE_B)

        unpause = ("set_property", "pause", False)
        assert unpause in rig.mpv.sent
        assert rig.mpv.sent.index(unpause) < rig.mpv.sent.index(rig.loads()[-1])
        assert rig.mpv.paused is False
        assert rig.playing()
        assert rig.episode() == EPISODE_B["uuid"]

    async def test_the_progress_is_saved_every_ten_seconds_of_sound(self, rig):
        """Without it nothing persists between the start of an episode and its
        end, so a power cut loses the whole listen and the in-progress queue
        (get_in_progress_episodes) never shows it."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(100)

        await rig.tick(9)
        assert rig.data.progress == {}

        await rig.tick()
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 100

    async def test_a_successful_start_clears_the_error_banner(self, rig):
        """A previous failure leaves the error banner (App.vue) up; the next
        episode that plays takes it down."""
        rig.mpv.broken["daily-0921"] = "loading failed"
        await rig.select()
        await rig.play(EPISODE_A)
        assert rig.errors() == ["stream_load_failed"]

        await rig.play(EPISODE_B)

        assert len(rig.envelopes("source", "error_cleared")) == 1


class TestTheOutgoingEpisode:
    async def test_switching_saves_the_outgoing_position(self, rig):
        """The second the outgoing episode was left at is read from mpv before
        the new load replaces it. Read after, it is the new episode's playhead,
        and the previous episode resumes from the wrong second (or restarts)."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(640)

        await rig.play(EPISODE_B)

        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 640
        assert rig.data.completed == []

    async def test_switching_replaces_the_entry_and_never_stops_mpv(self, rig):
        """The new episode replaces the old one in mpv in one load; a stop in
        between made mpv idle under the outgoing episode, which is what read as
        its end (E48). The replaced entry's own end (reason=stop) must not end
        the new session."""
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.play(EPISODE_B)

        assert ("stop",) not in rig.mpv.sent
        assert [load[2] for load in rig.loads()] == ["replace", "replace"]
        assert rig.playing()
        assert rig.episode() == EPISODE_B["uuid"]


class TestResumingWhereTheOwnerLeftOff:
    async def test_a_stored_position_past_the_threshold_rides_on_the_load(self, rig):
        """The resume second goes out with the load (`start=`), not as a seek
        after it (E57). If it fails, a resumed episode plays from 0:00."""
        rig.data.progress[EPISODE_A["uuid"]] = {"position": 640, "duration": 1800}
        await rig.select()

        await rig.play(EPISODE_A)

        assert rig.loads()[-1][3] == 640
        assert not [c for c in rig.mpv.sent if c[0] == "seek"]

    async def test_a_position_inside_the_first_ten_seconds_is_not_a_resume(self, rig):
        """Resuming from 4 s is worse than starting over: the player jumps, and
        the listener hears the intro twice."""
        rig.data.progress[EPISODE_A["uuid"]] = {"position": 4, "duration": 1800}
        await rig.select()

        await rig.play(EPISODE_A)

        assert rig.loads()[-1][3] is None

    async def test_an_episode_never_started_loads_from_the_top(self, rig):
        """No row in the progress file: no `start=` on the load."""
        await rig.select()

        await rig.play(EPISODE_A)

        assert rig.loads()[-1][3] is None

    async def test_the_resume_position_is_published_before_the_stream_opens(
        self, rig, slow_watchdog
    ):
        """The progress bar (useSourceProgress) has to open at the resume point,
        not at 0:00 — it is published with the buffering state, before mpv has
        opened the file."""
        rig.data.progress[EPISODE_A["uuid"]] = {"position": 640, "duration": 1800}
        rig.mpv.auto_open = False
        await rig.select()

        await rig.play(EPISODE_A)

        assert rig.buffering()
        assert rig.position_ms() == 640_000


class TestAStreamThatWillNotLoad:
    async def test_a_refused_load_is_reported_as_a_failure(self, rig):
        """If it fails, POST /api/podcast/play answers success over an mpv that
        never took the file."""
        await rig.select()
        rig.mpv.accept = False

        result = await rig.play(EPISODE_A)

        assert result["success"] is False

    async def test_a_refused_load_raises_the_error_banner(self, rig):
        """One banner (App.vue), and no "cleared" behind it."""
        await rig.select()
        rig.mpv.accept = False

        await rig.play(EPISODE_A)

        assert rig.errors() == ["stream_load_failed"]
        assert rig.envelopes("source", "error_cleared") == []

    async def test_a_refused_load_leaves_nothing_loading(self, rig):
        """A session left LOADING is a spinner over an episode that is not
        playing. The episode is kept as the one to resume (it was never
        listened to), and the next play works."""
        await rig.select()
        rig.mpv.accept = False

        await rig.play(EPISODE_A)

        state = rig.state()
        assert state["session"] is None
        assert state["resume"]["title"] == EPISODE_A["name"]
        assert rig.episode() == EPISODE_A["uuid"]
        assert rig.data.completed == []

        rig.mpv.accept = True
        assert (await rig.play(EPISODE_A))["success"] is True
        assert rig.playing()

    async def test_a_catalogue_that_raises_is_reported_and_starts_nothing(
        self, rig, monkeypatch
    ):
        """The catalogue reads the publisher's feed over the network; a raise
        there is answered as an error with its reason, raises the banner, and
        leaves no session behind. If it fails, the play route answers 500, or
        the card spins on an episode that was never loaded."""
        await rig.select()

        async def unreachable(episode_uuid, country="us"):
            raise RuntimeError("feed unreachable")

        monkeypatch.setattr(rig.source.podcast_api, "get_episode", unreachable)

        result = await rig.play(EPISODE_A)

        assert result["success"] is False
        assert "feed unreachable" in result["error"]
        assert rig.errors() == ["playback_failed"]
        assert rig.loads() == []
        assert not rig.active()


class TestTheHandshakePlayhead:
    """What a (re)connecting client is told the playhead is.

    GET /api/audio/state and the WebSocket handshake go through
    `state.refresh_active_view()`, which re-reads mpv's playhead. Without the
    re-read the client opens on the last anchor, however far mpv has moved
    from it since.
    """

    async def test_the_live_playhead_replaces_the_cached_one(self, rig):
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(642)

        await rig.machine.refresh_active_view()

        assert rig.position_ms() == 642_000

    async def test_a_pause_mpv_announces_on_its_own_is_published(self, rig):
        """mpv paused from outside Milō's commands: the phase follows mpv's
        pause event, so a connecting client opens on a play button, not a
        pause button over a paused episode."""
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.mpv.set_property("pause", True)
        await settle()

        assert rig.phase() == "paused"

    async def test_a_buffering_stream_keeps_its_play_state(self, rig, slow_watchdog):
        """mpv answers `pause=False` before the stream is ready, so trusting it
        while buffering would flip the card to "playing" on a silent stream."""
        rig.mpv.auto_open = False
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.machine.refresh_active_view()

        assert rig.phase() == "loading"

    async def test_a_property_mpv_will_not_answer_leaves_the_value_alone(self, rig):
        """mpv reads None for the playhead and the length while it cannot say;
        overwriting the position with it would send the client back to 0:00
        and hide the bar."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(642)
        await rig.tick()
        rig.mpv.playhead(None)
        rig.mpv.default_duration = None

        await rig.machine.refresh_active_view()

        assert (rig.position_ms(), rig.session()["duration_ms"]) == (642_000, 1_800_000)

    async def test_nothing_is_refreshed_with_no_episode(self, rig):
        await rig.select()

        assert await rig.source.refresh_when_idle() is False

    async def test_nothing_is_refreshed_once_mpv_is_gone(self, rig):
        """mpv dying ends the session; a handshake after it reads nothing from
        the new mpv, which knows nothing of the episode."""
        await rig.select()
        await rig.play(EPISODE_A)
        await rig.mpv.dies()
        await settle()

        assert await rig.source.refresh_when_idle() is False


class TestMpvGoingAwayUnderPlayback:
    async def test_the_position_is_persisted_and_the_episode_kept(self, rig):
        """mpv dying mid-episode is exactly when the owner most wants to come
        back to where they were: the last second read is written to the
        progress file, the session ends with a banner (App.vue), and the idle
        view keeps the episode at that second."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(640)
        await rig.tick()

        await rig.mpv.dies()
        await settle()

        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 640
        state = rig.state()
        assert state["session"] is None
        assert rig.episode() == EPISODE_A["uuid"]
        assert state["resume"]["position_ms"] == 640_000
        assert rig.errors() == ["stream_disconnected"]
        assert rig.data.completed == []

    async def test_an_episode_never_started_saves_nothing(self, rig, slow_watchdog):
        """Position 0 would overwrite a real stored position with a zero row and
        drop the episode out of the in-progress queue."""
        rig.mpv.auto_open = False
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.mpv.dies()
        await settle()

        assert rig.data.progress == {}


class TestThePeriodicProgressSave:
    async def test_a_failing_save_does_not_stop_the_later_ones(self, rig):
        """The per-tick guard the doctrine requires. Without it one transient
        disk error stops every later save for the rest of the episode — and
        nothing says so — or ends the playback it was only recording."""
        await rig.select()
        await rig.play(EPISODE_A)
        real = rig.data.update_playback_progress
        failures = []

        async def flaky(episode_uuid, position, duration, **kw):
            if not failures:
                failures.append(position)
                raise OSError("disk full")
            return await real(episode_uuid, position, duration, **kw)

        rig.data.update_playback_progress = flaky
        rig.mpv.playhead(100)
        await rig.tick(10)
        assert failures == [100]
        assert rig.playing()

        rig.mpv.playhead(200)
        await rig.tick(10)
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 200

    async def test_a_playhead_still_at_zero_is_never_written(self, rig):
        """A slow-starting stream can sit at 0 for its first ten seconds;
        writing that row overwrites a real stored position with zero, and
        `get_in_progress_episodes` drops the episode out of the queue for a
        listen that is under way."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(0)

        await rig.tick(10)

        assert rig.data.progress == {}

    async def test_a_paused_episode_is_not_re_saved_every_tick(self, rig):
        """A paused episode's row is written once, by the pause; rewriting it
        every ten seconds is a write to `/var/lib/milo` per tick for as long as
        the pause lasts."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(100)
        saves = count_saves(rig)

        await rig.command("pause")
        await rig.tick(30)

        assert saves == [(EPISODE_A["uuid"], 100)]


class TestBootFailureArms:
    """A start that fails settles the service as failed (the selector's error
    card, retried by selecting it again), and leaves no mpv link behind."""

    async def test_a_service_that_will_not_start_stops_the_boot(self, rig):
        """No IPC connect is attempted on a unit that did not start."""
        rig.systemd.start = AsyncMock(return_value=False)

        await rig.select()

        assert rig.state()["service"] == "failed"
        assert rig.mpv.connected_once is False

    async def test_an_mpv_that_will_not_answer_stops_the_boot(self, rig):
        """A source reported started with no IPC link accepts commands that
        cannot reach mpv, and every one of them fails individually."""
        rig.mpv.connect = AsyncMock(return_value=False)

        await rig.select()

        assert rig.state()["service"] == "failed"

    async def test_the_stored_speed_is_restored_at_boot(self, rig):
        """The speed control is a setting, not a per-session choice: the value
        an earlier run stored is what the first episode after a boot plays at.
        Without it every restart silently drops the owner back to 1.0x."""
        rig.data.settings["playback_speed"] = 1.5
        await rig.select()

        await rig.play(EPISODE_A)

        assert rig.mpv.speed == 1.5
        assert rig.details()["speed"] == 1.5

    async def test_a_crash_during_boot_leaves_no_link_behind(self, rig):
        """Half a start is worse than none: an mpv link outliving the failure
        with no owner."""
        async def unreadable(key, default=None):
            raise OSError("podcast_data.json unreadable")

        rig.data.get_setting = unreadable

        await rig.select()

        assert rig.state()["service"] == "failed"
        assert rig.mpv.connected_once is True
        assert rig.mpv.is_connected is False


class TestStopping:
    async def test_a_playing_episode_is_persisted_on_the_way_out(self, rig):
        """Leaving Podcast writes the live second, read from mpv on the way
        out: the next visit's resume (and the in-progress queue) starts there."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(640)

        await rig.leave()

        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 640

    async def test_an_episode_at_the_very_start_is_not_persisted(self, rig, slow_watchdog):
        rig.mpv.auto_open = False
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.leave()

        assert rig.data.progress == {}


class TestAPausedEpisodeReplacedByAnother:
    """Pausing arms the auto-stop timer; starting another episode must disarm
    it (E49).

    What breaks when this fails: a timer that expires while the new episode is
    loading stops mpv under it. The play then reports success over an idle
    mpv, the card shows the new episode playing in silence, and the stop reads
    as the episode's end — marking an episode nobody heard as completed.
    """

    async def test_the_new_stream_is_not_stopped_while_it_loads(self, rig, monkeypatch):
        await rig.select()
        await rig.play(EPISODE_A)
        clock = VirtualClock()
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(clock.sleep))
        await rig.command("pause")                     # arms the 120 s pause timer

        progress_held = asyncio.Event()
        real_progress = rig.data.get_playback_progress

        async def held_progress(episode_uuid):
            await progress_held.wait()
            return await real_progress(episode_uuid)

        rig.data.get_playback_progress = held_progress
        second = asyncio.create_task(rig.source.command(
            "play_episode", {"episode_uuid": EPISODE_B["uuid"]}
        ))
        await settle()                                 # the play waits on the disk
        await clock.advance(121)                       # the pause timer expires meanwhile
        progress_held.set()
        result = await second
        await clock.advance(1)

        assert result["success"] is True
        assert ("stop",) not in rig.mpv.sent
        assert rig.playing()
        assert rig.episode() == EPISODE_B["uuid"]
        assert rig.data.completed == []


class TestBoot:
    async def test_the_data_file_is_read_at_boot_so_a_schema_drift_fails_loud(
        self, tmp_path
    ):
        """The whole point of `initialize` here: a v1 `podcast_data.json` must
        raise the banner at boot, not on the first subscription read hours
        later."""
        source = PodcastSource({"mpv_socket": str(tmp_path / "podcast-ipc.sock")})
        source._podcast_data = AsyncMock()
        source._podcast_data.initialize = AsyncMock(
            side_effect=RuntimeError("missing required keys")
        )

        with pytest.raises(RuntimeError, match="missing required keys"):
            await source.initialize()


class TestTheEndOfAnEpisode:
    async def test_a_persistence_failure_does_not_strand_the_source_as_playing(
        self, rig
    ):
        """If marking completion throws, the episode still ends: its session
        ends for `eof` — what lets the frontend (podcastStore) flip the card to
        "already listened" without a re-fetch — and nothing is left to resume.
        Otherwise the card never comes down."""
        async def disk_full(episode_uuid):
            raise OSError("disk full")

        rig.data.mark_episode_completed = disk_full
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(1790)
        await rig.tick()
        playing = rig.recorder.stable(rig.state())["session"]["id"]

        await rig.mpv.ends("eof")
        await settle()

        state = rig.state()
        assert state["session"] is None
        assert state["resume"] is None
        ended = rig.envelopes("source", "session_ended")
        assert [e["data"]["reason"] for e in ended] == ["eof"]
        assert ended[0]["data"]["session_id"] == playing

    async def test_the_duration_becoming_known_is_broadcast_at_once(self, rig):
        """A feed omits `itunes:duration` often enough (EPISODE_B) that the
        progress bar would otherwise be missing until something else moved:
        the first second mpv knows the length, the state carries it — with the
        playhead read on that same second, in one envelope."""
        await rig.select()
        await rig.play(EPISODE_B)
        assert rig.session()["duration_ms"] is None
        before = len(rig.recorder.envelopes)
        rig.mpv.playhead(12)

        await rig.tick()

        after = rig.recorder.envelopes[before:]
        assert [(e["category"], e["type"]) for e in after] == [("source", "state")]
        session = after[0]["data"]["session"]
        assert (session["position"]["ms"], session["duration_ms"]) == (12_000, 1_800_000)

    async def test_a_playhead_the_anchor_predicts_publishes_nothing(self, rig):
        """The clients extrapolate the playhead from the anchor; a position
        event every second would be a WebSocket message per second per client
        for nothing. A reading within the tolerance (2 s) moves nothing."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(0)
        await rig.tick()
        before = len(rig.recorder.envelopes)

        rig.mpv.playhead(1)
        await rig.tick(29)

        assert rig.recorder.envelopes[before:] == []

    async def test_a_playhead_off_the_anchor_is_one_position_event(self, rig):
        """Past the tolerance the reading is a discontinuity: exactly one
        `source/position`, and no state beside it (nothing else moved).
        Counting, not presence — two events for one jump is what a membership
        assertion cannot see."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(0)
        await rig.tick()
        before = len(rig.recorder.envelopes)

        rig.mpv.playhead(300)
        await rig.tick(3)

        after = rig.recorder.envelopes[before:]
        assert [(e["category"], e["type"]) for e in after] == [("source", "position")]
        assert after[0]["data"]["position"]["ms"] == 300_000
        assert rig.position_ms() == 300_000
