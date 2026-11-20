# backend/core/multiroom/equalizer_router.py
"""
EqualizerRouter - Centralized Equalizer command routing.

Routes equalizer commands to local camilladsp_service or remote proxy_service
based on client IP address. Eliminates if/else duplication in endpoints.

Architecture:
- Lookup client in ClientRegistry by mac_id
- If client.is_local → local camilladsp_service
- Else → proxy_service to remote client
- Not in the registry → local camilladsp_service only when the mac names this
  unit (the `LOCAL_TARGET` sentinel or the MAC the volume service keys the
  local level by, or no registry at all); any other mac is a satellite the
  registry forgot, and is refused

A command answers one way and refuses one way, whichever side it reached: it
returns its answer — applied, or skipped for a reason that is not a failure
(`client_offline`, `external_volume_control`) — or raises `CommandNotApplied`.
This unit's DSP refusing raises it (502) exactly as a satellite refusing does
(`SatelliteUnreachable`, a subclass carrying the satellite's status).
"""
import logging
from typing import Any, Dict, Callable, Awaitable, Optional, TYPE_CHECKING

from backend.config.constants import LOUDNESS_REFERENCE_DB, MAX_VOLUME_DB
from backend.core.equalizer.client_proxy import CommandNotApplied
from backend.core.equalizer.multiroom_service import LOCAL_TARGET
from backend.core.multiroom.identity import get_local_mac

if TYPE_CHECKING:
    from backend.core.equalizer.client_proxy import EqualizerClientProxyService
    from backend.core.equalizer.service import CamillaDSPService

logger = logging.getLogger(__name__)


