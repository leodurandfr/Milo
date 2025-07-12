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
    - La source cible pendant les transitions
    """
    active_source: AudioSource = AudioSource.NONE
    plugin_state: PluginState = PluginState.INACTIVE
    transitioning: bool = False
    target_source: Optional[AudioSource] = None  # AJOUT
    metadata: Dict[str, Any] = None
    error: Optional[str] = None
    multiroom_enabled: bool = False
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
            "target_source": self.target_source.value if self.target_source else None,
            "metadata": self.metadata,
            "error": self.error,
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_enabled": self.equalizer_enabled
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemAudioState':
        """Crée un état à partir d'un dictionnaire"""
        target_source_str = data.get("target_source")
        target_source = AudioSource(target_source_str) if target_source_str else None
        
        return cls(
            active_source=AudioSource(data.get("active_source", "none")),
            plugin_state=PluginState(data.get("plugin_state", "inactive")),
            transitioning=data.get("transitioning", False),
            target_source=target_source,  # AJOUT
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            multiroom_enabled=data.get("multiroom_enabled", False),
            equalizer_enabled=data.get("equalizer_enabled", False)
        )