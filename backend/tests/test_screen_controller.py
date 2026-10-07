# backend/tests/test_screen_controller.py
"""
Tests for ScreenController's one privileged effect: driving the backlight.

The command is a shell write to a hidraw device (7" USB) or to a sysfs
backlight file (8" DSI). Both fail the same way — non-zero exit, message on
stderr — and neither was consulted, so a panel that took nothing reported the
same success as one that took everything.
"""
import asyncio
import json
import types
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest

from backend.core.models.audio_wire import SessionView, SourceView
from backend.core.settings import SettingsService
from backend.core.state import SystemAudioState
from backend.hardware.screen import ScreenController


def _make_proc(returncode=0, stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.returncode = returncode
    proc.kill = Mock()
    return proc


def _session_on_the_state(controller, live):
    """Put a live session (or none) on the state machine's record, where the
    screen reads it."""
    session = SessionView(
        id="s-1", phase="playing", title="T", artist=None, album=None,
        artwork=None, senders=[], duration_ms=None, position=None,
    ) if live else None
    controller.state_machine.system_state = SystemAudioState(view=SourceView(session=session))


@pytest.fixture
def controller():
    """A controller for the 7" USB panel — the variant with a real command."""
    hardware = Mock()
    hardware.get_screen_type = Mock(return_value="waveshare_7_usb")

    settings = Mock()
    settings.defaults = SettingsService().defaults

    # `broadcast` is awaited: a plain Mock makes every sleep-state announcement
    # die in `@handle_errors` after the screen command already went out, so the
    # assertions hold while the broadcast half never runs.
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()

    return ScreenController(
        state_machine=state_machine,
        settings_service=settings,
        hardware_service=hardware,
        systemd_manager=AsyncMock(),
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
        controller.session_live = False

        async def stop_after_first_pass(_delay):
            controller.running = False

        with patch("asyncio.create_subprocess_shell", return_value=_make_proc(returncode=1)), \
                patch("asyncio.sleep", new=AsyncMock(side_effect=stop_after_first_pass)):
            await controller._monitor_timeout()

        broadcasts.assert_not_called()

    async def test_the_source_monitor_does_not_announce_a_refused_wake(
        self, controller, broadcasts
    ):
        """A session opening wakes the panel; a refused wake stays unannounced."""
        controller.screen_on = False
        controller.running = True
        controller.session_live = False
        _session_on_the_state(controller, live=True)

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
        controller.session_live = False

        shell = await self._one_pass(controller)

        shell.assert_not_called()

    async def test_the_screen_blanks_once_the_window_has_passed(self, controller):
        controller.screen_on = True
        controller.running = True
        controller.timeout_seconds = 1
        controller.boot_grace_period = 30
        controller.boot_time = monotonic() - 600    # long past the window
        controller.last_activity_time = monotonic() - 600
        controller.session_live = False

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
        controller.session_live = False
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
        self._idle_for_ages(controller, session_live=True)

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
        controller.session_live = False
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

    async def test_a_session_opening_wakes_a_sleeping_panel(self, controller):
        """Starting playback from the phone must light the kiosk; this is the
        only path that does it without a touch."""
        controller._broadcast_sleep_state = AsyncMock()
        controller.screen_on = False
        controller.running = True
        controller.session_live = False
        _session_on_the_state(controller, live=True)

        shell = await self._one_pass(controller)

        assert shell.call_args.args[0] == controller.screen_on_cmd
        controller._broadcast_sleep_state.assert_awaited_once_with(False)

    async def test_a_panel_that_was_already_lit_is_not_announced_as_waking(
        self, controller
    ):
        controller._broadcast_sleep_state = AsyncMock()
        controller.screen_on = True
        controller.running = True
        controller.session_live = False
        _session_on_the_state(controller, live=True)

        await self._one_pass(controller)

        controller._broadcast_sleep_state.assert_not_called()

    async def test_playback_stopping_restarts_the_inactivity_clock(self, controller):
        """Otherwise the screen blanks the instant the last track ends, having
        counted the whole album as inactivity."""
        controller.running = True
        controller.screen_on = True
        controller.session_live = True
        controller.last_activity_time = monotonic() - 600
        _session_on_the_state(controller, live=False)

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

        type(controller.state_machine).system_state = PropertyMock(
            side_effect=[RuntimeError("state machine gone"), SystemAudioState()]
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
        controller = ScreenController(Mock(), settings, hardware, AsyncMock())
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


_REPO = Path(__file__).parents[2]


def _load_kiosk_zoom():
    """The ExecStartPre script, which has no .py suffix to import by. Executed
    from source rather than imported, so no __pycache__ lands in rootfs/."""
    path = _REPO / "rootfs" / "usr" / "local" / "bin" / "milo-kiosk-zoom"
    module = types.ModuleType("milo_kiosk_zoom")
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


def _stored_level(profile_root):
    prefs = json.loads((profile_root / "Default" / "Preferences").read_text())
    return prefs["partition"]["default_zoom_level"]["x"]


class TestKioskScale:
    """The interface scale reaches Chromium through kiosk.env and a restart."""

    async def test_the_written_variable_is_the_one_the_kiosk_zoom_reads(
        self, controller, tmp_path, monkeypatch
    ):
        """Writer, unit and script name the variable and the script separately;
        if they drift, the kiosk starts at 100% whatever the setting says."""
        env = tmp_path / "kiosk.env"
        monkeypatch.setattr(ScreenController, "KIOSK_ENV_PATH", str(env))
        await controller.write_kiosk_env(1.25)

        written = dict(
            line.split("=", 1) for line in env.read_text().splitlines()
            if line and not line.startswith("#")
        )
        unit = (_REPO / "system" / "milo-kiosk.service").read_text()
        assert "ExecStartPre=/usr/local/bin/milo-kiosk-zoom" in unit
        zoom = _load_kiosk_zoom()
        monkeypatch.setattr(zoom, "PREFERENCES", str(tmp_path / "Default" / "Preferences"))
        for name, value in written.items():
            monkeypatch.setenv(name, value)

        zoom.main()

        assert 1.2 ** _stored_level(tmp_path) == pytest.approx(1.25)

    def test_the_kiosk_zoom_keeps_the_rest_of_the_profile(self, tmp_path, monkeypatch):
        """The profile survives restarts and Chromium keeps its own state in the
        same file; replacing it would wipe that state on every scale change."""
        prefs = tmp_path / "Default" / "Preferences"
        prefs.parent.mkdir()
        prefs.write_text(json.dumps({"browser": {"window": 1}, "partition": {"per_host_zoom_levels": {}}}))
        zoom = _load_kiosk_zoom()
        monkeypatch.setattr(zoom, "PREFERENCES", str(prefs))
        monkeypatch.setenv("MILO_UI_SCALE", "1.1")

        zoom.main()

        stored = json.loads(prefs.read_text())
        assert stored["browser"] == {"window": 1}
        assert stored["partition"]["per_host_zoom_levels"] == {}
        assert 1.2 ** _stored_level(tmp_path) == pytest.approx(1.1)

    @pytest.fixture(autouse=True)
    def _no_settle(self, monkeypatch):
        monkeypatch.setattr("backend.hardware.screen.KIOSK_RESTART_SETTLE_S", 0)

    async def test_an_enabled_kiosk_is_restarted_even_while_still_restarting(self, controller):
        """A second change made while the first restart is under way must still
        restart it — probing `active` then answered no and dropped the change."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.probe_active = AsyncMock(return_value=False)

        await controller.restart_kiosk()

        controller.systemd_manager.restart.assert_awaited_once_with("milo-kiosk.service")

    async def test_a_disabled_kiosk_is_not_started(self, controller):
        """A unit set up with no screen has the kiosk disabled; a restart would
        start it."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=False)

        await controller.restart_kiosk()

        controller.systemd_manager.restart.assert_not_awaited()

    async def test_the_kiosk_brought_up_takes_the_screen_settings_once(self, controller):
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.restart = AsyncMock(return_value=True)

        await controller.restart_kiosk(reopen_screen_settings=True)

        assert controller.take_reopen_screen_settings() is True
        assert controller.take_reopen_screen_settings() is False

    async def test_a_restart_that_failed_leaves_nothing_to_reopen(self, controller):
        """The old kiosk is still up; reopening the settings on its next boot,
        hours later, would answer a change nobody remembers."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.restart = AsyncMock(return_value=False)

        await controller.restart_kiosk(reopen_screen_settings=True)

        assert controller.take_reopen_screen_settings() is False

    async def test_a_burst_of_scale_changes_restarts_the_kiosk_once(self, controller):
        """Each click restarting on its own queued the restarts past the 12.5 s
        control timeout ("Erreur système"), and stopped a cage still starting,
        which then held the screen black until systemd's 90 s SIGKILL."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.restart = AsyncMock(return_value=True)

        await asyncio.gather(*(controller.restart_kiosk() for _ in range(5)))

        controller.systemd_manager.restart.assert_awaited_once_with("milo-kiosk.service")

    async def test_a_kiosk_change_among_a_burst_still_reopens_the_settings(self, controller):
        """The last click may come from a remote browser; the kiosk that asked
        earlier in the burst must still come back on its Screen settings."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.restart = AsyncMock(return_value=True)

        await asyncio.gather(
            controller.restart_kiosk(reopen_screen_settings=True),
            controller.restart_kiosk(reopen_screen_settings=False),
        )

        assert controller.take_reopen_screen_settings() is True

    async def test_a_change_during_a_restart_restarts_again_after_it(self, controller):
        """The restart under way read the previous kiosk.env; the new value
        needs one more, and never two restarts of the unit at once."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        in_flight = 0
        overlapped = False
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def restart(unit):
            nonlocal in_flight, overlapped
            in_flight += 1
            overlapped |= in_flight > 1
            if not first_started.is_set():
                first_started.set()
                await release_first.wait()
            in_flight -= 1
            return True

        controller.systemd_manager.restart = AsyncMock(side_effect=restart)

        first = asyncio.create_task(controller.restart_kiosk())
        await first_started.wait()
        second = asyncio.create_task(controller.restart_kiosk())
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second)

        assert controller.systemd_manager.restart.await_count == 2
        assert not overlapped

    async def test_a_remote_change_after_a_kiosk_one_keeps_its_reopen(self, controller):
        """The kiosk the first restart brought up has not asked yet; the
        restart a remote browser's change triggers must not take its answer."""
        controller.systemd_manager.is_enabled = AsyncMock(return_value=True)
        controller.systemd_manager.restart = AsyncMock(return_value=True)

        await controller.restart_kiosk(reopen_screen_settings=True)
        await controller.restart_kiosk(reopen_screen_settings=False)

        assert controller.take_reopen_screen_settings() is True

    def test_a_profile_that_cannot_be_written_still_lets_the_kiosk_start(
        self, tmp_path, monkeypatch
    ):
        """ExecStartPre failing fails the unit: no Chromium, a blank panel."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        zoom = _load_kiosk_zoom()
        monkeypatch.setattr(zoom, "PREFERENCES", str(blocker / "Default" / "Preferences"))
        monkeypatch.setenv("MILO_UI_SCALE", "1.2")

        assert zoom.main() == 0
