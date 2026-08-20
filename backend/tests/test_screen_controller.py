# backend/tests/test_screen_controller.py
"""
Tests for ScreenController's one privileged effect: driving the backlight.

The command is a shell write to a hidraw device (7" USB) or to a sysfs
backlight file (8" DSI). Both fail the same way — non-zero exit, message on
stderr — and neither was consulted, so a panel that took nothing reported the
same success as one that took everything.
"""
import asyncio
from time import monotonic
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


class TestSleepStateFollowsThePanel:
    """The four callers that used to broadcast a literal after a dropped verdict.

    `_screen_cmd` already reports its own refusal at error level, so the banner
    fires — what was wrong is what happened next: the UI was told the panel had
    entered a state it had just refused, and the kiosk then renders a sleeping
    screen over a lit one (or the reverse) until something else moves.
    """

    @pytest.fixture
    def broadcasts(self, controller):
        controller._broadcast_sleep_state = AsyncMock()
        return controller._broadcast_sleep_state

    async def test_a_refused_wake_is_not_announced_as_awake(self, controller, broadcasts):
        controller.screen_on = False
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)):
            await controller.on_touch_detected()
        broadcasts.assert_not_called()

    async def test_a_successful_wake_is_announced(self, controller, broadcasts):
        controller.screen_on = False
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc()):
            await controller.on_touch_detected()
        broadcasts.assert_awaited_once_with(False)

    async def test_a_refused_sleep_is_not_announced_as_asleep(self, controller, broadcasts):
        controller.screen_on = True
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)):
            await controller.force_sleep()
        broadcasts.assert_not_called()

    async def test_a_successful_sleep_is_announced(self, controller, broadcasts):
        controller.screen_on = True
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc()):
            await controller.force_sleep()
        broadcasts.assert_awaited_once_with(True)

    async def test_the_inactivity_timeout_does_not_announce_a_refused_sleep(
        self, controller, broadcasts
    ):
        """One pass of the timeout loop, driven to the should_turn_off branch."""
        controller.screen_on = True
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_time = None
        controller.last_activity_time = monotonic() - 60
        controller.current_source_state = "ready"

        async def stop_after_first_pass(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)), \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop_after_first_pass)):
            await controller._monitor_timeout()

        broadcasts.assert_not_called()

    async def test_the_source_monitor_does_not_announce_a_refused_wake(
        self, controller, broadcasts
    ):
        """A source going active wakes the panel; a refused wake stays unannounced."""
        controller.screen_on = False
        controller.running = True
        controller.current_source_state = "ready"
        controller.state_machine.get_current_state = Mock(
            return_value={"source_state": "active"}
        )

        async def stop_after_first_pass(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)), \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop_after_first_pass)):
            await controller._monitor_source_state()

        broadcasts.assert_not_called()


class TestInitializeReportsThePanel:
    """`initialize` used to answer True over a dropped `_screen_cmd`.

    It is the shape rule 3 of test_silent_failure.py forbids — a sealed method
    manufacturing a verdict over a sealed sibling's discarded one — and its
    EXEMPT_MANUFACTURED entry was deleted with this fix.
    """

    async def test_a_refused_boot_backlight_is_reported(self, controller):
        controller.settings_service.invalidate_cache = Mock()
        controller.settings_service.load_settings = AsyncMock(
            return_value={"screen": controller.settings_service.defaults["screen"]}
        )
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)):
            assert await controller.initialize() is False

        assert controller.running is True, "monitoring must start whatever the panel did"
        await controller.cleanup()

    async def test_a_screenless_unit_still_initializes_cleanly(self, controller):
        """False must mean "a panel refused", never "this unit has no screen"."""
        controller.screen_type = "none"
        controller.settings_service.invalidate_cache = Mock()
        controller.settings_service.load_settings = AsyncMock(
            return_value={"screen": controller.settings_service.defaults["screen"]}
        )
        assert await controller.initialize() is True
        await controller.cleanup()
