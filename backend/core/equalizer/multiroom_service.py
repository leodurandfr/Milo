# backend/core/equalizer/multiroom_service.py
"""
MultiroomEqualizerService - Multiroom-aware equalizer coordination service.

This service coordinates equalizer settings across multiple clients and zones:
- Uses ClientRegistryService as the source of truth for settings
- Routes DSP commands to appropriate CamillaDSP instances
- Handles zone propagation logic (apply to all ONLINE clients)
- Manages standalone client equalizer separately

Architecture:
    API Layer
        │
        ▼
    MultiroomEqualizerService (this module - multiroom-aware)
        │
        ├─── ClientRegistryService (state/persistence)
        │       └── zone.equalizer_settings, standalone_equalizer
        │
        └─── CamillaDSPService (local daemon control)
"""
import asyncio
import logging
from typing import Optional

from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    FilterType,
)


class MultiroomEqualizerService:
    """
    Multiroom-aware equalizer coordination service.

    Coordinates equalizer settings for zones and standalone clients:
    - Zone equalizer: Shared settings applied to all ONLINE clients in zone
    - Standalone equalizer: Individual settings per client not in a zone

    Responsibilities:
    - Apply equalizer settings to zones (propagate to all online clients)
    - Apply equalizer settings to standalone clients
    - Handle CamillaDSP failures gracefully
    - Broadcast WebSocket events on equalizer changes
    - Provide partial update methods for individual equalizer components
    """

    def __init__(
        self,
        client_registry_service=None,
        camilladsp_service=None,
        proxy_service=None,
        routing_service=None,
        equalizer_router=None,
    ):
        """
        Initialize MultiroomEqualizerService.

        Dependencies are injected lazily via setters during service initialization
        in dependencies.py to handle circular dependencies.

        Args:
            client_registry_service: ClientRegistryService for state management
            camilladsp_service: CamillaDSPService for local DSP control
            proxy_service: EqualizerClientProxyService for remote client communication
            routing_service: AudioRoutingService for equalizer effects toggle
            equalizer_router: EqualizerRouter for routing filter updates to local/remote clients
        """
        self.logger = logging.getLogger(__name__)

        # Dependencies (can be set lazily via setters)
        self._registry = client_registry_service
        self._camilladsp_service = camilladsp_service
        self._proxy_service = proxy_service
        self._routing_service = routing_service
        self._equalizer_router = equalizer_router

        # State machine for event broadcasting (set via setter)
        self._state_machine = None

        # Async lock for thread safety
        self._lock = asyncio.Lock()

        self.logger.info("MultiroomEqualizerService created")

    # =========================================================================
    # Dependency Setters (for circular dependency resolution)
    # =========================================================================

    def set_registry(self, registry) -> None:
        """Set ClientRegistryService dependency."""
        self._registry = registry

    def set_camilladsp_service(self, camilladsp_service) -> None:
        """Set CamillaDSPService dependency."""
        self._camilladsp_service = camilladsp_service

    def set_state_machine(self, state_machine) -> None:
        """Set state machine for event broadcasting."""
        self._state_machine = state_machine

    def set_proxy_service(self, proxy_service) -> None:
        """Set EqualizerClientProxyService dependency."""
        self._proxy_service = proxy_service

    def set_routing_service(self, routing_service) -> None:
        """Set AudioRoutingService dependency."""
        self._routing_service = routing_service

    def set_equalizer_router(self, equalizer_router) -> None:
        """Set EqualizerRouter dependency."""
        self._equalizer_router = equalizer_router

    # =========================================================================
    # Zone Equalizer Methods (AC2, AC5)
    # =========================================================================

    async def apply_zone_equalizer(self, zone_id: str, settings: EqualizerSettings) -> bool:
        """
        Apply equalizer settings to a zone.

        Updates zone.equalizer_settings in ClientRegistryService (source of truth),
        then applies to all ONLINE clients via CamillaDSP. Offline clients
        will receive settings on reconnection.

        Args:
            zone_id: The zone ID to update
            settings: EqualizerSettings to apply

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

            # Update zone equalizer settings via registry's public method (handles persistence + broadcast)
            await self._registry.set_zone_equalizer(zone_id, settings)

            self.logger.info(f"Zone {zone_id} Equalizer settings updated")

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
                f"Zone {zone_id}: Applied equalizer to {success_count}/{len(online_clients)} online clients"
            )

        return True

    async def get_zone_equalizer(self, zone_id: str) -> Optional[EqualizerSettings]:
        """
        Get equalizer settings for a zone.

        Note: This method is async for API consistency with apply_zone_equalizer(),
        enabling uniform async/await usage patterns across the service.

        Args:
            zone_id: The zone ID

        Returns:
            EqualizerSettings or None if zone not found
        """
        if not self._registry:
            return None

        zone = self._registry.get_zone(zone_id)
        if zone:
            return zone.equalizer_settings
        return None

    async def resolve_preset_gains(self, preset_id: str, settings: EqualizerSettings = None) -> list:
        """
        Resolve gain values for a preset ID (builtin or custom).

        For 'custom' preset, reads from settings.custom_gains (zone/client-specific)
        with fallback to global CamillaDSP custom gains.
        """
        from backend.core.equalizer.presets import get_preset_by_id, DEFAULT_CUSTOM_GAINS

        if preset_id == "custom":
            # Per-zone/client custom gains (stored in EqualizerSettings)
            if settings and settings.custom_gains:
                return settings.custom_gains
            # Fallback to global custom gains (local standalone)
            if self._camilladsp_service and hasattr(self._camilladsp_service, 'get_custom_gains'):
                return await self._camilladsp_service.get_custom_gains()
            return DEFAULT_CUSTOM_GAINS

        preset = get_preset_by_id(preset_id)
        if not preset:
            raise ValueError(f"Preset not found: {preset_id}")
        return preset["gains"]

    def _new_standalone_settings(self, mac_id: str) -> EqualizerSettings:
        """Build default EQ settings for a registered standalone client on demand.

        A remote standalone client that has never had its EQ saved has no entry
        in the registry's standalone-equalizer store. The local target's
        preset/save paths persist active_preset unconditionally (see
        CamillaDSPService.load_preset); mirror that here so the remote
        preset-name write paths create the entry instead of raising — otherwise
        the route turns the ValueError into a 404 and the chosen preset NAME is
        silently dropped while the gains survive via the separate gains path.

        Raises:
            ValueError: if the client is unknown or currently in a zone (in which
            case the zone equalizer is the source of truth, not standalone).
        """
        client = self._registry.get_client(mac_id) if self._registry else None
        if not client:
            raise ValueError(f"Client not found: {mac_id}")
        if client.zone_id:
            raise ValueError(f"Client {mac_id} is in a zone. Use load_zone_preset() instead.")
        return EqualizerSettings.default()

    async def save_custom_preset(self, target_type: str, target_id: str) -> None:
        """
        Save current filter gains as the custom preset for a zone or client.

        Persists gains into custom_gains field and sets active_preset to 'custom'.
        """
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            if target_type == "client":
                # Create defaults on demand (symmetric with the local path) so a
                # fresh standalone client can save a custom preset without 404-ing.
                # In practice the UI only exposes "Save" after an edit (which has
                # already created the entry via _persist_remote), so this branch
                # is a defensive fallback: it snapshots the flat default as custom.
                current = self._new_standalone_settings(target_id)
            else:
                raise ValueError(f"{target_type.capitalize()} not found: {target_id}")

        current.custom_gains = [f.gain for f in current.filters[:10]]
        current.active_preset = "custom"

        if target_type == "zone":
            await self._registry.set_zone_equalizer(target_id, current)
        else:
            await self._registry.set_standalone_equalizer(target_id, current)

    def _build_preset_filters(self, gains: list) -> list:
        """Build EqFilter objects from gain values using standard frequencies."""
        from backend.core.equalizer.presets import DEFAULT_EQ_FREQS

        return [
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

    async def load_zone_preset(self, zone_id: str, preset_id: str) -> bool:
        """
        Load an EQ preset for a zone.

        Preserves existing compressor/loudness settings and applies to all zone clients.

        Raises:
            ValueError: If zone or preset not found
        """
        current = await self.get_zone_equalizer(zone_id)
        if not current:
            raise ValueError(f"Zone not found: {zone_id}")

        gains = await self.resolve_preset_gains(preset_id, current)
        current.filters = self._build_preset_filters(gains)
        current.active_preset = preset_id
        return await self.apply_zone_equalizer(zone_id, current)

    async def load_client_preset(self, mac_id: str, preset_id: str) -> bool:
        """
        Load an EQ preset for a standalone client.

        Preserves existing compressor/loudness settings and applies to the client.

        Raises:
            ValueError: If client not found, client is in a zone, or preset not found
        """
        current = await self.get_client_equalizer(mac_id)
        if not current:
            # No saved EQ yet for this standalone client — create defaults on
            # demand (symmetric with the local path) so the preset NAME persists
            # instead of the route returning a 404. Still raises for an unknown
            # or zoned client.
            current = self._new_standalone_settings(mac_id)

        gains = await self.resolve_preset_gains(preset_id, current)
        current.filters = self._build_preset_filters(gains)
        current.active_preset = preset_id
        return await self.apply_client_equalizer(mac_id, current)

    # =========================================================================
    # Standalone Client Equalizer Methods (AC3)
    # =========================================================================

    async def apply_client_equalizer(self, mac_id: str, settings: EqualizerSettings) -> bool:
        """
        Apply equalizer settings to a standalone client.

        Only works for clients NOT in a zone. For zone clients,
        use apply_zone_equalizer() instead.

        Args:
            mac_id: The client's MAC ID
            settings: EqualizerSettings to apply

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
                "Use apply_zone_equalizer() instead."
            )

        # Update standalone equalizer via registry (handles persistence + broadcast)
        await self._registry.set_standalone_equalizer(mac_id, settings)

        # Apply to CamillaDSP
        success = await self._apply_to_camilladsp(mac_id, settings)

        self.logger.info(f"Client {mac_id} Equalizer settings updated (applied: {success})")

        return True

    async def get_client_equalizer(self, mac_id: str) -> Optional[EqualizerSettings]:
        """
        Get equalizer settings for a standalone client.

        Note: This method is async for API consistency with apply_client_equalizer(),
        enabling uniform async/await usage patterns across the service.

        Args:
            mac_id: The client's MAC ID

        Returns:
            EqualizerSettings or None if client not found or not standalone
        """
        if not self._registry:
            return None

        return self._registry.get_standalone_equalizer(mac_id)

    # =========================================================================
    # Target-Agnostic Equalizer Methods (AC2, AC3)
    # =========================================================================

    async def apply_equalizer(
        self, target_type: str, target_id: str, settings: EqualizerSettings
    ) -> bool:
        """
        Apply equalizer settings to a zone or client.

        Routes to appropriate method based on target_type.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            settings: EqualizerSettings to apply

        Returns:
            True if successful

        Raises:
            ValueError: If invalid target_type
        """
        if target_type == "zone":
            return await self.apply_zone_equalizer(target_id, settings)
        elif target_type == "client":
            return await self.apply_client_equalizer(target_id, settings)
        else:
            raise ValueError(f"Invalid target_type: {target_type}. Must be 'zone' or 'client'")

    async def get_equalizer(
        self, target_type: str, target_id: str
    ) -> Optional[EqualizerSettings]:
        """
        Get equalizer settings for a zone or client.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID

        Returns:
            EqualizerSettings or None if not found

        Raises:
            ValueError: If invalid target_type
        """
        if target_type == "zone":
            return await self.get_zone_equalizer(target_id)
        elif target_type == "client":
            return await self.get_client_equalizer(target_id)
        else:
            raise ValueError(f"Invalid target_type: {target_type}. Must be 'zone' or 'client'")

    # =========================================================================
    # CamillaDSP Application with Error Handling (AC4)
    # =========================================================================

    async def _apply_to_camilladsp(
        self, mac_id: str, settings: EqualizerSettings
    ) -> bool:
        """
        Apply equalizer settings to a client's CamillaDSP instance.

        Handles both local and remote clients:
        - Local: Apply via CamillaDSPService
        - Remote: Apply via proxy service (batch filters + compressor + loudness)

        Handles failures gracefully:
        - If disconnected, logs warning and returns False
        - Settings are already saved in ClientRegistryService (source of truth)
        - No exception raised to caller

        Args:
            mac_id: The client's MAC ID
            settings: EqualizerSettings to apply

        Returns:
            True if applied successfully, False on failure
        """
        if self._registry.is_local_client(mac_id):
            return await self._apply_to_local(settings)
        else:
            return await self._apply_to_remote(mac_id, settings)

    async def _apply_to_local(self, settings: EqualizerSettings) -> bool:
        """Apply equalizer settings to local CamillaDSP instance."""
        if not self._camilladsp_service:
            self.logger.warning("CamillaDSPService not available")
            return False

        if not self._camilladsp_service.connected:
            self.logger.warning("CamillaDSP not connected, settings saved but not applied")
            return False

        try:
            # Apply EQ filters (suppress individual broadcasts - zone will broadcast complete state)
            for eq_filter in settings.filters:
                success = await self._camilladsp_service.set_filter(
                    filter_id=eq_filter.id,
                    freq=eq_filter.frequency,
                    gain=eq_filter.gain,
                    q=eq_filter.q,
                    filter_type=eq_filter.filter_type.value,
                    enabled=eq_filter.enabled,
                    persist=False,  # Don't persist to equalizer.* keys (multiroom uses registry)
                    broadcast=False,  # Don't broadcast per-filter (zone broadcasts complete state)
                )
                if not success:
                    self.logger.warning(f"Failed to apply filter {eq_filter.id}")

            # Apply compressor (suppress broadcast - zone broadcasts complete state)
            comp = settings.compressor
            await self._camilladsp_service.set_compressor(
                enabled=comp.enabled,
                threshold=comp.threshold,
                ratio=comp.ratio,
                attack=comp.attack,
                release=comp.release,
                makeup_gain=comp.makeup_gain,
                persist=False,  # Don't persist to equalizer.* keys keys
                broadcast=False,  # Don't broadcast (zone broadcasts complete state)
            )

            # Apply loudness (suppress broadcast - zone broadcasts complete state)
            loud = settings.loudness
            await self._camilladsp_service.set_loudness(
                enabled=loud.enabled,
                high_boost=loud.high_boost,
                low_boost=loud.low_boost,
                persist=False,  # Don't persist to equalizer.* keys keys
                broadcast=False,  # Don't broadcast (zone broadcasts complete state)
            )

            # Apply mono (suppress broadcast - zone broadcasts complete state)
            await self._camilladsp_service.set_mono(
                enabled=settings.mono,
                persist=False,
                broadcast=False,
            )

            self.logger.debug("Equalizer settings applied to local")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to apply equalizer settings to local: {e}")
            return False

    async def _apply_to_remote(self, mac_id: str, settings: EqualizerSettings) -> bool:
        """Apply equalizer settings to a remote client via proxy."""
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
                client_ip, "PUT", "/equalizer/filters", {"filters": filters_batch}
            )

            # Apply compressor
            comp = settings.compressor
            await self._proxy_service.request(
                client_ip, "PUT", "/equalizer/compressor",
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
                client_ip, "PUT", "/equalizer/loudness",
                {
                    "enabled": loud.enabled,
                    "high_boost": loud.high_boost,
                    "low_boost": loud.low_boost
                }
            )

            # Apply mono
            await self._proxy_service.request(
                client_ip, "PUT", "/equalizer/mono",
                {"enabled": settings.mono}
            )

            self.logger.debug(f"Equalizer settings applied to remote client {mac_id}")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to apply equalizer settings to {mac_id}: {e}")
            return False

    # =========================================================================
    # Partial Equalizer Update Methods (AC2, AC3)
    # =========================================================================

    async def _apply_partial_update(
        self,
        target_type: str,
        target_id: str,
        current: EqualizerSettings,
        router_method: str,
        router_kwargs: dict,
        broadcast_settings: dict,
    ) -> bool:
        """
        Shared logic for partial equalizer updates (save → route → broadcast).

        1. Save current settings to registry (source of truth) without full broadcast
        2. Route update to zone clients or standalone client via EqualizerRouter
        3. Broadcast targeted WebSocket event with only the changed sub-object

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            current: The mutated EqualizerSettings to persist
            router_method: EqualizerRouter method name (e.g., "update_filter")
            router_kwargs: Kwargs for the router method (excluding mac_id, persist, broadcast)
            broadcast_settings: Partial equalizer_settings dict for the WebSocket broadcast
        """
        # Save to registry (source of truth) without broadcasting full settings
        if target_type == "zone":
            await self._registry.set_zone_equalizer(target_id, current, broadcast=False)
        else:
            await self._registry.set_standalone_equalizer(target_id, current, broadcast=False)

        # Route to clients via EqualizerRouter
        if self._equalizer_router:
            method = getattr(self._equalizer_router, router_method)
            if target_type == "zone":
                online_clients = self._registry.get_online_zone_clients(target_id)
                if online_clients:
                    await asyncio.gather(
                        *[method(mac_id=c.mac_id, persist=False, broadcast=False, **router_kwargs)
                          for c in online_clients],
                        return_exceptions=True,
                    )
            else:
                await method(mac_id=target_id, persist=False, broadcast=False, **router_kwargs)
        else:
            self.logger.warning(f"EqualizerRouter not available, {router_method} not applied to clients")

        # Broadcast targeted WebSocket event
        if self._state_machine:
            await self._state_machine.broadcast_event(
                "multiroom",
                "equalizer_changed",
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "equalizer_settings": broadcast_settings,
                },
            )

        return True

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
        current = await self.get_equalizer(target_type, target_id)
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

        return await self._apply_partial_update(
            target_type, target_id, current,
            router_method="update_filter",
            router_kwargs={
                "filter_id": filter_id,
                "filter_data": {
                    "freq": updated_filter.frequency,
                    "gain": updated_filter.gain,
                    "q": updated_filter.q,
                    "filter_type": updated_filter.filter_type.value,
                    "enabled": updated_filter.enabled,
                },
            },
            broadcast_settings={
                "filters": [updated_filter.to_dict()],
                "active_preset": current.active_preset,
            },
        )

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
        Update compressor settings using targeted routing (no filter reapplication).

        Only touches compressor on CamillaDSP clients, leaving EQ filters and
        loudness untouched. Follows the same targeted pattern as update_filter().

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
        current = await self.get_equalizer(target_type, target_id)
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

        return await self._apply_partial_update(
            target_type, target_id, current,
            router_method="set_compressor",
            router_kwargs={
                "settings": {
                    "enabled": comp.enabled,
                    "threshold": comp.threshold,
                    "ratio": comp.ratio,
                    "attack": comp.attack,
                    "release": comp.release,
                    "makeup_gain": comp.makeup_gain,
                },
            },
            broadcast_settings={"compressor": comp.to_dict()},
        )

    async def update_loudness(
        self,
        target_type: str,
        target_id: str,
        enabled: Optional[bool] = None,
        high_boost: Optional[float] = None,
        low_boost: Optional[float] = None,
    ) -> bool:
        """
        Update loudness settings using targeted routing (no filter reapplication).

        Only touches loudness on CamillaDSP clients, leaving EQ filters and
        compressor untouched. Follows the same targeted pattern as update_filter().

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
        current = await self.get_equalizer(target_type, target_id)
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

        return await self._apply_partial_update(
            target_type, target_id, current,
            router_method="set_loudness",
            router_kwargs={
                "settings": {
                    "enabled": loud.enabled,
                    "high_boost": loud.high_boost,
                    "low_boost": loud.low_boost,
                },
            },
            broadcast_settings={"loudness": loud.to_dict()},
        )

    async def update_mono(
        self,
        target_type: str,
        target_id: str,
        enabled: bool,
    ) -> bool:
        """
        Update mono setting using targeted routing (no filter reapplication).

        Only touches the CamillaDSP mixer on clients, leaving EQ filters,
        compressor, and loudness untouched.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            enabled: True for mono, False for stereo

        Returns:
            True if successful
        """
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        current.mono = enabled

        return await self._apply_partial_update(
            target_type, target_id, current,
            router_method="set_mono",
            router_kwargs={"settings": {"enabled": enabled}},
            broadcast_settings={"mono": enabled},
        )

    async def update_equalizer_enabled(
        self,
        target_type: str,
        target_id: str,
        enabled: bool,
    ) -> bool:
        """
        Update global equalizer enabled state, preserving other settings.

        When disabled, equalizer effects are bypassed but settings are preserved.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            enabled: New equalizer enabled state

        Returns:
            True if successful
        """
        # Get current settings
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

        # Update enabled state
        current.enabled = enabled

        # Apply updated settings
        return await self.apply_equalizer(target_type, target_id, current)

    async def set_zone_equalizer_effects_enabled(self, zone_id: str, enabled: bool) -> bool:
        """
        Enable/disable equalizer effects for all clients in a zone.

        This method uses routing_service for local clients (which properly
        bypasses/restores equalizer effects in the audio chain) and proxies to
        remote clients.

        Args:
            zone_id: The zone ID
            enabled: Whether equalizer effects should be enabled

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
            if self._registry.is_local_client(client_id):
                # Local client: use routing_service
                if self._routing_service:
                    try:
                        if await self._routing_service.set_equalizer_effects_enabled(enabled):
                            success_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to set equalizer enabled for local: {e}")
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
                        client.ip, "PUT", "/equalizer/enabled", {"enabled": enabled}
                    )
                    if result.get("status") == "success":
                        success_count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to set equalizer enabled for {client_id}: {e}")

        # Persist the enabled flag into the zone's settings (source of truth) so
        # GET /zone/{id} reflects it and offline / reconnecting members can recover
        # it via _sync_zone_equalizer_to_client.
        zone.equalizer_settings.enabled = enabled
        await self._registry.set_zone_equalizer(zone_id, zone.equalizer_settings, broadcast=False)

        # Broadcast WebSocket event
        if self._state_machine:
            await self._state_machine.broadcast_event(
                "equalizer", "zone_enabled_changed",
                {"zone_id": zone_id, "enabled": enabled}
            )

        return success_count > 0

