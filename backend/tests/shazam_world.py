"""Shazam's outside world, as the recognition loop meets it: the stream ffmpeg
captures, shazamio, Shazam's HTTP endpoint and the clock — each a stand-in a
scenario scripts, none of them reachable for real.

What Shazam was measured to answer (journal of 2026-11-02..08, 399 tries):
a match is a 200 carrying `track` and at least one `matches` entry; "no match"
is a 200 with `matches: []`; throttling is a 429, from about 3 calls in a
rolling minute. Whether it sends Retry-After or `retryms` was never seen, so
both are scripted only where a scenario says so.

- shazamio is replaced by `_Shazamio`: it skips the signature (shazamio_core,
  CPU work on real audio) and makes the one call `Shazam.recognize` makes —
  `http_client.request("POST", …, json=…)` — through the service's own
  transport, so the transport's reading of every answer is what the loop gets.
- aiohttp's `ClientSession` is replaced by `_Session`, which answers each
  request with the next scripted `Reply` (or raises the scripted exception, or
  `Hang`s); once the script is spent it never answers, and the run ends.
- ffmpeg is `_Capture`: a clean exit with audio, unless the scenario queued a
  failure.
- The loop's clock (`time.monotonic`) moves only by its own sleeps, and a run
  ends when a sleep would carry it past `until`. The backoff's jitter is the
  factor the scenario sets (1.0 by default).
"""
import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import backend.sources.radio.shazam as shazam_mod
from backend.sources.radio.shazam import RecognitionQuota, ShazamRecognitionService
from backend.tests.golden.harness import AsyncioProxy

_real_sleep = asyncio.sleep

URL = "http://stream.example/fip.mp3"


def match(title: str, artist: str = "Artist", **extra: Any) -> Dict[str, Any]:
    """A 200 body naming a track, as Shazam's `discovery/v5` answers it."""
    return {"matches": [{"id": "1"}], "track": {"title": title, "subtitle": artist}, **extra}


def no_match(**extra: Any) -> Dict[str, Any]:
    return {"matches": [], **extra}


class Reply:
    """One HTTP answer: a status, a body (a dict is sent as JSON, a str as is)
    and headers."""

    def __init__(self, body: Any = None, status: int = 200, headers: Optional[Dict[str, str]] = None):
        self.body = body
        self.status = status
        self.headers = headers or {}


class Hang:
    """An answer that does not come within the recognition timeout."""


