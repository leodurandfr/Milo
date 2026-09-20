# backend/core/models/volume_state.py
"""
Unified volume state domain models.

VolumeState is the single source of truth for all volume data in the system.
It supports both direct mode (single client) and multiroom mode (multiple clients with zones).

Every level travels twice: in dB, which is what the audio path and the web UI
speak, and normalized over the operator's limits, which is what a client that
must not know the limits speaks. Both spellings are produced here rather than in
the response models, because `GET /api/volume/state` and the `volume_changed` WS
event share this one dict.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal

from backend.core.models.volume import normalize_volume


@dataclass
class ClientVolume:
    """Volume state for a single client."""
    volume_db: float
    offset_db: float
    mute: bool
    available: bool = True
    volume_control: bool = True  # False when this client is a DAC (external amp)

    def to_dict(self, limit_min_db: float, limit_max_db: float) -> dict:
        """The wire shape. The bounds are arguments because a client does not
        carry them — only the state that holds every client does, and it is the
        one caller."""
        return {
            "volume_db": self.volume_db,
            "volume": normalize_volume(self.volume_db, limit_min_db, limit_max_db),
            "offset_db": self.offset_db,
            "mute": self.mute,
            "available": self.available,
            "volume_control": self.volume_control
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

    The limits travel with the state, and not only as two numbers a client may
    display: they are the span every normalized level here was computed over, so
    a reader that keeps them keeps the two halves of one measurement together.
    """
    mode: Literal['direct', 'multiroom']
    global_volume_db: float
    global_mute: bool
    limit_min_db: float
    limit_max_db: float
    clients: Dict[str, ClientVolume] = field(default_factory=dict)
    zones: Dict[str, ZoneVolume] = field(default_factory=dict)
    volume_control: bool = True  # False when local device is a DAC (external amp)
    any_volume_control: bool = True  # True if at least one device manages volume via Milo

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "global_volume_db": self.global_volume_db,
            "global_volume": normalize_volume(
                self.global_volume_db, self.limit_min_db, self.limit_max_db
            ),
            "global_mute": self.global_mute,
            "limit_min_db": self.limit_min_db,
            "limit_max_db": self.limit_max_db,
            "volume_control": self.volume_control,
            "any_volume_control": self.any_volume_control,
            "clients": {
                k: v.to_dict(self.limit_min_db, self.limit_max_db)
                for k, v in self.clients.items()
            },
            "zones": {k: v.to_dict() for k, v in self.zones.items()}
        }
