"""
Modèles pour le routage audio dans oakOS - Étendu pour l'equalizer.
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
    """État du routage audio - Étendu pour l'equalizer"""
    mode: AudioRoutingMode = AudioRoutingMode.MULTIROOM
    equalizer_enabled: bool = False  # Nouveau champ pour l'equalizer
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'état en dictionnaire"""
        return {
            "mode": self.mode.value,
            "equalizer_enabled": self.equalizer_enabled
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioRoutingState':
        """Crée un état à partir d'un dictionnaire"""
        return cls(
            mode=AudioRoutingMode(data.get("mode", "multiroom")),
            equalizer_enabled=data.get("equalizer_enabled", False)
        )