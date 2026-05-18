"""
Network management service (Ethernet + WiFi) using NetworkManager.

Action paths (scan, connect, save, forget, hotspot, set_country) run through
nmcli — their D-Bus equivalents would require a PolicyKit dance and are far
more verbose. Status *change* triggers come from a direct NM D-Bus
subscription so the UI sees physical link / DHCP / WiFi roam events the
moment NM has them, with no polling and no race against DHCP lease
completion.

Three subscription tiers (eth0 + wlan0):
  1. Device.PropertiesChanged (base interface) — eth0 + wlan0
     Watches State, Ip4Config, ActiveConnection.
  2. Device.Wireless.PropertiesChanged — wlan0 only
     Watches ActiveAccessPoint (associate / roam / dissociate).
  3. AccessPoint.PropertiesChanged on the currently active AP — wlan0
     Watches Strength (live signal). Re-anchored when ActiveAccessPoint
     changes so we never leak handlers across roams.

Fails open: if NM D-Bus is unavailable (dev environment, NM stopped),
initialize() logs and returns False. The existing nmcli-based status path
keeps working — just without live updates.
"""
import asyncio
import contextlib
import logging
from typing import List, Optional, Tuple

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.shared.background import BackgroundTaskSet

from backend.core.network.models import (
    WifiNetwork, WifiConnectionStatus, EthernetStatus,
    NetworkStatus, SavedNetwork,
)


# Setup hotspot SSID (shared across all Milō devices). Acceptable trade-off:
# if multiple fresh devices broadcast their hotspot at the same time, the
# scanner deduplicates by SSID and only one of them is adoptable at a time.
HOTSPOT_NAME = "Milō"

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
NM_DEVICE_WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_ACCESS_POINT_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
NM_IP4_CONFIG_IFACE = "org.freedesktop.NetworkManager.IP4Config"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# Base-Device properties whose change should force a status re-broadcast.
_DEVICE_BASE_PROPS = {"State", "Ip4Config", "ActiveConnection"}
# IP4Config properties whose change should force a re-broadcast. NM updates
# these in place when DHCP completes, without re-emitting Device.Ip4Config
# PropertiesChanged, so we must subscribe to the IP4Config object itself.
_IP4_CONFIG_PROPS = {"AddressData", "Addresses"}


