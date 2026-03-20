# backend/features/radio/shazam.py
"""
Shazam-based track recognition for radio streams.

Periodically captures audio from the radio stream URL and uses ShazamIO
to identify the playing track. Provides track title, artist, and artwork
to enrich the radio player display.
"""
import asyncio
import io
import logging
import wave
from typing import Any, Callable, Coroutine, Dict, Optional

from aiohttp_retry import ExponentialRetry
from shazamio import Shazam, HTTPClient

logger = logging.getLogger(__name__)

# Recognition timing
INITIAL_DELAY_SECONDS = 10
RECOGNITION_INTERVAL_SECONDS = 30
SEGMENT_DURATION_SECONDS = 12
RECOGNITION_TIMEOUT_SECONDS = 25
MAX_RETRIES = 3

# ffmpeg capture timeout (capture duration + buffer for connection/codec init)
FFMPEG_TIMEOUT_SECONDS = SEGMENT_DURATION_SECONDS + 15


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
        self._shazam = Shazam(
            http_client=HTTPClient(
                retry_options=ExponentialRetry(
                    attempts=5,
                    max_timeout=30,
                    statuses={500, 502, 503, 504, 429},
                ),
            ),
            segment_duration_seconds=SEGMENT_DURATION_SECONDS,
        )

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
        """Main recognition loop with immediate retries on failure.

        On failure, retries up to MAX_RETRIES times with fresh audio segments
        (the stream advances, so each capture covers different audio).
        On success or after exhausting retries, waits RECOGNITION_INTERVAL_SECONDS.
        """
        try:
            await asyncio.sleep(INITIAL_DELAY_SECONDS)

            while self._running:
                recognized = await self._try_recognize()

                if not recognized:
                    for retry in range(MAX_RETRIES):
                        if not self._running:
                            break
                        logger.info(f"Retrying recognition ({retry + 1}/{MAX_RETRIES})...")
                        recognized = await self._try_recognize()
                        if recognized:
                            break

                await asyncio.sleep(RECOGNITION_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Recognition loop error: {e}")

    async def _try_recognize(self) -> bool:
        """Capture audio and attempt recognition.

        Returns:
            True if a track was recognized, False otherwise.
        """
        if not self._stream_url:
            return False

        try:
            # Check if shazam is still enabled in settings
            enabled = await self.is_enabled()
            if not enabled:
                if self._current_track:
                    self._current_track = None
                    if self._on_track_changed:
                        await self._on_track_changed(None)
                return False

            # Capture audio from stream
            audio_bytes = await self._capture_audio(self._stream_url)
            if not audio_bytes:
                logger.info("No audio captured, skipping recognition")
                return False

            logger.debug(f"Captured {len(audio_bytes)} bytes for recognition")

            # Recognize
            result = await asyncio.wait_for(
                self._shazam.recognize(audio_bytes),
                timeout=RECOGNITION_TIMEOUT_SECONDS
            )
            track = self._parse_result(result)

            if track:
                match_count = len(result.get("matches", []))
                logger.info(f"Track recognized ({match_count} matches): {track['title']} - {track['artist']}")
            else:
                logger.info("No track recognized")

            # Check if track changed and notify
            if self._track_changed(track):
                self._current_track = track
                if self._on_track_changed:
                    await self._on_track_changed(track)

            return track is not None

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.info("Shazam recognition timed out")
            return False
        except Exception as e:
            logger.info(f"Recognition attempt failed: {e}")
            return False

    async def _capture_audio(self, url: str) -> Optional[bytes]:
        """
        Capture audio from stream using ffmpeg and produce a valid WAV buffer.

        Outputs raw PCM from ffmpeg (avoiding the piped WAV header size issue
        where ffmpeg writes 0xFFFFFFFF as RIFF size, which rodio/hound rejects)
        and wraps it with Python's wave module to produce correct RIFF headers.
        """
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i", url,
                "-t", str(SEGMENT_DURATION_SECONDS),
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", "16000",
                "-v", "quiet",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )

            if process.returncode != 0 or not stdout:
                logger.debug(f"ffmpeg capture failed (exit {process.returncode})")
                return None

            # Wrap raw PCM in a proper WAV container with correct headers
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(stdout)

            return wav_buffer.getvalue()

        except asyncio.TimeoutError:
            if process:
                process.kill()
                await process.wait()
            logger.debug("Audio capture timed out")
            return None
        except Exception as e:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
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
