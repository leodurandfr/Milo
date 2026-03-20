# backend/features/radio/source.py
"""
Radio audio source using MPV.

This source handles streaming audio from internet radio stations via MPV.
It provides station management (favorites, custom stations), RadioBrowser
API integration, and playback control.

Features:
- MPV IPC for playback control
- RadioBrowser API for station search
- Local favorites and custom stations
- Stream fallback mechanism
- Station image management
"""
import asyncio
from typing import Dict, Any, Optional

from backend.core.models.audio_state import PluginState
from backend.features.radio.data import StationDataService
from backend.features.radio.shazam import ShazamRecognitionService
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.features.radio.browser_api import RadioBrowserAPI


class RadioSource(MpvAudioSource):
    """
    Radio audio source using MPV.

    Implements AudioSource Protocol with:
    - start(): Start MPV service and connect IPC
    - stop(): Stop playback and service
    - restart(): Restart service with state reset
    - status(): Get current status with metadata
    - command(): Handle playback and station commands
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="radio",
            service_name="milo-radio.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        # Station data service (initialized immediately for API access)
        self._station_data = StationDataService(
            state_machine=state_machine
        )

        # RadioBrowser API (initialized immediately for API access)
        self._radio_api = RadioBrowserAPI(
            cache_duration_minutes=60,
            station_manager=self._station_data
        )
        self._station_data.radio_api = self._radio_api

        # Shazam recognition
        self._shazam: Optional[ShazamRecognitionService] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._current_station: Optional[Dict[str, Any]] = None
        self._last_station: Optional[Dict[str, Any]] = None

        # Schedule async initialization
        self._init_task: Optional[asyncio.Task] = None

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize station data (call at startup for API access)."""
        await self._station_data.initialize()
        self._logger.info("Radio station data initialized")
        return True

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._last_station = self._current_station or self._last_station
        self._current_station = None

    async def _do_start(self) -> bool:
        """Start MPV service and initialize components."""
        try:
            # 1. Start service
            if not await self._start_service_and_wait():
                return False

            # 2. Ensure station data is initialized
            if not self._station_data._loaded:
                await self._station_data.initialize()

            # 3. Create Shazam recognition service
            self._shazam = ShazamRecognitionService(
                settings_service=self._settings_service,
                on_track_changed=self._on_shazam_track_changed
            )

            # 4. Connect to MPV IPC
            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.error("Failed to connect to MPV IPC")
                return False

            # 5. Reset state
            self._reset_playback_state()

            # 6. Start monitor task
            self._start_monitor()

            # 7. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _before_restart_save(self) -> None:
        """Stop Shazam before restart."""
        if self._shazam:
            await self._shazam.stop()

    async def _get_status(self) -> Dict[str, Any]:
        """Get Radio-specific status."""
        mpv_connected = self._mpv.is_connected if self._mpv else False
        mpv_playing = await self._mpv.is_playing() if self._mpv and mpv_connected else False

        return {
            "mpv_connected": mpv_connected,
            "is_playing": mpv_playing,
            "is_buffering": self._is_buffering,
            "current_station": self._current_station,
            "metadata": self._metadata,
            "favorites_count": self._station_data.get_stats()['favorites_count'] if self._station_data else 0
        }

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Radio-specific commands."""
        if cmd == "play_station":
            return await self._handle_play_station(data)

        if cmd == "stop_playback":
            return await self._handle_stop_playback()

        if cmd == "resume_playback":
            return await self._handle_resume_playback()

        if cmd == "add_favorite":
            return await self._handle_add_favorite(data)

        if cmd == "remove_favorite":
            return await self._handle_remove_favorite(data)

        return self.error_response(f"Unknown command: {cmd}")

    # === Command Handlers ===

    async def _handle_play_station(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Play a radio station with fallback to alternative URLs."""
        station_id = data.get('station_id')
        if not station_id:
            return self.error_response("station_id required")

        try:
            # Get station with fallback chain: local favorite → provided → API
            station = None
            provided_station = data.get('station')

            # 1. Try local data for favorites
            if self._station_data.is_favorite(station_id):
                station = self._station_data.get_favorite_metadata_local(station_id)

            # 2. Fallback to provided station
            if not station and provided_station:
                station = provided_station

            # 3. Fallback to API
            if not station:
                station = await self._radio_api.get_station_by_id(station_id)

            if not station:
                return self.error_response(f"Station {station_id} not found")

            station_name = station.get('name', 'Unknown')
            primary_url = station.get('url')

            self._logger.info(f"Playing station: {station_name}")

            # Increment Radio Browser counter (fire and forget)
            asyncio.create_task(self._radio_api.increment_station_clicks(station_id))

            # Clear old Shazam track info before building metadata for new station
            if self._shazam:
                await self._shazam.stop()

            # Update state: buffering in progress
            self._current_station = station
            self._is_playing = False
            self._is_buffering = True
            self._metadata = self._build_playback_metadata()

            # Notify buffering state
            self._update_connection_state()

            # Try to play with fallback mechanism
            working_url = await self._try_play_with_fallback(station)

            if not working_url:
                self._is_buffering = False
                self._current_station = None
                error_msg = f"Unable to load stream: {station_name}"
                self.broadcast_error(error_msg)
                return self.error_response(error_msg)

            # Update station URL if we used an alternative
            if working_url != primary_url:
                station['url'] = working_url
                self._current_station = station
                self._metadata = self._build_playback_metadata()

            # Start Shazam recognition if enabled
            if self._shazam and await self._shazam.is_enabled():
                await self._shazam.start(working_url)

            return self.success_response(f"Loading {station_name}", station=station)

        except Exception as e:
            self._logger.error(f"Station playback error: {e}")
            self._is_buffering = False
            self.broadcast_error(str(e))
            return self.error_response(str(e))

    async def _try_play_with_fallback(
        self, station: Dict[str, Any], max_alternatives: int = 3
    ) -> Optional[str]:
        """Try to play a station with fallback to alternative URLs."""
        station_name = station.get('name', 'Unknown')
        primary_url = station.get('url')

        # Try primary URL
        if await self._try_single_url(primary_url):
            return primary_url

        self._logger.info(f"Primary URL failed for {station_name}, searching alternatives...")

        # Find and try alternative URLs
        alternatives = await self._radio_api.find_alternative_urls(
            station_name, exclude_url=primary_url
        )

        if not alternatives:
            return None

        for i, alt_station in enumerate(alternatives[:max_alternatives]):
            alt_url = alt_station.get('url')
            if not alt_url:
                continue

            if await self._try_single_url(alt_url):
                self._logger.info(f"Alternative URL {i+1} works for {station_name}")
                return alt_url

        return None

    async def _try_single_url(self, url: str) -> bool:
        """Try to play a single URL in mpv."""
        success = await self._mpv.load_stream(url)
        if not success:
            self._logger.debug(f"mpv load_stream failed for: {url[:80]}")
        return success

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback and reset to READY state."""
        try:
            self._is_playing = False
            self._is_buffering = False

            if self._shazam:
                await self._shazam.stop()

            await self._mpv.stop()

            self._last_station = self._current_station
            self._current_station = None
            self._metadata = {"is_playing": False, "is_buffering": False, "ready": True}
            self.set_state(PluginState.READY, self._metadata)

            return self.success_response("Playback stopped")

        except Exception as e:
            return self.error_response(str(e))

    async def _handle_resume_playback(self) -> Dict[str, Any]:
        """Resume playback of the last station."""
        if not self._last_station:
            return self.error_response("No station to resume")

        station_id = self._last_station.get('id', '')
        return await self._handle_play_station({
            'station_id': station_id,
            'station': self._last_station
        })

    async def _handle_add_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Add station to favorites."""
        station_id = data.get('station_id')
        if not station_id:
            return self.error_response("station_id required")

        station = data.get('station')
        if not station:
            station = await self._radio_api.get_station_by_id(station_id)

        if not station:
            return self.error_response(f"Station {station_id} not found")

        success = await self._station_data.add_favorite(station_id, station)
        return (
            self.success_response("Station added to favorites")
            if success else self.error_response("Add favorite failed")
        )

    async def _handle_remove_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove station from favorites."""
        station_id = data.get('station_id')
        if not station_id:
            return self.error_response("station_id required")

        success = await self._station_data.remove_favorite(station_id)
        return (
            self.success_response("Station removed from favorites")
            if success else self.error_response("Remove favorite failed")
        )

    # === Helpers ===

    def _build_playback_metadata(self, track_override=None) -> Dict[str, Any]:
        """Build metadata dict for current station, enriched with Shazam track info."""
        if not self._current_station:
            return {}

        track = track_override if track_override is not None else (
            self._shazam.current_track if self._shazam else None
        )

        return {
            "station_id": self._current_station.get('id'),
            "station_name": self._current_station.get('name'),
            "station_url": self._current_station.get('url'),
            "country": self._current_station.get('country'),
            "genre": self._current_station.get('genre'),
            "favicon": self._current_station.get('favicon'),
            "bitrate": self._current_station.get('bitrate'),
            "codec": self._current_station.get('codec'),
            "is_favorite": self._station_data.is_favorite(
                self._current_station.get('id')
            ) if self._station_data else False,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            # Shazam track recognition data
            "track_title": track["title"] if track else None,
            "track_artist": track["artist"] if track else None,
            "track_artwork": track["artwork"] if track else None
        }

    def _update_connection_state(self) -> None:
        """Update state based on playback."""
        if self._current_station and self._is_playing:
            self.broadcast_error_cleared()
        self._set_connected_or_ready(
            bool(self._current_station),
            self._build_playback_metadata(),
            {"is_playing": False, "is_buffering": False, "ready": True}
        )

    async def on_shazam_setting_changed(self, enabled: bool) -> bool:
        """React to Shazam toggle change."""
        if not self._shazam:
            return True

        if enabled:
            # Start recognition if radio is currently playing
            if self._current_station and self._is_playing:
                stream_url = self._current_station.get('url')
                if stream_url:
                    await self._shazam.start(stream_url)
        else:
            # Stop recognition loop and clear track info
            await self._shazam.stop()

        return True

    async def _on_shazam_track_changed(self, track) -> None:
        """Callback from ShazamRecognitionService when a new track is detected."""
        if self._current_station and self._is_playing:
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._stop_monitor()

        if self._shazam:
            await self._shazam.stop()
            self._shazam = None

        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

        # Note: station_data and radio_api persist for API access when radio is inactive
        self._reset_playback_state()

    # === Monitor hooks ===

    async def _on_mpv_disconnect(self) -> None:
        """Handle unexpected mpv disconnect."""
        self._is_playing = False
        self._is_buffering = False
        self._current_station = None
        self._metadata = {}

    async def _on_monitor_tick(self) -> None:
        """Check playback state transitions."""
        was_playing = self._is_playing
        self._is_playing = await self._mpv.is_playing()

        if self._is_playing and not was_playing:
            # Started playing - buffering complete
            self._is_buffering = False
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

        elif not self._is_playing and was_playing:
            # Stopped playing
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

    # === Public API ===

    @property
    def mpv(self) -> Optional[MpvController]:
        """Get MPV controller."""
        return self._mpv

    @property
    def station_data(self) -> Optional[StationDataService]:
        """Get station data service."""
        return self._station_data

    @property
    def radio_api(self) -> Optional[RadioBrowserAPI]:
        """Get RadioBrowser API client."""
        return self._radio_api

    @property
    def current_station(self) -> Optional[Dict[str, Any]]:
        """Get current station."""
        return self._current_station

    @property
    def is_buffering(self) -> bool:
        """Check if currently buffering."""
        return self._is_buffering
