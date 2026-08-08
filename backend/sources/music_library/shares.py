# backend/sources/music_library/shares.py
"""Network shares (SMB/NFS) for the Music Library source.

Owns where the user's music comes from, end to end: the persisted share config,
the read-only mount under /media/milo that realises it, and the Navidrome rescan
that follows — plus a read of the USB volumes mounted beside them, since the
settings screen lists both origins together and the storage layer below knows
both.

Three collaborators, all owned here: :class:`MusicLibraryDataService` (config,
the source of truth a boot remount replays), :class:`StorageManager` (the
privileged milo-mount calls and the USB hotplug watcher) and
:class:`NavidromeLibraryService` (one Navidrome library per mount, which is what
makes a storage space something the catalog can be filtered by). Keeping them in
one place is what makes "config first, then mount, then library" a single
decision rather than an ordering every caller has to remember.

Reached from routes.py as ``source.shares`` — the shape radio (``station_data``),
podcast (``podcast_data``) and cd (``data_service``) already use.
"""
import asyncio
import contextlib
import errno
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.config.constants import MUSIC_LIBRARY_MOUNT_ROOT
from backend.shared.background import BackgroundTaskSet
from backend.sources.music_library.data import MusicLibraryDataService
from backend.sources.music_library.libraries import NavidromeLibraryService
from backend.sources.music_library.models import ShareRequest
from backend.sources.music_library.storage import NavidromeProvider, StorageManager

# Bounded catch-up schedule (seconds between attempts) for network shares whose
# NAS was still offline when the backend booted — a NAS often boots slower than
# the Pi. ~1.75 min total, then we give up; not an ongoing reconnection loop.
_SHARE_REMOUNT_RETRY_DELAYS_S = (15, 30, 60)

# Scan-watcher cadence. Navidrome exposes no scan event of any kind and runs its
# own hourly schedule, so a scan starts that Milō never asked for — polling is
# the only way to know, and the alternative (every browser polling for itself)
# is what this replaces. Idle is slow because nothing is happening; the active
# rate paces how often a storage space's growing track count reaches the UI.
_SCAN_POLL_IDLE_S = 15.0
_SCAN_POLL_ACTIVE_S = 3.0

# Liveness probe for network shares. /proc/mounts answers "is something mounted
# here", never "does the far side still answer": a NAS that loses power leaves
# its CIFS mount in the table indefinitely, and every track behind it becomes a
# stream Navidrome serves as HTTP 200 carrying a JSON error body — which mpv
# skips in silence. USB needs none of this (milo-umount removes the directory,
# so the absence is total and free to observe); a share is the ambiguous one.
#
# statvfs rather than stat or listdir: it is the call that actually reaches the
# filesystem, where the attribute cache (actimeo=1) can answer a stat from
# memory. Measured on the live mount: stat 0.93 ms, statvfs 0.50 ms,
# listdir 4.94 ms.
_LIVENESS_INTERVAL_S = 30.0
_LIVENESS_TIMEOUT_S = 5.0
# Consecutive certain-negative probes before a share counts as gone. Flipping
# `mounted` arms _stop_if_storage_gone, which cuts the music that is playing, so
# one blocked call — which a merely busy NAS produces on its own — must not.
_LIVENESS_FAILURES = 3
# One thread per simultaneously-wedged share; a probe that timed out stays
# parked in the syscall until the kernel gives up on the link, and at most one
# probe per share is ever in flight.
_LIVENESS_PROBE_THREADS = 4
# The errnos that mean "the link is gone" rather than "this call failed".
# Anything else keeps the previous verdict: calling a live NAS dead costs a
# stopped track and a library that leaves the browser, so only a certain
# negative may do it.
_DEAD_LINK_ERRNOS = frozenset({
    errno.EIO, errno.ETIMEDOUT, errno.ENOTCONN, errno.EHOSTDOWN, errno.ESTALE,
})


