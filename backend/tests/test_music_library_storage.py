"""Unit tests for the Music Library USB storage layer (StorageManager).

Covers the parts that run off the real hardware path: USB-partition
classification, the mount/unmount flows through the milo-mount/milo-umount
helpers (mocked subprocess), the devnode→mountpoint bookkeeping, and the
Navidrome rescan trigger. The pyudev monitor thread itself needs real udev
events and is exercised on the Pi, not here.
"""
import logging
import asyncio
import getpass
import re
import socket
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.config.constants import MILO_MOUNT_CMD, MILO_UMOUNT_CMD
from backend.sources.music_library.navidrome_client import ScanRequest, ScanStatus
from backend.sources.music_library import storage
from backend.sources.music_library.storage import StorageManager

MILO_MOUNT_SCRIPT = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-mount"
)


class _Dev(dict):
    """Minimal stand-in for a pyudev Device (only .get is used for classification)."""

    def get(self, key, default=None):
        return dict.get(self, key, default)


def _proc(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = lambda: None
    return proc


def _volume(mountpoint, uuid="1234-ABCD", label="USB KEY"):
    """A row of the manager's devnode→volume map."""
    return {"mountpoint": mountpoint, "uuid": uuid, "label": label}


def _navidrome_provider(client=None):
    """Stand-in for the source's shared-client accessor (the manager's only way
    to reach the catalog). ``None`` models a not-yet-provisioned daemon."""
    return AsyncMock(return_value=client)


def _scan_status(scanning):
    """One getScanStatus reply. Navidrome answers startScan with "ok" even when
    it drops the request, so this reply is the manager's ONLY way to know a scan
    is already running — see StorageManager.request_scan."""
    return ScanStatus(available=True, scanning=scanning)


@pytest.fixture
def navidrome():
    """A provisioned Navidrome client that records scan triggers, idle by default."""
    client = AsyncMock()
    client.start_scan = AsyncMock(return_value=ScanRequest.STARTED)
    client.get_scan_status = AsyncMock(return_value=_scan_status(False))
    return client


@pytest.fixture
def manager(navidrome):
    return StorageManager(_navidrome_provider(navidrome), AsyncMock())


# === classification ===============================================================

@pytest.mark.parametrize("props,expected", [
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "vfat"}, True),
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "exfat"}, True),
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "ntfs"}, True),
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "ext4"}, True),
    # not USB (SD card / internal)
    ({"ID_BUS": None, "DEVTYPE": "partition", "ID_FS_TYPE": "ext4"}, False),
    # whole disk, not a partition
    ({"ID_BUS": "usb", "DEVTYPE": "disk", "ID_FS_TYPE": "vfat"}, False),
    # no filesystem
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": None}, False),
    # unsupported fs (swap partition)
    ({"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "swap"}, False),
])
def test_is_usb_fs_partition(props, expected):
    assert StorageManager._is_usb_fs_partition(_Dev(props)) is expected


# === mount flow ===================================================================

async def test_mount_records_and_triggers_scan(manager, navidrome):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/USBKEY\n")) as exec_mock:
        await manager._mount("/dev/sda1", "1234-ABCD", "USB KEY")

    # milo-mount invoked via sudo -n with the devnode.
    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_MOUNT_CMD)
    assert args[3] == "/dev/sda1"
    # Mountpoint captured from stdout (whitespace stripped), filed with the
    # filesystem identity a user-given name is keyed by; scan triggered.
    assert manager._mounts == {
        "/dev/sda1": {
            "mountpoint": "/media/milo/USBKEY",
            "uuid": "1234-ABCD",
            "label": "USB KEY",
        }
    }
    navidrome.start_scan.assert_awaited_once()


async def test_mount_duplicate_is_ignored(manager, navidrome):
    manager._mounts["/dev/sda1"] = _volume("/media/milo/USBKEY")
    with patch("asyncio.create_subprocess_exec") as exec_mock:
        await manager._mount("/dev/sda1")
    exec_mock.assert_not_called()
    navidrome.start_scan.assert_not_awaited()


