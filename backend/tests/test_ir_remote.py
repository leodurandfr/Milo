# backend/tests/test_ir_remote.py
"""
Unit tests for the IR remote controller scancode parser and config persistence.

Hardware interaction (evdev async_read_loop, kernel keymap reload) is covered
by Pi-only smoke tests in Phase 6; here we exercise the pure-Python decoder
and the controller's config lifecycle without touching real devices.
"""
import asyncio
from types import SimpleNamespace

import evdev
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.models.audio_state import AudioSource
from backend.hardware.ir_remote import (
    GPIO_IR_DEVICE_NAME,
    MENU_CLICK_WINDOW,
    IrRemoteController,
    UnsupportedRemoteError,
    _find_gpio_ir_device,
    parse_apple_scancode,
)
from backend.hardware.keymap_writer import APPLE_MANUFACTURER


class TestParseAppleScancode:
    """Phase 1 confirmed layout: 0x87EE | device_id | command."""

    def test_decodes_phase_1_reference_remote(self):
        # Phase 1 capture: Center button on device_id 0x8D → 0x04
        scancode = (APPLE_MANUFACTURER << 16) | (0x8D << 8) | 0x04
        command, device_id = parse_apple_scancode(scancode)
        assert command == 0x04
        assert device_id == 0x8D

    def test_decodes_volume_up_with_parity(self):
        # KEY_VOLUMEUP parity-1 form
        scancode = (APPLE_MANUFACTURER << 16) | (0x8D << 8) | 0x0B
        command, device_id = parse_apple_scancode(scancode)
        assert command == 0x0B
        assert device_id == 0x8D

    def test_rejects_non_apple_scancode(self):
        # NEC-style scancode without the Apple manufacturer prefix
        scancode = (0x77E1 << 16) | (0x8D << 8) | 0x04
        with pytest.raises(UnsupportedRemoteError):
            parse_apple_scancode(scancode)

    def test_handles_device_id_zero(self):
        """Factory-fresh Apple Remotes report device_id = 0x00."""
        scancode = (APPLE_MANUFACTURER << 16) | (0x00 << 8) | 0x04
        command, device_id = parse_apple_scancode(scancode)
        assert command == 0x04
        assert device_id == 0x00


class TestControllerConfig:
    """Config load/persist lifecycle."""

    @pytest.fixture
    def controller(self):
        volume_service = MagicMock()
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        settings_service = MagicMock()
        settings_service.get_setting = AsyncMock(return_value=None)
        settings_service.set_setting = AsyncMock()
        screen_controller = MagicMock()
        screen_controller.force_sleep = AsyncMock()
        return IrRemoteController(
            volume_service, state_machine, settings_service, screen_controller
        )

    @pytest.mark.asyncio
    async def test_unconfigured_settings_yield_idle_state(self, controller):
        controller.settings_service.get_setting.return_value = None
        await controller._load_config_from_settings()
        assert controller.enabled is False
        assert controller.paired is False
        assert controller.device_id is None

    @pytest.mark.asyncio
    async def test_paired_settings_restore_device_id(self, controller):
        controller.settings_service.get_setting.return_value = {
            "enabled": True,
            "device_id": 0x8D,
            "paired_at": 1700000000.0,
        }
        await controller._load_config_from_settings()
        assert controller.enabled is True
        assert controller.paired is True
        assert controller.device_id == 0x8D
        assert controller.paired_at == 1700000000.0

    @pytest.mark.asyncio
    async def test_invalid_device_id_treated_as_unpaired(self, controller):
        controller.settings_service.get_setting.return_value = {
            "enabled": True,
            "device_id": "not-an-int",
        }
        await controller._load_config_from_settings()
        assert controller.paired is False
        assert controller.device_id is None

    def test_get_status_shape(self, controller):
        status = controller.get_status()
        for key in (
            "available", "enabled", "paired", "device_id",
            "paired_at", "listening", "pairing_in_progress",
        ):
            assert key in status

    @pytest.mark.asyncio
    async def test_cancel_pairing_when_idle_returns_false(self, controller):
        cancelled = await controller.cancel_pairing()
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_update_config_disabled_when_paired_stops_listener(self, controller):
        # Mark as paired so the controller would otherwise start listening
        controller.paired = True
        controller.enabled = True
        controller._stop_runtime_listener = AsyncMock()
        controller._start_runtime_listener = AsyncMock()

        await controller.update_config({"enabled": False})

        assert controller.enabled is False
        controller._stop_runtime_listener.assert_awaited()
        controller._start_runtime_listener.assert_not_called()
        controller.settings_service.set_setting.assert_awaited()


