# backend/core/system/logs/grouping.py
"""Repeated lines, told apart from the line that happened once.

A unit that misbehaves says so every few seconds: "Navidrome did not answer
yet" forty times, a speaker's SYNC_RECONNECT for each retry. Listed line by
line, the one line that matters is pushed off the page. So lines are grouped
on a *template* — the message with what changes between two occurrences of
the same event masked: numbers (durations, counts, ports, positions), hex
ids, MAC and IP addresses — and a group is shown once, with its count and its
first and last occurrence.

Grouped per service and level as well: the same text from two units, or at
two levels, is two events.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from backend.core.system.logs.entries import LogLine

# In order: a value that holds digits is masked whole before the number rule
# could cut it into pieces (`3f2a9c1e` → `#f#a#c#e`).
_MASKS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.IGNORECASE), "<id>"),
    (re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b"), "<mac>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE), "<id>"),
    # A hex run of six or more holding a digit and a letter: a session id, a
    # commit, a token. Six letters alone are a word ("facade"), not an id.
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,}\b", re.IGNORECASE), "<id>"),
    # Any number, a duration's included (`3.0s`, `1857 ms`, `0,5`).
    (re.compile(r"\d+(?:[.,]\d+)?"), "#"),
)


def template(message: str) -> str:
    """The message with its varying values masked; its first line only, so a
    traceback groups on the record it follows."""
    text = message.split("\n", 1)[0]
    for pattern, mask in _MASKS:
        text = pattern.sub(mask, text)
    return text


@dataclass
class Group:
    template: str
    level: str
    service: str
    logger: Optional[str]
    count: int
    first_at: datetime
    last_at: datetime
    # The newest occurrence, whole: what the row unfolds and copies.
    sample: str


def group(lines: Sequence[LogLine]) -> List[Group]:
    """Groups of `lines` (any order), newest last occurrence first."""
    groups: Dict[Tuple[str, str, str], Group] = {}
    for line in lines:
        key = (line.service, line.level, template(line.message))
        found = groups.get(key)
        if found is None:
            groups[key] = Group(
                template=key[2], level=line.level, service=line.service,
                logger=line.logger, count=1, first_at=line.at, last_at=line.at,
                sample=line.message,
            )
            continue
        found.count += 1
        if line.at < found.first_at:
            found.first_at = line.at
        if line.at >= found.last_at:
            found.last_at = line.at
            found.sample = line.message
            found.logger = line.logger
    return sorted(groups.values(), key=lambda g: g.last_at, reverse=True)
