"""`shared/timing.py` — one WARNING past a measured threshold, nothing below it.

What breaks when these fail: a slow transition, a slow first sound or a slow
admission that writes nothing is a complaint ("it took ages") nobody can find
in the journal; one that writes on every fast run, or a WARNING on an operation
nobody measured, is noise that teaches the owner to skip the line. The clock is
replaced (`timing.monotonic`) — no wall-clock budget here.
"""
import logging

import pytest

from backend.core.models.audio_state import AudioSource
from backend.shared import timing

# One operation of each kind, whichever the table holds today: measuring one
# more is an edit of the table, never of these tests.
MEASURED = next((op for op, ms in timing.THRESHOLDS_MS.items() if ms is not None), None)
TO_MEASURE = next((op for op, ms in timing.THRESHOLDS_MS.items() if ms is None), None)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(timing, "monotonic", fake)
    return fake


def _records(caplog, level):
    return [r for r in caplog.records if r.name == "backend.shared.timing" and r.levelno == level]


def test_the_table_holds_both_kinds():
    """The tests below are not run against an empty kind (until the day
    every operation is measured, when the to-measure tests are moot)."""
    assert MEASURED is not None and TO_MEASURE is not None


def test_every_source_declares_how_long_its_first_sound_may_take():
    """`open_session` times every LOADING session under its source's own
    operation, and an undeclared one is refused (KeyError) — a source missing
    here would fail the session it opens."""
    declared = {
        op.removeprefix("source.until_playing.")
        for op in timing.THRESHOLDS_MS if op.startswith("source.until_playing.")
    }
    assert declared == {s.value for s in AudioSource if s is not AudioSource.NONE}


def test_a_slow_operation_writes_one_warning_with_its_duration_and_threshold(clock, caplog):
    threshold = timing.THRESHOLDS_MS[MEASURED]
    caplog.set_level(logging.DEBUG, logger="backend.shared.timing")

    timer = timing.start(MEASURED, "bluetooth -> tidal")
    clock.now += (threshold + 250) / 1000
    assert timer.stop() == threshold + 250
    # The second end of the same operation (a stop after a stop) says nothing.
    assert timer.stop() is None

    warnings = _records(caplog, logging.WARNING)
    assert [r.getMessage() for r in warnings] == [
        f"slow: {MEASURED} took {threshold + 250} ms (threshold {threshold} ms): bluetooth -> tidal"
    ]
    assert not _records(caplog, logging.ERROR)


async def test_a_fast_operation_writes_nothing(clock, caplog):
    threshold = timing.THRESHOLDS_MS[MEASURED]
    caplog.set_level(logging.DEBUG, logger="backend.shared.timing")

    async with timing.timed(MEASURED):
        clock.now += (threshold - 1) / 1000
    with timing.timed(MEASURED):
        clock.now += threshold / 1000

    assert not [r for r in caplog.records if r.name == "backend.shared.timing"]


async def test_an_operation_to_measure_logs_its_duration_but_never_warns(clock, caplog):
    caplog.set_level(logging.DEBUG, logger="backend.shared.timing")

    @timing.measured(TO_MEASURE)
    async def browse():
        clock.now += 60.0

    await browse()

    assert not _records(caplog, logging.WARNING)
    infos = [r.getMessage() for r in _records(caplog, logging.INFO)]
    assert infos == [f"timed: {TO_MEASURE} took 60000 ms (threshold to measure): browse"]


async def test_a_step_that_reports_its_failure_by_its_result_writes_nothing(clock, caplog):
    """An update step returns `{"success": False}` rather than raising: its
    duration is not a sample, or the threshold read from them is a guess."""
    caplog.set_level(logging.DEBUG, logger="backend.shared.timing")

    @timing.measured(
        TO_MEASURE,
        detail=lambda args, kwargs: args[0]["service_name"],
        failed=lambda result: result.get("success") is False,
    )
    async def install(config, ok):
        clock.now += 2.0
        return {"success": ok}

    await install({"service_name": "milo-spotify.service"}, False)
    await install({"service_name": "milo-spotify.service"}, True)

    infos = [r.getMessage() for r in _records(caplog, logging.INFO)]
    assert infos == [
        f"timed: {TO_MEASURE} took 2000 ms (threshold to measure): install milo-spotify.service"
    ]


def test_an_operation_that_failed_or_was_dropped_writes_nothing(clock, caplog):
    threshold = timing.THRESHOLDS_MS[MEASURED]
    caplog.set_level(logging.DEBUG, logger="backend.shared.timing")

    with pytest.raises(RuntimeError):
        with timing.timed(MEASURED):
            clock.now += (threshold * 10) / 1000
            raise RuntimeError("transition failed")
    dropped = timing.start(MEASURED)
    clock.now += (threshold * 10) / 1000
    dropped.cancel()
    assert dropped.stop() is None

    assert not [r for r in caplog.records if r.name == "backend.shared.timing"]


def test_an_operation_with_no_declared_threshold_is_refused():
    """A typo or an operation placed without its measurement is caught at
    once, never timed against a default."""
    with pytest.raises(KeyError):
        timing.start("source.transitoin")
    with pytest.raises(KeyError):
        timing.measured("update.unknown_step")
