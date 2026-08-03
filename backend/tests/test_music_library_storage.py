"""Unit tests for the Music Library USB storage layer (StorageManager).

Covers the parts that run off the real hardware path: USB-partition
classification, the mount/unmount flows through the milo-mount/milo-umount
helpers (mocked subprocess), the devnode→mountpoint bookkeeping, and the
Navidrome rescan trigger. The pyudev monitor thread itself needs real udev
events and is exercised on the Pi, not here.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.config.constants import MILO_MOUNT_CMD, MILO_UMOUNT_CMD
from backend.sources.music_library.storage import StorageManager


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
    is already running — see StorageManager._trigger_scan."""
    return {"scanning": scanning, "count": 0, "folderCount": 0}


@pytest.fixture
def navidrome():
    """A provisioned Navidrome client that records scan triggers, idle by default."""
    client = AsyncMock()
    client.start_scan = AsyncMock(return_value=True)
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


# === Navidrome unavailable (not yet provisioned) ==================================

async def test_scan_skipped_when_navidrome_unavailable():
    """A daemon that hasn't provisioned its cred file yet yields no client; the
    mount must still complete rather than raise. Nothing else will notice it —
    no inotify event reports a mount — so the scheduled rescan is what catches
    up, and the mount itself must not fail over a catalog that isn't up yet."""
    mgr = StorageManager(_navidrome_provider(None), AsyncMock())
    await mgr._trigger_scan()  # must not raise


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


async def test_unmount_share_tracked(manager, navidrome):
    manager._share_mounts["nas-abcd1234"] = "/media/milo/nas-abcd1234"
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.unmount_share("nas-abcd1234")

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_UMOUNT_CMD)
    assert args[3] == "/media/milo/nas-abcd1234"
    assert manager._share_mounts == {}
    navidrome.start_scan.assert_awaited_once()


async def test_unmount_share_untracked_falls_back_to_deterministic_path(manager):
    # Not in the session map (e.g. mounted before a backend restart) -> derive
    # the deterministic /media/milo/<id> so a delete still unmounts it.
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.unmount_share("nfs-abcd1234")
    assert exec_mock.call_args.args[3] == "/media/milo/nfs-abcd1234"


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
    navidrome.start_scan.assert_awaited_once_with(full=False)


async def test_two_mounts_racing_one_scan_share_a_single_waiter(manager, navidrome):
    """Boot mounts a USB key and a share seconds apart; both find the scanner
    busy. They must collapse into one rescan, not queue a pass each over the
    same tree — and a removal folded in must keep its full scan, which is what
    purges the tracks that left with it."""
    navidrome.get_scan_status.side_effect = (
        [_scan_status(True)] * 3 + [_scan_status(False)]
    )
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")), \
         patch("backend.sources.music_library.storage._SCAN_WAIT_POLL_S", 0):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
        await manager.unmount_share("usb-old")   # defers full=True into the waiter
        await manager._deferred_scan
    navidrome.start_scan.assert_awaited_once_with(full=True)


async def test_an_idle_scanner_is_scanned_straight_away(manager, navidrome):
    """The common path keeps no latency: nothing running, scan now, no waiter."""
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/nas-abcd1234\n")):
        await manager.mount_share({"id": "nas-abcd1234", "type": "cifs",
                                   "host": "nas.local", "path": "music"})
    navidrome.start_scan.assert_awaited_once_with(full=False)
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
