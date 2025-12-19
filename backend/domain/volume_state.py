# backend/domain/volume_state.py
"""
Unified volume state domain models.

VolumeState is the single source of truth for all volume data in the system.
It supports both direct mode (single client) and multiroom mode (multiple clients with zones).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal


@dataclass
class ClientVolume:
    """Volume state for a single client."""
    volume_db: float
    offset_db: float
    mute: bool
    available: bool = True

    def to_dict(self) -> dict:
        return {
            "volume_db": self.volume_db,
            "offset_db": self.offset_db,
            "mute": self.mute,
            "available": self.available
        }


@dataclass
class ZoneVolume:
    """Volume state for a zone (group of linked clients)."""
    id: str
    name: str
    client_ids: List[str]
    average_volume_db: float
    all_muted: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "client_ids": self.client_ids,
            "average_volume_db": self.average_volume_db,
            "all_muted": self.all_muted
        }


@dataclass
class VolumeState:
    """
    Unified volume state for the entire system.

    This is the single source of truth for all volume data.
    Used by API endpoints, WebSocket broadcasts, and frontend stores.
    """
    mode: Literal['direct', 'multiroom']
    global_volume_db: float
    global_mute: bool
    display_volume_db: float
    clients: Dict[str, ClientVolume] = field(default_factory=dict)
    zones: Dict[str, ZoneVolume] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "global_volume_db": self.global_volume_db,
            "global_mute": self.global_mute,
            "display_volume_db": self.display_volume_db,
            "clients": {k: v.to_dict() for k, v in self.clients.items()},
            "zones": {k: v.to_dict() for k, v in self.zones.items()}
        }
