# backend/sources/music_library/storage.py
"""USB storage layer for the Music Library source.

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

SMB/NFS network shares are handled alongside. Unlike USB there is no hotplug event to
rediscover them, so their config is persisted (MusicLibraryDataService) and the
source replays it at boot via :meth:`mount_share`. The same milo-mount helper
mounts them read-only under the mount root; CIFS credentials are handed to it on
stdin (never argv) and it persists them to a root-only cred file the milo backend
cannot read.

Fail-open throughout: no udev (a dev host without libudev), no sudoers rule, or
Navidrome not provisioned yet must degrade to "auto-mount disabled" and log,
never crash the backend.
"""
import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.config.constants import (
    MILO_MOUNT_CMD,
    MILO_UMOUNT_CMD,
    MUSIC_LIBRARY_MOUNT_ROOT,
)
from backend.sources.music_library.navidrome_client import NavidromeClient

logger = logging.getLogger("source.music_library.storage")

# How the manager reaches the catalog: the source's shared-client accessor, which
# returns None until Navidrome's cred file exists.
NavidromeProvider = Callable[[], Awaitable[Optional[NavidromeClient]]]

# Called after every mount/unmount, before the rescan, so the layer above can
# bring Navidrome's libraries in line with the new set of storage spaces (a
# library must exist before the scan that fills it).
StorageChangedHook = Callable[[], Awaitable[None]]

# Filesystems worth mounting — mirrors milo-mount's allowlist. The helper is the
# real security boundary and re-validates independently; this is only a fast
# pre-filter so we don't shell out for a partition Navidrome couldn't read anyway.
_MOUNTABLE_FSTYPES = frozenset(
    {"vfat", "exfat", "ntfs", "ext2", "ext3", "ext4", "hfsplus"}
)

# Ceiling on a single mount/unmount helper call (a slow spin-up USB key still
# mounts well within this; a hang must not wedge the monitor).
_HELPER_TIMEOUT_S = 30.0

# Cadence and ceiling for waiting out a scan that was already running when a
# mount landed (see _scan_when_idle). Past the ceiling the mount is left to
# Navidrome's own periodic pass, which install/navidrome.sh sets to **6h** — so
# giving up early is not the cheap "within the hour" this said for a fortnight.
#
# The ceiling has to outlast a first index, because that is the scan a second
# mount most often lands behind. Measured on the unit 2026-09-02: indexing a
# 10 069-track iPod took 18m12s, against a 600 s ceiling — the waiter gave up
# nine minutes before the scan it was waiting for ended, and would have dropped
# a key plugged in meanwhile to the 6-hourly backstop.
_SCAN_WAIT_POLL_S = 5.0
_SCAN_WAIT_CEILING_S = 1800.0


