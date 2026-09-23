# backend/tests/test_radio_monitor.py
"""How what mpv announces becomes what the radio card shows.

The radio used to poll mpv once a second and infer loading, playing and a dead
stream from a tick count. It now follows mpv's events (MpvAudioSource
`_listen_to_mpv`): the sound starting, the cache running dry, a pause, the
entry ending. Each scenario drives mpv (tests/mpv_sim.py, mpv 0.40 as
measured) and the source's timers on a clock the test advances, and reads the
result where the Pinia store reads it — the state machine's
`source_state`/`metadata` and its `source/error` banners.

Stalls ended by the watchdog, a URL that fails to open, the knob during
buffering and Shazam dying with the session are in tests/test_mpv_sessions.py.
"""
import pytest

from backend.tests.golden.test_old_wire_radio import FIP, NOVA
from backend.tests.radio_world import STALL_TIMEOUT_S, RadioWorld


@pytest.fixture
def radio(monkeypatch):
    return RadioWorld(monkeypatch, settings={"audio.auto_stop_delay": 45}, clock=True)


class TestStreamThatNeverLoads:
    """A stream that opens nothing and sends nothing: only the loading
    watchdog ends it. Losing it is a spinner that never resolves on a station
    that will never play (the radio card, the lock screen)."""

    async def test_a_slow_stream_gets_the_whole_watchdog_and_then_is_reported(self, radio):
        """Both sides of the edge: a banner before the watchdog turns every slow
        station into a failure; none after it leaves the spinner up forever."""
        await radio.select()
        await radio.tune(FIP)

        await radio.advance(STALL_TIMEOUT_S - 1)
        assert radio.errors() == []
        assert radio.state()["source_state"] == "active"
        assert radio.meta()["is_buffering"] is True

        await radio.advance(1)
        assert radio.errors() == ["stream_load_failed"]
        assert radio.state()["source_state"] == "ready"

    async def test_the_station_it_failed_on_stays_on_screen_to_retry(self, radio):
        """READY carries the station the stream failed on, because that is
        what a press on Retry re-tunes — the banner and the card name the same
        station. Playback is off and the track it was showing is gone: the
        recognised track belongs to a stream that runs."""
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()
        await radio.tick(4)
        assert radio.meta()["track_title"] == "Snibor"

        await radio.stalls()
        await radio.advance(STALL_TIMEOUT_S)
        assert radio.errors() == ["stream_disconnected"]

        meta = radio.meta()
        assert meta["station_id"] == "fip"
        assert meta["title"] == meta["station_name"]
        assert meta["is_playing"] is False
        assert meta["is_buffering"] is False
        assert "track_title" not in meta

        await radio.command("resume_playback")
        assert radio.loads()[-1][1] == FIP["url"]

    async def test_a_station_that_opens_in_time_is_never_reported(self, radio):
        """The sound starting disarms the watchdog: a station that took most
        of it to open must not be declared dead afterwards."""
        await radio.select()
        await radio.tune(FIP)
        await radio.advance(STALL_TIMEOUT_S - 1)

        await radio.opens()
        await radio.advance(STALL_TIMEOUT_S * 3)

        assert radio.errors() == []
        assert radio.state()["source_state"] == "active"
        assert radio.meta()["is_playing"] is True


class TestPlaybackEdges:
    """The phase changes mpv announces, each published once."""

    async def test_the_sound_starting_clears_the_spinner(self, radio):
        """The spinner is up from the tap (a station can take seconds to open)
        and goes the moment mpv says sound started — not on a later poll."""
        await radio.select()
        await radio.tune(FIP)
        assert radio.state()["source_state"] == "active"
        assert (radio.meta()["is_playing"], radio.meta()["is_buffering"]) == (False, True)

        await radio.opens()

        assert (radio.meta()["is_playing"], radio.meta()["is_buffering"]) == (True, False)

    async def test_a_stream_that_runs_dry_shows_the_spinner_until_it_recovers(self, radio):
        """mpv's cache running dry is the only sign a stream stalled; the card
        must show it rather than a play state over silence, and a stream that
        comes back (measured: within 12 s) plays on with no banner."""
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()

        await radio.stalls()
        assert radio.state()["source_state"] == "active"
        assert (radio.meta()["is_playing"], radio.meta()["is_buffering"]) == (False, True)

        await radio.recovers()
        await radio.advance(STALL_TIMEOUT_S * 2)
        assert (radio.meta()["is_playing"], radio.meta()["is_buffering"]) == (True, False)
        assert radio.errors() == []

    async def test_a_stream_that_errors_out_after_its_sound_is_a_lost_stream(self, radio):
        """mpv losing the stream is not a command, so nothing else announces
        it: the card drops to READY with the disconnected banner and keeps the
        station for a re-tune."""
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()

        await radio.fails("network error")

        assert radio.state()["source_state"] == "ready"
        assert radio.errors() == ["stream_disconnected"]
        assert radio.meta()["station_id"] == "fip"

    async def test_a_steady_stream_publishes_nothing(self, radio):
        """No change, no event: the source hears from mpv every second while a
        station plays, and a publish per second is a broadcast storm on every
        client of every unit."""
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()
        await radio.tick(4)                    # the title is read and shown
        assert radio.meta()["track_title"] == "Snibor"
        before = len(radio.recorder.envelopes)

        await radio.tick(12)

        assert radio.recorder.envelopes[before:] == []


class TestPauseEdge:
    """Radio has no pause command, but mpv can be paused (another IPC client,
    a load that lands paused). mpv's `pause` event is what arms the idle
    timeout, as for the other mpv sources."""

    async def test_a_pause_is_shown_and_ends_the_session_after_the_idle_delay(self, radio):
        """The configured delay, not another: a stream paused for good must
        release the audio path, and one paused briefly must not lose it."""
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()

        await radio.paused(True)
        await radio.advance(44)
        assert radio.state()["source_state"] == "active"
        assert radio.meta()["is_playing"] is False

        await radio.advance(1)
        assert radio.state()["source_state"] == "ready"
        assert radio.meta()["station_id"] == "fip"
        assert radio.errors() == []

    async def test_an_unpause_disarms_the_idle_timeout(self, radio):
        """A timer left armed across the unpause cuts off a station that is
        playing again."""
        await radio.select()
        await radio.tune(FIP)
        await radio.opens()

        await radio.paused(True)
        await radio.advance(30)
        await radio.paused(False)
        await radio.advance(120)

        assert radio.state()["source_state"] == "active"
        assert radio.meta()["is_playing"] is True


class TestInbandReadingGate:
    """The in-band title is read only while sound plays."""

    async def test_a_loading_station_is_not_read_until_its_sound_starts(self, radio):
        """Reading a title off a stream that has not opened pins the previous
        station's title onto the one now loading (the card, the lock screen)."""
        await radio.select()
        await radio.tune(NOVA)
        await radio.opens()
        radio.stream_title("Gil Evans - Snibor")   # still the old stream's tag
        await radio.tune(FIP)                       # loading

        await radio.tick(8)
        assert "track_title" not in radio.meta()

        radio.stream_title("Miles Davis - So What")
        await radio.opens()
        await radio.tick(4)
        assert radio.meta()["track_title"] == "So What"