def _menu_controller(dock_apps, active_source):
    """Build a controller wired with a dock-order + active-source fixture."""
    volume_service = MagicMock()
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    state_machine.transition_to_source = AsyncMock()
    state_machine.system_state.active_source = active_source
    settings_service = MagicMock()
    settings_service.get_setting = AsyncMock(
        return_value={"enabled_apps": dock_apps}
    )
    settings_service.set_setting = AsyncMock()
    screen_controller = MagicMock()
    screen_controller.force_sleep = AsyncMock()
    return IrRemoteController(
        volume_service, state_machine, settings_service, screen_controller
    )


class TestNextAudioSourceInDockOrder:
    """`_next_audio_source_in_dock_order()` cycle resolution."""

    @pytest.mark.asyncio
    async def test_returns_first_audio_app_when_source_is_none(self):
        controller = _menu_controller(
            ["spotify", "radio", "settings"], AudioSource.NONE
        )
        result = await controller._next_audio_source_in_dock_order()
        assert result == AudioSource.SPOTIFY

    @pytest.mark.asyncio
    async def test_skips_non_audio_dock_apps(self):
        controller = _menu_controller(
            ["equalizer", "spotify", "settings", "radio"], AudioSource.SPOTIFY
        )
        result = await controller._next_audio_source_in_dock_order()
        assert result == AudioSource.RADIO

    @pytest.mark.asyncio
    async def test_wraps_around_to_first(self):
        controller = _menu_controller(
            ["spotify", "radio", "podcast"], AudioSource.PODCAST
        )
        result = await controller._next_audio_source_in_dock_order()
        assert result == AudioSource.SPOTIFY

    @pytest.mark.asyncio
    async def test_falls_back_to_first_when_active_source_missing_from_dock(self):
        controller = _menu_controller(["radio", "podcast"], AudioSource.SPOTIFY)
        result = await controller._next_audio_source_in_dock_order()
        assert result == AudioSource.RADIO

    @pytest.mark.asyncio
    async def test_returns_none_when_dock_has_no_audio_apps(self):
        controller = _menu_controller(
            ["equalizer", "multiroom", "settings"], AudioSource.NONE
        )
        result = await controller._next_audio_source_in_dock_order()
        assert result is None


class TestMenuClickResolver:
    """`_register_menu_click()` + `_resolve_menu_clicks()` integration."""

    @pytest.mark.asyncio
    async def test_single_click_cycles_to_next_source(self):
        controller = _menu_controller(
            ["spotify", "radio", "podcast"], AudioSource.SPOTIFY
        )
        controller._register_menu_click()
        controller._menu_pressed = False  # simulate release
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.RADIO
        )

    @pytest.mark.asyncio
    async def test_double_click_transitions_to_none(self):
        controller = _menu_controller(
            ["spotify", "radio"], AudioSource.SPOTIFY
        )
        controller._register_menu_click()
        controller._menu_pressed = False
        controller._register_menu_click()
        controller._menu_pressed = False
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.NONE
        )

    @pytest.mark.asyncio
    async def test_three_clicks_still_transitions_to_none(self):
        controller = _menu_controller(
            ["spotify", "radio"], AudioSource.SPOTIFY
        )
        for _ in range(3):
            controller._register_menu_click()
            controller._menu_pressed = False
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.NONE
        )

    @pytest.mark.asyncio
    async def test_single_click_from_none_activates_first_dock_source(self):
        controller = _menu_controller(
            ["bluetooth", "radio", "spotify"], AudioSource.NONE
        )
        controller._register_menu_click()
        controller._menu_pressed = False
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.BLUETOOTH
        )

    @pytest.mark.asyncio
    async def test_single_click_noop_when_no_audio_apps_in_dock(self):
        controller = _menu_controller(["settings"], AudioSource.NONE)
        controller._register_menu_click()
        controller._menu_pressed = False
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_menu_click_timer_drops_pending_resolution(self):
        controller = _menu_controller(["spotify"], AudioSource.NONE)
        controller._register_menu_click()
        controller._cancel_menu_click_timer()
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_not_called()
        controller.screen_controller.force_sleep.assert_not_called()
        assert controller._menu_click_count == 0
        assert controller._menu_click_timer is None
        assert controller._menu_pressed is False


