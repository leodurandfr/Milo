"""
WiFi management service using NetworkManager (nmcli).

Provides async methods for scanning, connecting, forgetting, and querying
WiFi networks. All nmcli calls use asyncio.create_subprocess_exec.
"""
import asyncio
import logging
import re
from typing import List, Optional, Tuple

from backend.core.wifi.models import (
    WifiNetwork, WifiConnectionStatus, EthernetStatus,
    NetworkStatus, SavedNetwork,
)


# Hotspot SSIDs are unique per device: 'Milō-XXXX' where XXXX is the last
# 4 hex chars of the wlan0 MAC (uppercase, no colons).
HOTSPOT_NAME_RE = re.compile(r"^Milō-[0-9A-F]{4}$")


def _compute_hotspot_name() -> str:
    """Return this device's unique hotspot SSID ('Milō-XXXX').

    XXXX is the last 4 hex chars of the wlan0 MAC, uppercased without colons.
    Falls back to 'Milō-0000' if the MAC cannot be read (dev environments).
    """
    try:
        with open("/sys/class/net/wlan0/address") as f:
            mac = f.read().strip()
        suffix = mac.replace(":", "").upper()[-4:]
        if len(suffix) == 4:
            return f"Milō-{suffix}"
    except OSError:
        pass
    return "Milō-0000"


class WifiService:
    """WiFi management service wrapping nmcli commands."""

    WIFI_INTERFACE = "wlan0"

    def __init__(self, state_machine, settings_service):
        self.logger = logging.getLogger(__name__)
        self.state_machine = state_machine
        self.settings_service = settings_service
        self._hotspot_active: bool = False
        self._connect_lock = asyncio.Lock()
        self.hotspot_con_name: str = _compute_hotspot_name()

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
                category="wifi",
                event_type="connect_failed",
                data={"ssid": ssid, "error": error_msg},
            )
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        try:
            rc, stdout, stderr = await self._run_nmcli(
                "connection", "up", con_name, timeout=30.0
            )
        except asyncio.TimeoutError:
            await self.state_machine.broadcast_event(
                category="wifi",
                event_type="connect_failed",
                data={"ssid": ssid, "error": "Connection timed out"},
            )
            raise RuntimeError(f"WiFi connection to '{ssid}' timed out")

        if rc != 0:
            error_msg = stderr or "Connection failed"
            self.logger.error("WiFi connection to '%s' failed: %s", ssid, error_msg)
            await self.state_machine.broadcast_event(
                category="wifi",
                event_type="connect_failed",
                data={"ssid": ssid, "error": error_msg},
            )
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        self.logger.info("Successfully connected to WiFi: %s", ssid)

        status = await self.get_network_status()

        await self.state_machine.broadcast_event(
            category="wifi",
            event_type="connected",
            data=status.model_dump(),
        )

        return status

    async def forget_network(self, ssid: str) -> None:
        """Forget (delete) all saved WiFi connection profiles for this SSID."""
        await self._delete_ssid_profiles(ssid)
        self.logger.info("Forgot WiFi network: %s", ssid)

        await self.state_machine.broadcast_event(
            category="wifi",
            event_type="network_forgotten",
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
            if HOTSPOT_NAME_RE.match(name):
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
            # Skip any hotspot AP connection (Milō-XXXX)
            if HOTSPOT_NAME_RE.match(connection):
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
        """Return ethernet connection status."""
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

        return EthernetStatus(
            connected=connection is not None,
            ip_address=ip_address if connection else None,
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

        # Hotspot's own AP connection (Milō-XXXX) is not a real WiFi client connection
        if not connection or HOTSPOT_NAME_RE.match(connection):
            return WifiConnectionStatus(connected=False, saved_ssid=saved)

        # Get actual SSID and signal from active wifi connection
        ssid = connection[5:] if connection.startswith("milo-") else connection
        signal = None

        rc2, stdout2, _ = await self._run_nmcli(
            "-t", "-f", "active,ssid,signal", "dev", "wifi"
        )

        if rc2 == 0:
            for line in stdout2.split("\n"):
                fields = _parse_nmcli_line(line)
                if len(fields) >= 3 and fields[0] == "yes":
                    ssid = fields[1] or ssid
                    try:
                        signal = int(fields[2])
                    except ValueError:
                        pass
                    break

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
