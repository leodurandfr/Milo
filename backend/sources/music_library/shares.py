# backend/sources/music_library/shares.py
"""Network shares (SMB/NFS) for the Music Library source.

Owns where the user's music comes from, end to end: the persisted share config,
the read-only mount under /media/milo that realises it, and the Navidrome rescan
that follows — plus a read of the USB volumes mounted beside them, since the
settings screen lists both origins together and the storage layer below knows
both.

Two collaborators, both owned here: :class:`MusicLibraryDataService` (config,
the source of truth a boot remount replays) and :class:`StorageManager` (the
privileged milo-mount calls and the USB hotplug watcher). Keeping them in one
place is what makes "config first, then mount" a single decision rather than an
ordering every caller has to remember.

Reached from routes.py as ``source.shares`` — the shape radio (``station_data``),
podcast (``podcast_data``) and cd (``data_service``) already use.
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from backend.sources.music_library.data import MusicLibraryDataService
from backend.sources.music_library.models import ShareRequest
from backend.sources.music_library.storage import NavidromeProvider, StorageManager

# Bounded catch-up schedule (seconds between attempts) for network shares whose
# NAS was still offline when the backend booted — a NAS often boots slower than
# the Pi. ~1.75 min total, then we give up; not an ongoing reconnection loop.
_SHARE_REMOUNT_RETRY_DELAYS_S = (15, 30, 60)


class NetworkShareService:
    """The configured SMB/NFS shares: config, mount, rescan, and their status."""

    def __init__(
        self,
        navidrome_provider: NavidromeProvider,
        on_catalog_changed: Callable[[], None],
    ) -> None:
        self._logger = logging.getLogger("source.music_library.shares")
        self._data = MusicLibraryDataService()
        self._storage = StorageManager(navidrome_provider)
        # The merged-album cache lives with the catalog, not here; a share change
        # invalidates it through this callback rather than reaching back up.
        self._on_catalog_changed = on_catalog_changed
        # Boot-remount retry for shares whose NAS was still offline at startup
        # (see _mount_configured). Tracked so it isn't GC'd mid-flight; bounded
        # and self-terminating, so it needs no explicit cancellation.
        self._retry_task: Optional[asyncio.Task] = None

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def initialize(self) -> None:
        """Load the share config, start the USB watcher, then remount the shares.

        The config load runs first and is the one **fail-loud** step: a
        schema_version drift raises SchemaVersionMismatch, which the gather in
        dependencies.py turns into the reset banner + SystemExit(1). USB detection
        and network remounting are both **fail-open** (no udev on a dev host just
        disables auto-mount; an offline NAS is skipped), so neither can block
        startup.
        """
        await self._data.initialize()
        await self._storage.initialize()
        await self._mount_configured()

    async def _mount_configured(self) -> None:
        """Boot remount of every configured network share (fail-open per share).

        No credentials are supplied — milo-mount reuses each share's persisted
        root-only cred file. A share whose NAS is offline at boot is retried in
        the background by :meth:`_retry_offline` (common reboot race: the Pi is
        up before the NAS finishes booting), so it reconnects without a manual
        re-save. Never raises.
        """
        try:
            shares = await self._data.list_shares()
        except Exception as e:
            self._logger.warning(f"Could not load network shares: {e}")
            return
        offline = [s for s in shares if not await self._try_mount(s)]
        if offline:
            self._retry_task = asyncio.create_task(self._retry_offline(offline))

    async def _try_mount(self, share: Dict[str, Any]) -> bool:
        """One remount attempt for a configured share; True on success.

        Fail-open like the rest of the boot path: an offline NAS returns None
        (or the helper raises), which we log and report as not-mounted so the
        caller can retry it — never propagates.
        """
        try:
            return await self._storage.mount_share(share) is not None
        except Exception as e:
            self._logger.warning(f"Failed to mount share {share.get('id')}: {e}")
            return False

    async def _retry_offline(self, shares: List[Dict[str, Any]]) -> None:
        """Retry shares that were offline at boot over a short, bounded schedule.

        Covers the reboot race where the NAS (or the LAN) isn't reachable yet
        when initialize() runs. Drops each share as it connects and exits once
        all are mounted or the schedule is exhausted — this is a boot catch-up,
        deliberately NOT an ongoing reconnection loop (a share going offline
        while running is out of scope; the appliance is rebooted, not repaired
        live). Each successful mount triggers a Navidrome rescan via mount_share.
        """
        pending = list(shares)
        for delay in _SHARE_REMOUNT_RETRY_DELAYS_S:
            await asyncio.sleep(delay)
            pending = [s for s in pending if not await self._try_mount(s)]
            if not pending:
                self._logger.info("All boot-offline shares remounted")
                return
        self._logger.warning(
            "Gave up remounting %d share(s) still offline: %s",
            len(pending),
            [s.get("id") for s in pending],
        )

    # =========================================================================
    # READS
    # =========================================================================

    def usb_devices(self) -> List[Dict[str, str]]:
        """USB volumes mounted under the library root right now (read-only status).

        Sits beside :meth:`list` in the settings UI so the user sees every music
        origin — the auto-mounted key and the configured NAS shares — in one
        place. Delegates to the storage manager's live mount map (no I/O).
        """
        return self._storage.get_usb_mounts()

    async def list(self) -> List[Dict[str, Any]]:
        """Configured network shares (non-secret metadata; safe over the API).

        Each entry is annotated with a live ``mounted`` flag (read from
        /proc/mounts) so the settings UI can show which shares are actually
        connected right now versus configured-but-offline.
        """
        mounted_ids = self._storage.get_mounted_share_ids()
        return [
            {**share, "mounted": share.get("id") in mounted_ids}
            for share in await self._data.list_shares()
        ]

    async def offline_names(self) -> List[str]:
        """Configured network shares not mounted right now (empty if all up).

        Gates the full-scan/purge route: purging while a share's NAS is offline would
        wrongly drop its still-valid tracks. USB is excluded — an unplug already
        purges its own tracks.
        """
        return [
            share.get("name") or share.get("host") or share.get("id")
            for share in await self.list()
            if not share.get("mounted")
        ]

    # =========================================================================
    # WRITES
    # =========================================================================

    async def add(self, req: ShareRequest) -> Dict[str, Any]:
        """Persist a new share, mount it read-only, and rescan.

        Returns the created share (no credentials — the password is written only
        to the root-only cred file by milo-mount, never surfaced here) plus a
        transient ``mounted`` flag so the UI can confirm the mount succeeded
        (the config is persisted either way — a share that's down now remounts at
        the next boot, but the user should be told it didn't connect).
        """
        share = await self._data.add_share(
            share_type=req.type,
            host=req.host,
            path=req.path,
            name=req.name,
            has_credentials=bool(req.password),
            username=req.username,
            domain=req.domain,
        )
        mountpoint = await self._storage.mount_share(
            share, credentials=self._credentials(req)
        )
        self._on_catalog_changed()
        return {**share, "mounted": mountpoint is not None}

    async def update(
        self, share_id: str, req: ShareRequest
    ) -> Optional[Dict[str, Any]]:
        """Replace a share's mutable fields, remount, and rescan.

        Returns the updated share, or None if no share has that id. A request that
        omits the password keeps the existing cred file (idempotent PUT).
        """
        if await self._data.get_share(share_id) is None:
            return None
        updates: Dict[str, Any] = {
            "type": req.type,
            "host": req.host,
            "path": req.path,
            "name": req.name,
        }
        # Credentials move as a unit with the password (which rewrites the cred
        # file): a new password stores the new username/domain too; NFS clears
        # them; a CIFS edit that omits the password keeps the existing login.
        if req.type == "nfs":
            updates.update(has_credentials=False, username=None, domain=None)
        elif req.password:
            updates.update(has_credentials=True, username=req.username, domain=req.domain)
        share = await self._data.update_share(share_id, updates)
        if share is None:
            return None
        # Unmount first so a changed host/path/credentials actually takes effect.
        await self._storage.unmount_share(share_id)
        mountpoint = await self._storage.mount_share(
            share, credentials=self._credentials(req)
        )
        self._on_catalog_changed()
        return {**share, "mounted": mountpoint is not None}

    async def remove(self, share_id: str) -> bool:
        """Unmount, drop the config entry, and forget the credentials. Returns
        False if no share has that id."""
        if await self._data.get_share(share_id) is None:
            return False
        await self._storage.unmount_share(share_id)
        await self._data.remove_share(share_id)
        await self._storage.forget_share_credentials(share_id)
        self._on_catalog_changed()
        return True

    @staticmethod
    def _credentials(req: ShareRequest) -> Optional[Dict[str, str]]:
        """CIFS credential dict from a request, or None when no password was given
        (guest share, or a PUT that keeps the existing creds)."""
        if not req.password:
            return None
        creds = {"password": req.password}
        if req.username:
            creds["username"] = req.username
        if req.domain:
            creds["domain"] = req.domain
        return creds
