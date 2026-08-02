# backend/sources/music_library/libraries.py
"""One Navidrome library per mounted storage space.

Navidrome indexes everything under /media/milo, but a catalog answer only says
*which library* a track belongs to — never which mount it came from. So the
storage spaces the user sees (a USB key, an SMB/NFS share) are materialised as
Navidrome libraries, one each, rooted at that mount's directory. That library's
id **is** the Subsonic ``musicFolderId``, which is what lets the frontend scope
albums/artists/search to one storage space.

This service keeps the two sets in step. It is a *reconciler*, not a queue of
operations: given the storage spaces that should exist right now, it creates the
missing libraries, deletes the ones that no longer correspond to a mount — the
single ``/media/milo`` root library a pre-multi-library install left behind is
one of those — renames the drifted ones, and re-grants the service account
access to the result. Any mount change calls it again with the new full set, so
a missed event heals on the next one rather than leaving a queue to replay.

What counts as "should exist" is *known to Milō*, not *reachable right now*: a
configured share keeps its library while its NAS is asleep, and an unplugged USB
key keeps its library too. Deleting either would throw away a catalog that is
still valid — for the key, the 18-minute pass that indexed 10 000 tracks, every
single time it is unplugged. A storage space only loses its library when the user
removes the share or forgets the key.

Fail-open: Navidrome not up yet, a rejected write, an expired token — log, retry
on a short bounded schedule, and let the next mount change reconcile. A mount
must never fail because the catalog engine was busy.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from backend.config.constants import MUSIC_LIBRARY_MOUNT_ROOT
from backend.shared.background import BackgroundTaskSet
from backend.sources.music_library.navidrome_admin import NavidromeAdminClient

logger = logging.getLogger("source.music_library.libraries")

# Catch-up schedule when Navidrome isn't answering yet (it boots alongside the
# backend, and the first USB mount can land before its port is open). ~1.5 min,
# then we stop: the next mount change reconciles anyway.
_RETRY_DELAYS_S = (5, 15, 30, 45)


class NavidromeLibraryService:
    """Maps mountpoints to Navidrome libraries, and keeps that mapping true."""

    def __init__(self) -> None:
        self.logger = logger
        # Built lazily: the admin cred file is the same one the Subsonic client
        # waits for, and first-boot provisioning may not have written it yet.
        self._admin: Optional[NavidromeAdminClient] = None
        # mountpoint -> library id, as of the last successful reconcile. Read by
        # the storages endpoint, so reads never hit HTTP.
        self._by_path: Dict[str, int] = {}
        # Serializes reconciles: a hotplug event and a share write can land
        # together, and both rewrite the same library set.
        self._lock = asyncio.Lock()
        self._bg = BackgroundTaskSet(self.logger, "music_library.libraries")
        # The set to converge on, last time we were told. Kept so a retry after a
        # Navidrome outage uses the current truth, not the stale one it failed on.
        self._desired: Dict[str, str] = {}
        # Library ids the service account is known to have been granted. Kept so
        # a grant that failed is retried even once nothing else needs changing.
        self._granted: set = set()
        self._retrying = False

    async def cleanup(self) -> None:
        await self._bg.cancel_all()
        if self._admin is not None:
            await self._admin.close()
            self._admin = None

    def library_id(self, mountpoint: str) -> Optional[int]:
        """Library id for a mountpoint, or None if it has none (yet)."""
        return self._by_path.get(mountpoint)

    async def stats(self) -> Dict[str, Dict[str, Any]]:
        """Per-library catalog counts, keyed by mountpoint. Empty when unreachable.

        Navidrome's *Subsonic* scan status reports one global track count that
        does not move until a scan ends — during the 18 minutes it took to index
        a 10 000-track iPod it read "2419", the total from the previous scan, and
        then jumped. These native-API records are the per-library truth behind it
        (``totalSongs``/``totalAlbums`` per storage space), which is both the only
        honest progress figure while a scan runs and what lets each storage
        button state how much is in it.

        Fail-open like every other call here: Navidrome down or still booting
        yields ``{}``, and the caller shows no counts rather than failing.
        """
        async with self._lock:
            admin = await self._get_admin()
            if admin is None:
                return {}
            libraries = await admin.list_libraries()
        if libraries is None:
            return {}
        return {
            lib["path"]: {
                "track_count": lib.get("totalSongs") or 0,
                "album_count": lib.get("totalAlbums") or 0,
                "missing_count": lib.get("totalMissingFiles") or 0,
            }
            for lib in libraries
            if lib.get("path")
        }

    # =========================================================================
    # RECONCILE
    # =========================================================================

    async def reconcile(self, desired: Dict[str, str]) -> bool:
        """Converge Navidrome's libraries on ``desired`` (mountpoint → name).

        Returns True when the two sets match afterwards. On failure a bounded
        retry is scheduled and False returned — never raises, so a mount path can
        call it inline.
        """
        async with self._lock:
            self._desired = dict(desired)
            ok = await self._converge()
        if not ok:
            self._schedule_retry()
        return ok

    async def _converge(self) -> bool:
        """One reconcile pass. Caller holds the lock."""
        admin = await self._get_admin()
        if admin is None:
            self.logger.info("Navidrome admin API unavailable; libraries not synced")
            return False

        existing = await admin.list_libraries()
        if existing is None:
            return False  # could not ask — never read as "there are none"

        desired = self._desired
        by_path = {lib.get("path"): lib for lib in existing if lib.get("path")}
        changed = False

        # Deletions are skipped entirely while nothing is mounted: an empty
        # desired set is far more likely to mean "storage not up yet" than "the
        # user removed everything", and a wrong deletion costs a full re-index.
        if desired:
            for path, lib in by_path.items():
                if path in desired or not self._is_managed(path):
                    continue
                self.logger.info(
                    "Dropping Navidrome library %r (%s): no longer a mount",
                    lib.get("name"), path,
                )
                changed |= await admin.delete_library(lib["id"])

        for path, name in desired.items():
            lib = by_path.get(path)
            if lib is None:
                changed |= (await admin.create_library(name, path)) is not None
            elif lib.get("name") != name:
                changed |= await admin.rename_library(lib["id"], name, path)

        if changed:
            existing = await admin.list_libraries()
            if existing is None:
                return False

        self._by_path = {
            lib["path"]: lib["id"] for lib in existing if lib.get("path")
        }
        # A library the service account can't see is invisible to the Subsonic
        # API as well, so every browse call would answer empty. Driven by the id
        # set rather than by `changed`, and a failure is a failed reconcile —
        # returning True here would leave a library that exists, is listed, and
        # answers nothing, with no retry scheduled to fix it.
        library_ids = set(self._by_path.values())
        if library_ids != self._granted:
            if not await admin.grant_all_libraries(sorted(library_ids)):
                self.logger.warning(
                    "Navidrome libraries created but not granted to the service "
                    "account; they would browse empty"
                )
                return False
            self._granted = library_ids

        missing = [path for path in desired if path not in self._by_path]
        if missing:
            self.logger.warning("Navidrome libraries still missing for: %s", missing)
            return False
        return True

    # =========================================================================
    # PLUMBING
    # =========================================================================

    def _is_managed(self, path: str) -> bool:
        """True for a library this service owns, i.e. one rooted at a mount.

        Navidrome refuses to delete the library it created from ``MusicFolder``
        ("library with ID 1 cannot be deleted"), so that one is left alone
        rather than retried forever. install/navidrome.sh points MusicFolder at
        an empty directory precisely so it indexes nothing; a library sitting on
        the mount root itself would double-index every mount, which is worth a
        line in the log.
        """
        root = str(MUSIC_LIBRARY_MOUNT_ROOT)
        if path == root or root.startswith(f"{path.rstrip('/')}/"):
            self.logger.warning(
                "Navidrome library at %s covers the whole mount root and will "
                "double-index every storage space; repoint it at an empty "
                "directory (see install/navidrome.sh MusicFolder)", path,
            )
            return False
        return path.startswith(f"{root}/")

    async def _get_admin(self) -> Optional[NavidromeAdminClient]:
        """The admin client, built on first use (cred file may appear late)."""
        if self._admin is None:
            self._admin = NavidromeAdminClient.from_cred_file()
        return self._admin

    def _schedule_retry(self) -> None:
        """Retry the reconcile on a short bounded schedule (one at a time)."""
        if self._retrying:
            return
        self._retrying = True
        self._bg.spawn(self._retry_loop(), label="library-reconcile-retry")

    async def _retry_loop(self) -> None:
        try:
            for delay in _RETRY_DELAYS_S:
                await asyncio.sleep(delay)
                async with self._lock:
                    if await self._converge():
                        self.logger.info("Navidrome libraries reconciled on retry")
                        return
            self.logger.warning(
                "Gave up syncing Navidrome libraries; next mount change retries"
            )
        finally:
            self._retrying = False
