"""Diagnostic route — the only way anything a satellite knows leaves it.

A satellite has no log surface in the Milō UI at all. Its journal, its ALSA
state and its unit states are reachable from the server over this route and
nowhere else, which makes it the half of a multiroom fault that is normally
invisible. The server asks for it while building its own report and pastes the
answer in as a block.

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
import logging
import platform
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from fastapi import APIRouter

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


async def _journal(units: Sequence[str]) -> List[str]:
    """A tail per unit, plus the kernel ring.

    `-b` and `--since` intersect, so the window is at most six hours and never
    crosses a reboot.
    """
    async def tail(args: Sequence[str]) -> List[str]:
        out = await _run([
            "journalctl", "--no-pager", "-o", "short-iso",
            "-n", str(JOURNAL_LINES_PER_UNIT), "-b", "--since", "-6h", *args,
        ], timeout=4.0)
        if not out:
            return []
        return [
            _cap(line) for line in (raw.rstrip() for raw in out.splitlines())
            if line and not line.startswith("-- ")
        ]

    results = await asyncio.gather(*[tail(["-u", unit]) for unit in units], tail(["-k"]))
    blocks = []
    for name, lines in zip([*units, "kernel"], results):
        if lines:
            blocks.append("\n".join([f"--- {name} ---", *lines]))
    return blocks


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
        await add("journal", _journal(units))

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

    return router


async def _identity() -> List[str]:
    from services.app_update import AppUpdateService

    uptime = _read(Path("/proc/uptime"))
    seconds = int(float(uptime.split()[0])) if uptime else 0
    service = AppUpdateService()
    kernel = await _run(["uname", "-r"])
    return [
        f"uptime          : {seconds // 86400}d {(seconds % 86400) // 3600}h "
        f"{(seconds % 3600) // 60}m",
        f"app release     : {service.get_app_release() or '-'}",
        f"app payload     : {service.get_app_payload() or '-'}",
        f"api started at  : {time.strftime('%Y-%m-%dT%H:%M:%S%z')} (now)",
        f"kernel          : {(kernel or '-').strip()}",
    ]


async def _camilladsp() -> List[str]:
    """The DSP's own state, read from its config file and its unit.

    Not from the running daemon: the server already fans EQ commands out over
    the equalizer routes, and a diagnostic that opened a second WebSocket to a
    CamillaDSP that is wedged would hang on exactly the fault it is reporting.
    """
    active = await _run(["systemctl", "is-active", "milo-client-camilladsp.service"])
    version = await _run(["camilladsp", "--version"])
    return [
        f"milo-client-camilladsp.service : {(active or 'unknown').strip()}",
        f"camilladsp version             : {(version or '-').strip()}",
    ]
