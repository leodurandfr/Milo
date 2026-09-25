# backend/tests/test_podcast_source.py
"""
Unit tests for PodcastSource (sources/podcast/source.py).

The command surface, the published episode record and how an episode ends,
driven through the outside world: MpvSim for mpv (tests/mpv_sim.py), a real
AudioStateMachine for the wire and an in-memory progress file, through the
PodcastRig of test_mpv_sessions.py. What mpv announces is the truth; nothing
here reads or writes the source's private fields. The data service and a few
construction checks keep their direct tests.
"""
import json

import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.data import PodcastDataService
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.shared.persistence import SchemaVersionMismatch
from backend.tests.golden.harness import settle
from backend.tests.golden.test_wire_podcast import EPISODE_A, SHOW
from backend.tests.test_mpv_sessions import PodcastRig


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


@pytest.fixture
def rig(monkeypatch):
    """PodcastSource on a real state machine, mpv simulated, time in steps."""
    return PodcastRig(monkeypatch)


@pytest.fixture
def auto_stop_rig(monkeypatch):
    """The same, with a 1 s auto-stop: a pause ends the session inside settle()."""
    return PodcastRig(monkeypatch, settings={"audio.auto_stop_delay": 1})


@pytest.fixture
def slow_watchdog(monkeypatch, rig):
    """A loading watchdog that does not fire during the test, for scenarios
    that hold a session in LOADING on purpose. Takes `rig` so it lands after
    the rig's own (short) watchdog."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)


def published(rig) -> int:
    return len(rig.recorder.envelopes)


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

    async def test_start_success(self, rig):
        """Selecting Podcast starts its unit and settles with no session and
        nothing loaded. If it fails, the podcast screen (frontend PodcastSource.vue)
        opens on an error card instead of the browser."""
        await rig.select()

        state = rig.state()
        assert (state["source"], state["service"]) == ("podcast", "running")
        assert (state["session"], state["resume"], state["details"]) == (None, None, None)
        rig.systemd.start.assert_awaited_with("milo-podcast.service")
        assert rig.mpv.is_connected

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
        podcast_source._mpv = Mock()
        podcast_source._mpv.disconnect = AsyncMock()
        podcast_source._podcast_api = Mock()
        podcast_source._podcast_api.close = AsyncMock()
        podcast_source._podcast_data = Mock()

        with patch.object(podcast_source, '_stop_service', return_value=True):
            result = await podcast_source.stop()

        assert result is True


class TestPodcastSourceCommands:
    """The five commands, as POST /api/audio/control/podcast and
    POST /api/podcast/play deliver them (frontend podcastStore.js)."""

    async def test_play_episode_command(self, rig):
        """play_episode loads the catalogue's audio URL and the episode plays
        once mpv says sound started. If it fails, a tap on an episode card does
        nothing audible."""
        await rig.select()

        result = await rig.play(EPISODE_A)

        assert result["success"] is True
        assert [load[1] for load in rig.loads()] == [EPISODE_A["audio_url"]]
        assert rig.playing()
        assert rig.episode() == EPISODE_A["uuid"]

    async def test_pause_command(self, rig):
        """A pause stops mpv, publishes the paused state and writes the second
        the owner stopped at to the progress file. If it fails, the player keeps
        a pause button over silence, or the in-progress queue (podcastStore's
        resume row) reopens the episode at an older second."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(300)

        result = await rig.command("pause")

        assert result["success"] is True
        assert rig.mpv.paused is True
        assert rig.phase() == "paused"
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 300

    async def test_resume_command(self, rig):
        """resume unpauses the live episode and the player shows it playing
        again. If it fails, the play button of the player (and the rotary's
        play/pause) leaves a paused episode silent."""
        await rig.select()
        await rig.play(EPISODE_A)
        await rig.command("pause")

        result = await rig.command("resume")

        assert result["success"] is True
        assert rig.mpv.paused is False
        assert rig.playing()

    async def test_seek_command(self, rig):
        """A seek moves mpv, publishes the new position and saves it. If it
        fails, the progress bar (useSourceProgress.seekTo) snaps back, or the
        next resume starts where the owner was before the seek."""
        await rig.select()
        await rig.play(EPISODE_A)

        result = await rig.command("seek", {"position": 300})

        assert result["success"] is True
        assert ("seek", 300) in rig.mpv.sent
        assert rig.position_ms() == 300_000
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 300

    async def test_auto_stop_clears_playback(self, auto_stop_rig):
        """The pause timeout ends the session: mpv is stopped, the second is
        saved, and the episode is not marked listened. If it fails, a paused
        episode holds mpv for good, or a long pause drops the episode out of the
        in-progress queue (get_in_progress_episodes) as if finished."""
        rig = auto_stop_rig
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(300)
        await rig.tick()
        rig.mpv.playhead(305)

        await rig.command("pause")               # the 1 s idle timeout fires
        await settle()

        assert not rig.active()
        assert ("stop",) in rig.mpv.sent
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 305
        assert rig.data.completed == []

    @pytest.mark.asyncio
    async def test_set_speed_command(self, podcast_source):
        """Test set_speed command."""
        podcast_source._mpv = Mock()
        podcast_source._mpv.set_property = AsyncMock()
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
        podcast_source._podcast_data = Mock()
        podcast_source._podcast_data.set_setting = AsyncMock(return_value=True)

        result = await podcast_source.command("set_speed", {"speed": 1.3})

        assert result["success"] is True
        assert podcast_source._playback_speed == 1.25  # Nearest valid

    async def test_set_speed_during_playback_reaches_mpv(self, rig):
        """A speed set while an episode plays changes mpv's speed now, is
        stored, and is published — with the playhead moving at the new rate
        from here on. If it fails, the speed menu (PodcastPlayer) shows 1.5x
        over an episode still playing at 1x, or the bar runs at the old
        speed."""
        await rig.select()
        await rig.play(EPISODE_A)

        result = await rig.command("set_speed", {"speed": 1.5})

        assert result["success"] is True
        assert rig.mpv.speed == 1.5
        assert rig.data.settings["playback_speed"] == 1.5
        assert rig.details()["speed"] == 1.5
        assert rig.session()["position"]["rate"] == 1.5

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


