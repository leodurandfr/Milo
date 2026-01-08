# backend/infrastructure/services/volume/__init__.py
"""
Volume management module for Milo audio system.

This module provides:
- VolumeService: Main volume orchestration service
- VolumeState: Single source of truth for volume state (clients, zones, limits)
- VolumeConfig: Configuration service for loading volume settings
- DSPController: Hardware abstraction for local/remote DSP control
"""

from backend.infrastructure.services.volume.volume_service import VolumeService
from backend.infrastructure.services.volume.volume_state import VolumeStateStore
from backend.infrastructure.services.volume.volume_config import VolumeConfigService
from backend.infrastructure.services.volume.dsp_controller import DSPController

__all__ = [
    "VolumeService",
    "VolumeStateStore",
    "VolumeConfigService",
    "DSPController",
]
