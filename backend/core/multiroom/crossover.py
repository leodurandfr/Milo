# backend/core/multiroom/crossover.py
"""
Crossover service for speaker type management in multiroom audio.

Manages automatic highpass filter application to speakers when a subwoofer
is present in a zone. The subwoofer receives the full signal while speakers
get a highpass filter to remove bass (handled by the subwoofer).

Integration with ClientRegistryService:
- Subscribes to registry availability events
- Queries registry for speaker types (single source of truth)
- Updates registry when speaker type changes
- Applies pending settings when clients reconnect
"""
import asyncio
import logging
from typing import Dict, Any, Optional, TYPE_CHECKING

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.shared.fanout import failed_members
from backend.core.models.ws_events import MultiroomZoneChanged
from backend.core.multiroom.models import (
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCY,
)

if TYPE_CHECKING:
    from backend.core.equalizer.client_proxy import EqualizerClientProxyService
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.settings import SettingsService
    from backend.core.state import AudioStateMachine


# Every DSP setting the pending queue can hold. Producers (this service's own
# crossover/lowpass proxying, and SnapcastWebSocketService's reconnection sync)
# and apply_pending_settings' dispatch must agree on this set: a type queued but
# not dispatched is silently discarded, since apply_pending_settings pops the
# whole per-client dict. Pinned by tests/architecture/test_service_wiring.py.
#
# "record" holds a whole EqualizerSettings; crossover/lowpass stay separate
# because they are derived from the zone's speaker layout, not from the record.
PENDING_SETTING_TYPES = frozenset({"crossover", "lowpass", "record"})


