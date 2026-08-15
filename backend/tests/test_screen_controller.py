# backend/tests/test_screen_controller.py
"""
Tests for ScreenController's one privileged effect: driving the backlight.

The command is a shell write to a hidraw device (7" USB) or to a sysfs
backlight file (8" DSI). Both fail the same way — non-zero exit, message on
stderr — and neither was consulted, so a panel that took nothing reported the
same success as one that took everything.
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.core.settings import SettingsService
from backend.hardware.screen import ScreenController


def _make_proc(returncode=0, stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.returncode = returncode
    proc.kill = Mock()
    return proc


@pytest.fixture
def controller():
    """A controller for the 7" USB panel — the variant with a real command."""
    hardware = Mock()
    hardware.get_screen_type = Mock(return_value="waveshare_7_usb")

    settings = Mock()
    settings.defaults = SettingsService().defaults

    return ScreenController(
        state_machine=Mock(),
        settings_service=settings,
        hardware_service=hardware,
    )


class TestScreenCommandResult:
    """What `_screen_cmd` answers, and what it is allowed to write."""

    async def test_a_successful_command_reports_the_screen_on(self, controller):
        proc = _make_proc()
        controller.screen_on = False

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            assert await controller._screen_cmd(controller.screen_on_cmd) is True

        assert controller.screen_on is True

    async def test_a_refused_command_reports_failure_and_writes_nothing(self, controller, caplog):
        """The realistic trigger is a replaced panel whose udev rule was never
        replayed: the write is refused, and screen_on used to be set anyway —
        so the controller believed the panel was lit and the sleep logic ran
        against a screen that was never on."""
        proc = _make_proc(returncode=1, stderr=b"Permission denied")
        controller.screen_on = False

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            assert await controller._screen_cmd(controller.screen_on_cmd) is False

        assert controller.screen_on is False
        assert "Permission denied" in caplog.text

    async def test_a_hung_command_reports_failure(self, controller):
        proc = _make_proc()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        controller.screen_on = False

        with patch("asyncio.create_subprocess_shell", return_value=proc):
            assert await controller._screen_cmd(controller.screen_on_cmd) is False

        assert controller.screen_on is False
        proc.kill.assert_called_once()

    async def test_no_screen_is_not_a_failure(self, controller):
        """A unit with no panel must not report every brightness apply as broken."""
        controller.screen_type = "none"
        assert await controller._screen_cmd("anything") is True


class TestApplyScreenConfig:
    """The public entry point the brightness-apply route answers from."""

    async def test_it_reports_what_the_panel_did(self, controller):
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)):
            assert await controller.apply_screen_config(8) is False

        with patch("asyncio.create_subprocess_shell", return_value=_make_proc()):
            assert await controller.apply_screen_config(8) is True

    async def test_the_command_carries_the_requested_brightness(self, controller):
        """The route's answer would be worth nothing if the value never reached
        the command — 8 on the 7" panel is passed through as-is."""
        proc = _make_proc()
        with patch("asyncio.create_subprocess_shell", return_value=proc) as shell:
            await controller.apply_screen_config(8)

        assert "-b 8" in shell.call_args.args[0]
