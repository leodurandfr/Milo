"""Diagnostic routes — the only way anything a satellite knows leaves it.

A satellite has no log surface in the Milō UI at all. Its journal, its ALSA
state and its unit states are reachable from the server over these two routes
and nowhere else, which makes it the half of a multiroom fault that is normally
invisible. The server asks for `/diagnostic` while building its own report and
pastes the answer in as a block; it asks for `/diagnostic/journal` for its logs
page, which reads a window of days where the block holds a few lines.

Everything here is bounded and nothing here fails the route: the server's own
timeout is short, and a probe that hangs must cost its own line rather than the
whole block. What cannot be collected is named in `unavailable`, which the
server prints under the satellite's heading.

The payload is three keys — `hostname`, `text`, `unavailable`. The satellite
renders its own block because the two trees are independent deployments (a
satellite update ships only `milo-client/`), so there is nothing to share with
the server's renderer even if we wanted to.

Redaction: a satellite holds no settings, no share list and no user-chosen name.
Its hostname comes from the image (`milo-client`, `milo-client-2`) and no route
renames it. So the whitelist here is the set of units admitted into the journal
— the same rule as the server's, for the same reason: NetworkManager and
wpa_supplicant log the SSID, and neither is in it.
"""
import asyncio
import contextlib
import grp
import json
import logging
import os
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException

from routes import health

logger = logging.getLogger(__name__)

# The satellite's own units, and nothing else. A glob would also catch a unit a
# future image installs under this prefix, which is the intent.
UNIT_GLOB = "milo-client*"

# Every probe is capped well under the server's 6 s total, so a slow one costs a
# line and the block still arrives.
PROBE_TIMEOUT = 2.0
JOURNAL_LINES_PER_UNIT = 30
MAX_LINE_CHARS = 240
# The server splits its satellite budget between however many answer, and cuts
# what overruns; keeping the block near that size means it rarely has to.
MAX_TEXT_BYTES = 5_000


