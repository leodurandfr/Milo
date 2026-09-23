# backend/sources/podcast/source.py
"""
Podcast audio source using MPV.

This source handles podcast playback with progress tracking, speed control,
and the podcast catalogue (Apple discovery, publisher feeds for content).

Features:
- MPV IPC for playback control
- Progress tracking with auto-save
- Playback speed control (0.5x - 2.0x)
- Resume from last position
- PodcastCatalog for discovery and feed reading
"""
from backend.core.models.ws_events import SourceErrorReason
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from pydantic import BaseModel

from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, ReroutePolicy, ResumePolicy,
)
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.podcast.models import PlayEpisodeParams, SeekParams, SetSpeedParams
from backend.sources.podcast.data import PodcastDataService
from backend.shared.decorators import handle_errors
from backend.shared.mpv_audio_source import MpvAudioSource, MpvSession
from backend.sources.podcast.podcast_catalog import PodcastCatalog

VALID_PLAYBACK_SPEEDS: list[float] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

# A position saved on disk is resumed only past this many seconds.
RESUME_MIN_POSITION_S = 10

# Seconds of sound between two progress saves.
PROGRESS_SAVE_TICKS = 10


@dataclass(eq=False)
class PodcastSession(MpvSession):
    """One episode loaded, from the play to a named end."""
    episode: Dict[str, Any] = field(default_factory=dict)


