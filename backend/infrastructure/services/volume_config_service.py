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
        """Get current configuration (use config.alsa_min, config.startup_volume, etc.)."""
        return self._config

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
