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
        Performs one-shot migration from old ALSA format if needed.

        Returns:
            VolumeConfig with loaded values
        """
        try:
            self.settings_service.invalidate_cache()
            volume_config = await self.settings_service.get_setting('volume') or {}

            # One-shot migration: convert old ALSA format to dB
            if 'alsa_min' in volume_config or 'alsa_max' in volume_config:
                self.logger.info("Migrating volume config from ALSA (%) to dB format...")
                volume_config = await self._migrate_to_db_format(volume_config)

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

    async def _migrate_to_db_format(self, old_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate old ALSA-based config (%) to new dB-based config.
        Conversion: dB = -80 + (percent / 100) * 80

        Args:
            old_config: Old configuration with alsa_* keys

        Returns:
            New configuration with *_db keys
        """
        def percent_to_db(percent: float) -> float:
            """Convert 0-100% to -80 to 0 dB."""
            return round(-80.0 + (percent / 100.0) * 80.0, 1)

        def step_percent_to_db(step_percent: float) -> float:
            """Convert step percentage to dB (1% ≈ 0.8 dB)."""
            return max(1.0, round(step_percent * 0.8, 1))

        new_config = {
            'limit_min_db': percent_to_db(old_config.get('alsa_min', 0)),
            'limit_max_db': percent_to_db(old_config.get('alsa_max', 65)),
            'step_mobile_db': step_percent_to_db(old_config.get('mobile_volume_steps', 5)),
            'step_rotary_db': step_percent_to_db(old_config.get('rotary_volume_steps', 2)),
            'startup_volume_db': percent_to_db(old_config.get('startup_volume', 37)),
            'restore_last_volume': old_config.get('restore_last_volume', False)
        }

        # Save new format and remove old keys
        await self.settings_service.set_setting('volume', new_config)
        self.logger.info(f"Migration complete: {new_config}")

        return new_config

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