class PodcastSource(MpvAudioSource):
    """
    Podcast audio source using MPV.

    Family C (active player): controlled from Milō's UI. A session is one
    episode; mpv says when its sound starts and why it ends, and only its own
    `eof` makes an episode "listened" (E47, E48). What any other end leaves is
    the episode to resume, at its second; the progress file stays the
    authority on where a resume starts.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.AUTO_STOP
    REROUTE = ReroutePolicy.RESTART_AND_RESTORE
    RESUME_POLICY = ResumePolicy(
        capture_on=frozenset({
            EndReason.USER_STOP, EndReason.IDLE_TIMEOUT, EndReason.SOURCE_SWITCH,
            EndReason.REROUTE, EndReason.DAEMON_DIED, EndReason.STREAM_LOST,
            # An episode that would not open stays to listen (the doc).
            EndReason.LOAD_FAILED,
        }),
        # Played to the end: finished, not paused. The rest cannot happen here.
        forget_on=frozenset({
            EndReason.EOF, EndReason.SENDER_LEFT, EndReason.STORAGE_GONE,
        }),
    )
    SESSION_DAEMON = False

    COMMANDS = {
        "play_episode": PlayEpisodeParams,
        "pause": None,
        "resume": None,
        "seek": SeekParams,
        "set_speed": SetSpeedParams,
    }
    COMMAND_SCOPES = {
        "play_episode": CommandScope.CONTENT,
        "pause": CommandScope.SESSION,
        "resume": CommandScope.RESUME,
        "seek": CommandScope.SESSION,
        "set_speed": CommandScope.PREFERENCE,
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="podcast",
            service_name="milo-podcast.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        # Podcast data service - initialized immediately for routes access
        self._podcast_data = PodcastDataService(
            state_machine=state_machine
        )

        # Catalogue client (Apple discovery + publisher feeds), initialized
        # immediately for routes access
        self._podcast_api = PodcastCatalog(cache_duration_minutes=60)

        self._playback_speed = 1.0

    async def initialize(self) -> bool:
        """Pre-load podcast_data.json so a schema mismatch surfaces at boot."""
        await self._podcast_data.initialize()
        return await super().initialize()

    def _idle_metadata(self) -> Dict[str, Any]:
        """A stopped podcast still has an episode to resume, so publish the
        full projection (same reason the CD keeps a loaded disc visible).

        An episode that ended leaves no resume point, and nothing played at
        all never made one; both fall back to the pair every player reads.
        """
        projection = self._build_playback_metadata()
        return projection if projection else super()._idle_metadata()

    def _resume_content(self, session: PodcastSession):
        return (
            session.episode.get('uuid') or "",
            session.position * 1000,
            {"episode": session.episode, "duration": session.duration},
        )

    async def _do_start(self) -> bool:
        """Start MPV service and initialize components."""
        try:
            if not await self._start_service_and_wait():
                return False

            if not await self._attach_mpv():
                return False
            await self._listen_to_mpv()

            self._playback_speed = await self._podcast_data.get_setting("playback_speed", 1.0)
            await self._load_auto_stop_config()

            point = self._resume_point
            if (
                point is not None
                and point.reason is EndReason.REROUTE
                and point.phase is not Phase.PAUSED
            ):
                # A multiroom toggle comes back playing, at the same second.
                await self._play(point.content["episode"], point.position_ms // 1000)
                return True
            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self.end_session(EndReason.LOAD_FAILED)
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Save where the episode is, keep it to resume (E46), stop mpv."""
        await self._end_with_progress(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    @handle_errors(default=False)
    async def _do_release(self) -> bool:
        await self._end_with_progress(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _end_with_progress(self, reason: EndReason) -> None:
        session = self._session
        if isinstance(session, PodcastSession):
            await self._sync_position(session)
            await self._save_progress(session)
        await self.end_session(reason)

    async def refresh_metadata(self) -> bool:
        """Pull the live playhead from mpv so the initial_state a (re)connecting
        client gets carries the current second, not the last 30 s sync."""
        session = self._session
        if not isinstance(session, PodcastSession) or not self._mpv or not self._mpv.is_connected:
            return False
        await self._sync_position(session)
        self._metadata = self._build_playback_metadata()
        return True

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Podcast-specific commands."""
        if cmd == "play_episode":
            return await self._handle_play_episode(params)

        if cmd == "pause":
            return await self._handle_pause()

        if cmd == "resume":
            return await self._handle_resume()

        if cmd == "seek":
            return await self._handle_seek(params)

        if cmd == "set_speed":
            return await self._handle_set_speed(params)

        return self.error_response(f"Unhandled command: {cmd}")

    async def _itunes_country(self) -> str:
        """The Apple storefront to resolve against — the same one the routes
        use. Playback resolving against a different store than the screen that
        listed the episode is how a podcast opens but refuses to play."""
        from backend.sources.podcast.podcast_catalog import (
            map_milo_language_to_itunes_country,
        )
        if not self._settings_service:
            return "us"
        # `language` is guaranteed by SettingsService.defaults, so it is read
        # straight — restating a fallback here would be a second declaration of
        # a default that lives in one place.
        settings = await self._settings_service.load_settings()
        return map_milo_language_to_itunes_country(settings["language"])

    # === Command Handlers ===

    async def _handle_play_episode(self, params: PlayEpisodeParams) -> Dict[str, Any]:
        """Play an episode, from `params.position` when given, else from the
        second the progress file kept."""
        episode_uuid = params.episode_uuid
        try:
            self._logger.info(f"Starting playback for episode: {episode_uuid}")

            episode = await self._podcast_api.get_episode(
                episode_uuid, country=await self._itunes_country()
            )
            if not episode:
                return self.error_response(f"Episode not found: {episode_uuid}")
            if not episode.get('audio_url'):
                return self.error_response(f"No audio URL for episode: {episode_uuid}")
            self._logger.info(f"Episode found: {episode.get('name', 'Unknown')}")

            if isinstance(self._session, PodcastSession):
                await self._sync_position(self._session)
                await self._save_progress(self._session)

            start_position = params.position or 0
            if not start_position:
                progress = await self._podcast_data.get_playback_progress(episode_uuid)
                if progress and progress.get('position', 0) > RESUME_MIN_POSITION_S:
                    start_position = progress['position']
                    self._logger.info(f"Resuming from {start_position}s")

            return await self._play(episode, start_position)

        except Exception as e:
            self._logger.error(f"Episode playback error: {e}")
            self.broadcast_error(SourceErrorReason.PLAYBACK_FAILED)
            return self.error_response(str(e))

    async def _play(self, episode: Dict[str, Any], start_position: int) -> Dict[str, Any]:
        """Open a session on `episode` and load it at `start_position`: one
        command, no wait-then-seek (E57)."""
        # Replaced, not stopped: the load below replaces the entry in mpv.
        await self.end_session(EndReason.USER_STOP)
        session = PodcastSession(
            phase=Phase.LOADING, episode=episode,
            position=int(start_position), duration=int(episode.get('duration') or 0),
        )
        self.open_session(session)
        self._update_connection_state()

        async def load():
            if await self._mpv_ready() and await self._set_mpv_pause(False):
                return await self._mpv.loadfile(
                    episode['audio_url'], mode="replace", start_s=start_position or None
                )
            return None

        entry = await self._attempt(load)
        if entry is None:
            await self._end_playback(EndReason.LOAD_FAILED, detail="mpv refused the load")
            return self.error_response("Failed to load stream")
        session.entry = entry
        session.link = self._mpv.link
        await self._attempt(lambda: self._mpv.set_property("speed", self._playback_speed))
        return self.success_response(f"Playing {episode.get('name', 'Unknown')}")

    async def _handle_pause(self) -> Dict[str, Any]:
        """Pause playback; the phase follows mpv's pause event."""
        session = self._session
        if not await self._mpv.pause():
            return self.mpv_refused("pause")
        await self._sync_position(session)
        await self._save_progress(session)
        return self.success_response("Paused")

    async def _handle_resume(self) -> Dict[str, Any]:
        """Resume playback — unpause the live episode, or reload the one kept.

        The second branch is what an auto-stop or a switch leaves behind: the
        episode the source published as its resume identity is exactly what a
        play press means — the rotary and the IR remote send `resume` and know
        no other name. It goes through the play path so the position comes
        from the progress file, like any play.
        """
        if self._session is None:
            point = self._resume_point
            if point is None:
                return self.error_response("No episode to resume")
            return await self._handle_play_episode(
                PlayEpisodeParams(episode_uuid=point.identity)
            )
        if not await self._mpv.resume():
            return self.mpv_refused("resume")
        return self.success_response("Resumed")

    async def _handle_seek(self, params: SeekParams) -> Dict[str, Any]:
        """Seek to position (params normalize `position`/`position_ms` to seconds)."""
        session = self._session
        position = int(params.seconds)
        if not await self._mpv.seek(position):
            return self.mpv_refused(f"seek to {position}s")
        session.position = position
        await self._save_progress(session)
        self._update_connection_state()
        return self.success_response(f"Seeked to {params.seconds}s")

    async def _handle_set_speed(self, params: SetSpeedParams) -> Dict[str, Any]:
        """Set playback speed — a stored preference, re-applied to every
        episode at play time, so it holds with nothing playing."""
        speed = params.speed
        if speed not in VALID_PLAYBACK_SPEEDS:
            self._logger.info(f"Invalid speed {speed}, using nearest valid")
            speed = min(VALID_PLAYBACK_SPEEDS, key=lambda x: abs(x - speed))

        if self._session is not None:
            if not await self._mpv.set_property("speed", speed):
                return self.mpv_refused(f"speed {speed}x")
        self._playback_speed = speed
        await self._podcast_data.set_setting("playback_speed", speed)
        self._update_connection_state()

        self._logger.info(f"Playback speed set to {speed}x")
        return self.success_response(f"Speed set to {speed}x", speed=speed)

    # === Helpers ===

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for the live episode, or the one kept to resume.

        position/duration are emitted in milliseconds to match the wire
        convention used by the other audio sources (Spotify, AirPlay, CD) and
        by broadcast_position_update. Internal state stays in seconds.
        """
        session = self._session
        if isinstance(session, PodcastSession):
            episode, position, duration = session.episode, session.position, session.duration
        elif self._resume_point is not None:
            point = self._resume_point
            episode = point.content["episode"]
            position, duration = point.position_ms // 1000, point.content["duration"]
        else:
            return {}

        is_playing, is_buffering = self._flags(session.phase if session else None)
        podcast_name = episode.get('podcast', {}).get('name')

        metadata = {
            "episode_uuid": episode.get('uuid'),
            "episode_name": episode.get('name'),
            "description": episode.get('description'),
            "image_url": episode.get('image_url'),
            "position": position * 1000,
            "duration": duration * 1000,
            "is_playing": is_playing,
            "is_buffering": is_buffering,
            "playback_speed": self._playback_speed,
            "current_episode": episode,
            # The cross-source floor every generic consumer reads (lock screen,
            # widget, shared player). Computed here so no client has to
            # re-derive it; see core/push/payloads.py.
            "title": episode.get('name'),
            "artist": podcast_name,
            "album": podcast_name,
            "album_art_url": episode.get('image_url'),
        }

        if 'podcast' in episode:
            metadata['podcast_name'] = podcast_name
            metadata['podcast_uuid'] = episode['podcast'].get('uuid')

        return metadata

    def _update_connection_state(self, extras: Optional[Dict[str, Any]] = None) -> None:
        """Update state based on playback — the source's only publish site.

        `extras` carries the fields that describe one particular transition
        rather than the session (the episode-end trio).
        """
        connected, core, built = self._connection_state()
        self.emit_connection_state(connected, core, {**built, **(extras or {})})

    def _connection_state(self):
        core, built = PlaybackMetadata.split(self._build_playback_metadata())
        return self._session is not None, core, built

    async def _sync_position(self, session: Optional[PodcastSession]) -> None:
        """Read the live playhead into the session (while its file is open)."""
        if isinstance(session, PodcastSession) and session.opened and self._mpv:
            await self._read_playhead(session)

    async def _save_progress(self, session: Optional[PodcastSession]) -> None:
        """Save the session's position to the progress file."""
        if not isinstance(session, PodcastSession) or session.position <= 0:
            return
        podcast_info = session.episode.get('podcast', {})
        await self._podcast_data.update_playback_progress(
            episode_uuid=session.episode['uuid'],
            position=session.position,
            duration=session.duration,
            podcast_uuid=podcast_info.get('uuid', ''),
            episode_name=session.episode.get('name', ''),
            podcast_name=podcast_info.get('name', ''),
            image_url=session.episode.get('image_url', '')
        )
        self._logger.debug(f"Saved progress: {session.position}/{session.duration}s")

    async def _cleanup(self) -> None:
        """Close mpv. The catalogue and the progress file stay: the routes read
        them while the source is stopped."""
        await self._detach_mpv()

    # === What mpv announces ===

    async def _mpv_lost(self, session: PodcastSession) -> None:
        await self._save_progress(session)
        await super()._mpv_lost(session)

    async def _before_idle_end(self, session: PodcastSession) -> None:
        await self._sync_position(session)
        await self._save_progress(session)

    async def _end_playback(self, reason: EndReason, **kwargs) -> None:
        if reason in (EndReason.STREAM_LOST, EndReason.LOAD_FAILED):
            await self._save_progress(self._session)
        await super()._end_playback(reason, **kwargs)

    async def _content_finished(self, session: PodcastSession) -> None:
        """The episode played to its end — its own `eof`, nothing else."""
        finished_uuid = session.episode['uuid']
        self._logger.info("Episode finished")
        # Persisted so the episode shows "already listened" and leaves the
        # in-progress queue. A final row first (a short clip may never have hit
        # a periodic save), then the explicit mark: the position>=duration-30
        # heuristic behind the row fails when mpv over-reports VBR durations.
        try:
            await self._save_progress(session)
            await self._podcast_data.mark_episode_completed(finished_uuid)
        except Exception as e:
            self._logger.error(f"Failed to persist episode completion: {e}")
        # episode_uuid + completed let the frontend flip the just-finished card
        # to "already listened" without a re-fetch.
        await self._end_playback(EndReason.EOF, stop_mpv=False, extras={
            "episode_ended": True,
            "episode_uuid": finished_uuid,
            "completed": True,
        })

    async def _on_playing_tick(self, session: PodcastSession) -> None:
        _, just_known = await self._read_playhead(session)
        self._sync_bar(session, just_known)
        if session.ticks % PROGRESS_SAVE_TICKS == 0:
            try:
                await self._save_progress(session)
            except Exception as e:
                # Per tick, like any loop body: a disk hiccup costs this save.
                self._logger.error(f"Progress save failed: {e}")

    # === Public API ===

    @property
    def podcast_data(self) -> Optional[PodcastDataService]:
        """Get podcast data service."""
        return self._podcast_data

    @property
    def podcast_api(self) -> Optional[PodcastCatalog]:
        """Get the podcast catalogue client."""
        return self._podcast_api

    @property
    def playback_speed(self) -> float:
        """Get current playback speed."""
        return self._playback_speed
