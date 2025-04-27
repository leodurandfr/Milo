"""
Définition des événements standardisés pour oakOS.
"""
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass
import time

class EventCategory(Enum):
    """Catégories principales d'événements"""
    SYSTEM = "system"
    PLUGIN = "plugin"
    AUDIO = "audio"
    USER = "user"

class EventType(Enum):
    """Types d'événements spécifiques"""
    # Événements système
    STATE_CHANGED = "state_changed"
    TRANSITION_START = "transition_start"
    TRANSITION_COMPLETE = "transition_complete"
    ERROR = "error"
    
    # Événements plugin
    PLUGIN_STATE_CHANGED = "plugin_state_changed"
    PLUGIN_METADATA = "plugin_metadata"
    PLUGIN_CONNECTION = "plugin_connection"
    
    # Événements audio
    VOLUME_CHANGED = "volume_changed"
    PLAYBACK_STATUS = "playback_status"

@dataclass
class StandardEvent:
    """Structure standardisée pour tous les événements"""
    category: EventCategory
    type: EventType
    source: str  # Qui a émis l'événement (system, librespot, snapclient, etc.)
    data: Dict[str, Any]
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'événement en dictionnaire pour la sérialisation"""
        return {
            "category": self.category.value,
            "type": self.type.value,
            "source": self.source,
            "data": self.data,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StandardEvent':
        """Crée un événement à partir d'un dictionnaire"""
        return cls(
            category=EventCategory(data["category"]),
            type=EventType(data["type"]),
            source=data["source"],
            data=data["data"],
            timestamp=data.get("timestamp")
        )