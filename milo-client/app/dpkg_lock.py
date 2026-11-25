"""Who holds dpkg's lock, read without root.

unattended-upgrades runs on every unit, at a time apt's timers pick, and holds
dpkg's frontend lock for its whole run. An install that meets it fails at once:
`apt-get` gives up on the lock unless told to wait, `dpkg --configure -a` never
waits, and `apt-get update` never waits for the package lists. So an update asks
first, and waits for the run to end before it starts installing anything.

The lock files are root's and mode 0640, so the app cannot take them to test
them. It does not need to: the kernel lists every lock held on the system in
/proc/locks, which anyone may read, by device and inode, and the lock files
themselves can be stat()ed by anyone. A lock on one of their inodes is held.
Lines marked `->` are processes waiting for a lock, never its holder.

Two copies, byte for byte: backend/core/updates/dpkg_lock.py (the server's
program updates) and milo-client/app/dpkg_lock.py (a satellite's app push and
snapclient update), since a satellite cannot import the server's code. Held equal by
backend/tests/test_dpkg_lock.py.
"""
import asyncio
import os
from pathlib import Path
from typing import Iterable, Optional

PROC_LOCKS = Path("/proc/locks")
# The frontend's lock, held by apt and unattended-upgrades for a whole run, and
# the database's, held by dpkg itself while it runs.
DPKG_LOCKS = (Path("/var/lib/dpkg/lock-frontend"), Path("/var/lib/dpkg/lock"))
# What `apt-get update` holds while it reads the package lists.
APT_LISTS_LOCK = Path("/var/lib/apt/lists/lock")
POLL_S = 2.0


def _lock_ids(paths: Iterable[Path]) -> set:
    """(major, minor, inode) of each lock file that exists."""
    ids = set()
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        ids.add((os.major(st.st_dev), os.minor(st.st_dev), st.st_ino))
    return ids


def _process_name(pid: int) -> str:
    if pid <= 0:
        # An open file description lock names no process.
        return "a process"
    try:
        # A process names itself, in whatever bytes it likes.
        name = Path(f"/proc/{pid}/comm").read_text(errors="replace").strip()
    except OSError:
        name = ""
    return f"{name or 'a process'} (pid {pid})"


def holder(paths: Optional[Iterable[Path]] = None) -> Optional[str]:
    """The process holding one of `paths` (dpkg's two locks by default), as
    "name (pid N)", or None when none does, or when /proc/locks cannot be read
    (whatever then installs finds the lock itself, as it did before this was
    asked). Blocking: an event loop asks it through a thread."""
    ids = _lock_ids(DPKG_LOCKS if paths is None else paths)
    if not ids:
        return None
    try:
        text = PROC_LOCKS.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        # `1: POSIX  ADVISORY  WRITE 812 b3:02:131074 0 EOF`, and for a process
        # blocked on that lock `1: -> POSIX  ADVISORY  WRITE 990 b3:02:131074 0 EOF`.
        fields = line.split()
        if len(fields) < 6 or fields[1] == "->":
            continue
        try:
            major, minor, inode = fields[5].split(":")
            lock_id = (int(major, 16), int(minor, 16), int(inode))
            pid = int(fields[4])
        except ValueError:
            continue
        if lock_id in ids:
            return _process_name(pid)
    return None


async def wait_until_free(timeout_s: float, paths: Optional[Iterable[Path]] = None) -> Optional[str]:
    """Waits until no process holds `paths` (dpkg's two locks by default), for
    `timeout_s` at most, asking every POLL_S.

    None once they are free; the holder still there at the end otherwise.
    Counts its sleeps, so the wait is never shorter than `timeout_s`.
    """
    paths = tuple(DPKG_LOCKS if paths is None else paths)
    waited = 0.0
    while True:
        held_by = await asyncio.to_thread(holder, paths)
        if held_by is None or waited >= timeout_s:
            return held_by
        await asyncio.sleep(POLL_S)
        waited += POLL_S
