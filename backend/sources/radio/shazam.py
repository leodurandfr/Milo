# backend/sources/radio/shazam.py
"""
Shazam-based track recognition for radio streams.

Captures a short segment of the stream with ffmpeg, asks Shazam what it is
(shazamio builds the signature, the one HTTP call goes through
`_ShazamTransport`), and decides from the answers what the player names.

Measured on the unit over 399 tries (journal of 2026-11-02..08):
- Shazam refuses (429) from about 3 calls in a rolling minute: 0 % of tries
  refused with 0-1 calls in the minute before, 6 % with 2-3, 60 % with 4-5.
- An immediate retry is wasted: after a no-match it was a no-match again 60
  times out of 75, after a 429 a 429 89 out of 94. First tries matched 142
  times out of 229, retries 9 out of 169.
- One verdict is not an answer: while throttled, "no match" alternates with
  429s, and 5 of 22 title changes and 12 clears came from a single verdict.

Hence the four rules below:
- **One HTTP call per try, no retry anywhere.** `_ShazamTransport` replaces
  shazamio's client (and its aiohttp-retry layer), and any answer that is not
  a decodable 200 is a refusal — a 403 or a JSON 5xx used to read as "no
  match" and clear the title.
- **Two agreeing verdicts move the title** (`DisplayedTitle`): a single no-match
  never clears it, a single different match never replaces it, and the first
  title needs two. A refusal or a failure is no verdict at all; a shown title
  that gets none for `STALE_AFTER_S` is cleared.
- **Ask only when the answer can have changed** (`next_delay`): a held title is
  asked about less and less often, a candidate is confirmed quickly, and a
  refusal backs off (`backoff_delay`) — never before the Retry-After or the
  `retryms` Shazam gave.
- **One budget for the whole radio source** (`RecognitionQuota`): at most
  `QUOTA_REQUESTS` per `QUOTA_WINDOW_S`, and the backoff deadline, both kept
  by `RadioSource` for its whole life, so neither a station change nor a
  source switch starts them over.
"""
import asyncio
import contextlib
import enum
import io
import json
import logging
import random
import time
import wave
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Deque, Dict, Optional, Tuple

import aiohttp
from shazamio import Shazam
from shazamio.interfaces.client import HTTPClientInterface

from backend.shared.network import describe_network_error, retry_after

logger = logging.getLogger("source.radio.shazam")

# Shazam is a *fallback* (started only once in-band metadata stayed empty — see
# RadioSource), so the stream has already played for a grace period.
INITIAL_DELAY_SECONDS = 5
SEGMENT_DURATION_SECONDS = 12
RECOGNITION_TIMEOUT_SECONDS = 25

# ffmpeg capture timeout (capture duration + buffer for connection/codec init)
FFMPEG_TIMEOUT_SECONDS = SEGMENT_DURATION_SECONDS + 15

# When to ask next (`next_delay`). A capture that failed sent nothing, so it is
# tried again soon, a few times; a candidate is confirmed soon; a title that
# holds, and a stream that keeps answering "no match", are asked about less and
# less often — the answer is not going to change every 20 s.
CAPTURE_RETRY_S = 10
CAPTURE_RETRIES = 3
CONFIRM_S = 20
HOLD_STEPS_S = (60, 90, 120, 180)
MISS_STEPS_S = (30, 60, 120, 240)

# A refusal, a timeout or a transport error: 60 s × 2^n, ±20 %, at most 900 s.
BACKOFF_BASE_S = 60
BACKOFF_CAP_S = 900
BACKOFF_JITTER = 0.2

# A shown title that no verdict has confirmed or contradicted for this long
# (only refusals and failures since) is cleared: nothing is known any more.
STALE_AFTER_S = 300

# At most this many requests in any window: under the ~3 per minute at which
# Shazam starts refusing.
QUOTA_REQUESTS = 2
QUOTA_WINDOW_S = 60


class ShazamRefused(Exception):
    """Shazam answered, but not with a usable 200 — a 429, any other status, or
    a body that is not JSON. Never a verdict about the audio."""

    def __init__(self, status: int, retry_after_s: Optional[float] = None, detail: str = ""):
        self.status = status
        self.retry_after_s = retry_after_s
        super().__init__(f"refused ({status}{', ' + detail if detail else ''})")


