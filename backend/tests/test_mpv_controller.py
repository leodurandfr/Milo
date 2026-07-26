# backend/tests/test_mpv_controller.py
"""
Unit tests for MpvController.connect()'s wall-clock budget.

connect() runs inside _do_start for the four mpv sources, which itself runs
under AudioStateMachine.TRANSITION_TIMEOUT. It used to retry a fixed number of
times, so a socket that existed but never answered cost max_retries × the full
command deadline (~55s) under a 10s caller budget — the transition timed out
and the whole source switch was reset. These are the guards that the budget is
now time-bounded, not attempt-bounded.
"""
import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.shared.mpv import MpvController


@pytest.fixture
def controller():
    return MpvController(ipc_socket_path="/nonexistent/milo-test-ipc.sock")


class TestConnectBudget:
    """connect() must give up inside its timeout, whatever mpv is doing."""

    @pytest.mark.asyncio
    async def test_missing_socket_gives_up_within_timeout(self, controller):
        """Socket never appears: retries, then gives up inside the budget."""
        started = time.monotonic()
        result = await controller.connect(timeout=2.0, retry_delay=0.1)
        elapsed = time.monotonic() - started

        assert result is False
        assert elapsed < 2.0

    @pytest.mark.asyncio
    async def test_unresponsive_mpv_gives_up_within_timeout(self, controller):
        """Socket accepts the connection but mpv never answers the probe.

        The regression case. Each probe is made expensive (a wedged mpv burns
        its whole reply deadline), which is what made the old attempt counter
        unbounded: 10 attempts × the full deadline, no matter the caller's
        budget. A time-bounded loop stops at the deadline instead.
        """
        probe_cost = 0.4

        async def wedged_probe(*args, **kwargs):
            await asyncio.sleep(probe_cost)
            return None

        with patch("backend.shared.mpv.Path") as mock_path, \
             patch("asyncio.open_unix_connection", new_callable=AsyncMock) as mock_open, \
             patch.object(MpvController, "_send_command", side_effect=wedged_probe) as mock_cmd:
            mock_path.return_value.exists.return_value = True
            mock_open.return_value = (Mock(), Mock())

            started = time.monotonic()
            # Budget must exceed retry_delay + PROBE_TIMEOUT, or the loop
            # correctly refuses to start a probe it cannot afford to finish.
            result = await controller.connect(timeout=2.0, retry_delay=0.1)
            elapsed = time.monotonic() - started

        assert result is False
        # An attempt-counter implementation would have run 10 × probe_cost = 4s
        # here regardless of the 2s budget.
        assert elapsed < 2.0
        assert mock_cmd.await_count > 1

    @pytest.mark.asyncio
    async def test_probe_uses_short_timeout(self, controller):
        """The liveness probe must not inherit the full command deadline."""
        from backend.shared.mpv import COMMAND_TIMEOUT, PROBE_TIMEOUT

        assert PROBE_TIMEOUT < COMMAND_TIMEOUT

        with patch("backend.shared.mpv.Path") as mock_path, \
             patch("asyncio.open_unix_connection", new_callable=AsyncMock) as mock_open, \
             patch.object(MpvController, "_send_command", new_callable=AsyncMock) as mock_cmd:
            mock_path.return_value.exists.return_value = True
            mock_open.return_value = (Mock(), Mock())
            mock_cmd.return_value = {"error": "success", "data": False}

            assert await controller.connect(timeout=2.0, retry_delay=0.1) is True

        probe_call = mock_cmd.await_args_list[0]
        assert probe_call.args == ("get_property", "idle-active")
        assert probe_call.kwargs["timeout"] == PROBE_TIMEOUT

    @pytest.mark.asyncio
    async def test_default_budget_fits_a_source_start(self):
        """connect()'s default must leave room under TRANSITION_TIMEOUT."""
        from backend.core.state import AudioStateMachine
        from backend.shared.mpv import CONNECT_TIMEOUT, PROBE_TIMEOUT

        # _start_service_and_wait settles for 0.5s before connect() is called.
        assert CONNECT_TIMEOUT + PROBE_TIMEOUT + 0.5 < AudioStateMachine.TRANSITION_TIMEOUT
