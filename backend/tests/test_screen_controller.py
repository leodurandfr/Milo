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

    async def test_an_already_dark_panel_is_not_told_to_sleep_again(
        self, controller, broadcasts
    ):
        """`force_sleep` is the IR MENU long-press. Held twice it would spawn a
        second shell and announce a transition that did not happen."""
        controller.screen_on = False
        with patch("asyncio.create_subprocess_shell", return_value=_make_proc()) as shell:
            await controller.force_sleep()
        shell.assert_not_called()
        broadcasts.assert_not_called()

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


class TestReloadingTheConfigAtRuntime:
    """`reload_timeout_config` — the whole body was at 0 %.

    It is the `reload_callback` of both `PUT /api/settings/screen-timeout` and
    `PUT /api/settings/screen-brightness`, and `_handle_setting_update` reads
    its verdict. Without it the two sliders in Réglages write settings.json and
    the running controller keeps the values it booted on until milo-backend
    restarts — the change appears to have been accepted and does nothing.
    """

    def _stored(self, controller, **overrides):
        section = {**controller.settings_service.defaults["screen"], **overrides}
        controller.settings_service.invalidate_cache = Mock()
        controller.settings_service.load_settings = AsyncMock(
            return_value={"screen": section}
        )
        return section

    async def test_the_new_timeout_and_brightness_are_adopted(self, controller):
        self._stored(controller, timeout_seconds=45, brightness_on=9)

        assert await controller.reload_timeout_config() is True

        assert controller.timeout_seconds == 45
        assert controller.brightness_on == 9

    async def test_the_backlight_command_is_rebuilt_with_the_new_brightness(
        self, controller
    ):
        """The command string is precomputed. Adopting the number without
        regenerating it leaves every later wake writing the old duty."""
        self._stored(controller, brightness_on=9)

        await controller.reload_timeout_config()

        assert "-b 9" in controller.screen_on_cmd

    async def test_the_inactivity_timer_restarts_from_the_reload(self, controller):
        """A shortened timeout must not put the screen to sleep for time that
        elapsed under the old one — the user just touched Réglages."""
        controller.last_activity_time = monotonic() - 600
        self._stored(controller, timeout_seconds=30)

        await controller.reload_timeout_config()

        assert monotonic() - controller.last_activity_time < 1

    async def test_the_file_is_re_read_rather_than_the_cache(self, controller):
        """The route persists through SettingsService and then calls back; a
        cached read here answers with the value from before the write."""
        self._stored(controller, timeout_seconds=45)

        await controller.reload_timeout_config()

        controller.settings_service.invalidate_cache.assert_called_once_with()

    async def test_a_settings_file_that_will_not_load_falls_back_to_the_defaults(
        self, controller, caplog
    ):
        """`_validate_and_merge` guarantees the section, so reaching the except
        arm means settings.json is broken. Keeping whatever half-applied values
        were in flight is worse than the declared defaults — and those are read
        from the one declaration, never restated here."""
        controller.settings_service.invalidate_cache = Mock()
        controller.settings_service.load_settings = AsyncMock(
            side_effect=ValueError("settings.json is not JSON")
        )
        controller.timeout_seconds = 7
        controller.brightness_on = 1

        assert await controller.reload_timeout_config() is True

        defaults = controller.settings_service.defaults["screen"]
        assert controller.timeout_seconds == defaults["timeout_seconds"]
        assert controller.brightness_on == defaults["brightness_on"]
        assert f"-b {defaults['brightness_on']}" in controller.screen_on_cmd
        assert "Error loading screen config" in caplog.text


