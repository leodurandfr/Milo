# backend/core/multiroom/wifi_adoption.py
"""
WiFi adoption orchestration for multiroom client setup.

When a fresh speaker exposes its setup hotspot ('Milō'), the server can adopt
it without ever touching the speaker physically:

  1. Capture the server's currently active wifi connection (for restore).
  2. Add + activate an open NM profile (ADOPTION_PROFILE) to join the
     speaker's hotspot.
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
the caller's responsibility (UI) to handle the brief outage. The whole switch
runs inside `NetworkService.recovery_held()`: off its home network, a
WiFi-only server reads "no link", and the recovery AP it would raise takes the
one radio from under the adoption.

The steps are cut at STEPS_DEADLINE and the request is answered once the
server is back on its own WiFi, which a WiFi-only server needs for the answer
to reach the caller at all; the restore's own caps fit before
ADOPTION_DEADLINE. The restore is the one thing no deadline cuts: should it
outrun its caps, the answer leaves at ADOPTION_DEADLINE and the restore runs to
its end after it, still under the lock and the hold.
"""
import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from backend.core.network.service import (
    ADOPTION_PROFILE, HOTSPOT_NAME, OWN_PROFILES, _parse_nmcli_line, is_rebuildable_wifi_profile,
)
from backend.shared.background import BackgroundTaskSet


logger = logging.getLogger(__name__)


# Backend port on a fresh speaker (it runs as a server, listening on 8000).
SETUP_API_PORT = 8000

# Each nmcli call not given a cap of its own.
NMCLI_TIMEOUT = 10.0

# Allow NM up to this long to associate with the speaker hotspot.
HOTSPOT_CONNECT_TIMEOUT = 30.0

# Allow NM up to this long to bring the original wifi back.
RESTORE_CONNECT_TIMEOUT = 30.0

# After hotspot association, wait for the speaker's DHCP lease to install a
# default route. NM returns from `connection up` before the lease is necessarily
# applied, so this is polled rather than slept through once.
GATEWAY_WAIT_TIMEOUT = 15.0
GATEWAY_POLL_INTERVAL = 1.0

# Total HTTP timeout for the become-client request (includes server-side
# atomic write + nmcli profile save before it returns).
BECOME_CLIENT_TIMEOUT = 20.0

WLAN_INTERFACE = "wlan0"

# The return to the server's own WiFi at its worst: the temporary profile
# deleted, the home connection brought up, wlan0 released when that fails.
RESTORE_WORST_CASE = NMCLI_TIMEOUT + RESTORE_CONNECT_TIMEOUT + NMCLI_TIMEOUT

# The adoption is answered within this, counted from the request: past the
# client's ADOPT_TIMEOUT_MS (frontend/src/stores/discoveryStore.js, held above
# this by tests/architecture/test_proxy_timeouts.py) the outcome reaches nobody.
ADOPTION_DEADLINE = 110.0

# The steps up to the push are cut at this, so that the restore fits before the
# answer: on a WiFi-only server, the answer reaches the caller only once wlan0
# is home again. Each step is capped, but stalled just under every cap they
# add up to ~120 s on their own.
STEPS_DEADLINE = ADOPTION_DEADLINE - RESTORE_WORST_CASE


class AdoptionError(Exception):
    """Adoption failure with a stable code for UI mapping."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass
class _Verdict:
    """What an adoption settled before the server's WiFi was restored."""

    result: Optional[dict] = None
    error: Optional[AdoptionError] = None


