# backend/shared/timing.py
"""One way to say an operation was slow, and one table of what slow means.

An operation is timed by a `Timer`: started when the operation begins, stopped
when it ends. Past its threshold, the stop writes one WARNING,

    slow: <operation> took N ms (threshold M ms): <detail>

and nothing below it. The threshold is declared once, in `THRESHOLDS_MS`, keyed
by the operation's name, and every one of them comes from a measurement on a
unit, cited beside it — never a guess. An operation nobody has measured yet is
declared with `None` ("to measure"): its stop writes the duration at INFO,

    timed: <operation> took N ms (threshold to measure): <detail>

so a unit run for a while yields the samples a threshold is derived from, and
it never writes a WARNING on a number nobody measured. INFO, not DEBUG: the
backend runs at INFO (`MILO_LOG_LEVEL` in milo-backend.service), so the samples
reach the journal without a change to the unit; DEBUG would need the whole
backend raised, which floods the 54 MB journal in hours.

Three forms, one implementation (`Timer`):

  * `with timed(op, detail):` / `async with timed(op, detail):` — an operation
    inside one block;
  * `timer = start(op, detail)` … `timer.stop()` — an operation that begins in
    one place and ends in another (a source's session, opened by a load and
    playing at a later phase event). `cancel()` drops it without a line;
  * `@measured(op)` on a coroutine function — every call is one operation,
    the function's name as its detail (`detail=` adds what the call worked
    on), and a call whose result `failed=` calls a failure writes nothing.

A block left by an exception, and a timer canceled, write nothing: a failure
is logged by its own handler, at its own level, and a "slow" line beside it
would report the same event twice (a transition that timed out at 15 s is a
`Transition failed` warning already).

The WARNING never raises the UI's error banner: this module logs under
`backend.shared.timing`, and `WebSocketLogHandler` forwards ERROR and above
only (`backend/main.py`). One logger for every operation, so the lines are found
with one grep and silenced, if ever needed, with one level.
"""
import functools
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# What "slow" means, per operation, in milliseconds. `None`: TO MEASURE — the
# duration is logged at INFO until enough samples exist to derive one.
#
# Every measured value below was read on the unit `milo` on 2026-11-27, from
# the milo-backend journal of 2026-11-25 00:10 to 2026-11-27 (journald keeps
# 54 MB, two days of INFO). A threshold is the larger of median × 3 and
# p90 × 1.5, rounded up to the next 500 ms — never down, or a run the rule
# calls normal would warn.
THRESHOLDS_MS: Dict[str, Optional[int]] = {
    # The transition lock taken (its caller holds it, so a transition queued
    # behind another is not timed while it waits) → the final publish of a
    # successful transition (core/state.py::_transition). n=36, median 660 ms, p90 1129 ms, max 1857
    # ms (bluetooth -> tidal), multiroom and direct modes. Paired from
    # `INFO:source.<id>:Stopping|Starting <id>` (or the first snapcast
    # SET_STREAM, for NONE -> X) to `INFO:backend.core.state:Transition
    # completed: `. Median × 3 = 1980 → 2000.
    "source.transition": 2000,
    # A session opened LOADING → its first PLAYING (core/audio_source.py),
    # one operation per source: what a first sound costs differs by source (a
    # radio stream, a CD that spins up, a track off a NAS, a Connect daemon
    # fetching from its CDN), so one source's measurement says nothing of
    # another's. Radio, measured by proxy: n=14 (13 radio, 1 podcast), median
    # 615 ms, p90 1074 ms, max 1525 ms, from `INFO:backend.shared.mpv:Loading
    # stream:` to snapserver's `onStateChanged (<stream>): playing` on that
    # source's stream (multiroom only; ALSA buffering later than mpv's own
    # playback-restart, so the proxy reads long). Median × 3 = 1845 → 2000.
    # The others TO MEASURE (one podcast session is not a sample): five or
    # more plays of each from these lines. A source that never opens a
    # session LOADING never writes one; every source is declared all the
    # same, so none is refused the day it does (test_timing.py holds the
    # list equal to AudioSource).
    "source.until_playing.radio": 2000,
    "source.until_playing.podcast": None,
    "source.until_playing.music_library": None,
    "source.until_playing.cd": None,
    "source.until_playing.spotify": None,
    "source.until_playing.tidal": None,
    "source.until_playing.qobuz": None,
    "source.until_playing.airplay": None,
    "source.until_playing.bluetooth": None,
    "source.until_playing.mac": None,
    # One admission attempt of a speaker that succeeded: its SYNC_RECONNECT
    # line → shown online (core/multiroom/websocket.py). The 3 s retry waits
    # are left out on purpose — a satellite whose API is still booting is the
    # design working, and timing them would flag every retried admission. n=66
    # (47 from two satellites, 19 local), median 343 ms, p90 404 ms, max 1359
    # ms, from `INFO:backend.core.multiroom.websocket:SYNC_RECONNECT: <mac>
    # target=` (the last one) to `INFO:backend.core.multiroom.client_registry:
    # Client <mac> online status: True`. Median × 3 = 1029 → 1500.
    "multiroom.admission": 1500,
    # The steps of a program or Milō update (core/updates/update.py). TO
    # MEASURE: no step is bracketed by a start and an end line on the success
    # path, and journald kept two program updates in all. Measure five or more
    # updates of each program from these lines, per step and per program (the
    # detail names the step and what it worked on); a step that failed writes
    # none.
    "update.backup": None,
    "update.download": None,
    "update.configure": None,
    "update.compile": None,
    "update.install": None,
    "update.verify": None,
    "update.service_control": None,
    "update.rollback": None,
    # One Subsonic JSON request to Navidrome
    # (sources/music_library/navidrome_client.py). TO MEASURE: nothing logged
    # a request with its duration, on Milō's side or Navidrome's. Measure five
    # or more requests per endpoint class (browse, search) from these lines —
    # not the scan watcher's getScanStatus poll, every 3 s through a scan:
    # nobody waits on it, and its lines would crowd the journal out.
    "music_library.navidrome_request": None,
}


