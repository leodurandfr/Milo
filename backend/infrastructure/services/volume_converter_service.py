# backend/infrastructure/services/volume_converter_service.py
"""
Volume conversion service for ALSA <-> Display volume transformations.
Handles the mathematical conversion between raw ALSA percentages and user-facing display volumes.
"""


class VolumeConverterService:
    """Service for converting between ALSA and display volume scales."""

    def __init__(self, alsa_min: int = 0, alsa_max: int = 65):
        """
        Initialize converter with ALSA limits.

        Args:
            alsa_min: Minimum ALSA volume (default 0)
            alsa_max: Maximum ALSA volume (default 65)
        """
        self._alsa_min = alsa_min
        self._alsa_max = alsa_max

    def update_limits(self, alsa_min: int, alsa_max: int) -> None:
        """Update ALSA limits (called when config changes)."""
        self._alsa_min = alsa_min
        self._alsa_max = alsa_max

    @property
    def alsa_min(self) -> int:
        """Get current ALSA minimum."""
        return self._alsa_min

    @property
    def alsa_max(self) -> int:
        """Get current ALSA maximum."""
        return self._alsa_max

    @property
    def alsa_range(self) -> int:
        """Get ALSA volume range."""
        return self._alsa_max - self._alsa_min

    def alsa_to_display(self, alsa_volume: int) -> int:
        """
        Convert ALSA raw volume to display percentage (0-100%).

        Args:
            alsa_volume: Raw ALSA volume value

        Returns:
            Display volume as integer (0-100)
        """
        normalized = alsa_volume - self._alsa_min
        return round((normalized / self.alsa_range) * 100)

    def display_to_alsa(self, display_volume: int) -> int:
        """
        Convert display percentage to ALSA raw volume.

        Args:
            display_volume: Display volume (0-100)

        Returns:
            ALSA raw volume value
        """
        return round((display_volume / 100) * self.alsa_range) + self._alsa_min

    def display_to_alsa_precise(self, display_volume: float) -> int:
        """
        Convert precise display volume (float) to ALSA raw volume.

        Args:
            display_volume: Precise display volume as float

        Returns:
            ALSA raw volume value
        """
        return round((display_volume / 100.0) * self.alsa_range) + self._alsa_min

    def display_to_alsa_with_old_limits(self, display_volume: int, old_min: int, old_max: int) -> int:
        """
        Convert display volume using old ALSA limits (for limit change migration).

        Args:
            display_volume: Display volume (0-100)
            old_min: Previous ALSA minimum
            old_max: Previous ALSA maximum

        Returns:
            ALSA raw volume using old limits
        """
        old_range = old_max - old_min
        return round((display_volume / 100) * old_range) + old_min

    def clamp_display(self, display_volume: float) -> float:
        """
        Clamp display volume to valid range (0-100%).

        Args:
            display_volume: Volume to clamp

        Returns:
            Clamped volume (0.0-100.0)
        """
        return max(0.0, min(100.0, display_volume))

    def clamp_alsa(self, alsa_volume: int) -> int:
        """
        Clamp ALSA volume to configured min/max range.

        Args:
            alsa_volume: ALSA volume to clamp

        Returns:
            Clamped ALSA volume
        """
        return max(self._alsa_min, min(self._alsa_max, alsa_volume))

    @staticmethod
    def round_half_up(value: float) -> int:
        """
        Standard mathematical rounding (half-up).

        Args:
            value: Float value to round

        Returns:
            Rounded integer
        """
        return int(value + 0.5)
