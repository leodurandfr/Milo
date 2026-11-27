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


class ThrottleCondition(BaseModel):
    """One limit the Pi's firmware reports: whether it holds now, and
    whether it held at any moment since boot."""
    name: Literal["under_voltage", "frequency_capped", "throttled", "soft_temperature_limit"]
    now: bool
    since_boot: bool


class Throttling(BaseModel):
    """`vcgencmd get_throttled`, read with the page. Anything but 0 is a
    warning, a bit not named in `conditions` included."""
    value: int
    raw: str
    conditions: List[ThrottleCondition]


class LogsPage(BaseModel):
    since: datetime
    until: datetime
    groups: List[LogGroupOut]
    # How many groups matched before `limit` cut them.
    total_groups: int
    # Every service a line was read from in the window, for the filter.
    services: List[str]
    lines_read: int
    # A journal read stopped at its cap: from `journal_from` on, the window
    # is whole; before it, that read's lines are not in (the other reads',
    # errors.log's, the speakers' still are).
    truncated: bool
    journal_from: Optional[datetime] = None
    # Every line at warning and above is in all the same: only the read of
    # every level of `milo-*` was cut, beside the reads at warning that were
    # not (`journal.page_reads`).
    warnings_whole: bool = False
    # Null when it could not be read, which `not_collected` says.
    throttling: Optional[Throttling] = None
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
