# backend/domain/audio_state.py
"""
Modèle d'état unifié pour le système audio - Version refactorisée avec multiroom_enabled.
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any


class AudioSource(Enum):
    """Sources audio disponibles dans le système"""
    NONE = "none"
    LIBRESPOT = "librespot"
    BLUETOOTH = "bluetooth"
    ROC = "roc"
    WEBRADIO = "webradio"


class PluginState(Enum):
    """États opérationnels possibles pour un plugin"""
    INACTIVE = "inactive"      # Plugin arrêté
    READY = "ready"           # Plugin démarré, en attente de connexion
    CONNECTED = "connected"   # Plugin connecté et opérationnel
    ERROR = "error"          # Plugin en erreur


@dataclass
class SystemAudioState:
    """
    État complet du système audio combinant :
    - La source active
    - L'état opérationnel du plugin actif
    - Les métadonnées associées
    - L'état du routage audio (multiroom_enabled au lieu de routing_mode)
    - L'état de l'equalizer
    """
    active_source: AudioSource = AudioSource.NONE
    plugin_state: PluginState = PluginState.INACTIVE
    transitioning: bool = False
    metadata: Dict[str, Any] = None
    error: Optional[str] = None
    multiroom_enabled: bool = False  # Refactorisé : multiroom désactivé par défaut
    equalizer_enabled: bool = False
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'état en dictionnaire pour la sérialisation"""
        return {
            "active_source": self.active_source.value,
            "plugin_state": self.plugin_state.value,
            "transitioning": self.transitioning,
            "metadata": self.metadata,
            "error": self.error,
            "multiroom_enabled": self.multiroom_enabled,  # Refactorisé
            "equalizer_enabled": self.equalizer_enabled
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemAudioState':
        """Crée un état à partir d'un dictionnaire"""
        return cls(
            active_source=AudioSource(data.get("active_source", "none")),
            plugin_state=PluginState(data.get("plugin_state", "inactive")),
            transitioning=data.get("transitioning", False),
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            multiroom_enabled=data.get("multiroom_enabled", False),
            equalizer_enabled=data.get("equalizer_enabled", False)
        )