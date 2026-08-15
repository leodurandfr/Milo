# backend/core/models/volume.py
"""
Volume configuration domain model.

VolumeConfig carries the volume limits and steps in memory; it does not declare
them. `SettingsService.defaults['volume']` does, and `VolumeService._load_volume_config`
fills this dataclass from it. The field defaults below only apply to an instance
built with no arguments — a test, or the pre-load value in `VolumeService.__init__`
— and must stay equal to that section.

What this model *does* own is the clamp: every volume operation goes through
config.clamp() for limit enforcement.
"""
from dataclasses import dataclass

from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB


@dataclass
class VolumeConfig:
    """
    In-memory volume configuration.

    All values are in decibels (dB).
    Range: -80 dB (silent) to 0 dB (maximum)
    """
    limit_min_db: float = -80.0
    limit_max_db: float = -20.0
    step_mobile_db: float = 2.0
    step_rotary_db: float = 2.0
    step_bt_remote_db: float = 2.0
    step_ir_remote_db: float = 2.0
    startup_volume_db: float = DEFAULT_VOLUME_DB
    restore_last_volume: bool = True

    def clamp(self, volume_db: float) -> float:
        """
        Clamp volume to configured user limits AND technical hard limits.

        Enforces both user-configurable limits (limit_min_db, limit_max_db)
        and technical hard limits (MIN_VOLUME_DB, MAX_VOLUME_DB).
        This is the ONLY method that should be used for volume clamping.

        Args:
            volume_db: Volume in dB to clamp

        Returns:
            Clamped volume in dB within safe bounds
        """
        # Apply user limits first, then enforce technical hard limits
        clamped = max(self.limit_min_db, min(self.limit_max_db, volume_db))
        return max(MIN_VOLUME_DB, min(MAX_VOLUME_DB, clamped))

    def to_dict(self) -> dict:
        """Convert config to dictionary for API responses."""
        return {
            "limit_min_db": self.limit_min_db,
            "limit_max_db": self.limit_max_db,
            "step_mobile_db": self.step_mobile_db,
            "step_rotary_db": self.step_rotary_db,
            "step_bt_remote_db": self.step_bt_remote_db,
            "step_ir_remote_db": self.step_ir_remote_db,
            "startup_volume_db": self.startup_volume_db,
            "restore_last_volume": self.restore_last_volume
        }
