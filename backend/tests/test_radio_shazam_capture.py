# backend/tests/test_radio_shazam_capture.py
"""`ShazamRecognitionService` — the loop's start/stop, its settings gate, and
the ffmpeg capture that feeds it.

Measured 2026-08-25: the module ran at 37,7 % of its lines. `start`, `stop` and
`is_enabled` were never entered at all, and `_try_recognize` (37 lines) and
`_capture_audio` (31) were at 0 %. `test_radio_shazam.py` covers what an answer
means and when the next try comes, through `tests/shazam_world.py`; this file
covers the loop's lifecycle, the transport's reading of an HTTP answer, and the
capture.

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
from backend.sources.radio.shazam import (
    RecognitionQuota, ShazamRecognitionService, ShazamRefused,
)
from backend.tests.shazam_world import Reply, ShazamWorld, match


class _FakeProcess:
    """Stands in for the ffmpeg child: no exec, no stream, no network."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, hangs_for=None):
        self._stdout, self._stderr = stdout, stderr
        # Like a real child: no exit status until it has exited.
        self._exit_status = returncode
        self.returncode = None
        self._hangs_for = hangs_for
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hangs_for is not None:
            # Bounded just above the reduced fixture timeout: an unbounded sleep
            # turns a mutation that drops `wait_for` into a very long wait
            # instead of a red test.
            await asyncio.sleep(self._hangs_for)
        self.returncode = self._exit_status
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
    return ShazamRecognitionService(settings_service=settings, quota=RecognitionQuota())


@pytest.fixture
def world(monkeypatch):
    w = ShazamWorld(monkeypatch)
    yield w
    w.close()


async def _showing(world, title="So What"):
    """A service on a station with `title` on screen."""
    world.replies = [Reply(match(title))]
    svc = world.service()
    await svc.start("http://stream.example/fip.mp3")
    await world.advance_to(shazam_mod.INITIAL_DELAY_SECONDS + 1)
    assert svc.current_track is not None and svc.current_track["title"] == title
    return svc


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


class TestCaptureCancelled:
    """The loop is cancelled by `stop()` — on every station change, source
    switch, session end and Shazam toggle — and a capture runs for 12 s of
    each 20 s round, so the cut usually lands inside one."""

    @pytest.mark.asyncio
    async def test_a_capture_cut_by_stop_kills_and_reaps_ffmpeg(
        self, service, spawns, monkeypatch
    ):
        """Cancellation is a BaseException and walks past every `except`. Left
        alive, ffmpeg keeps pulling the previous station — a phantom listener
        — for the rest of its segment, and for the life of the backend when
        the server accepts and then sends nothing."""
        monkeypatch.setattr(shazam_mod, "INITIAL_DELAY_SECONDS", 0)
        capturing = _FakeProcess(hangs_for=30)
        spawns.process = capturing

        await service.start("http://stream/fip")
        for _ in range(100):
            if spawns:
                break
            await asyncio.sleep(0)
        assert spawns, "the loop never reached the capture"

        await asyncio.wait_for(service.stop(), timeout=5)

        assert capturing.killed is True
        assert capturing.waited is True   # reaped, not left a zombie


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
        both writing the same title."""
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
    async def test_a_new_url_drops_the_previous_stations_track(self, world):
        svc = await _showing(world)
        await svc.start("http://stream.example/nova.aac")
        try:
            assert svc.current_track is None
            assert world.titles() == ["So What", None]
        finally:
            await svc.stop()

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
    async def test_stop_tells_the_source_the_title_is_gone(self, world):
        """The player keeps whatever it was last given. Without this the stopped
        station's track stays under the *next* station's name."""
        svc = await _showing(world)

        await svc.stop()

        assert world.titles() == ["So What", None]

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
    async def test_a_callback_that_raises_still_leaves_the_service_stopped(self, world):
        """`stop` is awaited on the play path. An exception here aborts the
        switch and leaves the old loop's state behind."""
        svc = await _showing(world)
        svc._on_track_changed = AsyncMock(side_effect=RuntimeError("ws gone"))

        await svc.stop()

        assert svc.is_running is False
        assert svc.current_track is None

    @pytest.mark.asyncio
    async def test_stop_says_stopped_only_when_something_ran(self, service, caplog):
        """`stop()` runs twice per end (the session's end, then `close()`), and
        on a service that never started: 130 "stopped" lines for 32 starts."""
        with caplog.at_level("INFO", logger="source.radio.shazam"):
            await service.stop()
            await service.start("http://stream/fip")
            await service.stop()
            await service.close()

        stopped = [r for r in caplog.records if r.getMessage() == "Shazam recognition stopped"]
        assert len(stopped) == 1

    @pytest.mark.asyncio
    async def test_stop_lands_while_a_request_is_in_flight(self, world):
        """`stop()` cancels the loop while Shazam has not answered. A catch-all
        widened to BaseException would swallow the cancellation and keep the
        loop asking about the previous station after stop returned."""
        world.replies = []                    # the first request never answers
        svc = world.service()
        await svc.start("http://stream.example/fip.mp3")
        await world.advance_to(shazam_mod.INITIAL_DELAY_SECONDS + 1)
        assert world.requests == [shazam_mod.INITIAL_DELAY_SECONDS]

        await asyncio.wait_for(svc.stop(), timeout=2)

        assert svc.is_running is False

    @pytest.mark.asyncio
    async def test_stop_on_a_service_that_never_started_is_harmless(self, service):
        await service.stop()
        assert service.is_running is False

    @pytest.mark.asyncio
    async def test_only_close_ends_the_http_session(self, world):
        """One aiohttp session for the service's life. `stop()` runs on every
        station change of a service that lives on, so closing there would
        reopen a connection per station; not closing in `close()` leaks it."""
        svc = await _showing(world)
        await svc.start("http://stream.example/nova.aac")
        await svc.stop()
        assert len(world.sessions) == 1
        assert world.sessions[0].closes == 0

        await svc.close()
        assert world.sessions[0].closes == 1
        assert svc.is_running is False


