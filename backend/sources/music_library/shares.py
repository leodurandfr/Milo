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
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.config.constants import MUSIC_LIBRARY_MOUNT_ROOT
from backend.shared.background import BackgroundTaskSet
from backend.sources.music_library.data import MusicLibraryDataService
from backend.sources.music_library.libraries import NavidromeLibraryService
from backend.sources.music_library.models import ShareRequest
from backend.sources.music_library.navidrome_client import CATALOG_UNREACHABLE
from backend.sources.music_library.storage import NavidromeProvider, StorageManager


class ShareLoginNeedsPassword(Exception):
    """A CIFS edit names a new username without its password: milo-mount would
    keep the saved cred file and authenticate as the old account."""


# Retry schedule (seconds between attempts) for configured network shares that
# are not mounted. The ramp covers the usual boot race (a NAS boots slower than
# the Pi); the plateau covers a router and a NAS that take minutes to come back
# after a power cut, and keeps going until every configured share is mounted —
# giving up left one out of the library for the rest of the session.
# Slow on purpose: each attempt is one milo-mount held under the storage lock,
# which a USB hotplug or a share edit waits behind. Against a host that does not
# answer, milo-mount refuses after its reachability probe (a few seconds) and
# never creates the mountpoint: an empty one is a library Navidrome marks
# missing, and these attempts never end while the NAS stays away.
_SHARE_REMOUNT_RETRY_DELAYS_S = (15, 30, 60)
_SHARE_REMOUNT_PLATEAU_S = 300

# The periodic rescan, the backstop behind every scan Milō asks for on an event
# (a mount, the source opening, Navidrome or a share coming back): it catches a
# mount whose own trigger was lost, whatever the reason. Milō's and not
# Navidrome's (`Scanner.Schedule` is off in provisioning/navidrome.sh), because
# Navidrome's own pass scans every library — the unplugged key's included, which
# it answered with an ERROR per pass — while this one names the mounted ones.
# Deliberately NOT the freshness mechanism: the source asks for a scan when the
# library is opened, the moment freshness matters and the NAS is about to be
# woken by playback anyway, which is what lets this run every 6h rather than
# hourly — a scan over a sleeping NAS spins its disks up for nothing.
_BACKSTOP_SCAN_S = 6 * 3600.0

