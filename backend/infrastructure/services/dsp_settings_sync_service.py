# backend/infrastructure/services/dsp_settings_sync_service.py
"""
DSP Settings Sync Service - Manages client DSP settings persistence and synchronization.

This service handles:
- Persistent storage of DSP settings for remote clients
- Synchronization of DSP settings between clients in a multiroom setup
- Settings categories: compressor, loudness, delay, filters, volume
"""
import asyncio
import json
import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from backend.config.constants import CLIENT_DSP_FILE

if TYPE_CHECKING:
    from backend.infrastructure.services.dsp_client_proxy_service import DspClientProxyService


class DspSettingsSyncService:
    """
    Service for persisting and synchronizing DSP settings across clients.

    Manages the client_dsp.json file for storing settings of remote clients,
    and provides synchronization between source and target clients.
    """

    # DSP setting categories that can be synced
    SYNC_CATEGORIES = ['compressor', 'loudness', 'delay', 'filters', 'volume']

    def __init__(
        self,
        proxy_service: "DspClientProxyService" = None,
        dsp_service=None
    ):
        """
        Initialize the sync service.

        Args:
            proxy_service: Service for proxying requests to remote clients
            dsp_service: Local CamillaDSP service for the main Milo unit
        """
        self.proxy_service = proxy_service
        self.dsp_service = dsp_service
        self.logger = logging.getLogger(__name__)
        self._lock = asyncio.Lock()

    def set_proxy_service(self, proxy_service: "DspClientProxyService") -> None:
        """Set the proxy service (for dependency injection after init)."""
        self.proxy_service = proxy_service

    def set_dsp_service(self, dsp_service) -> None:
        """Set the DSP service (for dependency injection after init)."""
        self.dsp_service = dsp_service

    # =========================================================================
    # Settings Persistence
    # =========================================================================

    async def load_settings(self) -> Dict[str, Any]:
        """
        Load all client DSP settings from disk.

        Returns:
            Dictionary of hostname -> settings
        """
        def _read_file():
            if CLIENT_DSP_FILE.exists():
                with open(CLIENT_DSP_FILE, "r") as f:
                    return json.load(f)
            return {}

        try:
            return await asyncio.to_thread(_read_file)
        except Exception as e:
            self.logger.error(f"Error loading client DSP settings: {e}")
            return {}

    async def save_settings(self, settings: Dict[str, Any]) -> None:
        """
        Save all client DSP settings to disk atomically.

        Args:
            settings: Dictionary of hostname -> settings to save
        """
        def _write_file():
            CLIENT_DSP_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp_file = CLIENT_DSP_FILE.with_suffix(".json.tmp")
            with open(temp_file, "w") as f:
                json.dump(settings, f, indent=2)
            temp_file.replace(CLIENT_DSP_FILE)

        async with self._lock:
            try:
                await asyncio.to_thread(_write_file)
            except Exception as e:
                self.logger.error(f"Error saving client DSP settings: {e}")

    async def get_client_settings(self, hostname: str) -> Dict[str, Any]:
        """
        Get saved DSP settings for a specific client.

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
        Update and persist DSP settings for a client.

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

    async def cleanup_duplicate_clients(
        self,
        active_clients: List[Dict[str, Any]]
    ) -> int:
        """
        Remove duplicate/stale client entries from client_dsp.json.

        When clients connect via different interfaces (eth0/wlan0), they may create
        duplicate entries with different identifiers. This method consolidates them
        using the current active client list as the source of truth.

        Args:
            active_clients: List of currently active clients from snapcast_service

        Returns:
            Number of entries removed
        """
        settings = await self.load_settings()
        if not settings:
            return 0

        # Build set of valid identifiers from active clients
        valid_ids = set()
        for client in active_clients:
            dsp_id = client.get("dsp_id")
            if dsp_id:
                valid_ids.add(dsp_id)
            # Also consider hostname as valid
            host = client.get("host")
            if host and host.startswith("milo-client"):
                valid_ids.add(host)

        # Find and remove stale entries
        stale_keys = []
        for key in settings.keys():
            # Keep 'local' always
            if key == "local":
                continue
            # Keep if it matches a valid identifier
            if key in valid_ids:
                continue
            # Check if this looks like an IP address that might be stale
            if self._is_ip_address(key) and key not in valid_ids:
                stale_keys.append(key)

        # Remove stale entries
        if stale_keys:
            for key in stale_keys:
                del settings[key]
            await self.save_settings(settings)
            self.logger.info(
                f"Cleaned up {len(stale_keys)} stale client entries: {stale_keys}"
            )

        return len(stale_keys)

    def _is_ip_address(self, value: str) -> bool:
        """Check if a string looks like an IP address."""
        parts = value.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False

    # =========================================================================
    # Settings Synchronization
    # =========================================================================

    async def _get_source_settings(self, source_client: str) -> Dict[str, Any]:
        """
        Get all DSP settings from a source client.

        Args:
            source_client: 'local' for main Milo, or hostname for remote client

        Returns:
            Dictionary of settings by category
        """
        if source_client == 'local':
            if not self.dsp_service:
                raise ValueError("DSP service not available for local settings")
            return {
                'compressor': await self.dsp_service.get_compressor(),
                'loudness': await self.dsp_service.get_loudness(),
                'delay': await self.dsp_service.get_delay(),
                'filters': await self.dsp_service.get_filters(),
                'volume': await self.dsp_service.get_volume()
            }
        else:
            # Get from remote client
            if not self.proxy_service:
                raise ValueError("Proxy service not available for remote settings")

            source_settings = {}
            for category in ['compressor', 'loudness', 'delay', 'volume']:
                try:
                    source_settings[category] = await self.proxy_service.request(
                        source_client, "GET", f"/dsp/{category}"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to get {category} from {source_client}: {e}")

            # Filters have a different response structure
            try:
                filters_resp = await self.proxy_service.request(source_client, "GET", "/dsp/filters")
                source_settings['filters'] = filters_resp.get('filters', [])
            except Exception as e:
                self.logger.warning(f"Failed to get filters from {source_client}: {e}")

            return source_settings

    async def _push_setting_to_target(
        self,
        target: str,
        category: str,
        data: Dict[str, Any]
    ) -> bool:
        """
        Push a single setting category to a target client.

        Args:
            target: 'local' or hostname
            category: Setting category
            data: Setting data

        Returns:
            True if successful, False otherwise
        """
        try:
            if target == 'local':
                if not self.dsp_service:
                    return False
                if category == 'compressor':
                    await self.dsp_service.set_compressor(**data)
                elif category == 'loudness':
                    await self.dsp_service.set_loudness(**data)
                elif category == 'delay':
                    await self.dsp_service.set_delay(**data)
            else:
                if not self.proxy_service:
                    return False
                await self.proxy_service.request(target, "PUT", f"/dsp/{category}", data)
                await self.update_client_settings(target, category, data)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to push {category} to {target}: {e}")
            return False

    async def sync_settings(
        self,
        source_client: str,
        target_clients: List[str]
    ) -> Dict[str, Any]:
        """
        Sync DSP settings from source client to target clients.

        Gets compressor, loudness, delay, filters and volume from source
        and pushes to all targets.

        Args:
            source_client: 'local' or hostname of source
            target_clients: List of target hostnames (can include 'local')

        Returns:
            Dictionary with 'synced' list and 'errors' list
        """
        synced = []
        errors = []

        # Get settings from source client
        try:
            source_settings = await self._get_source_settings(source_client)
        except Exception as e:
            self.logger.error(f"Error getting settings from source {source_client}: {e}")
            return {"synced": [], "errors": [f"Failed to get source settings: {e}"]}

        # Push settings to each target client
        for target in target_clients:
            if target == source_client:
                continue

            target_synced = []

            # Sync compressor
            if source_settings.get('compressor'):
                if await self._push_setting_to_target(target, 'compressor', source_settings['compressor']):
                    target_synced.append("compressor")
                else:
                    errors.append(f"{target}/compressor")

            # Sync loudness
            if source_settings.get('loudness'):
                if await self._push_setting_to_target(target, 'loudness', source_settings['loudness']):
                    target_synced.append("loudness")
                else:
                    errors.append(f"{target}/loudness")

            # Sync delay
            if source_settings.get('delay'):
                if await self._push_setting_to_target(target, 'delay', source_settings['delay']):
                    target_synced.append("delay")
                else:
                    errors.append(f"{target}/delay")

            # Sync filters
            if source_settings.get('filters'):
                for flt in source_settings['filters']:
                    filter_id = flt.get('id')
                    if not filter_id:
                        continue
                    filter_data = {
                        'freq': flt.get('freq'),
                        'gain': flt.get('gain'),
                        'q': flt.get('q'),
                        'filter_type': flt.get('type')
                    }
                    try:
                        if target == 'local':
                            if self.dsp_service:
                                await self.dsp_service.set_filter(filter_id, **filter_data)
                                target_synced.append(f"filter:{filter_id}")
                        else:
                            if self.proxy_service:
                                await self.proxy_service.request(
                                    target, "PUT", f"/dsp/filter/{filter_id}", filter_data
                                )
                                target_synced.append(f"filter:{filter_id}")
                    except Exception as e:
                        errors.append(f"{target}/filter:{filter_id}: {e}")

            # Sync volume/mute
            vol = source_settings.get('volume', {})
            if vol.get('main') is not None:
                try:
                    if target == 'local':
                        if self.dsp_service:
                            await self.dsp_service.set_volume(vol['main'])
                            target_synced.append("volume")
                    else:
                        if self.proxy_service:
                            await self.proxy_service.request(
                                target, "PUT", "/dsp/volume", {"volume": vol['main']}
                            )
                            await self.update_client_settings(target, "volume", {"main": vol['main']})
                            target_synced.append("volume")
                except Exception as e:
                    errors.append(f"{target}/volume: {e}")

            if vol.get('mute') is not None:
                try:
                    if target == 'local':
                        if self.dsp_service:
                            await self.dsp_service.set_mute(vol['mute'])
                            target_synced.append("mute")
                    else:
                        if self.proxy_service:
                            await self.proxy_service.request(
                                target, "PUT", "/dsp/mute", {"muted": vol['mute']}
                            )
                            target_synced.append("mute")
                except Exception as e:
                    errors.append(f"{target}/mute: {e}")

            if target_synced:
                synced.append({"target": target, "settings": target_synced})

        self.logger.info(f"Synced DSP settings from {source_client} to {len(synced)} targets")
        if errors:
            self.logger.warning(f"Sync errors: {errors}")

        return {"synced": synced, "errors": errors if errors else None}
