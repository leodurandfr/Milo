# backend/core/volume/__init__.py
"""
Volume management module for system-wide volume control.

This module provides volume management with support for both direct mode
(single local CamillaDSP) and multiroom mode (multiple Snapcast clients).
"""
from backend.core.volume.service import VolumeService
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.config import VolumeConfigService
from backend.core.volume.equalizer_controller import EqualizerController

__all__ = [
    "VolumeService",
    "VolumeStateStore",
    "VolumeConfigService",
    "EqualizerController",
]
