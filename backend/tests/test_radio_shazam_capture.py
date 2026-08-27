# backend/tests/test_radio_shazam_capture.py
"""`ShazamRecognitionService` — the loop's start/stop, its settings gate, and
the ffmpeg capture that feeds it.

Measured 2026-08-25: the module ran at 37,7 % of its lines. `start`, `stop` and
`is_enabled` were never entered at all, and `_try_recognize` (37 lines) and
`_capture_audio` (31) were at 0 %. `test_radio_shazam.py` covers the result
parser and the stale-clear counter by driving `_recognition_loop` with a
substituted `_try_recognize`, so everything below that substitution was dark.

The capture is the piece with a contract nothing else can restate: ffmpeg is
asked for raw PCM, and Python's `wave` module writes the RIFF header around it
from a *separate* set of numbers. If the two ever disagree, the WAV that reaches
Shazam describes audio that is not in the buffer — every recognition fails, in
silence, for ever. No log line says so.

ffmpeg is never actually spawned here: `create_subprocess_exec` is replaced, so
the real binary can neither be executed nor open the stream URL. Consumer:
`RadioSource._start_shazam_fallback` → the player's now-playing line.
"""
import asyncio
import io
import wave
from unittest.mock import AsyncMock, Mock

import pytest

import backend.sources.radio.shazam as shazam_mod
from backend.sources.radio.shazam import ShazamRecognitionService


