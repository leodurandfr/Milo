# backend/infrastructure/services/volume_config_service.py
"""
Volume configuration service for loading and managing volume settings.
All values are in decibels (dB) as the single source of truth.
Range: -80 dB (silent) to 0 dB (maximum)
"""
import logging
from typing import Any, Dict
from dataclasses import dataclass


@dataclass
class VolumeConfig:
    """Volume configuration data class - all values in dB."""
    limit_min_db: float = -80.0
    limit_max_db: float = -21.0
    step_mobile_db: float = 3.0
    step_rotary_db: float = 2.0
    startup_volume_db: float = -30.0
    restore_last_volume: bool = False


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
                limit_min_db=volume_config.get("limit_min_db", -80.0),
                limit_max_db=volume_config.get("limit_max_db", -21.0),
                step_mobile_db=volume_config.get("step_mobile_db", 3.0),
                step_rotary_db=volume_config.get("step_rotary_db", 2.0),
                startup_volume_db=volume_config.get("startup_volume_db", -30.0),
                restore_last_volume=volume_config.get("restore_last_volume", False)
            )

            return self._config
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
            return self._config

    async def reload_limits(self) -> tuple[float, float]:
        """
        Reload volume limits from settings.

        Returns:
            Tuple of (old_limit_min_db, old_limit_max_db) before reload
        """
        old_min = self._config.limit_min_db
        old_max = self._config.limit_max_db

        await self.load()

        return old_min, old_max

    def get_config_dict(self) -> Dict[str, Any]:
        """
        Get configuration as dictionary.

        Returns:
            Dictionary with all config values in dB
        """
        return {
            "limit_min_db": self._config.limit_min_db,
            "limit_max_db": self._config.limit_max_db,
            "step_mobile_db": self._config.step_mobile_db,
            "step_rotary_db": self._config.step_rotary_db,
            "startup_volume_db": self._config.startup_volume_db,
            "restore_last_volume": self._config.restore_last_volume
        }