class WifiAdoptionService:
    """Orchestrates wifi-based adoption of a fresh Milō speaker."""

    def __init__(self, network_service):
        self.network_service = network_service
        self.logger = logger
        self._lock = asyncio.Lock()
        self._bg = BackgroundTaskSet(logger, "wifi_adoption")

    async def cleanup(self) -> None:
        await self._bg.cancel_all()

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
        if not wifi_ssid:
            raise AdoptionError("invalid_target_wifi", "Target wifi SSID is required")

        now = asyncio.get_running_loop().time()
        verdict = _Verdict()
        adoption = self._bg.spawn(
            self._adopt_by(
                now + STEPS_DEADLINE, verdict,
                ssid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password,
            ),
            label="adopt_speaker",
        )
        # Shielded: a deadline ends this wait, never the restore under way.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout_at(now + ADOPTION_DEADLINE):
                await asyncio.shield(adoption)
        if verdict.error is not None:
            raise verdict.error
        if verdict.result is None:
            raise AdoptionError("adoption_timeout", f"Not settled within {STEPS_DEADLINE:.0f}s")
        return verdict.result

    async def _adopt_by(
        self,
        deadline: float,
        verdict: _Verdict,
        ssid: str,
        audio_id: str,
        speaker_name: str,
        speaker_type: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> None:
        """Settle `verdict` by `deadline`, then bring the server's WiFi back.

        The lock and the recovery hold are let go only once the restore is done,
        deadline or not: the next adoption and the recovery AP both want the
        radio the restore is using.
        """
        async with contextlib.AsyncExitStack() as held:
            switched, original_connection = False, None
            try:
                async with asyncio.timeout_at(deadline):
                    await held.enter_async_context(self._lock)
                    await held.enter_async_context(self.network_service.recovery_held())
                    await self._refuse_unadoptable(wifi_ssid)
                    original_connection = await self._get_active_wifi_name()
                    self.logger.info(
                        "Adopting speaker '%s' (server wifi='%s', target wifi='%s')",
                        ssid, original_connection or "<none>", wifi_ssid,
                    )
                    switched = True
                    gateway = await self._join_and_push(
                        ssid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password
                    )
                verdict.result = {"ssid": ssid, "gateway": gateway}
            except TimeoutError:
                self.logger.info("Adoption of '%s' ran out of time", ssid)
            except AdoptionError as e:
                verdict.error = e
            finally:
                if switched:
                    await self._cleanup_temp_profile()
                    await self._restore_connection(original_connection)

        if verdict.result is not None:
            self.logger.info(
                "Speaker '%s' adoption succeeded; device will reboot and join '%s'",
                ssid, wifi_ssid,
            )

    async def _refuse_unadoptable(self, wifi_ssid: str) -> None:
        # Read inside the hold: entering it lets a raise already under way
        # finish first.
        if self.network_service.hotspot_active:
            raise AdoptionError(
                "server_in_hotspot_mode",
                "Cannot adopt while broadcasting the setup hotspot",
            )
        # The speaker rebuilds its target as a visible wpa-psk profile. Only
        # the server's own network can be judged from here, a typed one
        # cannot; read under the lock, while no other adoption holds wlan0.
        profile = await self.network_service.get_active_wifi_profile()
        if profile is not None and profile["ssid"] == wifi_ssid and not is_rebuildable_wifi_profile(profile):
            kind = "hidden" if profile["hidden"] else profile["key_mgmt"]
            raise AdoptionError(
                "unsupported_target_wifi",
                f"'{wifi_ssid}' is a {kind} network: the speaker could not rebuild it from its name and key",
            )

    async def _join_and_push(
        self,
        ssid: str,
        audio_id: str,
        speaker_name: str,
        speaker_type: str,
        wifi_ssid: str,
        wifi_password: str,
    ) -> str:
        await self._connect_to_hotspot(ssid)
        gateway = await self._wait_for_default_gateway()
        if not gateway:
            raise AdoptionError(
                "no_gateway",
                f"No default route on {WLAN_INTERFACE} {GATEWAY_WAIT_TIMEOUT:.0f}s "
                f"after associating with the speaker hotspot",
            )
        await self._push_become_client(
            gateway, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password
        )
        return gateway

    async def _connect_to_hotspot(self, ssid: str) -> None:
        await self._run_nmcli("connection", "delete", ADOPTION_PROFILE)

        # No wifi-sec setting at all: the setup hotspot is open, and NM reads
        # `key-mgmt=none` as *WEP with static keys*, then refuses to activate
        # without a wep-key0 it can never be given ("Secrets were required, but
        # not provided"). Omitting the security block is how NetworkService
        # joins open networks too.
        # autoconnect=no: this profile is torn down in a `finally`, but a crash
        # or power cut before that would otherwise leave the server permanently
        # re-joining the speaker's hotspot instead of the home network.
        rc, _, stderr = await self._run_nmcli(
            "connection", "add",
            "type", "wifi",
            "ifname", WLAN_INTERFACE,
            "con-name", ADOPTION_PROFILE,
            "ssid", ssid,
            "connection.autoconnect", "no",
        )
        if rc != 0:
            raise AdoptionError(
                "hotspot_connect_failed",
                f"Failed to create temp profile for '{ssid}': {stderr}",
            )

        rc, _, stderr = await self._run_nmcli(
            "connection", "up", ADOPTION_PROFILE, timeout=HOTSPOT_CONNECT_TIMEOUT
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

    async def _cleanup_temp_profile(self) -> None:
        rc, _, stderr = await self._run_nmcli("connection", "delete", ADOPTION_PROFILE)
        if rc != 0:
            self.logger.warning(
                "Failed to delete temp hotspot profile '%s': %s", ADOPTION_PROFILE, stderr
            )

    async def _restore_connection(self, name: Optional[str]) -> None:
        if not name:
            return
        rc, _, stderr = await self._run_nmcli(
            "connection", "up", name, timeout=RESTORE_CONNECT_TIMEOUT
        )
        if rc == 0:
            return
        # Connection up failed (home AP still hidden by the speaker hotspot,
        # corrupted profile, etc.). Force NM to release wlan0 so it autoconnects
        # to the best known profile once the speaker stops broadcasting.
        self.logger.error(
            "Failed to restore wifi connection '%s': %s — falling back to nmcli device disconnect",
            name, stderr,
        )
        await self._run_nmcli("device", "disconnect", WLAN_INTERFACE)

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
            if name in OWN_PROFILES:
                continue
            return name
        return None

    async def _wait_for_default_gateway(self) -> Optional[str]:
        """Poll until the speaker's DHCP lease installs a default route, or give up."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + GATEWAY_WAIT_TIMEOUT
        while True:
            gateway = await self._get_default_gateway()
            if gateway:
                return gateway
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(GATEWAY_POLL_INTERVAL)

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
        except asyncio.CancelledError:
            if proc:
                proc.kill()
            raise
        match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", stdout.decode())
        return match.group(1) if match else None

    async def _run_nmcli(
        self, *args: str, timeout: float = NMCLI_TIMEOUT
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
        except asyncio.CancelledError:
            # The adoption's deadline: an association it gave up on must not
            # carry on under the restore.
            if proc:
                proc.kill()
            raise
