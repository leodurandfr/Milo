"""The listening-session vocabulary every source migrates onto.

Six axes, one owner each (docs: source architecture, "the six axes"): the
selection belongs to the state machine; the service, the session, its position
anchor and the resume point to the source; the device to each source. This
module holds the types for the axes the sources own and the one table that
says how a session's phase may move and how it may end.

Nothing here reaches the wire yet: the old projection stays in force until the
sources are migrated and the wire switches in one step.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple
from uuid import uuid4


class ServiceState(str, Enum):
    """The source's program, from start() to stop() — never the session."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


class Phase(str, Enum):
    """Where a live session stands.

    CONNECTED is a session nothing can say is playing or paused: a Mac sending
    its output (silence included), a phone with no AVRCP player. It is shown as
    "connected to X", never as paused, and no idle timeout applies to it.
    """
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    CONNECTED = "connected"


class PhaseEvent(str, Enum):
    """What a player or a sender reports, in the table's words."""
    SOUND_STARTED = "sound_started"      # playback-restart, will_play → playing, pffr
    PAUSED = "paused"                    # a pause, or a load that lands paused
    RESUMED = "resumed"
    SEEK = "seek"
    TRACK_CHANGE = "track_change"
    STATE_WITHDRAWN = "state_withdrawn"  # the sender stops publishing a play state


class EndReason(str, Enum):
    """Why a session ended. Named, logged, and what the resume policy reads.

    A metadata feed dying is not an end: it is reported once as `feed_lost` and
    changes no state. LOAD_FAILED is before the first sound, STREAM_LOST after.
    """
    EOF = "eof"
    USER_STOP = "user_stop"
    IDLE_TIMEOUT = "idle_timeout"
    SOURCE_SWITCH = "source_switch"
    REROUTE = "reroute"
    SENDER_LEFT = "sender_left"
    DAEMON_DIED = "daemon_died"
    LOAD_FAILED = "load_failed"
    STREAM_LOST = "stream_lost"
    STORAGE_GONE = "storage_gone"


class IllegalTransition(ValueError):
    """A (phase, event) or (phase, end) pair the table does not allow."""


TRANSITIONS: Dict[Tuple[Phase, PhaseEvent], Phase] = {
    (Phase.LOADING, PhaseEvent.SOUND_STARTED): Phase.PLAYING,
    (Phase.LOADING, PhaseEvent.PAUSED): Phase.PAUSED,
    (Phase.PLAYING, PhaseEvent.PAUSED): Phase.PAUSED,
    (Phase.PLAYING, PhaseEvent.TRACK_CHANGE): Phase.LOADING,
    (Phase.PLAYING, PhaseEvent.SEEK): Phase.LOADING,
    (Phase.PAUSED, PhaseEvent.RESUMED): Phase.PLAYING,
    (Phase.PAUSED, PhaseEvent.SEEK): Phase.PAUSED,
    # A sender that publishes a play state leaves CONNECTED; one that stops
    # publishing it goes back — from whichever state it had published.
    (Phase.CONNECTED, PhaseEvent.SOUND_STARTED): Phase.PLAYING,
    (Phase.CONNECTED, PhaseEvent.PAUSED): Phase.PAUSED,
    (Phase.CONNECTED, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
    (Phase.PLAYING, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
    (Phase.PAUSED, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
}

_ENDED_BY_MILO_OR_DAEMON = frozenset({
    EndReason.USER_STOP, EndReason.SOURCE_SWITCH, EndReason.REROUTE,
    EndReason.DAEMON_DIED,
})

ENDS: Dict[Phase, FrozenSet[EndReason]] = {
    Phase.LOADING: _ENDED_BY_MILO_OR_DAEMON | {EndReason.LOAD_FAILED},
    # Every LOADING end but LOAD_FAILED, which is "before the first sound" by
    # definition: a load failing after sound left is STREAM_LOST.
    Phase.PLAYING: _ENDED_BY_MILO_OR_DAEMON | {
        EndReason.EOF, EndReason.STREAM_LOST, EndReason.STORAGE_GONE,
        EndReason.SENDER_LEFT,
    },
    Phase.PAUSED: _ENDED_BY_MILO_OR_DAEMON | {
        EndReason.IDLE_TIMEOUT, EndReason.SENDER_LEFT,
    },
    Phase.CONNECTED: frozenset({
        EndReason.SENDER_LEFT, EndReason.USER_STOP, EndReason.SOURCE_SWITCH,
        EndReason.DAEMON_DIED,
    }),
}


def next_phase(phase: Phase, event: PhaseEvent) -> Phase:
    """The phase `event` moves `phase` to; a pair the table lacks raises."""
    try:
        return TRANSITIONS[(phase, event)]
    except KeyError:
        raise IllegalTransition(f"{event.value} is not allowed in {phase.value}") from None


def check_end(phase: Phase, reason: EndReason) -> None:
    """Raise when a session in `phase` cannot end for `reason`."""
    if reason not in ENDS[phase]:
        raise IllegalTransition(f"a {phase.value} session cannot end with {reason.value}")


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


@dataclass(eq=False)
class Session:
    """One listening session. Its identity is the generation token: a timer,
    a result or a feed message carrying a session that is no longer current is
    stale and dropped. Sources subclass it to add their typed content."""
    phase: Phase
    id: str = field(default_factory=lambda: uuid4().hex)

    def advance(self, event: PhaseEvent) -> Phase:
        self.phase = next_phase(self.phase, event)
        return self.phase


@dataclass(frozen=True)
class ResumePoint:
    """What "play" would bring back between two sessions."""
    identity: str
    position_ms: int
    captured_at: float
    reason: EndReason
    content: Any = None


class IdlePolicy(str, Enum):
    """What a long pause does to a session."""
    AUTO_STOP = "auto_stop"                   # Milō ends it (IDLE_TIMEOUT)
    REQUEST_END = "request_end"               # Milō asks the daemon; the end comes back
    KEEP_WHILE_LINKED = "keep_while_linked"   # kept as long as the sender is linked
    NONE = "none"                             # CONNECTED sessions: a pause is not visible


class ReroutePolicy(str, Enum):
    """What a multiroom toggle does to a live session."""
    KEEP_SESSION = "keep_session"                  # only the writer moves (Spotify, Bluetooth)
    RESTART_AND_RESTORE = "restart_and_restore"    # same content, position and phase
    END_SESSION = "end_session"                    # the service forces a reconnect


@dataclass(frozen=True)
class ResumePolicy:
    """Which ends keep a resume point, which forget it, and for how long."""
    capture_on: FrozenSet[EndReason]
    forget_on: FrozenSet[EndReason]
    ttl_s: Optional[float] = None
    restore_on_start: bool = False
