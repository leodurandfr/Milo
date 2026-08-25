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
import logging

import pytest
from unittest.mock import Mock

from backend.core.log_handler import WebSocketLogHandler


def _record(message="disaster"):
    return logging.LogRecord("test", logging.ERROR, __file__, 1, message, None, None)


@pytest.fixture
def handler():
    h = WebSocketLogHandler()
    h._bg = Mock()
    return h


class TestEmit:

    def test_nothing_is_broadcast_before_the_state_machine_is_injected(self, handler):
        """`set_state_machine` runs after service init; emit() can fire before."""
        handler.emit(_record())
        handler._bg.spawn.assert_not_called()

    def test_an_error_is_handed_to_the_background_set_once_injected(self, handler):
        handler.set_state_machine(Mock())
        handler.emit(_record())
        assert handler._bg.spawn.call_count == 1

    def test_a_second_error_inside_the_interval_is_dropped(self, handler):
        """A failing loop logs every tick; the socket must not carry each one."""
        handler.set_state_machine(Mock())
        handler.emit(_record("first"))
        handler.emit(_record("second"))
        assert handler._bg.spawn.call_count == 1

    def test_an_error_after_the_interval_is_broadcast_again(self, handler, monkeypatch):
        """The limit is a throttle, not a latch — the next failure must show."""
        handler.set_state_machine(Mock())
        # Not 0.0: `_last_broadcast_time` starts at 0, so a clock reading 0.0
        # would throttle the very first emit and prove nothing.
        start = 100.0
        clock = iter([start, start + WebSocketLogHandler.MIN_BROADCAST_INTERVAL + 0.1])
        monkeypatch.setattr("backend.core.log_handler.time.monotonic", lambda: next(clock))
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

        await handler._broadcast(_record("the disc drive is on fire"))

        assert state_machine.seen.message == "the disc drive is on fire"

    async def test_a_broadcast_that_fails_is_swallowed(self, handler):
        """Letting it propagate re-enters the WebSocket layer and loops."""
        state_machine = Mock()

        async def boom(event):
            raise RuntimeError("socket gone")
        state_machine.broadcast = boom
        handler.set_state_machine(state_machine)

        await handler._broadcast(_record())