class _FakeProcess:
    """Stands in for the ffmpeg child: no exec, no stream, no network."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, hangs_for=None):
        self._stdout, self._stderr = stdout, stderr
        self.returncode = returncode
        self._hangs_for = hangs_for
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hangs_for is not None:
            # Bounded just above the reduced fixture timeout: an unbounded sleep
            # turns a mutation that drops `wait_for` into a very long wait
            # instead of a red test.
            await asyncio.sleep(self._hangs_for)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


@pytest.fixture
def spawns(monkeypatch):
    """Record every ffmpeg argv and hand back a stand-in process.

    The list of recorded argv is the return value; `spawns.process` selects what
    the next spawn answers.
    """
    recorded = []

    class Recorder(list):
        process = _FakeProcess(stdout=b"\x00\x01" * 8000)

    recorded = Recorder()

    async def _spawn(*argv, **kwargs):
        recorded.append(list(argv))
        return recorded.process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    return recorded


@pytest.fixture
def service():
    settings = Mock()
    settings.get_setting = AsyncMock(return_value={"shazam_enabled": True})
    return ShazamRecognitionService(settings_service=settings)


class TestCaptureFormatContract:
    """The ffmpeg argv and the WAV header are two statements of one format."""

    @pytest.mark.asyncio
    async def test_the_wav_header_describes_exactly_what_ffmpeg_was_asked_for(
        self, service, spawns
    ):
        """Derived from the argv, not restated: this can only pass when the two
        halves agree. They are written 30 lines apart and nothing else pairs
        them — a `-ar 44100` with the header left at 16000 hands Shazam audio
        playing at a third of its speed, which never matches anything."""
        wav = await service._capture_audio("http://stream/fip")

        argv = spawns[0]
        channels = int(argv[argv.index("-ac") + 1])
        rate = int(argv[argv.index("-ar") + 1])
        assert argv[argv.index("-f") + 1] == "s16le"      # 16-bit little-endian
        assert argv[argv.index("-acodec") + 1] == "pcm_s16le"

        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getnchannels() == channels
            assert wf.getframerate() == rate
            assert wf.getsampwidth() == 2                 # what s16le means

    @pytest.mark.asyncio
    async def test_the_pcm_bytes_survive_the_wrapping_unchanged(
        self, service, spawns
    ):
        """The wrapper exists only to fix the RIFF size ffmpeg writes as
        0xFFFFFFFF over a pipe; it must not resample or truncate."""
        pcm = bytes(range(256)) * 64
        spawns.process = _FakeProcess(stdout=pcm)

        wav = await service._capture_audio("http://stream/fip")

        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.readframes(wf.getnframes()) == pcm

    @pytest.mark.asyncio
    async def test_the_capture_is_bounded_to_one_segment(self, service, spawns):
        await service._capture_audio("http://stream/fip")

        argv = spawns[0]
        assert int(argv[argv.index("-t") + 1]) == shazam_mod.SEGMENT_DURATION_SECONDS


class TestPrerollSkip:
    """Pre-roll ads are injected on each new HTTP connection (Infomaniak)."""

    @pytest.mark.asyncio
    async def test_no_skip_configured_passes_no_seek_at_all(self, service, spawns):
        await service._capture_audio("http://stream/fip")
        assert "-ss" not in spawns[0]

    @pytest.mark.asyncio
    async def test_the_seek_is_placed_after_the_input_so_ffmpeg_decodes_and_discards(
        self, service, spawns
    ):
        """Position is the whole mechanism. Before `-i`, ffmpeg seeks the
        *container* — and a pre-roll injected at connection time is not in the
        container, so the ad is captured and the music is not."""
        service._preroll_skip = 7

        await service._capture_audio("http://stream/fip")

        argv = spawns[0]
        assert argv[argv.index("-ss") + 1] == "7"
        assert argv.index("-ss") > argv.index("-i")

    @pytest.mark.asyncio
    async def test_the_timeout_grows_with_the_skip(self, service, spawns, monkeypatch):
        """The skip is decoded in real time, so a fixed timeout would kill every
        capture on a station with a long pre-roll — the exact stations this
        feature exists for."""
        monkeypatch.setattr(shazam_mod, "FFMPEG_TIMEOUT_SECONDS", 2)
        service._preroll_skip = 4
        # Hangs longer than the base timeout but inside base + skip.
        spawns.process = _FakeProcess(stdout=b"\x00" * 4000, hangs_for=3)

        wav = await asyncio.wait_for(
            service._capture_audio("http://stream/fip"), timeout=10
        )

        assert wav is not None


class TestCaptureFailures:
    """Every one of these must answer None, never a half-formed buffer."""

    @pytest.mark.asyncio
    async def test_a_non_zero_exit_is_refused_even_when_ffmpeg_printed_bytes(
        self, service, spawns
    ):
        """The exit status is the authority. ffmpeg writes what it decoded
        before it died, so a truncated capture reaches the parser looking
        exactly like a good one unless the status is read."""
        spawns.process = _FakeProcess(stdout=b"\x00\x01" * 8000, returncode=1)

        assert await service._capture_audio("http://stream/fip") is None

    @pytest.mark.asyncio
    async def test_a_clean_exit_with_no_audio_is_refused(self, service, spawns):
        spawns.process = _FakeProcess(stdout=b"", returncode=0)

        assert await service._capture_audio("http://stream/fip") is None

    @pytest.mark.asyncio
    async def test_a_stalled_ffmpeg_is_killed_and_reaped(
        self, service, spawns, monkeypatch
    ):
        """A stream that accepts the connection and then sends nothing leaves
        ffmpeg alive for ever. Without the kill the unit accumulates one stuck
        child per recognition round, every 20 s."""
        monkeypatch.setattr(shazam_mod, "FFMPEG_TIMEOUT_SECONDS", 1)
        stalled = _FakeProcess(hangs_for=30)
        spawns.process = stalled

        result = await asyncio.wait_for(
            service._capture_audio("http://stream/fip"), timeout=10
        )

        assert result is None
        assert stalled.killed is True
        assert stalled.waited is True   # reaped, not left a zombie

    @pytest.mark.asyncio
    async def test_a_spawn_that_cannot_start_is_reported_as_no_audio(
        self, service, monkeypatch
    ):
        """ffmpeg missing from PATH must degrade to "no track", not crash the
        recognition loop."""
        async def _boom(*a, **k):
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

        assert await service._capture_audio("http://stream/fip") is None


class TestSettingsGate:
    """`is_enabled` fails open — so the key it reads is a contract."""

    @pytest.mark.asyncio
    async def test_the_stored_toggle_is_honoured(self, service):
        service._settings_service.get_setting = AsyncMock(
            return_value={"shazam_enabled": False}
        )
        assert await service.is_enabled() is False

        service._settings_service.get_setting = AsyncMock(
            return_value={"shazam_enabled": True}
        )
        assert await service.is_enabled() is True

    @pytest.mark.asyncio
    async def test_it_reads_the_radio_section(self, service):
        await service.is_enabled()
        service._settings_service.get_setting.assert_awaited_once_with("radio")

    @pytest.mark.asyncio
    async def test_a_missing_key_falls_open_to_enabled(self, service):
        """Deliberate, and the reason the name matters: renaming
        `radio.shazam_enabled` on one side leaves the Réglages switch drawing
        `off` while recognition runs for everyone, with nothing in the log.
        `SettingsService.defaults` is where the name is declared."""
        service._settings_service.get_setting = AsyncMock(return_value={})

        assert await service.is_enabled() is True

    @pytest.mark.asyncio
    async def test_an_unreachable_settings_service_falls_open_to_enabled(self, service):
        service._settings_service.get_setting = AsyncMock(side_effect=OSError("gone"))

        assert await service.is_enabled() is True


class TestStartAndStop:
    """`start`/`stop` own the loop task; `RadioSource` calls them on every switch."""

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch):
        monkeypatch.setattr(shazam_mod, "INITIAL_DELAY_SECONDS", 30)

    @pytest.mark.asyncio
    async def test_start_arms_a_loop_for_the_url(self, service):
        await service.start("http://stream/fip")
        try:
            assert service.is_running is True
            assert service._stream_url == "http://stream/fip"
            assert service._loop_task is not None
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_starting_again_on_the_same_url_keeps_the_same_loop(self, service):
        """The monitor can re-arm on a station already running; restarting would
        reset the recognition cadence on every attempt and never converge."""
        await service.start("http://stream/fip")
        first = service._loop_task
        try:
            await service.start("http://stream/fip")
            assert service._loop_task is first
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_a_new_url_replaces_the_loop_rather_than_adding_one(self, service):
        """Two live loops means two captures per interval against two stations,
        both writing the same `_current_track`."""
        await service.start("http://stream/one")
        first = service._loop_task
        try:
            await service.start("http://stream/two")
            assert first.cancelled() or first.done()
            assert service._loop_task is not first
            assert service._stream_url == "http://stream/two"
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_a_new_url_drops_the_previous_stations_track(self, service):
        await service.start("http://stream/one")
        service._current_track = {"title": "Old", "artist": "A", "artwork": None}
        try:
            await service.start("http://stream/two")
            assert service._current_track is None
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_the_task_and_clears_the_url(self, service):
        await service.start("http://stream/fip")
        task = service._loop_task

        await service.stop()

        assert service.is_running is False
        assert service._loop_task is None
        assert service._stream_url is None
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_stop_tells_the_source_the_title_is_gone(self, service):
        """The player keeps whatever it was last given. Without this the stopped
        station's track stays under the *next* station's name."""
        callback = AsyncMock()
        service._on_track_changed = callback
        await service.start("http://stream/fip")
        service._current_track = {"title": "So What", "artist": "Miles", "artwork": None}

        await service.stop()

        callback.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_stop_with_no_title_pinned_says_nothing(self, service):
        """Radio stops far more often than it recognises; a clear event per stop
        is wire noise that also clears an in-band title the player is showing."""
        callback = AsyncMock()
        service._on_track_changed = callback
        await service.start("http://stream/fip")

        await service.stop()

        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_callback_that_raises_still_leaves_the_service_stopped(
        self, service
    ):
        """`stop` is awaited on the play path. An exception here aborts the
        switch and leaves the old loop's state behind."""
        service._on_track_changed = AsyncMock(side_effect=RuntimeError("ws gone"))
        await service.start("http://stream/fip")
        service._current_track = {"title": "T", "artist": "A", "artwork": None}

        await service.stop()

        assert service.is_running is False
        assert service._current_track is None

    @pytest.mark.asyncio
    async def test_stop_on_a_service_that_never_started_is_harmless(self, service):
        await service.stop()
        assert service.is_running is False


