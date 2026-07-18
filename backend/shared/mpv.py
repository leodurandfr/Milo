"""
mpv controller via IPC socket for playing radio streams
"""
import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any
from pathlib import Path


class MpvController:
    """
    Controls mpv via IPC socket for playing radio streams

    Asynchronous communication via Unix socket with mpv in JSON IPC mode.
    Pattern inspired by libmpv and python-mpv.
    """

    def __init__(self, ipc_socket_path: str = "/tmp/milo-radio-ipc.sock"):
        self.ipc_socket_path = ipc_socket_path
        self.logger = logging.getLogger(__name__)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._command_id = 0
        self._connected = False
        self._command_lock = asyncio.Lock()
        # Launch-time --stream-lavf-o (HTTP reconnect options), captured on
        # first connect and toggled off for HLS in load_stream. None until captured.
        self._default_stream_lavf_o: Optional[Any] = None

    async def connect(self, max_retries: int = 10, retry_delay: float = 0.5) -> bool:
        """
        Connects to mpv IPC socket with retry

        Args:
            max_retries: Number of connection attempts
            retry_delay: Delay between attempts (seconds)

        Returns:
            True if connection successful
        """
        for attempt in range(max_retries):
            try:
                if not Path(self.ipc_socket_path).exists():
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"IPC socket not found: {self.ipc_socket_path}")
                        return False

                self.reader, self.writer = await asyncio.open_unix_connection(self.ipc_socket_path)
                self._connected = True

                # Verify mpv responds to commands before declaring connected
                # Use get_property with idle-active (always available even when idle)
                test_response = await self._send_command("get_property", "idle-active")
                if test_response is None:
                    self.logger.debug("mpv socket connected but not responding, retrying...")
                    await self.disconnect()
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error("mpv connected but never responded to commands")
                        return False

                # Capture the launch-time reconnect options once, while mpv is
                # pristine — load_stream clears them for HLS and restores this.
                if self._default_stream_lavf_o is None:
                    self._default_stream_lavf_o = await self.get_property("stream-lavf-o")

                self.logger.info(f"Connected to mpv IPC socket: {self.ipc_socket_path}")
                return True

            except (ConnectionRefusedError, FileNotFoundError) as e:
                if attempt < max_retries - 1:
                    self.logger.debug(f"Retry {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(retry_delay)
                else:
                    self.logger.error(f"Failed to connect to mpv after {max_retries} attempts")
                    return False
            except Exception as e:
                self.logger.error(f"Unexpected error connecting to mpv: {e}")
                return False

        return False

    async def disconnect(self) -> None:
        """Disconnects from IPC socket"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                self.logger.debug(f"Error closing writer: {e}")

        self.reader = None
        self.writer = None
        self._connected = False
        self.logger.info("Disconnected from mpv IPC")

    @property
    def is_connected(self) -> bool:
        """Checks if connected to IPC socket"""
        return self._connected and self.writer is not None and not self.writer.is_closing()

    async def _send_command(self, command: str, *args) -> Optional[Dict[str, Any]]:
        """
        Sends a JSON IPC command to mpv

        mpv IPC format: {"command": ["command_name", "arg1", "arg2"], "request_id": 1}
        Serialized via _command_lock to prevent concurrent socket access.

        Args:
            command: mpv command name
            *args: Command arguments

        Returns:
            JSON response from mpv or None if error
        """
        if not self.is_connected:
            self.logger.debug("Not connected to mpv, attempting reconnect...")
            if not await self.connect():
                return None

        async with self._command_lock:
            try:
                self._command_id += 1
                request_id = self._command_id
                request = {
                    "command": [command, *args],
                    "request_id": request_id
                }

                command_json = json.dumps(request) + "\n"
                self.writer.write(command_json.encode('utf-8'))
                await self.writer.drain()

                # Read the response by matching request_id. mpv interleaves async
                # event lines (no request_id) on the same socket; during a stream
                # load or rapid station change it can burst many events before the
                # reply, so bound the search by a wall-clock deadline rather than a
                # fixed line count and keep skipping events until our reply arrives.
                deadline = time.monotonic() + 5.0
                try:
                    while True:
                        timeout = deadline - time.monotonic()
                        if timeout <= 0:
                            raise asyncio.TimeoutError
                        response_line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
                        if not response_line:
                            self.logger.debug(f"mpv socket closed while awaiting request {request_id}")
                            await self.disconnect()
                            return None

                        response = json.loads(response_line.decode('utf-8'))

                        # Ignore mpv events (no request_id)
                        if 'event' in response:
                            continue

                        # If it's the response to our request, return it
                        if response.get('request_id') == request_id:
                            error = response.get('error')
                            # Only log real errors, not transient errors
                            if error not in ('success', None, 'null', 'property unavailable'):
                                self.logger.warning(f"mpv command error: {error}")
                            return response
                        # Else: a reply to a stale/earlier request (shouldn't happen
                        # under _command_lock) — skip and keep reading until ours
                        # or the deadline.

                except asyncio.TimeoutError:
                    self.logger.debug(f"Timeout waiting for mpv response to: {command}")
                    return None

            except Exception as e:
                self.logger.error(f"Error sending command to mpv: {e}")
                await self.disconnect()
                return None

    async def command(self, command: str, *args) -> Optional[Dict[str, Any]]:
        """
        Sends an arbitrary mpv IPC command.

        This is a public wrapper around _send_command for commands not covered
        by dedicated methods (e.g., chapter navigation).

        Args:
            command: mpv command name (e.g., "add", "cycle")
            *args: Command arguments

        Returns:
            JSON response from mpv or None if error
        """
        return await self._send_command(command, *args)

    @staticmethod
    def _is_hls(url: str) -> bool:
        """True if the URL is an HLS playlist (.m3u8), ignoring query/fragment."""
        path = url.split("?", 1)[0].split("#", 1)[0]
        return path.lower().endswith(".m3u8")

    async def _apply_stream_options(self, url: str) -> None:
        """
        Scope mpv's --stream-lavf-o reconnect options per stream.

        The reconnect options are HTTP-stream options that keep an Icecast
        stream alive, but they stall HLS: ffmpeg reconnects at every segment
        EOF instead of advancing, so mpv never produces a first frame and the
        stream hangs in "loading" forever. Suppress them for .m3u8 (the HLS
        demuxer handles its own segment retries) and keep the launch defaults
        (captured in connect) otherwise. The systemd unit stays the single
        source of truth.
        """
        if not url:
            return
        if self._is_hls(url):
            await self.set_property("stream-lavf-o", "")
        elif self._default_stream_lavf_o is not None:
            await self.set_property("stream-lavf-o", self._default_stream_lavf_o)

    async def load_stream(self, url: str) -> bool:
        """
        Loads and plays a radio stream

        Args:
            url: Radio stream URL

        Returns:
            True if command sent successfully
        """
        await self._apply_stream_options(url)
        self.logger.info(f"Loading stream: {url[:100]}...")
        response = await self._send_command("loadfile", url, "replace")

        # mpv can return transient errors (None, "property unavailable")
        # during initial stream loading. We accept these errors.
        if response is None:
            self.logger.info("loadfile returned None")
            return False

        error = response.get('error')
        # Accept 'success' AND transient errors (None, null, property unavailable)
        # "property unavailable" happens when quickly changing stations
        # Only real errors ("file not found", etc.) cause failure
        if error in ('success', None, 'null', 'property unavailable'):
            return True

        # Log only real errors
        self.logger.error(f"loadfile failed with error: {error}")
        return False

    async def stop(self) -> bool:
        """
        Stops current playback

        Returns:
            True if command sent successfully
        """
        self.logger.info("Stopping playback")
        response = await self._send_command("stop")
        return response is not None and response.get('error') == 'success'

    async def get_property(self, property_name: str) -> Optional[Any]:
        """
        Gets an mpv property

        Args:
            property_name: Property name (e.g.: "pause", "volume", "metadata")

        Returns:
            Property value or None
        """
        response = await self._send_command("get_property", property_name)
        if response and response.get('error') == 'success':
            return response.get('data')
        return None

    async def get_metadata(self) -> Dict[str, str]:
        """
        Get current in-band stream metadata (ICY / HLS tags).

        mpv's IPC read loop skips async event lines, so metadata is not
        push-observed — this polls the `metadata` property (cheap local IPC).
        mpv exposes the ICY StreamTitle as `icy-title` and the station name as
        `icy-name`; HLS ID3 tags surface under their own keys.

        Returns:
            Lowercased-key dict of string metadata values (non-string values
            dropped), or an empty dict when mpv reports no metadata.
        """
        raw = await self.get_property("metadata")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key).lower(): value
            for key, value in raw.items()
            if isinstance(value, str)
        }

    async def set_property(self, property_name: str, value: Any) -> bool:
        """
        Sets an mpv property

        Args:
            property_name: Property name
            value: New value

        Returns:
            True if successful
        """
        response = await self._send_command("set_property", property_name, value)
        return response is not None and response.get('error') == 'success'

    async def is_playing(self) -> bool:
        """
        Checks if mpv is playing via playback-time

        Returns:
            True if playing (playback-time exists)
        """
        # playback-time is the most reliable property for streams
        # It exists as soon as mpv starts decoding, and disappears when stopped
        playback_time = await self.get_property("playback-time")

        # If playback-time is a number (even 0), the stream is playing
        return isinstance(playback_time, (int, float))

    async def wait_until_advancing(
        self, timeout: float = 3.0, poll_interval: float = 0.05
    ) -> bool:
        """Wait until mpv's playhead actually advances past 0.

        After un-pausing, mpv's audio output has a startup latency during which
        `time-pos` stays at 0 for up to ~1s — a mere "time-pos is a number"
        check (file loaded) fires immediately and is NOT real playback. Callers
        gate UI/buffering state on this so a progress bar doesn't run ahead of a
        not-yet-moving playhead. Bounded by `timeout` so a stalled source can't
        hang the caller. Returns True once advancing, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time_pos = await self.get_property("time-pos")
            if isinstance(time_pos, (int, float)) and time_pos > 0:
                return True
            await asyncio.sleep(poll_interval)
        return False

    async def get_status(self) -> Dict[str, Any]:
        """
        Gets current mpv state

        Returns:
            Dict with connection and playback state
        """
        return {
            "connected": self.is_connected,
            "playing": await self.is_playing() if self.is_connected else False
        }

    async def pause(self) -> bool:
        """
        Pauses playback

        Returns:
            True if successful
        """
        return await self.set_property("pause", True)

    async def resume(self) -> bool:
        """
        Resumes playback

        Returns:
            True if successful
        """
        return await self.set_property("pause", False)

    async def seek(self, position: float) -> bool:
        """
        Seeks to a specific position

        Args:
            position: Position in seconds

        Returns:
            True if successful
        """
        response = await self._send_command("seek", position, "absolute")
        return response is not None and response.get('error') == 'success'

    # === Playlist (gapless queue) ===

    async def load_playlist(self, urls: list, start_index: int = 0) -> bool:
        """Build mpv's native playlist from an ordered list of stream URLs and
        start playing at ``start_index``.

        With the unit's ``--gapless-audio=yes`` a native playlist plays truly
        gapless across the queue. Loads paused so the first entry doesn't blip
        before jumping to ``start_index``, then unpauses. The first URL uses
        ``loadfile … replace`` (which clears any previous queue); the rest are
        appended in order. Returns False if the initial load fails; a single
        failed append is logged but doesn't abort the whole queue.
        """
        if not urls:
            return False

        await self.set_property("pause", True)
        if not await self.load_stream(urls[0]):
            return False
        for url in urls[1:]:
            response = await self._send_command("loadfile", url, "append")
            if response is None:
                self.logger.warning("playlist append failed for an entry")

        if start_index:
            await self.set_property("playlist-pos", start_index)
        await self.set_property("pause", False)
        return True

    async def set_playlist_pos(self, index: int) -> bool:
        """Jump to a 0-based entry in the current playlist (mpv loads + plays it,
        honoring the current pause state)."""
        return await self.set_property("playlist-pos", index)
