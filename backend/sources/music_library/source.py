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
reshuffles the upcoming tracks without interrupting the current one), and so
can repeat (``set_repeat``: the queue or the track, Spotify's three modes),
which mpv carries out itself through its loop options.

The queue is edited while it plays, one flat list as Sonos and MPD keep it:
tracks played next (``insert-next``, after mpv's own current entry) or added
at the end, a track taken out or moved among those to come. The track playing is never touched, each edit is read back from mpv's
playlist, and shuffle off still brings back the original order, the tracks
added included. A queue remembers every storage space its tracks came from:
any one of them leaving ends it.

Resume-on-return: a session ended by a source switch, the idle timeout, a
multiroom toggle, a dead mpv or a failed load leaves its queue, track, second,
shuffle and repeat as the resume point (RESUME_POLICY); the next activation
reopens it paused — or playing, after a toggle that found it playing — however
long the detour lasted, as radio and podcast keep theirs. An explicit Stop, a
queue played out and a storage space that left forget it; it is never persisted.

Where the music comes from is NOT here: the configured SMB/NFS shares and the
USB watcher underneath them live in :mod:`shares.py`, reached as ``source.shares``
— they run for the whole backend lifetime, independent of playback, so a
plugged-in key is indexed even when music_library is not the active source.
"""
import asyncio
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel

from backend.config.constants import NAVIDROME_SERVICE
from backend.core.audio_source import Result
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, PhaseEvent, ReroutePolicy, ResumePolicy,
)
from backend.core.models.audio_wire import MusicLibraryDetails, RepeatMode, ResumeView
from backend.core.models.commands import SetRepeatParams, SetShuffleParams, SkipParams
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
    AddToQueueParams,
    MoveInQueueParams,
    PlayContextParams,
    PlayIndexParams,
    RemoveFromQueueParams,
    SeekParams,
)
from backend.sources.music_library.artist_images import ArtistImageService
from backend.sources.music_library.navidrome_client import NavidromeClient, NavidromeUnreachable
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

# What `loop-file` is set to while the track repeats: mpv's ceiling for the
# option (10001 is refused, measured on 0.40). Not `inf`, because a finite
# count is the only way mpv says it looped: each return to the start of the
# file takes one off `remaining-file-loops` and announces it, while under
# `inf` the property sits at -1 and a loop looks like any seek to 0. At one
# loop per pass this is 10 000 passes of the track, then the queue moves on.
TRACK_LOOPS = 10000

# Scrobble threshold — the Last.fm rule Navidrome implements: a play counts once
# it has been listened to for half the track or four minutes, whichever comes
# first, and a track shorter than 30 s never counts at all. This submission is
# the ONLY thing that feeds play_date/play_count, which is what getAlbumList2
# type=recent and type=frequent are built from (fetching `stream` counts for
# nothing — OpenSubsonic forbids servers from treating it as a play).
SCROBBLE_MIN_DURATION_S = 30
SCROBBLE_MAX_THRESHOLD_S = 240


def _consumed(head: List[Dict[str, Any]], pristine: List[Dict[str, Any]]) -> List[bool]:
    """Which tracks of the original order the played/current head accounts for.

    Consumed *positionally*, one copy per play: a queue can list the same
    track id twice (an album with a reprise, a playlist built by hand), and
    dropping it by set membership deletes every later copy the moment the
    first one has played.
    """
    played = Counter(track.get("id") for track in head)
    flags = []
    for track in pristine:
        used = played.get(track.get("id"), 0) > 0
        if used:
            played[track.get("id")] -= 1
        flags.append(used)
    return flags


@dataclass(eq=False)
class LibrarySession(MpvSession):
    """One queue playing, from the play to a named end. `entries` are mpv's
    playlist entry ids, by queue position: what start-file and end-file name,
    and so how a gapless advance or the end of the queue is recognized."""
    queue: List[Dict[str, Any]] = field(default_factory=list)
    unshuffled: List[Dict[str, Any]] = field(default_factory=list)
    index: int = 0
    # The storage spaces the queue's tracks were browsed from: any one of them
    # leaving ends it. Empty for a queue built unscoped.
    library_ids: FrozenSet[int] = frozenset()
    context_id: Optional[str] = None
    shuffle: bool = False
    # The mode mpv's two loop options hold (see _handle_set_repeat).
    repeat: RepeatMode = "off"
    # The last `remaining-file-loops` mpv announced: a pass is a drop of one.
    file_loops: int = TRACK_LOOPS
    entries: List[Optional[int]] = field(default_factory=list)
    # The last entry ended: mpv either starts the queue over or goes idle,
    # and only what it announces next says which.
    played_out: bool = False
    announced: Optional[int] = None     # the entry whose start went to Navidrome
    failed_in_a_row: int = 0
    # Where the running series of failed tracks began, (index, seconds): mpv
    # skips a track it cannot open, so the queue moves on to tracks never heard.
    failed_from: Optional[Tuple[int, int]] = None
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
        # The one initialization (see initialize), awaited by every caller.
        self._init_task: Optional[asyncio.Task] = None

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
        what they return is post-filtered against this set. Raises
        NavidromeUnreachable when the inventory walk stopped short (see
        :meth:`get_merged_albums`'s ``strict``).
        """
        return await self._scope_album_ids(await self.browse_scope(), strict=True)

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

    async def get_merged_albums(
        self, scope: List[int], *, strict: bool = False
    ) -> List[Dict[str, Any]]:
        """A browse scope's catalog, alphabetical, multi-disc sets collapsed.

        The album grid pages over this list (see routes.get_albums) so a split
        "… CD 1"/"CD 2" release is merged even when the pair would straddle a page
        boundary. ``scope`` is the storage spaces to read (see
        :meth:`browse_scope`). Cached with a short TTL; an explicit rescan or
        share change calls :meth:`invalidate_album_cache`. Returns [] until the
        catalog is reachable, and caches only a walk that ran to the end — a
        not-yet-ready daemon and a page that failed mid-catalog both retry on the
        next call rather than serving what they got for the whole TTL.

        ``strict`` is for the descent filter (:meth:`mounted_album_ids`), which
        sorts an item's tracks by this list instead of drawing it: there a walk
        that stopped short — a page unanswered, or answered with an error the
        client has logged — raises NavidromeUnreachable, a 503 at the route like
        a failed page of any other album list. Read as the catalog, a partial
        inventory drops every track past the break as unmounted: an album opened
        with no songs, a two-CD set with one disc, nothing said. A listing (this
        grid, the playlist pane) keeps the partial answer.
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
                if strict:
                    raise NavidromeUnreachable("getAlbumList2")
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

    async def _scope_album_ids(self, scope: List[int], *, strict: bool = False) -> set:
        """Every album id in a browse scope, merged sets expanded.

        The grid's merged catalog is reused rather than re-fetched, so this
        usually costs nothing — but a merged multi-disc album carries a
        synthetic id, and a track points at the member album, so the members are
        what has to be in the set. ``strict`` as in :meth:`get_merged_albums`.
        """
        ids: set = set()
        for album in await self.get_merged_albums(scope, strict=strict):
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
        One that took tracks from several spaces ends when any of them leaves:
        mpv would skip that space's tracks in silence.

        The availability they imply is decided here too, in the actor: no
        mounted storage carrying a library, else a catalog that does not answer.
        """
        mounted = {entry["library_id"] for entry in entries if entry["mounted"]}

        def gone(library_ids) -> bool:
            return any(library_id not in mounted for library_id in library_ids)

        session = self._session
        if isinstance(session, LibrarySession) and gone(session.library_ids):
            self._logger.info(
                "Storage space for libraries %s is gone — ending the session",
                sorted(session.library_ids),
            )
            await self._end_playback(EndReason.STORAGE_GONE)
        else:
            point = self._resume_point
            if point is not None and gone(point.content["queue_library_ids"]):
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
        """Run the one initialization, or wait for the one already in flight.

        The IR remote's MENU listens before the boot's init is done, and a
        selection reaching this source calls initialize() again while
        `is_initialized` is false, under the transition timeout. A second run
        started a second udev observer and a second set of share watchers, for
        the backend's life; the shield keeps that timeout from cancelling the
        boot's run. A run that failed is not shared: the next caller tries
        again.
        """
        if self._init_task is None or (self._init_task.done() and not self.is_initialized):
            self._init_task = asyncio.create_task(
                self._initialize_once(), name="source.music_library.initialize"
            )
        return await asyncio.shield(self._init_task)

    async def _initialize_once(self) -> bool:
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
        index, position = session.index, session.position
        if session.failed_from is not None and not session.opened:
            # The tracks mpv skipped were never heard: come back to the first
            # of them, not to the last one it tried (E63).
            index, position = session.failed_from
        track = session.queue[index]
        return track.get("id") or "", position * 1000, {
            "queue": list(session.queue),
            "queue_unshuffled": list(session.unshuffled),
            "queue_index": index,
            # Without them the restored queue is attributed to no space, and
            # a key leaving would not end it.
            "queue_library_ids": sorted(session.library_ids),
            "queue_context_id": session.context_id,
            "shuffle": session.shuffle,
            # Kept like shuffle: both are how this queue was being listened
            # to, and a looped album that comes back unlooped is a change
            # nobody asked for.
            "repeat": session.repeat,
        }

    async def _listen_to_mpv(self) -> None:
        """The base's phase properties, plus the two that say where the queue
        goes after a track: idle-active (it ended) and remaining-file-loops
        (the track started over)."""
        await super()._listen_to_mpv()
        await self._mpv.observe("idle-active")
        await self._mpv.observe("remaining-file-loops")

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
            if point is not None and self.RESUME_POLICY.restore_on_start:
                await self._restore(point, playing=False)
                return True
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
        "set_repeat": SetRepeatParams,
        "stop": None,
        "add_to_queue": AddToQueueParams,
        "remove_from_queue": RemoveFromQueueParams,
        "move_in_queue": MoveInQueueParams,
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
        "set_repeat": CommandScope.SESSION,
        "stop": CommandScope.RESUME,
        "add_to_queue": CommandScope.SESSION,
        "remove_from_queue": CommandScope.SESSION,
        "move_in_queue": CommandScope.SESSION,
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
        if cmd == "set_repeat":
            return await self._handle_set_repeat(params)
        if cmd == "stop":
            return await self._handle_stop()
        if cmd == "add_to_queue":
            return await self._handle_add_to_queue(params)
        if cmd == "remove_from_queue":
            return await self._handle_remove_from_queue(params)
        if cmd == "move_in_queue":
            return await self._handle_move_in_queue(params)
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_play_context(self, params: PlayContextParams) -> Dict[str, Any]:
        """Build the mpv playlist from a context and start playing at start_index."""
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
        # It repeats nothing until asked, whatever the queue it replaces did:
        # off is the default, as shuffle's is.
        await self.end_session(EndReason.USER_STOP)
        self._set_resume_point(None)
        library_ids = frozenset() if params.library_id is None else frozenset({params.library_id})
        if not await self._open_queue(
            tracks, original_order, start_index, library_ids, params.context_id,
            params.shuffle, "off", start_s=0, playing=True,
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
        """The next track; past the last one, the first again when the queue
        repeats (as Spotify does), else nothing."""
        session = self._session
        if session.index >= len(session.queue) - 1:
            if session.repeat == "off":
                return self.success_response("Already at end of queue")
            return await self._switch_to_index(session, 0)
        return await self._switch_to_index(session, session.index + 1)

    async def _handle_prev(self) -> Dict[str, Any]:
        session = self._session
        # The live playhead, not the last tick's: up to a second stale.
        await self._sync_position(session)
        if session.position >= PREV_RESTART_THRESHOLD_S or session.index == 0:
            if not (await self._mpv.seek(0) and await self._set_mpv_pause(False)):
                return self.mpv_refused("restart track")
            self._track_again(session)
            return self.success_response("Restarted track")
        return await self._switch_to_index(session, session.index - 1)

    async def _switch_to_index(self, session: "LibrarySession", index: int) -> Dict[str, Any]:
        """Play entry `index` of the live queue (play_index / next / prev)."""
        if not (await self._mpv.play_index(index) and await self._set_mpv_pause(False)):
            return self.mpv_refused(f"switch to track {index + 1}")
        self._move_to_track(session, index)
        session.opened = False
        # A jump while the last track plays out: mpv was going idle, and the
        # end that announces is no longer the queue's.
        session.played_out = False
        # A track the listener picked: the point to come back to from here.
        session.failed_in_a_row, session.failed_from = 0, None
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
        mpv's entries after the current one are replaced in place, the queue
        then read back from mpv (_adopt_mpv_playlist).
        """
        session = self._session
        target = bool(params.shuffle)
        if target == session.shuffle:
            return self.success_response("Shuffle unchanged")

        client = await self.get_navidrome_client()
        if client is None:
            return self.error_response("Music library catalog not ready")

        by_entry = dict(zip(session.entries, session.queue))
        keep, refused = await self._drop_upcoming(session)

        head = session.queue[: keep + 1]
        if target:
            tail = session.queue[keep + 1:]
            random.shuffle(tail)
        else:
            tail = [
                track for track, played in zip(session.unshuffled, _consumed(head, session.unshuffled))
                if not played
            ]

        # An append whose answer is lost may be in mpv all the same; the URL
        # sent is the only thing that names it (each one carries a fresh salt).
        lost: Optional[Tuple[str, Dict[str, Any]]] = None
        if not refused:
            for track in tail:
                stream = client.stream_url(track["id"])
                entry = await self._mpv.loadfile(stream, mode="append")
                if entry is None:
                    lost = (stream, track)
                    break
                by_entry[entry] = track

        if not await self._adopt_mpv_playlist(session, by_entry, lost):
            return self.error_response("Failed to reorder queue")
        if not await self._restart_if_idle(session, keep + 1):
            return self.error_response("Failed to reorder queue")
        if session.queue == head + tail:
            session.shuffle = target
        self._publish()
        if session.shuffle != target:
            self._logger.error("mpv did not take the whole reorder; the queue now ends where mpv's does")
            return self.error_response("Failed to reorder queue")
        return self.success_response("Shuffle on" if target else "Shuffle off")

    async def _handle_add_to_queue(self, params: AddToQueueParams) -> Dict[str, Any]:
        """Songs right after the track playing, or at the end of the queue.

        `next` is mpv's `insert-next`, sent in reverse so the block keeps its
        order: mpv places it after its own current entry, which is already
        the next track in the last ~0.35 s of one (measured) — a position
        computed from the session's index would land one track late there.
        """
        session = self._session
        client = await self.get_navidrome_client()
        if client is None:
            return self.error_response("Music library catalog not ready")

        by_entry = dict(zip(session.entries, session.queue))
        known = set(session.entries)
        head = session.queue[: session.index + 1]
        tracks = list(params.tracks)
        mode = "append" if params.position == "end" else "insert-next"
        lost: Optional[Tuple[str, Dict[str, Any]]] = None
        for track in tracks if params.position == "end" else reversed(tracks):
            stream = client.stream_url(track["id"])
            entry = await self._mpv.loadfile(stream, mode=mode)
            if entry is None:
                lost = (stream, track)
                break
            by_entry[entry] = track

        if not await self._adopt_mpv_playlist(session, by_entry, lost):
            return self.error_response("Failed to add to the queue")
        added = [index for index, entry in enumerate(session.entries) if entry not in known]
        if params.position == "next" and len(added) > 1 and await self._mpv.get_property("idle-active"):
            if not await self._order_played_out_block(session, added):
                return self.error_response("Failed to add to the queue")
            added = [index for index, entry in enumerate(session.entries) if entry not in known]
        if added and not await self._restart_if_idle(session, added[0]):
            return self.error_response("Failed to add to the queue")

        if not session.shuffle:
            session.unshuffled = list(session.queue)
        else:
            new = [session.queue[index] for index in added]
            if params.position == "end":
                session.unshuffled = session.unshuffled + new
            else:
                # Ahead of the rest of the original order, so shuffle off
                # still plays them next — and behind every copy already
                # played, which the head would otherwise count against a
                # copy just played next (one of the track playing, say).
                consumed = _consumed(head, session.unshuffled)
                session.unshuffled = (
                    [track for track, used in zip(session.unshuffled, consumed) if used]
                    + new
                    + [track for track, used in zip(session.unshuffled, consumed) if not used]
                )
        if params.library_id is not None and added:
            session.library_ids = session.library_ids | {params.library_id}
        self._publish()
        if len(added) != len(tracks):
            self._logger.error("mpv took %s of %s track(s) added to the queue", len(added), len(tracks))
            return self.error_response("Failed to add to the queue")
        return self.success_response(f"Added {len(tracks)} track(s)")

    async def _order_played_out_block(self, session: "LibrarySession", added: List[int]) -> bool:
        """Put back in order a block played next into a played-out mpv.

        With no current entry, mpv puts an `insert-next` at the end (measured
        on 0.40), so the block sent in reverse sits reversed there. Entry ids
        grow with each load, so the block's order is theirs, descending.
        Nothing plays yet: the moves cut nothing. False, the session ended."""
        by_entry = dict(zip(session.entries, session.queue))
        entries = list(session.entries)
        wanted = sorted((entries[index] for index in added), reverse=True)
        for slot, entry in zip(added, wanted):
            at = entries.index(entry)
            if at == slot:
                continue
            if not await self._mpv.move_entry(at, slot):
                break
            entries.insert(slot, entries.pop(at))
        return await self._adopt_mpv_playlist(session, by_entry, None)

    async def _handle_remove_from_queue(self, params: RemoveFromQueueParams) -> Dict[str, Any]:
        """Take a track out of the queue: one to come, or one already played."""
        session = self._session
        index = params.index
        if index >= len(session.queue) or session.queue[index].get("id") != params.track_id:
            return self.error_response("Queue moved: nothing removed")
        if index == session.index:
            return self.error_response("The track playing cannot be removed")

        by_entry = dict(zip(session.entries, session.queue))
        entry = session.entries[index]
        consumed = _consumed(session.queue[: session.index + 1], session.unshuffled)
        played = index < session.index
        await self._mpv.remove_entry(index)
        # Read back even when refused: an answer that missed its deadline may
        # still have been carried out.
        if not await self._adopt_mpv_playlist(session, by_entry, None):
            return self.error_response("Failed to remove from the queue")
        if entry in session.entries:
            self._publish()
            return self.mpv_refused(f"remove track {index + 1}")

        if not session.shuffle:
            session.unshuffled = list(session.queue)
        else:
            # The copy the original order holds for this track: among those
            # already consumed by the head when it was played, else the
            # first still to come — a queue can list one song twice.
            at = next((
                position for position, (track, used) in enumerate(zip(session.unshuffled, consumed))
                if used == played and track.get("id") == params.track_id
            ), None)
            if at is not None:
                session.unshuffled = session.unshuffled[:at] + session.unshuffled[at + 1:]
        if session.failed_from is not None:
            failed_index, seconds = session.failed_from
            if failed_index == index:
                session.failed_from = None
            elif failed_index > index:
                session.failed_from = (failed_index - 1, seconds)
        self._publish()
        return self.success_response(f"Removed track {index + 1}")

    async def _handle_move_in_queue(self, params: MoveInQueueParams) -> Dict[str, Any]:
        """Move a track to come to another place among the tracks to come.

        Shuffled, this reorders the shuffled order alone: shuffle off still
        brings back the original one."""
        session = self._session
        source, to = params.from_index, params.to_index
        if source >= len(session.queue) or session.queue[source].get("id") != params.track_id:
            return self.error_response("Queue moved: nothing moved")
        if not (session.index < source and session.index < to < len(session.queue)):
            return self.error_response("Only the tracks to come move, among themselves")
        if source == to:
            return self.success_response("Track unmoved")

        by_entry = dict(zip(session.entries, session.queue))
        entry = session.entries[source]
        await self._mpv.move_entry(source, to)
        if not await self._adopt_mpv_playlist(session, by_entry, None):
            return self.error_response("Failed to move in the queue")
        if not session.shuffle:
            session.unshuffled = list(session.queue)
        self._publish()
        if session.entries.index(entry) != to:
            return self.mpv_refused(f"move track {source + 1}")
        return self.success_response(f"Moved track {source + 1} to {to + 1}")

    async def _drop_upcoming(self, session: "LibrarySession") -> Tuple[int, bool]:
        """Take every entry after the current one out of mpv, from the end.

        Returns the last index kept — past the session's when mpv moved on
        to the next entry while the later ones went, which is part of the
        head then, not of the tail — and whether mpv refused a removal."""
        keep = session.index
        for index in range(len(session.entries) - 1, keep, -1):
            if index == keep + 1:
                playing = await self._mpv.get_property("playlist-playing-pos")
                if isinstance(playing, int) and playing > keep:
                    return playing, False
            if not await self._mpv.remove_entry(index):
                return keep, True
        return keep, False

    async def _adopt_mpv_playlist(
        self, session: "LibrarySession", by_entry: Dict[Optional[int], Dict[str, Any]],
        lost: Optional[Tuple[str, Dict[str, Any]]],
    ) -> bool:
        """Make the session's queue mpv's playlist, read back after an edit.

        mpv moves on by itself while an edit runs (a gapless advance,
        announced behind the command), and a command whose answer misses its
        deadline may still have been carried out. So the queue is never
        rewritten from the commands sent: it is rebuilt from mpv's own
        playlist once they are done, and handed to the session in one step — a
        stop that cuts the edit keeps the whole queue to come back to.

        `by_entry` names the track of every entry id this may meet; `lost` is
        a load whose answer never came, found by its URL. The track playing
        keeps playing, so the index follows its entry without the reset of a
        track change. False, the session ended: an entry nobody loaded, or the
        current one gone, is a queue that is not mpv's, whose end would never
        be recognized."""
        current = session.entries[session.index]
        playlist = await self._mpv.get_property("playlist")
        entries, queue = [], []
        for item in playlist if isinstance(playlist, list) else []:
            track = by_entry.get(item.get("id"))
            if track is None and lost is not None and item.get("filename") == lost[0]:
                track, lost = lost[1], None
            entries.append(item.get("id"))
            queue.append(track)
        if None in queue or current not in entries:
            await self._end_playback(
                EndReason.STREAM_LOST, detail="mpv's playlist unreadable after a queue edit"
            )
            return False
        session.entries, session.queue = entries, queue
        session.index = entries.index(current)
        return True

    async def _restart_if_idle(self, session: "LibrarySession", first_new: int) -> bool:
        """Start entry `first_new` if mpv stopped with it still to play.

        The entry mpv had just reached was taken out with nothing after it,
        or its track ended before the new entries were in — and neither an
        append nor an insertion starts anything: the session would play on
        over a silent mpv. One read is enough: mpv answers no command while
        it plays out its last sound (measured on 0.40), so it reads idle by
        then. False, the session ended."""
        if len(session.queue) <= first_new or not await self._mpv.get_property("idle-active"):
            return True
        if not await self._mpv.play_index(first_new):
            await self._end_playback(EndReason.STREAM_LOST, detail="mpv stopped by a queue edit")
            return False
        self._move_to_track(session, first_new)
        session.opened = session.played_out = False
        self._sync_phase(session, PhaseEvent.TRACK_CHANGE)
        return True

    async def _handle_set_repeat(self, params: SetRepeatParams) -> Dict[str, Any]:
        """Repeat the queue (`context`), the track, or neither.

        mpv does the looping: `loop-playlist` starts the queue over after its
        last entry, `loop-file` takes the track back to its start, both with
        no gap and no command at the seam. `track` holds both, as Spotify's
        track mode sits on its context mode. The track loop is dropped before
        the queue's and taken after it, so each step is itself one of the three
        modes: a refusal partway leaves the session on the one mpv holds.
        """
        session = self._session
        target = params.mode
        if target == session.repeat:
            return self.success_response("Repeat unchanged")
        refused = False
        if target != "track" and session.repeat == "track":
            refused = not await self._mpv.set_property("loop-file", "no")
            if not refused:
                session.repeat = "context"
        if not refused and (target == "off") != (session.repeat == "off"):
            refused = not await self._mpv.set_property(
                "loop-playlist", "no" if target == "off" else "inf"
            )
            if not refused:
                session.repeat = "off" if target == "off" else "context"
        if not refused and target == "track":
            refused = not await self._mpv.set_property("loop-file", TRACK_LOOPS)
            if not refused:
                session.repeat = "track"
        self._publish()
        if refused:
            return self.mpv_refused(f"repeat {target}")
        return self.success_response(f"Repeat {target}")

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
            frozenset(content["queue_library_ids"]), content["queue_context_id"],
            content["shuffle"], content["repeat"], start_s=start_s, playing=playing,
        )

    async def _open_queue(
        self, tracks: List[Dict[str, Any]], unshuffled: List[Dict[str, Any]], index: int,
        library_ids: FrozenSet[int], context_id: Optional[str], shuffle: bool, repeat: RepeatMode,
        *, start_s: int, playing: bool,
    ) -> bool:
        """Open a session on `tracks` and hand them to mpv: every track appended
        (nothing plays), the one at `index` starting at `start_s`, then that
        entry started — paused unless `playing`. The now-playing is published
        before the load, so the player snaps to it at once.

        Both loop options are set on every load: mpv keeps them across a
        `stop` (measured), so the previous queue's would otherwise hold."""
        client = await self.get_navidrome_client()
        if client is None:
            return False
        session = LibrarySession(
            phase=Phase.LOADING if playing else Phase.PAUSED, queue=tracks, unshuffled=unshuffled, index=index,
            library_ids=library_ids, context_id=context_id, shuffle=shuffle, repeat=repeat, position=start_s,
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
            if not (
                await self._mpv.set_property("loop-file", TRACK_LOOPS if repeat == "track" else "no")
                and await self._mpv.set_property("loop-playlist", "no" if repeat == "off" else "inf")
            ):
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
        the first one, or the queue starting over (repeat). The track changes
        with it; the play is announced to Navidrome once per start — after the
        last entry, even onto the same one: a queue of one track starts over by
        reloading it."""
        if entry not in session.entries:
            return
        session.started = True
        session.opened = False
        again, session.played_out = session.played_out, False
        index = session.entries.index(entry)
        if index != session.index or again:
            self._move_to_track(session, index)
        if session.announced != entry or again:
            session.announced = entry
            self._scrobble_now_playing(session)

    async def _entry_ended(self, session: "LibrarySession", event: Dict[str, Any]) -> None:
        """An entry ended. A track that failed is skipped by mpv, and said so in
        the journal.

        After the last entry, mpv either starts the queue over (repeat) or
        drains the sound it still holds and goes idle, and only what it
        announces next says which: the queue ends on `idle-active`
        (_property_changed), never here — played out (EOF), or failed (E53:
        Navidrome down fails every track, and the queue used to end as if it
        had played). Deciding from the repeat mode instead would be deciding
        from a command: a mode changed as the last track ended would leave
        mpv playing with no session, or a session over a silent mpv.

        A repeating queue goes round again over a failed track. mpv gives up
        once every entry has failed — after its last entry, it goes idle
        instead of starting over (measured: a second pass, each entry retried)
        — so a queue where nothing plays ends the same way, as failed."""
        entry = event.get("playlist_entry_id")
        reason = event.get("reason")
        if entry not in session.entries or reason not in ("eof", "error"):
            return
        if reason == "error":
            if session.opened:
                # This track was heard before it broke: the run starts over here.
                session.failed_in_a_row = 0
            if not session.failed_in_a_row:
                index = session.entries.index(entry)
                session.failed_from = (
                    index, session.position if index == session.index else 0
                )
            session.failed_in_a_row += 1
            self._logger.warning(
                "Track %s did not play: %s",
                session.entries.index(entry) + 1, event.get("file_error") or "error",
            )
        else:
            session.failed_in_a_row = 0
            session.failed_from = None
        if entry == session.entries[-1]:
            session.played_out = True

    async def _property_changed(self, session: "LibrarySession", name: Optional[str], value: Any) -> None:
        if name == "idle-active" and value and session.played_out:
            if session.failed_in_a_row:
                await self._end_playback(
                    EndReason.STREAM_LOST if session.heard else EndReason.LOAD_FAILED,
                    stop_mpv=False, detail=f"{session.failed_in_a_row} track(s) failed to play",
                )
                return
            self._logger.info("Queue finished")
            await self._end_playback(EndReason.EOF, stop_mpv=False)
        elif name == "remaining-file-loops" and isinstance(value, int):
            # One loop fewer: mpv took the track back to its start. Only a drop
            # of exactly one is a pass — setting the option announces
            # TRACK_LOOPS, dropping it 0, and either can be read late, after
            # quick presses have brought the mode back to `track`.
            previous, session.file_loops = session.file_loops, value
            if session.repeat == "track" and value == previous - 1:
                self._track_again(session)

    def _track_again(self, session: "LibrarySession") -> None:
        """The track playing starts over (prev's restart, a repeat pass): the
        playhead back to 0, and a new pass, which Navidrome counts as a play
        of its own."""
        session.position = 0
        self._anchor_position(0)
        self._reset_scrobble(session)
        self._scrobble_now_playing(session)

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
        """The resume point's queue and index."""
        point = self._resume_point
        if point is None:
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
            tracks, index = session.queue, session.index
            shuffle, repeat = session.shuffle, session.repeat
            context_id = session.context_id
        else:
            saved = self._saved_queue()
            if saved is None:
                return None
            tracks, index = saved
            content = self._resume_point.content
            shuffle, repeat = content["shuffle"], content["repeat"]
            context_id = content["queue_context_id"]
        current = tracks[index]
        return MusicLibraryDetails(
            queue=tracks, queue_index=index, shuffle=shuffle, repeat=repeat,
            track_id=current.get("id"), album_id=current.get("albumId"),
            artist_id=current.get("artistId"), context_id=context_id,
        )

    def _controls(self) -> List[str]:
        session = self._session
        if not isinstance(session, LibrarySession):
            return ["resume", "play_index", "stop"] if self._saved_queue() is not None else []
        # Past the last track, `next` goes back to the first while the queue repeats.
        last = session.index >= len(session.queue) - 1 and session.repeat == "off"
        controls = ["resume" if session.phase is Phase.PAUSED else "pause"]
        if session.phase is not Phase.LOADING:
            controls += ["seek", "skip"]
        controls += [c for c in ("next", "prev") if not (c == "next" and last)]
        controls += ["set_shuffle", "set_repeat", "play_index", "stop", "add_to_queue"]
        # The track playing is never removed, and only the tracks to come move.
        upcoming = len(session.queue) - 1 - session.index
        if len(session.queue) > 1:
            controls.append("remove_from_queue")
        if upcoming > 1:
            controls.append("move_in_queue")
        return controls

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