class TestTryRecognize:
    """One recognition round: settings re-check, capture, match, publish."""

    @pytest.fixture
    def armed(self, service, spawns):
        service._stream_url = "http://stream/fip"
        service._shazam = Mock()
        service._shazam.recognize = AsyncMock(return_value={
            "track": {"title": "So What", "subtitle": "Miles Davis"},
            "matches": [{"id": "1"}],
        })
        service._on_track_changed = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_a_match_is_cached_and_published(self, armed):
        assert await armed._try_recognize() is True

        assert armed.current_track == {
            "title": "So What", "artist": "Miles Davis", "artwork": None
        }
        armed._on_track_changed.assert_awaited_once_with(armed.current_track)

    @pytest.mark.asyncio
    async def test_the_same_track_twice_is_published_once(self, armed):
        """Recognition runs every 20 s on a track that lasts minutes. Publishing
        each round is a full state broadcast per 20 s per unit."""
        await armed._try_recognize()
        await armed._try_recognize()

        assert armed._on_track_changed.await_count == 1

    @pytest.mark.asyncio
    async def test_a_no_match_verdict_clears_the_title_on_the_spot(self, armed):
        """"No match" is a positive answer — an ad, talk, or a track Shazam does
        not hold. Holding the previous title through it is the phantom the
        service exists to avoid, so this does NOT go through
        `STALE_CLEAR_ROUNDS`."""
        await armed._try_recognize()
        armed._on_track_changed.reset_mock()
        armed._shazam.recognize = AsyncMock(return_value={"matches": []})

        assert await armed._try_recognize() is False
        assert armed.current_track is None
        armed._on_track_changed.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_a_failed_capture_leaves_the_pinned_track_alone(self, armed, spawns):
        """The other half of the same decision, and the one `STALE_CLEAR_ROUNDS`
        actually guards: nothing was learned about what is playing, so dropping
        the title would blank the player on a transient network blip. Only the
        loop's counter may clear it, two rounds later.

        Both halves are asserted here because the two look identical from
        `_try_recognize`'s return value alone — it answers False either way."""
        await armed._try_recognize()
        pinned = armed.current_track
        armed._on_track_changed.reset_mock()
        spawns.process = _FakeProcess(stdout=b"", returncode=1)

        assert await armed._try_recognize() is False
        assert armed.current_track == pinned
        armed._on_track_changed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_toggle_turned_off_mid_stream_clears_the_title(self, armed):
        """The loop keeps running until the next station change, so this is the
        only thing that takes a title off the screen when the user switches
        recognition off while a station is playing."""
        await armed._try_recognize()
        armed._on_track_changed.reset_mock()
        armed._settings_service.get_setting = AsyncMock(
            return_value={"shazam_enabled": False}
        )

        assert await armed._try_recognize() is False
        assert armed.current_track is None
        armed._on_track_changed.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_the_toggle_turned_off_captures_nothing(self, armed, spawns):
        armed._settings_service.get_setting = AsyncMock(
            return_value={"shazam_enabled": False}
        )

        await armed._try_recognize()

        assert spawns == []
        armed._shazam.recognize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_capture_that_returns_nothing_never_reaches_shazam(
        self, armed, spawns
    ):
        """Posting an empty buffer burns an API round-trip per round on a
        station whose stream is down."""
        spawns.process = _FakeProcess(stdout=b"", returncode=1)

        assert await armed._try_recognize() is False
        armed._shazam.recognize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_shazam_timeout_is_a_miss_not_a_crash(self, armed, monkeypatch):
        monkeypatch.setattr(shazam_mod, "RECOGNITION_TIMEOUT_SECONDS", 0.05)

        # Two traps, both measured. The side_effect must be a coroutine
        # *function*: a lambda that merely returns a coroutine is handed back
        # un-awaited, `wait_for` returns at once and the timeout never fires.
        # And the slow answer must be a HIT — with a miss, `False` comes out of
        # the no-track arm just as happily, so deleting `wait_for` altogether
        # left the test green.
        async def _answers_too_late(_audio):
            await asyncio.sleep(5)
            return {"matches": [{}], "track": {"title": "Ne me quitte pas",
                                               "subtitle": "Jacques Brel"}}

        armed._shazam.recognize = AsyncMock(side_effect=_answers_too_late)

        assert await armed._try_recognize() is False

    @pytest.mark.asyncio
    async def test_a_transport_error_is_a_miss_not_a_crash(self, armed):
        armed._shazam.recognize = AsyncMock(side_effect=ValueError("bad json"))

        assert await armed._try_recognize() is False

    @pytest.mark.asyncio
    async def test_cancellation_is_re_raised_so_stop_can_land(self, armed):
        """`stop()` cancels the loop task while a round may be in flight.
        Swallowing CancelledError here leaves the loop running after stop
        returned, on the previous station's URL.

        Measured: deleting the explicit `except asyncio.CancelledError: raise`
        alone changes nothing — CancelledError is a BaseException, so the two
        catch-alls below cannot reach it either way. What this catches is the
        combination, i.e. a catch-all widened to BaseException, which is the
        regression that would actually happen."""
        armed._shazam.recognize = AsyncMock(side_effect=asyncio.CancelledError)

        with pytest.raises(asyncio.CancelledError):
            await armed._try_recognize()

    @pytest.mark.asyncio
    async def test_a_round_with_no_url_captures_nothing(self, armed, spawns):
        armed._stream_url = None

        assert await armed._try_recognize() is False
        assert spawns == []
