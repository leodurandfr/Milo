"""`milo-client-finish-dpkg`, run for real under bash with dpkg stubbed.

An app update runs it as root before it installs anything
(services/app_update.py), and reads its exit status: 0 goes on, 75 refuses the
push as a conflict (another process took dpkg's lock), anything else is logged
and the update goes on. unattended-upgrades runs on every satellite, and a power
cut in the middle of its run leaves dpkg "interrupted": every later install —
a snapclient update, unattended-upgrades' own next run until it repairs it — is
then refused by apt.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import services.app_update as app_update_module

HELPER = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-finish-dpkg"
)
# What dpkg 1.22 (trixie, the image's release) writes on a held lock, measured:
# the line that names the holder, then a note on removing the lock. When it
# cannot name the process, "by another process" (its own strings, `strings
# /usr/bin/dpkg`).
LOCK_NOTE = (
    "Note: removing the lock file is always wrong, can damage the locked area\n"
    "and the entire system. See <https://wiki.debian.org/Teams/Dpkg/FAQ#db-lock>."
)
LOCKED_LINES = (
    "dpkg: error: dpkg frontend lock was locked by /usr/bin/python3.13 process with pid 812",
    "dpkg: error: dpkg database lock was locked by another process",
)


@pytest.fixture
def world():
    """A dpkg stub that logs its argv, the frontend and the locale it was
    given, then answers what the test asks ($STUB_DPKG_OUT on stderr,
    $STUB_DPKG_RC)."""
    root = Path(tempfile.mkdtemp())
    stubs = root / "bin"
    stubs.mkdir()
    dpkg = stubs / "dpkg"
    dpkg.write_text(
        "#!/bin/sh\n"
        'echo "dpkg $* frontend=$DEBIAN_FRONTEND lc=$LC_ALL" >> "$STUB_LOG"\n'
        '[ -z "$STUB_DPKG_OUT" ] || echo "$STUB_DPKG_OUT" >&2\n'
        'exit "${STUB_DPKG_RC:-0}"\n'
    )
    dpkg.chmod(0o755)
    yield {"stubs": stubs, "log": root / "stub.log"}
    shutil.rmtree(root, ignore_errors=True)


def _run(world, *args, rc=0, out=""):
    env = {
        "PATH": f"{world['stubs']}:/usr/bin:/bin",
        # A unit set in French: dpkg would answer in French unless told not to.
        "LANG": "fr_FR.UTF-8",
        "STUB_LOG": str(world["log"]),
        "STUB_DPKG_RC": str(rc),
        "STUB_DPKG_OUT": out,
    }
    result = subprocess.run(
        ["bash", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=30,
    )
    log = world["log"].read_text().splitlines() if world["log"].exists() else []
    return result, log


def test_it_finishes_dpkg_without_asking_anything(world):
    """Keeping a configuration file Milō rewrote, never prompting: nobody is
    there to answer, and a prompt would hang the update behind it. In C, so
    the words matched for a held lock are dpkg's untranslated ones."""
    result, log = _run(world)

    assert result.returncode == 0, result.stderr
    assert log == ["dpkg --configure -a --force-confdef --force-confold frontend=noninteractive lc=C"]


@pytest.mark.parametrize("locked", LOCKED_LINES, ids=["named", "unnamed"])
def test_a_lock_another_process_took_is_its_own_answer(world, locked):
    """The exit the updater reads as "refuse the push, nothing was run" — and
    the line it quotes is the one naming the holder, not the note under it."""
    result, _ = _run(world, rc=2, out=f"{locked}\n{LOCK_NOTE}")

    assert result.returncode == app_update_module.FINISH_DPKG_BUSY, result.stderr
    assert result.stdout.strip() == locked, "the updater's refusal quotes dpkg's own line"


def test_a_dpkg_that_cannot_finish_says_why(world):
    result, _ = _run(world, rc=1, out="dpkg: error processing package avahi-daemon (--configure):")

    assert result.returncode == 1
    assert "avahi-daemon" in result.stderr


def test_an_argument_is_refused_before_dpkg_runs(world):
    """The policy grants it with no argument (`""`), and it refuses one
    itself: reached by hand as root, an argv nobody vetted reads nothing."""
    result, log = _run(world, "--force-all")

    assert result.returncode == 2
    assert log == []