def monotonic() -> float:
    """The clock every Timer reads — one name, so a test replaces it alone."""
    return time.monotonic()


class Timer:
    """One timed operation, from its construction to its `stop()`.

    Stopped once: a second `stop()`, or one after `cancel()`, writes nothing and
    answers None, so the two places a long operation can end in may both say so.
    """

    __slots__ = ("operation", "detail", "_started", "_done")

    def __init__(self, operation: str, detail: str = ""):
        if operation not in THRESHOLDS_MS:
            # A name with no declared threshold is a typo or a new operation
            # placed without its measurement: refused at once, never timed
            # against a default.
            raise KeyError(f"no threshold is declared for {operation!r} in THRESHOLDS_MS")
        self.operation = operation
        self.detail = detail
        self._started = monotonic()
        self._done = False

    @property
    def running(self) -> bool:
        return not self._done

    def stop(self) -> Optional[int]:
        """End the operation: the elapsed milliseconds, logged as above, or
        None when it had already ended."""
        if self._done:
            return None
        self._done = True
        elapsed_ms = round((monotonic() - self._started) * 1000)
        threshold = THRESHOLDS_MS[self.operation]
        suffix = f": {self.detail}" if self.detail else ""
        if threshold is None:
            logger.info(
                "timed: %s took %d ms (threshold to measure)%s",
                self.operation, elapsed_ms, suffix,
            )
        elif elapsed_ms > threshold:
            logger.warning(
                "slow: %s took %d ms (threshold %d ms)%s",
                self.operation, elapsed_ms, threshold, suffix,
            )
        return elapsed_ms

    def cancel(self) -> None:
        """Drop the operation: it ended in a way that is not its duration."""
        self._done = True

    def __enter__(self) -> "Timer":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.stop()
        else:
            self.cancel()
        return False

    async def __aenter__(self) -> "Timer":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return self.__exit__(exc_type, exc, tb)


def start(operation: str, detail: str = "") -> Timer:
    """Start timing `operation`; the caller stops (or cancels) it later."""
    return Timer(operation, detail)


def timed(operation: str, detail: str = "") -> Timer:
    """Time the block it opens: `with timed(op):` or `async with timed(op):`."""
    return Timer(operation, detail)


def measured(
    operation: str,
    *,
    detail: Optional[Callable[[tuple, dict], str]] = None,
    failed: Optional[Callable[[Any], bool]] = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Time every call of the coroutine function it decorates, under its name.

    `detail(args, kwargs)` adds what the call worked on to the line (a
    program, a file); `failed(result)` says whether a call that returned is a
    failure — for a function that reports one by its result rather than by
    raising, so that its failure writes no duration, exactly as a raise does.
    """
    # Checked when the decorated module is imported, not at the first call.
    if operation not in THRESHOLDS_MS:
        raise KeyError(f"no threshold is declared for {operation!r} in THRESHOLDS_MS")

    def decorate(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def run(*args, **kwargs):
            subject = detail(args, kwargs) if detail is not None else ""
            label = f"{fn.__name__} {subject}" if subject else fn.__name__
            with Timer(operation, label) as timer:
                result = await fn(*args, **kwargs)
                if failed is not None and failed(result):
                    timer.cancel()
                return result
        return run
    return decorate
