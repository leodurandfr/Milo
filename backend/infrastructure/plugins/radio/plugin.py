# backend/infrastructure/plugins/radio/plugin.py
"""
Radio plugin for Milo - Web radio streaming via mpv
"""
import asyncio
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.media_base import BaseMediaPlugin
from backend.domain.audio_state import AudioSource, PluginState
from backend.infrastructure.plugins.radio.radio_browser_api import RadioBrowserAPI
from backend.infrastructure.plugins.radio.station_manager import StationManager
from backend.infrastructure.services.radio_data_service import RadioDataService


class RadioPlugin(BaseMediaPlugin):
    """
    Radio plugin for Milo

    Extends BaseMediaPlugin with radio-specific functionality:
    - Station management (favorites, broken stations)
    - RadioBrowser API integration
    - Stream fallback mechanism

    States:
        STARTING → service starting
        READY → service started (mpv in idle)
        CONNECTED → station playing
        ERROR → service error
    """

    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__(
            source=AudioSource.RADIO,
            config=config,
            state_machine=state_machine,
            settings_service=settings_service,
            default_ipc_socket="/run/milo/radio-ipc.sock"
        )

        # Create dedicated radio data service
        self.radio_data_service = RadioDataService()

        # Components (initialized in _initialize_components)
        self.station_manager: Optional[StationManager] = None
        self.radio_api: Optional[RadioBrowserAPI] = None

        # Current station
        self.current_station: Optional[Dict[str, Any]] = None

    async def _initialize_components(self) -> bool:
        """Initialize radio-specific components."""
        try:
            # Initialize station manager
            self.station_manager = StationManager(self.radio_data_service, self.state_machine)
            await self.station_manager.initialize()

            # Initialize RadioBrowser API with station manager for metadata fetching
            self.radio_api = RadioBrowserAPI(
                cache_duration_minutes=60,
                station_manager=self.station_manager
            )

            # Connect radio_api to station_manager for metadata fetching
            self.station_manager.radio_api = self.radio_api

            return True

        except Exception as e:
            self.logger.error(f"Radio components initialization error: {e}")
            return False

    async def _cleanup_resources(self) -> None:
        """Clean up radio-specific resources."""
        # Close Radio Browser API
        if self.radio_api:
            await self.radio_api.close()

    async def _reset_playback_state(self) -> None:
        """Reset radio-specific playback state."""
        self.current_station = None

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for current station."""
        if not self.current_station:
            return {}

        return {
            "station_id": self.current_station.get('id'),
            "station_name": self.current_station.get('name'),
            "station_url": self.current_station.get('url'),
            "country": self.current_station.get('country'),
            "genre": self.current_station.get('genre'),
            "favicon": self.current_station.get('favicon'),
            "bitrate": self.current_station.get('bitrate'),
            "codec": self.current_station.get('codec'),
            "is_favorite": self.station_manager.is_favorite(
                self.current_station.get('id')
            ) if self.station_manager else False,
            "is_playing": self._is_playing,
            "buffering": self._is_buffering
        }

    async def _on_playback_state_changed(
        self, is_playing: bool, was_playing: bool
    ) -> None:
        """Handle playback state changes for radio."""
        # Broadcast state change if we have a current station
        if self.current_station:
            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

    async def _monitor_update(self) -> None:
        """Periodic monitoring update for radio."""
        # Update metadata and broadcast if changed
        if self.current_station:
            old_metadata = self._metadata.copy() if self._metadata else {}
            self._metadata = self._build_playback_metadata()

            # Only broadcast if metadata actually changed
            if self._metadata != old_metadata:
                await self.notify_state_change(PluginState.CONNECTED, self._metadata)

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes plugin commands

        Supported commands:
            - play_station: Plays a station by ID
            - stop_playback: Stops playback
            - add_favorite: Adds to favorites
            - remove_favorite: Removes from favorites
            - mark_broken: Marks station as broken
            - reset_broken: Resets broken stations
        """
        try:
            if command == "play_station":
                return await self._handle_play_station(data)

            elif command == "stop_playback":
                return await self._handle_stop_playback()

            elif command == "add_favorite":
                return await self._handle_add_favorite(data)

            elif command == "remove_favorite":
                return await self._handle_remove_favorite(data)

            elif command == "mark_broken":
                return await self._handle_mark_broken(data)

            elif command == "reset_broken":
                return await self._handle_reset_broken()

            return self.format_response(False, error=f"Unsupported command: {command}")

        except Exception as e:
            self.logger.error(f"Command processing error {command}: {e}")
            return self.format_response(False, error=str(e))

    async def _handle_play_station(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Plays a radio station with fallback to alternative URLs"""
        station_id = data.get('station_id')
        if not station_id:
            self.logger.error("play_station command without station_id")
            return self.format_response(False, error="station_id required")

        try:
            # Get station - favorites use local data only, others use API
            station = None
            if self.station_manager.is_favorite(station_id):
                # Favorite: use LOCAL data only (no API call)
                station = self.station_manager.get_favorite_metadata_local(station_id)
                if station:
                    self.logger.debug(f"Using local favorite data for {station_id}")
            else:
                # Non-favorite: use API
                station = await self.radio_api.get_station_by_id(station_id)

            if not station:
                self.logger.error(f"Station not found: {station_id}")
                return self.format_response(False, error=f"Station {station_id} not found")

            station_name = station.get('name', 'Unknown')
            primary_url = station.get('url')

            self.logger.info(f"Playing station: {station_name} (URL: {primary_url})")

            # Increment Radio Browser counter (fire and forget)
            asyncio.create_task(self.radio_api.increment_station_clicks(station_id))

            # Update state: buffering in progress
            self.current_station = station
            self._is_playing = False
            self._is_buffering = True
            self._metadata = self._build_playback_metadata()

            # Immediately notify buffering state
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            # Try to play with fallback mechanism
            working_url = await self._try_play_with_fallback(station)

            if not working_url:
                # All URLs failed - mark as broken
                self._is_buffering = False
                self.current_station = None
                await self.station_manager.mark_as_broken(station_id)
                self.logger.error(f"Unable to load stream: {station_name} (all URLs failed)")
                return self.format_response(
                    False,
                    error=f"Unable to load stream {station_name}"
                )

            # Update station URL if we used an alternative
            if working_url != primary_url:
                self.logger.info(f"Using alternative URL for {station_name}")
                station['url'] = working_url
                self.current_station = station
                self._metadata = self._build_playback_metadata()

            # Buffering will continue until monitor detects is_playing=true

            return self.format_response(
                True,
                message=f"Loading {station_name}",
                station=station
            )

        except Exception as e:
            self.logger.error(f"Station playback error: {e}")
            self._is_buffering = False
            return self.format_response(False, error=str(e))

    async def _try_play_with_fallback(
        self, station: Dict[str, Any], max_alternatives: int = 3
    ) -> Optional[str]:
        """
        Tries to play a station, with fallback to alternative URLs if primary fails.

        Args:
            station: Station dict with 'name' and 'url'
            max_alternatives: Maximum number of alternative URLs to try (default: 3)

        Returns:
            Working URL if successful, None if all URLs failed
        """
        station_name = station.get('name', 'Unknown')
        primary_url = station.get('url')

        # Step 1: Try primary URL
        self.logger.debug(f"Trying primary URL for {station_name}")
        if await self._try_single_url(primary_url):
            return primary_url

        self.logger.warning(f"Primary URL failed for {station_name}, searching alternatives...")

        # Step 2: Find and try alternative URLs
        alternatives = await self.radio_api.find_alternative_urls(
            station_name, exclude_url=primary_url
        )

        if not alternatives:
            self.logger.warning(f"No alternative URLs found for {station_name}")
            return None

        # Try alternatives (limited to max_alternatives to avoid long delays)
        for i, alt_station in enumerate(alternatives[:max_alternatives]):
            alt_url = alt_station.get('url')
            if not alt_url:
                continue

            self.logger.debug(
                f"Trying alternative {i+1}/{min(len(alternatives), max_alternatives)}: "
                f"{alt_url[:80]}"
            )

            if await self._try_single_url(alt_url):
                self.logger.info(f"Alternative URL {i+1} works for {station_name}")
                return alt_url

        self.logger.error(
            f"All {min(len(alternatives), max_alternatives) + 1} URLs failed for {station_name}"
        )
        return None

    async def _try_single_url(self, url: str) -> bool:
        """
        Tries to play a single URL in mpv.

        Args:
            url: Stream URL to try

        Returns:
            True if mpv accepted the stream
        """
        # Let mpv handle stream validation directly
        success = await self.mpv.load_stream(url)

        if not success:
            self.logger.debug(f"mpv load_stream failed for: {url[:80]}")
            return False

        return True

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stops playback"""
        try:
            # Always stop mpv (ignore error if already stopped)
            await self.mpv.stop()

            # Reset state
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False

            # Create metadata with is_playing: false to notify frontend
            self._metadata = {
                "is_playing": False,
                "buffering": False,
                "ready": True
            }

            await self.notify_state_change(PluginState.READY, self._metadata)

            return self.format_response(True, message="Playback stopped")

        except Exception as e:
            return self.format_response(False, error=str(e))

    async def _handle_add_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Adds station to favorites with complete metadata"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id required")

        # Get complete station object
        station = data.get('station')

        # If not provided, get from API
        if not station:
            self.logger.debug(f"No station object provided, fetching from API for {station_id}")
            station = await self.radio_api.get_station_by_id(station_id)

        if not station:
            return self.format_response(False, error=f"Station {station_id} not found")

        # Add to favorites with complete metadata
        success = await self.station_manager.add_favorite(station_id, station)

        return self.format_response(
            success,
            message="Station added to favorites" if success else "Add favorite failed"
        )

    async def _handle_remove_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Removes station from favorites"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id required")

        success = await self.station_manager.remove_favorite(station_id)
        return self.format_response(
            success,
            message="Station removed from favorites" if success else "Remove favorite failed"
        )

    async def _handle_mark_broken(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Marks station as broken"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id required")

        success = await self.station_manager.mark_as_broken(station_id)
        return self.format_response(
            success,
            message="Station marked as broken" if success else "Marking failed"
        )

    async def _handle_reset_broken(self) -> Dict[str, Any]:
        """Resets broken stations"""
        success = await self.station_manager.reset_broken_stations()
        return self.format_response(
            success,
            message="Broken stations reset" if success else "Reset failed"
        )

    async def get_status(self) -> Dict[str, Any]:
        """Gets current plugin state"""
        try:
            # Get base status
            base_status = await super().get_status()

            # Add radio-specific info
            stats = self.station_manager.get_stats() if self.station_manager else {}

            return {
                **base_status,
                "current_station": self.current_station,
                "favorites_count": stats.get('favorites_count', 0),
                "broken_stations_count": stats.get('broken_stations_count', 0)
            }

        except Exception as e:
            self.logger.error(f"Status error: {e}")
            return {
                "service_active": False,
                "mpv_connected": False,
                "is_playing": False,
                "current_station": None,
                "metadata": {},
                "current_device": self._current_device,
                "error": str(e)
            }
