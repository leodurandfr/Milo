# backend/shared/fanout.py
"""Reporting for a fan-out across multiroom members.

One reading of the results that tells the operator *which speaker* did not
take a setting, in two forms. ``failed_members`` is for a fan-out no route
answers member by member (the zone crossover, the snapclient config push): it
names each member at error. ``members_not_applied`` is for a fan-out a route
answers once (the zone EQ record, a targeted edit of it — a band, the
compressor, the loudness, mono — and the zone's master EQ toggle): each member
at warning, and one raise for the route's one line. A member that refused reads
the same way whichever one dropped it.
"""
import logging
from typing import TYPE_CHECKING, Callable, Optional, Sequence

if TYPE_CHECKING:
    from backend.core.equalizer.client_proxy import MembersNotApplied


def _line(context: str, member, result) -> str:
    if isinstance(result, BaseException):
        return f"{context} not applied to {member}: {result}"
    return f"{context} not applied to {member}"


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
        if isinstance(result, BaseException) or not result:
            logger.error(_line(context, member, result))
            failed.append(member)
    return failed


def members_not_applied(
    logger: logging.Logger,
    context: str,
    members: Sequence,
    results: Sequence,
    is_local: Callable[[str], bool],
) -> Optional["MembersNotApplied"]:
    """The members of a fan-out a route answers for that did not take it, by why.

    Read like ``failed_members``, but each member is said at warning, and the
    whole is handed back as one ``MembersNotApplied`` for the route to raise:
    its ``api_error_handler`` writes the request's one line — an error when a
    speaker refused, a warning when every miss is a satellite nothing reached.
    A band drag sends 20 requests a second, and a line per member plus the
    route's was N+1 errors each. None when every member took it.

    A satellite that answered a 4xx refused; one nothing reached is
    unreachable; anything else a satellite did (a False answer, a 500) is a
    refusal too, as is any miss of this unit (``is_local``).
    """
    # Imported here, not at the top: core.equalizer's package imports this
    # module (multiroom_service), so a top-level import made whichever of the
    # two was imported first — crossover.py imports this one first at boot —
    # a circular import that stopped the backend from starting.
    from backend.core.equalizer.client_proxy import MembersNotApplied, is_satellite_unreachable

    refused, unreachable, local_refused = [], [], False
    for member, result in zip(members, results):
        if not isinstance(result, BaseException) and result:
            continue
        logger.warning(_line(context, member, result))
        if is_local(member):
            local_refused = True
        elif is_satellite_unreachable(result):
            unreachable.append(member)
        else:
            refused.append(member)
    if not (refused or unreachable or local_refused):
        return None
    return MembersNotApplied(context, refused, unreachable, local_refused)
