# backend/shared/journalctl.py
"""Shared journalctl helpers.

Two audio sources tail a systemd unit's journal to derive state: Spotify
(go-librespot auth/track errors) and Mac (ROC connect/disconnect). Both spawned
an identical `journalctl -f -u <unit>` subprocess + readline loop with their own
hand-rolled teardown; `follow_unit` owns that skeleton so each source keeps only
its per-line parsing. `read_unit` is the one-shot companion for startup scans.

The rule a dying feed follows, here and in every other one Milō reads
(`sources/bluetooth/monitor.py::_report_lost` is the other implementation):
**it is reported once, at error, naming the unit and what stops working. State
is changed only by the feed that owns the fact the state asserts.** A follow
that ends is the only thing that knew whether a sender was attached, so a
source reacting to its death by dropping the session would be guessing — the
audio may well still be flowing. Reporting is therefore the whole response, and
it is not optional: until this existed, a journalctl that exited took a
source's connection detection down for the rest of the session with no log, no
state change and no restart.
"""
import asyncio
import contextlib
import logging
from typing import AsyncIterator, Iterable, List, Optional


async def follow_unit(
    unit: str,
    *,
    consequence: str,
    output: str = "cat",
    tail: int = 0,
    logger: Optional[logging.Logger] = None,
) -> AsyncIterator[str]:
    """Yield decoded, stripped, non-empty lines from `journalctl -f -u <unit>`.

    Owns the subprocess lifecycle: journalctl is terminated in the finally block
    when the consumer's task is cancelled or the generator is closed. Decoding
    uses errors='ignore'.

    `consequence` names what stops working when the feed dies, in the caller's
    own terms — the primitive is shared by two sources that lose two different
    things, and a generic line would tell the owner a process ended without
    telling them what it cost. Required and keyword-only, so a third consumer
    cannot be added without answering the question.
    """
    proc = await asyncio.create_subprocess_exec(
        "journalctl", "-u", unit, "-f", "-n", str(tail), "-o", output,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if logger:
        logger.info("journalctl follow started for %s", unit)

    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:  # EOF: the writer went away
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 1.0)
                # A journalctl that died on a signal was killed by someone
                # else, and on this appliance that someone is systemd:
                # milo-backend.service runs KillMode=control-group, so an
                # ordinary `systemctl restart milo-backend` SIGTERMs the
                # journalctl children in the same cgroup, and they reach here
                # before uvicorn's shutdown cancels the monitor task that owns
                # this generator. Measured: returncode -15. Reporting it would
                # put a false "detection is down" in errors.log and the UI
                # banner on every restart — the noise this exists to replace.
                # A clean consumer teardown never comes through here at all:
                # it unwinds through CancelledError/GeneratorExit into the
                # finally below.
                if proc.returncode is None or proc.returncode >= 0:
                    (logger or logging.getLogger(__name__)).error(
                        "journalctl follow for %s ended (exit=%s) — %s until the "
                        "source is restarted",
                        unit, proc.returncode, consequence,
                    )
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                yield text
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
                await proc.wait()


async def read_unit(
    unit: str,
    *,
    tail: Optional[int] = None,
    since: Optional[str] = None,
    output: str = "cat",
    drop_substrings: Iterable[str] = (),
    keep_last: Optional[int] = None,
    timeout: float = 10.0,
    logger: Optional[logging.Logger] = None,
) -> List[str]:
    """One-shot `journalctl -u <unit>` read (exec, not shell — no shell-injection
    surface, no grep pipeline).

    Returns non-empty lines, with any line containing a `drop_substrings` token
    filtered out; `keep_last` then trims to the last N survivors (mirrors a
    trailing `| tail -N`). `output` matches follow_unit's default so both reads
    feed one journal format. Returns [] on timeout or a non-zero exit.
    """
    args = ["journalctl", "-u", unit, "--no-pager", "-o", output]
    if tail is not None:
        args += ["-n", str(tail)]
    if since is not None:
        args += ["--since", since]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        if logger:
            logger.error("Timeout reading journalctl for %s", unit)
        return []

    if proc.returncode != 0:
        return []

    drop = tuple(drop_substrings)
    out: List[str] = []
    for line in stdout.decode("utf-8", errors="ignore").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if any(token in stripped for token in drop):
            continue
        out.append(stripped)

    if keep_last is not None:
        return out[-keep_last:]
    return out
