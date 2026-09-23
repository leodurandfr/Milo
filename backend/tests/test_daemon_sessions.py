"""The base's primitives for a session a daemon holds (docs: source
architecture, "reconcile"): reconcile(), the daemon's process watch, and the
idle timeout that asks the daemon for the end (REQUEST_END).

AirPlay is their first user (tests/test_airplay_sessions.py); Spotify, Tidal,
Qobuz, DLNA and Mac follow in phases 3b-3d on the same three, so they are
pinned here on a source that is nothing but a daemon's session. The outside
world is systemd (the daemon's pid, its restart), the daemon's answer to an
end request, and the kernel (a real process, a real pidfd).
"""
import asyncio
import os
import signal
from typing import List
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core import audio_source
from backend.core.audio_source import BaseAudioSource
from backend.core.models.session import (
    DaemonSnapshot, EndReason, IdlePolicy, Phase, ReroutePolicy, ResumePolicy,
)
from backend.core.models.source_metadata import PlaybackMetadata
from backend.shared import pidfd
from backend.shared.pidfd import ProcessWatch
from backend.tests.golden.harness import AsyncioProxy, VirtualClock, settle

DELAY = 60.0


class Daemon(BaseAudioSource):
    """A source that is nothing but a daemon's session."""

    IDLE_POLICY = IdlePolicy.REQUEST_END
    REROUTE = ReroutePolicy.END_SESSION
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    def __init__(self, systemd) -> None:
        super().__init__("airplay", "milo-daemon.service", systemd_manager=systemd)
        self.auto_stop_enabled, self.auto_stop_delay = True, DELAY
        self.requests: List[str] = []
        self.answers = True
        self.ends: List[EndReason] = []

    async def _do_start(self) -> bool:
        return True

    async def _request_end(self, session) -> bool:
        self.requests.append(session.sender)
        return self.answers

    async def _session_ended(self, session, reason) -> None:
        self.ends.append(reason)

    def _connection_state(self):
        session = self._session
        if session is None:
            return False, None, {}
        return True, PlaybackMetadata(is_playing=session.phase is Phase.PLAYING), {
            "sender": session.sender, "phase": session.phase.value,
        }

    def _update_connection_state(self) -> None:
        self.emit_connection_state(*self._connection_state())


class Pids:
    """systemd's view of the daemon, and the watches opened on it."""

    def __init__(self, monkeypatch) -> None:
        self.pid = 100
        self.watched: List[tuple] = []
        self.restart_ok = True
        pids = self

        class Watch:
            def __init__(self, pid, on_exit) -> None:
                self.entry = (pid, on_exit)
                pids.watched.append(self.entry)

            def close(self) -> None:
                if self.entry in pids.watched:
                    pids.watched.remove(self.entry)

        monkeypatch.setattr(audio_source, "ProcessWatch", Watch)
        self.systemd = Mock()
        self.systemd.main_pid = AsyncMock(side_effect=lambda *_: self.pid)
        self.systemd.restart = AsyncMock(side_effect=self._restart)

    def exit(self) -> None:
        dead, self.pid = self.pid, (self.pid or 100) + 1
        for pid, on_exit in list(self.watched):
            if pid == dead:
                on_exit()

    async def _restart(self, *_) -> bool:
        if self.restart_ok:
            self.exit()
        return self.restart_ok


@pytest.fixture
def clock(monkeypatch):
    clock = VirtualClock()
    monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(clock.sleep))
    return clock


@pytest.fixture
def pids(monkeypatch):
    return Pids(monkeypatch)


@pytest.fixture
async def daemon(pids, clock):
    source = Daemon(pids.systemd)
    yield source
    await source.shutdown()


async def _report(source: Daemon, sender, phase=None):
    """The daemon reports its session (no phase: it holds none), in the actor
    like every feed."""
    snapshot = None if phase is None else DaemonSnapshot(sender, phase)
    done = asyncio.get_running_loop().create_future()

    async def apply():
        done.set_result(await source.reconcile(snapshot))
        source._update_connection_state()

    source._post_result(apply)
    await settle()
    return done.result()


