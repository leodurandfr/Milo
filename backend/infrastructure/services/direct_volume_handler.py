# backend/infrastructure/services/direct_volume_handler.py
"""
Direct mode volume handler - ALSA mixer control.
Handles volume operations when multiroom is disabled.
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.infrastructure.services.volume_converter_service import VolumeConverterService


class DirectVolumeHandler:
    """Handles volume control in direct mode (ALSA mixer)."""

    def __init__(self, converter: "VolumeConverterService"):
        self.converter = converter
        self.logger = logging.getLogger(__name__)

        self._precise_display_volume = 0.0
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    def get_display_volume(self) -> int:
        """Get current volume (0-100%)."""
        from backend.infrastructure.services.volume_converter_service import VolumeConverterService
        return VolumeConverterService.round_half_up(self._precise_display_volume)

    async def set_volume(self, display_volume: float) -> bool:
        """Set volume to specific level (0-100%)."""
        try:
            clamped = self.converter.clamp_display(display_volume)
            alsa = self.converter.display_to_alsa(int(clamped))

            success = await self._set_amixer_volume(alsa)
            if success:
                self._precise_display_volume = clamped
            return success
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def adjust_volume(self, delta: int) -> bool:
        """Adjust volume by delta."""
        try:
            from backend.infrastructure.services.volume_converter_service import VolumeConverterService

            current = self._precise_display_volume
            new_precise = self.converter.clamp_display(current + delta)
            new_display = VolumeConverterService.round_half_up(new_precise)
            new_alsa = self.converter.display_to_alsa(new_display)

            success = await self._set_amixer_volume(new_alsa)
            if success:
                self._precise_display_volume = new_precise
            return success
        except Exception as e:
            self.logger.error(f"Error adjusting volume: {e}")
            return False

    async def set_startup_volume(self, alsa_volume: int) -> bool:
        """Set startup volume."""
        try:
            success = await self._set_amixer_volume(alsa_volume)
            if success:
                display = self.converter.alsa_to_display(alsa_volume)
                self._precise_display_volume = float(display)
                self.logger.info(f"Direct startup: {display}%")
            return success
        except Exception as e:
            self.logger.error(f"Error setting startup volume: {e}")
            return False

    # ============================================================================
    # STATE MANAGEMENT
    # ============================================================================

    def set_precise_volume(self, value: float) -> None:
        """Set precise display volume (used during config reload)."""
        self._precise_display_volume = value

    def get_precise_volume(self) -> float:
        """Get precise display volume."""
        return self._precise_display_volume

    def get_status(self) -> dict:
        """Get handler status for debugging."""
        return {
            "mode": "direct",
            "precise_display_volume": self._precise_display_volume,
            "last_alsa_volume": self._last_alsa_volume,
        }

    # ============================================================================
    # INTERNAL METHODS
    # ============================================================================

    async def _set_amixer_volume(self, alsa_volume: int) -> bool:
        """Write volume to ALSA mixer via amixer subprocess."""
        try:
            limited = self.converter.clamp_alsa(alsa_volume)

            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()

            self._last_alsa_volume = limited
            self._alsa_cache_time = time.time()
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Error writing to amixer: {e}")
            return False
