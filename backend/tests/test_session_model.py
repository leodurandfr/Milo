"""The session phase table (core/models/session.py): whole, and strict.

Every migrated source moves its session through `next_phase` and ends it
through `check_end`, so a pair missing from the table is a transition some
source will raise on in production, and a pair the table allows by accident is
a state the screen will draw. Both directions are pinned here.
"""
import pytest

from backend.core.models.session import (
    ENDS, TRANSITIONS, EndReason, IllegalTransition, Phase, PhaseEvent, Session,
    check_end, next_phase, table_gaps,
)


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
    (Phase.PLAYING, PhaseEvent.SEEK, Phase.LOADING),
    (Phase.PAUSED, PhaseEvent.SEEK, Phase.PAUSED),
    (Phase.CONNECTED, PhaseEvent.SOUND_STARTED, Phase.PLAYING),
    (Phase.PAUSED, PhaseEvent.STATE_WITHDRAWN, Phase.CONNECTED),
])
def test_allowed_moves(phase, event, expected):
    assert next_phase(phase, event) is expected


@pytest.mark.parametrize("phase,event", [
    (Phase.LOADING, PhaseEvent.RESUMED),       # nothing to resume before sound
    (Phase.CONNECTED, PhaseEvent.SEEK),        # nothing can say where it is
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
