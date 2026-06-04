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

import aiohttp

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.config.constants import CLIENT_API_PORT as _CLIENT_API_PORT
from backend.core.multiroom.models import (
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
)

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService


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

    DEFAULT_CROSSOVER_FREQUENCY = 80  # Hz (THX/Dolby recommended)
    DEFAULT_Q = 0.707  # Butterworth (flattest passband)
    CLIENT_API_PORT = _CLIENT_API_PORT

    def __init__(self, settings_service=None, camilladsp_service=None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.camilladsp_service = camilladsp_service

        # State machine reference (set by container)
        self.state_machine = None

        # Volume service (set via set_volume_service after construction)
        self.volume_service = None

        # Client registry reference (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        # Pending settings queue for offline clients
        self._pending_settings: Dict[str, Dict[str, Any]] = {}

        # Background retry tasks keyed by client_id — one per client at most.
        # Tracked for deduplication and clean cancellation on shutdown.
        self._retry_tasks: Dict[str, asyncio.Task] = {}
        self._bg = BackgroundTaskSet(self.logger, "crossover")

    def set_state_machine(self, state_machine) -> None:
        """Set reference to UnifiedAudioStateMachine for event broadcasting."""
        self.state_machine = state_machine

    def set_volume_service(self, service) -> None:
        """Set VolumeService dependency."""
        self.volume_service = service

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
                # Client came online - apply pending settings
                if self.has_pending_settings(mac_id):
                    self.logger.info(f"Client {mac_id} reconnected, applying pending settings")
                    await self.apply_pending_settings(mac_id)

                # Recalculate crossover for zones containing this client
                await self._recalculate_zones_for_client(mac_id)

                # If recalculation queued settings (CamillaDSP not fully ready,
                # e.g. after audio card change), schedule a delayed retry
                if self.has_pending_settings(mac_id):
                    self._create_retry_task(mac_id, self._delayed_retry_pending(mac_id))

        elif event_type == RegistryEventType.CLIENT_DISCONNECTED:
            mac_id = data.get("mac_id")
            if mac_id:
                # Recalculate crossover for zones containing this client
                # (offline clients affect crossover_enabled state)
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
                        await self._set_client_filter(client_id, "crossover", False, self.DEFAULT_CROSSOVER_FREQUENCY)
                        await self._set_client_filter(client_id, "lowpass", False, self.DEFAULT_CROSSOVER_FREQUENCY)
                except Exception as e:
                    self.logger.error(f"Error disabling filters after zone {zone_id} deletion: {e}")

        elif event_type == "zone_client_added":
            # Client added to zone - recalculate crossover
            zone_id = data.get("zone_id")
            mac_id = data.get("mac_id")
            if zone_id and isinstance(zone_id, str):
                try:
                    self.logger.info(f"Client {mac_id} added to zone {zone_id}, recalculating crossover")
                    await self.apply_zone_crossover(zone_id)
                except Exception as e:
                    self.logger.error(f"Error recalculating crossover after adding client to zone {zone_id}: {e}")

        elif event_type == "zone_client_removed":
            # Client removed from zone - disable filters and recalculate
            zone_id = data.get("zone_id")
            mac_id = data.get("mac_id")
            try:
                if mac_id:
                    self.logger.info(f"Client {mac_id} removed from zone {zone_id}, disabling filters")
                    await self._set_client_filter(mac_id, "crossover", False, self.DEFAULT_CROSSOVER_FREQUENCY)
                    await self._set_client_filter(mac_id, "lowpass", False, self.DEFAULT_CROSSOVER_FREQUENCY)
                if zone_id and isinstance(zone_id, str):
                    await self.apply_zone_crossover(zone_id)
            except Exception as e:
                self.logger.error(f"Error handling client {mac_id} removal from zone {zone_id}: {e}")

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize the crossover service."""
        self.logger.info("Initializing CrossoverService...")
        self.logger.info("CrossoverService initialized (using ClientRegistryService for speaker types)")
        return True

    # === Client Type Management ===

    async def get_client_type(self, client_id: str) -> Dict[str, Any]:
        """Get the type configuration for a client."""
        if self._registry:
            client = self._registry.get_client(client_id)
            if client:
                return {
                    "speaker_type": client.speaker_type,
                    "crossover_frequency": client.crossover_frequency
                }

        return {
            "speaker_type": DEFAULT_SPEAKER_TYPE,
            "crossover_frequency": DEFAULT_CROSSOVER_FREQUENCIES.get(DEFAULT_SPEAKER_TYPE)
        }

    @handle_errors(default=False)
    async def set_client_crossover_frequency(self, client_id: str, frequency: float) -> bool:
        """Set a custom crossover frequency for a client."""
        frequency = max(20, min(200, frequency))
        speaker_type = self.get_client_speaker_type(client_id)

        if self._registry:
            await self._registry.update_speaker_type(
                client_id,
                speaker_type,
                int(frequency)
            )

        self.logger.info(f"Client {client_id} crossover frequency set to {frequency}Hz")

        if speaker_type != 'subwoofer':
            await self._set_client_filter(client_id, "crossover", True, frequency)

        await self._broadcast_event({
            "client_id": client_id,
            "crossover_frequency": frequency
        })

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

    @handle_errors(default={"frequency": 80, "enabled": False, "has_subwoofer": False})
    async def get_zone_crossover(self, zone_id: str) -> Dict[str, Any]:
        """Get crossover settings for a zone."""
        if not self._registry:
            return {
                "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                "enabled": False,
                "has_subwoofer": False
            }

        zone = self._registry.get_zone(zone_id)
        if not zone:
            return {
                "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                "enabled": False,
                "has_subwoofer": False
            }

        has_subwoofer = any(self.is_client_subwoofer(cid) for cid in zone.client_ids)

        return {
            "frequency": zone.crossover_frequency,
            "enabled": zone.crossover_enabled if zone.crossover_enabled is not None else has_subwoofer,
            "has_subwoofer": has_subwoofer
        }

    @handle_errors(default=80)
    async def get_zone_auto_crossover(self, zone_id: str) -> int:
        """Calculate automatic crossover frequency for a zone."""
        if not self._registry:
            return self.DEFAULT_CROSSOVER_FREQUENCY

        zone = self._registry.get_zone(zone_id)
        if not zone:
            return self.DEFAULT_CROSSOVER_FREQUENCY

        frequencies = []
        for client_id in zone.client_ids:
            speaker_type = self.get_client_speaker_type(client_id)
            if speaker_type != "subwoofer":
                freq = DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)
                if freq:
                    frequencies.append(freq)

        return min(frequencies) if frequencies else self.DEFAULT_CROSSOVER_FREQUENCY

    @handle_errors(default=False)
    async def set_zone_crossover_frequency(self, zone_id: str, frequency: float) -> bool:
        """Set the crossover frequency for a zone."""
        if not self._registry:
            return False

        frequency = max(20, min(200, frequency))

        zone = self._registry.get_zone(zone_id)
        if not zone:
            self.logger.warning(f"Zone {zone_id} not found")
            return False

        await self._registry.update_zone(zone_id, crossover_frequency=int(frequency))

        self.logger.info(f"Zone {zone_id} crossover frequency set to {frequency} Hz")

        # Get updated crossover state for complete event data (AC4)
        crossover_state = await self.get_zone_crossover(zone_id)

        await self._broadcast_event({
            "zone_id": zone_id,
            "crossover_enabled": crossover_state["enabled"],
            "crossover_frequency": int(frequency),
        })

        # Apply the updated crossover filters to all zone clients
        await self.apply_zone_crossover(zone_id)

        return True

    @handle_errors(default=False)
    async def apply_zone_crossover(self, zone_id: str) -> bool:
        """Apply crossover settings to all clients in a zone."""
        if not self._registry:
            return False

        zone = self._registry.get_zone(zone_id)
        if not zone:
            self.logger.warning(f"Zone {zone_id} not found")
            return False

        client_ids = zone.client_ids
        frequency = zone.crossover_frequency or await self.get_zone_auto_crossover(zone_id)

        available_clients = {
            cid for cid in client_ids
            if self._registry.is_client_online(cid)
        }

        has_subwoofer = any(
            self.is_client_subwoofer(cid) and cid in available_clients
            for cid in client_ids
        )

        # Determine if crossover should be applied
        # Auto mode (None): enable when there's an online subwoofer
        # Explicit mode: respect the setting but still require subwoofer
        if zone.crossover_enabled is not None:
            should_apply_crossover = has_subwoofer and zone.crossover_enabled
        else:
            # Auto mode: enable crossover when there's an online subwoofer
            should_apply_crossover = has_subwoofer

        self.logger.info(
            f"Applying crossover to zone {zone_id}: "
            f"has_sub={has_subwoofer}, zone_setting={zone.crossover_enabled}, "
            f"should_apply={should_apply_crossover}, freq={frequency}Hz, "
            f"available_clients={list(available_clients)}"
        )

        for client_id in client_ids:
            if client_id not in available_clients:
                self.logger.debug(f"Skipping unavailable client {client_id}")
                continue

            is_sub = self.is_client_subwoofer(client_id)

            if should_apply_crossover:
                if is_sub:
                    await self._set_client_filter(client_id, "lowpass", True, frequency)
                    await self._set_client_filter(client_id, "crossover", False, frequency)
                else:
                    await self._set_client_filter(client_id, "crossover", True, frequency)
                    await self._set_client_filter(client_id, "lowpass", False, frequency)
            else:
                await self._set_client_filter(client_id, "crossover", False, frequency)
                await self._set_client_filter(client_id, "lowpass", False, frequency)

        return True

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
        try:
            url = f"http://{ip_address}:{self.CLIENT_API_PORT}/equalizer/{filter_name}"

            payload = {
                "enabled": enabled,
                "frequency": frequency,
                "q": self.DEFAULT_Q
            }

            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.put(url, json=payload) as response:
                    if response.status == 200:
                        self.logger.info(
                            f"{filter_name.capitalize()} {'enabled' if enabled else 'disabled'} "
                            f"on client {identifier} at {frequency} Hz"
                        )
                        return True
                    else:
                        # Expected when CamillaDSP is not ready (e.g. after reboot).
                        # Queue as pending so the next reconnect retries.
                        self.logger.debug(
                            f"Client {identifier} rejected {filter_name} "
                            f"(HTTP {response.status}), queued as pending"
                        )
                        await self.queue_pending_settings(identifier, filter_name, {
                            "enabled": enabled,
                            "frequency": frequency
                        })
                        return False

        except aiohttp.ClientError:
            self.logger.debug(
                f"Cannot reach client {identifier} for {filter_name} update, queued as pending"
            )
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
            zone_data = {"zone_id": zone.id, "zone": self._registry.zone_to_enriched_dict(zone)}
            if self.state_machine:
                await self.state_machine.broadcast_event("multiroom", "zone_changed", zone_data)

    # === Event Broadcasting ===

    async def _broadcast_event(self, data: Dict[str, Any]) -> None:
        """Broadcast a crossover event via state machine (WebSocket).

        Canonical payload for 'multiroom.crossover_changed' (consumed by the
        frontend equalizerStore.handleZoneCrossoverChanged):
            {zone_id: str, crossover_enabled: bool, crossover_frequency: int}

        Other call sites in this file also emit this event with different
        shapes for per-client changes (e.g. {client_id, speaker_type,
        crossover_frequency}, {client_id, settings_applied}). These are not
        currently consumed by the frontend — same event type, different shape
        depending on whether zone_id or client_id is keyed. A future RFC may
        split this into distinct event types.
        """
        if self.state_machine:
            await self.state_machine.broadcast_event("multiroom", "crossover_changed", data)

    # === Pending Settings Queue for Offline Clients ===

    async def queue_pending_settings(self, client_id: str, setting_type: str, settings: Dict[str, Any]) -> None:
        """Queue equalizer settings for an offline client."""
        if client_id not in self._pending_settings:
            self._pending_settings[client_id] = {}

        self._pending_settings[client_id][setting_type] = settings
        self.logger.info(f"Queued {setting_type} settings for offline client {client_id}")

    async def apply_pending_settings(self, client_id: str) -> bool:
        """Apply all pending settings to a reconnected client."""
        if client_id not in self._pending_settings:
            return False

        pending = self._pending_settings.pop(client_id)
        if not pending:
            return False

        self.logger.info(f"Applying pending settings to reconnected client {client_id}: {list(pending.keys())}")

        success = True

        if "crossover" in pending:
            crossover = pending["crossover"]
            result = await self._set_client_filter(
                client_id, "crossover",
                crossover.get("enabled", False),
                crossover.get("frequency", self.DEFAULT_CROSSOVER_FREQUENCY)
            )
            if not result:
                success = False
                self.logger.debug(f"Failed to apply pending crossover to {client_id} (zone recalculation will re-apply)")

        if "lowpass" in pending:
            lowpass = pending["lowpass"]
            result = await self._set_client_filter(
                client_id, "lowpass",
                lowpass.get("enabled", False),
                lowpass.get("frequency", self.DEFAULT_CROSSOVER_FREQUENCY)
            )
            if not result:
                success = False
                self.logger.debug(f"Failed to apply pending lowpass to {client_id} (zone recalculation will re-apply)")

        if "volume" in pending:
            volume_db = pending["volume"].get("volume_db")
            if volume_db is not None and self.volume_service:
                try:
                    await self.volume_service.set_client_volume_db(client_id, volume_db)
                    self.logger.info(f"Applied pending volume {volume_db} dB to {client_id}")
                except Exception as e:
                    self.logger.warning(f"Failed to apply pending volume to {client_id}: {e}")
                    success = False

        if "mute" in pending:
            muted = pending["mute"].get("muted", False)
            await self._dispatch_to_client(
                client_id, "/equalizer/mute", {"muted": muted},
                lambda: self.camilladsp_service.set_mute(muted), "mute"
            )

        if "filters" in pending:
            for flt in pending["filters"]:
                filter_id = flt.get("id")
                if not filter_id:
                    continue
                data = {
                    "freq": flt.get("freq"),
                    "gain": flt.get("gain"),
                    "q": flt.get("q"),
                    "filter_type": flt.get("type")
                }
                result = await self._dispatch_to_client(
                    client_id, f"/equalizer/filter/{filter_id}", data,
                    lambda fid=filter_id, d=data: self.camilladsp_service.set_filter(fid, **d),
                    f"filter {filter_id}"
                )
                if not result:
                    success = False
                    self.logger.warning(f"Failed to apply pending filter {filter_id} to {client_id}")

        if "compressor" in pending:
            compressor_data = pending["compressor"]
            result = await self._dispatch_to_client(
                client_id, "/equalizer/compressor", compressor_data,
                lambda: self.camilladsp_service.set_compressor(**compressor_data),
                "compressor"
            )
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending compressor to {client_id}")

        if "loudness" in pending:
            loudness_data = pending["loudness"]
            result = await self._dispatch_to_client(
                client_id, "/equalizer/loudness", loudness_data,
                lambda: self.camilladsp_service.set_loudness(**loudness_data),
                "loudness"
            )
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending loudness to {client_id}")

        await self._broadcast_event({
            "client_id": client_id,
            "settings_applied": list(pending.keys())
        })

        return success

    @handle_errors(default=False, level='warning')
    async def _dispatch_to_client(
        self,
        client_id: str,
        endpoint: str,
        payload: Dict[str, Any],
        local_action,
        label: str
    ) -> bool:
        """Dispatch a pending setting to a client (local CamillaDSP or remote HTTP PUT).

        Args:
            client_id: Client MAC address
            endpoint: URL path suffix (e.g. "/equalizer/mute")
            payload: JSON payload for remote PUT request
            local_action: Callable returning a coroutine for local execution
            label: Human-readable label for log messages
        """
        client = self._registry.get_client(client_id) if self._registry else None
        is_local = client.is_local if client else False

        if is_local:
            if self.camilladsp_service:
                await local_action()
                return True
            return False
        else:
            if not client or not client.ip:
                self.logger.warning(f"Cannot apply pending {label}: client {client_id} has no IP address")
                return False
            url = f"http://{client.ip}:{self.CLIENT_API_PORT}{endpoint}"

            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.put(url, json=payload) as response:
                    return response.status == 200

    def has_pending_settings(self, client_id: str) -> bool:
        """Check if a client has pending settings."""
        return client_id in self._pending_settings and len(self._pending_settings[client_id]) > 0

    def get_pending_settings(self, client_id: str) -> Dict[str, Any]:
        """Read a client's queued pending settings (inspection accessor)."""
        return self._pending_settings.get(client_id, {}).copy()

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
