# backend/hardware/bt_remote.py
"""
Bluetooth HID remote controller for volume and playback control.

Supports ANTICATER VK-01 and similar BT HID devices that send
standard Consumer Control keycodes (KEY_VOLUMEUP, KEY_VOLUMEDOWN, KEY_MUTE).
Completely independent from the Bluetooth A2DP audio source.

Features:
- Automatic detection of BT HID devices via evdev
- Automatic Bluetooth discovery and pairing of matching devices
- Configurable key mapping (keycodes to actions)
- Multi-click playback dispatch via PlaybackDispatcher (1=play/pause, 2=next, 3=prev)
- Volume control via VolumeService
- Playback control via state_machine sources
"""
import asyncio
import contextlib
import logging
import re
from typing import Dict, Optional, Set, Union

from backend.core.models.ws_events import (
    BtRemoteConfig,
    BtRemoteConfigChanged,
    BtRemoteStatusChanged,
)
from backend.hardware.playback_dispatch import PlaybackDispatcher
from backend.hardware.volume_accumulator import VolumeAccumulator

try:
    import evdev
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

try:
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType, MessageType
    from dbus_next import Message
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default configuration for ANTICATER VK-01
DEFAULT_KEY_MAP = {
    "115": "volume_up",     # KEY_VOLUMEUP (rotation CW)
    "114": "volume_down",   # KEY_VOLUMEDOWN (rotation CCW)
    "113": "click",         # KEY_MUTE -> multi-click detection
}
DEFAULT_DEVICE_FILTER = "ANTICATER"
SCAN_INTERVAL = 30.0        # Fallback interval — D-Bus listener handles instant reconnect
# Fallback only: the D-Bus InterfacesAdded/PropertiesChanged listener handles
# instant reconnection; this cycle just catches anything it missed.
DISCOVERY_INTERVAL = 300.0  # Seconds between BT reconnect/discovery cycles
DISCOVERY_DURATION = 5      # Seconds to run BT scan
DBUS_RECONNECT_DELAY = 5.0  # Seconds before reconnecting a dropped D-Bus listener
DBUS_EVDEV_SETTLE = 1.0     # Seconds to wait for evdev nodes after BLE reconnect signal

_MAC_PATTERN = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')

# D-Bus match rule: only PropertiesChanged signals from BlueZ Device1 interfaces
_DBUS_MATCH_RULE = (
    "type='signal',"
    "interface='org.freedesktop.DBus.Properties',"
    "member='PropertiesChanged',"
    "arg0='org.bluez.Device1'"
)


