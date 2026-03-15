# backend/hardware/bt_remote.py
"""
Bluetooth HID remote controller for volume and playback control.

Supports ANTICATER VK1 Mini and similar BT HID devices that send
standard Consumer Control keycodes (KEY_VOLUMEUP, KEY_VOLUMEDOWN, KEY_MUTE).
Completely independent from the Bluetooth A2DP audio source.

Features:
- Automatic detection of BT HID devices via evdev
- Automatic Bluetooth discovery and pairing of matching devices
- Configurable key mapping (keycodes to actions)
- Multi-click detection on click action (1=play/pause, 2=next, 3=prev)
- Volume control via VolumeService
- Playback control via state_machine plugins
"""
import asyncio
import logging
import re
import time
from typing import Dict, Optional, Set

from backend.core.models.audio_state import AudioSource

try:
    import evdev
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

try:
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default configuration for ANTICATER VK1 Mini
DEFAULT_KEY_MAP = {
    "115": "volume_up",     # KEY_VOLUMEUP (rotation CW)
    "114": "volume_down",   # KEY_VOLUMEDOWN (rotation CCW)
    "113": "click",         # KEY_MUTE -> multi-click detection
}
DEFAULT_DEVICE_FILTER = "ANTICATER"
MULTI_CLICK_WINDOW = 0.4   # 400ms window for multi-click grouping
SCAN_INTERVAL = 5.0         # Seconds between evdev device scans
DISCOVERY_INTERVAL = 30.0   # Seconds between BT discovery attempts
DISCOVERY_DURATION = 5      # Seconds to run BT scan
IDLE_COOLDOWN_BASE = 30.0   # Seconds before allowing reconnection after idle disconnect
IDLE_COOLDOWN_MAX = 120.0   # Maximum cooldown duration (2 minutes)

_MAC_PATTERN = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')