class TestMenuHold:
    """Hold detection: MENU still pressed at T+MENU_CLICK_WINDOW → screen sleep."""

    @pytest.mark.asyncio
    async def test_hold_fires_screen_sleep(self):
        controller = _menu_controller(
            ["spotify", "radio"], AudioSource.SPOTIFY
        )
        controller._register_menu_click()
        # Button stays pressed — simulate by NOT clearing _menu_pressed.
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.screen_controller.force_sleep.assert_awaited_once()
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.NONE
        )

    @pytest.mark.asyncio
    async def test_release_before_window_fires_cycle_not_sleep(self):
        controller = _menu_controller(
            ["spotify", "radio"], AudioSource.SPOTIFY
        )
        controller._register_menu_click()
        # Release the button promptly (well within the click window).
        controller._menu_pressed = False
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.screen_controller.force_sleep.assert_not_called()
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.RADIO
        )

    @pytest.mark.asyncio
    async def test_hold_overrides_accumulated_clicks(self):
        """Two quick taps then a hold of the third press → hold wins."""
        controller = _menu_controller(
            ["spotify", "radio"], AudioSource.SPOTIFY
        )
        # Tap, release, tap, release, tap and HOLD
        controller._register_menu_click()
        controller._menu_pressed = False
        controller._register_menu_click()
        controller._menu_pressed = False
        controller._register_menu_click()
        # Last press still held at resolution
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.screen_controller.force_sleep.assert_awaited_once()
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.NONE
        )

    @pytest.mark.asyncio
    async def test_hold_resolver_clears_count_not_pressed_flag(self):
        """Resolver clears click bookkeeping but leaves `_menu_pressed` alone.

        The pressed flag is only cleared by the runtime loop when the
        physical value=0 (release) arrives. Clearing it inside the resolver
        would race with that release event.
        """
        controller = _menu_controller(["spotify"], AudioSource.NONE)
        controller._register_menu_click()
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.screen_controller.force_sleep.assert_awaited_once()
        assert controller._menu_click_count == 0
        assert controller._menu_click_timer is None
        assert controller._menu_pressed is True  # cleared by runtime loop, not here




# ============================================================================
# Runtime listener — device discovery, the EV_KEY filter, keycode dispatch
# ============================================================================

def _event(type_, code, value):
    """A minimal stand-in for evdev's InputEvent (only 3 fields are read)."""
    return SimpleNamespace(type=type_, code=code, value=value)


def _fake_input_device(events=(), name=GPIO_IR_DEVICE_NAME):
    """An evdev.InputDevice stand-in that replays `events` then ends the loop.

    Ending the read loop is how the tests get `_runtime_loop` to return: on
    hardware the generator never ends and the task is cancelled instead.
    """
    device = MagicMock()
    device.name = name

    async def _loop():
        for event in events:
            yield event

    device.async_read_loop = _loop
    return device


async def _drain_volume(controller):
    """Let VolumeAccumulator's processor task apply what it batched.

    It drains synchronously on its first pass, then sleeps BATCH_INTERVAL —
    two of those is enough for every delta these tests queue.
    """
    for _ in range(4):
        await asyncio.sleep(controller._volume.BATCH_INTERVAL)