class BtRemoteController:
    """
    Bluetooth HID remote controller.

    Detects BT HID input devices (e.g. ANTICATER VK-01), reads evdev
    events, and dispatches volume/playback actions. Automatically discovers
    and pairs matching BT devices in the background. Relies on BlueZ
    auto-connect for trusted devices — no aggressive reconnect polling,
    allowing the remote to deep-sleep and preserve battery.
    """

    def __init__(self, volume_service, state_machine, settings_service):
        self.volume_service = volume_service
        self.state_machine = state_machine
        self.settings_service = settings_service

        self.running = False
        self.enabled = False
        self.device_name_filter = DEFAULT_DEVICE_FILTER
        self.key_map: Dict[str, str] = dict(DEFAULT_KEY_MAP)

        # Tracked devices: path -> task/info
        self._monitored_paths: Set[str] = set()
        self._monitor_tasks: Dict[str, asyncio.Task] = {}
        self._device_info: Dict[str, dict] = {}  # path -> {name, address}
        self._scan_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None
        self._dbus_listener_task: Optional[asyncio.Task] = None
        self._dbus_reconnect_queue: asyncio.Queue = asyncio.Queue(maxsize=1)

        # Volume accumulator (shared with rotary encoder)
        self._volume = VolumeAccumulator(volume_service)

        # Playback dispatch (multi-click → play/pause, next, prev)
        self._dispatcher = PlaybackDispatcher(state_machine)

        self._config_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._discovering = False

    async def initialize(self) -> bool:
        """Initialize the BT remote controller."""
        if not EVDEV_AVAILABLE:
            logger.info("evdev not installed — BT remote controller disabled")
            return True  # Not a failure, just unavailable

        await self._load_config_from_settings()

        if not self.enabled:
            logger.info("BT remote controller disabled in settings")
            await self._disconnect_matching_devices()
            return True

        self._start_scanning()
        logger.info("BT remote controller initialized (filter=%s)", self.device_name_filter)
        return True

    def _start_scanning(self):
        """Start evdev scan, BT discovery, and D-Bus reconnect listener."""
        self.running = True
        self._scan_task = asyncio.create_task(self._periodic_scan())
        self._discovery_task = asyncio.create_task(self._periodic_discovery())
        if DBUS_AVAILABLE:
            self._dbus_listener_task = asyncio.create_task(self._run_dbus_listener())

    async def _stop_scanning(self):
        """Stop all scanning, monitoring, and D-Bus listener."""
        self.running = False

        await self._dispatcher.cleanup()
        await self._volume.cleanup()

        for task_ref in (self._scan_task, self._discovery_task, self._dbus_listener_task):
            if task_ref and not task_ref.done():
                task_ref.cancel()
        self._scan_task = None
        self._discovery_task = None
        self._dbus_listener_task = None

        # Drain pending reconnect events
        while not self._dbus_reconnect_queue.empty():
            try:
                self._dbus_reconnect_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        for task in self._monitor_tasks.values():
            if not task.done():
                task.cancel()
        self._monitor_tasks.clear()
        self._monitored_paths.clear()
        self._device_info.clear()

    async def cleanup(self):
        """Clean up resources."""
        await self._stop_scanning()
        logger.info("BT remote controller cleaned up")

    # ========================================================================
    # CONFIGURATION
    # ========================================================================

    async def _load_config_from_settings(self):
        """Load configuration from settings.json."""
        config = await self.settings_service.get_setting('hardware.bt_remote')
        if not config:
            return

        self.enabled = config.get('enabled', False)
        self.device_name_filter = config.get('device_name_filter', DEFAULT_DEVICE_FILTER)

        key_map = config.get('key_map', {})
        if key_map and isinstance(key_map, dict):
            self.key_map = key_map

    async def update_config(self, partial_config: dict):
        """Update configuration partially and persist."""
        async with self._config_lock:
            if 'enabled' in partial_config:
                self.enabled = bool(partial_config['enabled'])
            if 'device_name_filter' in partial_config:
                self.device_name_filter = str(partial_config['device_name_filter'])
            if 'key_map' in partial_config and isinstance(partial_config['key_map'], dict):
                self.key_map = partial_config['key_map']

            config = {
                'enabled': self.enabled,
                'device_name_filter': self.device_name_filter,
                'key_map': self.key_map
            }
            await self.settings_service.set_setting('hardware.bt_remote', config)

            # Handle enable/disable transitions
            transitioned = False
            if self.enabled and not self.running:
                self._start_scanning()
                transitioned = True
            elif not self.enabled and self.running:
                # Stop before disconnecting: _stop_scanning() clears `running`,
                # so the monitor tasks that die when BlueZ drops the evdev nodes
                # cannot race a half-broadcast against the explicit one below.
                await self._stop_scanning()
                await self._disconnect_matching_devices()
                transitioned = True

        await self.state_machine.broadcast(
            BtRemoteConfigChanged(config=BtRemoteConfig(**config))
        )
        if transitioned:
            # The transition changed the monitored set, and _stop_scanning()
            # notifies nobody. The UI set `discovering`/`connected` optimistically
            # when it called us and has no other way to learn the real state.
            await self._broadcast_status()

    def get_status(self) -> dict:
        """Return current controller status (one entry per physical device)."""
        seen_macs = set()
        connected = []
        for path in self._monitored_paths:
            info = self._device_info.get(path, {"name": "unknown", "address": ""})
            mac = info.get("address", "").upper()
            if mac and mac in seen_macs:
                continue
            seen_macs.add(mac)
            connected.append({"path": path, **info})

        return {
            "available": EVDEV_AVAILABLE,
            "enabled": self.enabled,
            "running": self.running,
            "discovering": self._discovering,
            "connected_devices": connected,
            "device_name_filter": self.device_name_filter,
            "key_map": self.key_map
        }

    def get_device_info(self) -> list[dict]:
        """Return monitored devices (one entry per unique MAC) for battery polling."""
        seen_macs = set()
        devices = []
        for path in list(self._monitored_paths):
            info = self._device_info.get(path, {})
            address = info.get("address", "")
            if not address or address.upper() in seen_macs:
                continue
            seen_macs.add(address.upper())
            devices.append({
                "path": path,
                "address": address,
                "name": info.get("name", ""),
            })
        return devices

    async def _broadcast_status(self):
        """Broadcast current connection status via WebSocket."""
        status = self.get_status()
        paired = await self.is_paired()
        await self.state_machine.broadcast(BtRemoteStatusChanged(
            connected_devices=status["connected_devices"],
            discovering=status["discovering"],
            paired=paired,
        ))

    # ========================================================================
    # BATTERY (on-demand D-Bus read)
    # ========================================================================

    @staticmethod
    def _mac_to_dbus_path(address: str) -> str:
        """Convert MAC address to BlueZ D-Bus object path."""
        return "/org/bluez/hci0/dev_" + address.upper().replace(":", "_")

    async def read_battery_level(self, address: str) -> Optional[int]:
        """Read battery level for a connected device via D-Bus (one-shot).

        Called on-demand from the API, not continuously in the background.
        """
        if not DBUS_AVAILABLE:
            return None

        mac = address.upper()
        dev_path = self._mac_to_dbus_path(mac)
        bus = None
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspection = await bus.introspect("org.bluez", dev_path)
            obj = bus.get_proxy_object("org.bluez", dev_path, introspection)
            props = obj.get_interface("org.freedesktop.DBus.Properties")
            variant = await props.call_get("org.bluez.Battery1", "Percentage")
            return variant.value
        except Exception as e:
            logger.debug("Battery read failed for %s: %s", mac, e)
            return None
        finally:
            if bus:
                with contextlib.suppress(Exception):
                    bus.disconnect()

    # ========================================================================
    # D-BUS RECONNECT LISTENER
    # ========================================================================

    async def _run_dbus_listener(self):
        """Maintain a persistent D-Bus connection to detect BLE reconnections instantly.

        Listens for PropertiesChanged signals on org.bluez.Device1 interfaces.
        When a device's Connected property becomes True, triggers an immediate
        evdev scan (after a short settle delay for kernel node creation).
        Auto-reconnects if the D-Bus connection drops.
        """
        while self.running:
            try:
                await self._connect_dbus_listener()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.info("D-Bus listener error: %s — reconnecting in %.0fs", e, DBUS_RECONNECT_DELAY)
                await asyncio.sleep(DBUS_RECONNECT_DELAY)

    async def _connect_dbus_listener(self):
        """Run a single D-Bus listener session until the bus disconnects."""
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            # Register match rule for BlueZ device property changes
            reply = await bus.call(Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[_DBUS_MATCH_RULE],
            ))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"D-Bus AddMatch failed: {reply.body}")

            bus.add_message_handler(self._on_dbus_message)
            logger.info("D-Bus PropertiesChanged listener active")

            disconnect_task = asyncio.create_task(bus.wait_for_disconnect())
            get_task = None
            try:
                while self.running:
                    get_task = asyncio.create_task(self._dbus_reconnect_queue.get())
                    done, _ = await asyncio.wait(
                        {disconnect_task, get_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if disconnect_task in done:
                        get_task.cancel()
                        return  # Bus dropped — outer loop will reconnect
                    # Reconnect event received — scan for new evdev nodes
                    get_task.result()
                    await asyncio.sleep(DBUS_EVDEV_SETTLE)
                    if self.running:
                        await self._scan_devices()
            finally:
                disconnect_task.cancel()
                if get_task and not get_task.done():
                    get_task.cancel()
        finally:
            with contextlib.suppress(Exception):
                bus.disconnect()

    def _on_dbus_message(self, msg):
        """Handle incoming D-Bus messages (synchronous callback).

        Filters for PropertiesChanged signals where Connected becomes True
        on BlueZ device objects, then enqueues a reconnect event.
        """
        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.member != "PropertiesChanged":
            return
        if not msg.path or not msg.path.startswith("/org/bluez/hci0/dev_"):
            return

        body = msg.body
        if not body or len(body) < 2:
            return
        if body[0] != "org.bluez.Device1":
            return

        changed_props = body[1]
        connected_variant = changed_props.get("Connected")
        if connected_variant is None or not connected_variant.value:
            return

        logger.debug("D-Bus: BLE device connected at %s", msg.path)
        with contextlib.suppress(asyncio.QueueFull):
            self._dbus_reconnect_queue.put_nowait(msg.path)

    # ========================================================================
    # BLUETOOTHCTL HELPERS
    # ========================================================================

    async def _get_matching_devices(self, *device_args) -> list[tuple[str, str]]:
        """Return (address, name) pairs for BT devices matching the name filter.

        Args:
            *device_args: Arguments for `bluetoothctl devices` (e.g. "Connected", "Paired", "Blocked").
                          If empty, lists all known devices.
        """
        output = await self._run_bluetoothctl("devices", *device_args, capture_stdout=True)
        matches = []
        for line in output.splitlines():
            if not line.startswith("Device "):
                continue
            parts = line.split(" ", 2)
            if len(parts) < 3:
                continue
            address, name = parts[1], parts[2]
            if not _MAC_PATTERN.match(address):
                continue
            # An empty filter means "match nothing", never "match everything":
            # every consumer of this list is destructive (disconnect, remove bond,
            # trust+pair), so a filter that went blank must strand them, not aim
            # them at the A2DP phone.
            if not self.device_name_filter or self.device_name_filter.upper() not in name.upper():
                continue
            matches.append((address, name))
        return matches

    async def is_paired(self) -> bool:
        """Whether a matching remote is bonded in BlueZ.

        This is the durable "a remote is set up" signal: it stays true while
        the remote sleeps and disconnects (unlike the transient connected
        state), so the UI uses it to offer the "unpair" action.
        """
        return bool(await self._get_matching_devices("Paired"))

    async def _disconnect_matching_devices(self):
        """Disconnect BT remote devices matching the name filter, KEEPING their
        BlueZ bond so they reconnect automatically later (e.g. after the
        controller is disabled then re-enabled). Bond removal is done only by
        forget_remote() — the explicit "unpair" action.

        Only affects devices whose name matches self.device_name_filter,
        leaving other BT connections (e.g. A2DP audio sources) untouched.
        """
        for address, name in await self._get_matching_devices("Connected"):
            logger.info("Disconnecting BT remote device: %s (%s)", name, address)
            await self._run_bluetoothctl("disconnect", address)

    async def _remove_matching_bonds(self):
        """Remove all paired matching devices from BlueZ (clear stale bonds)."""
        for address, name in await self._get_matching_devices("Paired"):
            logger.info("Removing bond: %s (%s)", name, address)
            await self._run_bluetoothctl("remove", address)

    async def _is_bt_connected(self, address: str) -> bool:
        """Check if a BT address is currently connected in BlueZ."""
        output = await self._run_bluetoothctl("devices", "Connected", capture_stdout=True)
        return address.upper() in output.upper()

    @staticmethod
    async def _run_bluetoothctl(
        *args,
        stdin_cmds: Optional[str] = None,
        capture_stdout: bool = False,
        timeout: int = 10,
    ) -> Union[str, bool]:
        """Execute a bluetoothctl command. Returns stdout (str) or success (bool)."""
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", *args,
            stdin=asyncio.subprocess.PIPE if stdin_cmds else None,
            stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=stdin_cmds.encode() if stdin_cmds else None),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return "" if capture_stdout else False
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return stdout.decode() if capture_stdout else proc.returncode == 0

    # ========================================================================
    # DEVICE SCANNING (evdev)
    # ========================================================================

    async def _periodic_scan(self):
        """Periodically scan for new BT HID devices in /dev/input/."""
        # Initial delay to let stale kernel evdev nodes disappear after re-enable
        await asyncio.sleep(2)
        while self.running:
            try:
                await self._scan_devices()
            except Exception as e:
                logger.error("Error scanning BT HID devices: %s", e)
            await asyncio.sleep(SCAN_INTERVAL)

    async def _scan_devices(self):
        """Scan /dev/input/ for matching BT HID devices.

        Uses a lock to prevent concurrent scans (e.g. from _run_discovery and _periodic_scan).
        BLE HID devices create multiple evdev nodes per connection — we monitor all of them
        (since only one carries the volume key events) but report a single device in status.
        """
        async with self._scan_lock:
            if not EVDEV_AVAILABLE or not self.running:
                return

            try:
                all_paths = evdev.list_devices()
            except OSError as e:
                # /dev/input unreadable or gone — expected on a dev host.
                logger.debug("Error listing input devices: %s", e)
                return
            except Exception as e:
                logger.warning("Unexpected error listing input devices: %s", e)
                return

            # Clean up disconnected devices
            active_paths = set(all_paths)
            disconnected = False
            for path in list(self._monitored_paths):
                if path not in active_paths:
                    self._drop_node(path)
                    logger.info("BT HID device disconnected: %s", path)
                    disconnected = True
            if disconnected and self.running:
                await self._broadcast_status()

            # Track which MACs we already monitor (for status broadcast dedup)
            macs_before = self._monitored_macs()

            # Check for new matching devices
            for path in all_paths:
                if path in self._monitored_paths:
                    continue

                try:
                    device = evdev.InputDevice(path)
                except OSError as e:
                    # The node vanished between list_devices() and here, or we
                    # may not open it — both are ordinary, both are frequent.
                    logger.debug("Error opening device %s: %s", path, e)
                    continue
                except Exception as e:
                    logger.warning("Unexpected error opening device %s: %s", path, e)
                    continue

                try:
                    if not self._is_bt_hid_device(device):
                        device.close()
                        continue
                    # Verify the device is actually connected in BlueZ
                    if device.uniq and not await self._is_bt_connected(device.uniq):
                        logger.debug("Ignoring stale evdev node: %s (%s)", device.name, device.uniq)
                        device.close()
                        continue
                    self._monitored_paths.add(device.path)
                    self._device_info[device.path] = {"name": device.name, "address": device.uniq or ""}
                    task = asyncio.create_task(self._monitor_device(device))
                    self._monitor_tasks[device.path] = task
                    logger.info("BT HID device found: %s (%s) at %s", device.name, device.uniq, device.path)
                except OSError as e:
                    device.close()
                    logger.debug("Error checking device %s: %s", path, e)
                except Exception as e:
                    # Anything else is a fault in our own matching, not a device
                    # that went away — at debug it silently ignored every remote.
                    device.close()
                    logger.warning("Unexpected error checking device %s: %s", path, e)

            # Broadcast only if a new MAC appeared (not for each additional evdev node)
            new_macs = self._monitored_macs() - macs_before
            if new_macs:
                await self._broadcast_status()

    def _monitored_macs(self) -> set:
        """Return set of unique MAC addresses currently monitored."""
        return {
            info["address"].upper()
            for info in self._device_info.values()
            if info.get("address")
        }

    def _drop_node(self, path: str):
        """Forget one evdev node: clear its bookkeeping and cancel its monitor task.

        Not usable from _monitor_device's own teardown, where the task being
        dropped is the caller and cancelling it would abort that teardown.
        """
        self._device_info.pop(path, None)
        self._monitored_paths.discard(path)
        task = self._monitor_tasks.pop(path, None)
        if task and not task.done():
            task.cancel()

    def _cancel_all_for_mac(self, address: str):
        """Cancel all monitor tasks for a given MAC (BLE HID has multiple evdev nodes)."""
        mac = address.upper()
        for path in list(self._monitored_paths):
            info = self._device_info.get(path, {})
            if info.get("address", "").upper() == mac:
                self._drop_node(path)

    def _is_bt_hid_device(self, device) -> bool:
        """Check if a device is a matching BT HID device."""
        capabilities = device.capabilities(verbose=False)
        if evdev.ecodes.EV_KEY not in capabilities:
            return False

        if not device.uniq:
            return False

        if self.device_name_filter:
            if self.device_name_filter.upper() not in device.name.upper():
                return False

        supported_keys = set(capabilities.get(evdev.ecodes.EV_KEY, []))
        configured_keycodes = {int(k) for k in self.key_map.keys()}
        if not supported_keys & configured_keycodes:
            return False

        return True

    # ========================================================================
    # AUTO-DISCOVERY + AUTO-PAIR (bluetoothctl)
    # ========================================================================

    async def trigger_discovery(self) -> dict:
        """Trigger an immediate reconnect or discovery + pair attempt."""
        if not EVDEV_AVAILABLE:
            return {"status": "error", "message": "evdev not available"}
        if not self.enabled:
            return {"status": "error", "message": "BT remote is disabled"}
        if self._monitored_paths:
            # Nothing else broadcasts on this path, and the UI is waiting on a
            # status to clear the `discovering` it set before calling.
            await self._broadcast_status()
            return {"status": "already_connected", "message": "Device already connected"}

        logger.info("Manual BT discovery triggered")

        # Try lightweight reconnect first for paired devices
        for address, name in await self._get_matching_devices("Paired"):
            if await self._run_bluetoothctl("connect", address, timeout=8):
                logger.info("Reconnected paired device: %s (%s)", name, address)
                await asyncio.sleep(1)
                await self._scan_devices()
                if self._monitored_paths:
                    return {"status": "success", "message": "Device reconnected"}

        # No paired device responded — run full discovery
        await self._auto_discover_and_pair()

        if self._monitored_paths:
            return {"status": "success", "message": "Device found and connected"}
        return {"status": "not_found", "message": "No matching device found"}

    async def forget_remote(self) -> dict:
        """Disconnect and remove the BlueZ bond for all matching remotes.

        Clears the durable pairing so a different remote can be paired on the
        next discovery. Safe to call whether or not a remote is currently
        connected (a sleeping remote keeps its bond until removed here).
        """
        if not EVDEV_AVAILABLE:
            return {"status": "error", "message": "evdev not available"}

        logger.info("Unpairing BT remote (disconnect + remove bond)")
        await self._disconnect_matching_devices()
        await self._remove_matching_bonds()
        # Refresh evdev monitoring so a now-disconnected device leaves the
        # status, then broadcast the new (unpaired) state — covers both the
        # connected case (scan drops the node) and the asleep case (no node,
        # explicit broadcast still reflects paired=False).
        if self.running:
            await self._scan_devices()
        await self._broadcast_status()
        return {"status": "success", "message": "Remote unpaired"}

    async def _periodic_discovery(self):
        """Periodically attempt reconnection or full discovery when no device is active.

        Runs every DISCOVERY_INTERVAL seconds when no device is connected.
        If paired devices exist, attempts a single reconnect (for startup / after
        the device wakes from sleep). If no paired device exists, runs a full
        BT scan+pair. Does NOT poll aggressively — the device's own BLE advertising
        on user interaction triggers BlueZ auto-connect for trusted devices.

        On startup, uses a shorter retry interval (15s) for the first few attempts
        to handle BlueZ not being fully ready yet.
        """
        await asyncio.sleep(6)
        boot_retries = 3

        while self.running:
            try:
                if not self._monitored_paths:
                    paired = await self._get_matching_devices("Paired")
                    if paired:
                        address, name = paired[0]
                        if await self._run_bluetoothctl("connect", address, timeout=8):
                            logger.info("Reconnected paired device: %s (%s)", name, address)
                            await asyncio.sleep(1)
                            await self._scan_devices()
                    else:
                        await self._auto_discover_and_pair()
            except Exception as e:
                logger.error("Error in BT auto-discovery: %s", e)

            # Shorter interval on startup to recover from BlueZ not yet ready
            if boot_retries > 0 and not self._monitored_paths:
                boot_retries -= 1
                await asyncio.sleep(15)
            else:
                boot_retries = 0
                await asyncio.sleep(DISCOVERY_INTERVAL)

    async def _auto_discover_and_pair(self):
        """Scan for matching BT devices and auto-pair them."""
        if self._discovering:
            return
        self._discovering = True
        await self._broadcast_status()
        try:
            await self._run_discovery()
        finally:
            self._discovering = False
            await self._broadcast_status()

    async def _run_discovery(self):
        """Discover and pair a new matching BT device via full scan+pair sequence.

        Only called when no paired devices exist. Reconnection of already-paired
        devices is handled by the caller (_periodic_discovery).
        """
        logger.debug("Starting BT discovery for '%s' devices...", self.device_name_filter)

        # Phase 1: BT scan (separate session is fine, populates daemon cache)
        scan_proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            scan_proc.stdin.write(b"scan on\n")
            await scan_proc.stdin.drain()
            await asyncio.sleep(DISCOVERY_DURATION)
            scan_proc.stdin.write(b"scan off\nquit\n")
            await scan_proc.stdin.drain()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(scan_proc.wait(), timeout=5)
        except asyncio.CancelledError:
            raise
        finally:
            if scan_proc.returncode is None:
                scan_proc.kill()
                await scan_proc.wait()

        if self._monitored_paths:
            return  # Device found by periodic_scan during discovery

        # Phase 2: find matching device from daemon cache
        matches = await self._get_matching_devices()
        if not matches:
            logger.debug("No matching BT device found during discovery")
            return
        address, name = matches[0]
        logger.info("Discovered matching BT device: %s (%s)", name, address)

        # Phase 3: trust + pair + connect in single session.
        # BLE devices may become unavailable between separate bluetoothctl calls,
        # so all pairing commands must run in the same process.
        pair_proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            pair_proc.stdin.write(f"trust {address}\n".encode())
            await pair_proc.stdin.drain()
            await asyncio.sleep(1)

            pair_proc.stdin.write(f"pair {address}\n".encode())
            await pair_proc.stdin.drain()
            await asyncio.sleep(10)  # Wait for BLE pairing to complete

            pair_proc.stdin.write(f"connect {address}\nquit\n".encode())
            await pair_proc.stdin.drain()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(pair_proc.wait(), timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error in BT pairing: %s", e)
        finally:
            if pair_proc.returncode is None:
                pair_proc.kill()
                await pair_proc.wait()

        # Verify result using BlueZ daemon state
        if await self._is_bt_connected(address):
            logger.info("Auto-paired BT device: %s (%s)", name, address)
            await asyncio.sleep(1)
            await self._scan_devices()
        else:
            logger.warning("Pairing/connect failed for %s (%s)", name, address)

    # ========================================================================
    # EVENT MONITORING
    # ========================================================================

    async def _monitor_device(self, device):
        """Monitor a single evdev device for key events."""
        device_name = device.name
        device_path = device.path
        device_mac = (self._device_info.get(device_path, {}).get("address", "") or device.uniq or "").upper()
        device_disconnected = False
        logger.info("Monitoring BT HID device: %s", device_name)

        try:
            async for event in device.async_read_loop():
                if not self.running:
                    break

                # Only process key press events (value==1)
                if event.type != evdev.ecodes.EV_KEY or event.value != 1:
                    continue

                keycode_str = str(event.code)
                action = self.key_map.get(keycode_str)
                if not action:
                    continue

                logger.debug("BT HID key: code=%d -> action=%s", event.code, action)

                if action == "click":
                    await self._dispatcher.on_click()
                else:
                    await self._dispatch_action(action)

        except OSError as e:
            logger.info("BT HID device disconnected: %s (%s)", device_name, e)
            device_disconnected = True
        except asyncio.CancelledError:
            logger.debug("Monitor cancelled for %s", device_name)
        except Exception as e:
            logger.error("Error monitoring BT HID device %s: %s", device_name, e)
            device_disconnected = True
        finally:
            info = self._device_info.pop(device_path, {})
            address = info.get("address", "")
            mac = (address or device_mac).upper()
            self._monitored_paths.discard(device_path)
            self._monitor_tasks.pop(device_path, None)
            with contextlib.suppress(Exception):
                device.close()

            if device_disconnected and mac:
                # BLE HID creates multiple evdev nodes per connection.
                # When one dies, the others are stale — cancel them all.
                self._cancel_all_for_mac(mac)

            if self.running:
                await self._broadcast_status()

    # ========================================================================
    # ACTION DISPATCH
    # ========================================================================

    async def _dispatch_action(self, action: str):
        """Dispatch a non-click action (volume only)."""
        try:
            if action in ("volume_up", "volume_down"):
                step = self.volume_service.volume_config.step_bt_remote_db
                self._volume.accumulate(step if action == "volume_up" else -step)
            else:
                logger.debug("Unknown BT remote action: %s", action)

        except Exception as e:
            logger.error("Error dispatching BT remote action '%s': %s", action, e)