# Scan-watcher cadence. Navidrome exposes no scan event of any kind, so a scan
# Milō did not announce (one started on the unit by hand) is only known by
# polling, and the alternative (every browser polling for itself) is what this
# replaces.
#
# Every poll Navidrome *answers* is a write to its database: the authenticated
# getScanStatus updates the account's last access and the player's last-seen,
# each rate-limited to once a minute (Navidrome v0.64.2, consts.go). At the 15 s
# this used to be, that was two SQLite transactions a minute, day and night, on
# a unit nobody was using. Measured on the SD card with nobody using the unit,
# Navidrome wrote 336-352 KiB per 5 min at the 15 s probe (about 95-99 MiB a
# day) and writes 128 KiB per 5 min at the 5 min one (about 36 MiB a day,
# measured 2026-11-22). So the idle
# cadence sits well past that one-minute limiter, and only a scan Milō did not
# ask for is left to it: every scan Milō asks for is announced the moment
# Navidrome accepts it, through note_scan_started (one asked through
# StorageManager.request_scan — a mount or a replug, the source opening,
# Navidrome or a share coming back — by its scanning hook, a press of Refresh by
# POST /scan itself), and any catalog call
# that goes unanswered — the client reports each one — asks the watcher at once
# (catalog_unanswered). A poll Navidrome does not answer writes nothing, which
# is why a catalog that is down is asked often; the active rate paces how often
# a storage space's growing track count reaches the UI.
_SCAN_POLL_IDLE_S = 300.0
_SCAN_POLL_UNANSWERED_S = 15.0
_SCAN_POLL_ACTIVE_S = 3.0
# How long a call reported unanswered no longer marks the catalog down by
# itself, once the poll it woke found Navidrome answering. A browser reloads
# every list it holds on the catalog coming back, so a call Navidrome is too
# busy to answer in time (an album page during a first index) is asked again on
# every up edge, fails again, and would push the next down edge: measured in a
# harness, 45,000 pushes in half a second against a 502, one cycle per
# timeout against a slow read — each one a reload burst per browser and an
# availability flip in every app's state. Within the hold a report only wakes
# the watcher, whose own poll is what decides.
_UNANSWERED_REFUTED_HOLD_S = _SCAN_POLL_IDLE_S

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
# Must outlast the kernel's own give-up on a dead link, or the errno branch of
# _probe_share is never reached and every negative arrives as a timeout — which
# is the one verdict that cannot tell a dead NAS from a slow one. Measured on
# this unit with the NAS's cable pulled: statvfs, stat and listdir all returned
# EHOSTDOWN after 10.18 s, against the 5.0 s this used to allow. The healthy
# 0.50 ms above is not the figure that governs here; the failing case is.
_LIVENESS_TIMEOUT_S = 15.0
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
        merged_album_count: Callable[[int, bool], Optional[int]],
    ) -> None:
        self._logger = logging.getLogger("source.music_library.shares")
        self._data = MusicLibraryDataService()
        self._libraries = NavidromeLibraryService(on_storages_changed)
        self._storage = StorageManager(
            navidrome_provider, self._sync_libraries, self.note_scan_started,
            self.scan_targets,
        )
        self._navidrome_provider = navidrome_provider
        # The merged-album cache lives with the catalog, not here; a share change
        # invalidates it through this callback rather than reaching back up.
        self._on_catalog_changed = on_catalog_changed
        # Announces a new storage/scan picture to whoever broadcasts it. Called
        # from the end of _sync_libraries (every mount, unmount, rename, forget
        # and share write funnels through it), the scan and liveness watchers,
        # and the library reconciler's retry when it lands the ids the inline
        # reconcile could not — so there is one push per change, not one per
        # caller.
        self._on_storages_changed = on_storages_changed
        # A storage space's album count as its grid lists it, multi-disc sets
        # merged: (library id, mounted) → count, or None to keep Navidrome's
        # own. Answered from what the source holds, never by a walk — see
        # storages_with_stats.
        self._merged_album_count = merged_album_count
        # Last scan state seen by the watcher, so it only pushes on a change.
        # Optimistic until the watcher's first poll, which runs immediately:
        # claiming "still starting" on a healthy unit would be a worse lie than
        # the sub-second window it would cover.
        self._scan: Dict[str, Any] = {"scanning": False, "catalog_ready": True}
        # Whether the watcher's own last poll found Navidrome not answering — an
        # outage it saw, as opposed to one a single unanswered call reported
        # (see catalog_unanswered). Only the first owes the catalog a rescan.
        self._poll_saw_down = False
        # A down state catalog_unanswered set and has not pushed yet. No poll
        # may write the scan state under it: one that found Navidrome answering
        # would put "ready" back first, and the down edge would never be sent.
        self._announcing_down = False
        # When a poll last found Navidrome answering under a down state only a
        # call had reported — the report refuted. See _UNANSWERED_REFUTED_HOLD_S.
        self._unanswered_refuted_at: Optional[float] = None
        # Cuts the watcher's current sleep short. Set whenever something has just
        # made a scan likely, so a short one is not missed between two polls.
        self._scan_kick = asyncio.Event()
        self._bg = BackgroundTaskSet(self._logger, "music_library.shares")
        # The remount loop (see _remount_configured): one at a time, and a
        # share write that did not mount rearms the running one.
        self._remounting = False
        self._remount_rearm = asyncio.Event()
        # One share write at a time, the remount loop's attempts included: an
        # attempt that read a share before an edit or a removal landed would
        # otherwise mount the old config between the edit's unmount and its
        # mount, or mount a share that no longer exists.
        self._write_lock = asyncio.Lock()
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

        The config load runs first (an unreadable file is set aside and the
        shares start empty; only a card that cannot be read raises). USB detection
        is **fail-open** (no udev on a dev host just disables auto-mount), and the
        network remount runs in the background, so neither can block startup: a
        NAS that does not answer used to hold the API for one mount timeout per
        share.
        """
        await self._data.initialize()
        await self._storage.initialize()
        # Every share that mounts fires the hook and syncs again; this pass
        # covers the ones that do not — a configured share whose NAS is offline,
        # and a boot with nothing plugged in at all.
        await self._sync_libraries()
        self._remount_soon(boot=True)
        self._bg.spawn(self._watch_scan(), label="scan-watcher")
        self._bg.spawn(self._watch_share_liveness(), label="share-liveness")
        self._bg.spawn(self._backstop_scans(), label="backstop-scan")

    async def cleanup(self) -> None:
        """Stop the USB monitor thread and drain the library reconciler.

        Called from the lifespan teardown: the remount loop, the reconcile retry
        and the scan watcher are background tasks, and one still sleeping on its
        schedule at shutdown would only wake to talk to a Navidrome (or a NAS)
        while the backend is going down. The remount loop is the one that never
        ends on its own while a share stays away.

        The probe pool is dropped without waiting: a worker parked on a dead
        mount returns when the kernel gives up on the link, which is minutes,
        and nothing here needs its answer.
        """
        await self._bg.cancel_all()
        self._probe_pool.shutdown(wait=False, cancel_futures=True)
        await self._storage.cleanup()
        await self._libraries.cleanup()

    def _remount_soon(self, boot: bool = False) -> None:
        """Have the remount loop try the unmounted shares again, starting it if
        it is not running; a running one starts its ramp over."""
        if self._remounting:
            self._remount_rearm.set()
            return
        self._remounting = True
        self._bg.spawn(self._remount_configured(boot=boot), label="share-remount")

    async def _remount_configured(self, boot: bool) -> None:
        """Mount every configured network share, retrying until none is missing.

        No credentials are supplied — milo-mount reuses each share's persisted
        root-only cred file. The boot pass runs at once; then the ramp, then the
        plateau, until every configured share is mounted, so a NAS that comes
        back late reconnects without a manual re-save. Every pass reads the
        config again, so a share removed or mounted by an edit in the meantime
        drops out. One warning when the ramp ends; past it, failures are quiet.
        Each successful mount triggers a Navidrome rescan via mount_share.
        """
        ramp = _SHARE_REMOUNT_RETRY_DELAYS_S
        attempt = 0
        first = boot
        try:
            while True:
                if not first:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._remount_rearm.wait(),
                            ramp[attempt] if attempt < len(ramp)
                            else _SHARE_REMOUNT_PLATEAU_S,
                        )
                        self._remount_rearm.clear()
                        attempt = 0
                        continue
                    attempt += 1
                first = False
                quiet = attempt > len(ramp)
                try:
                    pending = await self._remount_pass(quiet=quiet)
                except Exception as e:
                    # The config could not be read, so nothing says every
                    # share is mounted: the next pass reads it again.
                    self._logger.log(
                        logging.DEBUG if quiet else logging.WARNING,
                        f"Share remount pass failed: {e}",
                    )
                    pending = None
                if pending == [] and not self._remount_rearm.is_set():
                    if attempt:
                        self._logger.info("All configured shares remounted")
                    return
                if pending != [] and attempt == len(ramp):
                    self._logger.warning(
                        "Shares still not mounted: %s; retrying every %d s",
                        pending if pending is not None else "config unreadable",
                        _SHARE_REMOUNT_PLATEAU_S,
                    )
        finally:
            self._remounting = False

    async def _remount_pass(self, quiet: bool) -> List[str]:
        """One attempt at each configured share; returns the ids still unmounted.

        A share is skipped only when it is mounted *and* this session recorded
        that mount. A backend restart leaves the mounts in place, unrecorded, and
        milo-mount's idempotent answer is what records one and asks for its scan
        — so one whose NAS died meanwhile is asked again until it answers.
        """
        pending: List[str] = []
        for share_id in [s["id"] for s in await self._data.list_shares()]:
            async with self._write_lock:
                share = await self._data.get_share(share_id)
                if share is None:
                    continue
                if (
                    share_id in self._storage.recorded_share_ids()
                    and share_id in self._storage.get_mounted_share_ids()
                ):
                    continue
                if not await self._try_mount(share, quiet=quiet):
                    pending.append(share_id)
        return pending

    async def _try_mount(self, share: Dict[str, Any], quiet: bool = False) -> bool:
        """One remount attempt for a configured share; True on success.

        Fail-open like the rest of the remount path: an offline NAS returns None
        (or the helper raises), which we log and report as not-mounted so the
        caller can retry it — never propagates. ``quiet`` logs a failure at
        debug, for an attempt that is expected to fail.
        """
        try:
            return await self._mount_share(share, quiet=quiet) is not None
        except Exception as e:
            self._logger.log(
                logging.DEBUG if quiet else logging.WARNING,
                f"Failed to mount share {share.get('id')}: {e}",
            )
            return False

    async def _mount_share(
        self,
        share: Dict[str, Any],
        credentials: Optional[Dict[str, str]] = None,
        quiet: bool = False,
    ) -> Optional[str]:
        """Mount a share and drop whatever the liveness probe thought of it.

        The one path every mount in this service takes, because a mount that
        succeeded *is* the far side answering — mount.cifs talked to it — and a
        verdict must never outlive the mount it was about. Without this, fixing
        a dead share's host and saving it would leave the settings row grey and
        the library out of the browser until the next probe caught up.
        """
        mountpoint = await self._storage.mount_share(
            share, credentials=credentials, quiet=quiet
        )
        if mountpoint is not None:
            self._alive.pop(share["id"], None)
            self._probe_failures.pop(share["id"], None)
            # The probe parked on the *previous* mount is evidence about a mount
            # that no longer exists, and _probe_share reads a still-parked one as
            # a negative — three ticks of it and the share we have just repaired
            # is hidden again, 90 s after it came back. Dropped rather than
            # cancelled: the worker is inside statvfs, where cancellation is a
            # no-op, and it returns when the kernel gives up on the old link.
            self._probe_inflight.pop(share["id"], None)
        return mountpoint

    async def _unmount_share(self, share_id: str) -> None:
        """Unmount a share. No scan follows, and none is wanted.

        Removing the share retires its Navidrome library on the reconcile that
        follows (:meth:`remove`), which takes its tracks with it — so there is
        nothing left for a scan to clean up. An *edit* keeps the library and
        remounts under the same id: whatever the old path held stops being found
        and is marked missing by the next ordinary scan, which is enough, since
        a marked track is already absent from every catalog answer.
        """
        await self._storage.unmount_share(share_id)

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

        ``album_count`` is the one count not taken as Navidrome states it: its
        native ``totalAlbums`` counts every disc of a multi-disc set, the grid
        lists the set once, and the button names what the grid holds. So each
        space with albums is counted by the source (``merged_album_count``) —
        a mounted one from its merged catalog, one that is away from the last
        count known — and keeps Navidrome's figure only where none is known.
        That count never walks the catalog: this list runs on every storage
        change, right after the catalog cache was dropped, and a mounted space
        whose catalog is not cached is counted off the push, which comes again
        once the count has moved.
        """
        entries = await self.storages()
        stats = await self._libraries.stats()
        for entry in entries:
            entry.update(
                stats.get(entry["mountpoint"], {"track_count": 0, "album_count": 0,
                                                "missing_count": 0})
            )
        for entry in entries:
            if entry["library_id"] is None or not entry["album_count"]:
                continue
            count = self._merged_album_count(entry["library_id"], entry["mounted"])
            if count is not None:
                entry["album_count"] = count
        return entries

    def scan_state(self) -> Dict[str, Any]:
        """Whether a scan is running, and whether Navidrome answers, as polled."""
        return dict(self._scan)

    async def scan_targets(self) -> Optional[List[int]]:
        """The library ids a scan names: those of the storage spaces mounted now.

        ``mounted`` as ``storages()`` reports it, so a NAS the liveness probe
        found dead is left out as well as an unplugged key: a scan over a dead
        CIFS mount sits in the kernel for minutes. None when Navidrome could not
        be asked, ``[]`` when nothing is mounted — both mean no scan.
        """
        entries = await self.storages()
        return await self._libraries.mounted_library_ids(
            {entry["mountpoint"] for entry in entries if entry["mounted"]}
        )

    async def _backstop_scans(self) -> None:
        """Rescan the mounted storage spaces every ``_BACKSTOP_SCAN_S``.

        Through ``request_scan``, so a scan already running is waited out rather
        than fired into, and a Navidrome that is down (the dock has it off) is
        one debug line, not an error.
        """
        while True:
            await asyncio.sleep(_BACKSTOP_SCAN_S)
            try:
                await self.request_scan()
            except Exception as exc:
                self._logger.error("Periodic Navidrome rescan failed: %s", exc)

    async def request_scan(self) -> None:
        """Ask for an incremental rescan, deferring if one is already running.

        The same primitive the mount paths use, so a caller with no storage event
        to report (the source opening) gets the busy-scanner handling for free
        rather than a second, thinner implementation of it.
        """
        await self._storage.request_scan()

    def catalog_started(self) -> None:
        """Navidrome has just been started: reconcile its libraries once it listens.

        Until then every storage space carries a null library id, which the
        library view drops. The watcher is asked too, so a catalog it last saw
        down is seen answering as soon as it does.
        """
        self._libraries.reconcile_soon()
        self._scan_kick.set()

    def catalog_unanswered(self) -> None:
        """A catalog call has just gone unanswered: say so, and watch for its return.

        The client calls it on every Subsonic call Navidrome did not answer
        (``NavidromeClient``'s ``on_unanswered``), the routes when there is no
        client to call at all or its credential was refused. Idle, the watcher
        only asks every few minutes, so a Navidrome that restarts while nobody
        looks would otherwise stay "ready" on every screen while each list it
        is asked for fails — or comes back empty, for the reads that fail open.

        Marked down at once rather than left to the poll the kick brings: a
        browser that saw a 503 reloads it on the catalog *coming back*, so the
        down edge has to reach it first. The kick therefore follows the push,
        and no poll writes the state while the push is on its way — a poll that
        found Navidrome answering again would otherwise put "ready" back under
        it, and both pushes would say ready.

        Only the edge: the poll that sees the catalog answer again neither drops
        the caches nor asks for a rescan unless it saw the outage itself (see
        _poll_scan), so one slow call costs no more than the edge. Said once per
        outage, however many calls fail meanwhile: the watcher, down, already
        asks at its unanswered cadence. And not again for a while once the
        watcher found such a report wrong (_UNANSWERED_REFUTED_HOLD_S): within
        that hold a report only has the watcher look, and a down edge comes from
        its poll or not at all — the edge it would push is the one a browser
        answers by asking again for the very call that failed.
        """
        if not self._scan.get("catalog_ready"):
            return
        refuted = self._unanswered_refuted_at
        if refuted is not None and time.monotonic() - refuted < _UNANSWERED_REFUTED_HOLD_S:
            self._scan_kick.set()
            return
        self._scan = {**self._scan, "catalog_ready": False}
        self._announcing_down = True
        self._bg.spawn(self._announce_unanswered(), label="catalog-unanswered")

    async def _announce_unanswered(self) -> None:
        try:
            await self._on_storages_changed()
        finally:
            self._announcing_down = False
            self._scan_kick.set()

    async def note_scan_started(self) -> None:
        """Navidrome is scanning for Milō — push it and watch it closely.

        Called once Navidrome has accepted a scan (the refresh route, and
        StorageManager for every scan it asks for), or found one running that a
        mount's scan now waits behind. Without it a scan Milō asked for is
        invisible: a quick one over an already-indexed catalog takes ~0.4 s
        while the idle poll is minutes long, so it would start and finish
        between two polls and no client would ever learn it happened. Called
        *after* the request, never before: the add-share wizard concluded "no
        music in this folder" from a watcher woken by the mount, which read
        "no scan" and went back to sleep before the scan was asked for.

        Not optimism: a scan *is* running at the moment of this push. The kick
        then has the watcher confirm the end of it within one active poll rather
        than one idle one.
        """
        self._scan = {"scanning": True, "catalog_ready": True}
        await self._on_storages_changed()
        self._scan_kick.set()

    async def _watch_scan(self) -> None:
        """Poll Navidrome's scan status and push every change out.

        Replaces the per-browser polling the settings screen used to do: one
        watcher for the whole appliance, and every client learns a scan started
        or finished — including one started on the unit by hand, which no client
        could have known to poll for.

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
            # Sleep until the cadence elapses OR something kicks us — a scan
            # Milō started, a catalog call that went unanswered, the catalog
            # started: waiting out the idle cadence would miss a short scan
            # entirely, and leave a catalog that is down drawn as ready.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._scan_kick.wait(), timeout=self._poll_delay(scanning)
                )
            self._scan_kick.clear()

    def _poll_delay(self, scanning: bool) -> float:
        """How long the watcher sleeps after a poll, unless kicked."""
        if scanning:
            return _SCAN_POLL_ACTIVE_S
        if self._scan.get("catalog_ready"):
            return _SCAN_POLL_IDLE_S
        return _SCAN_POLL_UNANSWERED_S

    async def _poll_scan(self) -> bool:
        """One poll; broadcasts on a change. Returns whether a scan is running."""
        client = await self._navidrome_provider()
        # No client is exactly as unusable as a daemon that is down — the cred
        # file is not written yet, or an auth rejection dropped it — and every
        # catalog route answers 503 in both cases. Reported as ready, it is the
        # state the flag exists to end: an absent catalog drawn as an empty one.
        status = (
            await client.get_scan_status() if client is not None
            else CATALOG_UNREACHABLE
        )
        if self._announcing_down:
            # A call has just been reported unanswered and its push is on its
            # way: this answer is older than the push's own kick, which polls
            # again once the down edge has gone.
            return bool(self._scan.get("scanning"))
        was_scanning = bool(self._scan.get("scanning"))
        was_ready = bool(self._scan.get("catalog_ready"))
        saw_outage = self._poll_saw_down
        self._poll_saw_down = not status.available
        # A poll that got no answer says nothing about the scan; keeping the last
        # word is what stops a restart from reading as a scan that just ended.
        scanning = was_scanning if status.scanning is None else status.scanning
        # A mount still owed its scan is indexing too: between the scan it waits
        # behind and its own, Navidrome reports idle, and a falling edge there is
        # the wizard counting a share before its scan began.
        scanning = scanning or self._storage.scan_pending()
        self._scan = {"scanning": scanning, "catalog_ready": status.available}
        # While a scan runs, each poll carries the storage spaces' growing track
        # counts, so a freshly-plugged key's tab fills as it is indexed. A scan
        # that just ended also invalidates the catalog caches built from it.
        #
        # Navidrome becoming reachable again is the same kind of event and used
        # to push nothing at all: every count read while it was down came back
        # zero, and no later poll had a reason to correct them — measured, a
        # restart left an open browser on an empty catalog until the source
        # itself was restarted.
        #
        # Only an outage this watcher saw for itself, though — a whole poll
        # unanswered, a restart. One a single call reported (catalog_unanswered)
        # comes back with the edge alone: the caches never keep a failed read,
        # and a rescan per slow call is a database write per slow call, on the
        # Navidrome that was already too slow.
        regained = status.available and not was_ready and saw_outage
        if status.available and not was_ready and not saw_outage:
            self._unanswered_refuted_at = time.monotonic()
        if status.available != was_ready or (status.available and (scanning or was_scanning)):
            if (was_scanning and not scanning) or regained:
                self._on_catalog_changed()
            await self._on_storages_changed()
        if regained:
            # The scan owed from while it was down. Every path that would have
            # asked — the source starting, a mount landing, the library view
            # opening — was answered "not answering yet" and dropped it, and the
            # next one after that is the 6-hourly backstop.
            await self.request_scan()
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
            # purging. StorageManager announces it once Navidrome has it, which
            # puts "indexing…" on screen with it rather than an idle cadence later.
            await self._storage.request_scan()
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
        the stop-on-storage-gone — reads it from here.
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
            {entry["mountpoint"]: entry["name"] for entry in entries},
            {entry["mountpoint"] for entry in entries if entry["mounted"]},
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
        # No kick here, although a mount is about to trigger a scan: the scan is
        # asked for after this hook returns, so a watcher woken now reads "no
        # scan" and sleeps a full idle cadence. StorageManager announces the scan
        # once Navidrome has accepted it.

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
        (the config is persisted either way — a share that's down now is retried
        in the background, but the user should be told it didn't connect).
        """
        async with self._write_lock:
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
            # A share whose NAS is down never reached the mount hook, so this is
            # what gives it its library: a configured share keeps one whether or
            # not it answers today (libraries.py), and without this it would stay
            # unbrowsable until an unrelated mount change or a reboot. The
            # remount loop then keeps trying it.
            if mountpoint is None:
                await self._sync_libraries()
                self._remount_soon()
            self._on_catalog_changed()
            return {**share, "mounted": mountpoint is not None}

    async def update(
        self, share_id: str, req: ShareRequest
    ) -> Optional[Dict[str, Any]]:
        """Replace a share's mutable fields, remount, and rescan.

        Returns the updated share, or None if no share has that id. A CIFS request
        that omits the password keeps the existing cred file (idempotent PUT) as
        long as its username is the stored one or absent; an empty username turns
        the share guest, and a new one without its password raises
        ShareLoginNeedsPassword before anything is touched.
        """
        async with self._write_lock:
            existing = await self._data.get_share(share_id)
            if existing is None:
                return None
            updates: Dict[str, Any] = {
                "type": req.type,
                "host": req.host,
                "path": req.path,
                "name": req.name,
            }
            # Credentials move as a unit with the password (which rewrites the cred
            # file): a new password stores the new username/domain too; NFS and an
            # emptied username clear them; a CIFS edit that omits the password
            # keeps the existing login, which a different username cannot do.
            # A password-only login stores no username, so "" is a change only
            # when one was stored.
            username = req.username
            if username is not None and not username.strip():
                username = ""
            login_moved = (
                username is not None and username != (existing.get("username") or "")
            )
            if req.type == "nfs" or (not req.password and login_moved and not username):
                updates.update(has_credentials=False, username=None, domain=None)
            elif req.password:
                updates.update(
                    has_credentials=True, username=username or None, domain=req.domain
                )
            elif login_moved:
                raise ShareLoginNeedsPassword(share_id)
            share = await self._data.update_share(share_id, updates)
            if share is None:
                return None
            # Clearing the flag is not enough: milo-mount attaches <id>.cred whenever
            # the file exists, whatever the share now says. Left behind, it would
            # remount a share switched back to CIFS with the old password while the
            # edit screen — which reads has_credentials — says none is saved.
            dropped = updates.get("has_credentials") is False
            if dropped and existing.get("has_credentials"):
                await self._storage.forget_share_credentials(share_id)
            # Unmount first so a changed host/path/credentials actually takes effect.
            await self._unmount_share(share_id)
            mountpoint = await self._mount_share(
                share, credentials=self._credentials(req)
            )
            # Same reasons as in add(): a failed remount fires no hook, and the new
            # name has to reach the library even when the NAS is not answering.
            if mountpoint is None:
                await self._sync_libraries()
                self._remount_soon()
            self._on_catalog_changed()
            return {**share, "mounted": mountpoint is not None}

    async def remove(self, share_id: str) -> bool:
        """Unmount, drop the config entry, and forget the credentials. Returns
        False if no share has that id."""
        async with self._write_lock:
            if await self._data.get_share(share_id) is None:
                return False
            await self._unmount_share(share_id)
            await self._data.remove_share(share_id)
            await self._storage.forget_share_credentials(share_id)
            # The unmount's own sync still saw the share in the config (config
            # is dropped after, so a failed unmount can't orphan it); this is the
            # pass that actually retires its library.
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
