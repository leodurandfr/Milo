# backend/tests/test_background.py
"""Unit tests for BackgroundTaskSet."""
import asyncio
import logging

import pytest

from backend.shared.background import BackgroundTaskSet


@pytest.fixture
def logger():
    return logging.getLogger("test.background")


@pytest.mark.asyncio
async def test_spawn_schedules_coroutine(logger):
    bg = BackgroundTaskSet(logger, "test")
    ran = asyncio.Event()

    async def coro():
        ran.set()

    task = bg.spawn(coro(), label="schedule")
    assert task is not None
    assert task.get_name() == "test.schedule"
    assert task in bg._tasks  # strong-ref added synchronously
    await asyncio.wait_for(ran.wait(), timeout=1.0)
    await asyncio.sleep(0)  # let done_callback run


@pytest.mark.asyncio
async def test_spawn_survives_caller_dropping_ref(logger):
    """The set must hold a strong-ref so an unawaited task isn't GC'd."""
    import gc

    bg = BackgroundTaskSet(logger, "test")
    ran = asyncio.Event()

    async def coro():
        await asyncio.sleep(0.01)
        ran.set()

    bg.spawn(coro(), label="orphan")  # caller drops the returned task ref
    gc.collect()
    await asyncio.wait_for(ran.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_exception_logged_with_exc_info(logger, caplog):
    bg = BackgroundTaskSet(logger, "test")

    async def coro():
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="test.background"):
        bg.spawn(coro(), label="fail")
        await asyncio.sleep(0.01)

    matches = [r for r in caplog.records if "BG task 'test.fail' failed" in r.getMessage()]
    assert len(matches) == 1
    assert matches[0].exc_info is not None
    assert isinstance(matches[0].exc_info[1], RuntimeError)


@pytest.mark.asyncio
async def test_cancelled_error_not_logged(logger, caplog):
    bg = BackgroundTaskSet(logger, "test")

    async def coro():
        await asyncio.sleep(10)

    with caplog.at_level(logging.ERROR, logger="test.background"):
        task = bg.spawn(coro(), label="long")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert not any("failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_completed_task_removed_from_set(logger):
    bg = BackgroundTaskSet(logger, "test")

    async def coro():
        pass

    bg.spawn(coro(), label="done")
    await asyncio.sleep(0.01)
    assert len(bg._tasks) == 0


def test_spawn_outside_event_loop(logger, caplog):
    bg = BackgroundTaskSet(logger, "test")
    closed = False

    async def coro():
        nonlocal closed
        closed = True

    c = coro()
    with caplog.at_level(logging.DEBUG, logger="test.background"):
        result = bg.spawn(c, label="no_loop")

    assert result is None
    assert any(
        "skipped: no running event loop" in r.getMessage() for r in caplog.records
    )
    # coroutine was closed (never awaited) → no RuntimeWarning expected


@pytest.mark.asyncio
async def test_cancel_all_drains_tasks(logger):
    bg = BackgroundTaskSet(logger, "test")

    async def coro():
        await asyncio.sleep(10)

    for i in range(3):
        bg.spawn(coro(), label=f"task_{i}")

    assert len(bg._tasks) == 3
    await bg.cancel_all()
    assert len(bg._tasks) == 0


@pytest.mark.asyncio
async def test_cancel_all_idempotent(logger):
    bg = BackgroundTaskSet(logger, "test")

    async def coro():
        await asyncio.sleep(10)

    bg.spawn(coro(), label="t1")
    await bg.cancel_all()
    await bg.cancel_all()  # second call must not raise
    assert len(bg._tasks) == 0


@pytest.mark.asyncio
async def test_cancel_all_on_empty_set(logger):
    bg = BackgroundTaskSet(logger, "test")
    await bg.cancel_all()  # no tasks → no-op, no crash
    assert len(bg._tasks) == 0
