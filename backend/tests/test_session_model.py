"""The session phase table (core/models/session.py): whole, and strict.

Every migrated source moves its session through `next_phase` and ends it
through `check_end`, so a pair missing from the table is a transition some
source will raise on in production, and a pair the table allows by accident is
a state the screen will draw. Both directions are pinned here.
"""
import pytest

from itertools import permutations
from typing import Dict, FrozenSet, Tuple

from backend.core.models.session import (
    ENDS, TRANSITIONS, EndReason, IllegalTransition, Phase, PhaseEvent, Session,
    check_end, event_towards, next_phase,
)


def table_gaps(
    transitions: Dict[Tuple[Phase, PhaseEvent], Phase],
    ends: Dict[Phase, FrozenSet[EndReason]],
) -> list[str]:
    """What the table leaves undecided: a phase with no way on or no way out, an
    event nothing reacts to, a reason no phase can end with. Empty when whole."""
    gaps = []
    for phase in Phase:
        if not any(p is phase for p, _ in transitions):
            gaps.append(f"no event moves {phase.value}")
        if not ends.get(phase):
            gaps.append(f"{phase.value} cannot end")
    for event in PhaseEvent:
        if not any(e is event for _, e in transitions):
            gaps.append(f"{event.value} is never handled")
    for reason in EndReason:
        if not any(reason in r for r in ends.values()):
            gaps.append(f"{reason.value} ends nothing")
    return gaps


def test_the_table_decides_every_phase_event_and_reason():
    assert table_gaps(TRANSITIONS, ENDS) == []


def test_a_table_missing_an_entry_is_reported():
    """The completeness check itself, seen red: drop one transition and one end."""
    fewer = {k: v for k, v in TRANSITIONS.items() if k[1] is not PhaseEvent.RESUMED}
    shorter = {**ENDS, Phase.CONNECTED: frozenset()}
    gaps = table_gaps(fewer, shorter)
    assert "resumed is never handled" in gaps
    assert "connected cannot end" in gaps


@pytest.mark.parametrize("phase,event,expected", [
    (Phase.LOADING, PhaseEvent.SOUND_STARTED, Phase.PLAYING),
    (Phase.LOADING, PhaseEvent.PAUSED, Phase.PAUSED),
    (Phase.PLAYING, PhaseEvent.TRACK_CHANGE, Phase.LOADING),
    (Phase.PAUSED, PhaseEvent.STALLED, Phase.LOADING),
    (Phase.CONNECTED, PhaseEvent.SOUND_STARTED, Phase.PLAYING),
    (Phase.PAUSED, PhaseEvent.STATE_WITHDRAWN, Phase.CONNECTED),
])
def test_allowed_moves(phase, event, expected):
    assert next_phase(phase, event) is expected


@pytest.mark.parametrize("phase,event", [
    (Phase.LOADING, PhaseEvent.RESUMED),       # nothing to resume before sound
    (Phase.CONNECTED, PhaseEvent.STALLED),     # nothing plays, so nothing stalls
    (Phase.PAUSED, PhaseEvent.SOUND_STARTED),  # a paused player resumes, it does not start
])
def test_a_pair_the_table_lacks_raises(phase, event):
    with pytest.raises(IllegalTransition):
        next_phase(phase, event)


def test_ends_follow_the_phase():
    check_end(Phase.PAUSED, EndReason.IDLE_TIMEOUT)
    with pytest.raises(IllegalTransition):
        check_end(Phase.CONNECTED, EndReason.IDLE_TIMEOUT)   # no idle timeout on CONNECTED
    with pytest.raises(IllegalTransition):
        check_end(Phase.PLAYING, EndReason.LOAD_FAILED)      # sound already left


def test_a_session_advances_through_the_table_and_is_its_own_token():
    session = Session(phase=Phase.LOADING)
    assert session.advance(PhaseEvent.SOUND_STARTED) is Phase.PLAYING
    with pytest.raises(IllegalTransition):
        session.advance(PhaseEvent.RESUMED)
    assert session.phase is Phase.PLAYING
    assert Session(phase=Phase.LOADING) != Session(phase=Phase.LOADING)


@pytest.mark.parametrize("before,target", list(permutations(Phase, 2)), ids=lambda p: p.value)
def test_a_daemon_s_report_is_reached_in_one_step(before, target):
    """A daemon reports where its session stands, not what happened to it
    (reconcile). Measured on shairport-sync 5.5.1, a sender goes from any
    phase to any other: connected before it streams (CONNECTED → LOADING), its
    stream type known only at the first frame (LOADING → CONNECTED for a
    Realtime one). A pair with no event would leave the session behind what
    the daemon said."""
    assert next_phase(before, event_towards(before, target)) is target


@pytest.mark.parametrize("phase,reason", [
    (Phase.LOADING, EndReason.SENDER_LEFT),    # a sender leaving before its first frame
    (Phase.CONNECTED, EndReason.REROUTE),      # a multiroom toggle under a Mac's stream
])
def test_a_daemon_held_session_ends_where_a_sender_can_leave_it(phase, reason):
    check_end(phase, reason)
