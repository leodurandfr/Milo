# backend/core/system/logs/models.py
"""What `GET /api/system/logs` and `GET /api/system/logs/context` answer.

Every string a line carries — a message, a template, a sample, a reason — has
been through the diagnostic's Redactor before it is put in one of these.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel


class LogLineOut(BaseModel):
    at: datetime
    level: str
    service: str
    logger: Optional[str] = None
    message: str


class LogGroupOut(BaseModel):
    """One event, however many times it happened in the window."""
    template: str
    level: str
    service: str
    logger: Optional[str] = None
    count: int
    first_at: datetime
    last_at: datetime
    # The newest occurrence, whole.
    sample: str


class NotCollected(BaseModel):
    """A place lines are read from that answered nothing, and why."""
    source: str
    reason: str


class LogsPage(BaseModel):
    since: datetime
    until: datetime
    groups: List[LogGroupOut]
    # How many groups matched before `limit` cut them.
    total_groups: int
    # Every service a line was read from in the window, for the filter.
    services: List[str]
    lines_read: int
    # The journal read stopped at its cap: its lines older than
    # `journal_from` are not in (errors.log's still are).
    truncated: bool
    journal_from: Optional[datetime] = None
    not_collected: List[NotCollected]


class RunningBuild(BaseModel):
    version: Optional[str] = None
    commit: Optional[str] = None


class SourceStateAt(BaseModel):
    """The audio source at a moment, and how that is known.

    `logged_transition`: the last `Transition completed` the backend logged
    before the moment; `failed_transition`: the last transition logged before
    it failed — its target stayed selected, its service failed;
    `backend_started`: the backend started after its last transition and
    logged none before the moment, so nothing was selected (`source` null); `current`: no transition is in the journal before the
    moment (it reaches back two days on a unit) — the state as it is now,
    which may not be what it was then.
    """
    basis: Literal["logged_transition", "failed_transition", "backend_started", "current"]
    source: Optional[str] = None
    at: Optional[datetime] = None
    detail: str


class LogContext(BaseModel):
    at: datetime
    # Where the event sits in `lines`, or null when there is no line at all.
    event_index: Optional[int] = None
    # The event's own text, redacted with `lines` (one Redactor, so a label
    # found by shape is the same in both); null when no line is exactly at
    # `at` and `event_index` marks the nearest one.
    sample: Optional[str] = None
    lines: List[LogLineOut]
    build: RunningBuild
    source_state: SourceStateAt
    not_collected: List[NotCollected]