class StorageManager:
    """Keeps /media/milo in sync with the USB drives that are plugged in.

    Owns a pyudev monitor thread (started in :meth:`initialize`, stopped in
    :meth:`cleanup`) and a devnode→volume map so a ``remove`` event can unmount
    the right target. All mount/unmount go through the milo-mount / milo-umount
    sudoers helpers; the layer above is notified and Navidrome told to rescan
    after every change.
    """

    def __init__(
        self,
        navidrome_provider: NavidromeProvider,
        on_storage_changed: StorageChangedHook,
    ) -> None:
        self.logger = logger
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._observer = None  # pyudev.MonitorObserver (monitor thread)
        # The catalog client is owned by the source and shared, not rebuilt here:
        # a second instance would keep its own aiohttp session and miss the
        # auth-recovery path that drops a stale client on a rotated cred file.
        self._navidrome_provider = navidrome_provider
        self._on_storage_changed = on_storage_changed
        # Serializes mount/unmount so concurrent hotplug events can't race on the
        # mountpoint table or the helpers.
        self._lock = asyncio.Lock()
        # devnode (/dev/sda1) -> {mountpoint, uuid, label}  [USB]. The filesystem
        # UUID is carried because it is the only stable identity of a key: the
        # mountpoint follows the (renameable) filesystem label, and gains a suffix
        # when two keys collide on one.
        self._mounts: Dict[str, Dict[str, str]] = {}
        # share id -> mountpoint (/media/milo/<id>)  [network shares]
        self._share_mounts: Dict[str, str] = {}
        # A rescan owed to a mount that landed while another scan was running,
        # and the lock that keeps two mounts from each starting their own waiter.
        self._deferred_scan: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()

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
        """Stop the monitor thread. The Navidrome client belongs to the source."""
        if self._deferred_scan is not None and not self._deferred_scan.done():
            self._deferred_scan.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._deferred_scan
        self._deferred_scan = None
        if self._observer is not None:
            try:
                self._observer.stop()
            except Exception as exc:
                self.logger.debug("USB monitor stop error: %s", exc)
            self._observer = None

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
                await self._mount(device.device_node, *self._identity(device))

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
                coro = self._mount(devnode, *self._identity(device))
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

    @staticmethod
    def _identity(device) -> tuple:
        """``(uuid, label)`` for a partition, read on the udev thread.

        The UUID is what a user-given name is filed under: it survives a relabel
        and a replug into another port, neither of which the mountpoint does. It
        falls back to the kernel name for the (rare) filesystem without one, so a
        key is never identity-less — as the bare name (``sda1``), because the
        identity travels in a URL path segment and ``/dev/sda1`` would match no
        route at all.
        """
        uuid = device.get("ID_FS_UUID") or ""
        if not uuid:
            uuid = (device.get("DEVNAME") or "").rsplit("/", 1)[-1]
        return uuid, device.get("ID_FS_LABEL") or ""

    # =========================================================================
    # mount / unmount via privileged helpers
    # =========================================================================

    async def _mount(self, devnode: str, uuid: str = "", label: str = "") -> None:
        async with self._lock:
            if devnode in self._mounts:
                return  # duplicate add / re-trigger — already mounted
            mountpoint = await self._run_helper(MILO_MOUNT_CMD, devnode, capture=True)
            if not mountpoint:
                return
            self._mounts[devnode] = {
                "mountpoint": mountpoint,
                "uuid": uuid,
                "label": label,
            }
            self.logger.info("Mounted %s at %s", devnode, mountpoint)
        await self._on_storage_changed()
        await self.request_scan()

    async def _unmount(self, devnode: str) -> None:
        async with self._lock:
            volume = self._mounts.pop(devnode, None)
            if not volume:
                return  # not one of ours (some other block device)
            mountpoint = volume["mountpoint"]
            await self._run_helper(MILO_UMOUNT_CMD, mountpoint, capture=False)
            self.logger.info("Unmounted %s (%s)", devnode, mountpoint)
        # No scan on removal, and deliberately so: the key keeps its library and
        # its index (libraries.py), and a scan here would only walk a path that no
        # longer exists. The layer above is still told, so the UI drops the key at
        # once. What actually protects the index is that the mountpoint is *gone*
        # rather than left behind empty — see milo-umount, and the asymmetry
        # provisioning/navidrome.sh depends on.
        await self._on_storage_changed()

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
        await self._on_storage_changed()
        await self.request_scan()
        return mountpoint

    async def unmount_share(self, share_id: str) -> None:
        """Unmount a share (edit/removal). No scan follows.

        Idempotent: falls back to the deterministic /media/milo/<id> mountpoint
        when the share isn't in the session map (e.g. mounted before a backend
        restart), and milo-umount no-ops on a path that isn't mounted.

        A scan here would walk a path that no longer exists, and nothing needs
        one: a removal retires the whole Navidrome library, and an edit's stale
        tracks are marked by the next ordinary scan (SharesService._unmount_share).
        """
        async with self._lock:
            mountpoint = self._share_mounts.pop(
                share_id, str(MUSIC_LIBRARY_MOUNT_ROOT / share_id)
            )
            await self._run_helper(MILO_UMOUNT_CMD, mountpoint, capture=False)
            self.logger.info("Unmounted share %s (%s)", share_id, mountpoint)
        # The library itself outlives an unmount that is only a remount (an edit
        # unmounts before it mounts again) — the caller decides, from the share
        # config, whether it still belongs; here we only report the change.
        await self._on_storage_changed()

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

    def get_usb_mounts(self) -> List[Dict[str, str]]:
        """USB volumes mounted read-only under the mount root right now.

        One entry per mounted *partition*, so a key holding two of them is two
        volumes here — as it is two directories under the mount root and two
        Navidrome libraries. Reads the in-session devnode→volume map (network
        shares live in a separate one), so it never stats a mountpoint and can't
        hang. ``label`` is the mountpoint's final segment, which is the sanitized
        filesystem label milo-mount chose; ``uuid`` is the stable identity a
        user-given name is filed under.
        """
        return [
            {
                "uuid": volume["uuid"],
                "label": volume["mountpoint"].rsplit("/", 1)[-1],
                "mountpoint": volume["mountpoint"],
            }
            for volume in self._mounts.values()
        ]

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

    async def request_scan(self) -> None:
        """Ask Navidrome to rescan /media/milo after a mount change.

        One flavour only. The scan both indexes what appeared and marks what it
        cannot find — including, on a reconnection, un-marking everything that
        came back with the storage space, which is what repairs a library whose
        tracks were marked while it was away.

        A scan already in flight started before this mount existed, so it cannot
        possibly index it — and asking anyway achieves nothing: Navidrome answers
        `startScan` with HTTP 200 "ok" and its full scanStatus while logging
        "Error scanning: already scanning" server-side, so the refusal is
        invisible from here and start_scan() returns True on a call that did
        nothing. The only honest reading is scanStatus.scanning *before* asking,
        which is why this checks first and hands a busy scanner to
        :meth:`_scan_when_idle` rather than firing into it.

        There is no folder watcher to fall back on. inotify reports neither the
        mount that made these files visible (the contents come from another
        superblock — no events) nor later writes on the far side of a network
        share, so nothing else announces a storage space appearing.
        """
        client = await self._navidrome_provider()
        if client is None:
            # First boot provisions the cred file asynchronously; the provider
            # builds the client on first use, so a later mount still scans.
            self.logger.info("Navidrome client unavailable; skipping rescan")
            return

        async with self._scan_lock:
            if self._deferred_scan is not None and not self._deferred_scan.done():
                # A waiter is already owed a scan — fold this request into it
                # rather than queueing a second pass over the same tree.
                return
            if await self._scan_in_progress(client):
                self._deferred_scan = asyncio.create_task(self._scan_when_idle())
                return

        await self._start_scan(client)

    async def _scan_in_progress(self, client: NavidromeClient) -> bool:
        """Whether Navidrome is scanning right now; False when it can't say.

        Unknown counts as idle on purpose: a scan we fire needlessly is a wasted
        incremental pass, while one we skip on a failed poll is a storage space
        that stays uncatalogued until the 6-hourly schedule.
        """
        try:
            status = await client.get_scan_status()
        except Exception as exc:
            self.logger.debug("Could not read Navidrome scan status: %s", exc)
            return False
        return bool(status and status.get("scanning"))

    async def _scan_when_idle(self) -> None:
        """Wait out the running scan, then run the one the mount is owed.

        Tracked on ``self`` and cancelled in :meth:`cleanup`. Giving up at the
        ceiling is not a lost catalog — Navidrome's periodic pass sees the mount
        regardless — but that pass runs every 6h, so this decides whether the new
        storage space appears in seconds or somewhere in the next six hours.
        """
        waited = 0.0
        try:
            client = await self._navidrome_provider()
            while client is not None and await self._scan_in_progress(client):
                if waited >= _SCAN_WAIT_CEILING_S:
                    self.logger.warning(
                        "Navidrome still scanning after %.0fs; leaving the mount "
                        "to the scheduled rescan", waited
                    )
                    return
                await asyncio.sleep(_SCAN_WAIT_POLL_S)
                waited += _SCAN_WAIT_POLL_S
            if client is None:
                return
            await self._start_scan(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Deferred Navidrome rescan failed: %s", exc)

    async def _start_scan(self, client: NavidromeClient) -> None:
        """The one place a scan is actually asked for."""
        try:
            if not await client.start_scan():
                self.logger.warning("Navidrome refused the scan request")
        except Exception as exc:
            self.logger.warning("Navidrome scan trigger failed: %s", exc)