async def test_mount_helper_failure_records_nothing(manager, navidrome):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(returncode=1, stderr=b"not a usb partition")):
        await manager._mount("/dev/sda1")
    assert manager._mounts == {}
    # No scan on a failed mount — nothing changed under /media/milo.
    navidrome.start_scan.assert_not_awaited()


# === unmount flow =================================================================

async def test_unmount_tracked_device(manager, navidrome):
    manager._mounts["/dev/sda1"] = _volume("/media/milo/USBKEY")
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc()) as exec_mock:
        await manager._unmount("/dev/sda1")

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_UMOUNT_CMD)
    assert args[3] == "/media/milo/USBKEY"
    assert manager._mounts == {}
    # No scan on removal, and that is the whole point: the key keeps its library
    # and its index while it is away, so a replug costs a quick scan instead of
    # re-reading 10 000 tags. A full scan here would purge them outright
    # (PurgeMissing="full") and a quick one would walk a path that is now gone.
    navidrome.start_scan.assert_not_awaited()


async def test_unmount_untracked_device_is_noop(manager, navidrome):
    with patch("asyncio.create_subprocess_exec") as exec_mock:
        await manager._unmount("/dev/sdb1")
    exec_mock.assert_not_called()
    navidrome.start_scan.assert_not_awaited()


# === helper subprocess ============================================================

async def test_run_helper_returns_stdout_on_success(manager):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/X\n")):
        out = await manager._run_helper(MILO_MOUNT_CMD, "/dev/sda1", capture=True)
    assert out == "/media/milo/X"


async def test_run_helper_returns_none_on_failure(manager):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(returncode=1, stderr=b"boom")):
        out = await manager._run_helper(MILO_MOUNT_CMD, "/dev/sda1", capture=True)
    assert out is None


async def test_run_helper_times_out(manager):
    proc = _proc()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        out = await manager._run_helper(MILO_MOUNT_CMD, "/dev/sda1", capture=True)
    assert out is None


def test_the_helper_timeout_outlasts_milo_mounts_worst_case():
    """milo-mount survives sudo's death (it runs as root, reparented to PID 1),
    so a backend bound shorter than its worst case answers "not mounted" for a
    share that mounts a few seconds later — and is then never scanned. The worst
    case is a host that answers the reachability probe at its last second, then
    the network type with the most mount attempts in series (CIFS: a modern SMB
    attempt, then a vers=2.0 fallback), each capped at NET_MOUNT_TIMEOUT."""
    script = MILO_MOUNT_SCRIPT.read_text()
    found = re.search(r"^NET_MOUNT_TIMEOUT=(\d+)$", script, re.MULTILINE)
    assert found, f"NET_MOUNT_TIMEOUT not found in {MILO_MOUNT_SCRIPT}"
    per_attempt = int(found.group(1))
    probe = re.search(r"^PROBE_TIMEOUT=(\d+)$", script, re.MULTILINE)
    assert probe, f"PROBE_TIMEOUT not found in {MILO_MOUNT_SCRIPT}"
    attempts = {}
    for fstype in re.findall(r'timeout "\$NET_MOUNT_TIMEOUT" mount -t (\w+)', script):
        attempts[fstype] = attempts.get(fstype, 0) + 1
    assert attempts.get("cifs", 0) >= 2 and attempts.get("nfs", 0) >= 1, (
        f"mount attempts not found in {MILO_MOUNT_SCRIPT}: {attempts}"
    )
    worst_case = int(probe.group(1)) + max(attempts.values()) * per_attempt
    assert storage._HELPER_TIMEOUT_S > worst_case, (
        f"_HELPER_TIMEOUT_S={storage._HELPER_TIMEOUT_S} does not outlast milo-mount's "
        f"worst case of {worst_case} s (probe {probe.group(1)} s, {attempts}, "
        f"{per_attempt} s each)"
    )



# --- milo-mount over a host that does not answer -----------------------------