def _runtime_controller(active_source=AudioSource.SPOTIFY, step_db=2.0):
    """A controller whose volume / playback effects are observable.

    The VolumeAccumulator and PlaybackDispatcher are the controller's real
    collaborators — only the outside world (volume service, source instance)
    is mocked, so an assertion here covers the whole chain from an EV_KEY
    code to the command the source receives.
    """
    volume_service = MagicMock()
    volume_service.volume_config.step_ir_remote_db = step_db
    volume_service.adjust_volume_db = AsyncMock()

    source_instance = MagicMock()
    source_instance.command = AsyncMock()
    source_instance.is_playing = True

    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    state_machine.transition_to_source = AsyncMock()
    state_machine.system_state.active_source = active_source
    state_machine.get_source = MagicMock(return_value=source_instance)

    settings_service = MagicMock()
    settings_service.get_setting = AsyncMock(return_value=None)
    settings_service.set_setting = AsyncMock()
    screen_controller = MagicMock()
    screen_controller.force_sleep = AsyncMock()

    controller = IrRemoteController(
        volume_service, state_machine, settings_service, screen_controller
    )
    controller.source_instance = source_instance
    return controller


class TestFindGpioIrDevice:
    """`_find_gpio_ir_device()` — which /dev/input node the listener opens.

    When this picks the wrong node (or None), every button is dead and the
    only symptom is one WARNING line at startup.
    """

    def test_returns_the_path_of_the_node_named_gpio_ir_recv(self):
        devices = {
            "/dev/input/event0": _fake_input_device(name="vc4-hdmi"),
            "/dev/input/event1": _fake_input_device(name=GPIO_IR_DEVICE_NAME),
        }
        with patch("evdev.list_devices", return_value=list(devices)), \
             patch("evdev.InputDevice", side_effect=lambda p: devices[p]):
            assert _find_gpio_ir_device() == "/dev/input/event1"

    def test_returns_none_when_no_node_carries_the_name(self):
        devices = {
            "/dev/input/event0": _fake_input_device(name="vc4-hdmi"),
            "/dev/input/event1": _fake_input_device(name="pwr_button"),
        }
        with patch("evdev.list_devices", return_value=list(devices)), \
             patch("evdev.InputDevice", side_effect=lambda p: devices[p]):
            assert _find_gpio_ir_device() is None
        # Every node opened during the scan is closed again — the listener
        # reopens the winner by path, so a leaked fd here is a leaked fd.
        for device in devices.values():
            device.close.assert_called_once()

    def test_a_node_that_cannot_be_opened_is_skipped_not_fatal(self):
        """A device removed mid-scan raises OSError; the scan must continue."""
        target = _fake_input_device(name=GPIO_IR_DEVICE_NAME)

        def _open(path):
            if path == "/dev/input/event0":
                raise OSError(19, "No such device")
            return target

        with patch("evdev.list_devices",
                   return_value=["/dev/input/event0", "/dev/input/event1"]), \
             patch("evdev.InputDevice", side_effect=_open):
            assert _find_gpio_ir_device() == "/dev/input/event1"


class TestRuntimeListenerLifecycle:
    """`_start_runtime_listener()` / `_stop_runtime_listener()`."""

    @pytest.mark.asyncio
    async def test_missing_kernel_device_leaves_the_listener_down(self):
        controller = _runtime_controller()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value=None):
            await controller._start_runtime_listener()
        assert controller._runtime_task is None

    @pytest.mark.asyncio
    async def test_a_second_start_does_not_open_a_second_reader(self):
        """Two readers on one evdev fd would double-fire every button."""
        controller = _runtime_controller()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=_fake_input_device()):
            await controller._start_runtime_listener()
            first = controller._runtime_task
            await controller._start_runtime_listener()
            assert controller._runtime_task is first
            await controller._stop_runtime_listener()

    @pytest.mark.asyncio
    async def test_stop_cancels_the_task_and_clears_it(self):
        controller = _runtime_controller()
        started = asyncio.Event()

        async def _never_ends():
            started.set()
            await asyncio.Event().wait()

        controller._runtime_task = asyncio.create_task(_never_ends())
        await started.wait()

        task = controller._runtime_task
        await controller._stop_runtime_listener()

        assert controller._runtime_task is None
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_an_unopenable_device_is_logged_not_raised(self):
        """The listener runs as a bare task — an escaping OSError is silent."""
        controller = _runtime_controller()
        controller._device_path = "/dev/input/event1"
        with patch("evdev.InputDevice", side_effect=OSError(16, "Device busy")):
            await controller._runtime_loop()  # must not raise


