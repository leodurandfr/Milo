# backend/sources/bluetooth/monitor.py
"""
BlueALSA PCM monitor for connection detection.

Monitors `bluealsa-cli monitor` output to detect device
connections and disconnections via PCM add/remove events.
"""
import asyncio
import contextlib
import logging
import re
from typing import Dict, Any, Optional, Callable, Awaitable

from backend.shared.decorators import handle_errors


# Type for async callbacks
ConnectionCallback = Callable[[str, str], Awaitable[None]]


class BlueAlsaMonitor:
    """
    Monitors BlueALSA PCM events.

    Uses `bluealsa-cli monitor -p` to watch for PCM add/remove events
    and translates them into connection/disconnection callbacks.
    """

    def __init__(self):
        """Initialize monitor."""
        self._logger = logging.getLogger("source.bluetooth.monitor")
        self._process: Optional[asyncio.subprocess.Process] = None
        self._connected_devices: Dict[str, Dict[str, Any]] = {}
        self._on_connect: Optional[ConnectionCallback] = None
        self._on_disconnect: Optional[ConnectionCallback] = None
        self._stopped = False
        self._read_task: Optional[asyncio.Task] = None

    def set_callbacks(
        self,
        on_connect: ConnectionCallback,
        on_disconnect: ConnectionCallback
    ) -> None:
        """
        Set connection/disconnection callbacks.

        Args:
            on_connect: Called with (address, name) on device connection
            on_disconnect: Called with (address, name) on device disconnection
        """
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    @handle_errors(default=False)
    async def start(self) -> bool:
        """
        Start BlueALSA PCM monitoring.

        Returns:
            True if monitoring started successfully
        """
        self._stopped = False

        # Launch bluealsa-cli monitor
        self._process = await asyncio.create_subprocess_exec(
            "bluealsa-cli", "monitor", "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Start reading task
        self._read_task = asyncio.create_task(self._read_output())

        self._logger.info("BlueALSA monitoring started")
        return True

    async def stop(self) -> None:
        """Stop BlueALSA PCM monitoring."""
        self._stopped = True

        # Cancel read task
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._read_task
            self._read_task = None

        # Stop monitor process
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                self._logger.error(f"Error stopping monitor: {e}")
            finally:
                self._process = None

        self._connected_devices.clear()
        self._logger.info("BlueALSA monitoring stopped")

    async def _read_output(self) -> None:
        """Read and process bluealsa-cli output."""
        if not self._process or not self._process.stdout:
            return

        try:
            while not self._stopped and not self._process.stdout.at_eof():
                line = await self._process.stdout.readline()
                if not line:
                    break

                line_str = line.decode().strip()
                if line_str:
                    await self._process_line(line_str)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._stopped:
                self._logger.error(f"Monitor read error: {e}")

    @handle_errors(default=None)
    async def _process_line(self, line: str) -> None:
        """
        Process a single monitor output line.

        Args:
            line: Output line from bluealsa-cli monitor
        """
        if line.startswith("PCMAdded"):
            await self._handle_pcm_added(line)
        elif line.startswith("PCMRemoved"):
            await self._handle_pcm_removed(line)

    async def _handle_pcm_added(self, line: str) -> None:
        """Handle PCM added event."""
        # Extract path: "PCMAdded /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source"
        path = line.split("PCMAdded ", 1)[1].strip()
        device_info = self.parse_pcm_path(path)

        if device_info:
            address = device_info["address"]

            # Resolve device name
            name = await self.resolve_device_name(address)
            device_info["name"] = name

            # Store device
            self._connected_devices[address] = device_info
            self._logger.info(f"Device connected: {name} ({address})")

            # Notify callback
            if self._on_connect:
                await self._on_connect(address, name)

    async def _handle_pcm_removed(self, line: str) -> None:
        """Handle PCM removed event."""
        path = line.split("PCMRemoved ", 1)[1].strip()
        device_info = self.parse_pcm_path(path)

        if device_info:
            address = device_info["address"]

            if address in self._connected_devices:
                name = self._connected_devices[address].get("name", "Unknown")
                del self._connected_devices[address]

                self._logger.info(f"Device disconnected: {name} ({address})")

                # Notify callback
                if self._on_disconnect:
                    await self._on_disconnect(address, name)

    def parse_pcm_path(self, path: str) -> Optional[Dict[str, Any]]:
        """
        Parse PCM path to extract device info.

        Args:
            path: PCM path like /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source

        Returns:
            Dict with address, path, type or None if not A2DP source
        """
        parts = path.split("/")

        if len(parts) < 6:
            return None

        device_part = parts[-3]   # dev_XX_XX_XX_XX_XX_XX
        profile_part = parts[-2]  # a2dpsnk
        direction_part = parts[-1]  # source

        # Only handle A2DP sink sources (incoming audio)
        if (device_part.startswith("dev_") and
                "a2dp" in profile_part.lower() and
                direction_part == "source"):
            address = device_part[4:].replace("_", ":")
            return {
                "address": address,
                "path": path,
                "type": "a2dp-sink"
            }

        return None

    async def resolve_device_name(self, address: str) -> str:
        """
        Resolve device name via bluetoothctl.

        Args:
            address: Bluetooth device address

        Returns:
            Device name or fallback string
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "info", address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=2.0
                )
                output = stdout.decode()

                # Search for name in output
                match = re.search(r"Name: (.+)$", output, re.MULTILINE)
                if match:
                    return match.group(1).strip()

            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

        except Exception as e:
            self._logger.debug(f"Name resolution failed for {address}: {e}")

        return f"Device {address}"
