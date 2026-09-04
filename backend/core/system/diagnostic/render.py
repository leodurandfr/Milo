# backend/core/system/diagnostic/render.py
"""Turning collected facts into a file one person can read and another can paste.

The size ceiling is not a taste: the report's destination is a GitHub issue body,
capped at 65 536 characters, and a report that has to be split or attached stops
being the one gesture it exists to be. Everything here serves that — where the
budget is spent, and where it is taken back when a unit is noisier than expected.
"""
from typing import Dict, Iterable, List, Sequence, Tuple

# 65 536 is the issue-body limit; the slack is the sentence the user writes above
# the report and the code fence they wrap it in.
MAX_REPORT_BYTES = 60_000

# Per-section ceilings. They add up to less than the total on purpose: the
# structured sections are small and variable, and the slack is theirs.
JOURNAL_BUDGET_BYTES = 20_000
ERRORS_LOG_BUDGET_BYTES = 8_000
SATELLITE_BUDGET_BYTES = 10_000
PREVIOUS_BOOT_BUDGET_BYTES = 2_000

# Per-unit journal caps, applied before the budget so one unit cannot arrive
# holding the whole window.
JOURNAL_LINES_PER_UNIT = 40
MAX_LINE_CHARS = 240


def cap_line(line: str) -> str:
    """One line, at most MAX_LINE_CHARS, with the cut marked."""
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[:MAX_LINE_CHARS - 1] + "…"


def keep_newest(lines: Sequence[str], budget: int) -> Tuple[List[str], int]:
    """The newest whole lines that fit in `budget` bytes, and how many were cut.

    Whole lines, and from the end: a report truncated mid-line reads as a
    corrupted file, and the half of a log that matters is the recent half.
    """
    kept: List[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line.encode("utf-8")) + 1
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return kept, len(lines) - len(kept)


def round_robin(per_unit: Dict[str, List[str]], budget: int) -> Dict[str, List[str]]:
    """Spend `budget` across units one line at a time, newest first.

    Straight concatenation is what this avoids. Measured on this unit:
    snapserver alone writes 34 KB of the 42 KB the whole system logs in half an
    hour, so a first-come fill hands it the entire journal budget and the
    thirteen other units — the ones a fault is usually in — arrive empty. Taking
    one line from each in turn costs nothing and makes a quiet unit's three lines
    unlosable.
    """
    remaining = {unit: list(reversed(lines)) for unit, lines in per_unit.items() if lines}
    picked: Dict[str, List[str]] = {unit: [] for unit in remaining}
    used = 0

    while remaining:
        for unit in list(remaining):
            queue = remaining[unit]
            if not queue:
                del remaining[unit]
                continue
            line = queue[0]
            cost = len(line.encode("utf-8")) + 1
            if used + cost > budget:
                # The budget is spent; every remaining line of every unit is cut.
                return {unit: list(reversed(lines)) for unit, lines in picked.items() if lines}
            picked[unit].append(queue.pop(0))
            used += cost

    return {unit: list(reversed(lines)) for unit, lines in picked.items() if lines}


def section(title: str, body: str) -> str:
    """One delimited block. The delimiter is what makes the file skimmable."""
    return f"===== {title} =====\n{body.rstrip()}\n"


def unavailable_section(title: str, reason: str) -> str:
    """A section that could not be collected — present, and saying so.

    It is never dropped: a missing heading reads as "nothing to report here",
    which is the opposite of what a failed probe means.
    """
    return section(title, f"NOT COLLECTED — {reason}")


def fit_report(blocks: Iterable[str], limit: int = MAX_REPORT_BYTES) -> Tuple[str, int]:
    """Join the blocks, and if the whole still overruns, cut the tail and say so.

    Returns the text and the number of bytes dropped. Reaching this means a
    per-section budget was under-estimated, so the note names the number rather
    than hiding a silent truncation.
    """
    text = "\n".join(blocks)
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, 0

    note = "\n===== TRUNCATED =====\nreport exceeded {n} bytes; {d} bytes cut from the end\n"
    # Reserve the note, then cut back to a line boundary so the file never ends
    # mid-line.
    room = limit - len(note.format(n=limit, d=0).encode("utf-8")) - 16
    cut = raw[:room].rsplit(b"\n", 1)[0]
    dropped = len(raw) - len(cut)
    return cut.decode("utf-8", errors="ignore") + note.format(n=limit, d=dropped), dropped
