# backend/core/equalizer/sync.py
"""
Equalizer Settings Sync Service - Manages client equalizer settings persistence and synchronization.

This service handles:
- Persistent storage of equalizer settings for remote clients
- Synchronization of equalizer settings between clients in a multiroom setup
- Settings categories: compressor, loudness, filters, volume
"""
import asyncio
import json
import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from backend.config.constants import CLIENT_EQUALIZER_FILE
from backend.core.equalizer.client_proxy import is_ip_address
from backend.shared.decorators import handle_errors

if TYPE_CHECKING:
    from backend.core.equalizer.client_proxy import EqualizerClientProxyService


class EqualizerSettingsSyncService:
    """
    Service for persisting and synchronizing equalizer settings across clients.

    Manages the client_equalizer.json file for storing settings of remote clients,
    and provides synchronization between source and target clients.

    IMPORTANT: This service uses mac_id for storage (client_equalizer.json keys)
    but resolves to IP addresses for proxy calls via client_registry.
    """

    # Default equalizer settings for standalone clients (flat EQ, effects off)
    DEFAULT_EQUALIZER_SETTINGS = {
        "filters": {},
        "compressor": {"enabled": False, "threshold": -20, "ratio": 4, "attack": 10, "release": 100},
        "loudness": {"enabled": False, "high_boost": 5.0, "low_boost": 8.0},
    }

    def __init__(
        self,
        proxy_service: "EqualizerClientProxyService" = None,
        camilladsp_service=None,
        client_registry=None
    ):
        """
        Initialize the sync service.

        Args:
            proxy_service: Service for proxying requests to remote clients
            camilladsp_service: Local CamillaDSP service for the main Milo unit
            client_registry: Service for looking up client IP addresses
        """
        self.proxy_service = proxy_service
        self.camilladsp_service = camilladsp_service
        self._registry = client_registry
        self.logger = logging.getLogger(__name__)
        self._lock = asyncio.Lock()

    def set_proxy_service(self, proxy_service: "EqualizerClientProxyService") -> None:
        """Set the proxy service (for dependency injection after init)."""
        self.proxy_service = proxy_service

    def set_camilladsp_service(self, camilladsp_service) -> None:
        """Set the CamillaDSP service (for dependency injection after init)."""
        self.camilladsp_service = camilladsp_service

    def set_registry(self, registry) -> None:
        """Set the client registry (for dependency injection after init)."""
        self._registry = registry

    # =========================================================================
    # Settings Persistence
    # =========================================================================

    @handle_errors(default={})
    async def load_settings(self) -> Dict[str, Any]:
        """
        Load all client equalizer settings from disk.

        Returns:
            Dictionary of hostname -> settings
        """
        def _read_file():
            if CLIENT_EQUALIZER_FILE.exists():
                with open(CLIENT_EQUALIZER_FILE, "r") as f:
                    return json.load(f)
            return {}

        return await asyncio.to_thread(_read_file)

    @handle_errors(default=None)
    async def save_settings(self, settings: Dict[str, Any]) -> None:
        """
        Save all client equalizer settings to disk atomically.

        Args:
            settings: Dictionary of hostname -> settings to save
        """
        def _write_file():
            CLIENT_EQUALIZER_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = CLIENT_EQUALIZER_FILE.with_suffix(".json.tmp")
            with open(temp_file, "w") as f:
                json.dump(settings, f, indent=2)
            temp_file.replace(CLIENT_EQUALIZER_FILE)

        async with self._lock:
            await asyncio.to_thread(_write_file)

    async def get_client_settings(self, hostname: str) -> Dict[str, Any]:
        """
        Get saved equalizer settings for a specific client.

        Args:
            hostname: The client hostname

        Returns:
            Dictionary of settings for the client
        """
        settings = await self.load_settings()
        return settings.get(hostname, {})

    async def update_client_settings(
        self,
        hostname: str,
        category: str,
        data: Dict[str, Any]
    ) -> None:
        """
        Update and persist equalizer settings for a client.

        Args:
            hostname: The client hostname
            category: Setting category (compressor, loudness, etc.)
            data: The setting data to save
        """
        settings = await self.load_settings()
        if hostname not in settings:
            settings[hostname] = {}
        settings[hostname][category] = data
        await self.save_settings(settings)
        self.logger.info(f"Saved {category} settings for client {hostname}")

    # =========================================================================
    # Settings Synchronization
    # =========================================================================

    async def _get_source_settings(self, source_client: str) -> Dict[str, Any]:
        """
        Get all equalizer settings from a source client.

        Args:
            source_client: MAC address of source client

        Returns:
            Dictionary of settings by category
        """
        if self._registry.is_local_client(source_client):
            if not self.camilladsp_service:
                raise ValueError("Equalizer service not available for local settings")
            return {
                'compressor': await self.camilladsp_service.get_compressor(),
                'loudness': await self.camilladsp_service.get_loudness(),
                'filters': await self.camilladsp_service.get_filters(),
                'volume': await self.camilladsp_service.get_volume()
            }
        else:
            # Get from remote client - need to look up IP
            if not self.proxy_service:
                raise ValueError("Proxy service not available for remote settings")

            client_ip = self._registry.get_client_ip(source_client)
            if not client_ip:
                raise ValueError(f"Cannot resolve IP for client {source_client}")

            source_settings = {}
            for category in ['compressor', 'loudness', 'volume']:
                try:
                    source_settings[category] = await self.proxy_service.request(
                        client_ip, "GET", f"/equalizer/{category}"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to get {category} from {source_client}: {e}")

            # Filters have a different response structure
            try:
                filters_resp = await self.proxy_service.request(client_ip, "GET", "/equalizer/filters")
                source_settings['filters'] = filters_resp.get('filters', [])
            except Exception as e:
                self.logger.warning(f"Failed to get filters from {source_client}: {e}")

            return source_settings

    @handle_errors(default=False, level='warning')
    async def _push_setting_to_target(
        self,
        target: str,
        category: str,
        data: Dict[str, Any],
        filter_id: str = None
    ) -> bool:
        """
        Push a single setting to a target client (local or remote).

        Args:
            target: MAC address of target client
            category: Setting category (compressor, loudness, or filter)
            data: Setting data
            filter_id: Filter ID (required when category is 'filter')

        Returns:
            True if successful, False otherwise
        """
        if self._registry.is_local_client(target):
            if not self.camilladsp_service:
                return False
            if category == 'compressor':
                await self.camilladsp_service.set_compressor(**data)
            elif category == 'loudness':
                await self.camilladsp_service.set_loudness(**data)
            elif category == 'filter' and filter_id:
                await self.camilladsp_service.set_filter(filter_id, **data)
            elif category == 'volume':
                await self.camilladsp_service.set_volume(data.get("volume", data.get("main", 0)))
            elif category == 'mute':
                await self.camilladsp_service.set_mute(data.get("muted", False))
        else:
            client_ip = self._registry.get_client_ip(target)
            if not self.proxy_service or not client_ip:
                return False
            path = f"/equalizer/filter/{filter_id}" if filter_id else f"/equalizer/{category}"
            await self.proxy_service.request(client_ip, "PUT", path, data)
            if not filter_id:
                await self.update_client_settings(target, category, data)
        return True

    # =========================================================================
    # Standalone Client Equalizer Settings (Story 5.2)
    # =========================================================================

    def get_default_settings(self) -> Dict[str, Any]:
        """
        Get default equalizer settings for standalone clients.

        Returns flat EQ, compressor off, loudness off as per AC3.

        Returns:
            Dictionary with default settings for filters, compressor, loudness
        """
        import copy
        return copy.deepcopy(self.DEFAULT_EQUALIZER_SETTINGS)

    async def load_standalone_settings(self, client_id: str) -> Optional[Dict[str, Any]]:
        """
        Load standalone equalizer settings for a client from persistence.

        Args:
            client_id: The client identifier (mac_id or 'local')

        Returns:
            Dictionary of equalizer settings or None if not found
        """
        settings = await self.load_settings()
        return settings.get(client_id)

    async def save_standalone_settings(
        self,
        client_id: str,
        equalizer_settings: Dict[str, Any]
    ) -> None:
        """
        Save standalone equalizer settings for a client.

        Args:
            client_id: The client identifier (mac_id or 'local')
            equalizer_settings: Dictionary with filters, compressor, loudness
        """
        all_settings = await self.load_settings()
        all_settings[client_id] = equalizer_settings
        await self.save_settings(all_settings)
        self.logger.info(f"Saved standalone equalizer settings for {client_id}")