async def _run(args: Sequence[str], timeout: float = PROBE_TIMEOUT) -> Optional[str]:
    """stdout of `args`, or None. exec, never a shell."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="ignore")


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _cap(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS - 1] + "…"


async def _units() -> List[str]:
    out = await _run([
        "systemctl", "list-units", UNIT_GLOB, "--type=service", "--all",
        "--no-pager", "--no-legend", "--plain",
    ])
    if not out:
        return []
    return [
        parts[0] for parts in (line.split() for line in out.splitlines())
        if parts and parts[0].endswith(".service")
    ]


async def _unit_states(units: Sequence[str]) -> List[str]:
    """`Key=Value`, not `--value`: systemd prints properties in its own order,
    so positional parsing reads the restart count as the unit name."""
    if not units:
        return []
    out = await _run([
        "systemctl", "show", "--property=Id,ActiveState,SubState,NRestarts", *units
    ], timeout=4.0)
    if not out:
        return []
    lines = []
    for chunk in out.split("\n\n"):
        fields = dict(line.split("=", 1) for line in chunk.splitlines() if "=" in line)
        if not fields.get("Id"):
            continue
        lines.append(
            f"{fields['Id']:<40} {fields.get('ActiveState', '?'):<10} "
            f"{fields.get('SubState', '?'):<10} restarts={fields.get('NRestarts', '?')}"
        )
    return lines


def _alsa_states() -> List[str]:
    """RUNNING vs PAUSED vs closed, per substream.

    This is the whole reason a satellite is worth asking: CamillaDSP's silence
    pause writes nothing to any log, and the only place it is visible is here.
    """
    lines, closed = [], {}
    for path in sorted(Path("/proc/asound").glob("card*/pcm*/sub*/status")):
        content = _read(path)
        if content is None:
            continue
        if content.split() and content.split()[0] == "closed":
            device = str(path.parent.parent)
            closed[device] = closed.get(device, 0) + 1
            continue
        fields = dict(
            (k.strip(), v.strip())
            for k, v in (l.split(":", 1) for l in content.splitlines() if ":" in l)
        )
        lines.append(
            f"{path}: {fields.get('state', '?')} delay={fields.get('delay', '?')} "
            f"avail={fields.get('avail', '?')} avail_max={fields.get('avail_max', '?')}"
        )
    if closed:
        lines.append("closed: " + ", ".join(f"{d} ({n})" for d, n in sorted(closed.items())))
    return lines


# The kernel tail: warnings and above (`-p warning`) of the whole current
# boot (`-k`, which implies `-b`), with no `--since`. An undervoltage is a
# cause of dropouts, and it happens at power-up, when the supply is weakest —
# hours before anyone opens the logs: under the units' six-hour window, a
# speaker's boot-time undervoltage was in no answer at all (measured on two
# speakers: 0 kernel lines in the last 6 h, 14-15 at warning or above in the
# ~500 of the boot). Those fit under the 30-line cap whole. Read as JSON, so
# each line keeps its priority: short-iso names none, and the kernel's text
# never says its level (an undervoltage is `dev_crit`).
KERNEL_TAIL = ("-k", "-p", "warning", "-o", "json",
               "--output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,_HOSTNAME")
# Each unit's tail: at most six hours, and never across a reboot (`-b` and
# `--since` intersect).
UNIT_WINDOW = ("-b", "--since", "-6h")


def _kernel_lines(out: str) -> List[str]:
    """The kernel tail's JSON entries in short-iso's shape, local time, each
    message opened by its syslog priority (`<2>hwmon hwmon1: …`), which the
    server reads its level from
    (backend/core/system/logs/entries.py::parse_satellite_block)."""
    lines = []
    for raw in out.splitlines():
        try:
            entry = json.loads(raw)
            at = datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6).astimezone()
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
        message = entry.get("MESSAGE")
        if isinstance(message, list):  # a message that is not UTF-8
            with contextlib.suppress(TypeError, ValueError):
                message = bytes(message).decode("utf-8", errors="replace")
        if not isinstance(message, str) or not message.strip():
            continue
        priority = str(entry.get("PRIORITY", "6"))
        head = (
            f"{at.isoformat(timespec='seconds')} {entry.get('_HOSTNAME') or '-'} "
            f"{entry.get('SYSLOG_IDENTIFIER') or 'kernel'}: "
        )
        first, *rest = message.rstrip().splitlines()
        lines.append(f"{head}<{priority}>{first}")
        lines += [f"    {line}" for line in rest if line.strip()]
    return lines


async def _journal(units: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """A tail per unit, plus the kernel's warnings, each oldest line first."""
    async def tail(args: Sequence[str], render=None) -> List[str]:
        out = await _run([
            "journalctl", "--no-pager", "-n", str(JOURNAL_LINES_PER_UNIT), *args,
        ], timeout=4.0)
        if not out:
            return []
        lines = render(out) if render else out.splitlines()
        return [
            _cap(line) for line in (raw.rstrip() for raw in lines)
            if line and not line.startswith("-- ")
        ]

    results = await asyncio.gather(
        *[tail(["-o", "short-iso", "-u", unit, *UNIT_WINDOW]) for unit in units],
        tail(KERNEL_TAIL, _kernel_lines),
    )
    return [(name, lines) for name, lines in zip([*units, "kernel"], results) if lines]


def _fit_journal(tails: List[Tuple[str, List[str]]], budget: int) -> List[str]:
    """The journal's blocks within `budget` bytes, each unit's oldest lines cut
    first, from whichever unit holds the most.

    The journal is the report's last section, so a cut from the end of the
    text drops the newest lines and every unit after them — the lines a report
    is generated for.
    """
    tails = [(name, list(lines), 0) for name, lines in tails]

    def render() -> List[str]:
        return [
            "\n".join([f"--- {name} ---" + (f" ({cut} older lines cut)" if cut else ""), *lines])
            for name, lines, cut in tails
        ]

    while len("\n\n".join(render()).encode("utf-8")) > budget:
        index = max(
            range(len(tails)), key=lambda i: sum(len(line.encode("utf-8")) for line in tails[i][1])
        )
        name, lines, cut = tails[index]
        if not lines:
            break
        lines.pop(0)
        tails[index] = (name, lines, cut + 1)
    return render()


async def _journal_within(units: Sequence[str], budget: int) -> List[str]:
    return _fit_journal(await _journal(units), budget)


