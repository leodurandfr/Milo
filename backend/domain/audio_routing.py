"""
Audio routing models for Milo with multiroom support.
"""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class AudioRoutingState:
    """Audio routing state."""
    multiroom_enabled: bool = False
    dsp_effects_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary."""
        return {
            "multiroom_enabled": self.multiroom_enabled,
            "dsp_effects_enabled": self.dsp_effects_enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioRoutingState':
        """Create state from dictionary."""
        return cls(
            multiroom_enabled=data.get("multiroom_enabled", False),
            dsp_effects_enabled=data.get("dsp_effects_enabled", False)
        )