# === reconcile ===

async def test_a_report_opens_moves_and_ends_the_session(daemon):
    opened = await _report(daemon, "phone", Phase.CONNECTED)
    assert opened.phase is Phase.CONNECTED and opened.sender == "phone"
    moved = await _report(daemon, "phone", Phase.PLAYING)
    assert moved is opened and moved.phase is Phase.PLAYING
    assert await _report(daemon, None) is None
    assert daemon.ends == [EndReason.SENDER_LEFT]
    assert daemon.metadata.get("sender") is None


async def test_the_same_report_twice_changes_nothing(daemon):
    first = await _report(daemon, "phone", Phase.PLAYING)
    again = await _report(daemon, "phone", Phase.PLAYING)
    assert again is first and again.phase is Phase.PLAYING
    assert daemon.ends == []


async def test_a_report_from_another_sender_is_another_session(daemon):
    """E20's shape: the second sender does not inherit the first one's session."""
    first = await _report(daemon, "phone", Phase.PLAYING)
    second = await _report(daemon, "mac", Phase.CONNECTED)
    assert second is not first
    assert daemon.ends == [EndReason.SENDER_LEFT]
    assert daemon.metadata["sender"] == "mac"


async def test_a_session_opened_before_its_sender_was_named_keeps_it(daemon):
    """A daemon may announce a stream before it names the sender (AirPlay's
    `conn` is not guaranteed first): the name joins that session, it does not
    open another."""
    anonymous = await _report(daemon, None, Phase.CONNECTED)
    named = await _report(daemon, "phone", Phase.PLAYING)
    assert named is anonymous and named.sender == "phone"
    assert daemon.ends == []


# === the daemon's process ===

async def test_the_session_is_watched_against_the_daemon_that_holds_it(daemon, pids):
    await _report(daemon, "phone", Phase.PLAYING)
    assert [pid for pid, _ in pids.watched] == [100]
    pids.exit()
    await settle()
    assert daemon.ends == [EndReason.DAEMON_DIED]
    assert daemon.state.value == "ready"
    assert pids.watched == []


async def test_each_session_reads_the_pid_anew(daemon, pids):
    """E21: a pid kept from the previous session is how a sender reconnecting
    to the restarted daemon was watched against the dead one — and cut."""
    await _report(daemon, "phone", Phase.PLAYING)
    pids.exit()
    await settle()
    await _report(daemon, "phone", Phase.PLAYING)
    assert [pid for pid, _ in pids.watched] == [101]


async def test_a_daemon_that_cannot_be_named_is_not_watched(daemon, pids):
    """Fail open: an unanswerable question is not evidence the session died."""
    pids.pid = None
    await _report(daemon, "phone", Phase.PLAYING)
    assert pids.watched == []
    assert daemon.metadata["phase"] == "playing"


# === REQUEST_END ===

async def test_a_long_pause_is_ended_by_asking_the_daemon(daemon, clock):
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert daemon.requests == ["phone"]
    assert daemon.ends == []                    # asked, not done: the daemon answers
    await _report(daemon, None)
    assert daemon.ends == [EndReason.IDLE_TIMEOUT]


async def test_a_resume_withdraws_the_request_and_the_timer(daemon, clock):
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY - 1)
    await _report(daemon, "phone", Phase.PLAYING)
    await clock.advance(DELAY + 1)
    assert daemon.requests == []
    await _report(daemon, None)
    assert daemon.ends == [EndReason.SENDER_LEFT]


async def test_a_daemon_that_does_not_answer_is_restarted_and_that_is_the_end(daemon, pids, clock):
    daemon.answers = False
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert pids.systemd.restart.await_count == 1
    assert daemon.ends == [EndReason.IDLE_TIMEOUT]       # no DAEMON_DIED, no banner


async def test_a_failed_restart_leaves_the_session_with_the_daemon(daemon, pids, clock):
    daemon.answers = False
    pids.restart_ok = False
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert daemon.ends == []
    assert daemon.metadata["phase"] == "paused"
    await _report(daemon, "phone", Phase.PLAYING)
    await _report(daemon, None)
    assert daemon.ends == [EndReason.SENDER_LEFT]        # the request was withdrawn


