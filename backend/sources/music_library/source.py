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

Resume-on-return (P3-12): when playback stops because the user switched to
another source or the idle auto-stop fired, the live session (queue / track /
position) is snapshotted in memory; the next activation restores it PAUSED so a
tap on play continues where it left off. An explicit Stop or a naturally-finished
queue forgets it, and it is deliberately not persisted (a reboot starts fresh).

Where the music comes from is NOT here: the configured SMB/NFS shares and the
USB watcher underneath them live in :mod:`shares.py`, reached as ``source.shares``
— they run for the whole backend lifetime, independent of playback, so a
plugged-in key is indexed even when music_library is not the active source.
"""
import asyncio
import random
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from backend.core.models.audio_state import SourceState
from backend.core.models.source_metadata import PlaybackMetadata
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
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

# Bounded catch-up schedule (seconds between attempts) for network shares whose
# NAS was still offline when the backend booted — a NAS often boots slower than
# the Pi. ~1.75 min total, then we give up; not an ongoing reconnection loop.
_SHARE_REMOUNT_RETRY_DELAYS_S = (15, 30, 60)


class MusicLibrarySource(MpvAudioSource):
    """Music Library source (Family C): UI-driven gapless queue playback over a
    Navidrome-indexed local library, with the USB storage layer (P1-4) live."""

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
            self.get_navidrome_client, self.invalidate_album_cache
        )
        # Navidrome Subsonic client for the /api/music-library/* browse routes,
        # for building stream URLs at play time, and for the StorageManager's
        # post-mount rescans. Built lazily (the cred file only exists once the
        # daemon has provisioned its service account) and shared by all three —
        # routes read the catalog even while music_library is not active.
        self._navidrome: Optional[NavidromeClient] = None
        # Merged (multi-disc) album catalog, cached for the alphabetical grid —
        # one entry per storage space (library id → (built_at, albums)), since
        # the grid asks for one library at a time.
        self._album_cache: Dict[Optional[int], Tuple[float, List[Dict[str, Any]]]] = {}
        # playlist id → its first track's album id, for placing a playlist Milō
        # did not create in a storage space (see playlists_in_storage).
        self._playlist_album: Dict[str, Optional[str]] = {}

        # Playback / queue state (reset on stop). The queue holds the Subsonic
        # song dicts verbatim so it can be echoed to the frontend as-is.
        self._queue: List[Dict[str, Any]] = []
        self._queue_index: int = 0
        self._position: int = 0  # seconds into the current track
        self._duration: int = 0  # seconds
        self._shuffle: bool = False
        # The queue in its pristine (pre-shuffle) order, captured at play time so
        # toggling shuffle OFF can restore the original run of upcoming tracks.
        self._queue_unshuffled: List[Dict[str, Any]] = []
        # Guards the monitor tick while a load / track-switch is mid-flight (mpv
        # briefly reports idle-active or a stale playlist-pos during the change).
        self._loading: bool = False
        # Saved session for resume-on-return (P3-12): {queue, queue_unshuffled,
        # queue_index, position, shuffle}. Captured when playback stops via a
        # source switch or the idle auto-stop; restored PAUSED on the next
        # activation. In-memory only (a reboot forgets it), and deliberately NOT
        # reset by _reset_playback_state so it survives the stop→start cycle.
        self._resume: Optional[Dict[str, Any]] = None

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

    async def get_merged_albums(
        self, library_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """One storage space's catalog, alphabetical, multi-disc sets collapsed.

        The album grid pages over this list (see routes.get_albums) so a split
        "… CD 1"/"CD 2" release is merged even when the pair would straddle a page
        boundary. ``library_id`` scopes it to one storage space (None = every
        one). Cached with a short TTL; an explicit rescan or share change calls
        :meth:`invalidate_album_cache`. Returns [] until the catalog is reachable
        (never caches an empty result — a not-yet-ready daemon retries next call).
        """
        now = asyncio.get_event_loop().time()
        cached = self._album_cache.get(library_id)
        if cached is not None and now - cached[0] < ALBUM_CACHE_TTL_S:
            return cached[1]
        client = await self.get_navidrome_client()
        if client is None:
            return []
        albums: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = await client.get_album_list(
                list_type="alphabeticalByName",
                size=_ALBUM_PAGE,
                offset=offset,
                music_folder_id=library_id,
            )
            albums.extend(page)
            if len(page) < _ALBUM_PAGE:
                break
            offset += _ALBUM_PAGE
        merged = merge_albums(albums)
        if albums:
            self._album_cache[library_id] = (now, merged)
        return merged

    async def get_library_genres(self, library_id: int) -> List[Dict[str, Any]]:
        """The genres present in ONE storage space, in the getGenres shape.

        Navidrome accepts ``musicFolderId`` on getAlbumList2, getArtists,
        search3 and getSongsByGenre — but **not** on getGenres, which answers
        with every genre in the catalog whatever is asked. Left as-is, the genre
        list would offer a genre that belongs to another storage space and open
        an empty view, since the drill-down *is* scoped.

        So it is derived from the storage space's own album catalog, which is
        already cached for the album grid. ``songCount`` is the sum of the
        matching albums' track counts: exact for the usual single-genre album,
        an over-count for an album whose tracks disagree, and never wrong about
        *which* genres exist — the part a tap depends on.
        """
        totals: Dict[str, List[int]] = {}
        for album in await self.get_merged_albums(library_id):
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

    async def playlists_in_storage(
        self, playlists: List[Dict[str, Any]], library_id: int
    ) -> List[Dict[str, Any]]:
        """Keep the playlists that belong to ONE storage space.

        Navidrome's playlists are catalog-wide — ``getPlaylists`` accepts
        ``musicFolderId`` and ignores it — but a playlist mixing a NAS and a USB
        key is exactly what the storage filter exists to prevent, so membership
        is decided here, two ways:

        - **Created in Milō** → the storage space it was created in was recorded
          (``shares.playlist_storages``), and that is the answer. It is the only
          one that works for an *empty* playlist, which has no content to judge.
        - **Anything else** — Navidrome auto-imports the ``.m3u`` files it finds,
          so a music key brings its own playlists — → its first track's album is
          looked up in this storage's catalog. One extra call per unknown
          playlist, memoised until the next rescan.

        A playlist that is both unrecorded and empty is shown everywhere: there
        is nothing to place it by, and hiding it would make it unreachable. A
        record pointing at a storage space that no longer exists — the share it
        was created in was removed — is treated as no record at all, for exactly
        the same reason: it would otherwise match nothing and vanish from every
        storage space while still existing in Navidrome.
        """
        recorded = await self._shares.playlist_storages()
        entries = await self._shares.storages()
        storage_id = next(
            (e["id"] for e in entries if e["library_id"] == library_id), None
        )
        live_storages = {entry["id"] for entry in entries}
        album_ids = await self._library_album_ids(library_id)

        kept: List[Dict[str, Any]] = []
        undecided: List[Dict[str, Any]] = []
        for playlist in playlists:
            known = recorded.get(playlist.get("id"))
            if known in live_storages:
                if known == storage_id:
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

    async def _library_album_ids(self, library_id: int) -> set:
        """Every album id in one storage space, merged sets expanded.

        The grid's merged catalog is reused rather than re-fetched, so this
        usually costs nothing — but a merged multi-disc album carries a
        synthetic id, and a track points at the member album, so the members are
        what has to be in the set.
        """
        ids: set = set()
        for album in await self.get_merged_albums(library_id):
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
        """Drop every storage space's merged-album cache so the next grid load
        rebuilds it (called after an explicit rescan or a share add/update/remove).

        The playlist→album memo goes with it: both answer "what is in this
        storage space", and a rescan is exactly when that changes.
        """
        self._album_cache.clear()
        self._playlist_album.clear()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def initialize(self) -> bool:
        """Bring up the music origins (shares + USB watcher), then base init."""
        await self._shares.initialize()
        return await super().initialize()

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._queue = []
        self._queue_unshuffled = []
        self._queue_index = 0
        self._position = 0
        self._duration = 0
        self._shuffle = False
        self._loading = False

    async def _do_start(self) -> bool:
        """Start the mpv service, connect IPC, and idle on the WAITING placeholder
        until the user plays a context."""
        try:
            if not await self._start_service_and_wait():
                return False

            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.error("Failed to connect to mpv IPC")
                return False

            self._reset_playback_state()
            await self._load_auto_stop_config()
            self._start_monitor()

            # Resume the previous session (paused) if one was saved when the
            # source was switched away or idle-stopped; otherwise idle on the
            # WAITING placeholder until the user plays a context.
            if self._resume and await self._restore_resume_session():
                return True
            self.emit_connection_state(False)
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop mpv and the service, clearing the queue.

        A stop here is a source switch or routing change (never an explicit
        Stop), so the live session is snapshotted for resume-on-return before the
        teardown clears it."""
        await self._capture_resume_session()
        await self._cleanup()
        self._reset_playback_state()
        return await self._stop_service()

    async def _cleanup(self) -> None:
        """Tear down mpv + monitor. Leaves the StorageManager and the shared
        Navidrome client alone — both outlive playback (routes and the USB
        watcher use them while the source is inactive)."""
        self._stop_monitor()
        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None
        self._reset_playback_state()

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
        "set_shuffle": SetShuffleParams,
        "stop": None,
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
        if cmd == "set_shuffle":
            return await self._handle_set_shuffle(params)
        if cmd == "stop":
            return await self._handle_stop()
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_play_context(self, params: PlayContextParams) -> Dict[str, Any]:
        """Build the mpv playlist from a context and start playing at start_index."""
        if not self._mpv:
            return self.error_response("Music library not active")

        client = await self.get_navidrome_client()
        if client is None:
            return self.error_response("Music library catalog not ready")

        tracks = list(params.tracks)
        original_order = list(tracks)  # pristine order for a later shuffle-off
        start_index = min(params.start_index, len(tracks) - 1)
        shuffle = params.shuffle
        if shuffle:
            # Keep the picked track first, shuffle everything behind it.
            first = tracks.pop(start_index)
            random.shuffle(tracks)
            tracks.insert(0, first)
            start_index = 0

        urls = [client.stream_url(track["id"]) for track in tracks]

        # A fresh context supersedes any saved resume session.
        self._resume = None
        # Announce the target track buffering BEFORE the blocking load so the
        # player snaps to the new now-playing (title/artist/art, spinner) at once.
        self._loading = True
        self._queue = tracks
        self._queue_unshuffled = original_order
        self._queue_index = start_index
        self._shuffle = shuffle
        self._position = 0
        self._duration = int(tracks[start_index].get("duration") or 0)
        self._is_playing = False
        self._is_buffering = True
        self._update_connection_state()

        try:
            loaded = await self._mpv.load_playlist(urls, start_index)
        except Exception as e:
            self._logger.error(f"Failed to load playlist: {e}")
            loaded = False

        if not loaded:
            self._loading = False
            self._reset_playback_state()
            self.emit_connection_state(False)
            self.broadcast_error("Failed to start playback")
            return self.error_response("Failed to load playlist")

        # Playing now; is_buffering stays until the monitor sees the playhead
        # advance (keeps the progress bar at 0 instead of running ahead).
        self._is_playing = True
        self._loading = False
        self._handle_pause_change(False)
        self._update_connection_state()
        self.broadcast_error_cleared()
        return self.success_response(f"Playing {len(tracks)} track(s)")

    async def _handle_play_index(self, params: PlayIndexParams) -> Dict[str, Any]:
        """Jump to a specific entry in the current queue (queue-view tap)."""
        if not self._mpv or not self._queue:
            return self.error_response("No active queue")
        if params.index >= len(self._queue):
            return self.error_response(f"Queue index out of range: {params.index}")
        return await self._switch_to_index(params.index)

    async def _handle_next(self) -> Dict[str, Any]:
        if not self._mpv or not self._queue:
            return self.error_response("No active queue")
        if self._queue_index >= len(self._queue) - 1:
            return self.success_response("Already at end of queue")
        return await self._switch_to_index(self._queue_index + 1)

    async def _handle_prev(self) -> Dict[str, Any]:
        if not self._mpv or not self._queue:
            return self.error_response("No active queue")
        # Read the live playhead first — the tick's position can be up to
        # POSITION_SYNC_INTERVAL seconds stale.
        await self._sync_position_from_mpv()
        if self._position >= PREV_RESTART_THRESHOLD_S or self._queue_index == 0:
            try:
                await self._mpv.seek(0)
                await self._mpv.resume()
            except Exception as e:
                self._logger.error(f"Prev (restart) error: {e}")
                return self.error_response(str(e))
            self._position = 0
            self._is_playing = True
            self._handle_pause_change(False)
            self._update_connection_state()
            return self.success_response("Restarted track")
        return await self._switch_to_index(self._queue_index - 1)

    async def _switch_to_index(self, index: int) -> Dict[str, Any]:
        """Switch the mpv playlist to ``index`` and play it. Shared by
        play_index / next / prev-to-previous."""
        self._loading = True
        try:
            await self._mpv.set_playlist_pos(index)
            await self._mpv.resume()
        except Exception as e:
            self._loading = False
            self._logger.error(f"Track switch error: {e}")
            return self.error_response(str(e))

        self._queue_index = index
        self._position = 0
        self._duration = int(self._queue[index].get("duration") or 0)
        self._is_playing = True
        self._is_buffering = True  # cleared by the monitor once the playhead moves
        self._loading = False
        self._handle_pause_change(False)
        self._update_connection_state()
        self.broadcast_error_cleared()
        return self.success_response(f"Playing track {index + 1}")

    async def _handle_pause(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("Music library not active")
        if self._is_playing:
            # Snapshot the exact playhead before pausing so the broadcast lands
            # where playback stopped, not on the last (possibly stale) tick.
            await self._sync_position_from_mpv()
            await self._mpv.pause()
            self._is_playing = False
            self._handle_pause_change(True)
            self._update_connection_state()
        return self.success_response("Paused")

    async def _handle_resume(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("Music library not active")
        if not self._is_playing and self._queue:
            await self._mpv.resume()
            self._is_playing = True
            self._handle_pause_change(False)
            self._update_connection_state()
        return self.success_response("Resumed")

    async def _handle_seek(self, params: SeekParams) -> Dict[str, Any]:
        if not self._mpv or not self._queue:
            return self.error_response("No active queue")
        position = int(params.position_ms / 1000)
        try:
            await self._mpv.seek(position)
        except Exception as e:
            self._logger.error(f"Seek error: {e}")
            return self.error_response(str(e))
        self._position = position
        self._update_connection_state()
        return self.success_response(f"Seeked to {position}s")

    async def _handle_set_shuffle(self, params: SetShuffleParams) -> Dict[str, Any]:
        """Toggle shuffle on the live queue, reordering ONLY the upcoming tracks so
        the current one keeps playing (gapless, no restart).

        ON shuffles the tracks after the current index; OFF restores their pristine
        order from ``_queue_unshuffled``. The played/current head is left as-is
        either way. mpv's tail is rebuilt in place (no reload of the current
        entry) via :meth:`MpvController.replace_playlist_tail`.
        """
        if not self._mpv or not self._queue:
            return self.error_response("No active queue")
        target = bool(params.shuffle)
        if target == self._shuffle:
            return self.success_response("Shuffle unchanged")

        client = await self.get_navidrome_client()
        if client is None:
            return self.error_response("Music library catalog not ready")

        head = self._queue[: self._queue_index + 1]
        if target:
            tail = self._queue[self._queue_index + 1:]
            random.shuffle(tail)
        else:
            # Pristine order minus whatever is already in the played/current head.
            head_ids = {t.get("id") for t in head}
            tail = [t for t in self._queue_unshuffled if t.get("id") not in head_ids]

        urls = [client.stream_url(track["id"]) for track in tail]
        self._loading = True
        try:
            ok = await self._mpv.replace_playlist_tail(self._queue_index + 1, urls)
        except Exception as e:
            self._loading = False
            self._logger.error(f"Shuffle toggle error: {e}")
            return self.error_response(str(e))
        self._loading = False
        if not ok:
            return self.error_response("Failed to reorder queue")

        self._queue = head + tail
        self._shuffle = target
        self._update_connection_state()
        return self.success_response("Shuffle on" if target else "Shuffle off")

    async def _handle_stop(self) -> Dict[str, Any]:
        # Explicit Stop: forget any saved session (the user chose to stop).
        await self._stop_playback(save_resume=False)
        return self.success_response("Playback stopped")

    async def _auto_stop_action(self) -> None:
        """Stop playback after the idle pause timeout (releases the device; the
        screen can sleep). The source drops to WAITING but the session is saved so
        returning to the source resumes where it left off."""
        await self._stop_playback(save_resume=True)

    async def _stop_playback(self, save_resume: bool = False) -> None:
        """Stop mpv, clear the queue, and drop to WAITING.

        ``save_resume`` snapshots the live session first (idle auto-stop); an
        explicit Stop passes False and forgets any previously-saved session."""
        self._loading = False
        if save_resume:
            await self._capture_resume_session()
        else:
            self._resume = None
        # Drop any pending pause timer (mpv's pause property can stay True after
        # `stop`).
        self._handle_pause_change(False)
        if self._mpv:
            await self._mpv.stop()
        self._reset_playback_state()
        self.set_state(SourceState.WAITING, {"is_playing": False, "is_buffering": False})

    # =========================================================================
    # MONITOR
    # =========================================================================

    async def _on_monitor_tick(self) -> None:
        """Track gapless auto-advance, position, buffering and end-of-queue."""
        if not self._queue or self._loading:
            return

        idle_active = await self._mpv.get_property("idle-active")
        if idle_active is True:
            # keep-open=no + --idle=yes → mpv unloads at the end of the LAST
            # track and returns to idle. Authoritative end-of-queue signal.
            await self._handle_queue_finished()
            return

        playlist_pos = await self._mpv.get_property("playlist-pos")
        position = await self._mpv.get_property("time-pos")
        duration = await self._mpv.get_property("duration")
        pause_state = await self._mpv.get_property("pause")

        # Gapless auto-advance: mpv stepped to the next queue entry on its own.
        if (
            playlist_pos is not None
            and 0 <= playlist_pos < len(self._queue)
            and playlist_pos != self._queue_index
        ):
            self._queue_index = playlist_pos
            self._position = 0
            self._duration = int(self._queue[playlist_pos].get("duration") or 0)
            self._is_buffering = False
            self._update_connection_state()

        if position is not None:
            new_position = int(position)
            if new_position != self._position:
                self._position = new_position
            # Buffering → playing once the playhead actually moves.
            if self._is_buffering and position > 0:
                self._is_buffering = False
                self._update_connection_state()

        # Edge-trigger the first known duration so the ProgressBar appears without
        # waiting for the next periodic sync (Subsonic duration may be absent).
        duration_just_known = False
        if duration is not None:
            new_duration = int(duration)
            duration_just_known = self._duration == 0 and new_duration > 0
            self._duration = new_duration

        if duration_just_known:
            self.broadcast_position_update(self._position * 1000, self._duration * 1000)

        if (
            self._is_playing
            and self._position_sync_due()
            and not duration_just_known
        ):
            self.broadcast_position_update(self._position * 1000, self._duration * 1000)

        # Auto-stop on pause edges (device release after the configured timeout).
        if pause_state is not None:
            self._handle_pause_change(bool(pause_state))

    async def _handle_queue_finished(self) -> None:
        """The whole queue played out — drop to WAITING (the shared player hides)."""
        self._logger.info("Queue finished")
        # Nothing to resume once the queue has played out.
        self._resume = None
        self._reset_playback_state()
        self.set_state(
            SourceState.WAITING,
            {"is_playing": False, "is_buffering": False, "queue_ended": True},
        )

    async def _on_mpv_disconnect(self) -> None:
        """Unexpected mpv disconnect during playback: drop the queue state."""
        self._reset_playback_state()

    async def _sync_position_from_mpv(self) -> None:
        """Refresh _position from mpv's live time-pos (sub-tick precision)."""
        if not self._mpv:
            return
        position = await self._mpv.get_property("time-pos")
        if position is not None:
            self._position = int(position)

    # =========================================================================
    # RESUME-ON-RETURN (in-memory session snapshot)
    # =========================================================================

    async def _capture_resume_session(self) -> None:
        """Snapshot the live queue/track/position for resume-on-return.

        A no-op when nothing is loaded — it must NOT clear an existing snapshot:
        the idle auto-stop saves one and then empties the queue, so the source
        switch that follows would otherwise wipe the session it just took. Every
        deliberate "forget" is explicit elsewhere (explicit Stop, queue finished,
        a fresh context). Reads the exact playhead from mpv first so the resume
        lands where playback actually stopped. In-memory only — a backend restart
        forgets it.
        """
        if not self._queue or not (0 <= self._queue_index < len(self._queue)):
            return
        await self._sync_position_from_mpv()
        self._resume = {
            "queue": list(self._queue),
            "queue_unshuffled": list(self._queue_unshuffled),
            "queue_index": self._queue_index,
            "position": self._position,
            "shuffle": self._shuffle,
        }

    async def _restore_resume_session(self) -> bool:
        """Reload the saved session PAUSED at its stored track/position.

        Consumes ``self._resume`` (cleared regardless of outcome). Returns False
        when the catalog isn't ready or the load fails, so _do_start falls back to
        the WAITING placeholder.
        """
        session = self._resume
        self._resume = None
        if not session or not self._mpv:
            return False
        client = await self.get_navidrome_client()
        if client is None:
            return False

        tracks = session.get("queue") or []
        if not tracks:
            return False
        index = min(session.get("queue_index", 0), len(tracks) - 1)
        position = int(session.get("position") or 0)
        urls = [client.stream_url(track["id"]) for track in tracks]

        self._loading = True
        self._queue = tracks
        self._queue_unshuffled = session.get("queue_unshuffled") or list(tracks)
        self._queue_index = index
        self._shuffle = bool(session.get("shuffle"))
        self._position = position
        self._duration = int(tracks[index].get("duration") or 0)
        self._is_playing = False
        self._is_buffering = False
        # Show the restored (paused) track straight away, then load underneath.
        self._update_connection_state()

        try:
            # load_playlist always unpauses at the end, so pause right after it,
            # then seek into the saved position (seeking works fine while paused).
            loaded = await self._mpv.load_playlist(urls, index)
            if loaded:
                await self._mpv.pause()
                if position > 0:
                    await self._wait_and_seek(position)
        except Exception as e:
            self._logger.error(f"Resume restore failed: {e}")
            loaded = False

        self._loading = False
        if not loaded:
            self._reset_playback_state()
            return False

        self._is_playing = False
        self._handle_pause_change(True)
        self._update_connection_state()
        self._logger.info("Resumed previous session (paused) at %ss", position)
        return True

    async def _wait_and_seek(self, position: int, timeout: float = 2.0) -> None:
        """Wait until the stream is seekable (duration known), then seek there.

        Best-effort: on timeout the restored track simply starts from 0. Kept
        short because this runs inside _do_start, which must fit within
        AudioStateMachine.TRANSITION_TIMEOUT — losing the saved position is a
        far smaller regression than timing out the whole source switch."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            duration = await self._mpv.get_property("duration")
            if duration and duration > 0:
                await self._mpv.seek(position)
                return
            await asyncio.sleep(0.2)

    # =========================================================================
    # METADATA / STATE
    # =========================================================================

    def _cover_url(self, song: Dict[str, Any]) -> Optional[str]:
        """Our cover proxy URL for a song's art (coverArt id, album id fallback)."""
        cover_id = song.get("coverArt") or song.get("albumId")
        return f"/api/music-library/cover/{cover_id}" if cover_id else None

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """Now-playing projection for the current queue entry.

        position/duration are emitted in milliseconds to match the shared wire
        convention (Spotify/AirPlay/CD/Podcast); internal state stays in seconds.
        The whole queue rides along as extras so the frontend can render the
        queue view without a round-trip.
        """
        if not self._queue or not (0 <= self._queue_index < len(self._queue)):
            return {}

        current = self._queue[self._queue_index]
        return {
            "title": current.get("title"),
            "artist": current.get("artist"),
            "album": current.get("album"),
            "album_id": current.get("albumId"),
            "artist_id": current.get("artistId"),
            "album_art_url": self._cover_url(current),
            "position": self._position * 1000,
            "duration": self._duration * 1000,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "track_id": current.get("id"),
            "queue": self._queue,
            "queue_index": self._queue_index,
            "shuffle": self._shuffle,
        }

    def _update_connection_state(self) -> None:
        """Publish the current playback state. ACTIVE while a queue is loaded
        (playing OR paused), WAITING once it's cleared."""
        core, extras = PlaybackMetadata.split(self._build_playback_metadata())
        self.emit_connection_state(bool(self._queue), core, extras)

    async def refresh_metadata(self) -> bool:
        """Pull the live playhead from mpv so a (re)connecting client's
        initial_state reflects the current position, not the last periodic sync.
        Called by state.refresh_active_metadata() on the WS handshake.
        """
        if not self._queue or not self._mpv or not self._mpv.is_connected:
            return False

        position = await self._mpv.get_property("time-pos")
        duration = await self._mpv.get_property("duration")
        pause_state = await self._mpv.get_property("pause")
        if position is not None:
            self._position = int(position)
        if duration is not None:
            self._duration = int(duration)
        # Trust mpv's live pause over a cached flag (a reconnect can race a
        # pause still in flight), but not while buffering — mpv reports
        # pause=False before the stream is actually up.
        if pause_state is not None and not self._is_buffering:
            self._is_playing = not bool(pause_state)

        self._metadata = self._build_playback_metadata()
        return True

    # =========================================================================
    # NETWORK SHARES (SMB/NFS)
    # =========================================================================

    @property
    def shares(self) -> NetworkShareService:
        """Where the music comes from: the configured SMB/NFS shares and the USB
        volumes mounted beside them. Read by routes.py (see shares.py)."""
        return self._shares