def _journal_readable() -> bool:
    """Whether this account reads the whole journal, not only its own file.

    Outside `adm` and `systemd-journal`, journalctl still exits 0 and prints its
    hint on stderr: every systemd[1] line and the whole kernel ring are simply
    missing from what `_journal` returns.
    """
    if os.geteuid() == 0:
        return True
    groups = {os.getegid(), *os.getgroups()}
    for name in ("adm", "systemd-journal"):
        with contextlib.suppress(KeyError):
            if grp.getgrnam(name).gr_gid in groups:
                return True
    return False


# --------------------------------------------------------------------------- #
# The journal by window (`/diagnostic/journal`)
# --------------------------------------------------------------------------- #

# The server's logs page reads a window as it reads its own journal
# (backend/core/system/logs/journal.py): warnings and above, each read bounded
# in entries (newest kept) and in time, and journalctl filters before the cap,
# so a week fits where every level would not (a week of snapclient here holds
# some 15 000 lines, half of them errors only while the server is away).
JOURNAL_WINDOW_MAX_ENTRIES = 5000
JOURNAL_WINDOW_TIMEOUT = 8.0
_JOURNAL_FIELDS = "MESSAGE,PRIORITY,_SYSTEMD_UNIT,UNIT,SYSLOG_IDENTIFIER,_PID"
# Where a unit's level is in its text alone. journald files the app's and
# CamillaDSP's stderr at priority 6 (measured: every line of both); snapclient
# writes to syslog, so its priority says its level, as systemd's own lines
# about a unit do. The app writes Python's `- LEVEL -` and uvicorn's access
# line, whose status is the only word of a request the app refused; CamillaDSP
# writes `WARN [` / `ERROR [`. Uppercase, so journalctl matches it as written.
TEXT_LEVEL_UNITS = ("milo-client.service", "milo-client-camilladsp.service")
TEXT_LEVEL_GREP = r' - (WARNING|ERROR|CRITICAL) - | (WARN|ERROR) \[|" [45][0-9]{2} '
JOURNAL_WINDOW_READS = (
    ("warnings", ("-u", UNIT_GLOB, "-p", "warning")),
    ("text-level", (*(arg for unit in TEXT_LEVEL_UNITS for arg in ("-u", unit)),
                    "-p", "notice..debug", "-g", TEXT_LEVEL_GREP)),
    # Every boot: `-k` implies `-b`, the transport match does not.
    ("kernel", ("_TRANSPORT=kernel", "-p", "warning")),
)


