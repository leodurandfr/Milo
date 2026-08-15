# backend/core/equalizer/__init__.py
"""
Equalizer (Digital Signal Processing) module for Milo audio system.

This module provides:
- CamillaDSPService: Main DSP control (EQ, effects, volume)
- EqualizerClientProxyService: HTTP proxy for remote equalizer clients
- Presets: Built-in EQ presets
"""

from backend.core.equalizer.service import (
    CamillaDSPService,
    CamillaDspState,
)
from backend.core.equalizer.presets import (
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.equalizer.client_proxy import (
    EqualizerClientProxyService,
    SatelliteUnreachable,
)
from backend.core.equalizer.levels_monitor import LevelsMonitor
from backend.core.equalizer.multiroom_service import MultiroomEqualizerService

__all__ = [
    # Service
    "CamillaDSPService",
    "CamillaDspState",
    # Levels push
    "LevelsMonitor",
    # Multiroom Equalizer
    "MultiroomEqualizerService",
    # Proxy
    "EqualizerClientProxyService",
    "SatelliteUnreachable",
    # Presets
    "get_builtin_presets",
    "get_preset_by_id",
    "DEFAULT_CUSTOM_GAINS",
    "BUILTIN_PRESETS",
]
