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
    MAC = "mac"
    RADIO = "radio"
    PODCAST = "podcast"


class PluginState(Enum):
    """Possible operational states for a plugin."""
    INACTIVE = "inactive"      # Plugin stopped
    READY = "ready"            # Plugin started, waiting for connection
    CONNECTED = "connected"    # Plugin connected and operational
    ERROR = "error"            # Plugin in error state


@dataclass
class SystemAudioState:
    """
    Complete audio system state combining:
    - Active source
    - Operational state of the active plugin
    - Associated metadata
    - Audio routing state (multiroom_enabled flag)
    - DSP effects state (equalizer, compressor, loudness enabled)
    - Target source during transitions
    """
    active_source: AudioSource = AudioSource.NONE
    plugin_state: PluginState = PluginState.INACTIVE
    transitioning: bool = False
    target_source: Optional[AudioSource] = None
    metadata: Dict[str, Any] = None
    error: Optional[str] = None
    multiroom_enabled: bool = False
    dsp_effects_enabled: bool = False
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "active_source": self.active_source.value,
            "plugin_state": self.plugin_state.value,
            "transitioning": self.transitioning,
            "target_source": self.target_source.value if self.target_source else None,
            "metadata": self.metadata,
            "error": self.error,
            "multiroom_enabled": self.multiroom_enabled,
            "dsp_effects_enabled": self.dsp_effects_enabled
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemAudioState':
        """Create state from dictionary."""
        target_source_str = data.get("target_source")
        target_source = AudioSource(target_source_str) if target_source_str else None

        return cls(
            active_source=AudioSource(data.get("active_source", "none")),
            plugin_state=PluginState(data.get("plugin_state", "inactive")),
            transitioning=data.get("transitioning", False),
            target_source=target_source,
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            multiroom_enabled=data.get("multiroom_enabled", False),
            # Support both new key and legacy key for backwards compatibility
            dsp_effects_enabled=data.get("dsp_effects_enabled", data.get("dsp_enabled", False))
        )