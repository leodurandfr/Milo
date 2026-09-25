# backend/sources/music_library/source.py
"""Music Library audio source (Family C — active player).

Plays the user's own music, indexed by a Navidrome sidecar and streamed to mpv
over localhost HTTP (mirrors the Podcast source, which streams from Podcast
Index). Controlled from Milō's UI, with rich metadata (artwork/title/artist).

Playback model (P1-6): a queue is built from any context (album / genre /
playlist / search) — the frontend hands the ordered Subsonic song dicts to the
``play_context`` command, the source maps each id to a bit-perfect Navidrome
``stream?id=…&format=raw`` URL, and mpv plays them as one native playlist. With
the unit's ``--gapless-audio=yes`` that is truly gapless. Transport
(pause/resume/next/prev/seek/play_index) drives that single mpv playlist; the
now-playing projection (title/artist/album/art + queue/index/shuffle) is
broadcast over WS. Shuffle can be toggled live from the player (``set_shuffle``
reshuffles the upcoming tracks without interrupting the current one).

Resume-on-return: a session ended by a source switch, the idle timeout, a
multiroom toggle, a dead mpv or a failed load leaves its queue, track and second
as the resume point (RESUME_POLICY); the next activation reopens it paused — or
playing, after a toggle that found it playing — for as long as it is fresh
(``RESUME_TTL_S``, whose expiry is published). An explicit Stop, a queue played
out and a storage space that left forget it; it is never persisted.

Where the music comes from is NOT here: the configured SMB/NFS shares and the
USB watcher underneath them live in :mod:`shares.py`, reached as ``source.shares``
— they run for the whole backend lifetime, independent of playback, so a
plugged-in key is indexed even when music_library is not the active source.
"""
import asyncio
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from backend.config.constants import NAVIDROME_SERVICE
from backend.core.audio_source import Result
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, PhaseEvent, ReroutePolicy, ResumePolicy,
)
from backend.core.models.audio_wire import MusicLibraryDetails, ResumeView
from backend.core.models.commands import SkipParams
from backend.core.models.ws_events import SourceErrorReason, MusicLibraryStoragesChanged
from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.shared.mpv_audio_source import MpvAudioSource, MpvSession
from backend.sources.music_library.disc_merge import (
    is_merged_id,
    merge_albums,
    parse_merged_id,
)
from backend.sources.music_library.models import (
    PlayContextParams,
    PlayIndexParams,
    SeekParams,
    SetShuffleParams,
)
from backend.sources.music_library.artist_images import ArtistImageService
from backend.sources.music_library.navidrome_client import NavidromeClient
from backend.sources.music_library.shares import NetworkShareService

# Within this many seconds of a track, `prev` restarts the current track;
# earlier than that it steps to the previous entry (Spotify/go-librespot feel).
PREV_RESTART_THRESHOLD_S = 3

# Merged-album (multi-disc) catalog cache. The alphabetical grid pages over the
# whole collapsed catalog so a "… CD 1"/"CD 2" pair is never split across a page
# boundary; a short TTL bounds staleness from the watcher/scheduled scans (an
# explicit rescan or share change invalidates it at once).
ALBUM_CACHE_TTL_S = 30.0
# getAlbumList2's per-request ceiling — loop by it to pull the whole catalog.
_ALBUM_PAGE = 500

# How long a resume-on-return snapshot stays worth restoring, measured from the
# moment playback stopped. It exists to cover a detour — a source switch, the
# idle auto-stop while the user is answering the door — not a later sitting:
# past this, opening the library on a paused track nobody remembers starting
# reads as a bug, and the fresh READY placeholder is what the user wants.
RESUME_TTL_S = 600.0

# Scrobble threshold — the Last.fm rule Navidrome implements: a play counts once
# it has been listened to for half the track or four minutes, whichever comes
# first, and a track shorter than 30 s never counts at all. This submission is
# the ONLY thing that feeds play_date/play_count, which is what getAlbumList2
# type=recent and type=frequent are built from (fetching `stream` counts for
# nothing — OpenSubsonic forbids servers from treating it as a play).
SCROBBLE_MIN_DURATION_S = 30
SCROBBLE_MAX_THRESHOLD_S = 240


@dataclass(eq=False)
class LibrarySession(MpvSession):
    """One queue playing, from the play to a named end. `entries` are mpv's
    playlist entry ids, by queue position: what start-file and end-file name,
    and so how a gapless advance or the end of the queue is recognized."""
    queue: List[Dict[str, Any]] = field(default_factory=list)
    unshuffled: List[Dict[str, Any]] = field(default_factory=list)
    index: int = 0
    library_id: Optional[int] = None
    shuffle: bool = False
    entries: List[Optional[int]] = field(default_factory=list)
    announced: Optional[int] = None     # the entry whose start went to Navidrome
    failed_in_a_row: int = 0
    # Scrobble bookkeeping for the track playing: seconds heard, accumulated
    # from how far the playhead moved (a seek forward hears nothing).
    played_seconds: float = 0.0
    scrobbled: bool = False
    last_tick_position: Optional[float] = None


