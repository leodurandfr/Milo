# backend/infrastructure/plugins/radio/plugin.py
"""
Radio plugin for Milo - Web radio streaming via mpv
"""
import asyncio
import logging
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.radio.mpv_controller import MpvController
from backend.infrastructure.plugins.radio.radio_browser_api import RadioBrowserAPI
from backend.infrastructure.plugins.radio.station_manager import StationManager
from backend.infrastructure.services.radio_data_service import RadioDataService


class RadioPlugin(UnifiedAudioPlugin):
    """
    Radio plugin for Milo

    Follows the pattern of other plugins (Librespot, Bluetooth, ROC):
    - Controls an external systemd service (milo-radio.service with mpv)
    - Manages metadata (current station, stream title)
    - Multiroom and equalizer support via routing service

    States:
        INACTIVE → service stopped
        READY → service started (mpv in idle)
        CONNECTED → station playing
    """

    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__("radio", state_machine)

        self.config = config
        self.service_name = config.get("service_name", "milo-radio.service")
        self.ipc_socket_path = config.get("ipc_socket", "/tmp/milo-radio-ipc.sock")
        self.settings_service = settings_service

        # Create dedicated radio data service
        self.radio_data_service = RadioDataService()

        # Components
        self.mpv = MpvController(self.ipc_socket_path)
        self.station_manager = StationManager(self.radio_data_service, state_machine)
        # Note: station_manager is passed to RadioBrowserAPI to merge custom stations
        self.radio_api = RadioBrowserAPI(cache_duration_minutes=60, station_manager=self.station_manager)

        # Current state
        self.current_station: Optional[Dict[str, Any]] = None
        self._is_playing = False
        self._is_buffering = False
        self._metadata = {}
        self._current_device = "milo_radio"

        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

    async def _do_initialize(self) -> bool:
        """Radio plugin initialization"""
        try:
            # Check that service exists
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} not found")

            # Check that mpv is installed
            proc = await asyncio.create_subprocess_exec(
                "which", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()

            if proc.returncode != 0:
                raise RuntimeError("mpv is not installed")

            # Initialize components
            await self.station_manager.initialize()

            # Connect radio_api to station_manager for metadata fetching
            self.station_manager.radio_api = self.radio_api

            self.logger.info("Radio plugin initialized")
            return True

        except Exception as e:
            self.logger.error(f"Radio initialization error: {e}")
            return False

    async def _do_start(self) -> bool:
        """Starting Radio service"""
        try:
            # Start systemd service (mpv)
            if not await self.control_service(self.service_name, "start"):
                return False

            # Wait for service to be ready
            await asyncio.sleep(1)

            # Check that service is active
            is_active = await self.service_manager.is_active(self.service_name)
            if not is_active:
                self.logger.error("mpv service started but not active")
                return False

            # Connect to mpv IPC socket
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Unable to connect to mpv IPC socket")
                return False

            # Start mpv state monitoring
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notify READY state
            await self.notify_state_change(PluginState.READY, {
                "ready": True,
                "mpv_connected": self.mpv.is_connected
            })

            self.logger.info("Radio service started and ready")
            return True

        except Exception as e:
            self.logger.error(f"Radio start error: {e}")
            return False

    async def restart(self) -> bool:
        """Restarts mpv service"""
        try:
            self.logger.info("Restarting Radio service")

            # Stop monitoring
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

            # Disconnect mpv
            await self.mpv.disconnect()

            # Reset state
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            # Restart service
            success = await self.control_service(self.service_name, "restart")

            if not success:
                self.logger.error(f"Service restart failed {self.service_name}")
                return False

            # Wait for service to be ready
            await asyncio.sleep(1)

            # IPC reconnection
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Unable to reconnect to IPC socket after restart")
                return False

            # Restart monitoring
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notify READY state
            async def notify_ready_state():
                await asyncio.sleep(0.1)
                await self.notify_state_change(PluginState.READY, {"ready": True})

            asyncio.create_task(notify_ready_state())

            self.logger.info("Radio service restarted")
            return True

        except Exception as e:
            self.logger.error(f"Radio restart error: {e}")
            return False

    async def stop(self) -> bool:
        """Stops Radio plugin"""
        try:
            self.logger.info("Stopping Radio plugin")

            # Stop monitoring
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

            # Stop playback
            if self._is_playing:
                await self.mpv.stop()

            # Disconnect mpv
            await self.mpv.disconnect()

            # Close Radio Browser API
            await self.radio_api.close()

            # Stop service
            await self.control_service(self.service_name, "stop")

            # Reset state
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            await self.notify_state_change(PluginState.INACTIVE)
            self.logger.info("Radio plugin stopped")
            return True

        except Exception as e:
            self.logger.error(f"Radio stop error: {e}")
            return False

    async def _monitor_playback(self) -> None:
        """
        Monitors mpv playback state

        Periodically checks state and metadata
        """
        try:
            while not self._stopping:
                try:
                    # Check playback state
                    is_playing = await self.mpv.is_playing()

                    # DEBUG: Log raw mpv state (only during buffering)
                    if self.current_station and self._is_buffering:
                        playback_time = await self.mpv.get_property("playback-time")
                        self.logger.info(f"🔍 Monitor: is_playing={is_playing}, playback_time={playback_time}")

                    # Update state immediately (no debouncing with core-idle)
                    if is_playing != self._is_playing:
                        self._is_playing = is_playing
                        self.logger.info(f"Playback state changed: {'playing' if is_playing else 'stopped'}")

                        # If transitioning to playing and we were buffering, finish buffering
                        if is_playing and self._is_buffering:
                            self._is_buffering = False
                            self.logger.info("✅ Buffering completed, stream playing")

                    # plugin_state is CONNECTED while a station is loaded
                    # isPlaying in metadata indicates actual playback state
                    if self.current_station:
                        await self._update_metadata()

                        # Broadcast on each update to sync all clients
                        await self.notify_state_change(PluginState.CONNECTED, self._metadata)

                    # Fast polling to quickly detect playback start
                    await asyncio.sleep(0.5)  # Check every 0.5 seconds

                except Exception as e:
                    self.logger.error(f"Playback monitoring error: {e}")
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            self.logger.debug("Playback monitoring cancelled")
        except Exception as e:
            self.logger.error(f"Critical monitoring error: {e}")

    async def _update_metadata(self) -> None:
        """Updates metadata from mpv"""
        try:
            # Check that current_station is a dict BEFORE accessing properties
            if self.current_station and not isinstance(self.current_station, dict):
                self.logger.error(f"current_station is not a dict: {type(self.current_station)}, value: {self.current_station}")
                self.current_station = None
                self._metadata = {}
                return

            self._metadata = {
                "station_id": self.current_station.get('id') if self.current_station else None,
                "station_name": self.current_station.get('name') if self.current_station else None,
                "station_url": self.current_station.get('url') if self.current_station else None,
                "country": self.current_station.get('country') if self.current_station else None,
                "genre": self.current_station.get('genre') if self.current_station else None,
                "favicon": self.current_station.get('favicon') if self.current_station else None,
                "bitrate": self.current_station.get('bitrate') if self.current_station else None,
                "codec": self.current_station.get('codec') if self.current_station else None,
                "is_favorite": self.station_manager.is_favorite(
                    self.current_station.get('id')
                ) if self.current_station else False,
                "is_playing": self._is_playing,
                "buffering": self._is_buffering
            }

        except Exception as e:
            self.logger.error(f"Metadata update error: {e}", exc_info=True)

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
            # Get station
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
            await self._update_metadata()

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
                await self._update_metadata()

            # Buffering will continue until _monitor_playback detects is_playing=true
            # We don't set _is_playing = True here, we let _monitor_playback do it

            return self.format_response(
                True,
                message=f"Loading {station_name}",
                station=station
            )

        except Exception as e:
            self.logger.error(f"Station playback error: {e}")
            self._is_buffering = False
            return self.format_response(False, error=str(e))

    async def _try_play_with_fallback(self, station: Dict[str, Any], max_alternatives: int = 3) -> Optional[str]:
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
        alternatives = await self.radio_api.find_alternative_urls(station_name, exclude_url=primary_url)

        if not alternatives:
            self.logger.warning(f"No alternative URLs found for {station_name}")
            return None

        # Try alternatives (limited to max_alternatives to avoid long delays)
        for i, alt_station in enumerate(alternatives[:max_alternatives]):
            alt_url = alt_station.get('url')
            if not alt_url:
                continue

            self.logger.debug(f"Trying alternative {i+1}/{min(len(alternatives), max_alternatives)}: {alt_url[:80]}")

            if await self._try_single_url(alt_url):
                self.logger.info(f"Alternative URL {i+1} works for {station_name}")
                return alt_url

        self.logger.error(f"All {min(len(alternatives), max_alternatives) + 1} URLs failed for {station_name}")
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
        # mpv is better at detecting dead streams than HTTP HEAD/GET requests
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

            # Always reset state, even if mpv returns an error
            # (case where we call stop() when already stopped)
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

            return self.format_response(
                True,
                message="Playback stopped"
            )

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
            self.logger.debug(f"⚠️ No station object provided, fetching from API for {station_id}")
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
            service_status = await self.service_manager.get_status(self.service_name)
            mpv_status = await self.mpv.get_status()
            stats = self.station_manager.get_stats()

            return {
                "service_active": service_status.get("active", False),
                "mpv_connected": mpv_status.get("connected", False),
                "is_playing": self._is_playing,
                "current_station": self.current_station,
                "metadata": self._metadata,
                "current_device": self._current_device,
                "favorites_count": stats['favorites_count'],
                "broken_stations_count": stats['broken_stations_count']
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

    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state for WebSockets"""
        return await self.get_status()