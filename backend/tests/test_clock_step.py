"""The kernel's notice of a wall clock step (backend/shared/clock_step.py).

A unit with no RTC battery boots on the time timesyncd last saved and steps
forward once NTP answers. A source that hears nothing from its daemon between
events (AirPlay, Spotify) is re-dated only when a message reaches it, so the
state machine asks the kernel to say when the clock is set. The kernel and the
event loop are the outside world here: both are fakes, and what is asserted is
what the watch asked of the kernel and when it called back.
"""
import errno
import logging
import os
import time

from backend.shared.clock_step import WallClockStepWatch

CANCEL_ON_SET = os.TFD_TIMER_ABSTIME | os.TFD_TIMER_CANCEL_ON_SET


class FakeLoop:
    def __init__(self):
        self.readers = {}

    def add_reader(self, fd, callback):
        self.readers[fd] = callback

    def remove_reader(self, fd):
        self.readers.pop(fd, None)


class FakeTimerfdOs:
    """The timerfd calls of `os`, as Linux answers them."""

    TFD_NONBLOCK = os.TFD_NONBLOCK
    TFD_CLOEXEC = os.TFD_CLOEXEC
    TFD_TIMER_ABSTIME = os.TFD_TIMER_ABSTIME
    TFD_TIMER_CANCEL_ON_SET = os.TFD_TIMER_CANCEL_ON_SET

    def __init__(self):
        self.created = []
        self.armed = []
        self.closed = []
        self.read_raises = BlockingIOError(errno.EAGAIN, "would block")

    def timerfd_create(self, clockid, *, flags=0):
        self.created.append((clockid, flags))
        return 42

    def timerfd_settime(self, fd, *, flags=0, initial=0.0, interval=0.0):
        self.armed.append((fd, flags))

    def read(self, fd, n):
        raise self.read_raises

    def close(self, fd):
        self.closed.append(fd)


class NoTimerfdOs:
    """A host whose `os` has no timerfd (not Linux)."""

    def close(self, fd):  # pragma: no cover - never reached
        raise AssertionError("nothing was opened")


def test_a_clock_set_is_heard_once_and_the_watch_rearms():
    """A timerfd armed with CANCEL_ON_SET reads ECANCELED once per set of the
    clock and must be armed again to hear the next one: a watch that does not
    re-arm hears the boot's first step and is deaf from then on. A wake-up
    that reads nothing (EAGAIN) is not a step, and it does not end the watch
    either: closed there, the watch misses every step after it."""
    fake_os, loop, steps = FakeTimerfdOs(), FakeLoop(), []
    watch = WallClockStepWatch(lambda: steps.append(1), logging.getLogger("test"),
                               os_module=fake_os, loop=loop)

    assert watch.start() is True
    # CANCEL_ON_SET only means something on CLOCK_REALTIME: the kernel takes
    # the same flags on CLOCK_MONOTONIC without a word, and that timer never
    # hears a step while the watch still logs that it is running.
    assert fake_os.created == [(time.CLOCK_REALTIME, os.TFD_NONBLOCK | os.TFD_CLOEXEC)]
    assert fake_os.armed == [(42, CANCEL_ON_SET)]

    fake_os.read_raises = OSError(errno.ECANCELED, "clock was set")
    loop.readers[42]()
    assert steps == [1]
    assert fake_os.armed == [(42, CANCEL_ON_SET), (42, CANCEL_ON_SET)]

    fake_os.read_raises = BlockingIOError(errno.EAGAIN, "would block")
    loop.readers[42]()
    assert steps == [1]
    assert 42 in loop.readers and fake_os.closed == [], "a spurious wake-up ended the watch"

    fake_os.read_raises = OSError(errno.ECANCELED, "clock was set")
    loop.readers[42]()
    assert steps == [1, 1]
    assert fake_os.armed == [(42, CANCEL_ON_SET)] * 3

    watch.close()
    watch.close()
    assert loop.readers == {}
    assert fake_os.closed == [42]


def test_a_host_without_timerfd_fails_open(caplog):
    """A dev host without timerfd runs the backend as before: the sources are
    re-dated at their next message, start() says the watch is off, and the
    lifespan that called it carries on."""
    loop = FakeLoop()
    watch = WallClockStepWatch(lambda: None, logging.getLogger("test.clock"),
                               os_module=NoTimerfdOs(), loop=loop)

    with caplog.at_level(logging.INFO, logger="test.clock"):
        assert watch.start() is False

    assert loop.readers == {}
    records = [r for r in caplog.records if r.name == "test.clock"]
    assert [r.levelno for r in records] == [logging.INFO]
    watch.close()
