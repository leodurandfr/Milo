# backend/domain/volume.py
"""
Volume configuration domain model.

VolumeConfig is the single source of truth for volume limits and settings.
All volume operations should use config.clamp() for limit enforcement.
"""
from dataclasses import dataclass


@dataclass
class VolumeConfig:
    """
    Volume configuration - SSOT for volume limits and settings.

    All values are in decibels (dB).
    Range: -80 dB (silent) to 0 dB (maximum)
    """
    limit_min_db: float = -80.0
    limit_max_db: float = -21.0
    step_mobile_db: float = 3.0
    step_rotary_db: float = 2.0
    startup_volume_db: float = -30.0
    restore_last_volume: bool = False

    def clamp(self, volume_db: float) -> float:
        """
        Clamp volume to configured dB limits.

        This is the ONLY method that should be used for volume clamping.

        Args:
            volume_db: Volume in dB to clamp

        Returns:
            Clamped volume in dB within [limit_min_db, limit_max_db]
        """
        return max(self.limit_min_db, min(self.limit_max_db, volume_db))

    def to_dict(self) -> dict:
        """Convert config to dictionary for API responses."""
        return {
            "limit_min_db": self.limit_min_db,
            "limit_max_db": self.limit_max_db,
            "step_mobile_db": self.step_mobile_db,
            "step_rotary_db": self.step_rotary_db,
            "startup_volume_db": self.startup_volume_db,
            "restore_last_volume": self.restore_last_volume
        }