class TestPlaybackDetails:
    """What a playing episode publishes in `details` and its session."""

    async def test_a_playing_episode_publishes_its_record(self, rig):
        """The episode as the catalog routes return it, the show, the
        playhead and length in milliseconds (the shared wire convention), the
        phase and the speed. If it fails, the podcast player
        (podcastStore / AudioPlayer.vue) draws the wrong episode, a bar off by a
        factor of 1000, or the wrong speed."""
        await rig.select()
        await rig.command("set_speed", {"speed": 1.5})
        await rig.play(EPISODE_A)
        rig.mpv.playhead(120)
        await rig.machine.refresh_active_view()

        details, session = rig.details(), rig.session()
        assert details["episode"]["uuid"] == EPISODE_A["uuid"]
        assert details["episode"]["name"] == EPISODE_A["name"]
        assert details["episode"]["podcast"] == SHOW
        assert details["speed"] == 1.5
        assert rig.position_ms() == 120_000
        assert session["duration_ms"] == 1_800_000
        assert session["phase"] == "playing"
        assert session["position"]["rate"] == 1.5


class TestTheCommonFloor:
    """Podcast fills the session's title/artist/album/artwork like every other source.

    It filled none of them, and unlike radio it was in no consumer's fallback
    either: `core/push/payloads.py` and Milo-iOS both read
    `title | track_title | station_name`, and podcast publishes `episode_name`.
    A playing episode therefore reached the lock screen as a session whose
    every field was null — a card with no track at all. Two copies of one
    display rule, and both of them missed a source; that is what moving the
    rule into the producer is for.
    """

    async def test_a_playing_episode_fills_the_floor(self, rig):
        """If it fails, the lock screen and the Milo-iOS widget show a session
        with no title while an episode plays."""
        await rig.select()
        await rig.play(EPISODE_A)

        session = rig.session()
        assert session["title"] == EPISODE_A["name"]
        assert session["artist"] == SHOW["name"]
        assert session["album"] == SHOW["name"]
        assert session["artwork"] == EPISODE_A["image_url"]

    async def test_a_stopped_episode_publishes_where_it_would_resume(self, auto_stop_rig):
        """An auto-stop leaves an episode and a second to come back to, and
        both belong in the state — the resume point is what a play press uses,
        so publishing 0:00 for a source that resumes at 12:34 would be the
        state disagreeing with the next press. The length is mpv's (2100 s),
        not the feed's (1800 s). If it fails, the idle podcast view
        (podcastStore's resume card) offers the wrong episode or second."""
        rig = auto_stop_rig
        rig.mpv.durations["daily-0921"] = 2100
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(754)
        await rig.tick()

        await rig.command("pause")               # the 1 s idle timeout fires
        await settle()

        state = rig.state()
        assert state["session"] is None
        assert rig.episode() == EPISODE_A["uuid"]
        resume = state["resume"]
        assert resume["title"] == EPISODE_A["name"]
        assert resume["position_ms"] == 754_000
        assert resume["duration_ms"] == 2_100_000
        assert "resume" in state["controls"]

    async def test_an_episode_that_ended_leaves_nothing_to_resume(self, rig):
        """The distinction the payload has to carry: a stop is a pause that
        gave up, an ending is an ending. The frontend flips the finished card
        to "already listened" off the session's `eof` end, and must not also be
        offered it as the thing a play press resumes. If it fails, the rotary's
        play press restarts an episode the owner just finished."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(1790)
        await rig.tick()

        await rig.mpv.ends("eof")
        await settle()

        state = rig.state()
        assert rig.session_ends() == ["eof"]
        assert (state["session"], state["resume"], state["details"]) == (None, None, None)
        assert "resume" not in state["controls"]
        result = await rig.command("resume")
        assert result["success"] is False
        assert len(rig.loads()) == 1


class TestEpisodeEndDetection:
    """An episode ends when mpv says its own entry ended, and why.

    mpv's `end-file` carries the reason and the entry id; only `eof` on the
    session's own entry, after sound was heard, is an episode listened to the
    end. A position-vs-duration heuristic breaks whenever the reported
    duration overshoots the real end of audio (VBR podcast MP3s,
    early-terminated HTTP streams), and a transient stall is not an end.
    """

    async def test_episode_ends_on_eof_even_if_position_short_of_duration(self, rig):
        """EOF ends the session and persists completion even when the last
        observed position is far short of the reported duration — the original
        'stuck at the end' bug. A final progress row is written (so a short clip
        has one), then the explicit completion mark. If it fails, the episode
        stays in the in-progress queue (get_in_progress_episodes) and its card
        never reads "already listened"."""
        rig.mpv.durations["daily-0921"] = 3600   # over-reported
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(3000)                   # real end of audio
        await rig.tick()

        await rig.mpv.ends("eof")
        await settle()

        assert not rig.active()
        assert rig.session_ends() == ["eof"]
        assert rig.data.completed == [EPISODE_A["uuid"]]
        assert rig.data.progress[EPISODE_A["uuid"]]["position"] == 3000

    async def test_a_stall_mid_episode_does_not_end_it(self, rig, slow_watchdog):
        """mpv's cache running dry (paused-for-cache) mid-stream shows the
        spinner and ends nothing; when the stream comes back it plays on, with
        no command sent to mpv. If it fails, a network hiccup marks the episode
        listened or stops it, and the player (AudioPlayer.vue) loses it."""
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(1800)
        await rig.tick()
        sent_before = len(rig.mpv.sent)

        await rig.mpv.stalls()
        await settle()
        assert rig.phase() == "loading"

        await rig.mpv.recovers()
        await settle()
        assert rig.playing()
        assert rig.session_ends() == []
        assert rig.data.completed == []
        assert len(rig.mpv.sent) == sent_before

    async def test_an_eof_before_any_sound_is_a_failed_load(self, rig, slow_watchdog):
        """mpv can end an entry with `eof` before it ever played (a server
        answering an empty body or an HTML page): nothing was heard, so it is a
        failed load, not a finished episode. If it fails, an episode that never
        played is marked "already listened" and leaves the queue, with no
        banner (App.vue's source error)."""
        rig.mpv.auto_open = False
        await rig.select()
        await rig.play(EPISODE_A)

        await rig.mpv.ends("eof")
        await settle()

        assert rig.data.completed == []
        assert rig.errors() == ["stream_load_failed"]
        assert not rig.active()
        assert rig.episode() == EPISODE_A["uuid"]


class TestProperties:
    """Test public properties."""

    async def test_is_playing_property(self, rig, slow_watchdog):
        """`is_playing` is what the rotary and the IR remote's play/pause press
        reads (hardware/playback_dispatch.py): an episode still loading counts
        as playing (the press means "stop that", E42), a paused one does not.
        If it fails, a press on a buffering episode resumes instead of pausing,
        or a press on a paused one pauses it again."""
        rig.mpv.auto_open = False
        await rig.select()
        assert rig.source.is_playing is False

        await rig.play(EPISODE_A)
        assert rig.source.is_playing is True     # loading

        await rig.mpv.opens()
        await settle()
        assert rig.source.is_playing is True

        await rig.command("pause")
        assert rig.source.is_playing is False


class TestMpvRefusesTheTransportCommand:
    """mpv answers False whenever its IPC socket is down, and says so only at
    debug level.

    If these fail, a pause/resume/seek/speed the daemon never took is answered
    with `success`, the source publishes a state mpv does not have: the UI
    draws a play button over an episode that is still playing, and the progress
    saved on that pause is written for a stream that kept advancing.
    """

    async def _playing(self, rig):
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(300)
        await rig.tick()

    async def test_pause_refused_keeps_the_episode_playing(self, rig):
        await self._playing(rig)
        rig.mpv.accept = False
        before = published(rig)

        result = await rig.command("pause")

        assert result["success"] is False
        assert rig.playing()
        assert rig.data.progress == {}
        assert published(rig) == before

    async def test_resume_refused_keeps_the_episode_paused(self, rig):
        await self._playing(rig)
        await rig.command("pause")
        rig.mpv.accept = False
        before = published(rig)

        result = await rig.command("resume")

        assert result["success"] is False
        assert rig.phase() == "paused"
        assert published(rig) == before

    async def test_seek_refused_keeps_the_position(self, rig):
        await self._playing(rig)
        rig.mpv.accept = False
        before = published(rig)

        result = await rig.command("seek", {"position": 42})

        assert result["success"] is False
        assert rig.data.progress == {}
        assert published(rig) == before

    async def test_set_speed_refused_keeps_the_speed_unpersisted(self, rig):
        await self._playing(rig)
        rig.mpv.accept = False
        before = published(rig)

        result = await rig.command("set_speed", {"speed": 1.5})

        assert result["success"] is False
        assert rig.data.settings["playback_speed"] == 1.0
        assert rig.details()["speed"] == 1.0
        assert published(rig) == before


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
        result = await podcast_source.command("resume", {})

        assert result["success"] is False
        assert "resume" in result["error"].lower()

    async def test_resume_after_an_auto_stop_reopens_the_episode(self, auto_stop_rig):
        """A play press with no session is the case the rotary and the IR remote
        send, and the only name they know is `resume` — playback_dispatch maps
        every non-Spotify transport onto it. Refusing here answered "No episode
        to resume" on a source that was publishing the episode it would resume,
        for every idle timeout. It goes through the play path, so the second
        comes from the progress file and rides on the load."""
        rig = auto_stop_rig
        await rig.select()
        await rig.play(EPISODE_A)
        rig.mpv.playhead(305)
        await rig.command("pause")               # the 1 s idle timeout fires
        await settle()
        assert not rig.active()

        result = await rig.command("resume")

        assert result["success"] is True
        url, _mode, start_s = rig.loads()[-1][1:4]
        assert (url, start_s) == (EPISODE_A["audio_url"], 305)
        assert rig.playing()

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
