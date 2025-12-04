# backend/infrastructure/services/multiroom_volume_handler.py
"""
Multiroom mode volume handler - Snapcast client orchestration.
Handles volume operations when multiroom is enabled.
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Dict, List, Any, Optional

if TYPE_CHECKING:
    from backend.infrastructure.services.volume_converter_service import VolumeConverterService


class MultiroomVolumeHandler:
    """Handles volume control in multiroom mode (Snapcast clients)."""

    SNAPCAST_CACHE_MS = 50

    def __init__(
        self,
        converter: "VolumeConverterService",
        snapcast_service,
        state_machine,
    ):
        self.converter = converter
        self.snapcast_service = snapcast_service
        self.state_machine = state_machine
        self.logger = logging.getLogger(__name__)

        self._multiroom_volume = 50.0
        self._client_display_states: Dict[str, float] = {}
        self._client_states_initialized = False

        self._snapcast_clients_cache: List[Dict] = []
        self._snapcast_cache_time = 0

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    def get_display_volume(self) -> int:
        """Get current volume (average of all clients, 0-100%)."""
        from backend.infrastructure.services.volume_converter_service import VolumeConverterService
        return VolumeConverterService.round_half_up(self._multiroom_volume)

    async def set_volume(self, display_volume: float) -> bool:
        """Set volume to specific level on all clients."""
        delta = display_volume - self._multiroom_volume
        return await self._apply_delta(delta, fallback_volume=display_volume)

    async def adjust_volume(self, delta: int) -> bool:
        """Adjust volume by delta on all clients."""
        return await self._apply_delta(float(delta))

    async def set_startup_volume(self, alsa_volume: int) -> bool:
        """Set startup volume on all clients."""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return True

            display = self.converter.alsa_to_display(alsa_volume)
            operations = []

            for client in clients:
                client_id = client["id"]
                operations.append((client_id, alsa_volume))
                self._set_client_display_volume(client_id, float(display))

            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )

            self._multiroom_volume = float(display)
            self._client_states_initialized = True
            self.logger.info(f"Multiroom startup: {display}%")
            return True
        except Exception as e:
            self.logger.error(f"Error setting startup volume: {e}")
            return False

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT
    # ============================================================================

    async def initialize_new_client_volume(
        self,
        client_id: str,
        client_alsa_volume: int,
        startup_volume_getter,
    ) -> bool:
        """Initialize new client and apply current volume."""
        try:
            if client_id in self._client_display_states:
                await self.sync_client_volume_from_external(client_id, client_alsa_volume)
                return True

            # Calculate target volume from existing clients or use startup volume
            if len(self._client_display_states) > 0:
                existing_volumes = list(self._client_display_states.values())
                target_display = sum(existing_volumes) / len(existing_volumes)
            else:
                target_display = startup_volume_getter()

            target_alsa = self.converter.display_to_alsa_precise(target_display)
            target_alsa_clamped = self.converter.clamp_alsa(target_alsa)

            success = await self.snapcast_service.set_volume(client_id, target_alsa_clamped)

            if success:
                from backend.infrastructure.services.volume_converter_service import VolumeConverterService
                self._client_display_states[client_id] = target_display
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": client_id,
                    "volume": VolumeConverterService.round_half_up(target_display),
                    "muted": False,
                    "source": "new_client_multiroom_sync"
                })
                self.logger.debug(
                    f"New client {client_id} synced to "
                    f"{VolumeConverterService.round_half_up(target_display)}%"
                )
                return True
            else:
                self.logger.error(f"Failed to set volume for new client {client_id}")
                return False

        except Exception as e:
            self.logger.error(f"Error initializing client {client_id}: {e}")
            return False

    async def sync_existing_client_from_snapcast(
        self,
        client_id: str,
        snapcast_alsa_volume: int,
    ) -> bool:
        """Synchronize existing client from Snapcast WITHOUT modifying its volume."""
        try:
            display_volume = self.converter.alsa_to_display(snapcast_alsa_volume)
            self._client_display_states[client_id] = float(display_volume)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}")
            return False

    async def sync_client_volume_from_external(
        self,
        client_id: str,
        new_alsa_volume: int,
        adjustment_counter: int = 0,
        schedule_broadcast_callback=None,
    ) -> None:
        """Sync client volume from external change (e.g., snapcast UI)."""
        if adjustment_counter > 0:
            return

        old = self._client_display_states.get(client_id, "unknown")
        new_display = float(self.converter.alsa_to_display(new_alsa_volume))

        if isinstance(old, float) and abs(old - new_display) < 0.5:
            return

        self._client_display_states[client_id] = new_display

        try:
            from backend.infrastructure.services.volume_converter_service import VolumeConverterService
            await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": VolumeConverterService.round_half_up(new_display),
                "muted": False,
                "source": "external_sync"
            })
        except Exception as e:
            self.logger.error(f"Error broadcasting sync: {e}")

        await self._recalculate_multiroom_volume()
        if schedule_broadcast_callback:
            await schedule_broadcast_callback(show_bar=False)

    # ============================================================================
    # STATE MANAGEMENT
    # ============================================================================

    def set_multiroom_volume(self, value: float) -> None:
        """Set multiroom volume (used during initialization)."""
        self._multiroom_volume = value

    def get_multiroom_volume(self) -> float:
        """Get multiroom volume."""
        return self._multiroom_volume

    def invalidate_caches(self) -> None:
        """Invalidate all internal caches."""
        self._client_display_states = {}
        self._client_states_initialized = False
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0

    def update_client_display_volume(self, client_id: str, display_volume: int) -> None:
        """Update client display volume (called from API routes)."""
        try:
            clamped = self.converter.clamp_display(float(display_volume))
            self._client_display_states[client_id] = clamped
            self.logger.debug(f"Updated client {client_id} display volume to {display_volume}%")
        except Exception as e:
            self.logger.error(f"Error updating client volume: {e}")

    def get_status(self) -> dict:
        """Get handler status for debugging."""
        return {
            "mode": "multiroom",
            "multiroom_volume": self._multiroom_volume,
            "client_count": len(self._client_display_states),
        }

    async def get_detailed_status(self) -> dict:
        """Get detailed handler status with client information."""
        from backend.infrastructure.services.volume_converter_service import VolumeConverterService

        clients = await self._get_snapcast_clients_cached()
        details = []

        for client in clients:
            cid = client["id"]
            precise = self._client_display_states.get(cid, "not_cached")

            details.append({
                "id": cid,
                "name": client.get("name", "Unknown"),
                "alsa_volume": client.get("volume", 0),
                "display_volume_precise": precise,
                "display_volume_rounded": (
                    VolumeConverterService.round_half_up(precise)
                    if isinstance(precise, float) else "N/A"
                ),
                "muted": client.get("muted", False)
            })

        return {
            "mode": "multiroom",
            "multiroom_volume": VolumeConverterService.round_half_up(self._multiroom_volume),
            "client_count": len(clients),
            "clients": details
        }

    # ============================================================================
    # INTERNAL METHODS
    # ============================================================================

    def _set_client_display_volume(self, client_id: str, display_volume: float) -> None:
        """Set client display volume (internal state)."""
        clamped = self.converter.clamp_display(display_volume)
        self._client_display_states[client_id] = clamped

    async def _initialize_client_display_states(self) -> None:
        """Initialize client display states from Snapcast."""
        if self._client_states_initialized:
            return

        try:
            clients = await self._get_snapcast_clients_cached()
            total = 0.0
            count = 0

            for client in clients:
                client_id = client["id"]
                alsa_vol = client.get("volume", 0)
                display_vol = self.converter.alsa_to_display(alsa_vol)
                self._client_display_states[client_id] = float(display_vol)
                total += display_vol
                count += 1

            if count > 0:
                self._multiroom_volume = total / count

            self._client_states_initialized = True
        except Exception as e:
            self.logger.error(f"Error initializing states: {e}")

    async def _recalculate_multiroom_volume(self, save_callback=None) -> None:
        """Recalculate multiroom volume average and save if changed."""
        try:
            clients = await self._get_snapcast_clients_cached()
            active = [c for c in clients if not c.get("muted", False)]

            if not active:
                return

            old_volume = self._multiroom_volume
            volumes = []

            for client in active:
                client_id = client["id"]
                precise = self._client_display_states.get(client_id)

                if precise is None:
                    alsa = client.get("volume", 0)
                    precise = float(self.converter.alsa_to_display(alsa))
                    self._client_display_states[client_id] = precise

                volumes.append(precise)

            self._multiroom_volume = sum(volumes) / len(volumes)

            if save_callback and abs(self._multiroom_volume - old_volume) > 0.5:
                from backend.infrastructure.services.volume_converter_service import VolumeConverterService
                rounded = VolumeConverterService.round_half_up(self._multiroom_volume)
                save_callback(rounded)

        except Exception as e:
            self.logger.error(f"Error recalculating: {e}")

    async def _apply_delta(
        self,
        delta: float,
        fallback_volume: Optional[float] = None,
    ) -> bool:
        """Apply delta to all multiroom clients with precise per-client tracking."""
        try:
            from backend.infrastructure.services.volume_converter_service import VolumeConverterService

            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False

            await self._initialize_client_display_states()

            operations = []
            events = []
            new_volumes = []

            for client in clients:
                client_id = client["id"]
                current = self._client_display_states.get(client_id)

                if current is None:
                    alsa = client.get("volume", 0)
                    current = float(self.converter.alsa_to_display(alsa))
                    self._client_display_states[client_id] = current

                new_precise = self.converter.clamp_display(current + delta)
                self._client_display_states[client_id] = new_precise

                alsa = self.converter.display_to_alsa_precise(new_precise)
                alsa_clamped = self.converter.clamp_alsa(alsa)

                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": VolumeConverterService.round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })

                if not client.get("muted", False):
                    new_volumes.append(new_precise)

            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            elif fallback_volume is not None:
                self._multiroom_volume = fallback_volume

            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )

            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom"
                })

            # Invalidate cache after modifications
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0

            return True

        except Exception as e:
            self.logger.error(f"Error applying multiroom delta: {e}")
            return False

    async def _get_snapcast_clients_cached(self) -> List[Dict[str, Any]]:
        """Get snapcast clients with short-term caching."""
        current = time.time() * 1000

        if (current - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache

        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current
            return clients
        except Exception:
            return self._snapcast_clients_cache
