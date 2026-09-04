# backend/core/system/diagnostic/probes.py
"""Bounded reads of the outside world, for a report that must survive it.

Every probe here is used while something is already broken — that is the whole
point of the report — so each one is killable and each one answers. A NAS that
stopped answering takes 10.18 s to give the kernel an EHOSTDOWN (measured on
this fleet, 2026-09-02) and a `stat` on its mountpoint blocks in D state until
then, which is why nothing below reads a path in-process: a subprocess can be
killed, a thread blocked in the kernel cannot, and one leaked thread per hung
mount would eventually take the executor with it.
"""
import asyncio
import contextlib
import logging
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# Long enough for journalctl to walk a few hundred MB of journal on an SD card,
# short enough that a wedged one costs a section and not the report.
DEFAULT_TIMEOUT = 5.0


async def run(args: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """stdout of `args`, or None if it failed, timed out or does not exist.

    exec, never a shell: several arguments here carry a path or a unit name, and
    none of them is worth a quoting bug.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError) as e:
        logger.debug("diagnostic probe %s could not start: %s", args[0], e)
        return None

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        logger.debug("diagnostic probe timed out: %s", " ".join(args))
        return None

    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="ignore")


async def read_file(path: Path) -> Optional[str]:
    """Contents of a local file, or None.

    For /proc, /sys and /var/lib only — paths that cannot block. Anything under
    a mount goes through `stat_mount` instead.
    """
    try:
        return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="ignore")
    except OSError:
        return None


async def read_files(paths: Sequence[Path]) -> List[Optional[str]]:
    return list(await asyncio.gather(*(read_file(p) for p in paths)))


async def stat_mount(path: str, timeout: float = 3.0) -> Optional[str]:
    """`%T %a/%b` of the filesystem at `path`, or None when it does not answer.

    None is the interesting answer: it is what a mount whose server stopped
    responding looks like from here, and no log anywhere else says so.
    """
    out = await run(["stat", "-f", "-c", "%T %a/%b", path], timeout=timeout)
    return out.strip() if out else None


async def is_active(unit: str) -> bool:
    """`systemctl is-active` — no privilege, and both call sites run it plain."""
    out = await run(["systemctl", "is-active", unit], timeout=3.0)
    return bool(out) and out.strip() == "active"


async def unit_summary(units: Sequence[str]) -> List[str]:
    """One `<unit> <active> <sub> restarts=<n>` line per unit, in one call.

    `NRestarts` is the cheapest crash-loop detector the appliance has: a unit
    that died and came back leaves nothing in a report otherwise, because its
    journal tail shows only the healthy-looking start that followed.
    """
    if not units:
        return []
    out = await run(
        ["systemctl", "show", "--property=Id,ActiveState,SubState,NRestarts", *units],
        timeout=6.0,
    )
    if not out:
        return []

    # `Key=Value`, not `--value`: systemd prints the properties in ITS order, not
    # the order they were asked for (measured: NRestarts, Id, ActiveState,
    # SubState), so positional parsing reads the restart count as the unit name.
    lines = []
    for chunk in out.split("\n\n"):
        fields = dict(
            line.split("=", 1) for line in chunk.splitlines() if "=" in line
        )
        unit_id = fields.get("Id")
        if not unit_id:
            continue
        lines.append(
            f"{unit_id:<38} {fields.get('ActiveState', '?'):<10} "
            f"{fields.get('SubState', '?'):<10} restarts={fields.get('NRestarts', '?')}"
        )
    return lines


async def list_milo_units(glob: str, extra: Sequence[str] = ()) -> List[str]:
    """Every installed unit matching `glob`, plus the named extras that exist.

    Derived from the host rather than restated from `system/`: a unit added to
    the repo later is picked up here without anyone remembering this file.
    """
    out = await run(
        ["systemctl", "list-units", glob, "--type=service", "--all",
         "--no-pager", "--no-legend", "--plain"],
        timeout=6.0,
    )
    units = []
    if out:
        for line in out.splitlines():
            name = line.split()[0] if line.split() else ""
            if name.endswith(".service"):
                units.append(name)
    for unit in extra:
        if unit not in units:
            units.append(unit)
    return units


async def journal_tail(
    *,
    units: Sequence[str] = (),
    kernel: bool = False,
    lines: int,
    since: Optional[str] = "-6h",
    boot: Optional[str] = "0",
    priority: Optional[str] = None,
) -> List[str]:
    """The tail of the journal, restricted to `units` or to the kernel ring.

    Never unrestricted: a bare `journalctl` also answers for NetworkManager and
    wpa_supplicant, which log the SSID of every network they touch. The unit
    whitelist is the report's redaction rule for free text, so it has to hold
    here as much as in the per-unit section.

    `-b` and `--since` together intersect, which is deliberate: the window is at
    most six hours AND never crosses a reboot, so a unit that came up two minutes
    ago shows its boot instead of six hours of the session that preceded it.
    """
    if not units and not kernel:
        raise ValueError("journal_tail needs units or the kernel ring")

    args = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(lines)]
    for unit in units:
        args += ["-u", unit]
    if kernel:
        args.append("-k")
    if boot is not None:
        args += ["-b", boot]
    if since is not None:
        args += ["--since", since]
    if priority is not None:
        args += ["-p", priority]

    out = await run(args, timeout=DEFAULT_TIMEOUT)
    if out is None:
        return []
    # journalctl prints its own "-- No entries --" placeholder on an empty
    # window; passing it on would read as a log line that says nothing.
    return [
        line for line in (raw.rstrip() for raw in out.splitlines())
        if line and not line.startswith("-- ")
    ]
