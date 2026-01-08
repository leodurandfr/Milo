# backend/infrastructure/services/dsp/__init__.py
"""
DSP (Digital Signal Processing) module for Milo audio system.

This module provides:
- CamillaDSPService: Main DSP control (EQ, effects, volume)
- CamillaDSPConfigGenerator: YAML configuration generator
- CrossoverService: Speaker crossover management
- ClientProxyService: HTTP proxy for remote DSP clients
- SettingsSyncService: DSP settings synchronization across clients
"""

from backend.infrastructure.services.dsp.camilladsp_service import (
    CamillaDSPService,
    DspState,
    FilterType,
)
from backend.infrastructure.services.dsp.camilladsp_config import CamillaDSPConfigGenerator
from backend.infrastructure.services.dsp.crossover_service import CrossoverService
from backend.infrastructure.services.dsp.client_proxy_service import DspClientProxyService
from backend.infrastructure.services.dsp.settings_sync_service import DspSettingsSyncService

__all__ = [
    "CamillaDSPService",
    "CamillaDSPConfigGenerator",
    "DspState",
    "FilterType",
    "CrossoverService",
    "DspClientProxyService",
    "DspSettingsSyncService",
]
