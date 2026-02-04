# backend/features/radio/shazam.py
"""
Shazam-based track recognition for radio streams.

Periodically captures audio from the radio stream URL and uses ShazamIO
to identify the playing track. Provides track title, artist, and artwork
to enrich the radio player display.
"""
import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

import aiohttp
from shazamio import Shazam

logger = logging.getLogger(__name__)

# Suppress noisy symphonia warnings from ShazamIO's internal MP3 decoder
# (expected when decoding raw chunks captured from live audio streams)
logging.getLogger("symphonia_bundle_mp3").setLevel(logging.ERROR)
logging.getLogger("symphonia_core").setLevel(logging.ERROR)

# Recognition timing
INITIAL_DELAY_SECONDS = 10
RECOGNITION_INTERVAL_SECONDS = 45
AUDIO_CAPTURE_DURATION_SECONDS = 5
RECOGNITION_TIMEOUT_SECONDS = 25

# Audio capture limits (~40KB/s covers most stream bitrates up to 320kbps)
BYTES_PER_SECOND = 40_000


class ShazamRecognitionService:
    """
    Periodic track recognition for radio streams using ShazamIO.

    Captures short audio snippets from the stream URL and identifies
    the playing track. Results are cached to avoid redundant broadcasts.
    """

    def __init__(
        self,
        settings_service,
        on_track_changed: Optional[Callable[[Optional[Dict[str, Any]]], Coroutine]] = None
    ):
        """
        Initialize the recognition service.

        Args:
            settings_service: SettingsService for checking shazam_enabled
            on_track_changed: Async callback invoked when the recognized track changes.
                              Called with track dict or None (unrecognized).
        """
        self._settings_service = settings_service
        self._on_track_changed = on_track_changed
        self._shazam = Shazam()

        # State
        self._stream_url: Optional[str] = None
        self._current_track: Optional[Dict[str, Any]] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def current_track(self) -> Optional[Dict[str, Any]]:
        """
        Get the currently recognized track.

        Returns:
            Dict with 'title', 'artist', 'artwork' keys, or None if
            no track is recognized or recognition is not running.
        """
        return self._current_track

    def clear_track(self) -> None:
        """Clear the current track without stopping the recognition loop."""
        self._current_track = None

    async def start(self, stream_url: str) -> None:
        """
        Start periodic recognition for the given stream URL.

        If already running on the same URL, this is a no-op.
        If running on a different URL, restarts with the new URL.
        """
        if self._running and self._stream_url == stream_url:
            return

        # Stop existing loop if URL changed
        if self._running:
            await self.stop()

        self._stream_url = stream_url
        self._running = True
        self._current_track = None
        self._loop_task = asyncio.create_task(self._recognition_loop())
        logger.info("Shazam recognition started for stream")

    async def stop(self) -> None:
        """Stop the recognition loop and clear state."""
        self._running = False

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        previous_track = self._current_track
        self._current_track = None
        self._stream_url = None

        # Notify that track info was cleared
        if previous_track and self._on_track_changed:
            try:
                await self._on_track_changed(None)
            except Exception as e:
                logger.error(f"Error in track changed callback: {e}")

        logger.info("Shazam recognition stopped")

    async def _recognition_loop(self) -> None:
        """Main recognition loop: wait, capture, recognize, repeat."""
        try:
            # Initial delay to let buffering complete
            await asyncio.sleep(INITIAL_DELAY_SECONDS)

            while self._running:
                await self._try_recognize()
                await asyncio.sleep(RECOGNITION_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Recognition loop error: {e}")

    async def _try_recognize(self) -> None:
        """Capture audio and attempt recognition. Never raises."""
        if not self._stream_url:
            return

        try:
            # Check if shazam is still enabled in settings
            enabled = await self._is_enabled()
            if not enabled:
                if self._current_track:
                    self._current_track = None
                    if self._on_track_changed:
                        await self._on_track_changed(None)
                return

            # Capture audio from stream
            audio_bytes = await self._capture_audio(self._stream_url)
            if not audio_bytes:
                logger.warning("No audio captured, skipping recognition")
                return

            logger.debug(f"Captured {len(audio_bytes)} bytes, sending to Shazam")

            # Recognize
            result = await asyncio.wait_for(
                self._shazam.recognize(audio_bytes),
                timeout=RECOGNITION_TIMEOUT_SECONDS
            )
            track = self._parse_result(result)

            if track:
                logger.info(f"Track recognized: {track['title']} - {track['artist']}")
            else:
                matches = result.get("matches", []) if result else []
                logger.info(f"No track recognized (matches: {len(matches)})")

            # Check if track changed and notify
            if self._track_changed(track):
                self._current_track = track
                if self._on_track_changed:
                    await self._on_track_changed(track)

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Shazam recognition timed out, will retry next cycle")
        except Exception as e:
            logger.warning(f"Recognition attempt failed: {e}")

    async def _capture_audio(self, url: str) -> Optional[bytes]:
        """
        Download a short audio snippet from the stream URL.

        Opens a separate HTTP connection to the stream (independent of mpv)
        and reads data in chunks over AUDIO_CAPTURE_DURATION_SECONDS seconds
        to accumulate enough audio for reliable recognition.
        """
        max_bytes = BYTES_PER_SECOND * AUDIO_CAPTURE_DURATION_SECONDS
        timeout = aiohttp.ClientTimeout(
            total=AUDIO_CAPTURE_DURATION_SECONDS + 10,
            sock_read=AUDIO_CAPTURE_DURATION_SECONDS + 5
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    "Icy-MetaData": "0",
                    "User-Agent": "Milo/1.0"
                }
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.debug(f"Stream returned status {resp.status}")
                        return None

                    # Read in chunks to accumulate data from live stream
                    chunks = []
                    total = 0
                    deadline = asyncio.get_running_loop().time() + AUDIO_CAPTURE_DURATION_SECONDS

                    while total < max_bytes:
                        remaining_time = deadline - asyncio.get_running_loop().time()
                        if remaining_time <= 0:
                            break

                        try:
                            chunk = await asyncio.wait_for(
                                resp.content.read(8192),
                                timeout=remaining_time
                            )
                        except asyncio.TimeoutError:
                            break

                        if not chunk:
                            break

                        chunks.append(chunk)
                        total += len(chunk)

                    return b"".join(chunks) if chunks else None

        except asyncio.TimeoutError:
            logger.debug("Audio capture timed out")
            return None
        except aiohttp.ClientError as e:
            logger.debug(f"Audio capture failed: {e}")
            return None

    def _parse_result(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse ShazamIO recognition result into a simple track dict.

        Returns:
            Dict with 'title', 'artist', 'artwork' keys, or None if not recognized.
        """
        if not result or "track" not in result:
            return None

        track = result["track"]
        title = track.get("title")
        artist = track.get("subtitle")

        if not title:
            return None

        # Extract artwork URL from sections or top-level
        artwork = None
        # Try share.image first (highest quality)
        share = track.get("share", {})
        if share.get("image"):
            artwork = share["image"]
        # Fallback to images.coverart
        if not artwork:
            images = track.get("images", {})
            artwork = images.get("coverart") or images.get("coverarthq")

        # Upgrade artwork resolution from default 400x400 to 1280x1280
        if artwork:
            artwork = artwork.replace("/400x400cc.jpg", "/1280x1280cc.jpg")

        return {
            "title": title,
            "artist": artist or "",
            "artwork": artwork
        }

    def _track_changed(self, new_track: Optional[Dict[str, Any]]) -> bool:
        """Check if the recognized track differs from the cached one."""
        if new_track is None and self._current_track is None:
            return False
        if new_track is None or self._current_track is None:
            return True
        return (
            new_track["title"] != self._current_track["title"]
            or new_track["artist"] != self._current_track["artist"]
        )

    async def _is_enabled(self) -> bool:
        """Check if Shazam recognition is enabled in settings."""
        try:
            radio_settings = await self._settings_service.get_setting("radio") or {}
            return radio_settings.get("shazam_enabled", True)
        except Exception:
            return True
