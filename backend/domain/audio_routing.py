"""
Modèles pour le routage audio dans oakOS.
"""
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any

class AudioRoutingMode(Enum):
    """Modes de routage audio disponibles"""
    DIRECT = "direct"        # Audio directement vers la carte son
    MULTIROOM = "multiroom"  # Audio via snapserver pour multiroom

@dataclass
class AudioRoutingState:
    """État du routage audio"""
    mode: AudioRoutingMode = AudioRoutingMode.MULTIROOM
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'état en dictionnaire"""
        return {
            "mode": self.mode.value
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioRoutingState':
        """Crée un état à partir d'un dictionnaire"""
        return cls(
            mode=AudioRoutingMode(data.get("mode", "multiroom"))
        )