def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _milo_mount_copy(tmp_path: Path, port: int) -> tuple[Path, dict]:
    """milo-mount as it ships, pointed at tmp_path and at ``port``, with a stub
    mount(8) that records whether the mountpoint existed when it ran.

    Only the paths, the user, the root check and the two share ports are
    rewritten; each rewrite is asserted to have applied, so a renamed line fails
    here instead of leaving the real /media/milo in the copy."""
    script = MILO_MOUNT_SCRIPT.read_text()
    root = tmp_path / "media"
    rewrites = {
        'MOUNT_ROOT="/media/milo"': f'MOUNT_ROOT="{root}"',
        'CRED_DIR="/var/lib/milo/shares"': f'CRED_DIR="{tmp_path / "creds"}"',
        'MILO_USER="milo"': f'MILO_USER="{getpass.getuser()}"',
        '[ "$(id -u)" -eq 0 ] || die "must run as root"': ":",
        "CIFS_PORT=445": f"CIFS_PORT={port}",
        "NFS_PORT=2049": f"NFS_PORT={port}",
    }
    for old, new in rewrites.items():
        assert script.count(old) == 1, f"{old!r} not found once in {MILO_MOUNT_SCRIPT}"
        script = script.replace(old, new)
    copy = tmp_path / "milo-mount"
    copy.write_text(script)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "mount"
    stub.write_text(
        '#!/bin/bash\n'
        'target="${@: -1}"\n'
        '[ -d "$target" ] && echo present >> "$MOUNT_LOG" || echo absent >> "$MOUNT_LOG"\n'
        'exit "$MOUNT_RC"\n'
    )
    stub.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "MOUNT_LOG": str(tmp_path / "mount.log"),
        "MOUNT_RC": "32",
    }
    return copy, env


def _run_milo_mount(copy: Path, env: dict, fstype: str, stdin: str = ""):
    return subprocess.run(
        ["bash", str(copy), "--network", fstype, "--id", "nas-1",
         "--host", "127.0.0.1", "--path", "music"],
        input=stdin, capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.mark.parametrize("fstype", ["cifs", "nfs"])
def test_a_mute_host_never_gets_an_empty_mountpoint(tmp_path, fstype):
    """Every remount pass against an offline NAS goes through milo-mount, and
    the plateau retries it every 5 min with no end. Navidrome reads a present but
    empty /media/milo/<id> as a library whose every track is missing, and nothing
    stops its 6-hourly schedule or the source's open-rescan from landing while a
    mount attempt is in flight — so the mountpoint must not exist until the host
    answers. Fails when milo-mount creates it before asking, as it did for the
    30-40 s of two CIFS timeouts. Consumer: SharesService's remount task."""
    copy, env = _milo_mount_copy(tmp_path, _closed_port())
    result = _run_milo_mount(copy, env, fstype)

    assert result.returncode != 0
    assert "does not answer" in result.stderr, result.stderr
    assert not (tmp_path / "mount.log").exists(), "mount(8) ran against a mute host"
    assert not (tmp_path / "media" / "nas-1").exists()


@pytest.mark.parametrize("fstype", ["cifs", "nfs"])
def test_a_host_that_answers_is_mounted_on_its_mountpoint(tmp_path, fstype):
    """The probe must not stand between a reachable NAS and its mount: the
    mountpoint exists when mount(8) runs and its path is the helper's answer."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        copy, env = _milo_mount_copy(tmp_path, listener.getsockname()[1])
        env["MOUNT_RC"] = "0"
        result = _run_milo_mount(copy, env, fstype)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "media" / "nas-1")
    assert (tmp_path / "mount.log").read_text().split() == ["present"]


def test_credentials_are_kept_when_the_host_does_not_answer(tmp_path):
    """A share added while its NAS is off is saved and left to the remount task,
    whose attempts feed milo-mount an empty stdin and rely on the cred file. A
    probe that refused before persisting the credentials would leave every later
    attempt mounting as guest, refused for good once the NAS is back."""
    copy, env = _milo_mount_copy(tmp_path, _closed_port())
    result = _run_milo_mount(copy, env, "cifs", stdin="username=leo\npassword=secret\n")

    assert result.returncode != 0
    assert (tmp_path / "creds" / "nas-1.cred").read_text().split() == [
        "username=leo", "password=secret",
    ]

# === Navidrome unavailable (not yet provisioned) ==================================

async def test_scan_skipped_when_navidrome_unavailable():
    """A daemon that hasn't provisioned its cred file yet yields no client; the
    mount must still complete rather than raise. Nothing else will notice it —
    no inotify event reports a mount — so the scheduled rescan is what catches
    up, and the mount itself must not fail over a catalog that isn't up yet."""
    mgr = StorageManager(_navidrome_provider(None), AsyncMock())
    await mgr.request_scan()  # must not raise


# === network shares (SMB/NFS) =====================================================

_CIFS_SHARE = {"id": "nas-abcd1234", "type": "cifs", "host": "192.168.1.10", "path": "Music"}
_NFS_SHARE = {"id": "nfs-abcd1234", "type": "nfs", "host": "10.0.0.5", "path": "/volume1/music"}


async def test_mount_share_passes_args_and_credentials_on_stdin(manager, navidrome):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")) as exec_mock:
        mp = await manager.mount_share(
            _CIFS_SHARE,
            credentials={"username": "SECRETUSER", "password": "SECRETPASS", "domain": "WG"},
        )

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_MOUNT_CMD)
    # --network cifs --id <id> --host <host> --path <path>
    assert list(args[3:]) == [
        "--network", "cifs", "--id", "nas-abcd1234",
        "--host", "192.168.1.10", "--path", "Music",
    ]
    # Credentials go over stdin (PIPE), never argv.
    assert exec_mock.call_args.kwargs["stdin"] == asyncio.subprocess.PIPE
    joined = " ".join(args)
    assert "SECRETUSER" not in joined and "SECRETPASS" not in joined
    assert mp == "/media/milo/nas-abcd1234"
    assert manager._share_mounts == {"nas-abcd1234": "/media/milo/nas-abcd1234"}
    navidrome.start_scan.assert_awaited_once()


