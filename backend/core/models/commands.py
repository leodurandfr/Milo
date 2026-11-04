"""Command parameters shared by several sources, and the strict request base.

A command whose meaning does not depend on the source is declared once, here;
a source-specific one lives in `sources/{s}/models.py`. `StrictModel` is the
base of every params model and of the `/api/settings/*` bodies.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from backend.core.models.audio_wire import RepeatMode


class StrictModel(BaseModel):
    """A request model that refuses a key it does not declare.

    Pydantic drops an unknown key by default, and the field it was meant for
    falls back to its default: a command param or a settings field renamed on
    one side only becomes a request that succeeds and does something else, or
    nothing. Every command's params (`COMMANDS`, answered 400 by
    `run_source_command`) and every `/api/settings/*` body (answered 422)
    derive from this; test_command_contract.py and test_wire_conventions.py
    hold both sets to it.
    """
    model_config = ConfigDict(extra="forbid")


class SkipParams(StrictModel):
    """Params for `skip`: move the playhead by `seconds` (signed) from where it
    is when the source handles the command.

    Relative on purpose. A client adding 30 s to the last anchor it was sent
    aims two quick presses at the same second, since the anchor the first one
    causes has not reached it yet (measured from Milo-iOS, 2026-09-25). The
    source handles one command at a time, so skips in a burst add up.
    """
    seconds: float


class SetShuffleParams(StrictModel):
    """Params for `set_shuffle`: the desired shuffle state (the player's toggle
    sends the target, not a flip, so a stale click can't invert it)."""
    shuffle: bool


class SetRepeatParams(StrictModel):
    """Params for `set_repeat`: the target repeat mode (the player's button
    cycles off → context → track and sends where it lands)."""
    mode: RepeatMode


def skip_target(from_ms: Optional[int], seconds: float, duration_ms: Optional[int]) -> int:
    """Where a skip of `seconds` from `from_ms` lands, bounded to
    [0, duration_ms] (no upper bound while the duration is unknown)."""
    target = (from_ms or 0) + round(seconds * 1000)
    if duration_ms:
        target = min(target, duration_ms)
    return max(0, target)
