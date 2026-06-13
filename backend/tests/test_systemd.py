# backend/tests/test_systemd.py
"""
Tests for SystemdServiceManager — the single centralized privileged-exec path
(sudo systemctl) for service control + power actions (see invariant #1).
"""
import pytest
from unittest.mock import AsyncMock, patch

from backend.core.systemd import SystemdServiceManager


@pytest.fixture
def manager():
    return SystemdServiceManager()


def _make_mock_proc(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = AsyncMock()
    return proc


class TestPower:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["reboot", "poweroff"])
    async def test_power_success(self, manager, action):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            result = await manager.power(action)
        assert result is True
        exec_mock.assert_called_once()
        assert exec_mock.call_args.args[:3] == ("sudo", "systemctl", action)

    @pytest.mark.asyncio
    async def test_power_failure_is_loud(self, manager):
        proc = _make_mock_proc(returncode=1, stderr=b"not authorized")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(manager.logger, "error") as log_error:
                result = await manager.power("reboot")
        assert result is False
        log_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_power_invalid_action(self, manager):
        with pytest.raises(ValueError):
            await manager.power("halt")

    @pytest.mark.asyncio
    async def test_power_delay_flushes_response(self, manager):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
                await manager.power("reboot", delay=2.0)
        sleep_mock.assert_awaited_once_with(2.0)

    @pytest.mark.asyncio
    async def test_power_subprocess_error_is_loud(self, manager):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("boom")):
            with patch.object(manager.logger, "error") as log_error:
                result = await manager.power("poweroff")
        assert result is False
        log_error.assert_called_once()


class TestRestartSelf:
    @pytest.mark.asyncio
    async def test_restart_self_enqueues_with_no_block(self, manager):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            with patch.object(manager.logger, "error") as log_error:
                await manager.restart_self("milo-backend.service")
        args = exec_mock.call_args.args
        assert args == ("sudo", "systemctl", "restart", "--no-block", "milo-backend.service")
        log_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_self_enqueue_failure_is_loud(self, manager):
        proc = _make_mock_proc(returncode=1, stderr=b"unit not found")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(manager.logger, "error") as log_error:
                await manager.restart_self("milo-backend.service")
        log_error.assert_called_once()