class TestRuntimeEventFilter:
    """`_runtime_loop()` — which EV_KEY events become actions.

    The kernel keymap already filters by remote; this filter decides key-down
    vs autorepeat vs key-up. Getting it wrong double-fires transport commands
    or strands the MENU hold gesture.
    """

    @pytest.mark.asyncio
    async def _drive(self, controller, events):
        controller._device_path = "/dev/input/event1"
        with patch("evdev.InputDevice",
                   return_value=_fake_input_device(events)):
            await controller._runtime_loop()

    @pytest.mark.asyncio
    async def test_a_key_down_on_play_pause_reaches_the_active_source(self):
        controller = _runtime_controller(active_source=AudioSource.SPOTIFY)
        await self._drive(controller, [
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_PLAYPAUSE, 1),
        ])
        controller.source_instance.command.assert_awaited_once_with("playpause", {})

    @pytest.mark.asyncio
    async def test_autorepeat_on_play_pause_is_dropped(self):
        """value=2 is the kernel autorepeat: holding Center must not re-fire."""
        controller = _runtime_controller(active_source=AudioSource.SPOTIFY)
        await self._drive(controller, [
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_PLAYPAUSE, 1),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_PLAYPAUSE, 2),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_PLAYPAUSE, 2),
        ])
        controller.source_instance.command.assert_awaited_once_with("playpause", {})

    @pytest.mark.asyncio
    async def test_autorepeat_on_volume_accumulates(self):
        """Hold-to-repeat is the one gesture the volume keys DO accept."""
        controller = _runtime_controller(step_db=2.0)
        await self._drive(controller, [
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_VOLUMEUP, 1),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_VOLUMEUP, 2),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_VOLUMEUP, 2),
        ])
        await _drain_volume(controller)
        applied = sum(
            call.args[0]
            for call in controller.volume_service.adjust_volume_db.await_args_list
        )
        assert applied == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_key_up_on_menu_clears_the_hold_flag(self):
        """The release event is the ONLY thing that clears `_menu_pressed`."""
        controller = _runtime_controller()
        await self._drive(controller, [
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_HOMEPAGE, 1),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_HOMEPAGE, 0),
        ])
        assert controller._menu_pressed is False
        assert controller._menu_click_count == 1
        controller._cancel_menu_click_timer()

    @pytest.mark.asyncio
    async def test_key_up_on_another_button_leaves_the_hold_flag_alone(self):
        controller = _runtime_controller()
        await self._drive(controller, [
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_HOMEPAGE, 1),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_NEXTSONG, 0),
        ])
        assert controller._menu_pressed is True
        controller._cancel_menu_click_timer()

    @pytest.mark.asyncio
    async def test_only_ev_key_events_may_become_actions(self):
        """The same fd carries EV_MSC scancodes and EV_SYN separators too.

        The two events below are synthetic: they carry an EV_KEY code and an
        EV_KEY value under a non-EV_KEY type, which real rc-core never emits.
        That is the point — a realistic EV_MSC/EV_SYN pair sails through even
        with the type guard deleted, so it pins nothing. These collide on
        purpose, so the guard is the only thing standing between them and a
        dispatch.
        """
        controller = _runtime_controller()
        await self._drive(controller, [
            _event(evdev.ecodes.EV_MSC, evdev.ecodes.KEY_PLAYPAUSE, 1),
            _event(evdev.ecodes.EV_SYN, evdev.ecodes.KEY_HOMEPAGE, 1),
        ])
        controller.source_instance.command.assert_not_awaited()
        assert controller._menu_click_count == 0


