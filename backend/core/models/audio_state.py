# backend/core/models/audio_state.py
"""
Unified audio system state model with multiroom support.
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


class AudioSource(Enum):
    """Available audio sources in the system."""
    NONE = "none"
    SPOTIFY = "spotify"
    BLUETOOTH = "bluetooth"
    RADIO = "radio"
    PODCAST = "podcast"
    AIRPLAY = "airplay"
    MAC = "mac"
    CD = "cd"
    DLNA = "dlna"
    QOBUZ = "qobuz"


class SourceState(Enum):
    """Possible operational states for an audio source."""
    STARTING = "starting"      # Source starting or restarting
    WAITING = "waiting"        # Source started, waiting for connection
    ACTIVE = "active"          # Source active and operational
    ERROR = "error"            # Source in error state


@dataclass
class SystemAudioState:
    """
    Source-scoped audio state. Global feature flags (`multiroom_enabled`,
    `equalizer_effects_enabled`) are owned by their respective services
    (AudioRoutingService, CamillaDSPService) and merged into the wire payload
    by AudioStateMachine.broadcast() when aggregating full_state.
    """
    active_source: AudioSource = AudioSource.NONE
    source_state: SourceState = SourceState.WAITING
    transitioning: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "active_source": self.active_source.value,
            "source_state": self.source_state.value,
            "transitioning": self.transitioning,
            "metadata": self.metadata,
            "error": self.error,
        }

