# backend/shared/pidfd.py
"""A daemon's death, heard when it happens.

A session a daemon holds (AirPlay's shairport-sync) ends with a goodbye that
comes *from* the daemon, so a daemon killed outright never sends one, and
systemd's Restart= brings up a new process that announces nothing. The
process identity is what answers "is the daemon that holds this session still
there": a pidfd (Linux 5.3+) turns readable when its process exits, which the
event loop hears like any socket. Measured on the unit (2026-09-23): the `milo`
user opens one on another unit's process, and the exit is seen 0.09 ms later.

Fail-open to the check it replaced: without pidfd support, /proc is looked at
every FALLBACK_POLL_S — late, but never wrong.
"""
import asyncio
import contextlib
import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# The interval of the /proc check the pidfd replaced (AirPlay's position ticker).
FALLBACK_POLL_S = 10.0


class ProcessWatch:
    """Calls `on_exit` once, on the loop, when process `pid` has exited.

    A pid already gone is reported at once. `close()` makes sure it never is.
    """

    def __init__(self, pid: int, on_exit: Callable[[], None]) -> None:
        self._pid = pid
        self._on_exit: Optional[Callable[[], None]] = on_exit
        self._fd: Optional[int] = None
        self._poll: Optional[asyncio.Task] = None
        loop = asyncio.get_running_loop()
        try:
            self._fd = os.pidfd_open(pid)
        except ProcessLookupError:
            loop.call_soon(self._exited)
            return
        except (AttributeError, OSError) as e:
            logger.warning(
                "No pidfd for pid %s (%s): checking /proc every %.0f s instead",
                pid, e, FALLBACK_POLL_S,
            )
            self._poll = loop.create_task(self._poll_proc())
            return
        loop.add_reader(self._fd, self._exited)

    async def _poll_proc(self) -> None:
        while os.path.exists(f"/proc/{self._pid}"):
            await asyncio.sleep(FALLBACK_POLL_S)
        self._poll = None
        self._exited()

    def _exited(self) -> None:
        on_exit, self._on_exit = self._on_exit, None
        self.close()
        if on_exit is not None:
            on_exit()

    def close(self) -> None:
        self._on_exit = None
        if self._fd is not None:
            fd, self._fd = self._fd, None
            with contextlib.suppress(ValueError, OSError):
                asyncio.get_running_loop().remove_reader(fd)
            os.close(fd)
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None
