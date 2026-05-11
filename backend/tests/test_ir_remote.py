# backend/tests/test_ir_remote.py
"""
Unit tests for the IR remote controller scancode parser and config persistence.

Hardware interaction (evdev async_read_loop, kernel keymap reload) is covered
by Pi-only smoke tests in Phase 6; here we exercise the pure-Python decoder
and the controller's config lifecycle without touching real devices.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.hardware.ir_remote import (
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
