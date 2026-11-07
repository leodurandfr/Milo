"""The kernel's notice that the wall clock was set.

A unit with no RTC battery boots on the time timesyncd last saved (every
5 min) and steps the clock forward once NTP answers, ~35 s into every boot
measured with the internet up. A playhead anchor is stamped on the wall clock,
and `BaseAudioSource` re-dates it across a step before each message it handles
— which a source that hears nothing between its daemon's events (AirPlay, a
Spotify session, Spotify's other device) does not get until the sender's next
event. This watch is that message's trigger.

A timerfd on CLOCK_REALTIME armed with TFD_TIMER_CANCEL_ON_SET and a deadline
far ahead never expires; a read of it fails with ECANCELED once each time the
clock is set (a timesyncd step, a `date -s`, either direction — a slew is not
a set), and it is armed again to hear the next one. It is a file descriptor in
the event loop, not a task. A host without timerfd fails open: the sources are
then re-dated at their next message, as before.
"""
import asyncio
import errno
import os
import time
from typing import Callable, Optional

# The timer only exists to be cancelled: its deadline is never reached.
_FAR_AHEAD_S = 10 * 365 * 86400


class WallClockStepWatch:
    """Calls `on_step` from the event loop each time the wall clock is set."""

    def __init__(self, on_step: Callable[[], None], logger, *, os_module=os,
                 loop: Optional[asyncio.AbstractEventLoop] = None):
        self._on_step = on_step
        self._logger = logger
        self._os = os_module
        self._given_loop = loop
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._fd: Optional[int] = None

    def start(self) -> bool:
        """Open and arm the timer and register it with the loop. Never raises:
        False when the clock cannot be watched on this host."""
        if not hasattr(self._os, "timerfd_create"):
            self._logger.info("Wall clock steps not watched: no timerfd on this host")
            return False
        try:
            self._fd = self._os.timerfd_create(
                time.CLOCK_REALTIME, flags=self._os.TFD_NONBLOCK | self._os.TFD_CLOEXEC
            )
            self._arm()
            loop = self._given_loop or asyncio.get_running_loop()
            loop.add_reader(self._fd, self._readable)
        except (OSError, NotImplementedError) as e:
            self._logger.info(f"Wall clock steps not watched: {e}")
            self.close()
            return False
        self._loop = loop
        self._logger.info("Watching the wall clock for steps")
        return True

    def close(self) -> None:
        """Stop watching (idempotent)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        if self._loop is not None:
            self._loop.remove_reader(fd)
            self._loop = None
        self._os.close(fd)

    def _arm(self) -> None:
        self._os.timerfd_settime(
            self._fd,
            flags=self._os.TFD_TIMER_ABSTIME | self._os.TFD_TIMER_CANCEL_ON_SET,
            initial=time.time() + _FAR_AHEAD_S,
        )

    def _readable(self) -> None:
        try:
            self._os.read(self._fd, 8)
        except BlockingIOError:
            return
        except OSError as e:
            if e.errno != errno.ECANCELED:
                # Readable and failing for another reason would wake the loop
                # forever: stop watching rather than spin.
                self._logger.warning(f"Wall clock step watch failed, no longer watching: {e}")
                self.close()
                return
            try:
                self._arm()
            except OSError as e:
                self._logger.warning(f"Wall clock step watch could not re-arm, no longer watching: {e}")
                self.close()
            self._logger.info("Wall clock set: re-reading the active source")
            try:
                self._on_step()
            except Exception as e:
                self._logger.error(f"Following a wall clock step failed: {e}")