async def test_mount_share_credentials_stdin_bytes(manager):
    captured = {}

    def _make(*a, **k):
        proc = _proc(stdout=b"/media/milo/nas-abcd1234\n")

        async def _comm(input=None):
            captured["input"] = input
            return (b"/media/milo/nas-abcd1234\n", b"")
        proc.communicate = _comm
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_make):
        await manager.mount_share(_CIFS_SHARE, credentials={"username": "u", "password": "p"})

    assert captured["input"] == b"username=u\npassword=p\n"


async def test_mount_share_boot_remount_uses_devnull_stdin(manager):
    # No credentials (boot remount) -> stdin is /dev/null, milo-mount reuses the
    # persisted cred file.
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nfs-abcd1234\n")) as exec_mock:
        await manager.mount_share(_NFS_SHARE)

    assert exec_mock.call_args.kwargs["stdin"] == asyncio.subprocess.DEVNULL
    args = exec_mock.call_args.args
    assert list(args[3:]) == [
        "--network", "nfs", "--id", "nfs-abcd1234",
        "--host", "10.0.0.5", "--path", "/volume1/music",
    ]


async def test_mount_share_failure_records_nothing(manager, navidrome):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(returncode=1, stderr=b"cifs mount failed")):
        mp = await manager.mount_share(_CIFS_SHARE, credentials={"password": "p"})
    assert mp is None
    assert manager._share_mounts == {}
    navidrome.start_scan.assert_not_awaited()


async def test_a_quiet_mount_failure_raises_no_error(manager, caplog):
    """The share remount loop's plateau attempts ask for quiet: an error reaches
    every screen's banner, and a NAS that stays off would raise one every five
    minutes for as long as it is away."""
    with caplog.at_level(logging.DEBUG, logger="source.music_library.storage"):
        with patch("asyncio.create_subprocess_exec",
                   return_value=_proc(returncode=1, stderr=b"cifs mount failed")):
            assert await manager.mount_share(_CIFS_SHARE, quiet=True) is None
            assert await manager.mount_share(_CIFS_SHARE) is None

    assert [r.levelno for r in caplog.records if "failed" in r.getMessage()] == [
        logging.DEBUG, logging.ERROR,
    ]


