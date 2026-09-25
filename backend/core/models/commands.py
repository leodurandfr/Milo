"""Command parameters shared by several sources.

A command whose meaning does not depend on the source is declared once, here;
a source-specific one lives in `sources/{s}/models.py`.
"""
from typing import Optional

from pydantic import BaseModel


class SkipParams(BaseModel):
    """Params for `skip`: move the playhead by `seconds` (signed) from where it
    is when the source handles the command.

    Relative on purpose. A client adding 30 s to the last anchor it was sent
    aims two quick presses at the same second, since the anchor the first one
    causes has not reached it yet (measured from Milo-iOS, 2026-09-25). The
    source handles one command at a time, so skips in a burst add up.
    """
    seconds: float


def skip_target(from_ms: Optional[int], seconds: float, duration_ms: Optional[int]) -> int:
    """Where a skip of `seconds` from `from_ms` lands, bounded to
    [0, duration_ms] (no upper bound while the duration is unknown)."""
    target = (from_ms or 0) + round(seconds * 1000)
    if duration_ms:
        target = min(target, duration_ms)
    return max(0, target)