async def _journal_entries(args: Sequence[str]) -> Tuple[List[dict], Optional[str]]:
    """The JSON entries of one read, oldest first, or why it could not be read.
    journalctl answers 1, silently, to a `-g` that matched nothing: no entries,
    not a failure. A 1 that says something on stderr is one."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "--no-pager", "-q", "-o", "json",
            f"--output-fields={_JOURNAL_FIELDS}", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as e:
        return [], f"journalctl could not start: {e}"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), JOURNAL_WINDOW_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return [], f"timed out after {JOURNAL_WINDOW_TIMEOUT:g} s"
    silent = not stdout.strip() and not stderr.strip()
    if proc.returncode != 0 and not (proc.returncode == 1 and silent):
        reason = stderr.decode("utf-8", errors="replace").strip().splitlines()
        return [], f"journalctl exited {proc.returncode}" + (f": {reason[-1]}" if reason else "")
    entries = []
    for raw in stdout.splitlines():
        with contextlib.suppress(ValueError):
            entry = json.loads(raw)
            if isinstance(entry, dict):
                entries.append(entry)
    return entries, None


async def _journal_window(since: float, until: float) -> List[Dict]:
    """Each read of the window, its entries and whether its cap cut it."""
    window = ("--since", f"@{since:.6f}", "--until", f"@{until:.6f}",
              "-n", str(JOURNAL_WINDOW_MAX_ENTRIES))
    results = await asyncio.gather(*(
        _journal_entries((*args, *window)) for _, args in JOURNAL_WINDOW_READS
    ))
    return [
        {"name": name, "kernel": name == "kernel", "entries": entries,
         "cut": len(entries) >= JOURNAL_WINDOW_MAX_ENTRIES, "reason": reason}
        for (name, _), (entries, reason) in zip(JOURNAL_WINDOW_READS, results)
    ]


def create_diagnostic_router() -> APIRouter:
    router = APIRouter(tags=["diagnostic"])

    @router.get("/diagnostic")
    async def get_diagnostic():
        """This satellite's block of the server's report.

        Never raises: the server has one HTTP call and a short budget, and a 500
        here would turn a satellite that is merely missing one probe into a
        satellite reported as unreachable.
        """
        unavailable: List[Dict[str, str]] = []
        blocks: List[str] = []

        async def add(title: str, coro) -> None:
            try:
                body = await coro
            except Exception as e:  # noqa: BLE001 -- one bad probe costs its own line
                logger.warning("diagnostic section %s failed: %s", title, e)
                unavailable.append({"section": title, "reason": f"{type(e).__name__}: {e}"})
                return
            if not body:
                unavailable.append({"section": title, "reason": "nothing to report"})
                return
            blocks.append(f"[{title}]\n" + ("\n".join(body) if isinstance(body, list) else body))

        units = await _units()
        await add("identity", _identity())
        if units:
            await add("units", _unit_states(units))
        else:
            # Named precisely: "nothing to report" would read as healthy, and a
            # satellite whose units systemd does not list is the opposite.
            unavailable.append(
                {"section": "units", "reason": "systemd listed no milo-client unit"}
            )
        await add("alsa", asyncio.to_thread(_alsa_states))
        await add("camilladsp", _camilladsp())
        used = len("\n\n".join([*blocks, "[journal]\n"]).encode("utf-8"))
        await add("journal", _journal_within(units, MAX_TEXT_BYTES - used))
        if not _journal_readable():
            unavailable.append({
                "section": "journal",
                "reason": "account in neither adm nor systemd-journal: systemd and kernel lines are unreadable",
            })

        text = "\n\n".join(blocks)
        raw = text.encode("utf-8")
        if len(raw) > MAX_TEXT_BYTES:
            text = raw[:MAX_TEXT_BYTES].rsplit(b"\n", 1)[0].decode("utf-8", errors="ignore")
            text += f"\n(truncated on the satellite at {MAX_TEXT_BYTES} bytes)"

        return {
            "hostname": platform.node(),
            "text": text,
            "unavailable": unavailable,
        }

    @router.get("/diagnostic/journal")
    async def get_journal(since: float, until: float):
        """This satellite's journal from `since` to `until` (epoch seconds), at
        warning and above: its own units and its kernel, each read whole up to
        its cap, newest kept, and saying when the cap cut it. What one read
        could not do is its `reason`, never the route's failure, as for the
        block above. `journal_readable` false says the systemd and kernel lines
        are missing: outside `adm`, journalctl leaves them out and exits 0."""
        if not since < until:
            raise HTTPException(status_code=422, detail="since must come before until")
        return {
            "reads": await _journal_window(since, until),
            "journal_readable": _journal_readable(),
        }

    return router


async def _identity() -> List[str]:
    uptime = _read(Path("/proc/uptime"))
    seconds = int(float(uptime.split()[0])) if uptime else 0
    kernel = await _run(["uname", "-r"])
    return [
        f"uptime          : {seconds // 86400}d {(seconds % 86400) // 3600}h "
        f"{(seconds % 3600) // 60}m",
        f"app version     : {health.APP_VERSION or '-'}",
        f"app payload     : {health.APP_PAYLOAD or '-'}",
        f"api started at  : {time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(health.APP_STARTED_AT))}",
        f"kernel          : {(kernel or '-').strip()}",
    ]


async def _camilladsp() -> List[str]:
    """The DSP's own state, read from its config file and its unit.

    Not from the running daemon: the server already fans EQ commands out over
    the equalizer routes, and a diagnostic that opened a second WebSocket to a
    CamillaDSP that is wedged would hang on exactly the fault it is reporting.
    """
    # `show`, not `is-active`: is-active exits 3 in every state but active, and
    # `_run` drops the stdout of a non-zero exit.
    active = await _run([
        "systemctl", "show", "-p", "ActiveState", "--value", "milo-client-camilladsp.service"
    ])
    version = await _run(["camilladsp", "--version"])
    return [
        f"milo-client-camilladsp.service : {(active or 'unknown').strip()}",
        f"camilladsp version             : {(version or '-').strip()}",
    ]
