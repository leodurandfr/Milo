# backend/core/models/volume.py
"""
Volume configuration domain model.

VolumeConfig carries the volume limits and steps in memory; it does not declare
them. `SettingsService.defaults['volume']` does, and `VolumeService._load_volume_config`
fills this dataclass from it. The field defaults below only apply to an instance
built with no arguments — a test, or the pre-load value in `VolumeService.__init__`
— and must stay equal to that section.

What this model *does* own is the clamp and the scale: every volume operation goes
through config.clamp() for limit enforcement, and every crossing of the wire goes
through normalize/denormalize. The two conversions are reciprocal and live here
together so that neither side can be re-implemented elsewhere — which is exactly
what happened when only the outgoing half existed and a client kept its own copy
of the limits.
"""
from dataclasses import dataclass
from typing import List

from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB


def normalize_volume(volume_db: float, min_db: float, max_db: float) -> float:
    """dB → 0..1 over the operator's `volume_limits`.

    Milō normalizes rather than the client because only Milō knows the limits,
    and they move: a slider calibrated against a hardcoded -80..0 would sit at
    a third of its travel on a unit limited to -78..-8, and would jump the day
    an operator changed them.

    Degenerate spans answer 0.0 rather than dividing by zero — the validator
    already refuses a span under 6 dB, so this is defence, not a real case.
    """
    span = max_db - min_db
    if span <= 0:
        return 0.0
    return round(min(1.0, max(0.0, (volume_db - min_db) / span)), 4)


def denormalize_volume(level: float, min_db: float, max_db: float) -> float:
    """0..1 → dB over the same span. The reciprocal of `normalize_volume`.

    The incoming half, and the reason a phone never has to know a decibel. A
    client that converts on its own converts on limits it cached, and a cache
    the operator can invalidate from the other end of the house is a wrong
    answer with no error: Milo-iOS held -80..-21 while the unit ran -78..-8,
    so the level it sent for a given slider position was off by `-2 - 11x` dB.

    Deliberately NOT rounded: the result goes straight into a clamp, and an
    arbitrary rounding here would be a second opinion on a value the volume
    path already owns. Degenerate spans answer `min_db`, the floor — the
    silent end, symmetric with the 0.0 the forward direction answers.
    """
    span = max_db - min_db
    if span <= 0:
        return min_db
    return min_db + min(1.0, max(0.0, level)) * span


@dataclass
class VolumeConfig:
    """
    In-memory volume configuration.

    All values are in decibels (dB).
    Range: -80 dB (silent) to 0 dB (maximum)
    """
    limit_min_db: float = -80.0
    limit_max_db: float = -20.0
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

    def move_level(self, level_db: float, delta_db: float) -> float:
        """One room's level after its group (a zone, the whole house) moved by `delta_db`.

        The rooms move together while they can; a room that meets a limit stops
        there and the others go on, so a group always reaches the minimum and the
        maximum. Stopping the whole group at its first room's limit kept the
        distances, but left a zone with one room at the floor unable to go down
        at all. The cost: rooms that met a limit together come back together,
        since no level is ever stored outside the limits.
        """
        return self.clamp(level_db + delta_db)

    def delta_to_average(self, levels: List[float], target_db: float) -> float:
        """The delta that lands the average of `levels` on `target_db`, each room
        moved by `move_level`.

        Once a room stops at a limit, the average moves more slowly than the
        delta: `target - average` would land short, and a slider released there
        would jump back. The average is linear between the deltas at which a
        room meets its limit, so the answer is exact: walk those points, nearest
        first, and interpolate on the segment that crosses the target.
        """
        target_db = self.clamp(target_db)
        average = sum(levels) / len(levels)
        if target_db == average:
            return 0.0
        down = target_db < average
        limit = self.clamp(self.limit_min_db if down else self.limit_max_db)
        stops = sorted({limit - level for level in levels
                        if (limit - level < 0 if down else limit - level > 0)}, key=abs)
        prev_delta, prev_average = 0.0, average
        for stop in stops:
            reached = sum(self.move_level(level, stop) for level in levels) / len(levels)
            if (reached <= target_db) if down else (reached >= target_db):
                return prev_delta + (target_db - prev_average) * (stop - prev_delta) / (reached - prev_average)
            prev_delta, prev_average = stop, reached
        return prev_delta

    def normalize(self, volume_db: float) -> float:
        """dB → 0..1 over this config's own limits."""
        return normalize_volume(volume_db, self.limit_min_db, self.limit_max_db)

    def denormalize(self, level: float) -> float:
        """0..1 → dB over this config's own limits."""
        return denormalize_volume(level, self.limit_min_db, self.limit_max_db)
