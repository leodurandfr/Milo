# backend/sources/music_library/storage.py
"""USB storage layer for the Music Library source (Phase 1).

Navidrome indexes whatever is mounted under /media/milo but never mounts anything
itself — that is this module's job. ``StorageManager`` watches the ``block``
subsystem with pyudev (unprivileged, the analog of the CD disc-watcher), mounts
each USB partition read-only under the mount root through the ``milo-mount``
privileged helper, and asks Navidrome to rescan. On removal it unmounts through
``milo-umount`` and rescans so Navidrome drops the tracks that vanished with the
key.

Runs for the whole backend lifetime, independent of playback: a plugged-in key
gets indexed even when music_library is not the active source (Navidrome is
always-on).

Phase 2 adds SMB/NFS network shares. Unlike USB there is no hotplug event to
rediscover them, so their config is persisted (MusicLibraryDataService) and the
source replays it at boot via :meth:`mount_share`. The same milo-mount helper
mounts them read-only under the mount root; CIFS credentials are handed to it on
stdin (never argv) and it persists them to a root-only cred file the milo backend
cannot read. See docs/plans/music-library.md.

Fail-open throughout: no udev (a dev host without libudev), no sudoers rule, or
Navidrome not provisioned yet must degrade to "auto-mount disabled" and log,
never crash the backend.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from backend.config.constants import (
    MILO_MOUNT_CMD,
    MILO_UMOUNT_CMD,
    MUSIC_LIBRARY_MOUNT_ROOT,
)
from backend.sources.music_library.navidrome_client import NavidromeClient

logger = logging.getLogger("source.music_library.storage")

# Filesystems worth mounting — mirrors milo-mount's allowlist. The helper is the
# real security boundary and re-validates independently; this is only a fast
# pre-filter so we don't shell out for a partition Navidrome couldn't read anyway.
_MOUNTABLE_FSTYPES = frozenset(
    {"vfat", "exfat", "ntfs", "ext2", "ext3", "ext4", "hfsplus"}
)

# Ceiling on a single mount/unmount helper call (a slow spin-up USB key still
# mounts well within this; a hang must not wedge the monitor).
_HELPER_TIMEOUT_S = 30.0


class StorageManager:
    """Keeps /media/milo in sync with the USB drives that are plugged in.

    Owns a pyudev monitor thread (started in :meth:`initialize`, stopped in
    :meth:`cleanup`) and a devnode→mountpoint map so a ``remove`` event can
    unmount the right target. All mount/unmount go through the milo-mount /
    milo-umount sudoers helpers; Navidrome is told to rescan after every change.
    """

    def __init__(self) -> None:
        self.logger = logger
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._observer = None  # pyudev.MonitorObserver (monitor thread)
        self._navidrome: Optional[NavidromeClient] = None
        # Serializes mount/unmount so concurrent hotplug events can't race on the
        # mountpoint table or the helpers.
        self._lock = asyncio.Lock()
        # devnode (/dev/sda1) -> mountpoint (/media/milo/<label>)  [USB]
        self._mounts: Dict[str, str] = {}
        # share id -> mountpoint (/media/milo/<id>)  [network shares]
        self._share_mounts: Dict[str, str] = {}

    async def initialize(self) -> bool:
        """Mount any USB drive already present, then start the hotplug monitor.

        Returns False (auto-mount disabled) when udev is unavailable — e.g. a dev
        host without libudev — so the backend keeps running, just without USB.
        """
        self._loop = asyncio.get_running_loop()

        try:
            import pyudev
        except Exception as exc:
            self.logger.warning("pyudev unavailable, USB auto-mount disabled: %s", exc)
            return False

        try:
            context = pyudev.Context()
        except Exception as exc:
            self.logger.warning(
                "udev context unavailable, USB auto-mount disabled: %s", exc
            )
            return False

        await self._mount_present_devices(context)

        try:
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by("block")
            self._observer = pyudev.MonitorObserver(
                monitor, callback=self._on_udev_event, name="milo-usb-monitor"
            )
            self._observer.daemon = True  # never block process shutdown
            self._observer.start()
        except Exception as exc:
            self.logger.warning("Failed to start USB monitor: %s", exc)
            return False

        self.logger.info(
            "USB storage monitor started (mount root %s)", MUSIC_LIBRARY_MOUNT_ROOT
        )
        return True

    async def cleanup(self) -> None:
        """Stop the monitor thread and close the Navidrome client."""
        if self._observer is not None:
            try:
                self._observer.stop()
            except Exception as exc:
                self.logger.debug("USB monitor stop error: %s", exc)
            self._observer = None
        if self._navidrome is not None:
            await self._navidrome.close()
            self._navidrome = None

    # =========================================================================
    # udev detection
    # =========================================================================

    async def _mount_present_devices(self, context) -> None:
        """Mount USB partitions already connected at startup (booted with a key in)."""
        try:
            devices = list(
                context.list_devices(subsystem="block", DEVTYPE="partition")
            )
        except Exception as exc:
            self.logger.warning("Enumerating block devices failed: %s", exc)
            return
        for device in devices:
            if self._is_usb_fs_partition(device):
                await self._mount(device.device_node)

    def _on_udev_event(self, device) -> None:
        """pyudev callback — runs on the monitor thread, bridges to the loop.

        Reads the primitives it needs here (the Device object stays on this
        thread) and schedules the async mount/unmount on the event loop.
        """
        try:
            action = device.action
            devnode = device.device_node
            if not devnode or self._loop is None:
                return
            if action == "add" and self._is_usb_fs_partition(device):
                coro = self._mount(devnode)
            elif action == "remove":
                coro = self._unmount(devnode)
            else:
                return
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as exc:
            # A raised exception would kill the monitor thread — swallow and log.
            self.logger.debug("udev event handling error: %s", exc)

    @staticmethod
    def _is_usb_fs_partition(device) -> bool:
        """True for a USB-bus partition carrying a filesystem we can mount."""
        return (
            device.get("ID_BUS") == "usb"
            and device.get("DEVTYPE") == "partition"
            and device.get("ID_FS_TYPE") in _MOUNTABLE_FSTYPES
        )

    # =========================================================================
    # mount / unmount via privileged helpers
    # =========================================================================

    async def _mount(self, devnode: str) -> None:
        async with self._lock:
            if devnode in self._mounts:
                return  # duplicate add / re-trigger — already mounted
            mountpoint = await self._run_helper(MILO_MOUNT_CMD, devnode, capture=True)
            if not mountpoint:
                return
            self._mounts[devnode] = mountpoint
            self.logger.info("Mounted %s at %s", devnode, mountpoint)
        await self._trigger_scan()

    async def _unmount(self, devnode: str) -> None:
        async with self._lock:
            mountpoint = self._mounts.pop(devnode, None)
            if not mountpoint:
                return  # not one of ours (some other block device)
            await self._run_helper(MILO_UMOUNT_CMD, mountpoint, capture=False)
            self.logger.info("Unmounted %s (%s)", devnode, mountpoint)
        await self._trigger_scan()

    async def _run_helper(
        self,
        helper: str,
        *args: str,
        capture: bool,
        stdin: Optional[bytes] = None,
    ) -> Optional[str]:
        """Run a milo-mount / milo-umount helper via ``sudo -n``.

        ``stdin`` carries CIFS credentials to milo-mount on the standard input
        (never argv, which is world-readable in /proc); None means no input
        (stdin is /dev/null so the helper's ``cat`` returns at once).

        Returns the helper's stripped stdout (the mountpoint) when ``capture`` is
        set and it exited 0, else None. Fail-open: a missing sudoers rule, absent
        helper or timeout logs and returns None rather than raising.
        """
        arg_str = " ".join(args)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", helper, *args,
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin), timeout=_HELPER_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error("%s timed out for %s", helper, arg_str)
            return None
        except Exception as exc:
            self.logger.error("%s failed to spawn for %s: %s", helper, arg_str, exc)
            return None

        if proc.returncode != 0:
            self.logger.error(
                "%s failed (rc=%s) for %s: %s",
                helper, proc.returncode, arg_str,
                stderr.decode(errors="replace").strip(),
            )
            return None
        return stdout.decode(errors="replace").strip() if capture else ""

    # =========================================================================
    # network shares (SMB/NFS) via milo-mount --network
    # =========================================================================

    async def mount_share(
        self, share: Dict[str, Any], credentials: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """Mount a configured SMB/NFS share read-only under the mount root.

        ``credentials`` (username/password/domain) are fed to milo-mount on stdin
        and persisted by it to a root-only cred file; pass None for a boot remount
        (the helper reuses the stored file). Returns the mountpoint on success or
        None (fail-open: an offline NAS logs and returns None, never raises, so a
        share that is down at boot can't block startup). Triggers a Navidrome
        rescan on success so the newly-visible tracks get indexed.
        """
        share_id = share["id"]
        args = (
            "--network", share["type"],
            "--id", share_id,
            "--host", share["host"],
            "--path", share["path"],
        )
        stdin = self._encode_credentials(credentials) if credentials else None
        async with self._lock:
            mountpoint = await self._run_helper(
                MILO_MOUNT_CMD, *args, capture=True, stdin=stdin
            )
            if not mountpoint:
                return None
            self._share_mounts[share_id] = mountpoint
            self.logger.info(
                "Mounted %s share %s at %s", share["type"], share_id, mountpoint
            )
        await self._trigger_scan()
        return mountpoint

    async def unmount_share(self, share_id: str) -> None:
        """Unmount a share (edit/removal) and rescan so Navidrome drops its tracks.

        Idempotent: falls back to the deterministic /media/milo/<id> mountpoint
        when the share isn't in the session map (e.g. mounted before a backend
        restart), and milo-umount no-ops on a path that isn't mounted.
        """
        async with self._lock:
            mountpoint = self._share_mounts.pop(
                share_id, str(MUSIC_LIBRARY_MOUNT_ROOT / share_id)
            )
            await self._run_helper(MILO_UMOUNT_CMD, mountpoint, capture=False)
            self.logger.info("Unmounted share %s (%s)", share_id, mountpoint)
        await self._trigger_scan()

    async def forget_share_credentials(self, share_id: str) -> None:
        """Drop a share's root-only cred file (called on share deletion)."""
        async with self._lock:
            await self._run_helper(
                MILO_MOUNT_CMD, "--forget", "--id", share_id, capture=False
            )

    def get_mounted_share_ids(self) -> set:
        """Ids currently mounted under the mount root, read live from /proc/mounts.

        Lets the settings UI show which shares are actually connected right now (a
        share can be configured but its NAS offline). Reading /proc/mounts is a
        procfs op — it never touches the network filesystem, so it can't hang on a
        dead CIFS/NFS mount the way stat()/os.path.ismount would. Fail-open: an
        unreadable table yields an empty set.
        """
        root = f"{MUSIC_LIBRARY_MOUNT_ROOT}/"
        mounted: set = set()
        try:
            with open("/proc/mounts", "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    fields = line.split()
                    if len(fields) < 2:
                        continue
                    # Our ids are slugs (no spaces), so no /proc-mounts octal
                    # unescaping is needed; only the first path segment is the id.
                    mountpoint = fields[1]
                    if mountpoint.startswith(root):
                        mounted.add(mountpoint[len(root):].split("/")[0])
        except OSError as exc:
            self.logger.debug("Could not read /proc/mounts: %s", exc)
        return mounted

    @staticmethod
    def _encode_credentials(credentials: Dict[str, str]) -> bytes:
        """Serialize credentials as the ``key=value`` lines milo-mount writes to
        the CIFS cred file — only the three keys mount.cifs reads, empty ones
        dropped. Returns empty bytes when nothing usable is present."""
        lines = [
            f"{key}={credentials[key]}"
            for key in ("username", "password", "domain")
            if credentials.get(key)
        ]
        return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""

    # =========================================================================
    # Navidrome rescan
    # =========================================================================

    async def _trigger_scan(self) -> None:
        """Ask Navidrome to rescan /media/milo after a mount change.

        Best-effort: Navidrome's own folder watcher also notices, so a failure
        here (not provisioned yet, still starting up) just means a slightly later
        index update. Rebuilds the client if the cred file only appeared after
        startup (first boot provisions it asynchronously).
        """
        client = await self._ensure_navidrome()
        if client is None:
            self.logger.info(
                "Navidrome client unavailable; relying on its folder watcher"
            )
            return
        try:
            if not await client.start_scan():
                self.logger.info(
                    "Navidrome scan trigger returned falsy (may be starting up)"
                )
        except Exception as exc:
            self.logger.warning("Navidrome scan trigger failed: %s", exc)

    async def _ensure_navidrome(self) -> Optional[NavidromeClient]:
        """Lazily build the Navidrome client from the cred file (None until it exists)."""
        if self._navidrome is None:
            self._navidrome = NavidromeClient.from_cred_file()
        return self._navidrome
