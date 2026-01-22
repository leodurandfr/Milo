# backend/core/multiroom/dsp_router.py
"""
DspRouter - Centralized DSP command routing.

Routes DSP commands to local dsp_service or remote proxy_service
based on client IP address. Eliminates if/else duplication in endpoints.

Architecture:
- Lookup client in ClientRegistry by mac_id
- If client.ip == "127.0.0.1" → local dsp_service
- Else → proxy_service to remote client
"""
import logging
from typing import Any, Dict, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class DspRouter:
    """
    Routes DSP commands to appropriate service based on client location.

    Usage:
        router = DspRouter(registry, dsp_service, proxy_service)
        await router.set_volume(mac_id, volume_db)
        await router.set_mute(mac_id, muted)
    """

    def __init__(
        self,
        client_registry,
        dsp_service,
        proxy_service,
        volume_service=None
    ):
        self._registry = client_registry
        self._dsp_service = dsp_service
        self._proxy_service = proxy_service
        self._volume_service = volume_service

    def _get_client(self, mac_id: str):
        """Get client from registry."""
        return self._registry.get_client(mac_id) if self._registry else None

    def _is_local(self, client) -> bool:
        """Check if client is local (running on this device)."""
        return client and client.ip == "127.0.0.1"

    async def _route(
        self,
        mac_id: str,
        local_action: Callable[[], Awaitable[Any]],
        remote_action: Callable[[str], Awaitable[Any]],
        action_name: str = "action"
    ) -> Dict[str, Any]:
        """
        Route action to local or remote based on client IP.

        Args:
            mac_id: Client MAC address
            local_action: Async function to call for local client
            remote_action: Async function to call for remote (receives IP)
            action_name: Name for logging

        Returns:
            Action result dict with status
        """
        client = self._get_client(mac_id)

        if not client:
            logger.warning(f"Client {mac_id} not found for {action_name}")
            return {"status": "error", "message": f"Client {mac_id} not found"}

        if self._is_local(client):
            logger.debug(f"Routing {action_name} to local DSP for {mac_id}")
            return await local_action()
        else:
            if not self._proxy_service:
                return {"status": "error", "message": "Proxy service not available"}

            if not client.online:
                logger.debug(f"Skipping offline client {mac_id} for {action_name}")
                return {"status": "skipped", "reason": "client_offline"}

            logger.debug(f"Routing {action_name} to proxy for {mac_id} ({client.ip})")
            return await remote_action(client.ip)

    # === VOLUME ===

    async def set_volume(self, mac_id: str, volume_db: float) -> Dict[str, Any]:
        """Set volume for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_volume(volume_db)
                return {"status": "success" if success else "error", "volume": volume_db}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/volume", {"volume": volume_db})
            return result

        return await self._route(mac_id, local, remote, "set_volume")

    async def set_mute(self, mac_id: str, muted: bool) -> Dict[str, Any]:
        """Set mute for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_mute(muted)
                return {"status": "success" if success else "error", "mute": muted}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/mute", {"muted": muted})
            return result

        return await self._route(mac_id, local, remote, "set_mute")

    # === PRESETS ===

    async def load_preset(self, mac_id: str, preset_id: str) -> Dict[str, Any]:
        """Load a preset for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.load_preset(preset_id)
                return {"status": "success" if success else "error", "preset_id": preset_id}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", f"/dsp/preset/{preset_id}")
            return result

        return await self._route(mac_id, local, remote, "load_preset")

    # === FILTERS ===

    async def get_filters(self, mac_id: str) -> Dict[str, Any]:
        """Get all filters for a client."""
        async def local():
            if self._dsp_service:
                filters = await self._dsp_service.get_filters()
                return {"filters": filters}
            return {"filters": [], "error": "DSP service not available"}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "GET", "/dsp/filters")

        return await self._route(mac_id, local, remote, "get_filters")

    async def reset_filters(self, mac_id: str) -> Dict[str, Any]:
        """Reset all filters to flat for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.reset_filters()
                if success:
                    return {"status": "success", "message": "All filters reset to flat"}
                return {"status": "error", "message": "Failed to reset filters"}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "POST", "/dsp/reset")

        return await self._route(mac_id, local, remote, "reset_filters")

    async def update_filter(
        self,
        mac_id: str,
        filter_id: str,
        filter_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a filter for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_filter(
                    filter_id=filter_id,
                    freq=filter_data.get("freq"),
                    gain=filter_data.get("gain"),
                    q=filter_data.get("q"),
                    filter_type=filter_data.get("filter_type"),
                    enabled=filter_data.get("enabled", True)
                )
                return {"status": "success" if success else "error", "filter_id": filter_id}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(
                ip, "PUT", f"/dsp/filter/{filter_id}", filter_data
            )
            return result

        return await self._route(mac_id, local, remote, "update_filter")

    # === COMPRESSOR ===

    async def get_compressor(self, mac_id: str) -> Dict[str, Any]:
        """Get compressor settings for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_compressor()
            return {"enabled": False, "error": "DSP service not available"}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "GET", "/dsp/compressor")

        return await self._route(mac_id, local, remote, "get_compressor")

    async def set_compressor(self, mac_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Set compressor settings for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_compressor(**settings)
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/compressor", settings)
            return result

        return await self._route(mac_id, local, remote, "set_compressor")

    # === LOUDNESS ===

    async def get_loudness(self, mac_id: str) -> Dict[str, Any]:
        """Get loudness settings for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_loudness()
            return {"enabled": False, "error": "DSP service not available"}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "GET", "/dsp/loudness")

        return await self._route(mac_id, local, remote, "get_loudness")

    async def set_loudness(self, mac_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Set loudness settings for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_loudness(**settings)
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/loudness", settings)
            return result

        return await self._route(mac_id, local, remote, "set_loudness")

    # === DSP ENABLED ===

    async def set_dsp_enabled(self, mac_id: str, enabled: bool, routing_service=None) -> Dict[str, Any]:
        """Set DSP effects enabled state for a client."""
        async def local():
            if routing_service:
                success = await routing_service.set_dsp_effects_enabled(enabled)
                return {"status": "success" if success else "error", "enabled": enabled}
            return {"status": "error", "message": "Routing service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/enabled", {"enabled": enabled})
            return result

        return await self._route(mac_id, local, remote, "set_dsp_enabled")

    async def get_dsp_enabled(self, mac_id: str, routing_service=None) -> Dict[str, Any]:
        """Get DSP effects enabled state for a client."""
        async def local():
            if routing_service:
                return {"enabled": routing_service.dsp_effects_enabled}
            return {"enabled": True}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "GET", "/dsp/enabled")
            return result

        return await self._route(mac_id, local, remote, "get_dsp_enabled")

    # === STATUS ===

    async def get_status(self, mac_id: str) -> Dict[str, Any]:
        """Get DSP status for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_status()
            return {"available": False, "error": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "GET", "/dsp/status")
            return result

        return await self._route(mac_id, local, remote, "get_status")

    async def get_levels(self, mac_id: str) -> Dict[str, Any]:
        """Get audio levels for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_levels()
            return {"available": False}

        async def remote(ip: str):
            return await self._proxy_service.get_dsp_levels(ip)

        return await self._route(mac_id, local, remote, "get_levels")

    async def get_volume(self, mac_id: str) -> Dict[str, Any]:
        """Get volume for a client."""
        async def local():
            if self._dsp_service:
                vol = await self._dsp_service.get_volume()
                return {"main": vol.get("main", -60), "mute": vol.get("mute", False)}
            return {"main": -60, "mute": False}

        async def remote(ip: str):
            return await self._proxy_service.request(ip, "GET", "/dsp/volume")

        return await self._route(mac_id, local, remote, "get_volume")

    # === HELPER: Check if mac_id is local ===

    def is_local_client(self, mac_id: str) -> bool:
        """Check if mac_id belongs to the local client."""
        client = self._get_client(mac_id)
        return self._is_local(client)
