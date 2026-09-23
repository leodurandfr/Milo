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
    STALLED = "stalled"                  # the stream stopped delivering (mpv: paused-for-cache)
    STATE_WITHDRAWN = "state_withdrawn"  # the sender stops publishing a play state
    STREAM_OPENED = "stream_opened"      # a connected sender starts a stream (AirPlay pbeg)


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
    # Measured on mpv 0.40: a stream that stops delivering sets
    # paused-for-cache and announces nothing else, for minutes.
    (Phase.PLAYING, PhaseEvent.STALLED): Phase.LOADING,
    (Phase.PAUSED, PhaseEvent.RESUMED): Phase.PLAYING,
    # Unpaused, but nothing to play yet (the file is not open, the cache is dry).
    (Phase.PAUSED, PhaseEvent.STALLED): Phase.LOADING,
    (Phase.PAUSED, PhaseEvent.SEEK): Phase.PAUSED,
    # A sender that publishes a play state leaves CONNECTED; one that stops
    # publishing it goes back — from whichever state it had published.
    (Phase.CONNECTED, PhaseEvent.SOUND_STARTED): Phase.PLAYING,
    (Phase.CONNECTED, PhaseEvent.PAUSED): Phase.PAUSED,
    (Phase.CONNECTED, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
    (Phase.PLAYING, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
    (Phase.PAUSED, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
    # Measured on shairport-sync 5.5.1: a sender connects before it streams,
    # and which kind of stream it opened (one that reports its pauses, or not)
    # is known only once sound arrives.
    (Phase.CONNECTED, PhaseEvent.STREAM_OPENED): Phase.LOADING,
    (Phase.LOADING, PhaseEvent.STATE_WITHDRAWN): Phase.CONNECTED,
}

_ENDED_BY_MILO_OR_DAEMON = frozenset({
    EndReason.USER_STOP, EndReason.SOURCE_SWITCH, EndReason.REROUTE,
    EndReason.DAEMON_DIED,
})

# What ends content, whatever the phase it ends in: the file or the stream it
# reads from gives out. LOADING is not only "before the first sound" — a stall
# after it (STREAM_LOST), the next track of a queue opening (EOF when it is
# empty, STORAGE_GONE when its key left) are LOADING too; so is a PAUSED
# session whose paused load fails, or whose storage is pulled.
_ENDED_BY_CONTENT = frozenset({
    EndReason.EOF, EndReason.STREAM_LOST, EndReason.STORAGE_GONE,
})

ENDS: Dict[Phase, FrozenSet[EndReason]] = {
    # A sender can leave in the half-second between its stream opening and
    # its first sound.
    Phase.LOADING: _ENDED_BY_MILO_OR_DAEMON | _ENDED_BY_CONTENT | {
        EndReason.LOAD_FAILED, EndReason.SENDER_LEFT,
    },
    # Every LOADING end but LOAD_FAILED, which is "before the first sound" by
    # definition: a load failing after sound left is STREAM_LOST.
    Phase.PLAYING: _ENDED_BY_MILO_OR_DAEMON | _ENDED_BY_CONTENT | {EndReason.SENDER_LEFT},
    Phase.PAUSED: _ENDED_BY_MILO_OR_DAEMON | {
        EndReason.IDLE_TIMEOUT, EndReason.SENDER_LEFT, EndReason.LOAD_FAILED,
        EndReason.STREAM_LOST, EndReason.STORAGE_GONE,
    },
    Phase.CONNECTED: frozenset({
        EndReason.SENDER_LEFT, EndReason.USER_STOP, EndReason.SOURCE_SWITCH,
        EndReason.DAEMON_DIED, EndReason.REROUTE,
    }),
}


def next_phase(phase: Phase, event: PhaseEvent) -> Phase:
    """The phase `event` moves `phase` to; a pair the table lacks raises."""
    try:
        return TRANSITIONS[(phase, event)]
    except KeyError:
        raise IllegalTransition(f"{event.value} is not allowed in {phase.value}") from None


def event_towards(phase: Phase, target: Phase) -> PhaseEvent:
    """The one event that moves `phase` to `target`, for a daemon that reports
    where its session stands rather than what happened to it. Whether the
    table allows the pair is next_phase's to say."""
    if target is Phase.PAUSED:
        return PhaseEvent.PAUSED
    if target is Phase.PLAYING:
        return PhaseEvent.RESUMED if phase is Phase.PAUSED else PhaseEvent.SOUND_STARTED
    if target is Phase.LOADING:
        return PhaseEvent.STREAM_OPENED if phase is Phase.CONNECTED else PhaseEvent.STALLED
    return PhaseEvent.STATE_WITHDRAWN


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
    stale and dropped. Sources subclass it to add their typed content.

    `heard` says whether sound ever left in this session — what tells a load
    that failed (LOAD_FAILED) from a stream that was lost (STREAM_LOST).

    For a session a daemon holds: `sender` names who it belongs to (a
    different sender is a different session), and `end_requested` is the
    reason Milō asked the daemon to end it for — the end that comes back,
    however it comes, is recorded under that reason.
    """
    phase: Phase
    id: str = field(default_factory=lambda: uuid4().hex)
    heard: bool = False
    sender: Optional[str] = None
    end_requested: Optional[EndReason] = None

    def advance(self, event: PhaseEvent) -> Phase:
        self.phase = next_phase(self.phase, event)
        if self.phase is Phase.PLAYING:
            self.heard = True
        return self.phase


@dataclass(frozen=True)
class DaemonSnapshot:
    """Where a daemon says its session stands: whose it is, and its phase.

    What `reconcile()` makes the live session match. None in its place means
    the daemon holds no session.
    """
    sender: Optional[str]
    phase: Phase


@dataclass(frozen=True)
class ResumePoint:
    """What "play" would bring back between two sessions.

    `phase` is the phase the session had when it ended: a multiroom reroute
    restores it (playing stays playing), every other restore lands paused.
    """
    identity: str
    position_ms: int
    captured_at: float
    reason: EndReason
    content: Any = None
    phase: Phase = Phase.PAUSED


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


class CommandScope(str, Enum):
    """What a command acts on, which decides whether it can run with no session.

    CONTENT starts something new; SESSION needs a live session and is refused
    without one; RESUME works from the resume point when there is no session
    (it restores, then acts); PREFERENCE holds without a session; DEVICE acts on
    the hardware, not on the session.
    """
    CONTENT = "content"
    SESSION = "session"
    RESUME = "resume"
    PREFERENCE = "preference"
    DEVICE = "device"


@dataclass(frozen=True)
class ResumePolicy:
    """Which ends keep a resume point, which forget it, and for how long."""
    capture_on: FrozenSet[EndReason]
    forget_on: FrozenSet[EndReason]
    ttl_s: Optional[float] = None
    restore_on_start: bool = False
