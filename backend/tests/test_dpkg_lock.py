"""Who holds dpkg's lock, read the way the updates read it: from /proc/locks.

The server's program updates and a satellite's app push wait on this before
they install anything, because unattended-upgrades holds dpkg's lock for its
whole run. Read wrong, either installs under a run (a failed install, a room
left mute while a package that never lands is retried) or waits on a lock
nobody holds and refuses every push.

The holder tests take real locks in child processes and read the real
/proc/locks: the kernel's format is the contract, so it is not modeled.
"""
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import pytest

from backend.core.updates import dpkg_lock

REPO_ROOT = Path(__file__).resolve().parents[2]
SATELLITE_COPY = REPO_ROOT / "milo-client" / "app" / "dpkg_lock.py"
SERVER_COPY = Path(dpkg_lock.__file__)

linux_only = pytest.mark.skipif(
    not Path("/proc/locks").is_file(), reason="/proc/locks is Linux's; nothing else reads it"
)

# Takes an fcntl lock on argv[1], blocking until it has it, says so, and holds it
# until stdin closes. apt and dpkg lock their files the same way.
LOCKER = """
import fcntl, sys
f = open(sys.argv[1], "w")
fcntl.lockf(f, fcntl.LOCK_EX)
print("locked", flush=True)
sys.stdin.read()
"""


def _hold(path: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", LOCKER, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "locked"
    return proc


def _release(proc: subprocess.Popen) -> None:
    proc.stdin.close()
    proc.wait(timeout=10)


def _waiting(pid: int) -> bool:
    """Whether the kernel lists `pid` as blocked on a lock (`N: -> POSIX …`)."""
    for line in Path("/proc/locks").read_text().splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[1] == "->" and fields[5] == str(pid):
            return True
    return False


@pytest.fixture
def locks(tmp_path):
    """Two lock files standing for dpkg's, on the filesystem tmp_path is on."""
    frontend, database = tmp_path / "lock-frontend", tmp_path / "lock"
    frontend.touch()
    database.touch()
    return frontend, database


@linux_only
def test_a_lock_another_process_holds_is_named_with_its_pid(locks):
    proc = _hold(locks[0])
    try:
        held_by = dpkg_lock.holder(locks)
    finally:
        _release(proc)

    assert held_by is not None, "a held lock read as free: the /proc/locks parse is broken"
    assert f"(pid {proc.pid})" in held_by
    assert held_by.startswith("python"), f"{held_by!r} does not name the process"


@linux_only
def test_a_released_lock_reads_as_free(locks):
    _release(_hold(locks[1]))

    assert dpkg_lock.holder(locks) is None


@linux_only
def test_a_process_waiting_for_the_lock_is_not_its_holder(locks):
    """The kernel lists a process blocked on a lock beside its holder (`->`):
    named instead, the refusal would point at the push's own neighbor."""
    holder_proc = _hold(locks[0])
    waiter = subprocess.Popen(
        [sys.executable, "-c", LOCKER, str(locks[0])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(500):
            if _waiting(waiter.pid):
                break
            time.sleep(0.01)
        else:
            pytest.fail("the waiter never showed in /proc/locks")
        held_by = dpkg_lock.holder(locks)
    finally:
        _release(holder_proc)
        assert waiter.stdout.readline().strip() == "locked"
        _release(waiter)

    assert held_by is not None and f"(pid {holder_proc.pid})" in held_by, held_by


@linux_only
def test_a_lock_on_another_file_is_not_dpkg_s(tmp_path, locks):
    other = tmp_path / "unrelated"
    other.touch()
    proc = _hold(other)
    try:
        assert dpkg_lock.holder(locks) is None
    finally:
        _release(proc)


def test_what_cannot_be_read_says_nothing_rather_than_guess(tmp_path, locks):
    """A unit with no /proc/locks or no lock file: whatever installs then meets
    the lock itself, as before anything asked."""
    assert dpkg_lock.holder([tmp_path / "missing"]) is None
    with patch.object(dpkg_lock, "PROC_LOCKS", tmp_path / "no-proc-locks"):
        assert dpkg_lock.holder(locks) is None


@linux_only
class TestWaitUntilFree:
    """The wait, over a real lock and the real /proc/locks; only its sleeps
    are stood in for, each one a chance for the holder to let go."""

    @pytest.mark.asyncio
    async def test_it_returns_once_the_lock_is_let_go(self, locks):
        proc = _hold(locks[0])
        sleeps = []

        async def _sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                _release(proc)

        try:
            with patch.object(dpkg_lock.asyncio, "sleep", new=_sleep):
                assert await dpkg_lock.wait_until_free(60, locks) is None
        finally:
            if proc.poll() is None:
                _release(proc)
        assert len(sleeps) == 3, "it went on looking until the lock was let go, no longer"

    @pytest.mark.asyncio
    async def test_a_lock_held_throughout_is_named_after_the_whole_wait(self, locks):
        """The wait counts its sleeps: never shorter than asked, however long
        each look at /proc/locks takes."""
        proc = _hold(locks[1])
        try:
            with patch.object(dpkg_lock, "POLL_S", 0.5), \
                    patch.object(dpkg_lock.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                held_by = await dpkg_lock.wait_until_free(2, locks)
        finally:
            _release(proc)

        assert held_by is not None and f"(pid {proc.pid})" in held_by, held_by
        assert sleep.call_args_list == [call(0.5)] * 4


def test_the_satellite_reads_the_lock_with_the_same_code():
    """One reader, spelled twice because a satellite cannot import the
    server's code: a parse fixed in one copy and not the other is a push that
    waits on nothing, or installs under a run."""
    assert SATELLITE_COPY.is_file() and SERVER_COPY.is_file()
    assert SATELLITE_COPY.read_bytes() == SERVER_COPY.read_bytes(), (
        f"{SATELLITE_COPY.relative_to(REPO_ROOT)} and {SERVER_COPY.relative_to(REPO_ROOT)} "
        "have drifted: copy the one you fixed over the other"
    )
