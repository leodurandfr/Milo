"""`milo-set-wifi-country`, run for real under bash against a copy of cmdline.txt.

Both twins rewrite `/boot/firmware/cmdline.txt`, the one file the boot depends
on, on a vfat partition with no journal. They used to truncate it in place
(`printf … > "$CMDLINE_FILE"`) with no lock, no sync and no check of what was
read: two runs at once (the setup wizard calls the backend twice, a satellite
calls it at admission) let one read the file empty between the other's
truncation and write, and write back ` cfg80211.ieee80211_regdom=BE` alone —
no `root=`, a unit that no longer boots until its SD card is repaired
elsewhere. Consumers: `NetworkService.set_country` on the server (through
sudo), `milo-client-join-wifi` on a satellite (as root).
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

HELPERS = {
    "server": REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-set-wifi-country",
    "milo-client": REPO_ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin"
    / "milo-set-wifi-country",
}

CMDLINE_LINE = 'CMDLINE_FILE="/boot/firmware/cmdline.txt"\n'
LOCK_LINE = 'LOCK_FILE="/run/lock/milo-set-wifi-country.lock"\n'

BOOT_LINE = (
    "console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 "
    "fsck.repair=yes rootwait quiet cfg80211.ieee80211_regdom=FR\n"
)


@pytest.fixture(params=sorted(HELPERS))
def world(request, tmp_path):
    """A copy of the helper pointed at tmp_path, and an `iw` stub that logs.

    `iw reg set` needs root and a radio, and /run/lock is root's: the copy's
    two path lines are rewritten, and each rewrite must have happened exactly
    once, or the run would touch the host's real boot partition.
    """
    text = HELPERS[request.param].read_text(encoding="utf-8")
    cmdline = tmp_path / "cmdline.txt"
    lock = tmp_path / "set-wifi-country.lock"
    for line, target in ((CMDLINE_LINE, cmdline), (LOCK_LINE, lock)):
        assert text.count(line) == 1, f"{request.param}: {line.strip()!r} not found exactly once"
        text = text.replace(line, f'{line.split("=", 1)[0]}="{target}"\n')
    helper = tmp_path / "milo-set-wifi-country"
    helper.write_text(text, encoding="utf-8")

    stubs = tmp_path / "bin"
    stubs.mkdir()
    iw = stubs / "iw"
    iw.write_text('#!/bin/sh\necho "iw $*" >> "$STUB_LOG"\n')
    iw.chmod(0o755)
    return {"helper": helper, "cmdline": cmdline, "stubs": stubs, "log": tmp_path / "stub.log"}


def _run(world, country, *, applies=True):
    result = subprocess.run(
        ["bash", str(world["helper"]), country],
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin", "STUB_LOG": str(world["log"])},
        capture_output=True, text=True, timeout=30,
    )
    log = world["log"].read_text() if world["log"].exists() else ""
    if applies:
        # Without this, a PATH that missed the stub runs the host's real iw.
        assert f"iw reg set {country}" in log, (
            f"the helper never reached the iw stub; log: {log!r}, stderr: {result.stderr!r}"
        )
    else:
        assert log == "", f"a refused run still changed the live domain: {log!r}"
    return result


def test_the_helper_replaces_cmdline_by_rename_and_refuses_an_empty_read(world):
    """A change of country lands as a new file renamed over the old one, the
    rest of the line kept byte for byte. A rewrite on the same inode is the
    truncation a power cut or a second run turns into an unbootable unit."""
    cmdline = world["cmdline"]
    cmdline.write_bytes(BOOT_LINE.encode())
    inode = cmdline.stat().st_ino

    result = _run(world, "BE")

    assert result.returncode == 0, result.stderr
    after = cmdline.read_bytes()
    assert cmdline.stat().st_ino != inode
    assert after.count(b"\n") == 1 and after.count(b"root=") == 1
    assert after == BOOT_LINE.replace("regdom=FR", "regdom=BE").encode()
    assert not list(cmdline.parent.glob("*.milo-new"))


def test_an_unchanged_country_does_not_rewrite_the_file(world):
    """The backend applies the country again on every wizard mount and every
    satellite admission: each rewrite of the boot file is one more window for
    a power cut, for nothing. The session still gets the domain (iw ran)."""
    cmdline = world["cmdline"]
    cmdline.write_bytes(BOOT_LINE.encode())
    before = cmdline.stat()

    result = _run(world, "FR")

    assert result.returncode == 0, result.stderr
    after = cmdline.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize(
    "content",
    [b"", b"console=tty1 rootwait quiet\n"],
    ids=["empty", "no-root"],
)
def test_a_cmdline_without_root_is_refused_and_left_intact(world, content):
    """A read that came back empty, or a file already missing its root=, is
    never written back with a regdom token appended: that file boots nothing.
    The refusal changes nothing, the live domain included, so the backend's
    "not saved" is the whole truth."""
    cmdline = world["cmdline"]
    cmdline.write_bytes(content)

    result = _run(world, "BE", applies=False)

    assert result.returncode != 0
    assert cmdline.read_bytes() == content
    assert not list(cmdline.parent.glob("*.milo-new"))