class TestHandleKeycode:
    """`_handle_keycode()` — the code → action map itself.

    Every arm is wrapped in a try/except that only logs, so a wrong attribute
    or a renamed command shows up as a dead button, never as a failure.
    """

    @pytest.mark.asyncio
    async def test_volume_up_accumulates_the_configured_step(self):
        controller = _runtime_controller(step_db=3.5)
        await controller._handle_keycode(evdev.ecodes.KEY_VOLUMEUP)
        await _drain_volume(controller)
        controller.volume_service.adjust_volume_db.assert_awaited_once_with(3.5)

    @pytest.mark.asyncio
    async def test_volume_down_accumulates_the_negated_step(self):
        controller = _runtime_controller(step_db=3.5)
        await controller._handle_keycode(evdev.ecodes.KEY_VOLUMEDOWN)
        await _drain_volume(controller)
        controller.volume_service.adjust_volume_db.assert_awaited_once_with(-3.5)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code,direction", [
        ("KEY_NEXTSONG", "next"),
        ("KEY_PREVIOUSSONG", "prev"),
    ])
    async def test_track_buttons_reach_the_active_source(self, code, direction):
        controller = _runtime_controller(active_source=AudioSource.SPOTIFY)
        await controller._handle_keycode(getattr(evdev.ecodes, code))
        controller.source_instance.command.assert_awaited_once_with(direction, {})

    @pytest.mark.asyncio
    async def test_menu_registers_a_click_rather_than_acting_at_once(self):
        controller = _runtime_controller()
        await controller._handle_keycode(evdev.ecodes.KEY_HOMEPAGE)
        assert controller._menu_click_count == 1
        assert controller._menu_pressed is True
        controller.state_machine.transition_to_source.assert_not_called()
        controller._cancel_menu_click_timer()

    @pytest.mark.asyncio
    async def test_an_unmapped_keycode_does_nothing(self):
        controller = _runtime_controller()
        await controller._handle_keycode(evdev.ecodes.KEY_POWER)
        controller.source_instance.command.assert_not_awaited()
        controller.volume_service.adjust_volume_db.assert_not_awaited()
        assert controller._menu_click_count == 0


# ============================================================================
# Pairing — the wizard's whole contract, captured through the real evdev path
# ============================================================================

async def _until(predicate, tries=200):
    """Yield to the loop until `predicate()` holds (or give up)."""
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0)
    return False


def _pairing_device(events=(), hold=True):
    """An evdev.InputDevice stand-in for the pairing capture.

    `hold=True` keeps the read loop open after the last event, which is what
    the kernel device does — the capture ends because it found a scancode,
    was cancelled, or timed out, never because the loop ran out.
    """
    device = MagicMock()
    device.name = GPIO_IR_DEVICE_NAME

    async def _loop():
        for event in events:
            yield event
        if hold:
            await asyncio.Event().wait()

    device.async_read_loop = _loop
    return device


def _scan(scancode):
    return _event(evdev.ecodes.EV_MSC, evdev.ecodes.MSC_SCAN, scancode)


APPLE_SCANCODE_8D = (APPLE_MANUFACTURER << 16) | (0x8D << 8) | 0x04


