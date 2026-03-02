# backend/core/equalizer/__init__.py
"""
Equalizer (Digital Signal Processing) module for Milo audio system.

This module provides:
- CamillaDSPService: Main DSP control (EQ, effects, volume)
- EqualizerClientProxyService: HTTP proxy for remote equalizer clients
- EqualizerSettingsSyncService: Equalizer settings synchronization across clients
- Presets: Built-in EQ presets
"""

from backend.core.equalizer.service import (
    CamillaDSPService,
    CamillaDspState,
    FilterType,
)
from backend.core.equalizer.presets import (
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.equalizer.client_proxy import EqualizerClientProxyService, is_ip_address
from backend.core.equalizer.sync import EqualizerSettingsSyncService
from backend.core.equalizer.multiroom_service import MultiroomEqualizerService

__all__ = [
    # Service
    "CamillaDSPService",
    "CamillaDspState",
    "FilterType",
    # Multiroom Equalizer
    "MultiroomEqualizerService",
    # Proxy
    "EqualizerClientProxyService",
    "is_ip_address",
    # Sync
    "EqualizerSettingsSyncService",
    # Presets
    "get_builtin_presets",
    "get_preset_by_id",
    "DEFAULT_CUSTOM_GAINS",
    "BUILTIN_PRESETS",
]