class EqualizerRouter:
    """
    Routes equalizer commands to appropriate service based on client location.

    Usage:
        router = EqualizerRouter(registry, camilladsp_service, proxy_service)
        await router.set_volume(mac_id, volume_db)
        await router.set_mute(mac_id, muted)
    """

    def __init__(
        self,
        client_registry,
        camilladsp_service: "CamillaDSPService",
        proxy_service: "EqualizerClientProxyService"
    ):
        self._registry = client_registry
        self._camilladsp_service = camilladsp_service
        self._proxy_service = proxy_service
        # Unwired, the local speaker counts as managed: the direction that
        # attenuates, never the one that sends full scale.
        self._local_volume_control: Callable[[], bool] = lambda: True
        # Unwired, this unit is the MAC the board reports.
        self._local_mac: Callable[[], Optional[str]] = lambda: get_local_mac()

    def set_local_volume_control_source(self, source: Callable[[], bool]) -> None:
        """Hand in the reader of the local unit's DAC flag (VolumeService's).

        The registry carries a copy of that flag on the local client, but the
        copy is written after the flag moves and only resynced at an admission,
        so a local level decided on it was unity for a managed card (full
        scale) or the stored level for a DAC one. The satellites' flags stay
        the registry's: they live on the satellite and arrive with it.
        """
        self._local_volume_control = source

    def set_local_mac_source(self, source: Callable[[], Optional[str]]) -> None:
        """Hand in the reader of the MAC the volume service keys the local level by.

        That key is the volume store's, restored from last_volume.json, and
        nothing holds it equal to the board's MAC: an SD card moved to another
        Pi brings the previous board's key along. Taken from the board instead,
        a direct-mode unit (empty registry) had every local command refused as
        an unknown client, and CamillaDSP stayed muted at its startup floor.
        """
        self._local_mac = source

    def _get_client(self, mac_id: str):
        return self._registry.get_client(mac_id) if self._registry else None

    def _local_dsp(self) -> "CamillaDSPService":
        """This unit's CamillaDSPService, or a 503 when it has none."""
        if not self._camilladsp_service:
            raise CommandNotApplied("Equalizer service not available", status_code=503)
        return self._camilladsp_service

    @staticmethod
    def _applied(success: bool, action: str, **answer: Any) -> Dict[str, Any]:
        """A local setter's verdict in the router's one shape: the answer, or a raise."""
        if not success:
            raise CommandNotApplied(f"The local CamillaDSP refused {action}")
        return {"status": "success", **answer}

    def _is_local(self, client) -> bool:
        """Check if client is local (running on this device)."""
        return client.is_local if client else False

    def _names_this_unit(self, mac_id: str) -> bool:
        """Whether a mac the registry does not hold still addresses this unit.

        The registry is populated only by Snapcast connections, so in direct
        mode it is empty and this unit's own targets arrive unregistered: the
        `LOCAL_TARGET` sentinel of the EQ API, and the MAC VolumeService keys
        the local level by, read from it (`set_local_mac_source`). Any other
        mac is a satellite the registry forgot, deleted while its admission was
        retrying for one, and answering it here would put that room's level
        and unmute on this unit's speaker. With no registry at all, nothing can
        have been forgotten.
        """
        return (
            self._registry is None
            or mac_id == LOCAL_TARGET
            or mac_id == self._local_mac()
        )

    async def _route(
        self,
        mac_id: str,
        local_action: Callable[[], Awaitable[Any]],
        remote_action: Callable[[str], Awaitable[Any]],
        action_name: str = "action",
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Route action to local or remote based on client IP.

        Args:
            mac_id: Client MAC address
            local_action: Async function to call for local client
            remote_action: Async function to call for remote (receives IP)
            action_name: Name for logging
            force: If True, skip the online check (used during reconnection
                sync when the client is registered but not yet marked online)

        Returns:
            Action result dict with status: success, or skipped for an offline
            satellite

        Raises:
            CommandNotApplied: a satellite the registry forgot (404), no proxy
                to reach a satellite (503), or whatever the action raised
        """
        client = self._get_client(mac_id)

        if not client:
            if self._names_this_unit(mac_id):
                logger.debug(f"Client {mac_id} not in registry, falling back to local CamillaDSP for {action_name}")
                return await local_action()
            logger.warning(f"Client {mac_id} not found for {action_name}")
            raise CommandNotApplied(f"Client {mac_id} not found", status_code=404)

        if self._is_local(client):
            logger.debug(f"Routing {action_name} to local CamillaDSP for {mac_id}")
            return await local_action()
        else:
            if not self._proxy_service:
                raise CommandNotApplied("Proxy service not available", status_code=503)

            if not force and not client.online:
                logger.debug(f"Skipping offline client {mac_id} for {action_name}")
                return {"status": "skipped", "reason": "client_offline"}

            logger.debug(f"Routing {action_name} to proxy for {mac_id} ({client.ip})")
            return await remote_action(client.ip)

    # === VOLUME ===

    async def set_volume(self, mac_id: str, volume_db: float, force: bool = False) -> Dict[str, Any]:
        """Set volume for a client.

        A DAC client's external amp owns the level, so Milō asks for unity
        rather than for the level it would otherwise send — but it does ask.
        Writing nothing used to be the same thing, and it stopped being so the
        day CamillaDSP started at a floor: the fader would keep whatever its
        unit started it on, and the unmute that always follows
        (`websocket.py::_apply_target_volume_to_client`) would open the speaker
        on it. The caller still records the level the user asked for; this only
        decides what reaches the daemon, and on a real DAC path it is an
        idempotent no-op.

        Decided when the command leaves, from the local flag for this unit's
        own daemon and from the registry for a satellite — so a level already
        queued when the local flag flips leaves at unity, after the pin.
        """
        client = self._get_client(mac_id)

        def level(volume_control: bool) -> float:
            if volume_control:
                return volume_db
            logger.debug(f"Unity volume for DAC client {mac_id} (external amp owns the level)")
            return MAX_VOLUME_DB

        async def local():
            dsp = self._local_dsp()
            sent = level(self._local_volume_control())
            return self._applied(await dsp.set_volume(sent), "the volume", volume=sent)

        async def remote(ip: str):
            sent = level(client.volume_control)
            return await self._proxy_service.request(ip, "PUT", "/equalizer/volume", {"volume": sent})

        return await self._route(mac_id, local, remote, "set_volume", force=force)

    async def set_mute(self, mac_id: str, muted: bool, force: bool = False) -> Dict[str, Any]:
        """Set mute for a client."""
        async def local():
            return self._applied(
                await self._local_dsp().set_mute(muted), "the mute", mute=muted
            )

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/mute", {"muted": muted})
            return result

        return await self._route(mac_id, local, remote, "set_mute", force=force)

    # === LEVEL TRIM ===

    async def set_gain(self, mac_id: str, gain_db: float, force: bool = False) -> Dict[str, Any]:
        """Set a client's level trim (a fixed Gain stage, in dB).

        A DAC client is skipped: its external amp owns the level, and Milō
        attenuates nothing on that path. (`set_volume` does not skip a DAC — it
        sends unity instead, since a fader left where the unit started it would
        be opened by the unmute that follows.) So a trim is cleared before the
        DAC flag is set, never after (`VolumeService._clear_local_gain` for this
        unit, `api/multiroom.py::_drop_trim_before_dac` for a satellite). The
        flag is read where `set_volume` reads it: the local one for this unit,
        the registry's for a satellite.

        A trim only cuts. The bound is applied here as well as on each DSP
        because a satellite on an older release clamps to +12 dB, and the
        registry can still hold a positive trim the admission replays.
        """
        gain_db = min(gain_db, 0.0)
        client = self._get_client(mac_id)
        skipped = {"status": "skipped", "reason": "external_volume_control"}

        async def local():
            if not self._local_volume_control():
                logger.debug(f"Skipping level trim for DAC client {mac_id}")
                return skipped
            return self._applied(
                await self._local_dsp().set_gain(gain_db), "the level trim", gain_db=gain_db
            )

        async def remote(ip: str):
            if not client.volume_control:
                logger.debug(f"Skipping level trim for DAC client {mac_id}")
                return skipped
            return await self._proxy_service.request(
                ip, "PUT", "/equalizer/gain", {"gain_db": gain_db}
            )

        return await self._route(mac_id, local, remote, "set_gain", force=force)

    # === FILTERS ===

    async def update_filter(
        self,
        mac_id: str,
        filter_id: str,
        filter_data: Dict[str, Any],
        persist: bool = True,
    ) -> Dict[str, Any]:
        """
        Update a filter for a client.

        Args:
            mac_id: Client MAC address
            filter_id: Filter ID to update
            filter_data: Dict with freq, gain, q, filter_type. Carries no
                `enabled`: pipeline membership is the master toggle's, so a
                targeted band edit must not re-pipe a band on a bypassed client.
            persist: Save to settings (False for zone updates using registry)
        """
        async def local():
            success = await self._local_dsp().set_filter(
                filter_id=filter_id,
                freq=filter_data.get("freq"),
                gain=filter_data.get("gain"),
                q=filter_data.get("q"),
                filter_type=filter_data.get("filter_type"),
                persist=persist,
            )
            return self._applied(success, f"band {filter_id}", filter_id=filter_id)

        async def remote(ip: str):
            result = await self._proxy_service.request(
                ip, "PUT", f"/equalizer/filter/{filter_id}", filter_data
            )
            return result

        return await self._route(mac_id, local, remote, "update_filter")

    # === COMPRESSOR ===

    async def set_compressor(
        self,
        mac_id: str,
        settings: Dict[str, Any],
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Set compressor settings for a client."""
        async def local():
            return self._applied(
                await self._local_dsp().set_compressor(**settings, persist=persist), "the compressor"
            )

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/compressor", settings)
            return result

        return await self._route(mac_id, local, remote, "set_compressor")

    # === LOUDNESS ===

    async def set_loudness(
        self,
        mac_id: str,
        settings: Dict[str, Any],
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Set loudness settings for a client."""
        async def local():
            return self._applied(
                await self._local_dsp().set_loudness(**settings, persist=persist), "the loudness"
            )

        async def remote(ip: str):
            # The reference is Milō's, declared once; the satellite takes it as sent.
            body = {**settings, "reference_level": LOUDNESS_REFERENCE_DB}
            return await self._proxy_service.request(ip, "PUT", "/equalizer/loudness", body)

        return await self._route(mac_id, local, remote, "set_loudness")

    # === MONO ===

    async def set_mono(
        self,
        mac_id: str,
        settings: Dict[str, Any],
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Set mono/stereo mixing for a client."""
        async def local():
            return self._applied(
                await self._local_dsp().set_mono(**settings, persist=persist), "mono"
            )

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/mono", settings)
            return result

        return await self._route(mac_id, local, remote, "set_mono")

    # === STATUS ===

    async def get_status(self, mac_id: str) -> Dict[str, Any]:
        """Get equalizer status for a client."""
        async def local():
            if self._camilladsp_service:
                return await self._camilladsp_service.get_status()
            return {"available": False, "error": "Equalizer service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "GET", "/equalizer/status")
            return result

        return await self._route(mac_id, local, remote, "get_status")

    async def get_levels(self, mac_id: str) -> Dict[str, Any]:
        """Get audio levels for a client."""
        async def local():
            if self._camilladsp_service:
                return await self._camilladsp_service.get_levels()
            return {"available": False}

        async def remote(ip: str):
            return await self._proxy_service.get_equalizer_levels(ip)

        return await self._route(mac_id, local, remote, "get_levels")
