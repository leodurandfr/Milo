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
from typing import Dict, List, Any, Optional, TYPE_CHECKING

import aiohttp

from backend.core.events import EventBus, get_event_bus
from backend.config.constants import CLIENT_API_PORT as _CLIENT_API_PORT
from backend.core.multiroom.models import (
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
)

if TYPE_CHECKING:
    from backend.core.multiroom.registry import ClientRegistryService


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

    def __init__(self, settings_service=None, dsp_service=None, event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.dsp_service = dsp_service
        self.event_bus = event_bus or get_event_bus()

        # State machine reference (set by container)
        self.state_machine = None

        # Client registry reference (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        # Pending settings queue for offline clients
        self._pending_settings: Dict[str, Dict[str, Any]] = {}

    def set_state_machine(self, state_machine) -> None:
        """Set reference to UnifiedAudioStateMachine for event broadcasting."""
        self.state_machine = state_machine

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
                        await self._set_client_crossover(client_id, False, self.DEFAULT_CROSSOVER_FREQUENCY)
                        await self._set_client_lowpass(client_id, False, self.DEFAULT_CROSSOVER_FREQUENCY)
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
                    await self._set_client_crossover(mac_id, False, self.DEFAULT_CROSSOVER_FREQUENCY)
                    await self._set_client_lowpass(mac_id, False, self.DEFAULT_CROSSOVER_FREQUENCY)
                if zone_id and isinstance(zone_id, str):
                    await self.apply_zone_crossover(zone_id)
            except Exception as e:
                self.logger.error(f"Error handling client {mac_id} removal from zone {zone_id}: {e}")

    async def initialize(self) -> bool:
        """Initialize the crossover service."""
        try:
            self.logger.info("Initializing CrossoverService...")
            self.logger.info("CrossoverService initialized (using ClientRegistryService for speaker types)")
            return True

        except Exception as e:
            self.logger.error(f"Error initializing CrossoverService: {e}")
            return False

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

    async def set_client_speaker_type(
        self,
        client_id: str,
        speaker_type: str,
        crossover_frequency: Optional[float] = None
    ) -> bool:
        """Set the speaker type for a client."""
        try:
            if speaker_type not in SPEAKER_TYPES:
                self.logger.error(f"Invalid speaker type: {speaker_type}")
                return False

            if crossover_frequency is None:
                crossover_frequency = DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)

            if self._registry:
                await self._registry.update_speaker_type(
                    client_id,
                    speaker_type,
                    int(crossover_frequency) if crossover_frequency else None
                )

            self.logger.info(
                f"Client {client_id} speaker type set to '{speaker_type}' "
                f"with crossover {crossover_frequency}Hz"
            )

            if crossover_frequency is not None and speaker_type != 'subwoofer':
                await self._set_client_crossover(client_id, True, crossover_frequency)
            else:
                await self._set_client_crossover(client_id, False, 80)

            await self._broadcast_event("client_type_changed", {
                "client_id": client_id,
                "speaker_type": speaker_type,
                "crossover_frequency": crossover_frequency
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting client speaker type: {e}")
            return False

    async def set_client_crossover_frequency(self, client_id: str, frequency: float) -> bool:
        """Set a custom crossover frequency for a client."""
        try:
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
                await self._set_client_crossover(client_id, True, frequency)

            await self._broadcast_event("client_crossover_changed", {
                "client_id": client_id,
                "crossover_frequency": frequency
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting client crossover frequency: {e}")
            return False

    def get_client_speaker_type(self, client_id: str) -> str:
        """Get the speaker type for a client."""
        if self._registry:
            client = self._registry.get_client(client_id)
            if client:
                return client.speaker_type
        return DEFAULT_SPEAKER_TYPE

    def get_client_crossover_frequency(self, client_id: str) -> Optional[float]:
        """Get the crossover frequency for a client."""
        if self._registry:
            client = self._registry.get_client(client_id)
            if client:
                return client.crossover_frequency

        speaker_type = self.get_client_speaker_type(client_id)
        return DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)

    def is_client_subwoofer(self, client_id: str) -> bool:
        """Check if a client is marked as a subwoofer."""
        return self.get_client_speaker_type(client_id) == "subwoofer"

    async def get_all_client_types(self) -> Dict[str, Dict[str, Any]]:
        """Get all client type configurations."""
        if self._registry:
            return {
                mac_id: {
                    "speaker_type": client.speaker_type,
                    "crossover_frequency": client.crossover_frequency
                }
                for mac_id, client in self._registry.get_all_clients().items()
            }
        return {}

    # === Zone Crossover Management ===

    async def get_zone_crossover(self, zone_id: str) -> Dict[str, Any]:
        """Get crossover settings for a zone."""
        if not self._registry:
            return {
                "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                "enabled": False,
                "has_subwoofer": False
            }

        try:
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

        except Exception as e:
            self.logger.error(f"Error getting zone crossover: {e}")
            return {
                "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                "enabled": False,
                "has_subwoofer": False
            }

    async def get_zone_auto_crossover(self, zone_id: str) -> int:
        """Calculate automatic crossover frequency for a zone."""
        if not self._registry:
            return self.DEFAULT_CROSSOVER_FREQUENCY

        try:
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

        except Exception as e:
            self.logger.error(f"Error getting zone auto crossover: {e}")
            return self.DEFAULT_CROSSOVER_FREQUENCY

    async def set_zone_crossover_frequency(self, zone_id: str, frequency: float) -> bool:
        """Set the crossover frequency for a zone."""
        if not self._registry:
            return False

        try:
            frequency = max(20, min(200, frequency))

            zone = self._registry.get_zone(zone_id)
            if not zone:
                self.logger.warning(f"Zone {zone_id} not found")
                return False

            await self._registry.update_zone(zone_id, crossover_frequency=int(frequency))

            self.logger.info(f"Zone {zone_id} crossover frequency set to {frequency} Hz")

            # Get updated crossover state for complete event data (AC4)
            crossover_state = await self.get_zone_crossover(zone_id)

            await self._broadcast_event("zone_crossover_changed", {
                "zone_id": zone_id,
                "crossover_enabled": crossover_state["enabled"],
                "crossover_frequency": int(frequency),
                # Backward compatibility
                "frequency": frequency
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting zone crossover frequency: {e}")
            return False

    async def apply_zone_crossover(self, zone_id: str) -> bool:
        """Apply crossover settings to all clients in a zone."""
        if not self._registry:
            return False

        try:
            zone = self._registry.get_zone(zone_id)
            if not zone:
                self.logger.warning(f"Zone {zone_id} not found")
                return False

            client_ids = zone.client_ids
            frequency = await self.get_zone_auto_crossover(zone_id)

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
                        await self._set_client_lowpass(client_id, True, frequency)
                        await self._set_client_crossover(client_id, False, frequency)
                    else:
                        await self._set_client_crossover(client_id, True, frequency)
                        await self._set_client_lowpass(client_id, False, frequency)
                else:
                    await self._set_client_crossover(client_id, False, frequency)
                    await self._set_client_lowpass(client_id, False, frequency)

            return True

        except Exception as e:
            self.logger.error(f"Error applying zone crossover: {e}")
            return False

    async def _set_client_crossover(
        self,
        client_id: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """Apply or remove crossover filter on a specific client."""
        try:
            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    return await self.dsp_service.set_crossover_filter(
                        enabled=enabled,
                        frequency=frequency,
                        q=self.DEFAULT_Q
                    )
                return False
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.error(f"Cannot proxy crossover: client {client_id} has no IP address")
                    return False
                return await self._proxy_crossover_to_client(
                    client.ip, enabled, frequency, client_id=client_id
                )

        except Exception as e:
            self.logger.error(f"Error setting crossover for client {client_id}: {e}")
            return False

    async def _set_client_lowpass(
        self,
        client_id: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """Apply or remove lowpass filter on a specific client (subwoofer)."""
        try:
            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    return await self.dsp_service.set_lowpass_filter(
                        enabled=enabled,
                        frequency=frequency,
                        q=self.DEFAULT_Q
                    )
                return False
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.error(f"Cannot proxy lowpass: client {client_id} has no IP address")
                    return False
                return await self._proxy_lowpass_to_client(
                    client.ip, enabled, frequency, client_id=client_id
                )

        except Exception as e:
            self.logger.error(f"Error setting lowpass for client {client_id}: {e}")
            return False

    async def _proxy_crossover_to_client(
        self,
        ip_address: str,
        enabled: bool,
        frequency: float,
        client_id: str = None
    ) -> bool:
        """Proxy crossover settings to a remote milo-client.

        Args:
            ip_address: The client's IP address for HTTP requests
            enabled: Whether crossover is enabled
            frequency: Crossover frequency in Hz
            client_id: MAC address for logging and queue_pending_settings (optional)
        """
        identifier = client_id or ip_address
        try:
            url = f"http://{ip_address}:{self.CLIENT_API_PORT}/dsp/crossover"

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
                            f"Crossover {'enabled' if enabled else 'disabled'} on client {identifier} "
                            f"at {frequency} Hz"
                        )
                        return True
                    else:
                        self.logger.error(
                            f"Failed to set crossover on client {identifier}: HTTP {response.status}"
                        )
                        return False

        except aiohttp.ClientError as e:
            self.logger.warning(f"Cannot reach client {identifier} for crossover update: {url}")
            await self.queue_pending_settings(identifier, "crossover", {
                "enabled": enabled,
                "frequency": frequency
            })
            return False
        except Exception as e:
            self.logger.error(f"Error proxying crossover to client {identifier}: {e}")
            return False

    async def _proxy_lowpass_to_client(
        self,
        ip_address: str,
        enabled: bool,
        frequency: float,
        client_id: str = None
    ) -> bool:
        """Proxy lowpass settings to a remote milo-client (subwoofer).

        Args:
            ip_address: The client's IP address for HTTP requests
            enabled: Whether lowpass is enabled
            frequency: Lowpass frequency in Hz
            client_id: MAC address for logging and queue_pending_settings (optional)
        """
        identifier = client_id or ip_address
        try:
            url = f"http://{ip_address}:{self.CLIENT_API_PORT}/dsp/lowpass"

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
                            f"Lowpass {'enabled' if enabled else 'disabled'} on client {identifier} "
                            f"at {frequency} Hz"
                        )
                        return True
                    else:
                        self.logger.error(
                            f"Failed to set lowpass on client {identifier}: HTTP {response.status}"
                        )
                        return False

        except aiohttp.ClientError as e:
            self.logger.warning(f"Cannot reach client {identifier} for lowpass update: {url}")
            await self.queue_pending_settings(identifier, "lowpass", {
                "enabled": enabled,
                "frequency": frequency
            })
            return False
        except Exception as e:
            self.logger.error(f"Error proxying lowpass to client {identifier}: {e}")
            return False

    async def _recalculate_zones_for_client(self, client_id: str) -> None:
        """Recalculate crossover for zone containing this client."""
        from backend.core.multiroom.models import RegistryEventType

        try:
            if not self._registry:
                self.logger.warning("Registry not available, cannot recalculate zones")
                return

            zone = self._registry.get_zone_for_client(client_id)
            if zone:
                await self.apply_zone_crossover(zone.id)

                # Broadcast zone update with computed crossover_enabled
                await self._registry._emit_event(
                    RegistryEventType.ZONE_UPDATED,
                    {"zone_id": zone.id, "zone": self._registry.zone_to_enriched_dict(zone)}
                )
        except Exception as e:
            self.logger.error(f"Error recalculating zones for client {client_id}: {e}")

    async def on_zone_changed(self, zone_id: str) -> None:
        """Handle zone composition changes."""
        self.logger.info(f"Zone {zone_id} changed, recalculating crossover...")
        await self.apply_zone_crossover(zone_id)

    # === Event Broadcasting ===

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast crossover event via state machine and EventBus."""
        if self.state_machine:
            await self.state_machine.broadcast_event("multiroom", "crossover_changed", data)

        if self.event_bus:
            await self.event_bus.emit("multiroom.crossover_changed", data)

    # === Pending Settings Queue for Offline Clients ===

    async def queue_pending_settings(self, client_id: str, setting_type: str, settings: Dict[str, Any]) -> None:
        """Queue DSP settings for an offline client."""
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
            result = await self._set_client_crossover(
                client_id,
                crossover.get("enabled", False),
                crossover.get("frequency", self.DEFAULT_CROSSOVER_FREQUENCY)
            )
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending crossover to {client_id}")

        if "lowpass" in pending:
            lowpass = pending["lowpass"]
            result = await self._set_client_lowpass(
                client_id,
                lowpass.get("enabled", False),
                lowpass.get("frequency", self.DEFAULT_CROSSOVER_FREQUENCY)
            )
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending lowpass to {client_id}")

        if "volume" in pending:
            volume_db = pending["volume"].get("volume_db")
            if volume_db is not None and self.state_machine:
                volume_service = getattr(self.state_machine, 'volume_service', None)
                if volume_service:
                    try:
                        await volume_service.set_client_volume_db(client_id, volume_db)
                        self.logger.info(f"Applied pending volume {volume_db} dB to {client_id}")
                    except Exception as e:
                        self.logger.warning(f"Failed to apply pending volume to {client_id}: {e}")
                        success = False

        if "mute" in pending:
            muted = pending["mute"].get("muted", False)
            await self._apply_pending_mute(client_id, muted)

        # Apply EQ filters (zone DSP settings)
        if "filters" in pending:
            filters = pending["filters"]
            for flt in filters:
                filter_id = flt.get("id")
                if not filter_id:
                    continue
                result = await self._apply_pending_filter(client_id, filter_id, flt)
                if not result:
                    success = False
                    self.logger.warning(f"Failed to apply pending filter {filter_id} to {client_id}")

        # Apply compressor settings
        if "compressor" in pending:
            result = await self._apply_pending_compressor(client_id, pending["compressor"])
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending compressor to {client_id}")

        # Apply loudness settings
        if "loudness" in pending:
            result = await self._apply_pending_loudness(client_id, pending["loudness"])
            if not result:
                success = False
                self.logger.warning(f"Failed to apply pending loudness to {client_id}")

        await self._broadcast_event("pending_settings_applied", {
            "client_id": client_id,
            "settings_applied": list(pending.keys())
        })

        return success

    async def _apply_pending_mute(self, client_id: str, muted: bool) -> bool:
        """Apply pending mute settings to a client."""
        try:
            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    await self.dsp_service.set_mute(muted)
                    return True
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.warning(f"Cannot apply pending mute: client {client_id} has no IP address")
                    return False
                url = f"http://{client.ip}:{self.CLIENT_API_PORT}/dsp/mute"

                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(url, json={"muted": muted}) as response:
                        return response.status == 200

        except Exception as e:
            self.logger.warning(f"Failed to apply pending mute to {client_id}: {e}")
            return False

    async def _apply_pending_filter(self, client_id: str, filter_id: str, filter_data: Dict[str, Any]) -> bool:
        """Apply pending EQ filter settings to a client."""
        try:
            data = {
                "freq": filter_data.get("freq"),
                "gain": filter_data.get("gain"),
                "q": filter_data.get("q"),
                "filter_type": filter_data.get("type")
            }

            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    await self.dsp_service.set_filter(filter_id, **data)
                    return True
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.warning(f"Cannot apply pending filter: client {client_id} has no IP address")
                    return False
                url = f"http://{client.ip}:{self.CLIENT_API_PORT}/dsp/filter/{filter_id}"

                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(url, json=data) as response:
                        return response.status == 200

        except Exception as e:
            self.logger.warning(f"Failed to apply pending filter {filter_id} to {client_id}: {e}")
            return False

    async def _apply_pending_compressor(self, client_id: str, settings: Dict[str, Any]) -> bool:
        """Apply pending compressor settings to a client."""
        try:
            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    await self.dsp_service.set_compressor(**settings)
                    return True
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.warning(f"Cannot apply pending compressor: client {client_id} has no IP address")
                    return False
                url = f"http://{client.ip}:{self.CLIENT_API_PORT}/dsp/compressor"

                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(url, json=settings) as response:
                        return response.status == 200

        except Exception as e:
            self.logger.warning(f"Failed to apply pending compressor to {client_id}: {e}")
            return False

    async def _apply_pending_loudness(self, client_id: str, settings: Dict[str, Any]) -> bool:
        """Apply pending loudness settings to a client."""
        try:
            # Check if this is the local client via registry lookup
            client = self._registry.get_client(client_id) if self._registry else None
            is_local = (client.ip == "127.0.0.1") if client else False

            if is_local:
                if self.dsp_service:
                    await self.dsp_service.set_loudness(**settings)
                    return True
            else:
                # Use client IP for remote requests
                if not client or not client.ip:
                    self.logger.warning(f"Cannot apply pending loudness: client {client_id} has no IP address")
                    return False
                url = f"http://{client.ip}:{self.CLIENT_API_PORT}/dsp/loudness"

                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(url, json=settings) as response:
                        return response.status == 200

        except Exception as e:
            self.logger.warning(f"Failed to apply pending loudness to {client_id}: {e}")
            return False

    def has_pending_settings(self, client_id: str) -> bool:
        """Check if a client has pending settings."""
        return client_id in self._pending_settings and len(self._pending_settings[client_id]) > 0

    def get_pending_settings(self, client_id: str) -> Dict[str, Any]:
        """Get pending settings for a client (for debugging)."""
        return self._pending_settings.get(client_id, {}).copy()

    def clear_pending_settings(self, client_id: str) -> None:
        """Clear pending settings for a client."""
        if client_id in self._pending_settings:
            del self._pending_settings[client_id]
            self.logger.info(f"Cleared pending settings for client {client_id}")

    # === Cleanup ===

    async def cleanup(self) -> None:
        """Clean up resources."""
        self._pending_settings.clear()
        self.logger.info("CrossoverService cleanup complete")
