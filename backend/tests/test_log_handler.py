# backend/tests/test_log_handler.py
"""`WebSocketLogHandler.emit` — the backend-error banner of the UI.

Green in the Lot A eviscration sweep: replaced by `return None` the whole suite
stayed green, and with it every ERROR the backend logs stops reaching the
screen. It is the only channel that surfaces a backend failure to someone
standing in front of the appliance -- CLAUDE.md's route doctrine is written
around it, which is why an expected 404 on missing artwork must be logged at
debug rather than error.

Three behaviours, all of them the reason the method is not a one-liner: the
guard before the state machine is injected, the rate limit that keeps a failing
loop from flooding the socket, and the hand-off to BackgroundTaskSet -- emit()
is called synchronously, from uvicorn's thread pool among others.
"""
import asyncio
import logging

import pytest
from unittest.mock import Mock

from backend.core.log_handler import WebSocketLogHandler
from backend.tests.conftest import closing_spawn


def _record(message="disaster"):
    return logging.LogRecord("test", logging.ERROR, __file__, 1, message, None, None)


@pytest.fixture
def handler():
    h = WebSocketLogHandler()
    h._bg = Mock(spawn=closing_spawn())
    return h


class TestEmit:
    """Async on purpose: emit() runs on the event loop, as it does in the backend."""

    async def test_nothing_is_broadcast_before_the_state_machine_is_injected(self, handler):
        """`set_state_machine` runs after service init; emit() can fire before."""
        handler.emit(_record())
        handler._bg.spawn.assert_not_called()

    async def test_an_error_is_handed_to_the_background_set_once_injected(self, handler):
        handler.set_state_machine(Mock())
        handler.emit(_record())
        assert handler._bg.spawn.call_count == 1

    async def test_a_second_error_inside_the_interval_is_dropped(self, handler):
        """A failing loop logs every tick; the socket must not carry each one."""
        handler.set_state_machine(Mock())
        handler.emit(_record("first"))
        handler.emit(_record("second"))
        assert handler._bg.spawn.call_count == 1

    async def test_an_error_after_the_interval_is_broadcast_again(self, handler, monkeypatch):
        """The limit is a throttle, not a latch — the next failure must show."""
        handler.set_state_machine(Mock())
        # Not 0.0: `_last_broadcast_time` starts at 0, so a clock reading 0.0
        # would throttle the very first emit and prove nothing.
        start = 100.0
        clock = iter([start, start + WebSocketLogHandler.MIN_BROADCAST_INTERVAL + 0.1])
        monkeypatch.setattr("backend.core.log_handler.monotonic", lambda: next(clock))
        handler.emit(_record("first"))
        handler.emit(_record("second"))
        assert handler._bg.spawn.call_count == 2


class TestBroadcast:

    async def test_the_broadcast_carries_the_logged_message(self, handler):
        state_machine = Mock()
        state_machine.broadcast = Mock(return_value=None)

        async def broadcast(event):
            state_machine.seen = event
        state_machine.broadcast = broadcast
        handler.set_state_machine(state_machine)

        await handler._broadcast("the disc drive is on fire")

        assert state_machine.seen.message == "the disc drive is on fire"

    async def test_a_broadcast_that_fails_is_swallowed(self, handler):
        """Letting it propagate re-enters the WebSocket layer and loops."""
        state_machine = Mock()

        async def boom(event):
            raise RuntimeError("socket gone")
        state_machine.broadcast = boom
        handler.set_state_machine(state_machine)

        await handler._broadcast("disaster")


class TestFromAnotherThread:
    """An ERROR logged off the event loop — `asyncio.to_thread`, a GPIO callback —
    must still reach the banner. The broadcast is a coroutine, and a thread has
    no loop to run it on: it has to be handed to the one the backend serves on."""

    async def test_an_error_logged_from_a_thread_is_broadcast(self):
        seen = []

        class StateMachine:
            async def broadcast(self, event):
                seen.append(event.message)

        handler = WebSocketLogHandler()
        handler.set_state_machine(StateMachine())
        try:
            await asyncio.to_thread(handler.emit, _record("the fan controller died"))
            async with asyncio.timeout(2):
                while not seen:
                    await asyncio.sleep(0.01)
        finally:
            await handler._bg.cancel_all()

        assert seen == ["the fan controller died"]

    async def test_an_error_logged_from_another_threads_loop_reaches_the_backends(self):
        """A library worker running its own loop (zeroconf, dbus) has a running
        loop too — just not the one the WebSocket clients live on."""
        seen = []
        backend_loop = asyncio.get_running_loop()

        class StateMachine:
            async def broadcast(self, event):
                seen.append((event.message, asyncio.get_running_loop() is backend_loop))

        handler = WebSocketLogHandler()
        handler.set_state_machine(StateMachine())

        async def _worker():
            handler.emit(_record("the mDNS responder died"))
            await asyncio.sleep(0.05)  # give a mis-scheduled broadcast the chance to run here

        try:
            await asyncio.to_thread(asyncio.run, _worker())
            async with asyncio.timeout(2):
                while not seen:
                    await asyncio.sleep(0.01)
        finally:
            await handler._bg.cancel_all()

        assert seen == [("the mDNS responder died", True)]

    async def test_the_message_is_the_one_logged_not_a_later_mutation(self):
        """The banner must say what was true when the error was logged."""
        seen = []

        class StateMachine:
            async def broadcast(self, event):
                seen.append(event.message)

        handler = WebSocketLogHandler()
        handler.set_state_machine(StateMachine())
        state = ["drive empty"]
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "%s", (state,), None)

        def _log_then_mutate():
            handler.emit(record)
            state[0] = "drive full"

        try:
            await asyncio.to_thread(_log_then_mutate)
            async with asyncio.timeout(2):
                while not seen:
                    await asyncio.sleep(0.01)
        finally:
            await handler._bg.cancel_all()

        assert seen == ["['drive empty']"]
