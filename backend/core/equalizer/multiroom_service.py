# backend/core/equalizer/multiroom_service.py
"""
MultiroomEqualizerService - the per-client equalizer access layer.

One EQ record per client is the source of truth; a zone holds no EQ of its own
(its EQ is the identical EQ of its members). This service exposes the unified
access API (get/set_client_eq, get/set_zone_eq) and routes each write to the
domain that owns the record:

Architecture:
    API Layer
        │
        ▼
    MultiroomEqualizerService (get/set_client_eq, get/set_zone_eq)
        │
        ├─── CamillaDSPService   → local client's record (equalizer.json + DAC)
        │
        └─── ClientRegistryService.client_equalizer  → remote clients' records
                                                        (+ proxy push to satellite)
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
    # Per-Client Access Layer — the unified EQ source of truth
    # =========================================================================

    async def get_client_eq(self, mac_id: str) -> EqualizerSettings:
        """Read a client's one EQ record.

        Local client → snapshot from CamillaDSP (the equalizer.json-backed
        cache); remote client → the registry's client_equalizer store (a neutral
        default when nothing has been saved yet). Always returns a record.
        """
        if self._registry and self._registry.is_local_client(mac_id):
            if self._camilladsp_service:
                return self._camilladsp_service.get_equalizer_settings()
            return EqualizerSettings.default()
        record = self._registry.get_client_equalizer(mac_id) if self._registry else None
        # Hand out a copy so callers can mutate freely without aliasing the store.
        return EqualizerSettings.from_dict(record.to_dict()) if record else EqualizerSettings.default()

    async def set_client_eq(self, mac_id: str, settings: EqualizerSettings) -> bool:
        """Write a client's one EQ record — name and gains travel together.

        Local client → apply to the DAC and persist equalizer.json;
        remote client → store in the registry and push to the satellite.
        """
        if self._registry and self._registry.is_local_client(mac_id):
            applied = await self._apply_to_local(settings)
            # Persist only after a successful apply (applied == connected DSP);
            # a failed/disconnected apply leaves the live cache uncertain.
            if applied and self._camilladsp_service:
                if settings.custom_gains is not None:
                    self._camilladsp_service.set_custom_gains(settings.custom_gains)
                await self._camilladsp_service.persist_state()
            return applied
        if self._registry:
            await self._registry.set_client_equalizer(mac_id, settings)
        return await self._apply_to_remote(mac_id, settings)

    async def get_zone_eq(self, zone_id: str) -> Optional[EqualizerSettings]:
        """Zone EQ derives from its members (kept identical).

        Reads the local member if the zone contains one (most authoritative),
        otherwise the first member. Returns None if the zone has no members.
        """
        if not self._registry:
            return None
        zone = self._registry.get_zone(zone_id)
        if not zone or not zone.client_ids:
            return None
        member = next(
            (m for m in zone.client_ids if self._registry.is_local_client(m)),
            zone.client_ids[0],
        )
        return await self.get_client_eq(member)

    async def set_zone_eq(self, zone_id: str, settings: EqualizerSettings) -> bool:
        """Apply one EQ record to every zone member, keeping them identical.

        Each member receives its own copy so later per-member edits never alias.
        Members are written in parallel (NFR3: < 200ms).
        """
        if not self._registry:
            self.logger.error("ClientRegistryService not available")
            return False
        zone = self._registry.get_zone(zone_id)
        if not zone:
            raise ValueError(f"Zone not found: {zone_id}")
        await asyncio.gather(
            *[
                self.set_client_eq(mac_id, EqualizerSettings.from_dict(settings.to_dict()))
                for mac_id in zone.client_ids
            ],
            return_exceptions=True,
        )
        return True

    # =========================================================================
    # Zone / Client Equalizer Methods — route-facing wrappers over the access layer
    # =========================================================================

    async def apply_zone_equalizer(self, zone_id: str, settings: EqualizerSettings) -> bool:
        """Apply equalizer settings to a whole zone (fan-out to all members).

        Raises:
            ValueError: If zone not found
        """
        async with self._lock:
            result = await self.set_zone_eq(zone_id, settings)
            self.logger.info(f"Zone {zone_id} equalizer applied to all members")
            return result

    async def get_zone_equalizer(self, zone_id: str) -> Optional[EqualizerSettings]:
        """Get a zone's equalizer settings (derived from its members).

        Returns:
            EqualizerSettings or None if the zone is unknown / has no members
        """
        return await self.get_zone_eq(zone_id)

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

    async def save_custom_preset(self, target_type: str, target_id: str) -> None:
        """Snapshot current filter gains as the 'custom' preset for a zone or client.

        Persists the gains into custom_gains and sets active_preset='custom' on the
        target's record(s) through the per-client access layer, so name and gains
        travel together (local → equalizer.json, remote → registry, zone → every
        member).
        """
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type.capitalize()} not found: {target_id}")

        current.custom_gains = [f.gain for f in current.filters[:10]]
        current.active_preset = "custom"
        await self.apply_equalizer(target_type, target_id, current)

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
        Load an EQ preset for a single (non-zone) client.

        Preserves existing compressor/loudness settings and applies to the client.
        get_client_eq always returns a record (neutral default when none saved),
        and apply_client_equalizer validates the client (unknown / in-a-zone).

        Raises:
            ValueError: If client not found, client is in a zone, or preset not found
        """
        current = await self.get_client_eq(mac_id)
        gains = await self.resolve_preset_gains(preset_id, current)
        current.filters = self._build_preset_filters(gains)
        current.active_preset = preset_id
        return await self.apply_client_equalizer(mac_id, current)

    # =========================================================================
    # Single-Client Equalizer Methods (route-facing wrappers over the access layer)
    # =========================================================================

    async def apply_client_equalizer(self, mac_id: str, settings: EqualizerSettings) -> bool:
        """
        Apply equalizer settings to a single client (not via a zone).

        Validates the client exists and is not in a zone (zone members are
        driven through apply_zone_equalizer), then writes its one EQ record.

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

        result = await self.set_client_eq(mac_id, settings)
        self.logger.info(f"Client {mac_id} equalizer settings updated")
        return result

    async def get_client_equalizer(self, mac_id: str) -> Optional[EqualizerSettings]:
        """
        Get a single client's equalizer settings (its one EQ record).

        Returns a neutral default for a known client that has no saved EQ yet.
        """
        return await self.get_client_eq(mac_id)

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
    #
    # _apply_to_local / _apply_to_remote are the DSP-application halves of
    # set_client_eq (local vs remote). They apply settings to the audio chain;
    # the persistence half (equalizer.json / registry) lives in set_client_eq.
    # =========================================================================

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

            # Keep the local preset NAME in sync with the gains we just applied.
            # The local client's name is read from CamillaDSPService._active_preset
            # (GET /api/equalizer/presets), a store separate from the filter cache.
            # Without this, the local client shows a stale preset name against fresh
            # gains — e.g. it keeps the previous name after a zone is deleted, since
            # deletion leaves the local DSP untouched. persist=False: the registry is
            # the source of truth in multiroom mode, not equalizer.json.
            if settings.active_preset:
                await self._camilladsp_service.set_active_preset(
                    settings.active_preset, persist=False
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
        Shared logic for partial equalizer updates (persist → route → broadcast).

        1. Persist `current` to each affected member's per-client record (remote →
           registry; local → its live DSP snapshot, after the router applies below)
        2. Route the targeted update to ONLINE members via EqualizerRouter
        3. Broadcast targeted WebSocket event with only the changed sub-object

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            current: The mutated EqualizerSettings to persist
            router_method: EqualizerRouter method name (e.g., "update_filter")
            router_kwargs: Kwargs for the router method (excluding mac_id, persist, broadcast)
            broadcast_settings: Partial equalizer_settings dict for the WebSocket broadcast
        """
        # Resolve the affected members (zone fan-out, or a single client).
        if target_type == "zone":
            zone = self._registry.get_zone(target_id)
            members = list(zone.client_ids) if zone else []
        else:
            members = [target_id]

        # Persist `current` to each REMOTE member's record (the per-client source
        # of truth — a fresh copy each, so members never alias). The local member
        # is persisted from its live DSP cache below, after the router applies.
        local_touched = False
        for member in members:
            if self._registry.is_local_client(member):
                local_touched = True
            else:
                await self._registry.set_client_equalizer(
                    member, EqualizerSettings.from_dict(current.to_dict()), broadcast=False
                )

        # Route the targeted update to ONLINE members via EqualizerRouter
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

        # Snapshot the local client's live DSP → equalizer.json (the router already
        # applied the targeted change to the cache), so its record survives a restart.
        if local_touched and self._camilladsp_service and self._camilladsp_service.connected:
            await self._camilladsp_service.persist_state()

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
                # Remote client: push to the satellite (if online) and persist the
                # flag into its per-client record so offline / reconnecting members
                # recover it (the record is the per-client source of truth).
                client = self._registry.get_client(client_id)
                if client and client.online and client.ip and self._proxy_service:
                    try:
                        result = await self._proxy_service.request(
                            client.ip, "PUT", "/equalizer/enabled", {"enabled": enabled}
                        )
                        if result.get("status") == "success":
                            success_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to set equalizer enabled for {client_id}: {e}")

                existing = self._registry.get_client_equalizer(client_id)
                record = (
                    EqualizerSettings.from_dict(existing.to_dict())
                    if existing else EqualizerSettings.default_for_zone()
                )
                record.enabled = enabled
                await self._registry.set_client_equalizer(client_id, record, broadcast=False)

        # Broadcast WebSocket event
        if self._state_machine:
            await self._state_machine.broadcast_event(
                "equalizer", "zone_enabled_changed",
                {"zone_id": zone_id, "enabled": enabled}
            )

        return success_count > 0

