# backend/infrastructure/services/volume_converter_service.py
"""
Volume conversion service for dB volume management.
All volume values are in decibels (-80 to 0 dB).
ALSA is always set to 100% passthrough - no conversion needed.
"""


class VolumeConverterService:
    """Service for dB volume operations."""

    def __init__(self, limit_min_db: float = -80.0, limit_max_db: float = -21.0):
        """
        Initialize converter with dB limits.

        Args:
            limit_min_db: Minimum volume in dB (default -80.0)
            limit_max_db: Maximum volume in dB (default -21.0)
        """
        self._limit_min_db = limit_min_db
        self._limit_max_db = limit_max_db

    def update_limits(self, min_db: float, max_db: float) -> None:
        """Update volume limits (called when config changes)."""
        self._limit_min_db = min_db
        self._limit_max_db = max_db

    @property
    def limit_min_db(self) -> float:
        """Get current minimum volume in dB."""
        return self._limit_min_db

    @property
    def limit_max_db(self) -> float:
        """Get current maximum volume in dB."""
        return self._limit_max_db

    def clamp_db(self, volume_db: float) -> float:
        """
        Clamp volume to configured dB limits.

        Args:
            volume_db: Volume in dB to clamp

        Returns:
            Clamped volume in dB
        """
        return max(self._limit_min_db, min(self._limit_max_db, volume_db))

    def db_to_percent(self, db: float) -> int:
        """
        Convert dB to percentage (for UI progress bar only).
        Maps -80 dB to 0% and 0 dB to 100%.

        Args:
            db: Volume in dB (-80 to 0)

        Returns:
            Percentage (0-100)
        """
        clamped = max(-80.0, min(0.0, db))
        return round(((clamped + 80.0) / 80.0) * 100.0)

    def percent_to_db(self, percent: float) -> float:
        """
        Convert percentage to dB.
        Maps 0% to -80 dB and 100% to 0 dB.

        Args:
            percent: Percentage (0-100)

        Returns:
            Volume in dB (-80 to 0)
        """
        clamped = max(0.0, min(100.0, percent))
        return -80.0 + (clamped / 100.0) * 80.0