class BtRemoteController:
    """
    Bluetooth HID remote controller.

    Detects BT HID input devices (e.g. ANTICATER VK1 Mini), reads evdev
    events, and dispatches volume/playback actions. Automatically discovers
    and pairs matching BT devices in the background.
    """

    def __init__(self, volume_service, state_machine, settings_service):
        self.volume_service = volume_service
        self.state_machine = state_machine
        self.settings_service = settings_service

        self.running = False
        self.enabled = True
        self.device_name_filter = DEFAULT_DEVICE_FILTER
        self.key_map: Dict[str, str] = dict(DEFAULT_KEY_MAP)

        # Tracked devices: path -> task/info
        self._monitored_paths: Set[str] = set()
        self._monitor_tasks: Dict[str, asyncio.Task] = {}
        self._device_info: Dict[str, dict] = {}  # path -> {name, address}
        self._scan_task: Optional[asyncio.Task] = None
        self._discovery_task: Optional[asyncio.Task] = None

        # Idle disconnect tracking (reconnection cooldown)
        self._idle_disconnect_count: Dict[str, int] = {}  # MAC (upper) -> consecutive idle count
        self._cooldown_until: Dict[str, float] = {}  # MAC (upper) -> monotonic expiry time
        self._mac_had_key_event: Dict[str, bool] = {}  # MAC (upper) -> any key event this session

        # Volume accumulator (batch rapid events like rotary encoder)
        self._volume_accumulator = 0.0
        self._volume_processor_running = False
        self._volume_processor_task: Optional[asyncio.Task] = None

        # Multi-click state
        self._click_count = 0
        self._click_timer: Optional[asyncio.TimerHandle] = None

        # Locks
        self._config_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._discovering = False

    async def initialize(self) -> bool:
        """Initialize the BT remote controller."""
        if not EVDEV_AVAILABLE:
            logger.warning("evdev not installed — BT remote controller disabled")
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
        """Start evdev scan and BT discovery loops."""
        self.running = True
        self._scan_task = asyncio.create_task(self._periodic_scan())
        self._discovery_task = asyncio.create_task(self._periodic_discovery())

    def _stop_scanning(self):
        """Stop all scanning and monitoring."""
        self.running = False

        if self._click_timer:
            self._click_timer.cancel()
            self._click_timer = None

        if self._volume_processor_task and not self._volume_processor_task.done():
            self._volume_processor_task.cancel()
        self._volume_processor_task = None
        self._volume_accumulator = 0.0
        self._volume_processor_running = False

        for task_ref in (self._scan_task, self._discovery_task):
            if task_ref and not task_ref.done():
                task_ref.cancel()
        self._scan_task = None
        self._discovery_task = None

        for task in self._monitor_tasks.values():
            if not task.done():
                task.cancel()
        self._monitor_tasks.clear()
        self._monitored_paths.clear()
        self._device_info.clear()
        self._idle_disconnect_count.clear()
        self._cooldown_until.clear()
        self._mac_had_key_event.clear()

    def cleanup(self):
        """Clean up resources."""
        self._stop_scanning()
        logger.info("BT remote controller cleaned up")

    # ========================================================================
    # CONFIGURATION
    # ========================================================================

    async def _load_config_from_settings(self):
        """Load configuration from settings.json."""
        config = await self.settings_service.get_setting('plugins.bt_remote')
        if not config:
            return

        self.enabled = config.get('enabled', True)
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
            await self.settings_service.set_setting('plugins.bt_remote', config)

            # Handle enable/disable transitions
            if self.enabled and not self.running:
                self._start_scanning()
            elif not self.enabled and self.running:
                await self._disconnect_matching_devices()
                self._stop_scanning()

        await self.state_machine.broadcast_event(
            "settings", "bt_remote_config_changed",
            {"source": "settings", "config": config}
        )

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

    async def _broadcast_status(self):
        """Broadcast current connection status via WebSocket."""
        status = self.get_status()
        await self.state_machine.broadcast_event(
            "settings", "bt_remote_status_changed",
            {"source": "settings",
             "connected_devices": status["connected_devices"],
             "discovering": status["discovering"]}
        )

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
                try:
                    bus.disconnect()
                except Exception:
                    pass

    # ========================================================================
    # RECONNECTION COOLDOWN
    # ========================================================================

    def _is_in_cooldown(self, mac: str) -> bool:
        """Check if a MAC address is in reconnection cooldown."""
        until = self._cooldown_until.get(mac.upper())
        if until is None:
            return False
        if time.monotonic() >= until:
            self._cooldown_until.pop(mac.upper(), None)
            return False
        return True

    def _has_any_cooldown(self) -> bool:
        """Check if any MAC is currently in cooldown."""
        now = time.monotonic()
        expired = [mac for mac, until in self._cooldown_until.items() if now >= until]
        for mac in expired:
            self._cooldown_until.pop(mac)
        return bool(self._cooldown_until)

    def _clear_all_cooldowns(self):
        """Clear all cooldown state (e.g. on manual discovery)."""
        self._idle_disconnect_count.clear()
        self._cooldown_until.clear()
        self._mac_had_key_event.clear()

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
            if self.device_name_filter and self.device_name_filter.upper() not in name.upper():
                continue
            matches.append((address, name))
        return matches

    async def _disconnect_matching_devices(self):
        """Disconnect and remove BT devices matching the device name filter.

        Only affects devices whose name matches self.device_name_filter,
        leaving other BT connections (e.g. A2DP audio sources) untouched.
        """
        for address, name in await self._get_matching_devices("Connected"):
            logger.info("Disconnecting BT remote device: %s (%s)", name, address)
            await self._run_bluetoothctl("disconnect", address)
        await self._remove_matching_bonds()

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
    ) -> "str | bool":
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
            except Exception as e:
                logger.debug("Error listing input devices: %s", e)
                return

            # Clean up disconnected devices
            active_paths = set(all_paths)
            disconnected = False
            for path in list(self._monitored_paths):
                if path not in active_paths:
                    self._device_info.pop(path, None)
                    self._monitored_paths.discard(path)
                    task = self._monitor_tasks.pop(path, None)
                    if task and not task.done():
                        task.cancel()
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
                except Exception as e:
                    logger.debug("Error opening device %s: %s", path, e)
                    continue

                try:
                    if not self._is_bt_hid_device(device):
                        device.close()
                        continue
                    # Skip devices in reconnection cooldown (idle disconnect backoff)
                    if device.uniq and self._is_in_cooldown(device.uniq):
                        logger.debug("Skipping device in cooldown: %s (%s)", device.name, device.uniq)
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
                except Exception as e:
                    device.close()
                    logger.debug("Error checking device %s: %s", path, e)

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

    def _cancel_all_for_mac(self, address: str):
        """Cancel all monitor tasks for a given MAC (BLE HID has multiple evdev nodes)."""
        mac = address.upper()
        for path in list(self._monitored_paths):
            info = self._device_info.get(path, {})
            if info.get("address", "").upper() == mac:
                self._device_info.pop(path, None)
                self._monitored_paths.discard(path)
                task = self._monitor_tasks.pop(path, None)
                if task and not task.done():
                    task.cancel()

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
        """Trigger an immediate discovery + pair attempt. Returns result dict."""
        if not EVDEV_AVAILABLE:
            return {"status": "error", "message": "evdev not available"}
        if not self.enabled:
            return {"status": "error", "message": "BT remote is disabled"}
        if self._monitored_paths:
            return {"status": "already_connected", "message": "Device already connected"}

        # Clear cooldowns on manual discovery
        self._clear_all_cooldowns()
        logger.info("Manual BT discovery triggered")
        await self._auto_discover_and_pair()

        if self._monitored_paths:
            return {"status": "success", "message": "Device found and connected"}
        return {"status": "not_found", "message": "No matching device found"}

    async def _periodic_discovery(self):
        """Periodically discover and auto-pair matching BT devices."""
        # Wait for periodic_scan to run first (2s delay + scan time)
        await asyncio.sleep(4)

        while self.running:
            try:
                # Skip discovery if devices are in idle cooldown (let them sleep)
                if not self._monitored_paths and not self._has_any_cooldown():
                    await self._auto_discover_and_pair()
            except Exception as e:
                logger.error("Error in BT auto-discovery: %s", e)
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
        """Reconnect paired devices or discover and pair new ones."""
        # Device already found by periodic_scan — nothing to do
        if self._monitored_paths:
            return

        # Try reconnecting already-paired devices first (fast path after reboot)
        for address, name in await self._get_matching_devices("Paired"):
            logger.info("Reconnecting paired device: %s (%s)", name, address)
            if await self._run_bluetoothctl("connect", address, timeout=10):
                logger.info("Reconnected paired device: %s (%s)", name, address)
                await asyncio.sleep(1)
                await self._scan_devices()
                return

        # No paired devices — scan, then trust+pair+connect in a single
        # bluetoothctl session to keep the BLE device in BlueZ cache.
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
            try:
                await asyncio.wait_for(scan_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
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
            try:
                await asyncio.wait_for(pair_proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
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

                # User is active — reset idle tracking for this MAC (shared across sibling nodes)
                if device_mac and not self._mac_had_key_event.get(device_mac):
                    self._mac_had_key_event[device_mac] = True
                    self._idle_disconnect_count.pop(device_mac, None)
                    self._cooldown_until.pop(device_mac, None)

                logger.debug("BT HID key: code=%d -> action=%s", event.code, action)

                if action == "click":
                    await self._on_click_event()
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
            try:
                device.close()
            except Exception:
                pass

            if device_disconnected and mac:
                # BLE HID creates multiple evdev nodes per connection.
                # When one dies, the others are stale — cancel them all.
                self._cancel_all_for_mac(mac)

                # Track idle disconnections and apply exponential cooldown
                # Uses shared MAC-level flag so sibling evdev nodes don't cause false cooldowns
                if not self._mac_had_key_event.pop(mac, False):
                    count = self._idle_disconnect_count.get(mac, 0) + 1
                    self._idle_disconnect_count[mac] = count
                    cooldown = min(IDLE_COOLDOWN_BASE * (2 ** (count - 1)), IDLE_COOLDOWN_MAX)
                    self._cooldown_until[mac] = time.monotonic() + cooldown
                    logger.info("Idle disconnect #%d for %s, cooldown %.0fs", count, mac, cooldown)

            if self.running:
                await self._broadcast_status()

    # ========================================================================
    # MULTI-CLICK DETECTION
    # ========================================================================

    async def _on_click_event(self):
        """Handle a click event — accumulate clicks within the multi-click window."""
        self._click_count += 1

        if self._click_timer:
            self._click_timer.cancel()

        loop = asyncio.get_running_loop()
        self._click_timer = loop.call_later(
            MULTI_CLICK_WINDOW,
            lambda: asyncio.ensure_future(self._resolve_clicks())
        )

    async def _resolve_clicks(self):
        """Resolve accumulated clicks after the multi-click window expires."""
        count = self._click_count
        self._click_count = 0
        self._click_timer = None

        if not self.running:
            return

        if count == 1:
            await self._dispatch_action("play_pause")
        elif count == 2:
            await self._dispatch_action("next_track")
        elif count >= 3:
            await self._dispatch_action("previous_track")

    # ========================================================================
    # ACTION DISPATCH
    # ========================================================================

    async def _dispatch_action(self, action: str):
        """Dispatch an action to the appropriate service."""
        try:
            if action in ("volume_up", "volume_down"):
                step = self.volume_service.volume_config.step_bt_remote_db
                self._volume_accumulator += step if action == "volume_up" else -step
                if not self._volume_processor_running:
                    self._volume_processor_task = asyncio.create_task(self._process_volume())

            elif action == "play_pause":
                await self._dispatch_play_pause()

            elif action == "next_track":
                await self._dispatch_track_command("next")

            elif action == "previous_track":
                await self._dispatch_track_command("prev")

            else:
                logger.debug("Unknown BT remote action: %s", action)

        except Exception as e:
            logger.error("Error dispatching BT remote action '%s': %s", action, e)

    async def _process_volume(self):
        """Batch-process accumulated volume changes (mirrors rotary encoder pattern)."""
        self._volume_processor_running = True
        try:
            while self._volume_accumulator != 0.0:
                delta = self._volume_accumulator
                self._volume_accumulator = 0.0
                try:
                    await self.volume_service.adjust_volume_db(delta)
                except Exception as e:
                    logger.error("Error adjusting volume: %s", e)
                await asyncio.sleep(0.02)  # 20ms batch window
        finally:
            # Re-check: if an event arrived between the while-check and here,
            # spawn a new processor to avoid silently dropping it.
            if self._volume_accumulator != 0.0:
                self._volume_processor_running = False
                self._volume_processor_task = asyncio.create_task(self._process_volume())
                return
            self._volume_processor_running = False

    async def _dispatch_play_pause(self):
        """Dispatch play/pause to the active audio source."""
        active_source = self.state_machine.system_state.active_source
        plugin = self.state_machine.get_plugin(active_source)
        if not plugin:
            return

        try:
            if active_source == AudioSource.SPOTIFY:
                await plugin.command("playpause", {})
            elif active_source == AudioSource.RADIO:
                if plugin.is_playing:
                    await plugin.command("stop_playback", {})
                else:
                    await plugin.command("resume_playback", {})
            elif active_source == AudioSource.PODCAST:
                if plugin.is_playing:
                    await plugin.command("pause", {})
                else:
                    await plugin.command("resume", {})
        except Exception as e:
            logger.error("Error dispatching play/pause to %s: %s", active_source.value, e)

    async def _dispatch_track_command(self, cmd: str):
        """Dispatch next/prev track command (Spotify only)."""
        active_source = self.state_machine.system_state.active_source
        if active_source != AudioSource.SPOTIFY:
            return

        plugin = self.state_machine.get_plugin(active_source)
        if plugin:
            try:
                await plugin.command(cmd, {})
            except Exception as e:
                logger.error("Error dispatching %s to spotify: %s", cmd, e)
