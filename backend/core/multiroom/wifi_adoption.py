# backend/core/multiroom/wifi_adoption.py
"""
WiFi adoption orchestration for multiroom client setup.

When a fresh speaker exposes its setup hotspot ('Milō'), the server can adopt
it without ever touching the speaker physically:

  1. Capture the server's currently active wifi connection (for restore).
  2. Add + activate an open NM profile to join the speaker's hotspot.
  3. Discover the speaker's gateway IP (typically 10.42.0.1, served by the
     hotspot's NetworkManager 'shared' mode).
  4. POST /api/setup/become-client with audio config + target wifi creds.
  5. Tear down the temporary hotspot profile and reconnect the original wifi.

Side effects on the server:
  - Wifi-only servers temporarily lose LAN connectivity for ~30 s while
    connected to the speaker hotspot (frontend will reconnect via WebSocket
    once the home network is restored).
  - Ethernet servers keep their LAN through the whole flow.

The temporary network switch breaks any TCP connection to the caller; it is
the caller's responsibility (UI) to handle the brief outage.
"""
import asyncio
import logging
import re
from typing import Optional, Tuple

import aiohttp

from backend.core.wifi.service import HOTSPOT_NAME, _parse_nmcli_line


logger = logging.getLogger(__name__)


# Backend port on a fresh speaker (it runs as a server, listening on 8000).
SETUP_API_PORT = 8000

# Allow NM up to this long to associate with the speaker hotspot.
HOTSPOT_CONNECT_TIMEOUT = 30.0

# Allow NM up to this long to bring the original wifi back.
RESTORE_CONNECT_TIMEOUT = 30.0

# After hotspot association, wait for DHCP to issue the gateway lease.
DHCP_GRACE_PERIOD = 2.0

# Total HTTP timeout for the become-client request (includes server-side
# atomic write + nmcli profile save before it returns).
BECOME_CLIENT_TIMEOUT = 20.0

WLAN_INTERFACE = "wlan0"


class AdoptionError(Exception):
    """Adoption failure with a stable code for UI mapping."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class WifiAdoptionService:
    """Orchestrates wifi-based adoption of a fresh Milō speaker."""

    def __init__(self, wifi_service):
        self.wifi_service = wifi_service
        self.logger = logger
        self._lock = asyncio.Lock()

    async def adopt_speaker(
        self,
        ssid: str,
        audio_id: str,
        speaker_name: str,
        speaker_type: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> dict:
        """Adopt a speaker advertising hotspot ``ssid``.

        Raises :class:`AdoptionError` on any failure; on success returns a
        dict with the gateway used and the speaker SSID.
        """
        if ssid != HOTSPOT_NAME:
            raise AdoptionError("invalid_ssid", f"'{ssid}' is not the Milō hotspot SSID")
        if self.wifi_service.hotspot_active:
            raise AdoptionError("invalid_ssid", "Cannot adopt while broadcasting the setup hotspot")
        if not wifi_ssid:
            raise AdoptionError("invalid_target_wifi", "Target wifi SSID is required")

        async with self._lock:
            return await self._adopt_impl(
                ssid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password
            )

    async def _adopt_impl(
        self,
        ssid: str,
        audio_id: str,
        speaker_name: str,
        speaker_type: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> dict:
        original_connection = await self._get_active_wifi_name()
        self.logger.info(
            "Adopting speaker '%s' (server wifi='%s', target wifi='%s')",
            ssid, original_connection or "<none>", wifi_ssid,
        )

        try:
            await self._connect_to_hotspot(ssid)
        except AdoptionError:
            await self._cleanup_temp_profile(ssid)
            await self._restore_connection(original_connection)
            raise

        try:
            await asyncio.sleep(DHCP_GRACE_PERIOD)
            gateway = await self._get_default_gateway()
            if not gateway:
                raise AdoptionError(
                    "no_gateway",
                    "Could not discover speaker gateway IP after hotspot association",
                )

            await self._push_become_client(
                gateway, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password
            )
        finally:
            await self._cleanup_temp_profile(ssid)
            await self._restore_connection(original_connection)

        self.logger.info(
            "Speaker '%s' adoption succeeded; device will reboot and join '%s'",
            ssid, wifi_ssid,
        )
        return {"ssid": ssid, "gateway": gateway}

    async def _connect_to_hotspot(self, ssid: str) -> None:
        await self._run_nmcli("connection", "delete", ssid)

        rc, _, stderr = await self._run_nmcli(
            "connection", "add",
            "type", "wifi",
            "ifname", WLAN_INTERFACE,
            "con-name", ssid,
            "ssid", ssid,
            "wifi-sec.key-mgmt", "none",
        )
        if rc != 0:
            raise AdoptionError(
                "hotspot_connect_failed",
                f"Failed to create temp profile for '{ssid}': {stderr}",
            )

        rc, _, stderr = await self._run_nmcli(
            "connection", "up", ssid, timeout=HOTSPOT_CONNECT_TIMEOUT
        )
        if rc != 0:
            raise AdoptionError(
                "hotspot_connect_failed",
                f"Failed to associate with '{ssid}': {stderr}",
            )

    async def _push_become_client(
        self,
        gateway: str,
        audio_id: str,
        speaker_name: str,
        speaker_type: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> None:
        url = f"http://{gateway}:{SETUP_API_PORT}/api/setup/become-client"
        payload = {
            "audio_id": audio_id,
            "speaker_name": speaker_name,
            "speaker_type": speaker_type,
            "wifi_ssid": wifi_ssid,
            "wifi_password": wifi_password,
        }
        timeout = aiohttp.ClientTimeout(total=BECOME_CLIENT_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        self.logger.error(
                            "Speaker rejected become-client (status=%d): %s",
                            resp.status, body,
                        )
                        if resp.status == 409:
                            raise AdoptionError(
                                "already_configured",
                                "Speaker already configured (setup_completed=true)",
                            )
                        raise AdoptionError(
                            "push_rejected",
                            f"Speaker rejected configuration ({resp.status})",
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise AdoptionError(
                "push_failed", f"Cannot reach speaker at {gateway}: {e}"
            ) from e

    async def _cleanup_temp_profile(self, ssid: str) -> None:
        rc, _, stderr = await self._run_nmcli("connection", "delete", ssid)
        if rc != 0:
            self.logger.warning(
                "Failed to delete temp hotspot profile '%s': %s", ssid, stderr
            )

    async def _restore_connection(self, name: Optional[str]) -> None:
        if not name:
            return
        rc, _, stderr = await self._run_nmcli(
            "connection", "up", name, timeout=RESTORE_CONNECT_TIMEOUT
        )
        if rc != 0:
            self.logger.error(
                "Failed to restore wifi connection '%s': %s", name, stderr
            )

    async def _get_active_wifi_name(self) -> Optional[str]:
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"
        )
        if rc != 0:
            return None
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) < 2:
                continue
            name, device = fields[0], fields[1]
            if device != WLAN_INTERFACE:
                continue
            if name == HOTSPOT_NAME:
                continue
            return name
        return None

    async def _get_default_gateway(self) -> Optional[str]:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-4", "route", "show", "default", "dev", WLAN_INTERFACE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error("'ip route show default' timed out")
            return None
        match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", stdout.decode())
        return match.group(1) if match else None

    async def _run_nmcli(
        self, *args: str, timeout: float = 10.0
    ) -> Tuple[int, str, str]:
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
            return 124, "", "nmcli timeout"
