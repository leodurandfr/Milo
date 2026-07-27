# backend/core/multiroom/equalizer_router.py
"""
EqualizerRouter - Centralized Equalizer command routing.

Routes equalizer commands to local camilladsp_service or remote proxy_service
based on client IP address. Eliminates if/else duplication in endpoints.

Architecture:
- Lookup client in ClientRegistry by mac_id
- If client.is_local → local camilladsp_service
- Else → proxy_service to remote client
"""
import logging
from typing import Any, Dict, Callable, Awaitable, TYPE_CHECKING

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

    def _get_client(self, mac_id: str):
        return self._registry.get_client(mac_id) if self._registry else None

    def _is_local(self, client) -> bool:
        """Check if client is local (running on this device)."""
        return client.is_local if client else False

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
            Action result dict with status
        """
        client = self._get_client(mac_id)

        if not client:
            # Client not in registry — fall back to local CamillaDSP.
            # This happens in non-multiroom mode where the registry is empty
            # (populated only by Snapcast connections).
            if self._camilladsp_service:
                logger.debug(f"Client {mac_id} not in registry, falling back to local CamillaDSP for {action_name}")
                return await local_action()
            logger.warning(f"Client {mac_id} not found for {action_name}")
            return {"status": "error", "message": f"Client {mac_id} not found"}

        if self._is_local(client):
            logger.debug(f"Routing {action_name} to local CamillaDSP for {mac_id}")
            return await local_action()
        else:
            if not self._proxy_service:
                return {"status": "error", "message": "Proxy service not available"}

            if not force and not client.online:
                logger.debug(f"Skipping offline client {mac_id} for {action_name}")
                return {"status": "skipped", "reason": "client_offline"}

            logger.debug(f"Routing {action_name} to proxy for {mac_id} ({client.ip})")
            return await remote_action(client.ip)

    # === VOLUME ===

    async def set_volume(self, mac_id: str, volume_db: float, force: bool = False) -> Dict[str, Any]:
        """Set volume for a client."""
        client = self._get_client(mac_id)
        if client and not client.volume_control:
            logger.debug(f"Skipping volume for DAC client {mac_id}")
            return {"status": "skipped", "reason": "external_volume_control"}

        async def local():
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_volume(volume_db)
                return {"status": "success" if success else "error", "volume": volume_db}
            return {"status": "error", "message": "Equalizer service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/volume", {"volume": volume_db})
            return result

        return await self._route(mac_id, local, remote, "set_volume", force=force)

    async def set_mute(self, mac_id: str, muted: bool, force: bool = False) -> Dict[str, Any]:
        """Set mute for a client."""
        async def local():
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_mute(muted)
                return {"status": "success" if success else "error", "mute": muted}
            return {"status": "error", "message": "Equalizer service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/mute", {"muted": muted})
            return result

        return await self._route(mac_id, local, remote, "set_mute", force=force)

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
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_filter(
                    filter_id=filter_id,
                    freq=filter_data.get("freq"),
                    gain=filter_data.get("gain"),
                    q=filter_data.get("q"),
                    filter_type=filter_data.get("filter_type"),
                    persist=persist,
                )
                return {"status": "success" if success else "error", "filter_id": filter_id}
            return {"status": "error", "message": "Equalizer service not available"}

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
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_compressor(
                    **settings, persist=persist
                )
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "Equalizer service not available"}

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
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_loudness(
                    **settings, persist=persist
                )
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "Equalizer service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/equalizer/loudness", settings)
            return result

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
            if self._camilladsp_service:
                success = await self._camilladsp_service.set_mono(
                    **settings, persist=persist
                )
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "Equalizer service not available"}

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

    async def get_volume(self, mac_id: str) -> Dict[str, Any]:
        """Get volume for a client."""
        async def local():
            if self._camilladsp_service:
                vol = await self._camilladsp_service.get_volume()
                return {"main": vol.get("main", -60), "mute": vol.get("mute", False)}
            return {"main": -60, "mute": False}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "GET", "/equalizer/volume")

        return await self._route(mac_id, local, remote, "get_volume")
