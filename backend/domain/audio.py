"""
Définition des états et entités du domaine audio.
"""
from enum import Enum
from typing import Optional, Dict, Any


class AudioState(Enum):
    """États possibles du système audio"""
    NONE = "none"
    TRANSITIONING = "transitioning"
    LIBRESPOT = "librespot"
    BLUETOOTH = "bluetooth"
    SNAPCLIENT = "snapclient"
    WEBRADIO = "webradio"


class AudioStateInfo:
    """Information sur l'état actuel du système audio"""
    
    def __init__(self, state: AudioState = AudioState.NONE):
        self.state = state
        self.transitioning = False
        self.metadata = {}
        self.volume = 50  # Valeur par défaut en pourcentage
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'objet en dictionnaire pour la sérialisation"""
        return {
            "state": self.state.value,
            "transitioning": self.transitioning,
            "metadata": self.metadata,
            "volume": self.volume
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioStateInfo':
        """Crée un objet à partir d'un dictionnaire"""
        state_info = cls(AudioState(data.get("state", "none")))
        state_info.transitioning = data.get("transitioning", False)
        state_info.metadata = data.get("metadata", {})
        state_info.volume = data.get("volume", 50)
        return state_info