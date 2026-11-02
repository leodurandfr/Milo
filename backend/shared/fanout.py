# backend/shared/fanout.py
"""Reporting for a fan-out across multiroom members.

One writer for the line that tells the operator *which speaker* did not take a
setting. Every zone fan-out reports through it — the zone EQ record, a targeted
edit of it (a band, the compressor, the loudness, mono), the zone crossover and
the zone's master EQ toggle — so a member that refused reads the same way
whichever one dropped it.
"""
import logging
from typing import Sequence


def failed_members(
    logger: logging.Logger, context: str, members: Sequence, results: Sequence
) -> list:
    """Name the members a fan-out did not reach, one log line each.

    ``results`` is positional against ``members`` and may hold either verdicts or
    the exceptions ``asyncio.gather(return_exceptions=True)`` hands back — both
    directions are silent unless read, which is the whole class of bug this
    exists for. The level is error: this line is what tells the operator which
    speaker kept the old setting, and it is what raises the UI's backend-error
    banner through WebSocketLogHandler.
    """
    failed = []
    for member, result in zip(members, results):
        if isinstance(result, BaseException):
            logger.error(f"{context} not applied to {member}: {result}")
            failed.append(member)
        elif not result:
            logger.error(f"{context} not applied to {member}")
            failed.append(member)
    return failed
