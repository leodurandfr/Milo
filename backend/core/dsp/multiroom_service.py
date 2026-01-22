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
    ):
        """
        Initialize MultiroomDspService.

        Dependencies are injected lazily via setters during service initialization
        in dependencies.py to handle circular dependencies.

        Args:
            client_registry_service: ClientRegistryService for state management
            camilladsp_service: CamillaDSPService for local DSP control
        """
        self.logger = logging.getLogger(__name__)

        # Dependencies (can be set lazily via setters)
        self._registry = client_registry_service
        self._dsp_service = camilladsp_service

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

    async def _apply_to_camilladsp(
        self, mac_id: str, settings: DspSettings
    ) -> bool:
        """
        Apply DSP settings to a client's CamillaDSP instance.

        Handles CamillaDSP failures gracefully:
        - If disconnected, logs warning and returns False
        - Settings are already saved in ClientRegistryService (source of truth)
        - No exception raised to caller

        Args:
            mac_id: The client's MAC ID
            settings: DspSettings to apply

        Returns:
            True if applied successfully, False on failure
        """
        # For now, only support "local" client (main device)
        # Remote clients will be supported in Epic 5+
        if mac_id != "local":
            self.logger.debug(f"Skipping DSP application for remote client {mac_id}")
            return True  # Success (remote clients will sync on reconnection)

        if not self._dsp_service:
            self.logger.warning("CamillaDSPService not available")
            return False

        if not self._dsp_service.connected:
            self.logger.warning(
                f"CamillaDSP not connected, DSP settings saved but not applied for {mac_id}"
            )
            return False

        try:
            # Apply EQ filters
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
                )
                if not success:
                    self.logger.warning(f"Failed to apply filter {eq_filter.id}")

            # Apply compressor
            comp = settings.compressor
            await self._dsp_service.set_compressor(
                enabled=comp.enabled,
                threshold=comp.threshold,
                ratio=comp.ratio,
                attack=comp.attack,
                release=comp.release,
                makeup_gain=comp.makeup_gain,
                persist=False,  # Don't persist to dsp.* keys
            )

            # Apply loudness
            loud = settings.loudness
            await self._dsp_service.set_loudness(
                enabled=loud.enabled,
                high_boost=loud.high_boost,
                low_boost=loud.low_boost,
                persist=False,  # Don't persist to dsp.* keys
            )

            self.logger.debug(f"DSP settings applied to {mac_id}")
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
        Update a single EQ filter, preserving other settings.

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

        # Find and update the filter
        filter_found = False
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
                filter_found = True
                break

        if not filter_found:
            raise ValueError(f"Filter not found: {filter_id}")

        # Apply updated settings
        return await self.apply_dsp(target_type, target_id, current)

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