class TestTheBootGracePeriod:
    """The grace window, entirely at 0 %.

    The kiosk takes a while to paint after boot, and the inactivity clock
    starts at zero. Without the window a unit configured with a short timeout
    blanks its screen during startup and the owner sees a dark panel on a
    machine that just booted.
    """

    async def _one_pass(self, controller):
        async def stop(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell", return_value=_make_proc()) as shell, \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop)):
            await controller._monitor_timeout()
        return shell

    async def test_the_screen_is_not_blanked_during_the_window(self, controller):
        controller.screen_on = True
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_grace_period = 30
        controller.boot_time = monotonic()          # just booted
        controller.last_activity_time = monotonic() - 600
        controller.current_source_state = "ready"

        shell = await self._one_pass(controller)

        shell.assert_not_called()

    async def test_the_screen_blanks_once_the_window_has_passed(self, controller):
        controller.screen_on = True
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_grace_period = 30
        controller.boot_time = monotonic() - 600    # long past the window
        controller.last_activity_time = monotonic() - 600
        controller.current_source_state = "ready"

        shell = await self._one_pass(controller)

        assert shell.call_args.args[0] == controller.screen_off_cmd

    async def test_the_window_is_at_least_thirty_seconds_and_never_shorter_than_the_timeout(
        self, controller
    ):
        """`initialize` derives it; a window shorter than the timeout would let
        the screen blank inside its own grace period."""
        for configured, expected in ((0, 30), (10, 30), (300, 300)):
            controller.settings_service.invalidate_cache = Mock()
            controller.settings_service.load_settings = AsyncMock(return_value={
                "screen": {**controller.settings_service.defaults["screen"],
                           "timeout_seconds": configured}
            })
            with patch("asyncio.create_subprocess_shell", return_value=_make_proc()):
                await controller.initialize()
            assert controller.boot_grace_period == expected
            await controller.cleanup()


class TestTheInactivityTimeoutItself:
    async def _one_pass(self, controller, returncode=0):
        async def stop(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell",
                   return_value=_make_proc(returncode=returncode)) as shell, \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop)):
            await controller._monitor_timeout()
        return shell

    def _idle_for_ages(self, controller, **overrides):
        controller.screen_on = True
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_time = None
        controller.last_activity_time = monotonic() - 600
        controller.current_source_state = "ready"
        for key, value in overrides.items():
            setattr(controller, key, value)

    async def test_a_timeout_of_zero_means_never(self, controller):
        """0 is what the "screen always on" switch writes. Reading it as "blank
        immediately" would turn that switch into its own opposite."""
        self._idle_for_ages(controller, timeout_seconds=0)

        shell = await self._one_pass(controller)

        shell.assert_not_called()

    async def test_a_playing_source_holds_the_timer_open(self, controller):
        """The now-playing screen must stay lit while music plays, however long
        nobody touches the panel."""
        self._idle_for_ages(controller, current_source_state="active")

        shell = await self._one_pass(controller)

        shell.assert_not_called()
        assert monotonic() - controller.last_activity_time < 1

    async def test_a_successful_sleep_is_announced_to_the_ui(self, controller):
        controller._broadcast_sleep_state = AsyncMock()
        self._idle_for_ages(controller)

        await self._one_pass(controller)

        controller._broadcast_sleep_state.assert_awaited_once_with(True)

    async def test_an_already_dark_screen_is_not_blanked_again(self, controller):
        """Rewriting the off command every second would spawn a shell per tick
        for the life of the unit."""
        self._idle_for_ages(controller, screen_on=False)

        shell = await self._one_pass(controller)

        shell.assert_not_called()

    async def test_one_failing_pass_does_not_end_the_watch(self, controller, caplog):
        """With the task gone the screen never sleeps again, and nothing else
        would say so. The clock is the outermost thing this loop reads, so it
        is what the failure is injected through."""
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_time = None
        controller.screen_on = False
        controller.current_source_state = "ready"
        passes = []

        async def stop(_delay):
            passes.append(_delay)
            if len(passes) >= 2:
                controller.running = False

        clock = Mock(side_effect=[RuntimeError("clock went backwards")] + [1000.0] * 10)
        with patch("backend.hardware.screen.monotonic", clock), \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop)):
            await controller._monitor_timeout()

        assert len(passes) == 2, "the loop kept going after the failing pass"
        assert passes[0] == 10, "a failing pass backs off before retrying"
        assert "Timeout monitoring error" in caplog.text


