# backend/infrastructure/services/crossover_service.py
"""
Crossover service for speaker type management in Milo multiroom audio.

Manages automatic highpass filter application to speakers when a subwoofer
is present in a zone. The subwoofer receives the full signal while speakers
get a highpass filter to remove bass (handled by the subwoofer).
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional, Literal

import aiohttp

# Valid speaker types
SPEAKER_TYPES = ['satellite', 'bookshelf', 'tower', 'subwoofer']
DEFAULT_SPEAKER_TYPE = 'bookshelf'

# Default crossover frequencies (highpass) per speaker type in Hz
# Based on typical frequency response of each speaker category
DEFAULT_CROSSOVER_FREQUENCIES = {
    'satellite': 120,   # Small speakers, limited bass (~120 Hz)
    'bookshelf': 80,    # Medium speakers (THX standard)
    'tower': 50,        # Full-range speakers (~40-50 Hz response)
    'subwoofer': None   # No highpass for subwoofer (receives full signal)
}


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

    DEFAULT_CROSSOVER_FREQUENCY = 80.0  # Hz (THX/Dolby recommended)
    DEFAULT_Q = 0.707  # Butterworth (flattest passband)
    CLIENT_API_PORT = 8001  # Milo-client API port

    def __init__(self, settings_service=None, dsp_service=None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.dsp_service = dsp_service

        # State machine reference (set by container)
        self.state_machine = None

        # Cache for client types: { client_id: { "speaker_type": "bookshelf" } }
        self._client_types: Dict[str, Dict[str, Any]] = {}

        # Pending settings queue for offline clients
        # client_id -> {"crossover": {...}, "volume": {...}, "mute": {...}, ...}
        self._pending_settings: Dict[str, Dict[str, Any]] = {}

    def set_state_machine(self, state_machine) -> None:
        """Sets reference to UnifiedAudioStateMachine for event broadcasting"""
        self.state_machine = state_machine

    async def initialize(self) -> bool:
        """Initialize the crossover service"""
        try:
            self.logger.info("Initializing CrossoverService...")

            # Load client types from settings
            await self._load_client_types()

            self.logger.info("CrossoverService initialized")
            return True

        except Exception as e:
            self.logger.error(f"Error initializing CrossoverService: {e}")
            return False

    # === Client Type Management ===

    async def _load_client_types(self) -> None:
        """Load client types from settings"""
        if not self.settings_service:
            return

        try:
            client_types = await self.settings_service.get_setting("multiroom.client_types")
            if client_types:
                self._client_types = client_types
                self.logger.info(f"Loaded client types for {len(self._client_types)} clients")
        except Exception as e:
            self.logger.error(f"Error loading client types: {e}")

    async def _save_client_types(self) -> None:
        """Save client types to settings"""
        if not self.settings_service:
            return

        try:
            await self.settings_service.set_setting("multiroom.client_types", self._client_types)
        except Exception as e:
            self.logger.error(f"Error saving client types: {e}")

    async def get_client_type(self, client_id: str) -> Dict[str, Any]:
        """
        Get the type configuration for a client.

        Args:
            client_id: The client's DSP ID (IP address or 'local')

        Returns:
            Dict with 'speaker_type' and 'crossover_frequency'
        """
        client_data = self._client_types.get(client_id, {})

        # Migration: convert old is_subwoofer to speaker_type
        if "is_subwoofer" in client_data and "speaker_type" not in client_data:
            speaker_type = "subwoofer" if client_data["is_subwoofer"] else DEFAULT_SPEAKER_TYPE
        else:
            speaker_type = client_data.get("speaker_type", DEFAULT_SPEAKER_TYPE)

        # Get crossover frequency (custom or default for type)
        crossover_freq = client_data.get(
            "crossover_frequency",
            DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)
        )

        return {
            "speaker_type": speaker_type,
            "crossover_frequency": crossover_freq
        }

    async def set_client_speaker_type(
        self,
        client_id: str,
        speaker_type: str,
        crossover_frequency: Optional[float] = None
    ) -> bool:
        """
        Set the speaker type for a client.

        Automatically applies the appropriate highpass filter based on speaker type.

        Args:
            client_id: The client's DSP ID
            speaker_type: One of 'satellite', 'bookshelf', 'tower', 'subwoofer'
            crossover_frequency: Optional custom frequency (uses default if not provided)

        Returns:
            True if successful
        """
        try:
            # Validate speaker type
            if speaker_type not in SPEAKER_TYPES:
                self.logger.error(f"Invalid speaker type: {speaker_type}")
                return False

            # Use custom frequency or default for this type
            if crossover_frequency is None:
                crossover_frequency = DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)

            # Store client type configuration
            self._client_types[client_id] = {
                "speaker_type": speaker_type,
                "crossover_frequency": crossover_frequency
            }
            await self._save_client_types()

            self.logger.info(
                f"Client {client_id} speaker type set to '{speaker_type}' "
                f"with crossover {crossover_frequency}Hz"
            )

            # Apply crossover filter to the client
            if crossover_frequency is not None and speaker_type != 'subwoofer':
                await self._set_client_crossover(client_id, True, crossover_frequency)
            else:
                # Subwoofer or no crossover - disable highpass
                await self._set_client_crossover(client_id, False, 80)

            # Broadcast event
            await self._broadcast_event("client_type_changed", {
                "client_id": client_id,
                "speaker_type": speaker_type,
                "crossover_frequency": crossover_frequency
            })

            # Check if client is in a zone and recalculate crossover
            await self._recalculate_zones_for_client(client_id)

            return True

        except Exception as e:
            self.logger.error(f"Error setting client speaker type: {e}")
            return False

    async def set_client_crossover_frequency(self, client_id: str, frequency: float) -> bool:
        """
        Set a custom crossover frequency for a client.

        Args:
            client_id: The client's DSP ID
            frequency: Crossover frequency in Hz (20-200)

        Returns:
            True if successful
        """
        try:
            # Validate frequency
            frequency = max(20, min(200, frequency))

            # Get current speaker type
            client_data = self._client_types.get(client_id, {})
            speaker_type = client_data.get("speaker_type", DEFAULT_SPEAKER_TYPE)

            # Update with new frequency
            self._client_types[client_id] = {
                "speaker_type": speaker_type,
                "crossover_frequency": frequency
            }
            await self._save_client_types()

            self.logger.info(f"Client {client_id} crossover frequency set to {frequency}Hz")

            # Apply crossover if not subwoofer
            if speaker_type != 'subwoofer':
                await self._set_client_crossover(client_id, True, frequency)

            # Broadcast event
            await self._broadcast_event("client_crossover_changed", {
                "client_id": client_id,
                "crossover_frequency": frequency
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting client crossover frequency: {e}")
            return False

    def get_client_speaker_type(self, client_id: str) -> str:
        """Get the speaker type for a client"""
        client_data = self._client_types.get(client_id, {})
        # Migration: convert old is_subwoofer to speaker_type
        if "is_subwoofer" in client_data and "speaker_type" not in client_data:
            return "subwoofer" if client_data["is_subwoofer"] else DEFAULT_SPEAKER_TYPE
        return client_data.get("speaker_type", DEFAULT_SPEAKER_TYPE)

    def get_client_crossover_frequency(self, client_id: str) -> Optional[float]:
        """Get the crossover frequency for a client"""
        client_data = self._client_types.get(client_id, {})
        speaker_type = self.get_client_speaker_type(client_id)
        return client_data.get(
            "crossover_frequency",
            DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)
        )

    def is_client_subwoofer(self, client_id: str) -> bool:
        """Check if a client is marked as a subwoofer (derived from speaker_type)"""
        return self.get_client_speaker_type(client_id) == "subwoofer"

    async def get_all_client_types(self) -> Dict[str, Dict[str, Any]]:
        """Get all client type configurations"""
        return self._client_types.copy()

    # === Zone Crossover Management ===

    async def get_zone_crossover(self, zone_id: str) -> Dict[str, Any]:
        """
        Get crossover settings for a zone.

        Args:
            zone_id: The zone/linked group ID

        Returns:
            Dict with 'frequency', 'enabled', 'has_subwoofer'
        """
        if not self.settings_service:
            return {
                "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                "enabled": False,
                "has_subwoofer": False
            }

        try:
            # Get zone from linked_groups
            linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []

            zone = next((g for g in linked_groups if g.get("id") == zone_id), None)
            if not zone:
                return {
                    "frequency": self.DEFAULT_CROSSOVER_FREQUENCY,
                    "enabled": False,
                    "has_subwoofer": False
                }

            # Check if zone has a subwoofer
            client_ids = zone.get("client_ids", [])
            has_subwoofer = any(self.is_client_subwoofer(cid) for cid in client_ids)

            return {
                "frequency": zone.get("crossover_frequency", self.DEFAULT_CROSSOVER_FREQUENCY),
                "enabled": zone.get("crossover_enabled", has_subwoofer),  # Auto-enable if sub present
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
        """
        Calculate automatic crossover frequency for a zone.
        Returns MIN of all non-subwoofer speaker frequencies in the zone.

        Args:
            zone_id: The zone/linked group ID

        Returns:
            Crossover frequency in Hz (MIN of speakers, or 80 if no speakers)
        """
        if not self.settings_service:
            return self.DEFAULT_CROSSOVER_FREQUENCY

        try:
            # Get zone from linked_groups
            linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []
            zone = next((g for g in linked_groups if g.get("id") == zone_id), None)

            if not zone:
                return self.DEFAULT_CROSSOVER_FREQUENCY

            # Collect crossover frequencies from non-subwoofer speakers
            frequencies = []
            for client_id in zone.get("client_ids", []):
                speaker_type = self.get_client_speaker_type(client_id)
                if speaker_type != "subwoofer":
                    freq = DEFAULT_CROSSOVER_FREQUENCIES.get(speaker_type)
                    if freq:
                        frequencies.append(freq)

            # Return MIN of frequencies, or default if no speakers found
            return min(frequencies) if frequencies else self.DEFAULT_CROSSOVER_FREQUENCY

        except Exception as e:
            self.logger.error(f"Error getting zone auto crossover: {e}")
            return self.DEFAULT_CROSSOVER_FREQUENCY

    async def set_zone_crossover_frequency(self, zone_id: str, frequency: float) -> bool:
        """
        Set the crossover frequency for a zone.

        Args:
            zone_id: The zone/linked group ID
            frequency: Crossover frequency in Hz (40-200)

        Returns:
            True if successful
        """
        if not self.settings_service:
            return False

        try:
            # Validate frequency
            frequency = max(40, min(200, frequency))

            # Get linked_groups
            linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []

            # Find and update the zone
            zone_found = False
            for group in linked_groups:
                if group.get("id") == zone_id:
                    group["crossover_frequency"] = frequency
                    zone_found = True
                    break

            if not zone_found:
                self.logger.warning(f"Zone {zone_id} not found")
                return False

            # Save updated linked_groups
            await self.settings_service.set_setting("dsp.linked_groups", linked_groups)

            self.logger.info(f"Zone {zone_id} crossover frequency set to {frequency} Hz")

            # Apply crossover to zone
            await self.apply_zone_crossover(zone_id)

            # Broadcast event
            await self._broadcast_event("zone_crossover_changed", {
                "zone_id": zone_id,
                "frequency": frequency
            })

            return True

        except Exception as e:
            self.logger.error(f"Error setting zone crossover frequency: {e}")
            return False

    async def apply_zone_crossover(self, zone_id: str) -> bool:
        """
        Apply crossover settings to all clients in a zone.

        If the zone contains a subwoofer:
        - Non-subwoofer clients get a highpass filter at the crossover frequency
        - Subwoofer clients get no filter (full signal)

        If no subwoofer in zone:
        - All clients get no crossover filter

        Args:
            zone_id: The zone/linked group ID

        Returns:
            True if successful
        """
        if not self.settings_service:
            return False

        try:
            # Get zone configuration
            linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []
            zone = next((g for g in linked_groups if g.get("id") == zone_id), None)

            if not zone:
                self.logger.warning(f"Zone {zone_id} not found")
                return False

            client_ids = zone.get("client_ids", [])
            frequency = zone.get("crossover_frequency", self.DEFAULT_CROSSOVER_FREQUENCY)
            crossover_enabled = zone.get("crossover_enabled", True)

            # Check if zone has a subwoofer
            has_subwoofer = any(self.is_client_subwoofer(cid) for cid in client_ids)

            # Determine if crossover should be active
            should_apply_crossover = has_subwoofer and crossover_enabled

            self.logger.info(
                f"Applying crossover to zone {zone_id}: "
                f"has_sub={has_subwoofer}, enabled={crossover_enabled}, freq={frequency}Hz"
            )

            # Apply to each client
            for client_id in client_ids:
                is_sub = self.is_client_subwoofer(client_id)

                if should_apply_crossover:
                    if is_sub:
                        # Subwoofer: lowpass only (bass), no highpass
                        await self._set_client_lowpass(client_id, True, frequency)
                        await self._set_client_crossover(client_id, False, frequency)
                    else:
                        # Speakers: highpass only (no bass), no lowpass
                        await self._set_client_crossover(client_id, True, frequency)
                        await self._set_client_lowpass(client_id, False, frequency)
                else:
                    # No crossover needed - disable both filters
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
        """
        Apply or remove crossover filter on a specific client.

        Args:
            client_id: The client's DSP ID ('local' or IP address)
            enabled: Whether to enable the highpass filter
            frequency: Crossover frequency in Hz

        Returns:
            True if successful
        """
        try:
            if client_id == "local":
                # Apply to local CamillaDSP
                if self.dsp_service:
                    return await self.dsp_service.set_crossover_filter(
                        enabled=enabled,
                        frequency=frequency,
                        q=self.DEFAULT_Q
                    )
                return False
            else:
                # Apply to remote client via HTTP
                return await self._proxy_crossover_to_client(client_id, enabled, frequency)

        except Exception as e:
            self.logger.error(f"Error setting crossover for client {client_id}: {e}")
            return False

    async def _set_client_lowpass(
        self,
        client_id: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """
        Apply or remove lowpass filter on a specific client (subwoofer).

        Args:
            client_id: The client's DSP ID ('local' or IP address)
            enabled: Whether to enable the lowpass filter
            frequency: Cutoff frequency in Hz

        Returns:
            True if successful
        """
        try:
            if client_id == "local":
                # Apply to local CamillaDSP
                if self.dsp_service:
                    return await self.dsp_service.set_lowpass_filter(
                        enabled=enabled,
                        frequency=frequency,
                        q=self.DEFAULT_Q
                    )
                return False
            else:
                # Apply to remote client via HTTP
                return await self._proxy_lowpass_to_client(client_id, enabled, frequency)

        except Exception as e:
            self.logger.error(f"Error setting lowpass for client {client_id}: {e}")
            return False

    async def _proxy_crossover_to_client(
        self,
        hostname: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """
        Proxy crossover settings to a remote milo-client.

        Args:
            hostname: Client hostname or IP
            enabled: Whether to enable the highpass filter
            frequency: Crossover frequency in Hz

        Returns:
            True if successful
        """
        try:
            # Build URL (add .local suffix for hostnames, not for IPs)
            if self._is_ip_address(hostname):
                url = f"http://{hostname}:{self.CLIENT_API_PORT}/dsp/crossover"
            else:
                url = f"http://{hostname}.local:{self.CLIENT_API_PORT}/dsp/crossover"

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
                            f"Crossover {'enabled' if enabled else 'disabled'} on client {hostname} "
                            f"at {frequency} Hz"
                        )
                        return True
                    else:
                        self.logger.error(
                            f"Failed to set crossover on client {hostname}: HTTP {response.status}"
                        )
                        return False

        except aiohttp.ClientError as e:
            # Client is offline - queue the settings for when it reconnects
            self.logger.warning(f"Cannot reach client {hostname} for crossover update: {e}")
            await self.queue_pending_settings(hostname, "crossover", {
                "enabled": enabled,
                "frequency": frequency
            })
            return False
        except Exception as e:
            self.logger.error(f"Error proxying crossover to client {hostname}: {e}")
            return False

    async def _proxy_lowpass_to_client(
        self,
        hostname: str,
        enabled: bool,
        frequency: float
    ) -> bool:
        """
        Proxy lowpass settings to a remote milo-client (subwoofer).

        Args:
            hostname: Client hostname or IP
            enabled: Whether to enable the lowpass filter
            frequency: Cutoff frequency in Hz

        Returns:
            True if successful
        """
        try:
            # Build URL (add .local suffix for hostnames, not for IPs)
            if self._is_ip_address(hostname):
                url = f"http://{hostname}:{self.CLIENT_API_PORT}/dsp/lowpass"
            else:
                url = f"http://{hostname}.local:{self.CLIENT_API_PORT}/dsp/lowpass"

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
                            f"Lowpass {'enabled' if enabled else 'disabled'} on client {hostname} "
                            f"at {frequency} Hz"
                        )
                        return True
                    else:
                        self.logger.error(
                            f"Failed to set lowpass on client {hostname}: HTTP {response.status}"
                        )
                        return False

        except aiohttp.ClientError as e:
            # Client is offline - queue the settings for when it reconnects
            self.logger.warning(f"Cannot reach client {hostname} for lowpass update: {e}")
            await self.queue_pending_settings(hostname, "lowpass", {
                "enabled": enabled,
                "frequency": frequency
            })
            return False
        except Exception as e:
            self.logger.error(f"Error proxying lowpass to client {hostname}: {e}")
            return False

    def _is_ip_address(self, hostname: str) -> bool:
        """Check if hostname is an IP address"""
        import ipaddress
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    async def _recalculate_zones_for_client(self, client_id: str) -> None:
        """
        Recalculate crossover for all zones containing a client.

        Called when a client's subwoofer status changes.
        """
        if not self.settings_service:
            return

        try:
            linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []

            for group in linked_groups:
                if client_id in group.get("client_ids", []):
                    await self.apply_zone_crossover(group.get("id"))

        except Exception as e:
            self.logger.error(f"Error recalculating zones for client {client_id}: {e}")

    async def on_zone_changed(self, zone_id: str) -> None:
        """
        Handle zone composition changes.

        Called when clients are added/removed from a zone.
        Automatically applies or removes crossover filters based on zone composition.

        Args:
            zone_id: The zone/linked group ID that changed
        """
        self.logger.info(f"Zone {zone_id} changed, recalculating crossover...")
        await self.apply_zone_crossover(zone_id)

    # === Event Broadcasting ===

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast crossover event via state machine"""
        if self.state_machine:
            await self.state_machine.broadcast_event("crossover", event_type, data)

    # === Pending Settings Queue for Offline Clients ===

    async def queue_pending_settings(self, client_id: str, setting_type: str, settings: Dict[str, Any]) -> None:
        """
        Queue DSP settings for an offline client.

        Settings will be applied when the client reconnects.

        Args:
            client_id: The client's DSP ID
            setting_type: Type of setting ('crossover', 'lowpass', 'volume', 'mute', 'filters', 'compressor', 'loudness', 'delay')
            settings: The settings to apply
        """
        if client_id not in self._pending_settings:
            self._pending_settings[client_id] = {}

        self._pending_settings[client_id][setting_type] = settings
        self.logger.info(f"Queued {setting_type} settings for offline client {client_id}")

    async def apply_pending_settings(self, client_id: str) -> bool:
        """
        Apply all pending settings to a reconnected client.

        Called when a client comes back online.

        Args:
            client_id: The client's DSP ID

        Returns:
            True if settings were applied, False if no pending settings
        """
        if client_id not in self._pending_settings:
            return False

        pending = self._pending_settings.pop(client_id)
        if not pending:
            return False

        self.logger.info(f"Applying pending settings to reconnected client {client_id}: {list(pending.keys())}")

        success = True

        # Apply crossover settings
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

        # Apply lowpass settings (for subwoofers)
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

        # Apply volume settings via state_machine's volume_service
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

        # Apply mute settings
        if "mute" in pending:
            muted = pending["mute"].get("muted", False)
            await self._apply_pending_mute(client_id, muted)

        # Broadcast event
        await self._broadcast_event("pending_settings_applied", {
            "client_id": client_id,
            "settings_applied": list(pending.keys())
        })

        return success

    async def _apply_pending_mute(self, client_id: str, muted: bool) -> bool:
        """Apply pending mute settings to a client"""
        try:
            if client_id == "local":
                if self.dsp_service:
                    await self.dsp_service.set_mute(muted)
                    return True
            else:
                # Apply to remote client via HTTP
                if self._is_ip_address(client_id):
                    url = f"http://{client_id}:{self.CLIENT_API_PORT}/dsp/mute"
                else:
                    url = f"http://{client_id}.local:{self.CLIENT_API_PORT}/dsp/mute"

                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(url, json={"muted": muted}) as response:
                        return response.status == 200

        except Exception as e:
            self.logger.warning(f"Failed to apply pending mute to {client_id}: {e}")
            return False

    def has_pending_settings(self, client_id: str) -> bool:
        """Check if a client has pending settings"""
        return client_id in self._pending_settings and len(self._pending_settings[client_id]) > 0

    def get_pending_settings(self, client_id: str) -> Dict[str, Any]:
        """Get pending settings for a client (for debugging)"""
        return self._pending_settings.get(client_id, {}).copy()

    def clear_pending_settings(self, client_id: str) -> None:
        """Clear pending settings for a client"""
        if client_id in self._pending_settings:
            del self._pending_settings[client_id]
            self.logger.info(f"Cleared pending settings for client {client_id}")

    # === Cleanup ===

    async def cleanup(self) -> None:
        """Clean up resources"""
        self._pending_settings.clear()
        self.logger.info("CrossoverService cleanup complete")