async def test_unmount_share_unmounts_and_scans_nothing(manager, navidrome):
    """An unmount runs no scan — a scan here would walk a path that no longer
    exists, and the removal's own reconcile is what retires the library."""
    manager._share_mounts["nas-abcd1234"] = "/media/milo/nas-abcd1234"
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.unmount_share("nas-abcd1234")

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_UMOUNT_CMD)
    assert args[3] == "/media/milo/nas-abcd1234"
    assert manager._share_mounts == {}
    navidrome.start_scan.assert_not_awaited()


async def test_unmount_share_untracked_falls_back_to_deterministic_path(manager):
    # Not in the session map (e.g. mounted before a backend restart) -> derive
    # the deterministic /media/milo/<id> so a delete still unmounts it.
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.unmount_share("nfs-abcd1234")
    assert exec_mock.call_args.args[3] == "/media/milo/nfs-abcd1234"


async def test_a_failed_unmount_is_reported(manager, caplog):
    """milo-umount refusing (a dead NAS whose mount stayed in the table) is an
    error naming the mountpoint, never "Unmounted share" — an edit or a removal
    otherwise reports success over a mount only a reboot will clear."""
    manager._share_mounts["nas-abcd1234"] = "/media/milo/nas-abcd1234"
    failure = _proc(
        returncode=1,
        stderr=b"milo-umount: ERROR: /media/milo/nas-abcd1234 is still mounted",
    )
    with caplog.at_level(logging.INFO, logger="source.music_library.storage"), \
            patch("asyncio.create_subprocess_exec", return_value=failure):
        assert await manager.unmount_share("nas-abcd1234") is False

    messages = [r.getMessage() for r in caplog.records]
    assert not any("Unmounted" in m for m in messages)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("/media/milo/nas-abcd1234" in m for m in errors)


async def test_a_confirmed_share_unmount_says_so(manager, caplog):
    with caplog.at_level(logging.INFO, logger="source.music_library.storage"), \
            patch("asyncio.create_subprocess_exec", return_value=_proc()):
        assert await manager.unmount_share("nas-abcd1234") is True
    assert any("Unmounted share nas-abcd1234" in r.getMessage() for r in caplog.records)


async def test_a_failed_usb_unmount_is_not_announced(manager, caplog):
    manager._mounts["/dev/sda1"] = _volume("/media/milo/USBKEY")
    with caplog.at_level(logging.INFO, logger="source.music_library.storage"), \
            patch("asyncio.create_subprocess_exec",
                  return_value=_proc(returncode=1, stderr=b"still mounted")):
        await manager._unmount("/dev/sda1")
    assert not any("Unmounted" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_the_mount_table_is_unescaped_as_the_kernel_escapes_it():
    """A share path or key label with a space is listed as ``My\\040Music``;
    read raw, the connected indicator and the helpers would disagree on what is
    mounted. Field 4 (the root inside the filesystem) is never the mountpoint."""
    mountinfo = (
        "36 25 0:40 / /media/milo/My\\040Music ro,nosuid - cifs //nas/m ro\n"
        "37 25 0:41 / /media/milo/a\\134040b ro - cifs //nas/b ro\n"
        "38 25 0:42 /media/milo/elsewhere /mnt/bind rw - ext4 /dev/sda1 rw\n"
        "garbage\n"
    )
    assert storage.mount_table_targets(mountinfo) == [
        "/media/milo/My Music",
        "/media/milo/a\\040b",
        "/mnt/bind",
    ]


async def test_forget_share_credentials(manager):
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.forget_share_credentials("nas-abcd1234")
    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_MOUNT_CMD)
    assert list(args[3:]) == ["--forget", "--id", "nas-abcd1234"]


# === credential encoding ==========================================================

@pytest.mark.parametrize("creds,expected", [
    ({"username": "u", "password": "p", "domain": "d"}, b"username=u\npassword=p\ndomain=d\n"),
    ({"password": "p"}, b"password=p\n"),
    ({"username": "u", "password": "p"}, b"username=u\npassword=p\n"),
    ({"username": "", "password": ""}, b""),  # nothing usable
    ({}, b""),
])
def test_encode_credentials(creds, expected):
    assert StorageManager._encode_credentials(creds) == expected