class TestStartPairing:
    """`start_pairing()` — the five statuses the wizard switches on.

    IrRemoteSettings.vue branches on `error` / `success` / `timeout` /
    `unsupported` / `cancelled`; the route turns `error` into HTTP 500 and
    passes the rest through as 200. A status that changes shape strands the
    wizard on its spinner.
    """

    @pytest.mark.asyncio
    async def test_a_second_pairing_is_refused_while_one_is_running(self):
        controller = _runtime_controller()
        controller._pairing_in_progress = True
        with patch("backend.hardware.ir_remote._find_gpio_ir_device") as find:
            result = await controller.start_pairing()
        assert result["status"] == "error"
        # Refused before the device is even looked up — two readers on one fd
        # is the failure this guard exists to prevent.
        find.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_missing_receiver_is_an_error_not_a_timeout(self):
        """No gpio-ir overlay is a wiring fault; making the user wait 15 s for
        a `timeout` would blame the remote instead."""
        controller = _runtime_controller()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value=None):
            result = await controller.start_pairing()
        assert result["status"] == "error"
        assert controller._pairing_in_progress is False

    @pytest.mark.asyncio
    async def test_a_captured_scancode_pairs_enables_and_resumes_listening(self):
        controller = _runtime_controller()
        device = _pairing_device([_scan(APPLE_SCANCODE_8D)])
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device), \
             patch("backend.hardware.ir_remote.keymap_writer.apply_keymap",
                   new=AsyncMock()) as apply_keymap:
            result = await controller.start_pairing(timeout_seconds=5)

            assert result == {"status": "success", "device_id": 0x8D}
            apply_keymap.assert_awaited_once_with(0x8D)
            assert controller.paired is True
            assert controller.device_id == 0x8D
            assert isinstance(controller.paired_at, float)
            # Auto-enable: the remote must work the moment the wizard closes,
            # without a second trip through the header toggle.
            assert controller.enabled is True
            controller.settings_service.set_setting.assert_awaited_once_with(
                'hardware.ir_remote',
                {'enabled': True, 'device_id': 0x8D,
                 'paired_at': controller.paired_at},
            )
            assert controller._runtime_task is not None
            await controller._stop_runtime_listener()

    @pytest.mark.asyncio
    async def test_a_keymap_that_fails_to_load_leaves_the_controller_unpaired(self):
        """The keymap IS the pairing — the kernel filters by device_id, not
        Python. Recording a pairing the kernel never took would leave a remote
        that looks paired in the UI and does nothing."""
        controller = _runtime_controller()
        device = _pairing_device([_scan(APPLE_SCANCODE_8D)])
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device), \
             patch("backend.hardware.ir_remote.keymap_writer.apply_keymap",
                   new=AsyncMock(side_effect=RuntimeError("exit 1: no such file"))):
            result = await controller.start_pairing(timeout_seconds=5)

        assert result["status"] == "error"
        assert "exit 1: no such file" in result["message"]
        assert controller.paired is False
        assert controller.device_id is None
        controller.settings_service.set_setting.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_remote_that_is_not_an_apple_remote_is_reported_at_once(self):
        controller = _runtime_controller()
        foreign = (0x77E1 << 16) | (0x8D << 8) | 0x04
        device = _pairing_device([_scan(foreign)])
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device), \
             patch("backend.hardware.ir_remote.keymap_writer.apply_keymap",
                   new=AsyncMock()) as apply_keymap:
            result = await controller.start_pairing(timeout_seconds=5)

        assert result["status"] == "unsupported"
        apply_keymap.assert_not_awaited()
        assert controller.paired is False

    @pytest.mark.asyncio
    async def test_a_silent_remote_times_out_and_clears_the_pairing_state(self):
        controller = _runtime_controller()
        device = _pairing_device()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device):
            result = await controller.start_pairing(timeout_seconds=0.05)

        assert result["status"] == "timeout"
        assert controller.paired is False
        assert controller._pairing_in_progress is False
        assert controller._pairing_cancel_event is None
        # The capture holds `_mode_lock` while it reads; a timeout that leaks
        # it deadlocks every later pairing AND the runtime listener.
        assert not controller._mode_lock.locked()

    @pytest.mark.asyncio
    async def test_the_wizard_can_cancel_a_capture_in_flight(self):
        controller = _runtime_controller()
        device = _pairing_device()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device):
            pairing = asyncio.create_task(controller.start_pairing(timeout_seconds=5))
            assert await _until(lambda: controller._pairing_in_progress)

            assert await controller.cancel_pairing() is True
            result = await pairing

        assert result["status"] == "cancelled"
        assert controller.paired is False
        assert controller._pairing_in_progress is False
        assert not controller._mode_lock.locked()

    @pytest.mark.asyncio
    async def test_the_status_is_broadcast_on_both_sides_of_the_capture(self):
        """The wizard's spinner is driven by the WS flag, not by the pending
        HTTP request — it must be raised before the wait and lowered after."""
        controller = _runtime_controller()
        device = _pairing_device()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device):
            await controller.start_pairing(timeout_seconds=0.05)

        flags = [call.args[0].pairing_in_progress
                 for call in controller.state_machine.broadcast.await_args_list]
        assert flags == [True, False]


