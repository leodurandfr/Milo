# backend/core/dsp/__init__.py
"""
DSP (Digital Signal Processing) module for Milo audio system.

This module provides:
- CamillaDSPService: Main DSP control (EQ, effects, volume)
- DspClientProxyService: HTTP proxy for remote DSP clients
- DspSettingsSyncService: DSP settings synchronization across clients
- Presets: Built-in EQ presets
"""

from backend.core.dsp.service import (
    CamillaDSPService,
    DspState,
    FilterType,
)
from backend.core.dsp.presets import (
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_MANUAL_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.dsp.client_proxy import DspClientProxyService, is_ip_address
from backend.core.dsp.sync import DspSettingsSyncService

__all__ = [
    # Service
    "CamillaDSPService",
    "DspState",
    "FilterType",
    # Proxy
    "DspClientProxyService",
    "is_ip_address",
    # Sync
    "DspSettingsSyncService",
    # Presets
    "get_builtin_presets",
    "get_preset_by_id",
    "DEFAULT_MANUAL_GAINS",
    "BUILTIN_PRESETS",
]
