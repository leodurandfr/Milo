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
from unittest.mock import Mock

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import AudioSource
from backend.core.state import AudioStateMachine
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
        self.settime_raises = None

    def timerfd_create(self, clockid, *, flags=0):
        self.created.append((clockid, flags))
        return 42

    def timerfd_settime(self, fd, *, flags=0, initial=0.0, interval=0.0):
        if self.settime_raises is not None:
            raise self.settime_raises
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


def test_a_kernel_that_refuses_the_timer_fails_open():
    """A kernel or sandbox can have timerfd and still refuse it (EINVAL,
    EPERM). start() runs inside the lifespan, whose `except Exception`
    re-raises: escaping, the refusal would fail the backend's boot over a
    trigger the sources can do without. It is answered False, with the fd
    closed and nothing left in the loop."""
    fake_os, loop = FakeTimerfdOs(), FakeLoop()
    fake_os.settime_raises = OSError(errno.EINVAL, "invalid argument")
    watch = WallClockStepWatch(lambda: None, logging.getLogger("test"),
                               os_module=fake_os, loop=loop)

    assert watch.start() is False
    assert fake_os.closed == [42]
    assert loop.readers == {}


def test_a_read_failing_for_another_reason_stops_the_watch():
    """The loop's reader is level-triggered: an fd that stays readable and
    fails with anything but ECANCELED (EBADF, EIO) would wake the loop on
    every turn for good. The watch stops instead, and calls nothing."""
    fake_os, loop, steps = FakeTimerfdOs(), FakeLoop(), []
    watch = WallClockStepWatch(lambda: steps.append(1), logging.getLogger("test"),
                               os_module=fake_os, loop=loop)
    assert watch.start() is True

    fake_os.read_raises = OSError(errno.EBADF, "bad file descriptor")
    loop.readers[42]()

    assert steps == []
    assert loop.readers == {}
    assert fake_os.closed == [42]


def test_a_failing_callback_is_logged_and_the_watch_keeps_listening(caplog):
    """`on_step` runs from the loop's reader callback, where an exception
    reaches nothing but asyncio's default handler, with no Milō context. It
    is logged at error, and the next step is still heard."""
    fake_os, loop, calls = FakeTimerfdOs(), FakeLoop(), []

    def on_step():
        calls.append(1)
        raise RuntimeError("source gone")

    watch = WallClockStepWatch(on_step, logging.getLogger("test.clock"),
                               os_module=fake_os, loop=loop)
    assert watch.start() is True
    fake_os.read_raises = OSError(errno.ECANCELED, "clock was set")

    with caplog.at_level(logging.ERROR, logger="test.clock"):
        loop.readers[42]()
        loop.readers[42]()

    assert calls == [1, 1]
    assert [r.levelno for r in caplog.records if r.name == "test.clock"] == [logging.ERROR] * 2
    assert 42 in loop.readers


def test_a_clock_step_re_reads_the_active_source_alone():
    """A step re-reads the source playing now. An idle source's daemon is
    down or stopping (Spotify's unit, an mpv with nothing loaded), so
    re-reading every registered source would send requests nobody answers,
    at every boot. With no active source, nothing is re-read."""
    machine = AudioStateMachine()
    active, idle = Mock(spec=BaseAudioSource), Mock(spec=BaseAudioSource)
    machine.register_source(AudioSource.RADIO, active)
    machine.register_source(AudioSource.PODCAST, idle)

    machine.clock_stepped()
    active.follow_clock_step.assert_not_called()

    machine.system_state.active_source = AudioSource.RADIO
    machine.clock_stepped()

    active.follow_clock_step.assert_called_once_with()
    idle.follow_clock_step.assert_not_called()
