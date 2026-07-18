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
now-playing projection (title/artist/album/art + queue/index/shuffle/repeat) is
broadcast over WS. Shuffle is honoured at play time; repeat is surfaced as state
(the toggle command lands in Phase 3).

Underneath, the USB storage layer (P1-4): initialize() starts a StorageManager
that watches for USB keys, mounts them read-only under /media/milo and triggers
a Navidrome rescan — independent of playback, so a plugged-in key is indexed even
when music_library is not the active source. See docs/plans/music-library.md.
"""
import random
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.core.models.audio_state import SourceState
from backend.core.models.source_metadata import PlaybackMetadata
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.music_library.models import (
    PlayContextParams,
    PlayIndexParams,
    SeekParams,
)
from backend.sources.music_library.navidrome_client import NavidromeClient
from backend.sources.music_library.storage import StorageManager

# Within this many seconds of a track, `prev` restarts the current track;
# earlier than that it steps to the previous entry (Spotify/go-librespot feel).
PREV_RESTART_THRESHOLD_S = 3


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
        # USB storage watcher — runs for the whole backend lifetime, like the CD
        # disc-watcher, not gated on this source being active.
        self._storage = StorageManager()
        # Navidrome Subsonic client for the /api/music-library/* browse routes
        # AND for building stream URLs at play time. Built lazily (the cred file
        # only exists once the daemon has provisioned its service account), and
        # shared across requests. Independent of the StorageManager's own client
        # — routes read the catalog even while music_library is not active.
        self._navidrome: Optional[NavidromeClient] = None

        # Playback / queue state (reset on stop). The queue holds the Subsonic
        # song dicts verbatim so it can be echoed to the frontend as-is.
        self._queue: List[Dict[str, Any]] = []
        self._queue_index: int = 0
        self._position: int = 0  # seconds into the current track
        self._duration: int = 0  # seconds
        self._shuffle: bool = False
        self._repeat: str = "off"  # surfaced only; the toggle command is Phase 3
        # Guards the monitor tick while a load / track-switch is mid-flight (mpv
        # briefly reports idle-active or a stale playlist-pos during the change).
        self._loading: bool = False

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

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def initialize(self) -> bool:
        """Start the USB storage watcher, then the base source init.

        Fail-open: initialize() never raises for storage (no udev on a dev host
        just disables auto-mount), so a broken storage layer can't block startup.
        """
        await self._storage.initialize()
        return await super().initialize()

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._queue = []
        self._queue_index = 0
        self._position = 0
        self._duration = 0
        self._shuffle = False
        self._repeat = "off"
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

            # Idle placeholder (no queue yet) — selecting the source lands on the
            # status card; play_context flips it to ACTIVE.
            self.emit_connection_state(False)
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop mpv and the service, clearing the queue."""
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
        start_index = min(params.start_index, len(tracks) - 1)
        shuffle = params.shuffle
        if shuffle:
            # Keep the picked track first, shuffle everything behind it.
            first = tracks.pop(start_index)
            random.shuffle(tracks)
            tracks.insert(0, first)
            start_index = 0

        urls = [client.stream_url(track["id"]) for track in tracks]

        # Announce the target track buffering BEFORE the blocking load so the
        # player snaps to the new now-playing (title/artist/art, spinner) at once.
        self._loading = True
        self._queue = tracks
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

    async def _handle_stop(self) -> Dict[str, Any]:
        await self._stop_playback()
        return self.success_response("Playback stopped")

    async def _auto_stop_action(self) -> None:
        """Stop playback in place after the pause timeout (releases the device;
        the screen can sleep). The source drops to WAITING."""
        await self._stop_playback()

    async def _stop_playback(self) -> None:
        """Stop mpv, clear the queue, and drop to WAITING."""
        self._loading = False
        # Explicit stop — drop any pending pause timer (mpv's pause property can
        # stay True after `stop`).
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
            "album_art_url": self._cover_url(current),
            "position": self._position * 1000,
            "duration": self._duration * 1000,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "track_id": current.get("id"),
            "queue": self._queue,
            "queue_index": self._queue_index,
            "shuffle": self._shuffle,
            "repeat": self._repeat,
        }

    def _update_connection_state(self) -> None:
        """Publish the current playback state. ACTIVE while a queue is loaded
        (playing OR paused), WAITING once it's cleared."""
        core, extras = PlaybackMetadata.split(self._build_playback_metadata())
        self.emit_connection_state(bool(self._queue), core, extras)

    async def _refresh_metadata(self) -> bool:
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
    # PUBLIC API
    # =========================================================================

    @property
    def position(self) -> int:
        """Current position in seconds."""
        return self._position

    @property
    def duration(self) -> int:
        """Current track duration in seconds."""
        return self._duration

    @property
    def queue(self) -> List[Dict[str, Any]]:
        """The current play queue (Subsonic song dicts)."""
        return self._queue
