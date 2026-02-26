# backend/core/dsp/multiroom_service.py
"""
MultiroomDspService - Multiroom-aware DSP coordination service.

This service coordinates DSP settings across multiple clients and zones:
- Uses ClientRegistryService as the source of truth for settings
- Routes DSP commands to appropriate CamillaDSP instances
- Handles zone propagation logic (apply to all ONLINE clients)
- Manages standalone client DSP separately

Architecture:
    API Layer
        │
        ▼
    MultiroomDspService (this module - multiroom-aware)
        │
        ├─── ClientRegistryService (state/persistence)
        │       └── zone.dsp_settings, standalone_dsp
        │
        └─── CamillaDSPService (local daemon control)
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from backend.core.multiroom.models import (
    DspSettings,
    EqFilter,
    CompressorSettings,
    LoudnessSettings,
    FilterType,
)


class MultiroomDspService:
    """
    Multiroom-aware DSP coordination service.

    Coordinates DSP settings for zones and standalone clients:
    - Zone DSP: Shared settings applied to all ONLINE clients in zone
    - Standalone DSP: Individual settings per client not in a zone

    Responsibilities:
    - Apply DSP settings to zones (propagate to all online clients)
    - Apply DSP settings to standalone clients
    - Handle CamillaDSP failures gracefully
    - Broadcast WebSocket events on DSP changes
    - Provide partial update methods for individual DSP components
    """

    def __init__(
        self,
        client_registry_service=None,
        camilladsp_service=None,
        proxy_service=None,
        routing_service=None,
        dsp_router=None,
    ):
        """
        Initialize MultiroomDspService.

        Dependencies are injected lazily via setters during service initialization
        in dependencies.py to handle circular dependencies.

        Args:
            client_registry_service: ClientRegistryService for state management
            camilladsp_service: CamillaDSPService for local DSP control
            proxy_service: DspClientProxyService for remote client communication
            routing_service: AudioRoutingService for DSP effects toggle
            dsp_router: DspRouter for routing filter updates to local/remote clients
        """
        self.logger = logging.getLogger(__name__)

        # Dependencies (can be set lazily via setters)
        self._registry = client_registry_service
        self._dsp_service = camilladsp_service
        self._proxy_service = proxy_service
        self._routing_service = routing_service
        self._dsp_router = dsp_router

        # State machine for event broadcasting (set via setter)
        self._state_machine = None

        # Async lock for thread safety
        self._lock = asyncio.Lock()

        self.logger.info("MultiroomDspService created")

    # =========================================================================
    # Dependency Setters (for circular dependency resolution)
    # =========================================================================

    def set_registry(self, registry) -> None:
        """Set ClientRegistryService dependency."""
        self._registry = registry

    def set_dsp_service(self, dsp_service) -> None:
        """Set CamillaDSPService dependency."""
        self._dsp_service = dsp_service

    def set_state_machine(self, state_machine) -> None:
        """Set state machine for event broadcasting."""
        self._state_machine = state_machine

    def set_proxy_service(self, proxy_service) -> None:
        """Set DspClientProxyService dependency."""
        self._proxy_service = proxy_service

    def set_routing_service(self, routing_service) -> None:
        """Set AudioRoutingService dependency."""
        self._routing_service = routing_service

    def set_dsp_router(self, dsp_router) -> None:
        """Set DspRouter dependency."""
        self._dsp_router = dsp_router

    # =========================================================================
    # Zone DSP Methods (AC2, AC5)
    # =========================================================================

    async def apply_zone_dsp(self, zone_id: str, settings: DspSettings) -> bool:
        """
        Apply DSP settings to a zone.

        Updates zone.dsp_settings in ClientRegistryService (source of truth),
        then applies to all ONLINE clients via CamillaDSP. Offline clients
        will receive settings on reconnection.

        Args:
            zone_id: The zone ID to update
            settings: DspSettings to apply

        Returns:
            True if settings were saved (even if some clients failed)

        Raises:
            ValueError: If zone not found
        """
        async with self._lock:
            # Get zone from registry
            if not self._registry:
                self.logger.error("ClientRegistryService not available")
                return False

            zone = self._registry.get_zone(zone_id)
            if not zone:
                raise ValueError(f"Zone not found: {zone_id}")

            # Update zone DSP settings via registry's public method (handles persistence)
            await self._registry.set_zone_dsp(zone_id, settings)

            self.logger.info(f"Zone {zone_id} DSP settings updated")

        # Apply to all ONLINE clients in parallel (NFR3: < 200ms)
        online_clients = self._registry.get_online_zone_clients(zone_id)

        if online_clients:
            # Parallel application for performance (NFR3)
            results = await asyncio.gather(
                *[self._apply_to_camilladsp(client.mac_id, settings) for client in online_clients],
                return_exceptions=True
            )
            success_count = sum(1 for r in results if r is True)
            self.logger.info(
                f"Zone {zone_id}: Applied DSP to {success_count}/{len(online_clients)} online clients"
            )
        else:
            self.logger.debug(f"Zone {zone_id}: No online clients to apply DSP")

        # Broadcast WebSocket event
        await self._broadcast_dsp_event(
            target_type="zone",
            target_id=zone_id,
            settings=settings,
        )

        return True

    async def get_zone_dsp(self, zone_id: str) -> Optional[DspSettings]:
        """
        Get DSP settings for a zone.

        Note: This method is async for API consistency with apply_zone_dsp(),
        enabling uniform async/await usage patterns across the service.

        Args:
            zone_id: The zone ID

        Returns:
            DspSettings or None if zone not found
        """
        if not self._registry:
            return None

        zone = self._registry.get_zone(zone_id)
        if zone:
            return zone.dsp_settings
        return None

    async def load_zone_preset(self, zone_id: str, preset_id: str) -> bool:
        """
        Load an EQ preset for a zone.

        Converts the preset gains to EqFilter objects, preserves existing
        compressor/loudness settings, and applies to all zone clients.

        Args:
            zone_id: The zone ID
            preset_id: The preset ID (e.g., "rock", "classical", "manual")

        Returns:
            True if successful

        Raises:
            ValueError: If zone or preset not found
        """
        from backend.core.dsp.presets import get_preset_by_id, DEFAULT_MANUAL_GAINS, DEFAULT_EQ_FREQS

        # Get preset gains
        if preset_id == "manual":
            # Manual preset: use saved manual gains or defaults
            if self._dsp_service and hasattr(self._dsp_service, 'get_manual_gains'):
                gains = await self._dsp_service.get_manual_gains()
            else:
                gains = DEFAULT_MANUAL_GAINS
        else:
            preset = get_preset_by_id(preset_id)
            if not preset:
                raise ValueError(f"Preset not found: {preset_id}")
            gains = preset["gains"]

        # Get current zone settings to preserve compressor/loudness
        current = await self.get_zone_dsp(zone_id)
        if not current:
            raise ValueError(f"Zone not found: {zone_id}")

        # Build new filters from preset gains
        new_filters = [
            EqFilter(
                id=f"eq_band_{i:02d}",
                frequency=DEFAULT_EQ_FREQS[i],
                gain=gains[i],
                q=1.41,
                filter_type=FilterType.PEAKING,
                enabled=True
            )
            for i in range(10)
        ]

        # Update settings with new filters, preserve compressor/loudness
        current.filters = new_filters
        current.active_preset = preset_id  # Track which preset was loaded

        # Apply to zone
        return await self.apply_zone_dsp(zone_id, current)

    async def load_client_preset(self, mac_id: str, preset_id: str) -> bool:
        """
        Load an EQ preset for a standalone client.

        Converts the preset gains to EqFilter objects, preserves existing
        compressor/loudness settings, and applies to the client.

        Args:
            mac_id: The client's MAC ID
            preset_id: The preset ID (e.g., "rock", "classical", "manual")

        Returns:
            True if successful

        Raises:
            ValueError: If client not found, client is in a zone, or preset not found
        """
        from backend.core.dsp.presets import get_preset_by_id, DEFAULT_MANUAL_GAINS, DEFAULT_EQ_FREQS

        # Get preset gains
        if preset_id == "manual":
            if self._dsp_service and hasattr(self._dsp_service, 'get_manual_gains'):
                gains = await self._dsp_service.get_manual_gains()
            else:
                gains = DEFAULT_MANUAL_GAINS
        else:
            preset = get_preset_by_id(preset_id)
            if not preset:
                raise ValueError(f"Preset not found: {preset_id}")
            gains = preset["gains"]

        # Get current client settings to preserve compressor/loudness
        current = await self.get_client_dsp(mac_id)
        if not current:
            # Client not found or in a zone - check which case
            if self._registry:
                client = self._registry.get_client(mac_id)
                if client and client.zone_id:
                    raise ValueError(f"Client {mac_id} is in a zone. Use load_zone_preset() instead.")
            raise ValueError(f"Client not found: {mac_id}")

        # Build new filters from preset gains
        new_filters = [
            EqFilter(
                id=f"eq_band_{i:02d}",
                frequency=DEFAULT_EQ_FREQS[i],
                gain=gains[i],
                q=1.41,
                filter_type=FilterType.PEAKING,
                enabled=True
            )
            for i in range(10)
        ]

        # Update settings with new filters, preserve compressor/loudness
        current.filters = new_filters
        current.active_preset = preset_id  # Track which preset was loaded

        # Apply to client
        return await self.apply_client_dsp(mac_id, current)

    # =========================================================================
    # Standalone Client DSP Methods (AC3)
    # =========================================================================

    async def apply_client_dsp(self, mac_id: str, settings: DspSettings) -> bool:
        """
        Apply DSP settings to a standalone client.

        Only works for clients NOT in a zone. For zone clients,
        use apply_zone_dsp() instead.

        Args:
            mac_id: The client's MAC ID
            settings: DspSettings to apply

        Returns:
            True if settings were saved and applied

        Raises:
            ValueError: If client not found or client is in a zone
        """
        if not self._registry:
            self.logger.error("ClientRegistryService not available")
            return False

        client = self._registry.get_client(mac_id)
        if not client:
            raise ValueError(f"Client not found: {mac_id}")

        if client.zone_id is not None:
            raise ValueError(
                f"Client {mac_id} is in zone {client.zone_id}. "
                "Use apply_zone_dsp() instead."
            )

        # Update standalone DSP via registry
        await self._registry.set_standalone_dsp(mac_id, settings)

        # Apply to CamillaDSP
        success = await self._apply_to_camilladsp(mac_id, settings)

        self.logger.info(f"Client {mac_id} DSP settings updated (applied: {success})")

        # Broadcast WebSocket event
        await self._broadcast_dsp_event(
            target_type="client",
            target_id=mac_id,
            settings=settings,
        )

        return True

    async def get_client_dsp(self, mac_id: str) -> Optional[DspSettings]:
        """
        Get DSP settings for a standalone client.

        Note: This method is async for API consistency with apply_client_dsp(),
        enabling uniform async/await usage patterns across the service.

        Args:
            mac_id: The client's MAC ID

        Returns:
            DspSettings or None if client not found or not standalone
        """
        if not self._registry:
            return None

        return self._registry.get_standalone_dsp(mac_id)

    # =========================================================================
    # Target-Agnostic DSP Methods (AC2, AC3)
    # =========================================================================

    async def apply_dsp(
        self, target_type: str, target_id: str, settings: DspSettings
    ) -> bool:
        """
        Apply DSP settings to a zone or client.

        Routes to appropriate method based on target_type.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            settings: DspSettings to apply

        Returns:
            True if successful

        Raises:
            ValueError: If invalid target_type
        """
        if target_type == "zone":
            return await self.apply_zone_dsp(target_id, settings)
        elif target_type == "client":
            return await self.apply_client_dsp(target_id, settings)
        else:
            raise ValueError(f"Invalid target_type: {target_type}. Must be 'zone' or 'client'")

    async def get_dsp(
        self, target_type: str, target_id: str
    ) -> Optional[DspSettings]:
        """
        Get DSP settings for a zone or client.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID

        Returns:
            DspSettings or None if not found

        Raises:
            ValueError: If invalid target_type
        """
        if target_type == "zone":
            return await self.get_zone_dsp(target_id)
        elif target_type == "client":
            return await self.get_client_dsp(target_id)
        else:
            raise ValueError(f"Invalid target_type: {target_type}. Must be 'zone' or 'client'")

    # =========================================================================
    # CamillaDSP Application with Error Handling (AC4)
    # =========================================================================

    def _is_local_client(self, mac_id: str) -> bool:
        """Check if a client is the local device via registry."""
        if not self._registry:
            return False
        client = self._registry.get_client(mac_id)
        return client.is_local if client else False

    async def _apply_to_camilladsp(
        self, mac_id: str, settings: DspSettings
    ) -> bool:
        """
        Apply DSP settings to a client's CamillaDSP instance.

        Handles both local and remote clients:
        - Local: Apply via CamillaDSPService
        - Remote: Apply via proxy service (batch filters + compressor + loudness)

        Handles failures gracefully:
        - If disconnected, logs warning and returns False
        - Settings are already saved in ClientRegistryService (source of truth)
        - No exception raised to caller

        Args:
            mac_id: The client's MAC ID
            settings: DspSettings to apply

        Returns:
            True if applied successfully, False on failure
        """
        if self._is_local_client(mac_id):
            return await self._apply_to_local(settings)
        else:
            return await self._apply_to_remote(mac_id, settings)

    async def _apply_to_local(self, settings: DspSettings) -> bool:
        """Apply DSP settings to local CamillaDSP instance."""
        if not self._dsp_service:
            self.logger.warning("CamillaDSPService not available")
            return False

        if not self._dsp_service.connected:
            self.logger.warning("CamillaDSP not connected, settings saved but not applied")
            return False

        try:
            # Apply EQ filters (suppress individual broadcasts - zone will broadcast complete state)
            for eq_filter in settings.filters:
                success = await self._dsp_service.set_filter(
                    filter_id=eq_filter.id,
                    freq=eq_filter.frequency,
                    gain=eq_filter.gain,
                    q=eq_filter.q,
                    filter_type=eq_filter.filter_type.value,
                    enabled=eq_filter.enabled,
                    persist=False,  # Don't persist to dsp.* keys (multiroom uses registry)
                    from_preset=True,  # Don't switch to manual preset
                    broadcast=False,  # Don't broadcast per-filter (zone broadcasts complete state)
                )
                if not success:
                    self.logger.warning(f"Failed to apply filter {eq_filter.id}")

            # Apply compressor (suppress broadcast - zone broadcasts complete state)
            comp = settings.compressor
            await self._dsp_service.set_compressor(
                enabled=comp.enabled,
                threshold=comp.threshold,
                ratio=comp.ratio,
                attack=comp.attack,
                release=comp.release,
                makeup_gain=comp.makeup_gain,
                persist=False,  # Don't persist to dsp.* keys
                broadcast=False,  # Don't broadcast (zone broadcasts complete state)
            )

            # Apply loudness (suppress broadcast - zone broadcasts complete state)
            loud = settings.loudness
            await self._dsp_service.set_loudness(
                enabled=loud.enabled,
                high_boost=loud.high_boost,
                low_boost=loud.low_boost,
                persist=False,  # Don't persist to dsp.* keys
                broadcast=False,  # Don't broadcast (zone broadcasts complete state)
            )

            self.logger.debug("DSP settings applied to local")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to apply DSP settings to local: {e}")
            return False

    async def _apply_to_remote(self, mac_id: str, settings: DspSettings) -> bool:
        """Apply DSP settings to a remote client via proxy."""
        if not self._proxy_service:
            self.logger.debug(f"Proxy service not available, skipping remote client {mac_id} (will sync on reconnection)")
            return True  # Not a failure - settings are persisted and will sync later

        if not self._registry:
            self.logger.warning(f"Registry not available for remote client {mac_id}")
            return False

        client = self._registry.get_client(mac_id)
        if not client or not client.online:
            self.logger.debug(f"Client {mac_id} offline, will sync on reconnection")
            return True  # Not a failure - will sync later

        client_ip = client.ip
        if not client_ip:
            self.logger.warning(f"Client {mac_id} has no IP")
            return False

        try:
            # Apply filters as batch for efficiency
            filters_batch = [
                {
                    "id": f.id,
                    "gain": f.gain,
                    "freq": f.frequency,
                    "q": f.q,
                    "filter_type": f.filter_type.value,
                    "enabled": f.enabled
                }
                for f in settings.filters
            ]
            await self._proxy_service.request(
                client_ip, "PUT", "/dsp/filters", {"filters": filters_batch}
            )

            # Apply compressor
            comp = settings.compressor
            await self._proxy_service.request(
                client_ip, "PUT", "/dsp/compressor",
                {
                    "enabled": comp.enabled,
                    "threshold": comp.threshold,
                    "ratio": comp.ratio,
                    "attack": comp.attack,
                    "release": comp.release,
                    "makeup_gain": comp.makeup_gain
                }
            )

            # Apply loudness
            loud = settings.loudness
            await self._proxy_service.request(
                client_ip, "PUT", "/dsp/loudness",
                {
                    "enabled": loud.enabled,
                    "high_boost": loud.high_boost,
                    "low_boost": loud.low_boost
                }
            )

            self.logger.debug(f"DSP settings applied to remote client {mac_id}")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to apply DSP settings to {mac_id}: {e}")
            return False

    # =========================================================================
    # Partial DSP Update Methods (AC2, AC3)
    # =========================================================================

    async def update_filter(
        self,
        target_type: str,
        target_id: str,
        filter_id: str,
        frequency: Optional[int] = None,
        gain: Optional[float] = None,
        q: Optional[float] = None,
        filter_type: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """
        Update a single EQ filter using targeted routing (no compressor/loudness reapplication).

        This method updates ONLY the specified filter without touching compressor or loudness,
        eliminating spurious compressor_changed/loudness_changed events.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            filter_id: The filter ID to update (e.g., "eq_band_00")
            frequency: New frequency in Hz (optional)
            gain: New gain in dB (optional)
            q: New Q factor (optional)
            filter_type: New filter type (optional)
            enabled: New enabled state (optional)

        Returns:
            True if successful
        """
        # Get current settings
        current = await self.get_dsp(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        # Find and update the filter in settings
        updated_filter = None
        for f in current.filters:
            if f.id == filter_id:
                if frequency is not None:
                    f.frequency = frequency
                if gain is not None:
                    f.gain = gain
                if q is not None:
                    f.q = q
                if filter_type is not None:
                    f.filter_type = FilterType(filter_type)
                if enabled is not None:
                    f.enabled = enabled
                updated_filter = f
                break

        if not updated_filter:
            raise ValueError(f"Filter not found: {filter_id}")

        # ALWAYS save manual gains on ANY filter modification
        gains = [f.gain for f in current.filters[:10]]

        if self._dsp_service and self._dsp_service.settings_service:
            await self._dsp_service.settings_service.set_setting("dsp.manual_gains", gains)

        # Handle preset auto-switch when user manually modifies a filter
        preset_changed = False
        if current.active_preset and current.active_preset != "manual":
            current.active_preset = "manual"
            preset_changed = True

        # Save to registry (source of truth)
        if target_type == "zone":
            await self._registry.set_zone_dsp(target_id, current)
        else:
            await self._registry.set_standalone_dsp(target_id, current)

        # Build filter data dict for router
        filter_data = {
            "freq": updated_filter.frequency,
            "gain": updated_filter.gain,
            "q": updated_filter.q,
            "filter_type": updated_filter.filter_type.value,
            "enabled": updated_filter.enabled
        }

        # Apply to clients via DspRouter (handles local/remote routing)
        if self._dsp_router:
            if target_type == "zone":
                online_clients = self._registry.get_online_zone_clients(target_id)
                for client in online_clients:
                    await self._dsp_router.update_filter(
                        mac_id=client.mac_id,
                        filter_id=filter_id,
                        filter_data=filter_data,
                        persist=False,      # Don't persist (registry is source of truth)
                        from_preset=True,   # Don't switch to manual preset
                        broadcast=False     # Don't broadcast per-filter
                    )
            else:
                await self._dsp_router.update_filter(
                    mac_id=target_id,
                    filter_id=filter_id,
                    filter_data=filter_data,
                    persist=False,
                    from_preset=True,
                    broadcast=False
                )
        else:
            self.logger.warning("DspRouter not available, filter update not applied to clients")

        # Broadcast zone event ONCE (includes active_preset in dsp_settings)
        await self._broadcast_dsp_event(target_type, target_id, current)

        # Also emit preset_loaded event if preset changed to manual
        if preset_changed and self._state_machine:
            await self._state_machine.broadcast_event(
                "dsp", "preset_loaded", {"id": "manual"}
            )

        return True

    async def update_compressor(
        self,
        target_type: str,
        target_id: str,
        enabled: Optional[bool] = None,
        threshold: Optional[float] = None,
        ratio: Optional[float] = None,
        attack: Optional[float] = None,
        release: Optional[float] = None,
        makeup_gain: Optional[float] = None,
    ) -> bool:
        """
        Update compressor settings, preserving other settings.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            enabled: New enabled state (optional)
            threshold: New threshold in dB (optional)
            ratio: New ratio (optional)
            attack: New attack time in ms (optional)
            release: New release time in ms (optional)
            makeup_gain: New makeup gain in dB (optional)

        Returns:
            True if successful
        """
        # Get current settings
        current = await self.get_dsp(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        # Update compressor
        comp = current.compressor
        if enabled is not None:
            comp.enabled = enabled
        if threshold is not None:
            comp.threshold = threshold
        if ratio is not None:
            comp.ratio = ratio
        if attack is not None:
            comp.attack = attack
        if release is not None:
            comp.release = release
        if makeup_gain is not None:
            comp.makeup_gain = makeup_gain

        # Apply updated settings
        return await self.apply_dsp(target_type, target_id, current)

    async def update_loudness(
        self,
        target_type: str,
        target_id: str,
        enabled: Optional[bool] = None,
        high_boost: Optional[float] = None,
        low_boost: Optional[float] = None,
    ) -> bool:
        """
        Update loudness settings, preserving other settings.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            enabled: New enabled state (optional)
            high_boost: New high boost in dB (optional)
            low_boost: New low boost in dB (optional)

        Returns:
            True if successful
        """
        # Get current settings
        current = await self.get_dsp(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        # Update loudness
        loud = current.loudness
        if enabled is not None:
            loud.enabled = enabled
        if high_boost is not None:
            loud.high_boost = high_boost
        if low_boost is not None:
            loud.low_boost = low_boost

        # Apply updated settings
        return await self.apply_dsp(target_type, target_id, current)

    async def update_dsp_enabled(
        self,
        target_type: str,
        target_id: str,
        enabled: bool,
    ) -> bool:
        """
        Update global DSP enabled state, preserving other settings.

        When disabled, DSP effects are bypassed but settings are preserved.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            enabled: New DSP enabled state

        Returns:
            True if successful
        """
        # Get current settings
        current = await self.get_dsp(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        # Update enabled state
        current.enabled = enabled

        # Apply updated settings
        return await self.apply_dsp(target_type, target_id, current)

    async def set_zone_dsp_effects_enabled(self, zone_id: str, enabled: bool) -> bool:
        """
        Enable/disable DSP effects for all clients in a zone.

        This method uses routing_service for local clients (which properly
        bypasses/restores DSP effects in the audio chain) and proxies to
        remote clients.

        Args:
            zone_id: The zone ID
            enabled: Whether DSP effects should be enabled

        Returns:
            True if at least one client was updated successfully

        Raises:
            ValueError: If zone not found
        """
        if not self._registry:
            self.logger.error("ClientRegistryService not available")
            return False

        zone = self._registry.get_zone(zone_id)
        if not zone:
            raise ValueError(f"Zone not found: {zone_id}")

        success_count = 0

        for client_id in zone.client_ids:
            if self._is_local_client(client_id):
                # Local client: use routing_service
                if self._routing_service:
                    try:
                        if await self._routing_service.set_dsp_effects_enabled(enabled):
                            success_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to set DSP enabled for local: {e}")
            else:
                # Remote client: proxy
                if not self._proxy_service or not self._registry:
                    continue

                client = self._registry.get_client(client_id)
                if not client or not client.online:
                    continue  # Offline clients will sync on reconnection

                if not client.ip:
                    continue

                try:
                    result = await self._proxy_service.request(
                        client.ip, "PUT", "/dsp/enabled", {"enabled": enabled}
                    )
                    if result.get("status") == "success":
                        success_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to set DSP enabled for {client_id}: {e}")

        # Broadcast WebSocket event
        if self._state_machine:
            await self._state_machine.broadcast_event(
                "dsp", "zone_enabled_changed",
                {"zone_id": zone_id, "enabled": enabled}
            )

        return success_count > 0

    # =========================================================================
    # Event Broadcasting
    # =========================================================================

    async def _broadcast_dsp_event(
        self,
        target_type: str,
        target_id: str,
        settings: DspSettings,
    ) -> None:
        """
        Broadcast DSP changed event via WebSocket.

        Event format matches architecture spec:
        {
            "category": "multiroom",
            "type": "dsp_changed",
            "data": {
                "target_type": "zone" | "client",
                "target_id": "uuid-..." | "mac-...",
                "dsp_settings": { ... }
            }
        }
        """
        if self._state_machine:
            await self._state_machine.broadcast_event(
                "multiroom",
                "dsp_changed",
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "dsp_settings": settings.to_dict(),
                },
            )
