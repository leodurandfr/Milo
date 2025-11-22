"""
mpv controller via IPC socket for playing radio streams
"""
import asyncio
import json
import logging
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

        Args:
            command: mpv command name
            *args: Command arguments

        Returns:
            JSON response from mpv or None if error
        """
        if not self.is_connected:
            self.logger.warning("Not connected to mpv, attempting reconnect...")
            if not await self.connect():
                return None

        try:
            self._command_id += 1
            request = {
                "command": [command, *args],
                "request_id": self._command_id
            }

            # Send the command
            command_json = json.dumps(request) + "\n"
            self.writer.write(command_json.encode('utf-8'))
            await self.writer.drain()

            # Read the response by matching request_id (with timeout)
            try:
                # Read up to 10 lines max to find the right response
                for _ in range(10):
                    response_line = await asyncio.wait_for(self.reader.readline(), timeout=5.0)
                    if not response_line:
                        break

                    response = json.loads(response_line.decode('utf-8'))

                    # Ignore mpv events (no request_id)
                    if 'event' in response:
                        continue

                    # If it's the response to our request, return it
                    if response.get('request_id') == self._command_id:
                        error = response.get('error')
                        # Only log real errors, not transient errors
                        if error not in ('success', None, 'null', 'property unavailable'):
                            self.logger.warning(f"mpv command error: {error}")
                        return response

                # No matching response found
                self.logger.warning(f"No matching response for request {self._command_id}")
                return None

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for mpv response to: {command}")
                return None

        except Exception as e:
            self.logger.error(f"Error sending command to mpv: {e}")
            self._connected = False
            return None

    async def load_stream(self, url: str) -> bool:
        """
        Loads and plays a radio stream

        Args:
            url: Radio stream URL

        Returns:
            True if command sent successfully
        """
        self.logger.info(f"Loading stream: {url[:100]}...")
        response = await self._send_command("loadfile", url, "replace")

        # mpv can return transient errors (None, "property unavailable")
        # during initial stream loading. We accept these errors.
        if response is None:
            self.logger.warning("loadfile returned None")
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

