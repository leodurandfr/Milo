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

from shazamio import Shazam

logger = logging.getLogger(__name__)

# Suppress noisy symphonia warnings from ShazamIO's internal MP3 decoder
# (expected when decoding raw chunks captured from live audio streams)
logging.getLogger("symphonia_bundle_mp3").setLevel(logging.ERROR)
logging.getLogger("symphonia_core").setLevel(logging.ERROR)

# Recognition timing
INITIAL_DELAY_SECONDS = 10
RECOGNITION_INTERVAL_SECONDS = 30
AUDIO_CAPTURE_DURATION_SECONDS = 5
RECOGNITION_TIMEOUT_SECONDS = 25


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

        # Start recognition loop
        self._loop_task = asyncio.create_task(self._recognition_loop())

        logger.info(f"Shazam recognition started for stream: {stream_url}")

    async def stop(self) -> None:
        """Stop the recognition loop and clear state."""
        self._running = False

        # Cancel recognition loop
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
        """Capture audio and attempt recognition."""
        if not self._stream_url:
            return

        try:
            # Check if shazam is still enabled in settings
            enabled = await self.is_enabled()
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

            logger.debug(f"Captured {len(audio_bytes)} bytes for recognition")

            # Recognize
            result = await asyncio.wait_for(
                self._shazam.recognize(audio_bytes),
                timeout=RECOGNITION_TIMEOUT_SECONDS
            )
            track = self._parse_result(result)

            if track:
                logger.info(f"Track recognized: {track['title']} - {track['artist']}")
            else:
                logger.info("No track recognized")

            # Check if track changed and notify
            if self._track_changed(track):
                self._current_track = track
                if self._on_track_changed:
                    await self._on_track_changed(track)

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Shazam recognition timed out")
        except Exception as e:
            logger.warning(f"Recognition attempt failed: {e}")

    async def _capture_audio(self, url: str) -> Optional[bytes]:
        """
        Capture audio from stream using ffmpeg for reliable codec conversion.

        Uses ffmpeg to connect to the stream URL, decode any audio format
        (AAC, MP3, OGG, etc.), and output a clean WAV suitable for
        ShazamIO fingerprinting.
        """
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i", url,
                "-t", str(AUDIO_CAPTURE_DURATION_SECONDS),
                "-f", "wav",
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", "16000",
                "-v", "quiet",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=AUDIO_CAPTURE_DURATION_SECONDS + 10
            )
            if process.returncode != 0 or not stdout:
                logger.debug(f"ffmpeg capture failed (exit {process.returncode})")
                return None
            return stdout

        except asyncio.TimeoutError:
            if process:
                process.kill()
            logger.debug("Audio capture timed out")
            return None
        except Exception as e:
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

    async def is_enabled(self) -> bool:
        """Check if Shazam recognition is enabled in settings."""
        try:
            radio_settings = await self._settings_service.get_setting("radio") or {}
            return radio_settings.get("shazam_enabled", True)
        except Exception:
            return True