class MusicLibrarySource(MpvAudioSource):
    """Music Library source (Family C): UI-driven gapless queue playback over a
    Navidrome-indexed local library, with the USB storage layer (P1-4) live.

    **`NETWORK_REQUIREMENT` is left at NONE, and that is a decision.** It reads
    as an oversight next to Radio's INTERNET and AirPlay's LAN, because half the
    source does need the LAN: a library on an SMB/NFS share is unreachable
    without it. The other half is not — a USB key plus a local Navidrome plays
    with the cable out — and the attribute is per *source* while the question is
    per *library*. Declaring LAN would take a USB library that works perfectly
    and replace its browser with "no network"; on the mixed setup (a key and two
    shares, the common one) it would blank the whole thing to explain half of it.
    Over-blocking something that works is the worse error, so the flat answer
    stays NONE. A dynamic one is not available either: the state machine reads
    this synchronously every time it composes the state, and everything
    `NetworkShareService` knows is async.

    It costs nothing, because unavailability is already modelled one level down
    and at the right granularity: each storage space carries `mounted`, which is
    what `browsableStorages` filters on and what makes `disconnectedStorage` put
    "storage unplugged" in place of the grid — for that space alone, while the
    others keep playing. A link dying with the mount still in /proc/mounts is
    covered there too: `NetworkShareService._watch_share_liveness` probes the far
    side and folds its verdict into that same `mounted`, so the message that
    already exists fires — rather than a source-wide flag that cannot see which
    library the user is in.
    """

    IDLE_POLICY = IdlePolicy.AUTO_STOP
    REROUTE = ReroutePolicy.RESTART_AND_RESTORE
    RESUME_POLICY = ResumePolicy(
        capture_on=frozenset({
            EndReason.IDLE_TIMEOUT, EndReason.SOURCE_SWITCH, EndReason.REROUTE,
            EndReason.DAEMON_DIED, EndReason.LOAD_FAILED, EndReason.STREAM_LOST,
        }),
        # A queue played out, an explicit Stop, a storage that left: nothing to
        # reopen. SENDER_LEFT cannot happen here.
        forget_on=frozenset({
            EndReason.EOF, EndReason.USER_STOP, EndReason.STORAGE_GONE,
            EndReason.SENDER_LEFT,
        }),
        ttl_s=RESUME_TTL_S,
        restore_on_start=True,
    )
    SESSION_DAEMON = False

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
    ):
        super().__init__(
            source_id="music_library",
            service_name="milo-music-library.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )
        # Where the music comes from: the configured SMB/NFS shares and the USB
        # watcher underneath them. Runs for the whole backend lifetime, like the
        # CD disc-watcher, not gated on this source being active — and it rescans
        # through the shared catalog client below rather than building its own.
        self._shares = NetworkShareService(
            self.get_navidrome_client,
            self.invalidate_album_cache,
            self.broadcast_storages,
        )
        # Starts and stops of the catalog (see set_catalog_running), in order.
        self._catalog_bg = BackgroundTaskSet(self._logger, "source.music_library.catalog")
        self._catalog_lock = asyncio.Lock()
        # Navidrome Subsonic client for the /api/music-library/* browse routes,
        # for building stream URLs at play time, and for the StorageManager's
        # post-mount rescans. Built lazily (the cred file only exists once the
        # daemon has provisioned its service account) and shared by all three —
        # routes read the catalog even while music_library is not active.
        self._navidrome: Optional[NavidromeClient] = None
        # Artist photos, resolved from Deezer by Milō because Navidrome's own
        # online tier picks the wrong person (see artist_images.py). Like the
        # client above it outlives playback — the cover route reaches it while
        # the source is inactive — and it borrows the same lazy accessor to turn
        # a cover id into an artist name.
        self._artist_images = ArtistImageService(self.get_navidrome_client)
        # Why the library cannot play now (docs: "le fil", §3), as of the last
        # storage picture broadcast_storages() saw: no mounted storage bearing a
        # library, or no catalog (Navidrome not answering / not provisioned).
        self._availability: Optional[str] = None
        # Merged (multi-disc) album catalog, cached for the alphabetical grid —
        # one entry per browse scope (the sorted library ids → (built_at,
        # albums)), so the merged view and the "everything mounted" view of the
        # same spaces are one entry rather than two.
        self._album_cache: Dict[Tuple[int, ...], Tuple[float, List[Dict[str, Any]]]] = {}
        # A–Z artist index per browse scope, same shape and lifetime as the
        # album cache above: the artist grid and the search results both read it
        # (see get_artist_index).
        self._artist_cache: Dict[Tuple[int, ...], Tuple[float, List[Dict[str, Any]]]] = {}
        # playlist id → its first track's album id, for placing a playlist Milō
        # did not create in a storage space (see playlists_in_scope).
        self._playlist_album: Dict[str, Optional[str]] = {}

    # =========================================================================
    # NAVIDROME CLIENT (shared with routes.py)
    # =========================================================================

    async def get_navidrome_client(self) -> Optional[NavidromeClient]:
        """Return the shared Navidrome client, building it on first use.

        Returns None until the first-boot-provisioned cred file exists (fresh dev
        host, provisioning not finished); the routes surface that as a 503. Re-reads
        the cred file on each attempt while None, so the client appears as soon as
        provisioning completes — no backend restart needed.
        """
        if self._navidrome is None:
            self._navidrome = NavidromeClient.from_cred_file()
        return self._navidrome

    async def invalidate_navidrome_client(self) -> None:
        """Drop and close the cached Navidrome client so the next request rebuilds
        it from a possibly-rotated cred file. Called by routes after an auth
        rejection (NavidromeAuthError)."""
        if self._navidrome is not None:
            await self._navidrome.close()
            self._navidrome = None

    async def browse_scope(self, library_id: Optional[int] = None) -> List[int]:
        """The Navidrome libraries a browse call is allowed to read.

        The default — what every unscoped call gets — is **the storage spaces
        that are mounted right now**, because a space that cannot be read is not
        a space to offer: Navidrome keeps an unplugged key's index on purpose
        (that is what makes a replug cost a quick scan instead of 18 minutes),
        and answers a stream request for its files with HTTP **200** carrying a
        JSON error body, which mpv skips over in silence. There is no hook at
        play time, so the filter has to fall at browse time.

        An explicit ``library_id`` is honoured as asked, mounted or not: the
        caller named its scope, and the frontend deliberately keeps the
        selection on a storage space that has just gone away.
        """
        if library_id is not None:
            return [library_id]
        return self._scope_from(await self._shares.storages())

    @staticmethod
    def _scope_from(entries: List[Dict[str, Any]], library_id: Optional[int] = None) -> List[int]:
        """:meth:`browse_scope`'s rule over an already-read storage list.

        Split out so a caller that needs both the scope *and* the entries reads
        them once: recomputing `storages()` costs five JSON file reads, and
        `GET /playlists` used to do it twice per request.
        """
        if library_id is not None:
            return [library_id]
        return [
            entry["library_id"]
            for entry in entries
            if entry["mounted"] and entry["library_id"] is not None
        ]

    async def mounted_album_ids(self) -> set:
        """Every album id readable right now — the descent routes' filter.

        ``getArtist``/``getAlbum``/``getPlaylist`` take no scope in Subsonic, so
        what they return is post-filtered against this set.
        """
        return await self._scope_album_ids(await self.browse_scope())

    async def get_artist_index(self, scope: List[int]) -> List[Dict[str, Any]]:
        """A browse scope's A–Z artist index (``getArtists``), cached.

        Cached because it has two readers, not because one is slow: the artist
        grid renders it, and search borrows the ``albumCount`` it carries. That
        borrowing is the point. ``search3`` honours ``musicFolderId`` for the
        rows it returns but not for the counter it hangs on an artist row, so it
        answers the artist's count across the *whole* catalog — measured, same
        instant, same scope: an artist with 2 albums on the mounted NAS and 3 on
        an unplugged iPod came back as 2 from ``getArtists`` and 5 from
        ``search3``. ``getArtists`` is the one that scopes it.

        Same TTL and same invalidation as the album cache — a rescan or a share
        change moves both.
        """
        key = tuple(sorted(scope))
        now = asyncio.get_event_loop().time()
        cached = self._artist_cache.get(key)
        if cached is not None and now - cached[0] < ALBUM_CACHE_TTL_S:
            return cached[1]
        client = await self.get_navidrome_client()
        if client is None:
            return []
        index = await client.get_artists(scope)
        if index:
            self._artist_cache[key] = (now, index)
        return index

    async def get_merged_albums(self, scope: List[int]) -> List[Dict[str, Any]]:
        """A browse scope's catalog, alphabetical, multi-disc sets collapsed.

        The album grid pages over this list (see routes.get_albums) so a split
        "… CD 1"/"CD 2" release is merged even when the pair would straddle a page
        boundary. ``scope`` is the storage spaces to read (see
        :meth:`browse_scope`). Cached with a short TTL; an explicit rescan or
        share change calls :meth:`invalidate_album_cache`. Returns [] until the
        catalog is reachable, and caches only a walk that ran to the end — a
        not-yet-ready daemon and a page that failed mid-catalog both retry on the
        next call rather than serving what they got for the whole TTL.
        """
        key = tuple(sorted(scope))
        now = asyncio.get_event_loop().time()
        cached = self._album_cache.get(key)
        if cached is not None and now - cached[0] < ALBUM_CACHE_TTL_S:
            return cached[1]
        client = await self.get_navidrome_client()
        if client is None:
            return []
        albums: List[Dict[str, Any]] = []
        offset = 0
        complete = True
        while True:
            page = await client.get_album_list(
                scope,
                list_type="alphabeticalByName",
                size=_ALBUM_PAGE,
                offset=offset,
            )
            if page is None:
                # A failed request ends the walk short of the catalog. This call
                # still serves what it has — a partial grid beats none — but the
                # result is not the catalog and must not become the answer for
                # the whole TTL.
                complete = False
                break
            albums.extend(page)
            if len(page) < _ALBUM_PAGE:
                break
            offset += _ALBUM_PAGE
        merged = merge_albums(albums)
        if albums and complete:
            self._album_cache[key] = (now, merged)
        return merged

    async def genres_in_scope(self, scope: List[int]) -> List[Dict[str, Any]]:
        """The genres present in a browse scope, in the getGenres shape.

        Navidrome accepts ``musicFolderId`` on getAlbumList2, getArtists,
        search3 and getSongsByGenre — but **not** on getGenres, which answers
        with every genre in the catalog whatever is asked. Left as-is, the genre
        list would offer a genre that belongs to another storage space and open
        an empty view, since the drill-down *is* scoped.

        So it is derived from the scope's own album catalog, which is already
        cached for the album grid. ``songCount`` is the sum of the matching
        albums' track counts: exact for the usual single-genre album, an
        over-count for an album whose tracks disagree, and never wrong about
        *which* genres exist — the part a tap depends on.
        """
        totals: Dict[str, List[int]] = {}
        for album in await self.get_merged_albums(scope):
            names = [g.get("name") for g in album.get("genres") or []]
            if not names and album.get("genre"):
                names = [album["genre"]]
            for name in dict.fromkeys(n for n in names if n and n.strip()):
                entry = totals.setdefault(name, [0, 0])
                entry[0] += int(album.get("songCount") or 0)
                entry[1] += 1
        return [
            {"value": name, "songCount": counts[0], "albumCount": counts[1]}
            for name, counts in sorted(totals.items())
        ]

    async def playlists_in_scope(
        self, playlists: List[Dict[str, Any]], library_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Keep the playlists that belong to a browse scope's storage spaces.

        Takes the raw ``library_id`` rather than a resolved scope so the storage
        list is read **once** for the whole request: the scope is derived from
        the same entries this needs anyway. Passing a scope meant the caller had
        to resolve it first, and `GET /playlists` paid for two identical
        computations — five JSON file reads each.

        Navidrome's playlists are catalog-wide — ``getPlaylists`` accepts
        ``musicFolderId`` and ignores it — but a playlist mixing a NAS and a USB
        key is exactly what the storage filter exists to prevent, so membership
        is decided here, two ways:

        - **Created in Milō** → the storage space it was created in was recorded
          (``shares.playlist_storages``), and that is the answer. It is the only
          one that works for an *empty* playlist, which has no content to judge.
        - **Anything else** — Navidrome auto-imports the ``.m3u`` files it finds,
          so a music key brings its own playlists — → its first track's album is
          looked up in the scope's catalog. One extra call per unknown playlist,
          memoised until the next rescan.

        A playlist that is both unrecorded and empty is shown everywhere: there
        is nothing to place it by, and hiding it would make it unreachable. A
        record pointing at a storage space that no longer exists — the share it
        was created in was removed — is treated as no record at all, for exactly
        the same reason: it would otherwise match nothing and vanish from every
        storage space while still existing in Navidrome. A record pointing at a
        space that exists but is *away* is a different thing and is honoured: the
        playlist is out of scope, because none of it can be played.
        """
        recorded = await self._shares.playlist_storages()
        entries = await self._shares.storages()
        scope = self._scope_from(entries, library_id)
        scoped_storages = {e["id"] for e in entries if e["library_id"] in scope}
        live_storages = {entry["id"] for entry in entries}
        album_ids = await self._scope_album_ids(scope)

        kept: List[Dict[str, Any]] = []
        undecided: List[Dict[str, Any]] = []
        for playlist in playlists:
            known = recorded.get(playlist.get("id"))
            if known in live_storages:
                if known in scoped_storages:
                    kept.append(playlist)
            elif not playlist.get("songCount"):
                kept.append(playlist)
            else:
                undecided.append(playlist)

        # One round-trip per unplaced playlist, all at once: they are independent
        # and a library of imported .m3u files would otherwise serialise dozens.
        album_of = await asyncio.gather(
            *(self._playlist_album_id(playlist["id"]) for playlist in undecided)
        )
        kept.extend(
            playlist
            for playlist, album_id in zip(undecided, album_of)
            if album_id is not None and album_id in album_ids
        )
        # gather() answered out of order relative to getPlaylists; restore it.
        order = {playlist["id"]: index for index, playlist in enumerate(playlists)}
        return sorted(kept, key=lambda playlist: order[playlist["id"]])

    async def _scope_album_ids(self, scope: List[int]) -> set:
        """Every album id in a browse scope, merged sets expanded.

        The grid's merged catalog is reused rather than re-fetched, so this
        usually costs nothing — but a merged multi-disc album carries a
        synthetic id, and a track points at the member album, so the members are
        what has to be in the set.
        """
        ids: set = set()
        for album in await self.get_merged_albums(scope):
            album_id = album.get("id")
            if not album_id:
                continue
            if is_merged_id(album_id):
                ids.update(parse_merged_id(album_id))
            else:
                ids.add(album_id)
        return ids

    async def _playlist_album_id(self, playlist_id: str) -> Optional[str]:
        """The album of a playlist's first track (memoised), or None if empty."""
        if playlist_id in self._playlist_album:
            return self._playlist_album[playlist_id]
        client = await self.get_navidrome_client()
        album_id = None
        if client is not None:
            playlist = await client.get_playlist(playlist_id)
            entries = (playlist or {}).get("entry") or []
            album_id = entries[0].get("albumId") if entries else None
        self._playlist_album[playlist_id] = album_id
        return album_id

    def forget_playlist_placement(self, playlist_id: str) -> None:
        """Drop a playlist's memoised album after its contents changed.

        Reordering or removing tracks can move the first one to another storage
        space, which is what the playlist is placed by — without this the memo
        would keep it filed under the old one until the next rescan.
        """
        self._playlist_album.pop(playlist_id, None)

    def invalidate_album_cache(self) -> None:
        """Drop every scope's cached catalog — merged albums and artist index —
        so the next grid load rebuilds it (called after an explicit rescan or a
        share add/update/remove).

        The playlist→album memo goes with it: both answer "what is in this
        storage space", and a rescan is exactly when that changes. So does the
        client's cover memo — a rescan is when an album gains or loses its art,
        and the one moment an artist Deezer had no photo for is worth re-asking.
        """
        self._album_cache.clear()
        self._artist_cache.clear()
        self._playlist_album.clear()
        self._artist_images.invalidate()
        if self._navidrome is not None:
            self._navidrome.invalidate_cover_memo()

    async def broadcast_storages(self) -> None:
        """Push the storage spaces (with their counts) and the scan flag over WS.

        The hook NetworkShareService calls on every storage change and on each
        poll of a running scan — the one thing that makes plugging a key in, or
        pulling it out, visible without a refetch. What a storage leaving does
        to playback is applied first, in the actor, so the state it leaves is
        published before the storages event; the availability it implies goes
        into the state too.
        """
        entries = await self._shares.storages_with_stats()
        scan = self._shares.scan_state()
        await self._submit(Result(lambda: self._storage_changed(entries, scan)))
        if self.state_machine:
            await self.state_machine.broadcast(MusicLibraryStoragesChanged(
                storages=entries,
                scanning=bool(scan.get("scanning")),
            ))

    async def _storage_changed(self, entries: List[Dict[str, Any]], scan: Dict[str, Any]) -> None:
        """A storage space that is no longer mounted takes with it the session
        playing from it — the session, never the source: mpv and the other
        storages stay playable (E01) — and a resume point that would reopen it
        (E51).

        A queue built unscoped is attributed to no storage and left alone: an
        unasked-for stop is worse than a track that plays on out of page cache.

        The availability they imply is decided here too, in the actor: no
        mounted storage carrying a library, else a catalog that does not answer.
        """
        def gone(library_id: Optional[int]) -> bool:
            return library_id is not None and not any(
                entry["library_id"] == library_id and entry["mounted"] for entry in entries
            )

        session = self._session
        if isinstance(session, LibrarySession) and gone(session.library_id):
            self._logger.info(
                "Storage space for library %s is gone — ending the session", session.library_id
            )
            await self._end_playback(EndReason.STORAGE_GONE)
        else:
            point = self._resume_point
            if point is not None and gone(point.content["queue_library_id"]):
                self._logger.info("Storage space of the resume point is gone — forgetting it")
                self._set_resume_point(None)
                if self._session is None and self._published is not None:
                    self._publish()

        # After the session's end, never before: published first, the new
        # availability went out over the session that storage was playing.
        if not any(entry["mounted"] and entry.get("library_id") is not None for entry in entries):
            availability = "no_storage"
        elif not scan.get("catalog_ready"):
            availability = "catalog_unavailable"
        else:
            availability = None
        if availability != self._availability:
            self._availability = availability
            self._availability_changed()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def initialize(self) -> bool:
        """Start or stop the catalog as the dock says, bring up the music origins
        (shares + USB watcher), then base init.

        Nothing else starts the catalog: the unit has no [Install], and
        PartOf=milo-backend never propagates a start. Fail-open: a catalog that
        does not come up leaves an empty library, not a dead backend.
        """
        # A dock app id is the source id (AUDIO_SOURCE_APPS derives from the enum).
        self.set_catalog_running(
            self.source_id in await self._settings_service.get_setting("dock.enabled_apps")
        )
        await self._shares.initialize()
        return await super().initialize()

    def set_catalog_running(self, wanted: bool) -> None:
        """Start or stop the catalog, at boot and when the dock toggles Music Library.

        Navidrome runs exactly while the dock enables the source, whether or not
        the source is active: it serves the settings screen and indexes a key
        plugged in while another source plays.

        Not awaited: `systemctl start` waits out the unit's ExecStartPre and its
        config oneshot, which is no reason to hold up the storage layer at boot or
        a dock toggle's HTTP answer — and a caller that gave up at the control
        timeout would report a failure while systemd carried the job through
        anyway. Requests are applied in the order they were made, in a task set
        of their own: `_bg` is drained on every stop of the source.
        """
        self._catalog_bg.spawn(self._apply_catalog(wanted), label="catalog")

    async def _apply_catalog(self, wanted: bool) -> None:
        async with self._catalog_lock:
            if wanted:
                ok = await self._start_service(NAVIDROME_SERVICE)
            else:
                ok = await self._stop_service(NAVIDROME_SERVICE)
        if not ok:
            self._logger.warning(
                "Could not %s %s; the library stays empty until it runs",
                "start" if wanted else "stop", NAVIDROME_SERVICE,
            )
        elif wanted:
            # Every storage space carries a null library id until the libraries
            # are reconciled, and the library view drops those.
            self._shares.catalog_started()

    async def shutdown(self) -> None:
        await self._catalog_bg.cancel_all()
        await super().shutdown()

    def _resume_content(self, session: "LibrarySession"):
        track = session.queue[session.index]
        return track.get("id") or "", session.position * 1000, {
            "queue": list(session.queue),
            "queue_unshuffled": list(session.unshuffled),
            "queue_index": session.index,
            # Without it the restored queue is attributed to no space, and a
            # key leaving would not end it.
            "queue_library_id": session.library_id,
            "shuffle": session.shuffle,
        }

    async def _do_start(self) -> bool:
        """Start the mpv service, connect IPC, then reopen the session left
        behind (paused; playing after a multiroom toggle that found it playing)
        or idle on the READY placeholder."""
        try:
            if not await self._start_service_and_wait():
                return False
            if not await self._attach_mpv():
                return False
            await self._listen_to_mpv()
            await self._load_auto_stop_config()

            # Opening the library is the moment its freshness matters, and the
            # only moment Milō can infer it: music copied straight onto a NAS
            # raises no event anyone here can see (inotify crosses neither a
            # network mount nor a mount itself), so without this the catalog only
            # moves on the scheduled pass. Spawned rather than awaited — the scan
            # is incremental and asynchronous on Navidrome's side, and a wedged
            # daemon must delay the source by nothing.
            self._bg.spawn(self._shares.request_scan(), label="open-rescan")

            point = self._resume_point
            if point is not None and point.reason is EndReason.REROUTE:
                await self._restore(point, playing=point.phase is not Phase.PAUSED)
                return True
            if point is not None and self.RESUME_POLICY.restore_on_start and self._resume_fresh():
                await self._restore(point, playing=False)
                return True
            if point is not None:
                self._set_resume_point(None)
            self._publish()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self.end_session(EndReason.LOAD_FAILED)
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """A source switch: the session is kept to reopen (resume-on-return)."""
        await self._end_with_position(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    @handle_errors(default=False)
    async def _do_release(self) -> bool:
        await self._end_with_position(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _end_with_position(self, reason: EndReason) -> None:
        session = self._session
        if isinstance(session, LibrarySession):
            await self._sync_position(session)
        await self.end_session(reason)

    async def _cleanup(self) -> None:
        """Tear down mpv. The storage layer and the shared Navidrome client stay:
        routes and the USB watcher use them while the source is inactive."""
        await self._detach_mpv()

    # =========================================================================
    # COMMANDS
    # =========================================================================

    COMMANDS = {
        "play_context": PlayContextParams,
        "play_index": PlayIndexParams,
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
        "seek": SeekParams,
        "skip": SkipParams,
        "set_shuffle": SetShuffleParams,
        "stop": None,
    }
    COMMAND_SCOPES = {
        "play_context": CommandScope.CONTENT,
        "play_index": CommandScope.RESUME,
        "pause": CommandScope.SESSION,
        "resume": CommandScope.RESUME,
        "next": CommandScope.SESSION,
        "prev": CommandScope.SESSION,
        "seek": CommandScope.SESSION,
        "skip": CommandScope.SESSION,
        "set_shuffle": CommandScope.SESSION,
        "stop": CommandScope.RESUME,
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        if cmd == "play_context":
            return await self._handle_play_context(params)
        if cmd == "play_index":
            return await self._handle_play_index(params)
        if cmd == "pause":
            return await self._handle_pause()
        if cmd == "resume":
            return await self._handle_resume()
        if cmd == "next":
            return await self._handle_next()
        if cmd == "prev":
            return await self._handle_prev()
        if cmd == "seek":
            return await self._handle_seek(params)
        if cmd == "skip":
            return await self._handle_skip(params)
        if cmd == "set_shuffle":
            return await self._handle_set_shuffle(params)
        if cmd == "stop":
            return await self._handle_stop()
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_play_context(self, params: PlayContextParams) -> Dict[str, Any]:
        """Build the mpv playlist from a context and start playing at start_index."""
        if not self._mpv:
            return self.error_response("Music library not active")
        if await self.get_navidrome_client() is None:
            return self.error_response("Music library catalog not ready")

        tracks = list(params.tracks)
        original_order = list(tracks)  # pristine order for a later shuffle-off
        start_index = min(params.start_index, len(tracks) - 1)
        if params.shuffle:
            # Keep the picked track first, shuffle everything behind it.
            first = tracks.pop(start_index)
            random.shuffle(tracks)
            tracks.insert(0, first)
            start_index = 0

        # A fresh context supersedes the session and any saved resume point.
        await self.end_session(EndReason.USER_STOP)
        self._set_resume_point(None)
        if not await self._open_queue(
            tracks, original_order, start_index, params.library_id, params.shuffle,
            start_s=0, playing=True,
        ):
            return self.error_response("Failed to load playlist")
        return self.success_response(f"Playing {len(tracks)} track(s)")

    async def _handle_play_index(self, params: PlayIndexParams) -> Dict[str, Any]:
        """Jump to an entry of the queue — the live one, or the one on the
        resume view (restored, then played there: E50)."""
        session = self._session
        if session is None:
            point = self._resume_point
            if point is None:
                return self.error_response("No active queue")
            if params.index >= len(point.content["queue"]):
                return self.error_response(f"Queue index out of range: {params.index}")
            if not await self._restore(point, playing=True, index=params.index):
                return self.error_response("Failed to load playlist")
            return self.success_response(f"Playing track {params.index + 1}")
        if params.index >= len(session.queue):
            return self.error_response(f"Queue index out of range: {params.index}")
        return await self._switch_to_index(session, params.index)

    async def _handle_next(self) -> Dict[str, Any]:
        session = self._session
        if session.index >= len(session.queue) - 1:
            return self.success_response("Already at end of queue")
        return await self._switch_to_index(session, session.index + 1)

    async def _handle_prev(self) -> Dict[str, Any]:
        session = self._session
        # The live playhead, not the last tick's: up to a second stale.
        await self._sync_position(session)
        if session.position >= PREV_RESTART_THRESHOLD_S or session.index == 0:
            if not (await self._mpv.seek(0) and await self._set_mpv_pause(False)):
                return self.mpv_refused("restart track")
            session.position = 0
            self._anchor_position(0)
            self._reset_scrobble(session)
            self._scrobble_now_playing(session)
            return self.success_response("Restarted track")
        return await self._switch_to_index(session, session.index - 1)

    async def _switch_to_index(self, session: "LibrarySession", index: int) -> Dict[str, Any]:
        """Play entry `index` of the live queue (play_index / next / prev)."""
        if not (await self._mpv.play_index(index) and await self._set_mpv_pause(False)):
            return self.mpv_refused(f"switch to track {index + 1}")
        self._move_to_track(session, index)
        session.opened = False
        self._sync_phase(session, PhaseEvent.TRACK_CHANGE)
        self._publish()
        return self.success_response(f"Playing track {index + 1}")

    async def _handle_pause(self) -> Dict[str, Any]:
        session = self._session
        # The exact playhead, so the paused state lands where playback stopped.
        await self._sync_position(session)
        if not await self._mpv.pause():
            return self.mpv_refused("pause")
        return self.success_response("Paused")

    async def _handle_resume(self) -> Dict[str, Any]:
        """Unpause the live queue, or reopen the one a stop left behind — what
        the rotary and the IR remote send, knowing no other name."""
        if self._session is None:
            point = self._resume_point
            if point is None:
                return self.error_response("No session to resume")
            if not await self._restore(point, playing=True):
                return self.error_response("No session to resume")
            return self.success_response("Resumed")
        if not await self._mpv.resume():
            return self.mpv_refused("resume")
        return self.success_response("Resumed")

    async def _handle_seek(self, params: SeekParams) -> Dict[str, Any]:
        session = self._session
        position = int(params.position_ms / 1000)
        if not await self._mpv.seek(position):
            return self.mpv_refused(f"seek to {position}s")
        session.position = position
        self._anchor_position(position * 1000)
        return self.success_response(f"Seeked to {position}s")

    async def _handle_skip(self, params: SkipParams) -> Dict[str, Any]:
        """Move the playhead by `params.seconds` from where mpv has it
        (MpvAudioSource._skip_by). Past the end of a track, mpv goes on to the
        next entry, whose start re-anchors it."""
        if await self._skip_by(self._session, params.seconds) is None:
            return self.mpv_refused(f"skip {params.seconds:+g}s")
        return self.success_response(f"Skipped {params.seconds:+g}s")

    async def _handle_set_shuffle(self, params: SetShuffleParams) -> Dict[str, Any]:
        """Toggle shuffle on the live queue, reordering ONLY the upcoming tracks so
        the current one keeps playing (gapless, no restart).

        ON shuffles the tracks after the current index; OFF restores their
        pristine order. The played/current head is left as-is either way, and
        mpv's entries after the current one are replaced in place.
        """
        session = self._session
        target = bool(params.shuffle)
        if target == session.shuffle:
            return self.success_response("Shuffle unchanged")

        client = await self.get_navidrome_client()
        if client is None:
            return self.error_response("Music library catalog not ready")

        head = session.queue[: session.index + 1]
        if target:
            tail = session.queue[session.index + 1:]
            random.shuffle(tail)
        else:
            # Pristine order minus the played/current head, consumed *positionally*:
            # a queue can list the same track id twice (an album with a reprise, a
            # playlist built by hand), and dropping it by set membership deletes
            # every later copy the moment the first one has played.
            played = Counter(track.get("id") for track in head)
            tail = []
            for track in session.unshuffled:
                if played.get(track.get("id")):
                    played[track.get("id")] -= 1
                    continue
                tail.append(track)

        # The session follows mpv step by step: a refusal partway leaves a
        # shorter queue that matches mpv's playlist, never entry ids mpv does
        # not hold (the end of the queue would then never be recognized).
        refused = False
        for index in range(len(session.entries) - 1, session.index, -1):
            if not await self._mpv.remove_entry(index):
                refused = True
                break
            del session.entries[index]
            del session.queue[index]
        if not refused:
            for track in tail:
                entry = await self._mpv.loadfile(client.stream_url(track["id"]), mode="append")
                if entry is None:
                    refused = True
                    break
                session.entries.append(entry)
                session.queue.append(track)
            session.shuffle = target
        self._publish()
        if refused:
            self._logger.error("mpv refused part of the reorder; the queue now ends where mpv's does")
            return self.error_response("Failed to reorder queue")
        return self.success_response("Shuffle on" if target else "Shuffle off")

    async def _handle_stop(self) -> Dict[str, Any]:
        """Explicit Stop: the session ends and nothing is kept to reopen."""
        if self._session is not None:
            await self._mpv.stop()
            await self.end_session(EndReason.USER_STOP)
        self._set_resume_point(None)
        self._publish()
        return self.success_response("Playback stopped")

    # =========================================================================
    # THE QUEUE IN MPV
    # =========================================================================

    async def _restore(self, point, *, playing: bool, index: Optional[int] = None) -> bool:
        """Reopen the resume point's queue — at its track and second, or at the
        start of `index`."""
        content = point.content
        tracks = content["queue"]
        at = min(content["queue_index"] if index is None else index, len(tracks) - 1)
        start_s = point.position_ms // 1000 if index is None else 0
        self._logger.info("Reopening the saved queue at track %s, %ss", at + 1, start_s)
        return await self._open_queue(
            tracks, content["queue_unshuffled"] or list(tracks), at,
            content["queue_library_id"], content["shuffle"],
            start_s=start_s, playing=playing,
        )

    async def _open_queue(
        self, tracks: List[Dict[str, Any]], unshuffled: List[Dict[str, Any]], index: int,
        library_id: Optional[int], shuffle: bool, *, start_s: int, playing: bool,
    ) -> bool:
        """Open a session on `tracks` and hand them to mpv: every track appended
        (nothing plays), the one at `index` starting at `start_s`, then that
        entry started — paused unless `playing`. The now-playing is published
        before the load, so the player snaps to it at once."""
        client = await self.get_navidrome_client()
        if client is None:
            return False
        session = LibrarySession(
            phase=Phase.LOADING if playing else Phase.PAUSED, queue=tracks, unshuffled=unshuffled, index=index,
            library_id=library_id, shuffle=shuffle, position=start_s,
            duration=int(tracks[index].get("duration") or 0),
        )
        self.open_session(session)
        self._anchor_position(start_s * 1000)
        self._publish()

        self._logger.info("Loading a queue of %s track(s) at track %s", len(tracks), index + 1)
        async def load() -> bool:
            # Inside the load, after the session is open: a refusal here ends
            # it (LOAD_FAILED stops mpv), never a previous queue left playing
            # with no session to pause it.
            if not await self._mpv_ready() or not await self._set_mpv_pause(not playing):
                return False
            if not await self._mpv.stop():
                return False
            entries: List[int] = []
            for position, track in enumerate(tracks):
                entry = await self._mpv.loadfile(
                    client.stream_url(track["id"]), mode="append",
                    start_s=start_s if position == index and start_s else None,
                )
                if entry is None:
                    return False
                entries.append(entry)
            session.entries = entries
            session.link = self._mpv.link
            return await self._mpv.play_index(index)

        if not await self._attempt(load):
            await self._end_playback(EndReason.LOAD_FAILED, detail="mpv refused the queue")
            return False
        return True

    async def _entry_started(self, session: "LibrarySession", entry: Optional[int]) -> None:
        """mpv started one of the queue's entries: a gapless advance, a jump,
        or the first one. The track changes with it; the play is announced to
        Navidrome once per start."""
        if entry not in session.entries:
            return
        session.started = True
        session.opened = False
        index = session.entries.index(entry)
        if index != session.index:
            self._move_to_track(session, index)
        if session.announced != entry:
            session.announced = entry
            self._scrobble_now_playing(session)

    async def _entry_ended(self, session: "LibrarySession", event: Dict[str, Any]) -> None:
        """An entry ended. A track that failed is skipped by mpv, and said so in
        the journal; the queue ends when its last entry does — played out
        (EOF), or failed (E53: Navidrome down fails every track, and the queue
        used to end as if it had played)."""
        entry = event.get("playlist_entry_id")
        reason = event.get("reason")
        if entry not in session.entries or reason not in ("eof", "error"):
            return
        if reason == "error":
            session.failed_in_a_row += 1
            self._logger.warning(
                "Track %s did not play: %s",
                session.entries.index(entry) + 1, event.get("file_error") or "error",
            )
        else:
            session.failed_in_a_row = 0
        if entry != session.entries[-1]:
            return
        if session.failed_in_a_row:
            await self._end_playback(
                EndReason.STREAM_LOST if session.heard else EndReason.LOAD_FAILED,
                stop_mpv=False, detail=f"{session.failed_in_a_row} track(s) failed to play",
            )
            return
        self._logger.info("Queue finished")
        await self._end_playback(EndReason.EOF, stop_mpv=False)

    def _failure_banner(self, reason: EndReason) -> Optional[str]:
        if reason is EndReason.LOAD_FAILED:
            return SourceErrorReason.PLAYBACK_FAILED
        return super()._failure_banner(reason)

    def _move_to_track(self, session: "LibrarySession", index: int) -> None:
        session.index = index
        session.position = 0
        session.duration = int(session.queue[index].get("duration") or 0)
        self._anchor_position(0)
        self._reset_scrobble(session)

    async def _before_idle_end(self, session: "LibrarySession") -> None:
        await self._sync_position(session)

    async def _sync_position(self, session: Optional["LibrarySession"]) -> None:
        """Read the live playhead into the session (while its file is open)."""
        if isinstance(session, LibrarySession) and session.opened and self._mpv:
            await self._read_playhead(session)

    async def _on_playing_tick(self, session: "LibrarySession") -> None:
        position = await self._read_playhead(session)
        # Listening time for the scrobble threshold: how far the playhead
        # actually moved since the last tick, capped at one tick. Neither half of
        # that cap is decoration — a seek forward jumps the position with nothing
        # heard (so the jump is capped), and a stalled stream leaves the playhead
        # frozen (so a still position credits nothing).
        if position is not None:
            if session.last_tick_position is not None:
                advanced = position - session.last_tick_position
                session.played_seconds += max(0.0, min(advanced, self.MONITOR_TICK_S))
            session.last_tick_position = position
            self._maybe_submit_scrobble(session)

    async def refresh_metadata(self) -> bool:
        """Pull the live playhead from mpv so a (re)connecting client's state
        carries the player's own second."""
        session = self._session
        if not isinstance(session, LibrarySession) or not self._mpv or not self._mpv.is_connected:
            return False
        await self._sync_position(session)
        return True

    # =========================================================================
    # SCROBBLE (Navidrome play history)
    # =========================================================================

    @staticmethod
    def _reset_scrobble(session: "LibrarySession") -> None:
        """Start the accounting over for a new listen.

        The "already scrobbled" flag belongs to a PASS, not to a song id: a queue
        can list the same track twice (an album with a reprise, a playlist built
        by hand), and each pass over it is a play of its own.
        """
        session.played_seconds = 0.0
        session.scrobbled = False
        session.last_tick_position = None

    def _scrobble_now_playing(self, session: "LibrarySession") -> None:
        """Announce the track that just started (``submission=false``).

        Counts nothing — it is what makes Navidrome show the track as currently
        playing. Fire-and-forget on purpose: none of this may wait on Navidrome.
        """
        track = session.queue[session.index]
        if track.get("id"):
            self._bg.spawn(
                self._send_scrobble(track["id"], submission=False),
                label="scrobble-now-playing",
            )

    def _maybe_submit_scrobble(self, session: "LibrarySession") -> None:
        """Submit the play once it has been listened to past the threshold.

        This is the call that writes play_date/play_count, so it is what makes
        the library's ``type=recent`` and ``type=frequent`` lists non-empty.
        Fired once per pass, from the accumulated listening time rather than the
        playhead.
        """
        if session.scrobbled:
            return
        track = session.queue[session.index]
        if not track.get("id"):
            return
        duration = session.duration or int(track.get("duration") or 0)
        if duration < SCROBBLE_MIN_DURATION_S:
            return
        if session.played_seconds < min(duration / 2, SCROBBLE_MAX_THRESHOLD_S):
            return
        session.scrobbled = True
        self._bg.spawn(
            self._send_scrobble(track["id"], submission=True),
            label="scrobble-submission",
        )

    async def _send_scrobble(self, song_id: str, submission: bool) -> None:
        """One scrobble call, swallowed whole on failure.

        Listening history is bookkeeping: a Navidrome that is down, slow or
        refusing must cost a log line and nothing else — never a gap in the
        music.
        """
        try:
            client = await self.get_navidrome_client()
            if client is None:
                return
            if not await client.scrobble(song_id, submission=submission):
                self._logger.warning(
                    "Navidrome refused scrobble (submission=%s) for %s",
                    submission,
                    song_id,
                )
        except Exception as e:
            self._logger.warning(f"Scrobble failed for {song_id}: {e}")

    # =========================================================================
    # METADATA / STATE
    # =========================================================================

    def _cover_url(self, song: Dict[str, Any]) -> Optional[str]:
        """Our cover proxy URL for a song's art (coverArt id, album id fallback)."""
        cover_id = song.get("coverArt") or song.get("albumId")
        return f"/api/music-library/cover/{cover_id}" if cover_id else None

    # The view (docs: "le fil"). A live queue and a saved one read the same:
    # only where the numbers come from differs.

    def availability(self) -> Optional[str]:
        return self._availability

    def _saved_queue(self) -> Optional[Tuple[List[Dict[str, Any]], int]]:
        """The resume point's queue and index while it may still be reopened."""
        point = self._resume_point
        if point is None or not self._resume_fresh():
            return None
        tracks = point.content["queue"]
        return tracks, min(point.content["queue_index"], len(tracks) - 1)

    def _session_fields(self, session: "LibrarySession") -> Dict[str, Any]:
        current = session.queue[session.index]
        return {
            "title": current.get("title"),
            "artist": current.get("artist"),
            "album": current.get("album"),
            "artwork": self._cover_url(current),
            "duration_ms": session.duration * 1000 or None,
        }

    def _resume_view(self) -> Optional[ResumeView]:
        saved = self._saved_queue()
        if saved is None:
            return None
        tracks, index = saved
        current = tracks[index]
        return ResumeView(
            title=current.get("title"), artist=current.get("artist"),
            album=current.get("album"), artwork=self._cover_url(current),
            duration_ms=int(current.get("duration") or 0) * 1000 or None,
            position_ms=self._resume_point.position_ms,
        )

    def _details(self) -> Optional[MusicLibraryDetails]:
        """The whole queue, so the frontend renders it without a round-trip,
        and the ids its navigation opens the current track's album/artist by."""
        session = self._session
        if isinstance(session, LibrarySession):
            tracks, index, shuffle = session.queue, session.index, session.shuffle
        else:
            saved = self._saved_queue()
            if saved is None:
                return None
            tracks, index = saved
            shuffle = self._resume_point.content["shuffle"]
        current = tracks[index]
        return MusicLibraryDetails(
            queue=tracks, queue_index=index, shuffle=shuffle,
            track_id=current.get("id"), album_id=current.get("albumId"),
            artist_id=current.get("artistId"),
        )

    def _controls(self) -> List[str]:
        session = self._session
        if not isinstance(session, LibrarySession):
            return ["resume", "play_index", "stop"] if self._saved_queue() is not None else []
        last = session.index >= len(session.queue) - 1
        controls = ["resume" if session.phase is Phase.PAUSED else "pause"]
        if session.phase is not Phase.LOADING:
            controls += ["seek", "skip"]
        controls += [c for c in ("next", "prev") if not (c == "next" and last)]
        return controls + ["set_shuffle", "play_index", "stop"]

    # =========================================================================
    # NETWORK SHARES (SMB/NFS)
    # =========================================================================

    @property
    def shares(self) -> NetworkShareService:
        """Where the music comes from: the configured SMB/NFS shares and the USB
        volumes mounted beside them. Read by routes.py (see shares.py)."""
        return self._shares

    @property
    def artist_images(self) -> ArtistImageService:
        """Artist photos from Deezer, for the artists Navidrome has no local art
        for. Read by the cover route (see artist_images.py)."""
        return self._artist_images
