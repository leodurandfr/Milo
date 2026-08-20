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

from backend.core.models.ws_events import (
    EqualizerZoneEnabledChanged,
    MultiroomEqualizerChanged,
)
from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    FilterType,
)
from backend.shared.fanout import failed_members


# The local device is addressable as this sentinel — no registry entry required.
# The registry is populated only by Snapcast connections, so it is empty when
# multiroom is off; the sentinel keeps base-audio EQ addressable (and independent
# of the multiroom registry) in every mode.
LOCAL_TARGET = "local"


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
        state_machine=None,
    ):
        """
        Initialize MultiroomEqualizerService.

        Every dependency is acyclic and constructor-injected from
        dependencies.py — none of them holds a back-reference to this service,
        so there is no setter and no post-construction wiring step.

        Args:
            client_registry_service: ClientRegistryService for state management
            camilladsp_service: CamillaDSPService for local DSP control
            proxy_service: EqualizerClientProxyService for remote client communication
            routing_service: AudioRoutingService for equalizer effects toggle
            equalizer_router: EqualizerRouter for routing filter updates to local/remote clients
            state_machine: AudioStateMachine for event broadcasting
        """
        self.logger = logging.getLogger(__name__)

        self._registry = client_registry_service
        self._camilladsp_service = camilladsp_service
        self._proxy_service = proxy_service
        self._routing_service = routing_service
        self._equalizer_router = equalizer_router
        self._state_machine = state_machine

        # Async lock for thread safety
        self._lock = asyncio.Lock()

        self.logger.info("MultiroomEqualizerService created")

    # =========================================================================
    # Per-Client Access Layer — the unified EQ source of truth
    # =========================================================================

    def _is_local(self, target_id: str) -> bool:
        """True for the local device: the ``LOCAL_TARGET`` sentinel (addressable
        without a registry entry, e.g. multiroom off) or a registered client
        flagged ``is_local``."""
        return target_id == LOCAL_TARGET or bool(
            self._registry and self._registry.is_local_client(target_id)
        )

    def _is_eq_independent(self, mac_id: str) -> bool:
        """True for a zone member whose EQ is detached from its zone.

        Such a member stays in the zone for playback but is excluded from every
        zone-EQ operation (fan-out, representative selection, enabled
        conjunction) and is addressed directly as a client instead.
        """
        client = self._registry.get_client(mac_id) if self._registry else None
        return bool(client and client.eq_independent)

    async def get_client_eq(self, mac_id: str) -> EqualizerSettings:
        """Read a client's one EQ record.

        Local client → snapshot from CamillaDSP (the equalizer.json-backed
        cache); remote client → the registry's client_equalizer store (a neutral
        default when nothing has been saved yet). Always returns a record.
        """
        if self._is_local(mac_id):
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
        if self._is_local(mac_id):
            cds = self._camilladsp_service
            applied = await self._apply_to_local(settings)
            if applied and cds:
                # Successful apply (connected DSP): snapshot the live cache to disk.
                if settings.custom_gains is not None:
                    cds.set_custom_gains(settings.custom_gains)
                await cds.persist_state()
            elif cds and not cds.connected:
                # DSP disconnected: the live apply no-op'd without touching the
                # cache, so capture the intent into the cache + equalizer.json.
                # restore_effects() re-pushes it on reconnect, so the local record
                # never drifts from the zone's other members (boot/reconnect window).
                await cds.update_cache(settings)
            # else: connected but the apply raised → cache is uncertain, leave it.
            return applied
        if self._registry:
            await self._registry.set_client_equalizer(mac_id, settings)
        return await self._apply_to_remote(mac_id, settings)

    async def get_zone_eq(self, zone_id: str) -> Optional[EqualizerSettings]:
        """Zone EQ derives from its members (kept identical).

        Reads the local member if the zone contains one (most authoritative),
        otherwise the first member. Returns None if the zone has no members.

        ``enabled`` is the exception: it lives in two domains — settings.json for
        the local member, the per-client record for a satellite — so one member
        cannot speak for the zone. It is reported as the conjunction, and a zone
        that says ``False`` therefore means "not every member is applying
        effects", never "the local one is bypassed".
        """
        if not self._registry:
            return None
        zone = self._registry.get_zone(zone_id)
        if not zone or not zone.client_ids:
            return None
        # A member with an independent EQ has left the zone's shared record; it is
        # neither the representative nor part of the enabled conjunction. If every
        # member is independent the zone has no EQ of its own to report.
        members = [m for m in zone.client_ids if not self._is_eq_independent(m)]
        if not members:
            return None
        member = next(
            (m for m in members if self._registry.is_local_client(m)),
            members[0],
        )
        record = await self.get_client_eq(member)
        others = [m for m in members if m != member]
        for mac_id in others:
            if not (await self.get_client_eq(mac_id)).enabled:
                record.enabled = False
                break
        return record

    def _member_record(self, mac_id: str, record: EqualizerSettings) -> EqualizerSettings:
        """A fresh per-member copy of a zone record, carrying the member's own
        ``enabled``.

        Everything in the record is the zone's except the master bypass. A zone
        holds no ``enabled`` of its own: ``get_zone_eq`` reports the conjunction
        of its members', and the flag itself lives in a different domain per
        member — settings.json for the local one, the per-client record for a
        satellite. Writing the derived value back would make the fan-out a
        second way to set it, and the conjunction reads False as soon as ONE
        member is bypassed (the default on a fresh unit), so a band edit would
        silently bypass every satellite that had effects on. The local path
        already ignores it — neither ``apply_settings`` nor ``update_cache``
        touches the master toggle — and this keeps the remote path symmetrical.
        ``set_zone_equalizer_effects_enabled`` is the one way to change it.
        """
        copy = EqualizerSettings.from_dict(record.to_dict())
        existing = self._registry.get_client_equalizer(mac_id) if self._registry else None
        # Nothing stored yet → nothing to preserve, so the neutral default
        # applies rather than the zone's conjunction.
        copy.enabled = existing.enabled if existing else EqualizerSettings.default().enabled
        return copy

    async def set_zone_eq(self, zone_id: str, settings: EqualizerSettings) -> bool:
        """Apply one EQ record to every zone member, keeping them identical.

        Each member receives its own copy so later per-member edits never alias
        (and keeps its own ``enabled`` — see ``_member_record``). Members are
        written in parallel.

        Returns True only when *every* member took it: the zone's invariant is
        that its members hold identical records, so a partial fan-out is a
        failure, not a success. Each member that failed is logged by name.
        """
        if not self._registry:
            self.logger.error("ClientRegistryService not available")
            return False
        zone = self._registry.get_zone(zone_id)
        if not zone:
            raise ValueError(f"Zone not found: {zone_id}")
        # Skip members that detached their EQ from the zone — they own their record.
        members = [m for m in zone.client_ids if not self._is_eq_independent(m)]
        results = await asyncio.gather(
            *[
                self.set_client_eq(mac_id, self._member_record(mac_id, settings))
                for mac_id in members
            ],
            return_exceptions=True,
        )
        return not failed_members(self.logger, f"Zone {zone_id} equalizer", members, results)

    # =========================================================================
    # Zone / Client Equalizer Methods — route-facing wrappers over the access layer
    # =========================================================================

    async def apply_zone_equalizer(self, zone_id: str, settings: EqualizerSettings) -> bool:
        """Apply equalizer settings to a whole zone (fan-out to all members).

        False when at least one member did not take the record; ``set_zone_eq``
        has already logged which ones by name.

        Raises:
            ValueError: If zone not found
        """
        async with self._lock:
            result = await self.set_zone_eq(zone_id, settings)
            if result:
                self.logger.info(f"Zone {zone_id} equalizer applied to all members")
            return result

    async def resolve_preset_gains(
        self,
        preset_id: str,
        settings: Optional[EqualizerSettings],
        target_type: str,
        target_id: str,
    ) -> list:
        """Resolve the gain values of a preset ID (builtin or custom) for one target.

        The target is required because "custom" has no global meaning: it is the
        curve saved on *that* record. The local DAC's own curve is the fallback
        for the local target only — a satellite or a zone that has never saved
        one gets the flat default, never the server's curve (which is what
        ``GET /target/{target}`` already refuses to show for the same reason).
        """
        from backend.core.equalizer.presets import get_preset_by_id, DEFAULT_CUSTOM_GAINS

        if preset_id == "custom":
            # The target's own saved curve, when it has one.
            if settings and settings.custom_gains:
                return settings.custom_gains
            if (
                target_type == "client"
                and self._is_local(target_id)
                and self._camilladsp_service
            ):
                return await self._camilladsp_service.get_custom_gains()
            return DEFAULT_CUSTOM_GAINS

        preset = get_preset_by_id(preset_id)
        if not preset:
            raise ValueError(f"Preset not found: {preset_id}")
        return preset["gains"]

    async def save_custom_preset(self, target_type: str, target_id: str) -> bool:
        """Snapshot current filter gains as the 'custom' preset for a zone or client.

        Persists the gains into custom_gains and sets active_preset='custom' on the
        target's record(s) through the per-client access layer, so name and gains
        travel together (local → equalizer.json, remote → registry, zone → every
        member). Returns whether the apply reached every member.
        """
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type.capitalize()} not found: {target_id}")

        current.custom_gains = [f.gain for f in current.filters[:10]]
        current.active_preset = "custom"
        return await self.apply_equalizer(target_type, target_id, current)

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

    async def load_preset(
        self, target_type: str, target_id: str, preset_id: str
    ) -> tuple[bool, list]:
        """Load an EQ preset for a zone or client, returning (success, gains).

        Preserves the target's compressor/loudness/mono; only the ten bands and
        the preset name change. The resolved gains come back so the caller does
        not have to read the record and re-resolve them to report what was
        applied.

        Raises:
            ValueError: unknown zone, unknown client, a client that is in a zone
                (drive those through the zone), or an unknown preset.
        """
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            # Only a zone can be missing — a client always yields a record.
            raise ValueError(f"Zone not found: {target_id}")

        gains = await self.resolve_preset_gains(preset_id, current, target_type, target_id)
        current.filters = self._build_preset_filters(gains)
        current.active_preset = preset_id
        return await self.apply_equalizer(target_type, target_id, current), gains

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
        # The local device is the DAC — it owns equalizer.json and is never "in a
        # zone" in this direct-local context; address it as the sentinel without a
        # registry lookup (the registry is empty when multiroom is off).
        if self._is_local(mac_id):
            result = await self.set_client_eq(mac_id, settings)
            self.logger.info(f"Client {mac_id} equalizer settings updated")
            return result

        if not self._registry:
            self.logger.error("ClientRegistryService not available")
            return False

        client = self._registry.get_client(mac_id)
        if not client:
            raise ValueError(f"Client not found: {mac_id}")

        # A zone member is normally driven through the zone — unless it detached
        # its EQ (eq_independent), in which case it is addressed directly.
        if client.zone_id is not None and not client.eq_independent:
            raise ValueError(
                f"Client {mac_id} is in zone {client.zone_id}. "
                "Use apply_zone_equalizer() instead."
            )

        result = await self.set_client_eq(mac_id, settings)
        self.logger.info(f"Client {mac_id} equalizer settings updated")
        return result

    # =========================================================================
    # Target-Agnostic Equalizer Methods
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
            return await self.get_zone_eq(target_id)
        elif target_type == "client":
            return await self.get_client_eq(target_id)
        else:
            raise ValueError(f"Invalid target_type: {target_type}. Must be 'zone' or 'client'")

    # =========================================================================
    # CamillaDSP Application with Error Handling
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

        # One batched graph write for the whole record (filters + compressor +
        # loudness + mono + active_preset name), instead of 13 sequential read-
        # modify-write round-trips. persist/broadcast suppressed: set_client_eq
        # snapshots to equalizer.json and the zone broadcasts the complete state.
        # apply_settings is @handle_errors(default=False), so it never raises.
        success = await self._camilladsp_service.apply_settings(settings, persist=False)
        if not success:
            self.logger.warning("Failed to apply equalizer settings to local")
        return success

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

        applied = await self._proxy_service.apply_record(client_ip, settings)
        if applied:
            self.logger.debug(f"Equalizer settings applied to remote client {mac_id}")
        return applied

    # =========================================================================
    # Partial Equalizer Update Methods
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
           registry, each keeping its own ``enabled``; local → its live DSP
           snapshot, after the router applies below)
        2. Route the targeted update to ONLINE members via EqualizerRouter
        3. Broadcast targeted WebSocket event with only the changed sub-object

        Raises whatever the fan-out raised (typically ``SatelliteUnreachable``,
        which the api/ layer maps to a 503) once the three steps are done — a
        member that refused the command must not be reported as applied.

        Args:
            target_type: "zone" or "client"
            target_id: Zone ID or client MAC ID
            current: The mutated EqualizerSettings to persist
            router_method: EqualizerRouter method name (e.g., "update_filter")
            router_kwargs: Kwargs for the router method (excluding mac_id, persist, broadcast)
            broadcast_settings: Partial equalizer_settings dict for the WebSocket broadcast
        """
        # A client target must be a known client (a zone target is validated by the
        # caller via get_zone). Fail loud so an unknown MAC surfaces as 404 instead
        # of silently materializing a phantom per-client record. The local sentinel
        # is exempt — it has no registry entry when multiroom is off.
        if (
            target_type == "client"
            and not self._is_local(target_id)
            and self._registry
            and not self._registry.get_client(target_id)
        ):
            raise ValueError(f"Client not found: {target_id}")

        # Resolve the affected members (zone fan-out, or a single client). A zone
        # member that detached its EQ is excluded — it is edited as its own client.
        if target_type == "zone":
            zone = self._registry.get_zone(target_id)
            members = [
                m for m in zone.client_ids if not self._is_eq_independent(m)
            ] if zone else []
        else:
            members = [target_id]

        # Persist `current` to each REMOTE member's record (the per-client source
        # of truth — a fresh copy each, so members never alias) in ONE write: the
        # registry rewrites the whole of settings.json per call, and a drag emits
        # 20 requests a second. The local member is persisted from its live DSP
        # cache below, after the router applies.
        local_touched = any(self._is_local(member) for member in members)
        remote_records = {
            member: self._member_record(member, current)
            for member in members
            if not self._is_local(member)
        }
        if remote_records and self._registry:
            await self._registry.set_clients_equalizer(
                remote_records, broadcast=False, defer_persist=True
            )

        # Route the targeted update to ONLINE members via EqualizerRouter
        fanout_error: Optional[BaseException] = None
        if self._equalizer_router:
            method = getattr(self._equalizer_router, router_method)
            if target_type == "zone":
                online_clients = [
                    c for c in self._registry.get_online_zone_clients(target_id)
                    if not self._is_eq_independent(c.mac_id)
                ]
                if online_clients:
                    results = await asyncio.gather(
                        *[method(mac_id=c.mac_id, persist=False, **router_kwargs)
                          for c in online_clients],
                        return_exceptions=True,
                    )
                    # Same outcome as the single-client branch below, which lets a
                    # SatelliteUnreachable reach api_error_handler (→ 503): gather
                    # holds the exceptions back, it does not make the write
                    # succeed. Every member is named in the log first, since one
                    # raise can only carry one of them. "skipped" is not a failure
                    # — the client went offline between the online filter and the
                    # call, and its record above syncs on reconnection.
                    outcomes = [
                        r if isinstance(r, BaseException) else r.get("status") != "error"
                        for r in results
                    ]
                    failed_members(
                        self.logger,
                        f"Zone {target_id} {router_method}",
                        [c.mac_id for c in online_clients],
                        outcomes,
                    )
                    # Raised at the end, not here: the members that DID take the
                    # update still need the local snapshot and the broadcast below,
                    # or equalizer.json drifts from the DSP the router just wrote.
                    fanout_error = next(
                        (r for r in results if isinstance(r, BaseException)), None
                    )
            else:
                await method(mac_id=target_id, persist=False, **router_kwargs)
        else:
            self.logger.warning(f"EqualizerRouter not available, {router_method} not applied to clients")

        # Persist the local member's record so it survives a restart. When the DSP
        # is connected the router already applied the targeted change to the live
        # cache, so we schedule a snapshot of it — debounced, because this is the
        # drag path and an immediate write here cost one full rewrite + fsync of
        # equalizer.json per throttled request. When it is disconnected the router
        # no-op'd, so we capture the intended record into the cache + equalizer.json
        # instead — restore_effects() re-pushes it on reconnect (no drift from
        # remote members), and that branch stays immediate: it is not a hot path.
        if local_touched and self._camilladsp_service:
            if self._camilladsp_service.connected:
                self._camilladsp_service.schedule_persist()
            else:
                await self._camilladsp_service.update_cache(current)

        # Broadcast targeted WebSocket event
        if self._state_machine:
            await self._state_machine.broadcast(MultiroomEqualizerChanged(
                target_type=target_type,
                target_id=target_id,
                equalizer_settings=broadcast_settings,
            ))

        if fanout_error is not None:
            raise fanout_error

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
                },
            },
            broadcast_settings={
                # Wire shape (freq/type) — the frontend WS handler reads freq/type.
                "filters": [updated_filter.to_wire_dict()],
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
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

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
        current = await self.get_equalizer(target_type, target_id)
        if not current:
            raise ValueError(f"{target_type} not found: {target_id}")

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

    async def set_client_equalizer_effects_enabled(
        self, mac_id: str, enabled: bool, routing_service=None
    ) -> bool:
        """Enable/disable equalizer effects for a single client.

        Local client → ``routing_service`` bypass/restore (its enabled flag lives
        in settings, not the registry). Remote client → push to the satellite (if
        online) and persist the flag on its per-client record so an offline /
        reconnecting member recovers it. This is the single per-client primitive
        the ``/client/{mac}/enabled`` route and the zone fan-out both build on.

        Returns True when the change reached an online client OR was persisted for
        an offline one (it syncs on reconnect); False only on a failed online push
        or a missing routing service for the local client.
        """
        routing = routing_service or self._routing_service
        if self._is_local(mac_id):
            if routing:
                return await routing.set_equalizer_effects_enabled(enabled)
            self.logger.warning("Routing service not available for local equalizer toggle")
            return False
        # Remote: must be a known client (fail loud → 404 for an unknown MAC).
        if self._registry and not self._registry.get_client(mac_id):
            raise ValueError(f"Client not found: {mac_id}")
        return await self._set_remote_client_enabled(
            mac_id, enabled, fallback=EqualizerSettings.default
        )

    async def set_local_equalizer_effects_enabled(self, enabled: bool) -> bool:
        """Toggle the local master bypass, keeping a zone's members identical.

        The dock's Equalizer app owns the *local* flag, but a zone's invariant is
        that its members hold one record: writing the local domain alone leaves
        every satellite playing under its own flag, audibly out of step, with no
        later transition to repair it. So when the local client is a zone member
        the toggle fans out to the zone; otherwise it is the plain local write.

        This is the entry point for any caller that means "the equalizer of this
        appliance", as opposed to ``/equalizer/target/local/enabled``, which
        addresses the local client explicitly.
        """
        zone = (
            self._registry.get_zone_for_client(self._local_mac_id())
            if self._registry
            else None
        )
        if zone:
            return await self.set_zone_equalizer_effects_enabled(zone.id, enabled)
        return await self.set_client_equalizer_effects_enabled(LOCAL_TARGET, enabled)

    def _local_mac_id(self) -> str:
        """The registered local client's mac_id, or ``LOCAL_TARGET`` when the
        registry holds none (multiroom off — the local device is addressable
        without a registry entry)."""
        if not self._registry:
            return LOCAL_TARGET
        return next(
            (mac for mac, c in self._registry.get_all_clients().items() if c.is_local),
            LOCAL_TARGET,
        )

    async def _set_remote_client_enabled(self, client_id: str, enabled: bool, *, fallback) -> bool:
        """Push the enabled flag to an online satellite and persist it on the
        client's per-client record (the source of truth).

        ``fallback`` builds the neutral record when the client has none yet —
        ``EqualizerSettings.default`` for a standalone client, ``default_for_zone``
        for a zone member (mono on).

        Returns True when an online push succeeded OR the flag was persisted for an
        offline/unknown client (syncs on reconnect); False only when an online push
        failed.
        """
        client = self._registry.get_client(client_id) if self._registry else None

        pushed = False
        if client and client.online and client.ip and self._proxy_service:
            try:
                result = await self._proxy_service.request(
                    client.ip, "PUT", "/equalizer/enabled", {"enabled": enabled}
                )
                pushed = result.get("status") == "success"
            except Exception as e:
                self.logger.warning(f"Failed to push equalizer enabled to {client_id}: {e}")

        if self._registry:
            existing = self._registry.get_client_equalizer(client_id)
            record = (
                EqualizerSettings.from_dict(existing.to_dict()) if existing else fallback()
            )
            record.enabled = enabled
            await self._registry.set_client_equalizer(client_id, record, broadcast=False)

        return pushed if (client and client.online) else True

    async def set_zone_equalizer_effects_enabled(self, zone_id: str, enabled: bool) -> bool:
        """
        Enable/disable equalizer effects for all clients in a zone.

        Fans out to each member through the per-client primitives (local →
        routing_service bypass/restore; remote → push + persist), then broadcasts
        the zone-level event.

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
                # Local client: use routing_service (bypass/restore the DSP effects).
                if self._routing_service:
                    try:
                        if await self._routing_service.set_equalizer_effects_enabled(enabled):
                            success_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to set equalizer enabled for local: {e}")
            elif await self._set_remote_client_enabled(
                client_id, enabled, fallback=EqualizerSettings.default_for_zone
            ):
                success_count += 1

        if self._state_machine:
            await self._state_machine.broadcast(
                EqualizerZoneEnabledChanged(zone_id=zone_id, enabled=enabled)
            )

        return success_count > 0