async def test_connected_sessions_never_time_out(daemon, clock):
    await _report(daemon, "mac", Phase.CONNECTED)
    await clock.advance(10 * DELAY)
    assert daemon.requests == []


# === ProcessWatch, on a real process ===

async def _child() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec("sleep", "30")


async def test_a_watch_hears_its_process_exit():
    proc = await _child()
    exited = asyncio.Event()
    watch = ProcessWatch(proc.pid, exited.set)
    os.kill(proc.pid, signal.SIGKILL)
    await proc.wait()
    await asyncio.wait_for(exited.wait(), 5)
    watch.close()


async def test_a_closed_watch_stays_silent():
    proc = await _child()
    calls = []
    watch = ProcessWatch(proc.pid, lambda: calls.append(1))
    watch.close()
    os.kill(proc.pid, signal.SIGKILL)
    await proc.wait()
    await asyncio.sleep(0.05)
    assert calls == []


async def test_a_process_already_gone_is_reported_at_once():
    proc = await _child()
    os.kill(proc.pid, signal.SIGKILL)
    await proc.wait()
    exited = asyncio.Event()
    ProcessWatch(proc.pid, exited.set)
    await asyncio.wait_for(exited.wait(), 5)


async def test_without_pidfd_the_watch_falls_back_to_proc(monkeypatch):
    """Fail-open to the check pidfd replaced: /proc, looked at on an interval."""
    def unsupported(pid):
        raise OSError(38, "Function not implemented")

    proc = await _child()   # asyncio watches its own children with a pidfd
    monkeypatch.setattr(pidfd.os, "pidfd_open", unsupported)
    monkeypatch.setattr(pidfd, "FALLBACK_POLL_S", 0.01)
    exited = asyncio.Event()
    watch = ProcessWatch(proc.pid, exited.set)
    await asyncio.sleep(0.05)
    assert not exited.is_set()
    os.kill(proc.pid, signal.SIGKILL)
    await proc.wait()
    await asyncio.wait_for(exited.wait(), 5)
    watch.close()


# === Found by the code review (2026-09-24) ===

async def test_a_daemon_that_could_not_be_named_is_named_later(daemon, pids):
    """A pid read that fails once (systemctl slow on a busy card, MainPID 0
    mid-restart) is retried on the next report: an unwatched session is the
    ghost the watch exists to end."""
    pids.pid = None
    await _report(daemon, "phone", Phase.PLAYING)
    pids.pid = 100
    await _report(daemon, "phone", Phase.PLAYING)
    assert [pid for pid, _ in pids.watched] == [100]
    pids.exit()
    await settle()
    assert daemon.ends == [EndReason.DAEMON_DIED]


async def test_a_restart_that_nothing_watches_still_ends_the_session(daemon, pids, clock):
    """The fallback restarts the daemon; with no watch to hear the old one
    exit, the session would outlive the process that held it."""
    pids.pid = None
    daemon.answers = False
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert pids.systemd.restart.await_count == 1
    assert daemon.ends == [EndReason.IDLE_TIMEOUT]


async def test_an_end_that_never_comes_back_falls_back_to_the_restart(daemon, pids, clock):
    """The daemon took the request and said nothing more: after a bound, the
    restart is tried rather than leaving the session paused for good."""
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert daemon.requests == ["phone"] and daemon.ends == []
    await clock.advance(audio_source.END_REQUEST_TIMEOUT_S + 1)
    assert pids.systemd.restart.await_count == 1
    assert daemon.ends == [EndReason.IDLE_TIMEOUT]


async def test_a_request_that_raises_still_falls_back_to_the_restart(daemon, pids, clock):
    async def broken(session):
        raise RuntimeError("the bus answered something unexpected")

    daemon._request_end = broken
    await _report(daemon, "phone", Phase.PAUSED)
    await clock.advance(DELAY + 1)
    assert pids.systemd.restart.await_count == 1
    assert daemon.ends == [EndReason.IDLE_TIMEOUT]
