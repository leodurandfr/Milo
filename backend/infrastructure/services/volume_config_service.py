# backend/infrastructure/services/volume_config_service.py
"""
Volume configuration service for loading and managing volume settings.
Handles reading/reloading volume configuration from the settings service.
"""
import logging
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class VolumeConfig:
    """Volume configuration data class."""
    alsa_min: int = 0
    alsa_max: int = 65
    startup_volume: int = 37
    restore_last_volume: bool = False
    mobile_volume_steps: int = 5
    rotary_volume_steps: int = 2


class VolumeConfigService:
    """Service for managing volume configuration."""

    def __init__(self, settings_service):
        """
        Initialize the config service.

        Args:
            settings_service: SettingsService instance for reading settings
        """
        self.settings_service = settings_service
        self.logger = logging.getLogger(__name__)
        self._config = VolumeConfig()

    @property
    def config(self) -> VolumeConfig:
        """Get current configuration."""
        return self._config

    @property
    def alsa_min(self) -> int:
        """Get ALSA minimum volume."""
        return self._config.alsa_min

    @property
    def alsa_max(self) -> int:
        """Get ALSA maximum volume."""
        return self._config.alsa_max

    @property
    def startup_volume(self) -> int:
        """Get startup volume."""
        return self._config.startup_volume

    @property
    def restore_last_volume(self) -> bool:
        """Get restore last volume flag."""
        return self._config.restore_last_volume

    @property
    def mobile_volume_steps(self) -> int:
        """Get mobile volume steps."""
        return self._config.mobile_volume_steps

    @property
    def rotary_volume_steps(self) -> int:
        """Get rotary encoder volume steps."""
        return self._config.rotary_volume_steps

    async def load(self) -> VolumeConfig:
        """
        Load volume configuration from settings.

        Returns:
            VolumeConfig with loaded values
        """
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}

            self._config = VolumeConfig(
                alsa_min=volume_config.get("alsa_min", 0),
                alsa_max=volume_config.get("alsa_max", 65),
                startup_volume=volume_config.get("startup_volume", 37),
                restore_last_volume=volume_config.get("restore_last_volume", False),
                mobile_volume_steps=volume_config.get("mobile_volume_steps", 5),
                rotary_volume_steps=volume_config.get("rotary_volume_steps", 2)
            )

            return self._config
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
            return self._config

    async def reload_limits(self) -> tuple[int, int]:
        """
        Reload volume limits from settings.

        Returns:
            Tuple of (old_alsa_min, old_alsa_max) before reload
        """
        old_min = self._config.alsa_min
        old_max = self._config.alsa_max

        await self.load()

        return old_min, old_max

    async def reload_startup_config(self) -> bool:
        """
        Reload startup configuration only.

        Returns:
            True if successful
        """
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}

            self._config.startup_volume = volume_config.get("startup_volume", 37)
            self._config.restore_last_volume = volume_config.get("restore_last_volume", False)

            return True
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def reload_mobile_steps(self) -> int:
        """
        Reload mobile volume steps.

        Returns:
            New mobile volume steps value
        """
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}
            self._config.mobile_volume_steps = volume_config.get("mobile_volume_steps", 5)
            return self._config.mobile_volume_steps
        except Exception as e:
            self.logger.error(f"Error reloading mobile steps: {e}")
            return self._config.mobile_volume_steps

    async def reload_rotary_steps(self) -> int:
        """
        Reload rotary encoder steps.

        Returns:
            New rotary volume steps value
        """
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}
            self._config.rotary_volume_steps = volume_config.get("rotary_volume_steps", 2)
            return self._config.rotary_volume_steps
        except Exception as e:
            self.logger.error(f"Error reloading rotary steps: {e}")
            return self._config.rotary_volume_steps

    def get_config_dict(self) -> Dict[str, Any]:
        """
        Get configuration as dictionary.

        Returns:
            Dictionary with all config values
        """
        return {
            "alsa_min": self._config.alsa_min,
            "alsa_max": self._config.alsa_max,
            "startup_volume": self._config.startup_volume,
            "restore_last_volume": self._config.restore_last_volume,
            "mobile_steps": self._config.mobile_volume_steps,
            "rotary_steps": self._config.rotary_volume_steps
        }

    def limits_changed(self, old_min: int, old_max: int) -> bool:
        """
        Check if limits have changed.

        Args:
            old_min: Previous ALSA minimum
            old_max: Previous ALSA maximum

        Returns:
            True if limits changed
        """
        return old_min != self._config.alsa_min or old_max != self._config.alsa_max
