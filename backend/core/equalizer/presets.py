# backend/core/equalizer/presets.py
"""
Predefined EQ presets for 10-band parametric equalizer.

Frequencies: 31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 Hz
Gains: -15 to +15 dB

Each preset contains:
  - id: unique identifier (used for API and i18n keys)
  - gains: array of 10 gain values in dB

Note: "custom" is a special preset stored in user settings, not here.
The order below matches the UI display order.
"""

from typing import List, Dict, Optional

BUILTIN_PRESETS: List[Dict] = [
    {"id": "acoustic", "gains": [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]},
    {"id": "bass_boost", "gains": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]},
    {"id": "bass_reducer", "gains": [-6, -5, -4, -2, 0, 0, 0, 0, 0, 0]},
    {"id": "classical", "gains": [5, 4, 3, 2, -1, -1, 0, 2, 3, 4]},
    {"id": "dance", "gains": [4, 6, 5, 0, 2, 4, 5, 4, 3, 0]},
    {"id": "deep", "gains": [5, 4, 2, 1, 3, 2, 1, -1, -3, -4]},
    {"id": "electronic", "gains": [5, 4, 2, 0, -2, 2, 1, 3, 5, 4]},
    {"id": "flat", "gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]},
    {"id": "hip_hop", "gains": [5, 5, 3, 1, -1, -1, 1, 0, 2, 3]},
    {"id": "jazz", "gains": [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]},
    {"id": "latin", "gains": [4, 3, 0, 0, -2, -2, -2, 0, 4, 5]},
    {"id": "loudness", "gains": [6, 5, 2, 0, -2, -2, 0, 2, 4, 5]},
    {"id": "lounge", "gains": [-3, -2, -1, 1, 3, 2, 0, -1, 2, 1]},
    {"id": "piano", "gains": [3, 2, 0, 2, 3, 2, 3, 4, 3, 3]},
    {"id": "pop", "gains": [-1, 1, 3, 4, 3, 1, 0, 0, -1, -1]},
    {"id": "rnb", "gains": [3, 6, 5, 2, -2, -1, 2, 3, 3, 4]},
    {"id": "rock", "gains": [5, 4, 3, 2, 0, -1, 1, 3, 4, 5]},
    {"id": "small_speakers", "gains": [6, 5, 4, 3, 2, 1, 0, -1, -2, -2]},
    {"id": "spoken_word", "gains": [-3, -1, 0, 3, 5, 5, 4, 2, 1, 0]},
    {"id": "treble_boost", "gains": [0, 0, 0, 0, 0, 2, 3, 4, 5, 6]},
    {"id": "treble_reducer", "gains": [0, 0, 0, 0, 0, -2, -3, -4, -5, -6]},
    {"id": "vocal_boost", "gains": [-2, -1, 0, 2, 4, 4, 3, 2, 1, 0]},
]

# Default gains for custom preset (flat)
DEFAULT_CUSTOM_GAINS = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

# Standard 10-band EQ frequencies (Hz)
DEFAULT_EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def get_builtin_presets() -> List[Dict]:
    """Return all builtin presets."""
    return BUILTIN_PRESETS


def get_preset_by_id(preset_id: str) -> Optional[Dict]:
    """Find a builtin preset by its ID."""
    return next((p for p in BUILTIN_PRESETS if p["id"] == preset_id), None)