class TestTransport:
    """`_ShazamTransport` — how one HTTP answer reaches the loop. Every status
    but 200, and every 200 that is not JSON, is a refusal: shazamio's own
    client returned a 403's or a JSON 5xx's body as an answer, which read as
    "no match" and cleared the title."""

    @pytest.fixture
    def transport(self, world):
        return shazam_mod._ShazamTransport()

    @pytest.mark.asyncio
    async def test_a_200_is_its_json(self, world, transport):
        world.replies = [Reply(match("So What"))]

        answer = await transport.request("POST", "https://amp.shazam.com/x", json={})

        assert answer["track"]["title"] == "So What"

    @pytest.mark.asyncio
    async def test_a_429_is_refused_with_its_retry_after_and_never_retried(self, world, transport):
        """shazamio's client retried a 429 itself, 0.2 s later: every refusal
        cost two calls to a limiter that counts them."""
        world.replies = [Reply(status=429, headers={"Retry-After": "120"}), Reply(match("So What"))]

        with pytest.raises(ShazamRefused) as refused:
            await transport.request("POST", "https://amp.shazam.com/x", json={})

        assert (refused.value.status, refused.value.retry_after_s) == (429, 120.0)
        assert len(world.requests) == 1

    @pytest.mark.parametrize("status", [400, 403, 500, 503])
    @pytest.mark.asyncio
    async def test_any_other_status_is_refused_even_with_a_json_body(self, world, transport, status):
        world.replies = [Reply({"matches": []}, status=status)]

        with pytest.raises(ShazamRefused) as refused:
            await transport.request("POST", "https://amp.shazam.com/x", json={})

        assert refused.value.status == status
        assert len(world.requests) == 1

    @pytest.mark.parametrize("body", ["<html>portal</html>", '{"matches": [', ""])
    @pytest.mark.asyncio
    async def test_a_200_that_is_not_json_is_refused(self, world, transport, body):
        world.replies = [Reply(body)]

        with pytest.raises(ShazamRefused) as refused:
            await transport.request("POST", "https://amp.shazam.com/x", json={})

        assert refused.value.status == 200

    @pytest.mark.asyncio
    async def test_the_session_is_reopened_only_once_closed(self, world, transport):
        world.replies = [Reply(match("A")), Reply(match("B")), Reply(match("C"))]

        await transport.request("POST", "https://amp.shazam.com/x")
        await transport.request("POST", "https://amp.shazam.com/x")
        assert len(world.sessions) == 1

        await transport.close()
        await transport.request("POST", "https://amp.shazam.com/x")
        assert len(world.sessions) == 2