class CrossoverService:
    """
    Manages speaker types and crossover logic across multiroom zones.

    Key responsibilities:
    - Track client speaker types (satellite, bookshelf, tower, subwoofer)
    - Detect when a zone contains a subwoofer
    - Automatically apply highpass filters to non-subwoofer clients in the zone
    - Coordinate crossover frequency across zone members
    - Queue pending settings for offline clients
    """

    DEFAULT_Q = 0.707  # Butterworth (flattest passband)

    def __init__(self, settings_service: Optional["SettingsService"] = None, camilladsp_service=None,
                 state_machine: Optional["AudioStateMachine"] = None, volume_service=None,
                 proxy_service: Optional["EqualizerClientProxyService"] = None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.camilladsp_service = camilladsp_service

        # Acyclic deps (constructor-injected; neither holds a back-reference).
        self.state_machine = state_machine
        self.volume_service = volume_service
        # Satellite HTTP transport — shared keep-alive session, non-raising
        # try_request() (this service owns its queue-pending retry semantics).
        self._proxy_service = proxy_service

        # Client registry reference (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        # Pending settings queue for offline clients
        self._pending_settings: Dict[str, Dict[str, Any]] = {}

        # Background retry tasks keyed by client_id — one per client at most.
        # Tracked for deduplication and clean cancellation on shutdown.
        self._retry_tasks: Dict[str, asyncio.Task] = {}
        self._bg = BackgroundTaskSet(self.logger, "crossover")

    def set_registry(self, registry: "ClientRegistryService") -> None:
        """
        Set the client registry and subscribe to availability events.

        Args:
            registry: ClientRegistryService instance
        """
        self._registry = registry
        registry.subscribe(self._handle_registry_event)
        self.logger.info("CrossoverService subscribed to ClientRegistryService events")

    async def _handle_registry_event(self, event_type: str, data: dict) -> None:
        """Handle events from ClientRegistryService."""
        from backend.core.multiroom.models import RegistryEventType

        if event_type == RegistryEventType.CLIENT_CONNECTED:
            mac_id = data.get("mac_id")
            if mac_id:
                if self.has_pending_settings(mac_id):
                    self.logger.info(f"Client {mac_id} reconnected, applying pending settings")
                    await self.apply_pending_settings(mac_id)

                await self._recalculate_zones_for_client(mac_id)

                # If recalculation queued settings (CamillaDSP not fully ready,
                # e.g. after audio card change), schedule a delayed retry
                if self.has_pending_settings(mac_id):
                    self._create_retry_task(mac_id, self._delayed_retry_pending(mac_id))

        elif event_type == RegistryEventType.CLIENT_DISCONNECTED:
            mac_id = data.get("mac_id")
            if mac_id:
                # A disconnect can take away the zone's only subwoofer, which is
                # what decides whether the crossover applies at all
                await self._recalculate_zones_for_client(mac_id)

        elif event_type == RegistryEventType.CLIENT_UPDATED:
            # Client updated (speaker_type change triggers crossover recalculation)
            mac_id = data.get("mac_id")
            if mac_id:
                await self._recalculate_zones_for_client(mac_id)

        elif event_type == RegistryEventType.ZONE_CREATED:
            zone_id = data.get("zone_id")
            if zone_id and isinstance(zone_id, str):
                try:
                    self.logger.info(f"Zone {zone_id} created, applying crossover filters")
                    await self.apply_zone_crossover(zone_id)
                except Exception as e:
                    self.logger.error(f"Error applying crossover for new zone {zone_id}: {e}")

        elif event_type == RegistryEventType.ZONE_UPDATED:
            zone_id = data.get("zone_id")
            if zone_id and isinstance(zone_id, str):
                try:
                    self.logger.info(f"Zone {zone_id} updated, recalculating crossover")
                    await self.apply_zone_crossover(zone_id)
                except Exception as e:
                    self.logger.error(f"Error recalculating crossover for zone {zone_id}: {e}")

        elif event_type == RegistryEventType.ZONE_DELETED:
            zone_data = data.get("zone", {})
            client_ids = zone_data.get("client_ids", [])
            zone_id = zone_data.get("id", "unknown")
            if client_ids:
                try:
                    self.logger.info(f"Zone {zone_id} deleted, disabling filters for {len(client_ids)} clients")
                    for client_id in client_ids:
                        await self._set_client_filter(client_id, "crossover", False, DEFAULT_CROSSOVER_FREQUENCY)
                        await self._set_client_filter(client_id, "lowpass", False, DEFAULT_CROSSOVER_FREQUENCY)
                except Exception as e:
                    self.logger.error(f"Error disabling filters after zone {zone_id} deletion: {e}")

        elif event_type == RegistryEventType.ZONE_CLIENT_REMOVED:
            # Client removed from zone - disable filters and recalculate
            zone_id = data.get("zone_id")
            mac_id = data.get("mac_id")
            try:
                if mac_id:
                    self.logger.info(f"Client {mac_id} removed from zone {zone_id}, disabling filters")
                    await self._set_client_filter(mac_id, "crossover", False, DEFAULT_CROSSOVER_FREQUENCY)
                    await self._set_client_filter(mac_id, "lowpass", False, DEFAULT_CROSSOVER_FREQUENCY)
                # Only if the zone outlived the removal. Taking a member out of a
                # two-member zone dissolves it, and this event is emitted before
                # the ZONE_DELETED that says so — recalculating regardless logged
                # "Zone not found" at warning on the normal, successful path of
                # taking a zone apart. The ZONE_DELETED arm disables the
                # remaining members' filters a moment later.
                if (zone_id and isinstance(zone_id, str)
                        and self._registry and self._registry.get_zone(zone_id)):
                    await self.apply_zone_crossover(zone_id)
            except Exception as e:
                self.logger.error(f"Error handling client {mac_id} removal from zone {zone_id}: {e}")

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize the crossover service."""
        self.logger.info("Initializing CrossoverService...")
        self.logger.info("CrossoverService initialized (using ClientRegistryService for speaker types)")
        return True

    def get_client_speaker_type(self, client_id: str) -> str:
        """Get the speaker type for a client."""
        if self._registry:
            client = self._registry.get_client(client_id)
            if client:
                return client.speaker_type
        return DEFAULT_SPEAKER_TYPE

    def is_client_subwoofer(self, client_id: str) -> bool:
        """Check if a client is marked as a subwoofer."""
        return self.get_client_speaker_type(client_id) == "subwoofer"

    # === Zone Crossover Management ===

    @handle_errors(default={"frequency": DEFAULT_CROSSOVER_FREQUENCY, "auto": True,
                            "enabled": False, "has_subwoofer": False})
    async def get_zone_crossover(self, zone_id: str) -> Dict[str, Any]:
        """Get crossover settings for a zone.

        `frequency` is always the number the DSP will actually use; `auto` says
        whether it was derived from the members' speaker types or pinned by the
        user. Answering a bare None for auto would leave every consumer to
        re-derive it — which is how the derivation came to exist twice.
        """
        unknown = {
            "frequency": DEFAULT_CROSSOVER_FREQUENCY,
            "auto": True,
            "enabled": False,
            "has_subwoofer": False
        }
        if not self._registry:
            return unknown

        zone = self._registry.get_zone(zone_id)
        if not zone:
            return unknown

        has_subwoofer = any(self.is_client_subwoofer(cid) for cid in zone.client_ids)

        return {
            "frequency": self._resolve_frequency(zone),
            "auto": zone.crossover_frequency is None,
            "enabled": has_subwoofer,
            "has_subwoofer": has_subwoofer
        }

    def _resolve_frequency(self, zone) -> int:
        """The zone's pinned frequency, or the one its speakers imply."""
        if zone.crossover_frequency is not None:
            return zone.crossover_frequency
        return self._registry.auto_crossover_frequency(zone)

    @handle_errors(default=False)
    async def set_zone_crossover_frequency(
        self, zone_id: str, frequency: Optional[float]
    ) -> bool:
        """Pin the crossover frequency for a zone, or hand it back to auto.

        `None` is not "no change" here — it is the request to stop pinning and
        let the members' speaker types decide again.
        """
        if not self._registry:
            return False

        if frequency is not None:
            frequency = int(max(20, min(200, frequency)))

        zone = self._registry.get_zone(zone_id)
        if not zone:
            self.logger.warning(f"Zone {zone_id} not found")
            return False

        # update_zone emits ZONE_UPDATED, so the enriched zone (carrying the new
        # crossover_frequency) reaches the UI on multiroom/zone_changed — no
        # second event for the same change.
        await self._registry.update_zone(zone_id, crossover_frequency=frequency)

        self.logger.info(
            f"Zone {zone_id} crossover frequency set to "
            f"{'auto' if frequency is None else f'{frequency} Hz'}"
        )

        # Apply the updated crossover filters to all zone clients. Its verdict is
        # this method's: the route raises a 500 on False, and the pin above stays
        # persisted either way — it is what the reconnection sync replays.
        return await self.apply_zone_crossover(zone_id)

    @handle_errors(default=False)
    async def apply_zone_crossover(self, zone_id: str) -> bool:
        """Apply crossover settings to all clients in a zone.

        False when a member that is *still online* refused one of its filters,
        each named at error level. A member that went offline mid-apply is not
        one: _set_client_filter queued its setting and CLIENT_CONNECTED replays
        it, while nothing at all drains that queue for a client that keeps
        answering — which is exactly the case the operator has to be told about.
        """
        if not self._registry:
            return False

        zone = self._registry.get_zone(zone_id)
        if not zone:
            self.logger.warning(f"Zone {zone_id} not found")
            return False

        client_ids = zone.client_ids
        frequency = self._resolve_frequency(zone)

        available_clients = {
            cid for cid in client_ids
            if self._registry.is_client_online(cid)
        }

        has_subwoofer = any(
            self.is_client_subwoofer(cid) and cid in available_clients
            for cid in client_ids
        )

        # An online subwoofer in the zone is the whole condition: it is what the
        # highpass hands the low band to. No stored override — a zone carries no
        # crossover_enabled of its own.
        should_apply_crossover = has_subwoofer

        self.logger.info(
            f"Applying crossover to zone {zone_id}: "
            f"has_sub={has_subwoofer}, "
            f"should_apply={should_apply_crossover}, freq={frequency}Hz, "
            f"available_clients={list(available_clients)}"
        )

        members: list = []
        results: list = []

        for client_id in client_ids:
            if client_id not in available_clients:
                self.logger.debug(f"Skipping unavailable client {client_id}")
                continue

            is_sub = self.is_client_subwoofer(client_id)

            # The filter the member keeps is enabled before the one it drops is
            # removed, so it never runs full-range between the two pushes.
            if should_apply_crossover and is_sub:
                wanted = (("lowpass", True), ("crossover", False))
            elif should_apply_crossover:
                wanted = (("crossover", True), ("lowpass", False))
            else:
                wanted = (("crossover", False), ("lowpass", False))

            took = all([
                await self._set_client_filter(client_id, name, enabled, frequency)
                for name, enabled in wanted
            ])

            if not took and not self._registry.is_client_online(client_id):
                self.logger.debug(
                    f"Client {client_id} went offline mid-apply, crossover queued as pending"
                )
                continue

            members.append(client_id)
            results.append(took)

        return not failed_members(self.logger, f"Zone {zone_id} crossover", members, results)

    @handle_errors(default=False)
    async def _set_client_filter(
        self,
        client_id: str,
        filter_name: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """Apply or remove a filter (crossover or lowpass) on a specific client.

        Args:
            client_id: Client MAC address
            filter_name: Filter type ("crossover" or "lowpass")
            enabled: Whether the filter is enabled
            frequency: Filter frequency in Hz
        """
        client = self._registry.get_client(client_id) if self._registry else None
        is_local = client.is_local if client else False

        if is_local:
            if self.camilladsp_service:
                method = getattr(self.camilladsp_service, f"set_{filter_name}_filter")
                return await method(
                    enabled=enabled,
                    frequency=frequency,
                    q=self.DEFAULT_Q
                )
            return False
        else:
            if not client or not client.ip:
                self.logger.error(f"Cannot proxy {filter_name}: client {client_id} has no IP address")
                return False
            if self._registry and not self._registry.is_client_online(client_id):
                await self.queue_pending_settings(client_id, filter_name, {
                    "enabled": enabled,
                    "frequency": frequency
                })
                return False
            return await self._proxy_filter_to_client(
                filter_name, client.ip, enabled, frequency, client_id=client_id
            )

    async def _proxy_filter_to_client(
        self,
        filter_name: str,
        ip_address: str,
        enabled: bool,
        frequency: float,
        client_id: str = None
    ) -> bool:
        """Proxy filter settings (crossover or lowpass) to a remote milo-client.

        Args:
            filter_name: Filter type ("crossover" or "lowpass")
            ip_address: The client's IP address for HTTP requests
            enabled: Whether the filter is enabled
            frequency: Filter frequency in Hz
            client_id: MAC address for logging and queue_pending_settings (optional)
        """
        identifier = client_id or ip_address
        if not self._proxy_service:
            self.logger.error(f"Cannot proxy {filter_name} to {identifier}: proxy service not available")
            return False
        try:
            payload = {
                "enabled": enabled,
                "frequency": frequency,
                "q": self.DEFAULT_Q
            }

            status = await self._proxy_service.try_request(
                ip_address, "PUT", f"/equalizer/{filter_name}", payload, timeout=5.0
            )
            if status == 200:
                self.logger.info(
                    f"{filter_name.capitalize()} {'enabled' if enabled else 'disabled'} "
                    f"on client {identifier} at {frequency} Hz"
                )
                return True

            # status 0 = unreachable; non-200 = rejected (e.g. CamillaDSP not
            # ready after reboot). Either way queue as pending for the next sync.
            reason = "unreachable" if status == 0 else f"HTTP {status}"
            message = (
                f"Client {identifier} did not apply {filter_name} ({reason}), queued as pending"
            )
            # A client the registry still calls online is the case nothing
            # recovers from: the pending queue drains on CLIENT_CONNECTED only,
            # so a client that refuses without ever disconnecting sits on the old
            # filter forever. Error, so it reaches the banner. Offline is the
            # expected case and stays quiet.
            if client_id and self._registry and self._registry.is_client_online(client_id):
                self.logger.error(message)
            else:
                self.logger.debug(message)
            await self.queue_pending_settings(identifier, filter_name, {
                "enabled": enabled,
                "frequency": frequency
            })
            return False

        except Exception as e:
            self.logger.error(f"Error proxying {filter_name} to client {identifier}: {e}")
            return False

    @handle_errors(default=None)
    async def _recalculate_zones_for_client(self, client_id: str) -> None:
        """Recalculate crossover for zone containing this client."""
        if not self._registry:
            self.logger.warning("Registry not available, cannot recalculate zones")
            return

        zone = self._registry.get_zone_for_client(client_id)
        if zone:
            await self.apply_zone_crossover(zone.id)

            # Broadcast zone update directly (WebSocket) without using
            # registry._emit_event(ZONE_UPDATED), which would re-enter
            # _handle_registry_event and call apply_zone_crossover a second time.
            if self.state_machine:
                await self.state_machine.broadcast(MultiroomZoneChanged(
                    action="updated",
                    zone_id=zone.id,
                    zone=self._registry.zone_to_enriched_dict(zone),
                ))

    # === Pending Settings Queue for Offline Clients ===

    async def queue_pending_settings(self, client_id: str, setting_type: str, settings: Dict[str, Any]) -> None:
        """Queue a DSP setting whose push to a client failed, for replay on reconnect.

        ``setting_type`` must be one of PENDING_SETTING_TYPES — an unlisted type
        would be stored and then dropped unreplayed by apply_pending_settings, so
        it fails loud instead. tests/architecture/test_service_wiring.py pins the
        producers and this dispatch to the same set.
        """
        if setting_type not in PENDING_SETTING_TYPES:
            raise ValueError(
                f"Unknown pending setting type {setting_type!r} "
                f"(known: {sorted(PENDING_SETTING_TYPES)})"
            )
        if client_id not in self._pending_settings:
            self._pending_settings[client_id] = {}

        self._pending_settings[client_id][setting_type] = settings
        self.logger.info(f"Queued {setting_type} settings for offline client {client_id}")

    async def apply_pending_settings(self, client_id: str) -> bool:
        """Replay every queued setting to a reconnected client."""
        if client_id not in self._pending_settings:
            return False

        pending = self._pending_settings.pop(client_id)
        if not pending:
            return False

        self.logger.info(f"Applying pending settings to reconnected client {client_id}: {list(pending.keys())}")

        success = True

        for filter_name in ("crossover", "lowpass"):
            if filter_name not in pending:
                continue
            queued = pending[filter_name]
            result = await self._set_client_filter(
                client_id, filter_name,
                queued.get("enabled", False),
                queued.get("frequency", DEFAULT_CROSSOVER_FREQUENCY)
            )
            if not result:
                success = False
                self.logger.debug(f"Failed to apply pending {filter_name} to {client_id} (zone recalculation will re-apply)")

        if "record" in pending:
            # Only ever queued for a remote client — the local client's record is
            # equalizer.json, restored by CamillaDSPService itself.
            client = self._registry.get_client(client_id) if self._registry else None
            applied = False
            if client and client.ip and self._proxy_service:
                applied = await self._proxy_service.apply_record(client.ip, pending["record"])
                if not applied:
                    self.logger.warning(f"Failed to apply pending EQ record to {client_id}")
            else:
                self.logger.warning(f"Cannot apply pending EQ record: client {client_id} unreachable")

            if not applied:
                success = False
                # Unlike crossover/lowpass above, nothing else re-applies a
                # record: the zone recalculation that covers those filters does
                # not touch it. The pop at the top of this method already removed
                # it, so dropping it here leaves the client on whatever EQ it
                # booted with until someone edits the EQ by hand — and the
                # unreachable branch is the *likely* one, since a client that
                # just failed admission is exactly a client without an ip yet.
                self._pending_settings.setdefault(client_id, {})["record"] = pending["record"]

        return success

    def has_pending_settings(self, client_id: str) -> bool:
        """Check if a client has pending settings."""
        return client_id in self._pending_settings and len(self._pending_settings[client_id]) > 0

    def clear_pending_settings(self, client_id: str) -> None:
        """Clear pending settings for a client."""
        if client_id in self._pending_settings:
            del self._pending_settings[client_id]
            self.logger.info(f"Cleared pending settings for client {client_id}")

    # === Retry Tasks ===

    def _create_retry_task(self, client_id: str, coro) -> Optional[asyncio.Task]:
        """Create a background retry task, cancelling any existing one for this client."""
        existing = self._retry_tasks.pop(client_id, None)
        if existing and not existing.done():
            existing.cancel()
        task = self._bg.spawn(coro, label=f"retry_{client_id}")
        if task is not None:
            self._retry_tasks[client_id] = task
        return task

    @handle_errors(default=None, level='debug')
    async def _delayed_retry_pending(
        self, client_id: str, max_retries: int = 3, retry_delay: float = 5.0
    ) -> None:
        """Retry pending crossover settings after a delay.

        Called when zone recalculation at CLIENT_CONNECTED time fails
        (e.g. CamillaDSP not ready after audio card change). The failed
        settings are already re-queued as pending by _proxy_filter_to_client.
        """
        for attempt in range(max_retries):
            await asyncio.sleep(retry_delay)

            if not self.has_pending_settings(client_id):
                return  # Applied by another path (e.g. new CLIENT_CONNECTED)

            self.logger.info(
                f"Retrying pending crossover settings for {client_id} "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            await self.apply_pending_settings(client_id)

        if self.has_pending_settings(client_id):
            self.logger.warning(
                f"Pending crossover settings for {client_id} still not applied "
                f"after {max_retries} retries"
            )

    # === Cleanup ===

    async def cleanup(self) -> None:
        """Clean up resources."""
        await self._bg.cancel_all()
        self._retry_tasks.clear()

        self._pending_settings.clear()
        self.logger.info("CrossoverService cleanup complete")