class _ShazamTransport(HTTPClientInterface):
    """shazamio's HTTP client, with no retry: one try is one call.

    shazamio's own client retries a 429 itself (aiohttp-retry), drops the status
    and the headers with it, and hands back any non-429 JSON body as if it were
    an answer. This one keeps the status and the Retry-After, and raises
    `ShazamRefused` for everything that is not a decodable 200.

    One aiohttp session for the life of the service (`close()` ends it), opened
    on the first call, inside the loop that makes it.
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        async with self._client().request(method, url, **kwargs) as response:
            if response.status != 200:
                raise ShazamRefused(response.status, retry_after(response.headers))
            try:
                return json.loads(await response.text())
            except ValueError:
                raise ShazamRefused(200, detail="not JSON") from None

    async def request_text(self, url: str, *, content_type: str, **kwargs: Any) -> str:
        raise NotImplementedError("recognition never asks for text")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


class Verdict(enum.Enum):
    MATCH = "match"          # a 200 naming a track
    NO_MATCH = "no match"    # a 200 naming nothing usable
    REFUSED = "refused"      # Shazam answered something else (ShazamRefused)
    FAILED = "failed"        # no answer: the capture, a timeout, the transport


@dataclass(frozen=True)
class Attempt:
    """What one try learned. `sent` is False when nothing reached Shazam (the
    capture failed); `wait_s` is the least Shazam asked to be left alone for
    (Retry-After, or `retryms` in a 200)."""

    verdict: Verdict
    track: Optional[Dict[str, Any]] = None
    wait_s: Optional[float] = None
    sent: bool = True
    reason: str = ""


class Step(enum.Enum):
    """What a try means for the next one (`next_delay`)."""

    CAPTURE_FAILED = "capture failed"    # nothing was sent
    CANDIDATE = "candidate"              # a match naming something new
    HOLD = "hold"                        # a match naming what is shown
    MISS = "miss"                        # no match, nothing shown to clear
    CLEAR_CANDIDATE = "clear candidate"  # no match while a title is shown


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").split()).casefold()


def _key(track: Optional[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    if track is None:
        return None
    return _norm(track.get("title")), _norm(track.get("artist"))


_NOTHING = object()   # a pending verdict of "no match": it would clear the title


class DisplayedTitle:
    """The title the player shows, moved only by two consecutive verdicts that
    agree on the new value (a track, or nothing).

    Tracks compare on title and artist, case and spacing aside. A verdict equal
    to what is shown drops any pending candidate; only refusals and failures —
    which never reach `hear` — leave one standing.
    """

    def __init__(self) -> None:
        self.track: Optional[Dict[str, Any]] = None
        self._pending: Any = None            # None, _NOTHING or a track
        self._heard_at: Optional[float] = None

    def hear(self, track: Optional[Dict[str, Any]], now: float) -> Tuple[Step, bool]:
        """Take one verdict (None = no match); answer its step and whether the
        shown title changed."""
        self._heard_at = now
        key = _key(track)
        if key == _key(self.track):
            self._pending = None
            return (Step.HOLD if track else Step.MISS), False
        if self._pending is not None:
            pending = None if self._pending is _NOTHING else self._pending
            if _key(pending) == key:
                self.track = track
                self._pending = None
                return (Step.HOLD if track else Step.MISS), True
        self._pending = track if track is not None else _NOTHING
        return (Step.CANDIDATE if track else Step.CLEAR_CANDIDATE), False

    def stale_at(self) -> Optional[float]:
        """When the shown title expires for want of a verdict, if one is shown."""
        if self.track is None or self._heard_at is None:
            return None
        return self._heard_at + STALE_AFTER_S

    def clear(self) -> None:
        self.track = None
        self._pending = None


def next_delay(step: Step, streak: int) -> float:
    """Seconds to the next try after one that took `step`, the `streak`-th in a
    row of its kind (1 for the first): consecutive capture failures, matches
    holding the shown title, or no-matches."""
    def stepped(steps: Tuple[int, ...], n: int) -> float:
        return float(steps[min(max(n, 1), len(steps)) - 1])

    if step is Step.CAPTURE_FAILED:
        if streak <= CAPTURE_RETRIES:
            return float(CAPTURE_RETRY_S)
        return stepped(MISS_STEPS_S, streak - CAPTURE_RETRIES)
    if step in (Step.CANDIDATE, Step.CLEAR_CANDIDATE):
        return float(CONFIRM_S)
    if step is Step.HOLD:
        return stepped(HOLD_STEPS_S, streak)
    return stepped(MISS_STEPS_S, streak)


def backoff_delay(refusals: int, jitter: float) -> float:
    """Seconds to wait after the (`refusals` + 1)-th refusal or failure in a
    row, `jitter` being the factor drawn in [1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER]."""
    return min(float(BACKOFF_CAP_S), BACKOFF_BASE_S * 2 ** min(refusals, 16) * jitter)


class RecognitionQuota:
    """The requests Shazam may still be sent, shared by every recognition run.

    A sliding window (at most `QUOTA_REQUESTS` in any `QUOTA_WINDOW_S`), a
    deadline nothing is sent before (a backoff, a Retry-After, a `retryms`),
    and the count of refusals in a row the backoff grows on. `RadioSource`
    builds one for its whole life and hands it to every service it creates, so
    zapping or a source switch neither empties the window nor resets the
    backoff.
    """

    def __init__(self, requests: int = QUOTA_REQUESTS, window_s: float = QUOTA_WINDOW_S):
        self._requests = requests
        self._window_s = window_s
        self._sent: Deque[float] = deque()
        self._not_before = float("-inf")
        self._refusals = 0

    def ready_at(self) -> float:
        """The monotonic time from which the next request may go."""
        now = time.monotonic()
        while self._sent and self._sent[0] <= now - self._window_s:
            self._sent.popleft()
        at = self._not_before
        if len(self._sent) >= self._requests:
            at = max(at, self._sent[0] + self._window_s)
        return at

    def spend(self) -> None:
        """A request goes out now."""
        self._sent.append(time.monotonic())

    def defer(self, delay: float) -> None:
        """Send nothing for `delay` seconds from now (a later deadline stands)."""
        self._not_before = max(self._not_before, time.monotonic() + delay)

    @property
    def backing_off(self) -> bool:
        """True since a refusal or a failure, until Shazam answers 200 again."""
        return self._refusals > 0

    def back_off(self, at_least: Optional[float] = None) -> float:
        """A refusal or a failure: the wait it earns (never under `at_least`),
        which is also the new deadline."""
        jitter = random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)
        delay = backoff_delay(self._refusals, jitter)
        if at_least is not None:
            delay = max(delay, at_least)
        self._refusals += 1
        self.defer(delay)
        return delay

    def answered(self) -> None:
        """Shazam answered 200: the backoff starts over."""
        self._refusals = 0


def _retryms(result: Dict[str, Any]) -> Optional[float]:
    """The wait, in seconds, a 200 asks for in its `retryms`, if any."""
    value = result.get("retryms")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value / 1000


def _named(track: Optional[Dict[str, Any]]) -> str:
    if track is None:
        return "nothing"
    return f"{track.get('artist') or '?'} – {track.get('title')}"


class ShazamRecognitionService:
    """
    Track recognition for one radio stream at a time, using ShazamIO.

    `start(url)` runs the loop for a station, `stop()` ends it (a station
    change keeps the service and its HTTP session), `close()` ends the service.
    """

    def __init__(
        self,
        settings_service,
        quota: RecognitionQuota,
        on_track_changed: Optional[Callable[[Optional[Dict[str, Any]]], Coroutine]] = None,
    ):
        """
        Args:
            settings_service: SettingsService for checking shazam_enabled
            quota: the request budget shared with every other run (RadioSource's)
            on_track_changed: Async callback invoked when the shown track changes,
                              with the track dict or None (cleared).
        """
        self._settings_service = settings_service
        self._quota = quota
        self._on_track_changed = on_track_changed
        self._transport = _ShazamTransport()
        self._shazam = Shazam(
            http_client=self._transport,
            segment_duration_seconds=SEGMENT_DURATION_SECONDS,
        )

        # State of the run in progress (reset by start())
        self._stream_url: Optional[str] = None
        self._display = DisplayedTitle()
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False
        self._preroll_skip: int = 0
        self._capture_failures = 0
        self._holds = 0
        self._misses = 0
        self._error_reported = False

    @property
    def is_running(self) -> bool:
        """True while the recognition loop is alive."""
        return (
            self._running
            and self._loop_task is not None
            and not self._loop_task.done()
        )

    @property
    def current_track(self) -> Optional[Dict[str, Any]]:
        """The track shown — 'title', 'artist', 'artwork' — or None."""
        return self._display.track

    async def start(self, stream_url: str, preroll_skip: int = 0) -> None:
        """
        Start recognition for the given stream URL.

        If already running on the same URL, this is a no-op.
        If running on a different URL, restarts with the new URL.

        Args:
            stream_url: The radio stream URL to capture audio from
            preroll_skip: Seconds to skip at start of each capture (pre-roll ads)
        """
        if self.is_running and self._stream_url == stream_url:
            return

        if self._loop_task is not None:
            await self.stop()

        self._stream_url = stream_url
        self._preroll_skip = preroll_skip
        self._display = DisplayedTitle()
        self._capture_failures = self._holds = self._misses = 0
        self._error_reported = False
        self._running = True

        self._loop_task = asyncio.create_task(self._recognition_loop())

        logger.info(f"Shazam recognition started for stream: {stream_url}")

    async def stop(self) -> None:
        """Stop the recognition loop and clear the title."""
        was_running = self._loop_task is not None
        self._running = False

        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

        previous_track = self._display.track
        self._display = DisplayedTitle()
        self._stream_url = None

        if previous_track:
            await self._notify(None)

        if was_running:
            logger.info("Shazam recognition stopped")

    async def close(self) -> None:
        """Stop recognition and release the HTTP session. The service is not reusable after."""
        await self.stop()
        await self._transport.close()

    async def _notify(self, track: Optional[Dict[str, Any]]) -> None:
        if not self._on_track_changed:
            return
        try:
            await self._on_track_changed(track)
        except Exception as e:
            logger.error(f"Error in track changed callback: {e}")

    async def _recognition_loop(self) -> None:
        """Try, then wait as long as the answer says, until stopped.

        The body is guarded: an unexpected error is reported once per run and
        backed off like a failure, so the loop never dies under `is_running`.
        """
        next_at = time.monotonic() + INITIAL_DELAY_SECONDS
        while self._running:
            try:
                await self._wait_until(next_at)
                next_at = await self._take_turn()
            except Exception as e:
                if not self._error_reported:
                    self._error_reported = True
                    logger.error(f"Shazam recognition loop error (it goes on): {e!r}")
                else:
                    logger.debug(f"Shazam recognition loop error again: {e!r}")
                next_at = time.monotonic() + self._quota.back_off()

    async def _wait_until(self, due: float) -> None:
        """Sleep until `due` and until the quota lets a request go, clearing
        the shown title on the way if it goes stale meanwhile."""
        while True:
            now = time.monotonic()
            stale_at = self._display.stale_at()
            if stale_at is not None and stale_at <= now:
                self._display.clear()
                logger.info(f"Shazam: title cleared (no verdict for {STALE_AFTER_S} s)")
                await self._notify(None)
                continue
            ready = max(due, self._quota.ready_at())
            if ready <= now:
                return
            wake = ready if stale_at is None else min(ready, stale_at)
            await asyncio.sleep(wake - now)

    async def _take_turn(self) -> float:
        """One try, and what it did to the title; answers when to try next."""
        if not await self.is_enabled():
            # Recognition was switched off under a running loop.
            if self._display.track is not None:
                self._display.clear()
                logger.info("Shazam: title cleared (recognition turned off)")
                await self._notify(None)
            return time.monotonic() + MISS_STEPS_S[0]

        attempt = await self._attempt()
        now = time.monotonic()

        if not attempt.sent:
            self._capture_failures += 1
            logger.debug(f"Shazam: no audio captured ({self._capture_failures} in a row)")
            return now + next_delay(Step.CAPTURE_FAILED, self._capture_failures)
        self._capture_failures = 0

        if attempt.verdict in (Verdict.REFUSED, Verdict.FAILED):
            first = not self._quota.backing_off
            delay = self._quota.back_off(attempt.wait_s)
            say = logger.info if first else logger.debug
            say(f"Shazam {attempt.reason}, next try in {delay:.0f} s")
            return now + delay

        self._quota.answered()
        if attempt.wait_s is not None:
            self._quota.defer(attempt.wait_s)

        step, changed = self._display.hear(attempt.track, now)
        if attempt.verdict is Verdict.MATCH:
            self._misses = 0
            self._holds = self._holds + 1 if step is Step.HOLD else 0
        else:
            self._holds = 0
            self._misses += 1
        delay = next_delay(step, self._holds if step is Step.HOLD else self._misses)
        if attempt.wait_s is not None:
            delay = max(delay, attempt.wait_s)
        logger.debug(
            f"Shazam heard {_named(attempt.track)} ({step.value}), next try in {delay:.0f} s"
        )

        if changed:
            shown = self._display.track
            if shown is None:
                logger.info("Shazam: title cleared")
            else:
                logger.info(f"Shazam: {_named(shown)}")
            await self._notify(shown)
        return now + delay

    async def _attempt(self) -> Attempt:
        """Capture, ask Shazam once, and classify what came back."""
        audio_bytes = await self._capture_audio(self._stream_url) if self._stream_url else None
        if not audio_bytes:
            return Attempt(Verdict.FAILED, sent=False, reason="no audio captured")

        self._quota.spend()
        try:
            result = await asyncio.wait_for(
                self._shazam.recognize(audio_bytes),
                timeout=RECOGNITION_TIMEOUT_SECONDS,
            )
        except ShazamRefused as e:
            return Attempt(Verdict.REFUSED, wait_s=e.retry_after_s, reason=str(e))
        except asyncio.TimeoutError:
            return Attempt(Verdict.FAILED, reason="timed out")
        except aiohttp.ClientError as e:
            return Attempt(Verdict.FAILED, reason=f"unreachable ({describe_network_error(e)})")

        if not isinstance(result, dict):
            return Attempt(Verdict.REFUSED, reason="refused (200, not a JSON object)")
        track = self._parse_result(result)
        return Attempt(
            Verdict.MATCH if track else Verdict.NO_MATCH,
            track=track,
            wait_s=_retryms(result),
        )

    async def _capture_audio(self, url: str) -> Optional[bytes]:
        """
        Capture audio from stream using ffmpeg and produce a valid WAV buffer.

        Outputs raw PCM from ffmpeg (avoiding the piped WAV header size issue
        where ffmpeg writes 0xFFFFFFFF as RIFF size, which rodio/hound rejects)
        and wraps it with Python's wave module to produce correct RIFF headers.

        If a pre-roll skip is configured, ffmpeg decodes and discards that many
        seconds before capturing, bypassing pre-roll ads injected on connection.
        """
        process = None
        try:
            cmd = ["ffmpeg", "-i", url]
            if self._preroll_skip > 0:
                cmd += ["-ss", str(self._preroll_skip)]
            cmd += [
                "-t", str(SEGMENT_DURATION_SECONDS),
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", "16000",
                "-v", "quiet", "pipe:1",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            timeout = FFMPEG_TIMEOUT_SECONDS + self._preroll_skip
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            if process.returncode != 0 or not stdout:
                logger.debug(f"ffmpeg capture failed (exit {process.returncode})")
                return None

            # Wrap raw PCM in a proper WAV container with correct headers
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(stdout)

            return wav_buffer.getvalue()

        except asyncio.TimeoutError:
            logger.debug("Audio capture timed out")
            return None
        except Exception as e:
            logger.debug(f"Audio capture failed: {e}")
            return None
        finally:
            # Also on cancellation (stop() at every station change), which no
            # `except` above sees: a live ffmpeg keeps pulling the old stream.
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    def _parse_result(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse ShazamIO recognition result into a simple track dict.

        Returns:
            Dict with 'title', 'artist', 'artwork' keys, or None if not recognized.
        """
        if not result or "track" not in result:
            return None

        # Confidence gate: a genuine recognition carries at least one match.
        # A `track` block with zero matches is a low-confidence guess — drop it
        # rather than pin a wrong title. (Real FIP matches were all `1 matches`,
        # so requiring ≥1 does not over-gate.)
        if not result.get("matches"):
            return None

        track = result["track"]
        title = track.get("title")
        artist = track.get("subtitle")

        if not title:
            return None

        # Extract artwork URL from sections or top-level
        artwork = None
        # Try share.image first (highest quality)
        share = track.get("share", {})
        if share.get("image"):
            artwork = share["image"]
        # Fallback to images.coverart
        if not artwork:
            images = track.get("images", {})
            artwork = images.get("coverart") or images.get("coverarthq")

        # Upgrade artwork resolution from default 400x400 to 1280x1280
        if artwork:
            artwork = artwork.replace("/400x400cc.jpg", "/1280x1280cc.jpg")

        return {
            "title": title,
            "artist": artist or "",
            "artwork": artwork
        }

    async def is_enabled(self) -> bool:
        """Check if Shazam recognition is enabled in settings."""
        try:
            radio_settings = await self._settings_service.get_setting("radio")
            return radio_settings["shazam_enabled"]
        except Exception as e:
            logger.debug(f"Shazam is_enabled check failed, defaulting to True: {e}")
            return True
