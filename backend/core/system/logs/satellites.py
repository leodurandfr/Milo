# backend/core/system/logs/satellites.py
"""Each speaker's journal over a window, asked of the speaker itself.

A satellite's journal leaves it through its `GET /diagnostic/journal`
(milo-client/app/routes/diagnostic.py) and through nothing else: the window
asked, at warning and above, its own units and its kernel, each read capped
on the speaker, newest kept. Every speaker is asked in parallel, so the wall
clock is one speaker's however many there are; one the registry shows offline
is not asked at all, and says so, rather than waited on for the connect
timeout.

Unredacted, like the server's own reads: the caller applies its Redactor
(the logs page), or reads the lines on the unit itself (the triage).
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp

from backend.config.constants import CLIENT_API_PORT
from backend.core.system.logs.entries import LogLine, parse_satellite_reads
from backend.core.system.logs.models import NotCollected

# A speaker walks up to three reads of 8 s each on its SD card, in parallel,
# then sends some megabytes over the LAN when the window is a busy week.
SATELLITE_JOURNAL_TIMEOUT = 20.0
# A powered-off host sends no RST: nothing but this bounds the connect.
SATELLITE_CONNECT_TIMEOUT = 2.0


@dataclass(frozen=True)
class SatelliteTarget:
    """One speaker to ask: the label its lines are filed under, and where."""
    label: str
    mac_id: str
    ip: Optional[str]
    online: bool = True


@dataclass
class SatelliteJournal:
    """What one speaker answered: its lines, and what of the window is missing."""
    target: SatelliteTarget
    lines: List[LogLine] = field(default_factory=list)
    not_collected: List[NotCollected] = field(default_factory=list)


async def read_all(
    targets: Sequence[SatelliteTarget], *, since: datetime, until: datetime,
) -> List[SatelliteJournal]:
    """Every speaker's lines from `since` to `until`, one result per target."""
    asked = [t for t in targets if t.online and t.ip]
    results: Dict[str, SatelliteJournal] = {
        t.label: SatelliteJournal(t, not_collected=[NotCollected(
            source=f"speaker {t.label}", reason="offline in the registry, not asked",
        )])
        for t in targets if t not in asked
    }
    if asked:
        timeout = aiohttp.ClientTimeout(
            total=SATELLITE_JOURNAL_TIMEOUT, sock_connect=SATELLITE_CONNECT_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            answers = await asyncio.gather(
                *(_ask(session, t, since, until) for t in asked), return_exceptions=True,
            )
        for target, answer in zip(asked, answers):
            if isinstance(answer, BaseException):
                results[target.label] = SatelliteJournal(target, not_collected=[NotCollected(
                    source=f"speaker {target.label}", reason=_reason(answer),
                )])
            else:
                results[target.label] = _read(target, *answer)
    return [results[t.label] for t in targets]


async def _ask(
    session: aiohttp.ClientSession, target: SatelliteTarget, since: datetime, until: datetime,
) -> Tuple[List[Dict[str, Any]], Optional[bool]]:
    """The speaker's reads, and whether its account reads the whole journal."""
    # The path is spelled inline, not through a constant: the milo-client
    # contract test resolves satellite calls out of the f-string itself.
    url = f"http://{target.ip}:{CLIENT_API_PORT}/diagnostic/journal"
    params = {"since": f"{since.timestamp():.6f}", "until": f"{until.timestamp():.6f}"}
    async with session.get(url, params=params) as response:
        if response.status != 200:
            raise _Refused(response.status, (await response.text())[:200])
        answer = await response.json()
        return answer.get("reads") or [], answer.get("journal_readable")


class _Refused(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"answered HTTP {status}" + (f": {body}" if body else ""))


def _reason(error: BaseException) -> str:
    if isinstance(error, asyncio.TimeoutError):
        return f"no answer within {SATELLITE_JOURNAL_TIMEOUT:g} s"
    return str(error) or type(error).__name__


def _read(
    target: SatelliteTarget, reads: List[Dict[str, Any]], journal_readable: Optional[bool],
) -> SatelliteJournal:
    """A speaker's answer as lines, each read's failure or cut said by name."""
    journal = SatelliteJournal(target, lines=parse_satellite_reads(target.label, reads))
    source = f"speaker {target.label}"
    for read in reads:
        name = read.get("name", "?")
        if read.get("reason"):
            journal.not_collected.append(NotCollected(source=f"{source} {name}", reason=read["reason"]))
        elif read.get("cut"):
            journal.not_collected.append(NotCollected(
                source=f"{source} {name}",
                reason="cut at its cap on the speaker: the window's older lines are missing",
            ))
    if journal_readable is False:
        journal.not_collected.append(NotCollected(
            source=source,
            reason="account in neither adm nor systemd-journal: systemd and kernel lines are unreadable",
        ))
    return journal
