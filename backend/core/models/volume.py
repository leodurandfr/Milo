# backend/core/models/volume.py
"""
Volume configuration domain model.

VolumeConfig is the single source of truth for volume limits and settings.
All volume operations should use config.clamp() for limit enforcement.
"""
from dataclasses import dataclass

from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB


@dataclass
class VolumeConfig:
    """
    Volume configuration - SSOT for volume limits and settings.

    All values are in decibels (dB).
    Range: -80 dB (silent) to 0 dB (maximum)
    """
    limit_min_db: float = -80.0
    limit_max_db: float = -20.0
    step_mobile_db: float = 2.0
    step_rotary_db: float = 2.0
    step_bt_remote_db: float = 2.0
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
            "startup_volume_db": self.startup_volume_db,
            "restore_last_volume": self.restore_last_volume
        }