class TestCaptureFilter:
    """`_capture_one_scancode()` — which events on the fd are a pairing press."""

    @pytest.mark.asyncio
    async def test_only_msc_scan_events_carry_a_scancode(self):
        """The fd interleaves EV_KEY and EV_SYN with the scancode events; the
        first non-scan event decoded as one would pair a garbage device_id.

        The third event is synthetic — an EV_KEY carrying MSC_SCAN's code and
        a valid Apple scancode as its value, which no real device emits. The
        two guards overlap on every realistic event (an EV_KEY code is never
        MSC_SCAN), so only a collision tells them apart: without the type
        guard this pairs 0x42, the wrong remote.
        """
        controller = _runtime_controller()
        device = _pairing_device([
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.KEY_PLAYPAUSE, 1),
            _event(evdev.ecodes.EV_MSC, evdev.ecodes.MSC_RAW, 0xDEADBEEF),
            _event(evdev.ecodes.EV_KEY, evdev.ecodes.MSC_SCAN,
                   (APPLE_MANUFACTURER << 16) | (0x42 << 8) | 0x04),
            _scan(APPLE_SCANCODE_8D),
        ])
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=device), \
             patch("backend.hardware.ir_remote.keymap_writer.apply_keymap",
                   new=AsyncMock()):
            result = await controller.start_pairing(timeout_seconds=5)
            assert result == {"status": "success", "device_id": 0x8D}
            await controller._stop_runtime_listener()

    @pytest.mark.asyncio
    async def test_a_device_that_cannot_be_opened_is_an_error(self):
        controller = _runtime_controller()
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", side_effect=OSError(16, "Device busy")):
            result = await controller.start_pairing(timeout_seconds=5)
        assert result["status"] == "error"
        assert "Device busy" in result["message"]


class TestUpdateConfigEnable:
    """The header master switch — `PATCH /api/ir-remote/config`."""

    @pytest.mark.asyncio
    async def test_enabling_a_paired_remote_starts_the_listener(self):
        controller = _runtime_controller()
        controller.paired = True
        controller.enabled = False
        with patch("backend.hardware.ir_remote._find_gpio_ir_device",
                   return_value="/dev/input/event1"), \
             patch("evdev.InputDevice", return_value=_fake_input_device()):
            await controller.update_config({"enabled": True})
            assert controller.enabled is True
            assert controller._runtime_task is not None
            await controller._stop_runtime_listener()
        controller.settings_service.set_setting.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enabling_an_unpaired_remote_starts_nothing(self):
        controller = _runtime_controller()
        controller.paired = False
        with patch("backend.hardware.ir_remote._find_gpio_ir_device") as find:
            await controller.update_config({"enabled": True})
        assert controller.enabled is True
        assert controller._runtime_task is None
        find.assert_not_called()


class TestUnpair:
    """`unpair()` — the one path that talks to the kernel on the way out."""

    def _paired(self):
        controller = _runtime_controller()
        controller.enabled = True
        controller.paired = True
        controller.device_id = 0x8D
        controller.paired_at = 1700000000.0
        return controller

    @pytest.mark.asyncio
    async def test_forgetting_a_remote_clears_the_pairing_and_persists_it(self):
        controller = self._paired()
        with patch("backend.hardware.ir_remote.keymap_writer.clear_kernel_keymap",
                   new=AsyncMock()) as clear:
            await controller.unpair()

        clear.assert_awaited_once()
        assert controller.paired is False
        assert controller.device_id is None
        assert controller.paired_at is None
        controller.settings_service.set_setting.assert_awaited_once_with(
            'hardware.ir_remote',
            {'enabled': True, 'device_id': None, 'paired_at': None},
        )
        controller.state_machine.broadcast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unpairing_does_not_touch_the_master_switch(self):
        """`enabled` is the header toggle, orthogonal to pairing: clearing it
        here would drop the user into the "feature disabled" panel instead of
        the pairing wizard."""
        controller = self._paired()
        with patch("backend.hardware.ir_remote.keymap_writer.clear_kernel_keymap",
                   new=AsyncMock()):
            await controller.unpair()
        assert controller.enabled is True

    @pytest.mark.asyncio
    async def test_a_kernel_that_refuses_the_clear_still_unpairs(self):
        """The helper can fail (no sudoers grant, no /etc/rc_keymaps); the
        settings and the UI state still have to move, or the user is stuck
        with a remote they cannot forget."""
        controller = self._paired()
        with patch("backend.hardware.ir_remote.keymap_writer.clear_kernel_keymap",
                   new=AsyncMock(side_effect=RuntimeError("exit 1"))):
            await controller.unpair()

        assert controller.paired is False
        controller.settings_service.set_setting.assert_awaited_once()
