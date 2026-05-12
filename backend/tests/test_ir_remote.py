# backend/tests/test_ir_remote.py
"""
Unit tests for the IR remote controller scancode parser and config persistence.

Hardware interaction (evdev async_read_loop, kernel keymap reload) is covered
by Pi-only smoke tests in Phase 6; here we exercise the pure-Python decoder
and the controller's config lifecycle without touching real devices.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.core.models.audio_state import AudioSource
from backend.hardware.ir_remote import (
    MENU_CLICK_WINDOW,
    IrRemoteController,
    UnsupportedRemoteError,
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
        state_machine.broadcast_event = AsyncMock()
        settings_service = MagicMock()
        settings_service.get_setting = AsyncMock(return_value=None)
        settings_service.set_setting = AsyncMock()
        return IrRemoteController(volume_service, state_machine, settings_service)

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
    state_machine.broadcast_event = AsyncMock()
    state_machine.transition_to_source = AsyncMock()
    state_machine.system_state.active_source = active_source
    settings_service = MagicMock()
    settings_service.get_setting = AsyncMock(
        return_value={"enabled_apps": dock_apps}
    )
    settings_service.set_setting = AsyncMock()
    return IrRemoteController(volume_service, state_machine, settings_service)


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

    @pytest.mark.asyncio
    async def test_returns_none_when_dock_setting_missing(self):
        controller = _menu_controller([], AudioSource.NONE)
        controller.settings_service.get_setting.return_value = None
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
        controller._register_menu_click()
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
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.BLUETOOTH
        )

    @pytest.mark.asyncio
    async def test_single_click_noop_when_no_audio_apps_in_dock(self):
        controller = _menu_controller(["settings"], AudioSource.NONE)
        controller._register_menu_click()
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_menu_click_timer_drops_pending_resolution(self):
        controller = _menu_controller(["spotify"], AudioSource.NONE)
        controller._register_menu_click()
        controller._cancel_menu_click_timer()
        await asyncio.sleep(MENU_CLICK_WINDOW + 0.1)
        controller.state_machine.transition_to_source.assert_not_called()
        assert controller._menu_click_count == 0
        assert controller._menu_click_timer is None


