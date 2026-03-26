# backend/domain/audio_state.py
"""
Unified audio system state model with multiroom support.
"""
from enum import Enum
from dataclasses import dataclass
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


class SourceState(Enum):
    """Possible operational states for an audio source."""
    STARTING = "starting"      # Source starting or restarting
    WAITING = "waiting"        # Source started, waiting for connection
    ACTIVE = "active"          # Source active and operational
    ERROR = "error"            # Source in error state


@dataclass
class SystemAudioState:
    """
    Complete audio system state combining:
    - Active source
    - Operational state of the active source
    - Associated metadata
    - Audio routing state (multiroom_enabled flag)
    - equalizer effects state (equalizer, compressor, loudness enabled)
    """
    active_source: AudioSource = AudioSource.NONE
    source_state: SourceState = SourceState.WAITING
    transitioning: bool = False
    metadata: Dict[str, Any] = None
    error: Optional[str] = None
    multiroom_enabled: bool = False
    equalizer_effects_enabled: bool = False

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "active_source": self.active_source.value,
            "source_state": self.source_state.value,
            "transitioning": self.transitioning,
            "metadata": self.metadata,
            "error": self.error,
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_effects_enabled": self.equalizer_effects_enabled
        }