class NetworkService:
    """Network management service (Ethernet + WiFi) wrapping nmcli + NetworkManager D-Bus."""

    WIFI_INTERFACE = "wlan0"
    SIGNAL_DEBOUNCE_S = 1.5  # coalesce Strength bursts during roaming

    def __init__(self, state_machine, settings_service):
        self.logger = logging.getLogger(__name__)
        self.state_machine = state_machine
        self.settings_service = settings_service
        self._hotspot_active: bool = False
        self._connect_lock = asyncio.Lock()
        self.hotspot_con_name: str = HOTSPOT_NAME

        # D-Bus state
        self._bus: Optional[MessageBus] = None
        # Cached wlan0 proxy — reused to read ActiveAccessPoint between events.
        self._wlan_proxy = None
        # iface_name → (properties_iface, handler), tier 1 (base Device)
        self._device_listeners: dict = {}
        # iface_name → device proxy (used to re-read Ip4Config path on demand)
        self._device_proxies: dict = {}
        # Tier 1b — per-device IP4Config listener, re-anchored when
        # Device.Ip4Config path changes. Required because NM updates
        # IP4Config.Addresses in place on DHCP lease without bubbling a
        # fresh Device.Ip4Config PropertiesChanged signal.
        self._ip4_path: dict = {}
        self._ip4_listener: dict = {}
        # (properties_iface, handler) on wlan0, tier 2 (Device.Wireless)
        self._wireless_listener: Optional[Tuple] = None
        # Tier 3 — re-anchored each time ActiveAccessPoint changes path.
        # _ap_proxy is also read on every status refresh for live SSID +
        # Strength (avoids `nmcli dev wifi` which can stall on scans).
        self._ap_path: Optional[str] = None
        self._ap_proxy = None
        self._ap_listener: Optional[Tuple] = None
        # Broadcast plumbing
        self._last_broadcast: Optional[NetworkStatus] = None
        self._broadcast_lock = asyncio.Lock()
        # Serializes listener mutations to avoid leaks when path changes bounce.
        self._listener_setup_lock = asyncio.Lock()
        self._signal_debounce_task: Optional[asyncio.Task] = None
        # Holds strong refs to fire-and-forget tasks (CPython can otherwise GC
        # a pending task that has no external reference).
        self._bg = BackgroundTaskSet(self.logger, "network")

    @property
    def hotspot_active(self) -> bool:
        """Whether the setup hotspot is currently active."""
        return self._hotspot_active

    # =========================================================================
    # Public API
    # =========================================================================

    async def scan_networks(self) -> List[WifiNetwork]:
        """Scan for available WiFi networks.

        Deduplicates SSIDs (keeps strongest signal) and sorts by signal descending.
        """
        rc, stdout, stderr = await self._run_nmcli(
            "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
            "device", "wifi", "list", "--rescan", "yes",
            timeout=15.0
        )

        if rc != 0:
            self.logger.error("WiFi scan failed: %s", stderr)
            raise RuntimeError(f"WiFi scan failed: {stderr}")

        if not stdout:
            return []

        networks: dict[str, WifiNetwork] = {}
        for line in stdout.split("\n"):
            if not line:
                continue

            fields = _parse_nmcli_line(line)
            if len(fields) < 4:
                continue

            ssid = fields[0]
            if not ssid:
                continue

            try:
                signal = int(fields[1])
            except ValueError:
                signal = 0

            security = fields[2] if fields[2] else ""
            in_use = fields[3] == "*"

            # Deduplicate: keep strongest signal
            if ssid not in networks or signal > networks[ssid].signal:
                networks[ssid] = WifiNetwork(
                    ssid=ssid,
                    signal=signal,
                    security=security,
                    in_use=in_use,
                )

        return sorted(networks.values(), key=lambda n: n.signal, reverse=True)

    async def get_network_status(self) -> NetworkStatus:
        """Get combined network status (both ethernet and WiFi)."""
        wifi_enabled = await self.get_wifi_enabled()
        ethernet = await self._get_ethernet_info()

        if wifi_enabled:
            wifi = await self._get_wifi_info()
        else:
            wifi = WifiConnectionStatus(connected=False)

        return NetworkStatus(
            wifi_enabled=wifi_enabled,
            ethernet=ethernet,
            wifi=wifi,
        )

    async def get_wifi_enabled(self) -> bool:
        """Check if WiFi radio is enabled."""
        rc, stdout, _ = await self._run_nmcli("radio", "wifi")
        return rc == 0 and stdout.strip() == "enabled"

    async def set_wifi_enabled(self, enabled: bool) -> None:
        """Enable or disable WiFi radio."""
        state = "on" if enabled else "off"
        rc, _, stderr = await self._run_nmcli("radio", "wifi", state)
        if rc != 0:
            raise RuntimeError(f"Failed to set WiFi radio {state}: {stderr}")
        self.logger.info("WiFi radio set to %s", state)

    async def save_network(self, ssid: str, password: Optional[str] = None) -> None:
        """Save WiFi credentials without connecting.

        Creates a NM profile for the given SSID so NetworkManager will
        auto-connect on next boot.  The current connection (e.g. hotspot)
        is left untouched.
        """
        async with self._connect_lock:
            self.logger.info("Saving WiFi network: %s", ssid)

            await self._delete_ssid_profiles(ssid)

            con_name = f"milo-{ssid}"
            add_args = [
                "connection", "add",
                "type", "wifi",
                "ifname", self.WIFI_INTERFACE,
                "con-name", con_name,
                "ssid", ssid,
            ]
            if password is not None:
                add_args.extend([
                    "wifi-sec.key-mgmt", "wpa-psk",
                    "wifi-sec.psk", password,
                    "wifi-sec.psk-flags", "0",
                ])

            rc, _, stderr = await self._run_nmcli(*add_args)
            if rc != 0:
                error_msg = stderr or "Failed to create connection profile"
                self.logger.error("Failed to save WiFi profile for '%s': %s", ssid, error_msg)
                raise RuntimeError(f"WiFi save failed: {error_msg}")

            self.logger.info("WiFi profile saved for '%s' (will connect on next boot)", ssid)

    async def connect(self, ssid: str, password: Optional[str] = None) -> NetworkStatus:
        """Connect to a WiFi network.

        Removes all existing profiles for this SSID (including broken
        netplan-generated ones), then creates a clean profile with explicit
        security settings. Broadcasts WebSocket events on success or failure.
        Timeout: 30 seconds.
        """
        async with self._connect_lock:
            return await self._connect_impl(ssid, password)

    async def _connect_impl(self, ssid: str, password: Optional[str] = None) -> NetworkStatus:
        """Internal connect implementation (called under _connect_lock)."""
        self.logger.info("Connecting to WiFi network: %s (password provided: %s)", ssid, password is not None)

        # Disconnect WiFi device first to ensure clean state — an active
        # connection prevents its profile from being fully removed by NM
        await self._run_nmcli("device", "disconnect", self.WIFI_INTERFACE)

        # Delete hotspot profile early to prevent NM from auto-reconnecting
        # to the AP while we switch to STA mode
        if self._hotspot_active:
            self._hotspot_active = False
            await self._delete_hotspot_profile()

        # Remove all existing profiles for this SSID to avoid stale profiles
        await self._delete_ssid_profiles(ssid)

        # Create profile and activate in two steps with psk-flags=0
        # to store the password in the system file (not via secret agent,
        # which doesn't exist on headless systems).
        con_name = f"milo-{ssid}"
        add_args = [
            "connection", "add",
            "type", "wifi",
            "ifname", self.WIFI_INTERFACE,
            "con-name", con_name,
            "ssid", ssid,
        ]
        if password is not None:
            add_args.extend([
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                "wifi-sec.psk-flags", "0",
            ])

        rc, _, stderr = await self._run_nmcli(*add_args)
        if rc != 0:
            error_msg = stderr or "Failed to create connection profile"
            self.logger.error("Failed to create WiFi profile for '%s': %s", ssid, error_msg)
            await self.state_machine.broadcast_event(
                category="network",
                event_type="wifi_connect_failed",
                data={"ssid": ssid, "error": error_msg},
            )
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        try:
            rc, stdout, stderr = await self._run_nmcli(
                "connection", "up", con_name, timeout=30.0
            )
        except asyncio.TimeoutError:
            await self.state_machine.broadcast_event(
                category="network",
                event_type="wifi_connect_failed",
                data={"ssid": ssid, "error": "Connection timed out"},
            )
            raise RuntimeError(f"WiFi connection to '{ssid}' timed out")

        if rc != 0:
            error_msg = stderr or "Connection failed"
            self.logger.error("WiFi connection to '%s' failed: %s", ssid, error_msg)
            await self.state_machine.broadcast_event(
                category="network",
                event_type="wifi_connect_failed",
                data={"ssid": ssid, "error": error_msg},
            )
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        self.logger.info("Successfully connected to WiFi: %s", ssid)

        status = await self.get_network_status()

        await self.state_machine.broadcast_event(
            category="network",
            event_type="wifi_connected",
            data=status.model_dump(),
        )

        return status

    async def forget_network(self, ssid: str) -> None:
        """Forget (delete) all saved WiFi connection profiles for this SSID."""
        await self._delete_ssid_profiles(ssid)
        self.logger.info("Forgot WiFi network: %s", ssid)

        await self.state_machine.broadcast_event(
            category="network",
            event_type="wifi_forgotten",
            data={"ssid": ssid},
        )

    async def get_saved_networks(self) -> List[SavedNetwork]:
        """List saved WiFi network connections."""
        rc, stdout, stderr = await self._run_nmcli(
            "-t", "-f", "NAME,TYPE", "connection", "show"
        )

        if rc != 0:
            self.logger.error("Failed to list saved networks: %s", stderr)
            raise RuntimeError(f"Failed to list saved networks: {stderr}")

        networks = []
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) >= 2 and "wireless" in fields[1]:
                name = fields[0]
                # Show milo-managed profiles with the SSID as display name
                if name.startswith("milo-"):
                    networks.append(SavedNetwork(ssid=name[5:]))
                elif not name.startswith("netplan-"):
                    networks.append(SavedNetwork(ssid=name))

        return networks

    async def get_active_wifi_credentials(self) -> Optional[dict]:
        """Return SSID + PSK of the active WiFi client connection on wlan0.

        Used by the multiroom adoption flow: when adopting a wifi-only speaker,
        the server pushes its own home-network credentials so the speaker
        joins the same LAN after reboot.

        Returns a dict ``{"ssid": str, "password": str}`` or ``None`` when
        wlan0 has no active client connection (e.g. ethernet-only server, or
        only the setup hotspot is up). ``password`` is an empty string for
        open networks.
        """
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"
        )
        if rc != 0:
            return None

        active_name: Optional[str] = None
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) < 2:
                continue
            name, device = fields[0], fields[1]
            if device != self.WIFI_INTERFACE:
                continue
            if name == HOTSPOT_NAME:
                continue
            active_name = name
            break

        if not active_name:
            return None

        rc, stdout, stderr = await self._run_nmcli(
            "-s", "-t",
            "-f", "802-11-wireless.ssid,802-11-wireless-security.psk",
            "connection", "show", active_name,
        )
        if rc != 0:
            self.logger.error(
                "Failed to read WiFi credentials for '%s': %s", active_name, stderr
            )
            return None

        ssid: Optional[str] = None
        password: str = ""
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) < 2:
                continue
            key, value = fields[0], fields[1]
            if key == "802-11-wireless.ssid":
                ssid = value
            elif key == "802-11-wireless-security.psk":
                password = value

        if not ssid:
            return None

        return {"ssid": ssid, "password": password}

    async def get_country(self) -> str:
        """Return the stored WiFi country code, or empty string if not set."""
        code = await self.settings_service.get_setting("wifi.country")
        return code or ""

    async def set_country(self, country_code: str) -> None:
        """Apply WiFi regulatory domain and persist the country code.

        Calls the privileged milo-set-wifi-country script (via sudo) to:
          - Run 'iw reg set <CC>' for immediate effect in the current session.
          - Update cfg80211.ieee80211_regdom=<CC> in /boot/firmware/cmdline.txt.

        The caller is responsible for triggering a reboot if needed.
        """
        self.logger.info("Setting WiFi country code to %s", country_code)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "/usr/local/bin/milo-set-wifi-country", country_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error("milo-set-wifi-country timed out for country %s", country_code)
            raise RuntimeError("WiFi country script timed out")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or "Unknown error"
            self.logger.error("milo-set-wifi-country failed (rc=%d): %s", proc.returncode, error_msg)
            raise RuntimeError(f"Failed to set WiFi country: {error_msg}")

        self.logger.info("WiFi country set to %s: %s", country_code, stdout.decode().strip())
        await self.settings_service.set_setting("wifi.country", country_code)

    # =========================================================================
    # Hotspot management
    # =========================================================================

    async def maybe_start_hotspot(self, settings_service) -> bool:
        """Activate WiFi hotspot if setup is incomplete and no network is available.

        Called once at backend startup. Returns True if hotspot was activated.
        """
        setup_completed = bool(await settings_service.get_setting("setup_completed"))
        if setup_completed:
            # Clean up stale hotspot profile from a previous setup session
            await self._delete_hotspot_profile()
            return False

        if await self._has_active_connection():
            self.logger.info("Hotspot skipped: active network connection found")
            return False

        try:
            await self._activate_hotspot()
            self._hotspot_active = True
            self.logger.info("Hotspot '%s' activated for first-boot setup", self.hotspot_con_name)
            return True
        except Exception as e:
            self.logger.error("Failed to activate hotspot: %s", e)
            return False

    async def _has_active_connection(self) -> bool:
        """Return True if any Ethernet or WiFi client (non-AP) connection is active."""
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "TYPE,STATE,CONNECTION",
            "device", "status"
        )
        if rc != 0:
            return False
        for line in stdout.split("\n"):
            fields = _parse_nmcli_line(line)
            if len(fields) < 3:
                continue
            device_type, state, connection = fields[0], fields[1], fields[2]
            if state != "connected":
                continue
            # Skip the setup hotspot AP connection
            if connection == HOTSPOT_NAME:
                continue
            if device_type in ("ethernet", "wifi"):
                return True
        return False

    async def _activate_hotspot(self) -> None:
        """Create and activate an open (no password) NetworkManager hotspot."""
        # Clean up any stale profile from a previous crashed run
        await self._delete_hotspot_profile()

        # Create an open AP profile (nmcli device wifi hotspot always adds WPA)
        rc, _, stderr = await self._run_nmcli(
            "connection", "add",
            "type", "wifi",
            "ifname", self.WIFI_INTERFACE,
            "con-name", self.hotspot_con_name,
            "ssid", self.hotspot_con_name,
            "wifi.mode", "ap",
            "wifi.band", "bg",
            "wifi.channel", "6",
            "ipv4.method", "shared",
            timeout=20.0,
        )
        if rc != 0:
            raise RuntimeError(f"Hotspot profile creation failed: {stderr}")

        rc, _, stderr = await self._run_nmcli(
            "connection", "up", self.hotspot_con_name,
            timeout=20.0,
        )
        if rc != 0:
            await self._delete_hotspot_profile()
            raise RuntimeError(f"Hotspot activation failed: {stderr}")

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _get_ethernet_info(self) -> EthernetStatus:
        """Return ethernet connection status.

        connected=True requires both an active NM profile AND an IPv4 address,
        so the badge matches what the Avahi dispatcher uses to advertise on
        eth0 (`has_ip eth0`). This prevents reporting "connected" during the
        DHCP activation window when milo.local is not yet resolvable on eth0.
        """
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
            "device", "show", "eth0"
        )
        if rc != 0:
            return EthernetStatus(connected=False)

        connection = None
        ip_address = None
        for line in stdout.split("\n"):
            key, _, value = line.partition(":")
            value = value.strip()
            if key == "GENERAL.CONNECTION":
                connection = value if value and value != "--" else None
            elif key.startswith("IP4.ADDRESS"):
                ip_address = value.split("/")[0] if value and value != "--" else None

        connected = connection is not None and ip_address is not None
        return EthernetStatus(
            connected=connected,
            ip_address=ip_address if connected else None,
        )

    async def _get_wifi_info(self) -> WifiConnectionStatus:
        """Get WiFi connection status."""
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
            "device", "show", self.WIFI_INTERFACE
        )

        if rc != 0:
            return WifiConnectionStatus(connected=False)

        connection = None
        ip_address = None

        for line in stdout.split("\n"):
            key, _, value = line.partition(":")
            value = value.strip()
            if key == "GENERAL.CONNECTION":
                connection = value if value and value != "--" else None
            elif key.startswith("IP4.ADDRESS"):
                ip_address = value.split("/")[0] if value and value != "--" else None

        saved = await self._get_saved_ssid()

        # Hotspot's own AP connection is not a real WiFi client connection.
        # No IPv4 yet means the profile is mid-activation (DHCP pending); the
        # Avahi dispatcher won't advertise on wlan0 until `has_ip wlan0` is true,
        # so report not-connected to keep the badge aligned with reachability.
        if not connection or connection == HOTSPOT_NAME or not ip_address:
            return WifiConnectionStatus(connected=False, saved_ssid=saved)

        # Read live SSID + Strength directly from the cached AP D-Bus proxy.
        # `nmcli dev wifi` was the old source here, but its default --rescan auto
        # could stall the refresh for several seconds during an implicit scan,
        # which serialised behind every NM PropertiesChanged event.
        ssid, signal = await self._read_active_ap_info()
        if not ssid:
            # Fail-open: D-Bus init didn't take, or AP not yet anchored.
            # Derive a usable SSID from the milo-prefixed connection name.
            ssid = connection[5:] if connection.startswith("milo-") else connection

        return WifiConnectionStatus(
            connected=True,
            ssid=ssid,
            ip_address=ip_address,
            signal=signal,
            saved_ssid=saved,
        )

    async def _get_saved_ssid(self) -> Optional[str]:
        """Return the SSID of the first milo-managed WiFi profile, or None."""
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "NAME,TYPE", "connection", "show"
        )
        if rc != 0:
            return None
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) >= 2 and "wireless" in fields[1]:
                name = fields[0]
                if name.startswith("milo-"):
                    return name[5:]
        return None

    async def _delete_ssid_profiles(self, ssid: str) -> None:
        """Delete all NM connection profiles matching a given SSID.

        Handles netplan-prefixed names (e.g. 'netplan-wlan0-MySSID') and
        milo-prefixed names from previous connections.
        """
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "NAME,TYPE", "connection", "show"
        )
        if rc != 0:
            return

        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) < 2 or "wireless" not in fields[1]:
                continue
            name = fields[0]
            if name == ssid or name == f"milo-{ssid}" or f"-{ssid}" in name:
                self.logger.info("Deleting WiFi profile: %s (ssid=%s)", name, ssid)
                rc2, _, stderr2 = await self._run_nmcli("connection", "delete", name)
                if rc2 == 0:
                    self.logger.info("Deleted WiFi profile: %s", name)
                else:
                    self.logger.error("Failed to delete WiFi profile %s: %s", name, stderr2)

    async def _delete_hotspot_profile(self) -> None:
        """Remove the hotspot NM connection profile (ignores if missing)."""
        rc, _, stderr = await self._run_nmcli(
            "connection", "delete", self.hotspot_con_name
        )
        if rc != 0:
            self.logger.debug("Hotspot profile cleanup (rc=%d): %s", rc, stderr)

    # =========================================================================
    # NetworkManager D-Bus subscription (live status updates)
    # =========================================================================

    async def initialize(self) -> bool:
        """Connect to NM via D-Bus, subscribe to the three tiers, prime cache.

        Returns True if at least the system bus connection succeeded. Even
        when individual interfaces are missing (e.g. boards without wlan0),
        we still consider initialize() successful as long as the bus came up.
        Fails open: if NM is unavailable, returns False and live updates are
        disabled. The nmcli-based read path keeps working.
        """
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            await self._subscribe_device_base("eth0")
            await self._subscribe_device_base(self.WIFI_INTERFACE)
            await self._subscribe_wireless()
            await self._reanchor_active_ap()
            await self._refresh_and_broadcast()

            self.logger.info(
                "Network service ready (devices=%s, wireless=%s, ap=%s)",
                list(self._device_listeners),
                "yes" if self._wireless_listener else "no",
                self._ap_path or "none",
            )
            return True
        except Exception as exc:
            self.logger.warning(
                "NetworkManager D-Bus unavailable, network status will lack live updates: %s",
                exc,
            )
            await self.cleanup()
            return False

    async def cleanup(self) -> None:
        """Detach every listener and disconnect the bus. Idempotent."""
        await self._bg.cancel_all()
        self._signal_debounce_task = None

        await self._detach_ap_listener()

        if self._wireless_listener is not None:
            properties_iface, handler = self._wireless_listener
            with contextlib.suppress(Exception):
                properties_iface.off_properties_changed(handler)
            self._wireless_listener = None

        for iface_name in list(self._ip4_listener):
            await self._detach_ip4(iface_name)
        self._ip4_path.clear()

        for _, (properties_iface, handler) in self._device_listeners.items():
            with contextlib.suppress(Exception):
                properties_iface.off_properties_changed(handler)
        self._device_listeners.clear()
        self._device_proxies.clear()

        self._wlan_proxy = None

        if self._bus is not None:
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

    async def _resolve_device_path(self, iface_name: str) -> Optional[str]:
        """Ask NM for the D-Bus object path of a given network interface."""
        try:
            introspect = await self._bus.introspect(NM_SERVICE, NM_PATH)
            nm_proxy = self._bus.get_proxy_object(NM_SERVICE, NM_PATH, introspect)
            nm_iface = nm_proxy.get_interface(NM_IFACE)
            return await nm_iface.call_get_device_by_ip_iface(iface_name)
        except Exception as exc:
            self.logger.debug("NM device lookup for %s failed: %s", iface_name, exc)
            return None

    # ---- Tier 1: base Device interface (eth0 + wlan0) ----

    async def _subscribe_device_base(self, iface_name: str) -> None:
        path = await self._resolve_device_path(iface_name)
        if not path:
            self.logger.info(
                "Skipping NM D-Bus subscription for %s (interface not present)",
                iface_name,
            )
            return

        introspect = await self._bus.introspect(NM_SERVICE, path)
        proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
        properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
        handler = self._make_device_base_handler(iface_name)
        properties_iface.on_properties_changed(handler)
        self._device_listeners[iface_name] = (properties_iface, handler)
        self._device_proxies[iface_name] = proxy

        if iface_name == self.WIFI_INTERFACE:
            self._wlan_proxy = proxy

        # Anchor the IP4Config listener so DHCP-lease updates surface as events.
        await self._reanchor_ip4(iface_name)

    def _make_device_base_handler(self, iface_name: str):
        def _handler(iface: str, changed: dict, _invalidated: list) -> None:
            if iface != NM_DEVICE_IFACE:
                return
            if not (changed.keys() & _DEVICE_BASE_PROPS):
                return
            if "Ip4Config" in changed:
                self._bg.spawn(self._reanchor_ip4(iface_name), label="reanchor_ip4")
            self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")
        return _handler

    # ---- Tier 1b: IP4Config object (re-anchored on Device.Ip4Config change) ----

    async def _reanchor_ip4(self, iface_name: str) -> None:
        """Re-attach the IP4Config listener if Device.Ip4Config path changed."""
        proxy = self._device_proxies.get(iface_name)
        if proxy is None:
            return
        async with self._listener_setup_lock:
            try:
                device_iface = proxy.get_interface(NM_DEVICE_IFACE)
                new_path = await device_iface.get_ip4_config()
            except Exception as exc:
                self.logger.debug("Failed to read %s Ip4Config path: %s", iface_name, exc)
                return

            if new_path == self._ip4_path.get(iface_name):
                return

            await self._detach_ip4(iface_name)
            self._ip4_path[iface_name] = new_path

            if new_path and new_path != "/":
                await self._attach_ip4(iface_name, new_path)

        # Path change is itself a status change — refresh now.
        self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")

    async def _attach_ip4(self, iface_name: str, path: str) -> None:
        try:
            introspect = await self._bus.introspect(NM_SERVICE, path)
            proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
            properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            handler = self._make_ip4_handler(iface_name)
            properties_iface.on_properties_changed(handler)
            self._ip4_listener[iface_name] = (properties_iface, handler)
        except Exception as exc:
            self.logger.debug("Failed to attach IP4 listener for %s at %s: %s", iface_name, path, exc)

    async def _detach_ip4(self, iface_name: str) -> None:
        listener = self._ip4_listener.pop(iface_name, None)
        if listener is None:
            return
        properties_iface, handler = listener
        with contextlib.suppress(Exception):
            properties_iface.off_properties_changed(handler)

    def _make_ip4_handler(self, iface_name: str):
        def _handler(iface: str, changed: dict, _invalidated: list) -> None:
            if iface != NM_IP4_CONFIG_IFACE:
                return
            if not (changed.keys() & _IP4_CONFIG_PROPS):
                return
            self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")
        return _handler

    # ---- Tier 2: Device.Wireless (wlan0 only) ----

    async def _subscribe_wireless(self) -> None:
        if self._wlan_proxy is None:
            return
        properties_iface = self._wlan_proxy.get_interface(DBUS_PROPERTIES_IFACE)
        properties_iface.on_properties_changed(self._on_wireless_props_changed)
        self._wireless_listener = (properties_iface, self._on_wireless_props_changed)

    def _on_wireless_props_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        if iface != NM_DEVICE_WIRELESS_IFACE:
            return
        if "ActiveAccessPoint" not in changed:
            return
        self._bg.spawn(self._reanchor_active_ap(), label="reanchor_active_ap")

    # ---- Tier 3: AccessPoint (re-anchored on AP change) ----

    async def _read_active_ap_path(self) -> Optional[str]:
        if self._wlan_proxy is None:
            return None
        try:
            wireless_iface = self._wlan_proxy.get_interface(NM_DEVICE_WIRELESS_IFACE)
            return await wireless_iface.get_active_access_point()
        except Exception as exc:
            self.logger.debug("Failed to read ActiveAccessPoint: %s", exc)
            return None

    async def _reanchor_active_ap(self) -> None:
        """Detach old AP listener (if any), attach to the new ActiveAccessPoint."""
        async with self._listener_setup_lock:
            new_path = await self._read_active_ap_path()
            if new_path == self._ap_path:
                return

            await self._detach_ap_listener()
            self._ap_path = new_path

            if new_path and new_path != "/":
                await self._attach_ap_listener(new_path)

        # AP change is itself a status change — refresh now.
        self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")

    async def _attach_ap_listener(self, path: str) -> None:
        try:
            introspect = await self._bus.introspect(NM_SERVICE, path)
            proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
            properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            properties_iface.on_properties_changed(self._on_ap_props_changed)
            self._ap_proxy = proxy
            self._ap_listener = (properties_iface, self._on_ap_props_changed)
        except Exception as exc:
            self.logger.debug("Failed to attach AP listener at %s: %s", path, exc)
            self._ap_proxy = None
            self._ap_listener = None

    async def _detach_ap_listener(self) -> None:
        self._ap_proxy = None
        if self._ap_listener is None:
            return
        properties_iface, handler = self._ap_listener
        with contextlib.suppress(Exception):
            properties_iface.off_properties_changed(handler)
        self._ap_listener = None

    async def _read_active_ap_info(self) -> Tuple[Optional[str], Optional[int]]:
        """Return (ssid, strength) from the cached AP proxy. (None, None) on miss."""
        if self._ap_proxy is None:
            return None, None
        try:
            ap_iface = self._ap_proxy.get_interface(NM_ACCESS_POINT_IFACE)
            ssid_bytes, strength = await asyncio.gather(
                ap_iface.get_ssid(),
                ap_iface.get_strength(),
            )
            ssid = bytes(ssid_bytes).decode("utf-8", errors="replace") or None
            return ssid, int(strength)
        except Exception as exc:
            self.logger.debug("Failed to read AP info: %s", exc)
            return None, None

    def _on_ap_props_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        if iface != NM_ACCESS_POINT_IFACE:
            return
        if "Strength" not in changed:
            return

        # Debounce: coalesce Strength bursts (RSSI sampling, roaming) into
        # one refresh. connected/Ip4Config events stay un-debounced — they
        # surface immediately via the tier-1 path.
        if self._signal_debounce_task and not self._signal_debounce_task.done():
            self._signal_debounce_task.cancel()
        self._signal_debounce_task = self._bg.spawn(
            self._debounced_refresh(), label="debounced_refresh"
        )

    async def _debounced_refresh(self) -> None:
        try:
            await asyncio.sleep(self.SIGNAL_DEBOUNCE_S)
            await self._refresh_and_broadcast()
        except asyncio.CancelledError:
            pass

    async def _refresh_and_broadcast(self) -> None:
        """Re-read full network status and broadcast it, dedup against last sent.

        The WS broadcast runs OUTSIDE the lock: a slow client would otherwise
        keep the lock held while D-Bus signal handlers pile up new refresh
        tasks behind it.
        """
        async with self._broadcast_lock:
            try:
                status = await self.get_network_status()
            except Exception as exc:
                self.logger.error("Failed to read network status for broadcast: %s", exc)
                return

            if status == self._last_broadcast:
                return

            self._last_broadcast = status

        await self.state_machine.broadcast_event(
            category="network",
            event_type="status_changed",
            data=status.model_dump(),
        )

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _run_nmcli(self, *args: str, timeout: float = 10.0) -> Tuple[int, str, str]:
        """Run an nmcli command asynchronously with timeout.

        Returns (returncode, stdout, stderr).
        Raises asyncio.TimeoutError if the command exceeds the timeout.
        """
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error("nmcli timed out: nmcli %s", " ".join(args))
            raise


def _parse_nmcli_line(line: str) -> List[str]:
    """Parse a line from nmcli -t output, handling escaped characters.

    nmcli terse mode uses ':' as field separator and escapes literal
    colons as '\\:' and backslashes as '\\\\'.
    """
    fields = []
    current: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
        elif line[i] == ":":
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    fields.append("".join(current))
    return fields