class TestTheSourceStateWatch:
    async def _one_pass(self, controller, returncode=0):
        async def stop(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell",
                   return_value=_make_proc(returncode=returncode)) as shell, \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop)):
            await controller._monitor_source_state()
        return shell

    async def test_a_source_going_active_wakes_a_sleeping_panel(self, controller):
        """Starting playback from the phone must light the kiosk; this is the
        only path that does it without a touch."""
        controller._broadcast_sleep_state = AsyncMock()
        controller.screen_on = False
        controller.running = True
        controller.current_source_state = "ready"
        controller.state_machine.get_current_state = Mock(
            return_value={"source_state": "active"}
        )

        shell = await self._one_pass(controller)

        assert shell.call_args.args[0] == controller.screen_on_cmd
        controller._broadcast_sleep_state.assert_awaited_once_with(False)

    async def test_a_panel_that_was_already_lit_is_not_announced_as_waking(
        self, controller
    ):
        controller._broadcast_sleep_state = AsyncMock()
        controller.screen_on = True
        controller.running = True
        controller.current_source_state = "ready"
        controller.state_machine.get_current_state = Mock(
            return_value={"source_state": "active"}
        )

        await self._one_pass(controller)

        controller._broadcast_sleep_state.assert_not_called()

    async def test_playback_stopping_restarts_the_inactivity_clock(self, controller):
        """Otherwise the screen blanks the instant the last track ends, having
        counted the whole album as inactivity."""
        controller.running = True
        controller.screen_on = True
        controller.current_source_state = "active"
        controller.last_activity_time = monotonic() - 600
        controller.state_machine.get_current_state = Mock(
            return_value={"source_state": "ready"}
        )

        shell = await self._one_pass(controller)

        assert monotonic() - controller.last_activity_time < 1
        shell.assert_not_called()

    async def test_one_failing_pass_does_not_end_the_watch(self, controller):
        controller.running = True
        passes = []

        async def stop(_delay):
            passes.append(1)
            if len(passes) >= 2:
                controller.running = False

        controller.state_machine.get_current_state = Mock(
            side_effect=[RuntimeError("state machine gone"), {"source_state": "ready"}]
        )
        with patch("asyncio.sleep", new=AsyncMock(side_effect=stop)):
            await controller._monitor_source_state()

        assert len(passes) == 2


class TestPanelsWithNoBacklightToDrive:
    """The DSI branch when `/sys/class/backlight` holds nothing.

    `_detect_backlight_path` globs the real sysfs on this host, so it is
    redirected at a tmp tree — and both arms must leave the controller inert
    rather than emitting a shell command with an empty path in it.
    """

    def _dsi(self, monkeypatch, backlight_root):
        hardware = Mock()
        hardware.get_screen_type = Mock(return_value="waveshare_8_dsi")
        settings = Mock()
        settings.defaults = SettingsService().defaults
        asked = []

        def redirected(root):
            asked.append(root)
            return backlight_root

        monkeypatch.setattr("backend.hardware.screen.Path", redirected)
        controller = ScreenController(Mock(), settings, hardware)
        assert asked == ["/sys/class/backlight"], (
            "the DSI backlight is enumerated from the kernel class directory; "
            f"this asked for {asked}"
        )
        return controller

    async def test_a_dsi_panel_with_no_backlight_device_drives_nothing(
        self, tmp_path, monkeypatch, caplog
    ):
        """`/bin/sh -c 'echo 5 > None'` would create a file called None in the
        working directory and report success."""
        (tmp_path / "empty").mkdir()
        controller = self._dsi(monkeypatch, tmp_path / "empty")

        assert controller.backlight_path is None
        assert controller.screen_on_cmd == ""
        assert controller.screen_off_cmd == ""
        assert "No backlight device found" in caplog.text
        assert await controller._screen_cmd(controller.screen_on_cmd) is True

    async def test_a_dsi_panel_writes_the_resolved_node(self, tmp_path, monkeypatch):
        node = tmp_path / "10-0045" / "brightness"
        node.parent.mkdir(parents=True)
        node.write_text("0")
        controller = self._dsi(monkeypatch, tmp_path)

        assert controller.backlight_path == str(node)
        assert str(node) in controller.screen_on_cmd
        assert controller.screen_off_cmd.endswith(f"echo 0 > {node}'")