class _Clock:
    """The loop's clock: time passes only in its sleeps, and never past
    `until` — a sleep that would cross it waits there, `finished` set, until
    the scenario moves `until` on (`run_to`)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.until = float("inf")
        self.finished = asyncio.Event()
        self._gate = asyncio.Event()

    async def sleep(self, delay: float, *a: Any, **k: Any) -> None:
        target = self.now + max(delay, 0.0)
        while target > self.until:
            self.now = self.until
            self.finished.set()
            await self._gate.wait()
        self.now = target
        await _real_sleep(0)

    def run_to(self, until: float) -> None:
        self.until = until
        self.finished.clear()
        gate, self._gate = self._gate, asyncio.Event()
        gate.set()


class _Response:
    def __init__(self, reply: Reply) -> None:
        self.status = reply.status
        self.headers = reply.headers
        self._body = reply.body

    async def text(self) -> str:
        if isinstance(self._body, str):
            return self._body
        return json.dumps(self._body)


class _Exchange:
    def __init__(self, world: "ShazamWorld", method: str, url: str, kwargs: Dict[str, Any]):
        self._world, self.method, self.url, self.kwargs = world, method, url, kwargs

    async def __aenter__(self) -> _Response:
        world = self._world
        world.requests.append(world.clock.now)
        if not world.replies:
            world.clock.finished.set()
            await asyncio.get_running_loop().create_future()   # until stopped
        reply = world.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Hang):
            await _real_sleep(30)
        return _Response(reply)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Session:
    """aiohttp.ClientSession, as the transport uses it."""

    def __init__(self, world: "ShazamWorld", **_: Any) -> None:
        self._world = world
        self.closed = False
        self.closes = 0
        world.sessions.append(self)

    def request(self, method: str, url: str, **kwargs: Any) -> _Exchange:
        if self.closed:
            raise RuntimeError("Session is closed")
        return _Exchange(self._world, method, url, kwargs)

    async def close(self) -> None:
        self.closed = True
        self.closes += 1


class _Shazamio:
    """shazamio.Shazam, down to its one HTTP call."""

    def __init__(self, http_client: Any = None, segment_duration_seconds: int = 0, **_: Any):
        self.http_client = http_client

    async def recognize(self, data: bytes) -> Any:
        return await self.http_client.request(
            "POST", "https://amp.shazam.com/discovery/v5/en-US/GB/iphone/-/tag/A/B",
            headers={"X-Shazam-Platform": "IPHONE"}, proxy=None, json={"samplems": len(data)},
        )


class _Capture:
    """The ffmpeg child: a clean exit with audio, or the failure queued."""

    def __init__(self, ok: bool) -> None:
        self._ok = ok
        self.returncode: Optional[int] = None

    async def communicate(self) -> Tuple[bytes, bytes]:
        self.returncode = 0 if self._ok else 1
        return (b"\x00\x01" * 8000 if self._ok else b""), b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> Optional[int]:
        return self.returncode


class _StampedLines(logging.Handler):
    def __init__(self, clock: _Clock) -> None:
        super().__init__(logging.DEBUG)
        self.clock = clock
        self.lines: List[Tuple[str, float, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((record.levelname, self.clock.now, record.getMessage()))


class ShazamWorld:
    """Everything outside `ShazamRecognitionService`, scripted.

    `replies` is the queue of HTTP answers, `captures_failing` how many of the
    next captures fail; `captures` and `requests` the clock time of every
    ffmpeg spawn and of every HTTP call that reached Shazam, `shown` every
    (time, track) the service told its source.
    `close()` undoes the logging set-up (monkeypatch undoes the rest).
    """

    def __init__(self, monkeypatch: Any) -> None:
        self.clock = _Clock()
        self.replies: List[Any] = []
        self.requests: List[float] = []
        self.sessions: List[_Session] = []
        self.captures: List[float] = []
        self.captures_failing = 0
        self.jitter = 1.0
        self.jitter_bounds: List[Tuple[float, float]] = []
        self.enabled = True
        self.shown: List[Tuple[float, Optional[Dict[str, Any]]]] = []
        world = self

        class _ShazamAsyncio(AsyncioProxy):
            async def create_subprocess_exec(self, *argv: Any, **kwargs: Any) -> _Capture:
                world.captures.append(world.clock.now)
                failing = world.captures_failing > 0
                world.captures_failing -= int(failing)
                return _Capture(ok=not failing)

        def uniform(low: float, high: float) -> float:
            world.jitter_bounds.append((low, high))
            return world.jitter

        monkeypatch.setattr(shazam_mod, "asyncio", _ShazamAsyncio(self.clock.sleep))
        monkeypatch.setattr(shazam_mod, "time", SimpleNamespace(monotonic=lambda: world.clock.now))
        monkeypatch.setattr(shazam_mod, "random", SimpleNamespace(uniform=uniform))
        monkeypatch.setattr(shazam_mod, "Shazam", _Shazamio)
        monkeypatch.setattr(shazam_mod.aiohttp, "ClientSession", lambda **kw: _Session(world, **kw))

        self.settings = Mock()
        self.settings.get_setting = AsyncMock(
            side_effect=lambda _section: {"shazam_enabled": world.enabled}
        )
        self._logger = logging.getLogger("source.radio.shazam")
        self._level = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._stamped = _StampedLines(self.clock)
        self._logger.addHandler(self._stamped)

    def close(self) -> None:
        self._logger.removeHandler(self._stamped)
        self._logger.setLevel(self._level)

    @property
    def lines(self) -> List[Tuple[str, float, str]]:
        """Every line the service logged, with the clock's time."""
        return self._stamped.lines

    def logged(self, *levels: str) -> List[Tuple[str, float, str]]:
        return [line for line in self.lines if line[0] in levels]

    def service(self, quota: Optional[RecognitionQuota] = None) -> ShazamRecognitionService:
        async def on_track_changed(track: Optional[Dict[str, Any]]) -> None:
            self.shown.append((self.clock.now, track))

        return ShazamRecognitionService(
            settings_service=self.settings,
            quota=quota if quota is not None else RecognitionQuota(),
            on_track_changed=on_track_changed,
        )

    async def advance_to(self, until: float) -> None:
        """Let the clock run to `until`, or until the script runs out."""
        self.clock.run_to(until)
        await asyncio.wait_for(self.clock.finished.wait(), timeout=5)

    async def run(self, service: ShazamRecognitionService, until: float,
                  url: str = URL, stop: bool = True) -> None:
        """Start `service` on `url` and let the clock run to `until` (or the
        script run out), then stop it unless told not to."""
        await service.start(url)
        await self.advance_to(until)
        if stop:
            await service.stop()

    def titles(self) -> List[Optional[str]]:
        """What the source was told, in order: a title, or None for a clear."""
        return [track["title"] if track else None for _, track in self.shown]
