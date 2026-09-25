# backend/sources/bluetooth/monitor.py
"""
BlueALSA PCM monitor for connection detection.

Monitors `bluealsa-cli monitor` output to detect device
connections and disconnections via PCM add/remove events.
"""
import asyncio
import contextlib
import logging
import signal
from typing import Dict, Any, Optional, Callable, Awaitable

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.shared.decorators import handle_errors


# Type for async callbacks
ConnectionCallback = Callable[[str, str], Awaitable[None]]
RunningCallback = Callable[[str, bool], Awaitable[None]]
ServiceCallback = Callable[[bool], Awaitable[None]]

# bluealsa-cli monitor event tokens (the scraped programmatic contract) and the
# PCM object-path structure. Pinned here + tested in test_bluetooth_pcm.py so a
# change is a deliberate edit, not a silent break.
PCM_ADDED_PREFIX = "PCMAdded"
PCM_REMOVED_PREFIX = "PCMRemoved"
# `-p`: "PropertyChanged <pcm path> Running true" — the sender's stream opened
# or stopped. Measured: a pause does not stop it (a paused phone keeps
# streaming, the Mac streams silence); a sender sending nothing does.
PROPERTY_CHANGED_PREFIX = "PropertyChanged"
RUNNING_PROPERTY = "Running"
# The BlueALSA daemon itself, printed once when the monitor starts and again
# on every change. Measured: BlueALSA killed, the monitor prints
# `ServiceStopped` and keeps running — its PCMs go without a PCMRemoved.
SERVICE_RUNNING_LINE = "ServiceRunning org.bluealsa"
SERVICE_STOPPED_LINE = "ServiceStopped org.bluealsa"
DEVICE_PATH_PREFIX = "dev_"        # …/dev_XX_XX_XX_XX_XX_XX/…
A2DP_PROFILE_TOKEN = "a2dp"        # profile segment, e.g. a2dpsnk
PCM_SOURCE_DIRECTION = "source"    # incoming audio (vs. sink)


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
        self._bus: Optional[MessageBus] = None  # BlueZ system bus for name lookups
        self._on_lost: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_running: Optional[RunningCallback] = None
        self._on_service: Optional[ServiceCallback] = None
        # What the monitor said of BlueALSA last; None until its first line,
        # which states the daemon as it was found and is not a change.
        self._service_up: Optional[bool] = None
        self._alive = False

    def set_callbacks(
        self,
        *,
        on_connect: ConnectionCallback,
        on_disconnect: ConnectionCallback,
        on_lost: Optional[Callable[[str], Awaitable[None]]] = None,
        on_running: Optional[RunningCallback] = None,
        on_service: Optional[ServiceCallback] = None,
    ) -> None:
        """
        Set the callbacks.

        Args:
            on_connect: Called with (address, name) on device connection
            on_disconnect: Called with (address, name) on device disconnection
            on_lost: Called with a reason when the feed itself dies, i.e. when
                connection detection has gone mute for good
            on_running: Called with (address, running) when a sender's stream
                opens or stops
            on_service: Called with False when BlueALSA goes (every PCM went
                with it, unannounced) and True when it is back
        """
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_lost = on_lost
        self._on_running = on_running
        self._on_service = on_service

    @handle_errors(default=False)
    async def start(self) -> bool:
        """
        Start BlueALSA PCM monitoring.

        Returns:
            True if monitoring started successfully
        """
        self._stopped = False
        self._service_up = None

        # Connect a persistent BlueZ system bus for device-name lookups.
        # Best-effort: fail open so dev machines without BlueZ still run.
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            self._bus = None
            self._logger.warning(f"BlueZ D-Bus unavailable, names will fall back: {e}")

        # Launch bluealsa-cli monitor
        self._process = await asyncio.create_subprocess_exec(
            "bluealsa-cli", "monitor", "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Start reading task
        self._alive = True
        self._read_task = asyncio.create_task(self._read_output())

        self._logger.info("BlueALSA monitoring started")
        return True

    async def stop(self) -> None:
        """Stop BlueALSA PCM monitoring."""
        self._stopped = True
        self._alive = False

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

        # Disconnect BlueZ bus
        if self._bus:
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

        self._connected_devices.clear()
        self._logger.info("BlueALSA monitoring stopped")

    async def _read_output(self) -> None:
        """Read and process bluealsa-cli output until it stops talking.

        Every way out of this loop that is not our own stop() leaves the source
        started with its connection detection permanently mute: no PCMAdded, no
        PCMRemoved, so a phone can neither be seen arriving nor leaving. It used
        to exit through a bare `break` — no log, no return code read, nothing
        anywhere to say the feed had gone. `_report_lost` is that report.
        """
        if not self._process or not self._process.stdout:
            await self._report_lost("bluealsa-cli produced no output stream")
            return

        try:
            while not self._stopped and not self._process.stdout.at_eof():
                line = await self._process.stdout.readline()
                if not line:
                    await self._report_lost("bluealsa-cli monitor closed its output")
                    return

                line_str = line.decode().strip()
                if line_str:
                    await self._process_line(line_str)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._stopped:
                await self._report_lost(f"monitor read error: {e}")

    async def _report_lost(self, reason: str) -> None:
        """Log the death of the feed with what the process left behind, and
        surface it once.

        One of the two implementations of the rule written down in
        `shared/journalctl.py`: a feed that dies is reported once, at error,
        naming what stops working — and the state is left to whoever owns the
        fact it asserts (here `BluetoothSource._on_monitor_lost`, which
        deliberately changes nothing).

        No automatic restart, deliberately: the client dying is not the daemon
        dying — measured, BlueALSA killed leaves `bluealsa-cli monitor` running
        and printing `ServiceStopped` (see `_handle_service`) — so an EOF here
        is the client itself failing, and respawning it blindly could busy-loop.
        The recovery gesture is a source restart, which _do_start performs in
        full; this makes it visible so it can be asked for.
        """
        if self._stopped or not self._alive:
            return
        self._alive = False

        detail = ""
        if self._process:
            with contextlib.suppress(Exception):
                if self._process.stderr:
                    detail = (await self._process.stderr.read()).decode().strip()
            # Bounded: a monitor that closed stdout without exiting must not
            # hold this report — the report is the point.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._process.wait(), 1.0)
            reason = f"{reason} (exit={self._process.returncode})"

        # A SIGTERM is logged at info: a backend restart signals this child with
        # everything else in its cgroup (measured). Whether that was all it was
        # is the source's to say — it asks systemd, and reports it otherwise.
        terminated = self._process is not None and self._process.returncode == -signal.SIGTERM
        (self._logger.info if terminated else self._logger.error)(
            f"BlueALSA monitor lost — Bluetooth connection detection is down until "
            f"the source is restarted: {reason}{f': {detail}' if detail else ''}"
        )

        if self._on_lost:
            with contextlib.suppress(Exception):
                await self._on_lost(reason)

    @handle_errors(default=None)
    async def _process_line(self, line: str) -> None:
        """
        Process a single monitor output line.

        Args:
            line: Output line from bluealsa-cli monitor
        """
        if line.startswith(PCM_ADDED_PREFIX):
            await self._handle_pcm_added(line)
        elif line.startswith(PCM_REMOVED_PREFIX):
            await self._handle_pcm_removed(line)
        elif line.startswith(PROPERTY_CHANGED_PREFIX):
            await self._handle_property(line)
        elif line in (SERVICE_RUNNING_LINE, SERVICE_STOPPED_LINE):
            await self._handle_service(line == SERVICE_RUNNING_LINE)

    async def _handle_property(self, line: str) -> None:
        """`PropertyChanged <path> Running true|false`; other properties
        (Volume, Delay) are not the monitor's business."""
        parts = line.split()
        if len(parts) != 4 or parts[2] != RUNNING_PROPERTY:
            return
        device_info = self.parse_pcm_path(parts[1])
        if device_info and self._on_running:
            await self._on_running(device_info["address"], parts[3] == "true")

    async def _handle_service(self, up: bool) -> None:
        """BlueALSA went or came back. Its PCMs went with it, unannounced, so
        the collection that authorizes departures is emptied here."""
        first, self._service_up = self._service_up is None, up
        if first:
            return
        if not up:
            self._connected_devices.clear()
            # Info, not error: stopped on purpose (a backend restart) looks the
            # same from here, and the source says which it was.
            self._logger.info("BlueALSA exited — every Bluetooth link it carried is gone")
        else:
            self._logger.info("BlueALSA is back")
        if self._on_service:
            await self._on_service(up)

    async def _handle_pcm_added(self, line: str) -> None:
        """Handle PCM added event."""
        # Extract path: "PCMAdded /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source"
        path = line.split(f"{PCM_ADDED_PREFIX} ", 1)[1].strip()
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

    def adopt_device(self, device_info: Dict[str, Any], name: str) -> None:
        """Record a PCM discovered outside the event stream.

        `_handle_pcm_removed` fires the departure only for addresses this
        collection holds, so a link found by the source's own start-time scan —
        a backend restart over a live A2DP session — was seen arriving and never
        seen leaving: the card kept naming a sender that had gone, and no other
        sender could take its place until the source was switched away. One
        collection owns "who is connected"; this is how the scan writes into it.

        Args:
            device_info: A parse_pcm_path() result for the live PCM
            name: Resolved device name
        """
        self._connected_devices[device_info["address"]] = {**device_info, "name": name}

    async def _handle_pcm_removed(self, line: str) -> None:
        """Handle PCM removed event."""
        path = line.split(f"{PCM_REMOVED_PREFIX} ", 1)[1].strip()
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

    async def read_running(self, path: str) -> bool:
        """Whether a PCM's stream is running now (`bluealsa-cli info`), for a
        link found outside the event stream. False when it cannot be read."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluealsa-cli", "info", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            self._logger.warning(f"Could not read the state of {path}: {e}")
            return False
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            self._logger.warning(f"Timeout reading the state of {path}")
            return False
        for line in stdout.decode().splitlines():
            key, _, value = line.partition(":")
            if key.strip() == RUNNING_PROPERTY:
                return value.strip() == "true"
        return False

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
        if (device_part.startswith(DEVICE_PATH_PREFIX) and
                A2DP_PROFILE_TOKEN in profile_part.lower() and
                direction_part == PCM_SOURCE_DIRECTION):
            address = device_part[len(DEVICE_PATH_PREFIX):].replace("_", ":")
            return {
                "address": address,
                "path": path,
                "type": "a2dp-sink"
            }

        return None

    async def resolve_device_name(self, address: str) -> str:
        """
        Resolve device name via BlueZ D-Bus (org.bluez.Device1 Alias/Name).

        Reads the property straight off the known device object path (same
        shape as hardware/bt_remote.py) rather than scraping `bluetoothctl
        info` or enumerating every managed object. Bounded by a 2s timeout so a
        wedged BlueZ can't stall the monitor read loop, and fails open to a
        synthetic name if D-Bus or the device object is unavailable.

        Args:
            address: Bluetooth device address

        Returns:
            Device name or fallback string
        """
        if self._bus:
            dev_path = "/org/bluez/hci0/dev_" + address.upper().replace(":", "_")
            try:
                name = await asyncio.wait_for(self._read_device_name(dev_path), 2.0)
                if name:
                    return name
            except asyncio.TimeoutError:
                self._logger.debug(f"D-Bus name resolution timed out for {address}")
            except Exception as e:
                self._logger.debug(f"D-Bus name resolution failed for {address}: {e}")

        return f"Device {address}"

    async def _read_device_name(self, dev_path: str) -> Optional[str]:
        """Read Alias (falling back to Name) for a BlueZ device object path."""
        introspect = await self._bus.introspect("org.bluez", dev_path)
        obj = self._bus.get_proxy_object("org.bluez", dev_path, introspect)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        for prop in ("Alias", "Name"):
            with contextlib.suppress(Exception):
                variant = await props.call_get("org.bluez.Device1", prop)
                if variant and variant.value:
                    return variant.value
        return None
