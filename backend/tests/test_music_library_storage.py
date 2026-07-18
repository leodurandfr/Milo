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


@pytest.fixture
def manager():
    mgr = StorageManager()
    # Pretend Navidrome is provisioned; record scan triggers.
    mgr._navidrome = AsyncMock()
    mgr._navidrome.start_scan = AsyncMock(return_value=True)
    return mgr


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

async def test_mount_records_and_triggers_scan(manager):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(stdout=b"/media/milo/USBKEY\n")) as exec_mock:
        await manager._mount("/dev/sda1")

    # milo-mount invoked via sudo -n with the devnode.
    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_MOUNT_CMD)
    assert args[3] == "/dev/sda1"
    # Mountpoint captured from stdout (whitespace stripped), scan triggered.
    assert manager._mounts == {"/dev/sda1": "/media/milo/USBKEY"}
    manager._navidrome.start_scan.assert_awaited_once()


async def test_mount_duplicate_is_ignored(manager):
    manager._mounts["/dev/sda1"] = "/media/milo/USBKEY"
    with patch("asyncio.create_subprocess_exec") as exec_mock:
        await manager._mount("/dev/sda1")
    exec_mock.assert_not_called()
    manager._navidrome.start_scan.assert_not_awaited()


async def test_mount_helper_failure_records_nothing(manager):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(returncode=1, stderr=b"not a usb partition")):
        await manager._mount("/dev/sda1")
    assert manager._mounts == {}
    # No scan on a failed mount — nothing changed under /media/milo.
    manager._navidrome.start_scan.assert_not_awaited()


# === unmount flow =================================================================

async def test_unmount_tracked_device(manager):
    manager._mounts["/dev/sda1"] = "/media/milo/USBKEY"
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc()) as exec_mock:
        await manager._unmount("/dev/sda1")

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_UMOUNT_CMD)
    assert args[3] == "/media/milo/USBKEY"
    assert manager._mounts == {}
    manager._navidrome.start_scan.assert_awaited_once()


async def test_unmount_untracked_device_is_noop(manager):
    with patch("asyncio.create_subprocess_exec") as exec_mock:
        await manager._unmount("/dev/sdb1")
    exec_mock.assert_not_called()
    manager._navidrome.start_scan.assert_not_awaited()


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
    mgr = StorageManager()  # no _navidrome set
    with patch(
        "backend.sources.music_library.storage.NavidromeClient.from_cred_file",
        return_value=None,
    ):
        # Should not raise even though there is no client to scan with.
        await mgr._trigger_scan()
    assert mgr._navidrome is None


# === network shares (SMB/NFS) =====================================================

_CIFS_SHARE = {"id": "nas-abcd1234", "type": "cifs", "host": "192.168.1.10", "path": "Music"}
_NFS_SHARE = {"id": "nfs-abcd1234", "type": "nfs", "host": "10.0.0.5", "path": "/volume1/music"}


async def test_mount_share_passes_args_and_credentials_on_stdin(manager):
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
    manager._navidrome.start_scan.assert_awaited_once()


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


async def test_mount_share_failure_records_nothing(manager):
    with patch("asyncio.create_subprocess_exec",
               return_value=_proc(returncode=1, stderr=b"cifs mount failed")):
        mp = await manager.mount_share(_CIFS_SHARE, credentials={"password": "p"})
    assert mp is None
    assert manager._share_mounts == {}
    manager._navidrome.start_scan.assert_not_awaited()


async def test_unmount_share_tracked(manager):
    manager._share_mounts["nas-abcd1234"] = "/media/milo/nas-abcd1234"
    with patch("asyncio.create_subprocess_exec", return_value=_proc()) as exec_mock:
        await manager.unmount_share("nas-abcd1234")

    args = exec_mock.call_args.args
    assert args[:3] == ("sudo", "-n", MILO_UMOUNT_CMD)
    assert args[3] == "/media/milo/nas-abcd1234"
    assert manager._share_mounts == {}
    manager._navidrome.start_scan.assert_awaited_once()


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
