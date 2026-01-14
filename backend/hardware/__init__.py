# backend/hardware/__init__.py
"""
Hardware module for Milo audio system.

This module provides hardware controllers:
- RotaryVolumeController: Rotary encoder for volume control
- ScreenController: Screen brightness and power management
- HardwareService: Hardware configuration service
"""

from backend.hardware.rotary import RotaryVolumeController
from backend.hardware.screen import ScreenController
from backend.hardware.service import HardwareService

__all__ = [
    "RotaryVolumeController",
    "ScreenController",
    "HardwareService",
]