class NetworkShareService:
    """The configured SMB/NFS shares: config, mount, rescan, liveness, status."""

    def __init__(
        self,
        navidrome_provider: NavidromeProvider,
        on_catalog_changed: Callable[[], None],
        on_storages_changed: Callable[[], Awaitable[None]],
    ) -> None:
        self._logger = logging.getLogger("source.music_library.shares")
        self._data = MusicLibraryDataService()
        self._libraries = NavidromeLibraryService()
        self._storage = StorageManager(navidrome_provider, self._sync_libraries)
        self._navidrome_provider = navidrome_provider
        # The merged-album cache lives with the catalog, not here; a share change
        # invalidates it through this callback rather than reaching back up.
        self._on_catalog_changed = on_catalog_changed
        # Announces a new storage/scan picture to whoever broadcasts it. Called
        # from exactly two places — the end of _sync_libraries (every mount,
        # unmount, rename, forget and share write funnels through it) and the
        # scan watcher — so there is one push per change, not one per caller.
        self._on_storages_changed = on_storages_changed
        # Last scan state seen by the watcher, so it only pushes on a change.
        self._scan: Dict[str, Any] = {"scanning": False}
        # Cuts the watcher's current sleep short. Set whenever something has just
        # made a scan likely, so a short one is not missed between two polls.
        self._scan_kick = asyncio.Event()
        self._bg = BackgroundTaskSet(self._logger, "music_library.shares")
        # Boot-remount retry for shares whose NAS was still offline at startup
        # (see _mount_configured). Tracked so it isn't GC'd mid-flight; bounded
        # and self-terminating, so it needs no explicit cancellation.
        self._retry_task: Optional[asyncio.Task] = None
        # share id -> did its far side answer the last probe. An id absent from
        # the map reads as alive, which is what makes the probe purely
        # subtractive: it can hide a share it has caught being dead, and can
        # never be the reason one fails to appear.
        self._alive: Dict[str, bool] = {}
        # share id -> consecutive certain-negative probes (see _LIVENESS_FAILURES).
        self._probe_failures: Dict[str, int] = {}
        # share id -> the probe submitted for it, kept while it is still parked
        # in the syscall so the next tick doesn't submit a second one.
        self._probe_inflight: Dict[str, Future] = {}
        # A dedicated pool, not asyncio.to_thread: the default executor is 8
        # threads shared with the fan telemetry, the CD poller and api/system.py,
        # and a probe stuck on a dead mount must not starve them.
        self._probe_pool = ThreadPoolExecutor(
            max_workers=_LIVENESS_PROBE_THREADS, thread_name_prefix="share-probe"
        )

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
        # Every successful mount above already synced; this pass covers the ones
        # that did not fire a hook — a configured share whose NAS is offline, and
        # a boot with nothing plugged in at all.
        await self._sync_libraries()
        self._bg.spawn(self._watch_scan(), label="scan-watcher")
        self._bg.spawn(self._watch_share_liveness(), label="share-liveness")

    async def cleanup(self) -> None:
        """Stop the USB monitor thread and drain the library reconciler.

        Called from the lifespan teardown: the reconcile retry and the scan
        watcher are background tasks, and one still sleeping on its schedule at
        shutdown would only wake to talk to a Navidrome that is going down with
        us.

        The probe pool is dropped without waiting: a worker parked on a dead
        mount returns when the kernel gives up on the link, which is minutes,
        and nothing here needs its answer.
        """
        await self._bg.cancel_all()
        self._probe_pool.shutdown(wait=False, cancel_futures=True)
        await self._storage.cleanup()
        await self._libraries.cleanup()

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
            return await self._mount_share(share) is not None
        except Exception as e:
            self._logger.warning(f"Failed to mount share {share.get('id')}: {e}")
            return False

    async def _mount_share(
        self, share: Dict[str, Any], credentials: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """Mount a share and drop whatever the liveness probe thought of it.

        The one path every mount in this service takes, because a mount that
        succeeded *is* the far side answering — mount.cifs talked to it — and a
        verdict must never outlive the mount it was about. Without this, fixing
        a dead share's host and saving it would leave the settings row grey and
        the library out of the browser until the next probe caught up.
        """
        mountpoint = await self._storage.mount_share(share, credentials=credentials)
        if mountpoint is not None:
            self._alive.pop(share["id"], None)
            self._probe_failures.pop(share["id"], None)
        return mountpoint

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

    async def usb_is_mounted(self, uuid: str) -> bool:
        """Whether a known USB key is plugged in right now.

        Lets the forget route tell "no such key" (404) from "unplug it first"
        (409) — :meth:`forget_usb` refuses in both cases and cannot say which.
        There is no list counterpart: the settings screen reads USB keys out of
        :meth:`storages_with_stats` like every other storage space, which is what
        makes a hotplug reach it over the same push instead of a second fetch.
        """
        return any(v["uuid"] == uuid for v in self._storage.get_usb_mounts())

    async def storages(self) -> List[Dict[str, Any]]:
        """Every storage space music can come from, as one uniform list.

        This is what the library view's storage filter is built from, so each
        entry carries the ``library_id`` that scopes a browse call (None while
        Navidrome hasn't accepted the library yet — such an entry cannot be
        filtered by, and the frontend leaves it out).

        Membership is now the *same* rule for both kinds — a storage space is
        listed once Milō knows it, whether or not it answers right now — and
        ``mounted`` is what separates "browsable" from "listed". A configured
        share whose NAS is asleep and an unplugged USB key are the same
        situation, and were only ever modelled differently because a key used to
        be detected live and never remembered.
        """
        entries: List[Dict[str, Any]] = [
            {
                "kind": "share",
                "id": share["id"],
                "name": share.get("name") or share["id"],
                "mountpoint": str(MUSIC_LIBRARY_MOUNT_ROOT / share["id"]),
                "mounted": share["mounted"],
            }
            for share in await self.list()
        ]
        known = await self._data.get_known_usb()
        live = {volume["uuid"]: volume for volume in self._storage.get_usb_mounts()}
        # Known keys first, in first-seen order, so the filter's buttons don't
        # reshuffle on a replug; a key mounted but not yet remembered (the window
        # between the mount and the sync that records it) is appended so it is
        # never invisible.
        for uuid in [*known, *(u for u in live if u not in known)]:
            entry = known.get(uuid, {})
            volume = live.get(uuid)
            entries.append({
                "kind": "usb",
                "id": uuid,
                # The name the user gave this key (filed under its filesystem
                # UUID, so it survives a replug), else its sanitized disk label.
                "name": entry.get("name")
                or (volume or entry).get("label")
                or uuid,
                # The disk label milo-mount derived the mountpoint from, kept
                # beside the display name because clearing a user-given name
                # restores it — the rename screen offers it as the placeholder.
                "label": (volume or entry).get("label", ""),
                # The live mountpoint while plugged in, else the one it was last
                # mounted at — which is the path its Navidrome library carries,
                # and therefore what keeps that library findable while it's away.
                "mountpoint": (volume or entry).get("mountpoint", ""),
                "mounted": volume is not None,
            })
        self._disambiguate(entries)
        for entry in entries:
            entry["library_id"] = self._libraries.library_id(entry["mountpoint"])
        return entries

    async def storages_with_stats(self) -> List[Dict[str, Any]]:
        """:meth:`storages` plus each space's catalog counts — the UI's shape.

        Kept separate from :meth:`storages` because the counts cost one HTTP call
        to Navidrome, and the internal callers (library reconcile, playlist
        placement) want the cheap list. This is what the ``/storages`` route and
        the ``storages_changed`` broadcast both return, so a page load and a
        hotplug push agree field for field.
        """
        entries = await self.storages()
        stats = await self._libraries.stats()
        for entry in entries:
            entry.update(
                stats.get(entry["mountpoint"], {"track_count": 0, "album_count": 0,
                                                "missing_count": 0})
            )
        return entries

    def scan_state(self) -> Dict[str, Any]:
        """Whether a Navidrome scan is running right now, as last polled."""
        return dict(self._scan)

    async def request_scan(self) -> None:
        """Ask for an incremental rescan, deferring if one is already running.

        The same primitive the mount paths use, so a caller with no storage event
        to report (the source opening) gets the busy-scanner handling for free
        rather than a second, thinner implementation of it.
        """
        await self._storage.request_scan()

    async def note_scan_started(self) -> None:
        """Navidrome has just accepted a scan — push it and watch it closely.

        Without this a manual refresh is invisible: a quick scan over an
        already-indexed catalog takes ~0.4 s while the idle poll is 15 s, so it
        would start and finish between two polls and no client would ever learn
        it happened — the refresh button would sit there saying nothing.

        Not optimism: ``start_scan`` returned success, so a scan *is* running at
        the moment of this push. The kick then has the watcher confirm the end of
        it within one active poll rather than one idle one.
        """
        self._scan = {"scanning": True}
        await self._on_storages_changed()
        self._scan_kick.set()

    async def _watch_scan(self) -> None:
        """Poll Navidrome's scan status and push every change out.

        Replaces the per-browser polling the settings screen used to do: one
        watcher for the whole appliance, and every client learns a scan started
        or finished — including the hourly one Navidrome schedules itself, which
        no client could have known to poll for.

        A finished scan is a catalog change, so the merged-album cache is dropped
        and the same push carries the storage spaces' new counts.
        """
        while True:
            try:
                scanning = await self._poll_scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.debug("Scan watcher poll failed: %s", exc)
                scanning = False
            # Sleep until the cadence elapses OR something kicks us — a mount
            # change and an explicit refresh both make a scan imminent, and
            # waiting out the idle cadence would miss a short one entirely.
            delay = _SCAN_POLL_ACTIVE_S if scanning else _SCAN_POLL_IDLE_S
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._scan_kick.wait(), timeout=delay)
            self._scan_kick.clear()

    async def _poll_scan(self) -> bool:
        """One poll; broadcasts on a change. Returns whether a scan is running."""
        client = await self._navidrome_provider()
        if client is None:
            return False
        status = await client.get_scan_status()
        if status is None:
            return False
        was_scanning = bool(self._scan.get("scanning"))
        scanning = bool(status.get("scanning"))
        self._scan = {"scanning": scanning}
        # While a scan runs, each poll carries the storage spaces' growing track
        # counts, so a freshly-plugged key's tab fills as it is indexed. A scan
        # that just ended also invalidates the catalog caches built from it.
        if scanning or was_scanning:
            if was_scanning and not scanning:
                self._on_catalog_changed()
            await self._on_storages_changed()
        return scanning

    async def _watch_share_liveness(self) -> None:
        """Keep ``mounted`` honest for network shares, by asking the far side.

        A share is the only storage space that can be listed in /proc/mounts and
        unreadable at the same time: unplugging a USB key removes its directory,
        while a NAS losing power leaves a perfectly-formed CIFS mount behind.
        Every browse then offers tracks whose stream Navidrome answers with HTTP
        200 and a JSON error body, which mpv skips without a word.

        **The verdict folds into ``mounted``; it adds no second key.** ``mounted``
        already means "usable right now" to every consumer, and a `s.mounted &&
        s.reachable !== false` chain in the frontend is exactly the shape this
        repo forbids. So the "storage disconnected" message that already exists
        starts working for a dead NAS, for that space alone.

        **What it promises:** a share is hidden in ~30 s plus a probe timeout,
        *after the kernel has given up on the link* — not instantly. `soft`
        bounds the failure, not the latency: the CIFS client only notices a dead
        server through its echo worker (echo_interval=60), so the first blocked
        call after a power cut can sit there for a minute or two. Add the
        three-strike hysteresis and a real outage is announced in a couple of
        minutes, deliberately.

        Rejected: /proc/fs/cifs/DebugData gives the same verdict with no network
        I/O, but it flips on that same echo timeout (cheaper, not faster), it is
        a debug interface with no ABI promise, and it is CIFS-only while
        ShareRequest.type also accepts nfs — two code paths for one question.
        """
        while True:
            await asyncio.sleep(_LIVENESS_INTERVAL_S)
            try:
                await self._probe_shares()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("Share liveness sweep failed: %s", exc)

    async def _probe_shares(self) -> None:
        """One sweep over the mounted shares; pushes once if a verdict moved.

        Only configured shares are probed — :meth:`StorageManager.get_mounted_share_ids`
        reports every mount under the root, USB keys included, and those have no
        far side to ask about.
        """
        mounted_ids = self._storage.get_mounted_share_ids()
        targets = [
            share["id"]
            for share in await self._data.list_shares()
            if share["id"] in mounted_ids
        ]
        # A verdict is about a mount, so it dies with it: an unmounted share is
        # already reported absent by /proc/mounts, and keeping the verdict would
        # let it survive into the next mount under the same id.
        for share_id in [i for i in self._alive if i not in targets]:
            self._alive.pop(share_id, None)
            self._probe_failures.pop(share_id, None)

        changed = False
        returned = False
        for share_id in targets:
            verdict = await self._probe_share(share_id)
            if verdict is None:
                continue
            if verdict:
                self._probe_failures[share_id] = 0
                if not self._alive.get(share_id, True):
                    self._alive[share_id] = True
                    changed = returned = True
                    self._logger.info("Share %s is answering again", share_id)
                continue
            failures = self._probe_failures.get(share_id, 0) + 1
            self._probe_failures[share_id] = failures
            if failures >= _LIVENESS_FAILURES and self._alive.get(share_id, True):
                self._alive[share_id] = False
                changed = True
                self._logger.warning(
                    "Share %s stopped answering (%d consecutive probes); "
                    "hiding it until it comes back",
                    share_id, failures,
                )

        if returned:
            # Symmetric with a USB replug: what changed on the far side while it
            # was away is only knowable from a scan, and a quick one adds without
            # purging. The kick puts "indexing…" on screen with it rather than an
            # idle cadence later.
            await self._storage.request_scan()
            self._scan_kick.set()
        if changed:
            # A space entering or leaving the browsable set is a catalog change:
            # the browse scope is built from `mounted`, and the per-scope album
            # lists cached behind it were built for the other set.
            self._on_catalog_changed()
            await self._on_storages_changed()

    async def _probe_share(self, share_id: str) -> Optional[bool]:
        """Ask one share's filesystem whether it is still there.

        True = it answered, False = it certainly did not, None = inconclusive,
        keep the previous verdict (fail open — see _DEAD_LINK_ERRNOS).

        The timeout is a deadline on *our wait*, not a cancellation: a worker
        already inside statvfs stays there whatever asyncio does with the future,
        so the probe is left tracked and no second one is submitted for that
        share. Still parked at the next tick is itself a negative — the
        filesystem is not answering — which is what lets a wedged mount reach the
        three strikes it takes to be hidden.
        """
        inflight = self._probe_inflight.get(share_id)
        if inflight is not None:
            if not inflight.done():
                return False
            self._probe_inflight.pop(share_id, None)

        mountpoint = str(MUSIC_LIBRARY_MOUNT_ROOT / share_id)
        future = self._probe_pool.submit(os.statvfs, mountpoint)
        self._probe_inflight[share_id] = future
        try:
            await asyncio.wait_for(
                asyncio.wrap_future(future), timeout=_LIVENESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            return False
        except OSError as exc:
            if exc.errno in _DEAD_LINK_ERRNOS:
                return False
            self._logger.debug(
                "Liveness probe for %s inconclusive: %s", share_id, exc
            )
            return None
        return True

    async def storage_id_for_library(self, library_id: int) -> Optional[str]:
        """The storage space a Navidrome library belongs to, or None.

        Library ids are Navidrome's and are reassigned when a key comes back;
        anything Milō persists about a storage space is keyed by *this* id.
        """
        return next(
            (
                entry["id"]
                for entry in await self.storages()
                if entry["library_id"] == library_id
            ),
            None,
        )

    async def playlist_storages(self) -> Dict[str, str]:
        """Playlist id → storage id, for the playlists Milō created."""
        return await self._data.get_playlist_storages()

    async def record_playlist_storage(self, playlist_id: str, library_id: int) -> None:
        """Tie a new playlist to the storage space it was created in."""
        storage_id = await self.storage_id_for_library(library_id)
        if storage_id is not None:
            await self._data.set_playlist_storage(playlist_id, storage_id)

    async def forget_playlist(self, playlist_id: str) -> None:
        """Drop a deleted playlist's storage association."""
        await self._data.forget_playlist(playlist_id)

    @staticmethod
    def _disambiguate(entries: List[Dict[str, Any]]) -> None:
        """Make display names unique, in place.

        Two keys can carry the same filesystem label, and Navidrome requires a
        unique library name — but the reason to do it here is the filter itself:
        two buttons reading "MUSIC" name nothing. The mountpoint's final segment
        is already unique (milo-mount disambiguates it), so it is what the
        duplicate is qualified with.
        """
        seen: set = set()
        for entry in entries:
            if entry["name"] in seen:
                entry["name"] = f"{entry['name']} ({entry['mountpoint'].rsplit('/', 1)[-1]})"
            seen.add(entry["name"])

    async def list(self) -> List[Dict[str, Any]]:
        """Configured network shares (non-secret metadata; safe over the API).

        Each entry is annotated with a live ``mounted`` flag so the settings UI
        can show which shares are actually connected right now versus
        configured-but-offline. It is the conjunction of the two questions that
        both have to be yes: /proc/mounts says a filesystem is mounted there,
        *and* the liveness probe has not caught the far side gone (see
        :meth:`_watch_share_liveness` — a NAS that loses power answers the first
        question and not the second). The probe is purely subtractive: a share it
        has never had a verdict on reads as mounted, so it can hide a share but
        can never be the reason one fails to appear.

        This is the only expression of ``mounted`` for a share; everything that
        decides anything from it — the storage filter, the browse scope, the
        purge gate, the stop-on-storage-gone — reads it from here.
        """
        mounted_ids = self._storage.get_mounted_share_ids()
        return [
            {
                **share,
                "mounted": share.get("id") in mounted_ids
                and self._alive.get(share.get("id"), True),
            }
            for share in await self._data.list_shares()
        ]

    async def offline_names(self) -> List[str]:
        """Storage spaces not mounted right now (empty when everything is up).

        Gates the full-scan/purge route: a full scan purges every track Navidrome
        cannot see (Scanner.PurgeMissing="full"), so running one while a storage
        space is away drops a catalog that is still perfectly valid.

        USB keys count here, and that is the whole point of remembering them: an
        unplugged key keeps its library and its index, so a full scan would now
        undo exactly the 18-minute indexing pass a replug is supposed to skip.
        """
        return [
            entry["name"]
            for entry in await self.storages()
            if not entry["mounted"]
        ]

    # =========================================================================
    # WRITES
    # =========================================================================

    async def _sync_libraries(self) -> None:
        """Bring Navidrome's libraries in line with the storage spaces there are.

        The hook StorageManager calls after every mount and unmount, and the one
        write path for the library set — it is a reconcile, so calling it twice
        costs nothing and a missed call heals on the next change.
        """
        # Record whatever is mounted before reading the set: a key that just
        # arrived has to be in the known set for storages() to list it, and the
        # mountpoint milo-mount chose is only knowable now.
        for volume in self._storage.get_usb_mounts():
            await self._data.remember_usb(
                volume["uuid"], volume["label"], volume["mountpoint"]
            )
        entries = await self.storages()
        await self._libraries.reconcile(
            {entry["mountpoint"]: entry["name"] for entry in entries}
        )
        # A mount change is a catalog change: a key that just appeared (or left)
        # invalidates the per-storage album lists cached for the grid, and with
        # them the cache entries of libraries that no longer exist.
        self._on_catalog_changed()
        # The single push. Every storage change reaches this line — mount,
        # unmount, rename, forget, and the three share writes, which all route
        # through here — so the frontend needs no refetch and no polling to see
        # a key appear or go.
        await self._on_storages_changed()
        # A mount change is about to trigger a scan (StorageManager does it right
        # after this hook): have the watcher look now rather than up to a full
        # idle cadence later, so "indexing…" appears with the new storage space
        # instead of 15 seconds after it.
        self._scan_kick.set()

    async def rename_usb(self, uuid: str, name: str) -> bool:
        """Name a known USB key. False when no key has that UUID.

        The name is persisted against the key's filesystem UUID and pushed to its
        Navidrome library, so the storage filter and Navidrome's own UI agree.
        Works while the key is unplugged: its library outlives the unplug, so the
        rename has somewhere to land.
        """
        if not await self._data.set_usb_name(uuid, name):
            return False
        await self._sync_libraries()
        return True

    async def forget_usb(self, uuid: str) -> bool:
        """Drop a USB key from the known set. False when no key has that UUID.

        Refuses while the key is plugged in: the mount would put it straight back
        on the next sync, so the only readable outcome is to unplug it first.
        Dropping it retires its Navidrome library on the reconcile below, which is
        what actually frees the index — the deliberate counterpart to keeping one
        forever by default.
        """
        if any(v["uuid"] == uuid for v in self._storage.get_usb_mounts()):
            return False
        if not await self._data.forget_usb(uuid):
            return False
        await self._sync_libraries()
        self._on_catalog_changed()
        return True

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
        mountpoint = await self._mount_share(
            share, credentials=self._credentials(req)
        )
        # A share whose NAS is down never reached the mount hook, so this is what
        # gives it its library: a configured share keeps one whether or not it
        # answers today (libraries.py), and without this it would stay
        # unbrowsable until an unrelated mount change or a reboot.
        if mountpoint is None:
            await self._sync_libraries()
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
        mountpoint = await self._mount_share(
            share, credentials=self._credentials(req)
        )
        # Same reason as in add(): a failed remount fires no hook, and the new
        # name has to reach the library even when the NAS is not answering.
        if mountpoint is None:
            await self._sync_libraries()
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
        # The unmount's own sync still saw the share in the config (config is
        # dropped after, so a failed unmount can't orphan it); this is the pass
        # that actually retires its library.
        await self._sync_libraries()
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
