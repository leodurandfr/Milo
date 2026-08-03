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
    MUSIC_LIBRARY = "music_library"


class SourceState(Enum):
    """The four states an audio source can be in — all reachable, no other.

    READY and ACTIVE split on whether a session exists, not on whether audio is
    coming out: a paused radio stays ACTIVE because a station is still tuned.
    "ready" rather than "connected" because nothing connects to Radio, CD or
    the Music Library — they are simply ready to play.
    """
    STARTING = "starting"      # Starting or restarting
    READY = "ready"            # Engine up, nothing in session
    ACTIVE = "active"          # A session or content exists
    ERROR = "error"            # Not operational (a failed transition)


@dataclass
class SystemAudioState:
    """
    Source-scoped audio state. Global feature flags (`multiroom_enabled`,
    `equalizer_effects_enabled`) are owned by their respective services
    (AudioRoutingService, CamillaDSPService) and merged into the wire payload
    by AudioStateMachine.broadcast() when aggregating full_state.
    """
    active_source: AudioSource = AudioSource.NONE
    source_state: SourceState = SourceState.READY
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

