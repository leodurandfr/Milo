"""
WiFi management service using NetworkManager (nmcli).

Provides async methods for scanning, connecting, forgetting, and querying
WiFi networks. All nmcli calls use asyncio.create_subprocess_exec.
"""
import asyncio
import logging
from typing import List, Optional, Tuple

from backend.core.wifi.models import WifiNetwork, WifiStatus, SavedNetwork


class WifiService:
    """WiFi management service wrapping nmcli commands."""

    HOTSPOT_CON_NAME = "Milo-Setup"

    def __init__(self, state_machine):
        self.logger = logging.getLogger(__name__)
        self.state_machine = state_machine
        self._hotspot_active: bool = False

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

    async def get_status(self) -> WifiStatus:
        """Get current WiFi connection status."""
        # Get connection name and IP from device info
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
            "device", "show", "wlan0"
        )

        if rc != 0:
            return WifiStatus(connected=False)

        connection = None
        ip_address = None

        for line in stdout.split("\n"):
            key, _, value = line.partition(":")
            value = value.strip()
            if key == "GENERAL.CONNECTION":
                connection = value if value and value != "--" else None
            elif key.startswith("IP4.ADDRESS"):
                ip_address = value.split("/")[0] if value and value != "--" else None

        if not connection:
            return WifiStatus(connected=False)

        # Get actual SSID and signal from active wifi connection
        ssid = connection
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

        return WifiStatus(
            connected=True,
            ssid=ssid,
            ip_address=ip_address,
            signal=signal,
        )

    async def connect(self, ssid: str, password: Optional[str] = None) -> WifiStatus:
        """Connect to a WiFi network.

        Broadcasts WebSocket events on success or failure.
        Timeout: 30 seconds.
        """
        args = ["device", "wifi", "connect", ssid]
        if password is not None:
            args.extend(["password", password])

        self.logger.info("Connecting to WiFi network: %s", ssid)

        try:
            rc, stdout, stderr = await self._run_nmcli(*args, timeout=30.0)
        except asyncio.TimeoutError:
            await self.state_machine.broadcast_event(
                category="wifi",
                type="connect_failed",
                data={"ssid": ssid, "error": "Connection timed out"},
            )
            raise RuntimeError(f"WiFi connection to '{ssid}' timed out")

        if rc != 0:
            error_msg = stderr or "Connection failed"
            self.logger.error("WiFi connection to '%s' failed: %s", ssid, error_msg)
            await self.state_machine.broadcast_event(
                category="wifi",
                type="connect_failed",
                data={"ssid": ssid, "error": error_msg},
            )
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        self.logger.info("Successfully connected to WiFi: %s", ssid)

        # Clean up hotspot profile if it was active
        if self._hotspot_active:
            self._hotspot_active = False
            await self._delete_hotspot_profile()

        status = await self.get_status()

        await self.state_machine.broadcast_event(
            category="wifi",
            type="connected",
            data=status.model_dump(),
        )

        return status

    async def forget_network(self, ssid: str) -> None:
        """Forget (delete) a saved WiFi network connection."""
        rc, _, stderr = await self._run_nmcli("connection", "delete", ssid)

        if rc != 0:
            error_msg = stderr or "Failed to forget network"
            self.logger.error("Failed to forget network '%s': %s", ssid, error_msg)
            raise RuntimeError(f"Failed to forget network: {error_msg}")

        self.logger.info("Forgot WiFi network: %s", ssid)

        await self.state_machine.broadcast_event(
            category="wifi",
            type="network_forgotten",
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
                networks.append(SavedNetwork(ssid=fields[0]))

        return networks

    # =========================================================================
    # Hotspot management
    # =========================================================================

    async def maybe_start_hotspot(self, settings_service) -> bool:
        """Activate WiFi hotspot if setup is incomplete and no network is available.

        Called once at backend startup. Returns True if hotspot was activated.
        """
        setup_completed = bool(await settings_service.get_setting("setup_completed"))
        if setup_completed:
            return False

        if await self._has_active_connection():
            self.logger.info("Hotspot skipped: active network connection found")
            return False

        try:
            await self._activate_hotspot()
            self._hotspot_active = True
            self.logger.info("Hotspot '%s' activated for first-boot setup", self.HOTSPOT_CON_NAME)
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
            # Skip the hotspot's own AP connection
            if connection == self.HOTSPOT_CON_NAME:
                continue
            if device_type in ("ethernet", "wifi"):
                return True
        return False

    async def _activate_hotspot(self) -> None:
        """Create and activate NetworkManager hotspot on wlan0."""
        # Clean up any stale profile from a previous crashed run
        await self._delete_hotspot_profile()

        rc, _, stderr = await self._run_nmcli(
            "device", "wifi", "hotspot",
            "ifname", "wlan0",
            "ssid", self.HOTSPOT_CON_NAME,
            "con-name", self.HOTSPOT_CON_NAME,
            timeout=20.0,
        )
        if rc != 0:
            raise RuntimeError(f"nmcli hotspot failed: {stderr}")

    async def _delete_hotspot_profile(self) -> None:
        """Remove the Milo-Setup NM connection profile (ignores if missing)."""
        rc, _, stderr = await self._run_nmcli(
            "connection", "delete", self.HOTSPOT_CON_NAME
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
