"""`milo-set-password`, run for real under bash against a stub chpasswd.

The policy grants the helper with no argument at all (`""`), and the helper
refuses one itself as well: the password comes on stdin, and an argv is a
caller that bypassed the route — on the server, whose grant is bare, the
helper's own refusal is the only one. A helper that went on to set the password
anyway would also have changed the `milo` account (SSH, sudo) on a command line
every process on the unit can read. Consumer: `POST /api/system/password`
(routes/system.py), and the server's own route through its twin in `rootfs/`,
held equal by `test_twin_files_have_not_drifted`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-set-password"
MARKER_LINE = 'MARKER="/var/lib/milo/password-changed"\n'
PASSWORD = "correct-horse-battery"

# chpasswd as the helper reaches it: what it is fed on stdin, appended to a log.
CHPASSWD_STUB = r'''
import os, sys

with open(os.environ["CHPASSWD_LOG"], "a") as f:
    f.write(sys.stdin.read())
'''


@pytest.fixture
def world(tmp_path):
    """A copy of the helper writing its marker under tmp_path, and stubs for `id` and chpasswd.

    `id` is stubbed so the result never depends on whether the host has a
    `milo` account; chpasswd sits in no directory of the PATH below but the
    stubs', so a stub that went missing fails the run instead of reaching the
    real one.
    """
    text = HELPER.read_text(encoding="utf-8")
    assert text.count(MARKER_LINE) == 1, f"{MARKER_LINE.strip()!r} not found exactly once"
    marker = tmp_path / "state" / "password-changed"
    helper = tmp_path / "milo-set-password"
    helper.write_text(text.replace(MARKER_LINE, f'MARKER="{marker}"\n'), encoding="utf-8")

    stubs = tmp_path / "bin"
    stubs.mkdir()
    chpasswd = stubs / "chpasswd"
    chpasswd.write_text(f"#!{sys.executable}\n{CHPASSWD_STUB}")
    chpasswd.chmod(0o755)
    id_stub = stubs / "id"
    id_stub.write_text("#!/bin/sh\nexit 0\n")
    id_stub.chmod(0o755)
    return {"helper": helper, "stubs": stubs, "marker": marker, "log": tmp_path / "chpasswd.log"}


def _run(world, args=()):
    result = subprocess.run(
        ["bash", str(world["helper"]), *args],
        input=f"{PASSWORD}\n",
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin", "CHPASSWD_LOG": str(world["log"])},
        capture_output=True, text=True, timeout=30,
    )
    fed = world["log"].read_text() if world["log"].exists() else None
    return result, fed


def test_the_password_read_on_stdin_is_set_and_marked(world):
    """The run the refusal below is measured against: same world, same stdin, no argument."""
    result, fed = _run(world)

    assert result.returncode == 0, result.stderr
    assert fed == f"milo:{PASSWORD}\n"
    assert world["marker"].is_file()


@pytest.mark.parametrize("args", [(PASSWORD,), ("--help",), ("",)])
def test_an_argument_is_refused_whatever_it_says(world, args):
    """Even an empty one: sudo's `""` grant refuses it too, so only a bypassing caller sends it."""
    result, fed = _run(world, args)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.startswith("ERROR:")
    assert fed is None
    assert not world["marker"].exists()
