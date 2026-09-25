# backend/shared/journalctl.py
"""Shared journalctl helpers.

Two audio sources tail a systemd unit's journal to derive state: Spotify
(go-librespot auth/track errors) and Mac (ROC connect/disconnect). Both spawned
an identical `journalctl -f -u <unit>` subprocess + readline loop with their own
hand-rolled teardown; `follow_unit` owns that skeleton so each source keeps only
its per-line parsing.

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
import signal
from typing import AsyncIterator, Optional, Union


async def follow_unit(
    unit: str,
    *,
    consequence: str,
    output: str = "cat",
    tail: Union[int, str] = 0,
    since: Optional[str] = None,
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

    `tail` lines already written are replayed first (`"all"` for every one),
    from `since` on when it is given — one process for replay and follow, so
    no line falls between the two.
    """
    args = ["journalctl", "-u", unit, "-f", "-n", str(tail), "-o", output]
    if since is not None:
        args += ["--since", since]
    proc = await asyncio.create_subprocess_exec(
        *args,
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
                # SIGTERM alone is the backend going down with it:
                # milo-backend.service runs KillMode=control-group, so an
                # ordinary `systemctl restart milo-backend` SIGTERMs the
                # journalctl children in the same cgroup, and they reach here
                # before uvicorn's shutdown cancels the monitor task that owns
                # this generator. Measured: returncode -15, and a restart with
                # Mac selected logged nothing. Reporting it would put a false
                # "detection is down" in errors.log on every restart (a
                # `source.*` logger reaches no banner). Any other signal is a
                # death the backend survives — SIGKILL from the OOM killer, a
                # crash — and silencing those
                # would leave the source deaf with nothing said, which is the
                # hole this report exists to close. A clean consumer teardown
                # never comes through here at all: it unwinds through
                # CancelledError/GeneratorExit into the finally below.
                if proc.returncode != -signal.SIGTERM:
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

