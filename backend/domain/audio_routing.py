"""
Modèles pour le routage audio dans oakOS - Version refactorisée avec multiroom_enabled.
"""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class AudioRoutingState:
    """État du routage audio - Version refactorisée"""
    multiroom_enabled: bool = False
    equalizer_enabled: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'état en dictionnaire"""
        return {
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_enabled": self.equalizer_enabled
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioRoutingState':
        """Crée un état à partir d'un dictionnaire"""
        return cls(
            multiroom_enabled=data.get("multiroom_enabled", False),
            equalizer_enabled=data.get("equalizer_enabled", False)
        )