# === rescan against a busy scanner ================================================
# The boot bug these cover: Navidrome's own startup scan began while /media/milo
# was still an empty directory, the share mounted four seconds later, and the
# mount's rescan landed mid-scan. Navidrome answered "ok", logged "already
# scanning" to its own journal, and indexed nothing — leaving every track in the
# library flagged missing and the source serving an empty catalog for 16 hours.

async def test_mount_during_a_scan_does_not_fire_into_it(manager, navidrome):
    """A scan already running started before this mount and cannot see it.

    Firing anyway is not harmless-but-useless: it is the exact call Navidrome
    accepts with HTTP 200 and silently drops, which is what made the loss
    invisible. Nothing may be asked of a busy scanner.
    """
    navidrome.get_scan_status.return_value = _scan_status(True)
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
    navidrome.start_scan.assert_not_awaited()
    assert manager._deferred_scan is not None


async def test_the_deferred_scan_runs_once_the_scanner_goes_idle(manager, navidrome):
    """The mount is still owed its scan — the wait defers it, never drops it."""
    navidrome.get_scan_status.side_effect = [
        _scan_status(True),    # at mount time
        _scan_status(True),    # first poll of the waiter
        _scan_status(False),   # scanner free
    ]
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")), \
         patch("backend.sources.music_library.storage._SCAN_WAIT_POLL_S", 0):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
        await manager._deferred_scan
    navidrome.start_scan.assert_awaited_once_with()


async def test_two_mounts_racing_one_scan_share_a_single_waiter(manager, navidrome):
    """Boot mounts a USB key and a share seconds apart; both find the scanner
    busy. They must collapse into one rescan, not queue a pass each over the
    same tree."""
    navidrome.get_scan_status.side_effect = (
        [_scan_status(True)] * 3 + [_scan_status(False)]
    )
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")), \
         patch("backend.sources.music_library.storage._SCAN_WAIT_POLL_S", 0):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
        await manager.mount_share({"id": "nfs-abcd1234", "type": "nfs",
                                   "host": "nas.local", "path": "export"})
        await manager._deferred_scan
    navidrome.start_scan.assert_awaited_once_with()


async def test_an_idle_scanner_is_scanned_straight_away(manager, navidrome):
    """The common path keeps no latency: nothing running, scan now, no waiter."""
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
    navidrome.start_scan.assert_awaited_once_with()
    assert manager._deferred_scan is None


async def test_unreadable_scan_status_scans_rather_than_skips(manager, navidrome):
    """A poll that fails must not swallow the rescan: a needless incremental
    pass costs seconds, a skipped one costs a storage space until the hour."""
    navidrome.get_scan_status.side_effect = OSError("connection refused")
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
    navidrome.start_scan.assert_awaited_once()


async def test_cleanup_cancels_a_pending_waiter(manager, navidrome):
    """Teardown must not leave the waiter polling a daemon that is going away."""
    navidrome.get_scan_status.return_value = _scan_status(True)
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
    pending = manager._deferred_scan
    await manager.cleanup()
    assert pending.cancelled()
    assert manager._deferred_scan is None


async def test_a_catalog_that_is_not_up_yet_is_not_reported_as_a_refusal(
    manager, navidrome, caplog
):
    """The mount path fires a scan the moment a key appears — which, at boot, is
    while Navidrome is still starting. Logged as a refusal it became a warning
    per plugged-in key, for a daemon that had simply not answered yet."""
    navidrome.start_scan.return_value = ScanRequest.UNAVAILABLE

    with caplog.at_level(logging.WARNING, logger="source.music_library.storage"):
        with patch("asyncio.create_subprocess_exec",
                   return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
            await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                       "host": "nas.local", "path": "music"})

    navidrome.start_scan.assert_awaited_once()
    assert caplog.records == []


async def test_a_scan_navidrome_really_refused_is_still_warned_about(
    manager, navidrome, caplog
):
    navidrome.start_scan.return_value = ScanRequest.REFUSED

    with caplog.at_level(logging.WARNING, logger="source.music_library.storage"):
        with patch("asyncio.create_subprocess_exec",
                   return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
            await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                       "host": "nas.local", "path": "music"})

    assert any("refused" in r.getMessage() for r in caplog.records)
