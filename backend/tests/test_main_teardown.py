"""
Tests for the lifespan shutdown teardown loop.

What breaks when these fail: the whole shutdown used to be eighteen awaits
under one try/except, so the first cleanup to raise or block denied every
later one its own — including the only flush of pending volume state and of
pending multiroom state, both of which sit behind CamillaDSP's cleanup.
"""
import asyncio

from backend.main import run_teardown


async def test_a_raising_entry_does_not_deny_the_later_ones():
    ran = []

    async def ok(name):
        ran.append(name)

    async def boom():
        raise RuntimeError("daemon already gone")

    await run_teardown([
        ("first", lambda: ok("first")),
        ("raiser", boom),
        ("flush", lambda: ok("flush")),
    ])

    assert ran == ["first", "flush"]


async def test_a_blocking_entry_is_bounded_and_the_flush_still_runs():
    """
    The entry most likely to block is CamillaDSP's, and the two flushes used to
    sit behind it. A hang must cost its bound, not the flush.
    """
    ran = []

    async def hang():
        await asyncio.Event().wait()

    async def flush():
        ran.append("flush")

    await run_teardown([("hanger", hang), ("flush", flush)], timeout=0.05)

    assert ran == ["flush"]


async def test_every_entry_is_awaited_exactly_once():
    calls = []

    async def entry(name):
        calls.append(name)

    names = ["a", "b", "c"]
    await run_teardown([(n, (lambda n=n: entry(n))) for n in names])

    assert calls == names
