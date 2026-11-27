# backend/tests/test_stderr_priority.py
"""The backend's stderr line carries its level where journald reads one.

What breaks when these fail: journald files every stderr line at PRIORITY 6
whatever it says, so without the `<N>` prefix the logs page's warning read
(`journalctl -p warning`, core/system/logs/journal.py) holds none of the
backend's warnings — and with a prefix on the first line only, it holds a
record without its traceback. Printed in a terminal (a dev run), a `<4>` is
noise on every line, so it is printed only when stderr is the journal.

journald's side is the outside world, faked as it was measured (systemd 257,
a transient unit writing to stderr, 2026-11-27): a line opening with `<N>`
is stored with PRIORITY=N and its MESSAGE without the prefix; a line without
one at PRIORITY 6.
"""
import logging
import os
import re
import sys
from pathlib import Path

import pytest

from backend.core.log_handler import stderr_formatter
from backend.core.system.logs.entries import parse_journal

_PREFIX = re.compile(r"^<(?P<priority>[0-7])>(?P<message>.*)$", re.S)


def _journald(text: str, at_us: int = 1_795_000_000_000_000):
    """The entries journald stores for what a process wrote on its stderr."""
    entries = []
    for i, line in enumerate(text.split("\n")):
        prefixed = _PREFIX.match(line)
        entries.append({
            "__REALTIME_TIMESTAMP": str(at_us + i * 10),
            "_SYSTEMD_UNIT": "milo-backend.service",
            "_PID": "4242",
            "PRIORITY": prefixed.group("priority") if prefixed else "6",
            "MESSAGE": prefixed.group("message") if prefixed else line,
        })
    return entries


def _record(level: int, message: str, *, with_traceback: bool = False) -> logging.LogRecord:
    exc_info = None
    if with_traceback:
        try:
            raise ValueError("bad frame")
        except ValueError:
            exc_info = sys.exc_info()
    return logging.LogRecord("backend.shared.mpv", level, __file__, 1, message, None, exc_info)


@pytest.fixture
def under_journald(monkeypatch):
    """stderr is the stream systemd connected: JOURNAL_STREAM names it."""
    stat = os.fstat(2)
    monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino}")


def test_under_journald_a_warning_and_its_traceback_survive_a_read_at_warning(under_journald):
    text = stderr_formatter().format(_record(logging.WARNING, "mpv IPC refused", with_traceback=True))

    entries = _journald(text)
    at_warning = [e for e in entries if int(e["PRIORITY"]) <= 4]

    assert len(at_warning) == len(entries) > 2
    [line] = parse_journal(at_warning)
    assert (line.level, line.logger) == ("warning", "backend.shared.mpv")
    assert line.message.startswith("mpv IPC refused\nTraceback (most recent call last):")
    assert line.message.endswith("ValueError: bad frame")


@pytest.mark.parametrize("level, priority", [
    (logging.DEBUG, 7), (logging.INFO, 6), (logging.WARNING, 4),
    (logging.ERROR, 3), (logging.CRITICAL, 2),
])
def test_under_journald_the_priority_is_the_records_level(under_journald, level, priority):
    """syslog's numbers, which journald reads: `-p warning` is 0..4."""
    [entry] = _journald(stderr_formatter().format(_record(level, "x")))
    assert int(entry["PRIORITY"]) == priority
    # The message is the line Python's default format writes: what
    # entries.classify and the source-state grep read.
    assert entry["MESSAGE"] == f"{logging.getLevelName(level)}:backend.shared.mpv:x"


@pytest.mark.parametrize("stream", [None, "1:1", "not a stream"])
def test_outside_the_journal_no_line_carries_a_prefix(monkeypatch, stream):
    """A run in a terminal inherits no JOURNAL_STREAM; a stderr redirected
    elsewhere inherits one that names another file."""
    if stream is None:
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    else:
        monkeypatch.setenv("JOURNAL_STREAM", stream)

    text = stderr_formatter().format(_record(logging.ERROR, "boom", with_traceback=True))

    assert text.startswith("ERROR:backend.shared.mpv:boom\nTraceback")
    assert not any(_PREFIX.match(line) for line in text.split("\n"))


def test_the_backends_stderr_handler_is_the_one_that_carries_it():
    """main.py's root handler is built with this formatter: the tests above
    prove a formatter nothing installs."""
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "_stderr_handler.setFormatter(stderr_formatter())" in source
    assert re.search(r"logging\.basicConfig\([^)]*handlers=\[_stderr_handler\]", source)
