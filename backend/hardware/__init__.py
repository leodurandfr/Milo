# backend/hardware/__init__.py
"""
Hardware module for Milo audio system.

This module provides hardware controllers:
- RotaryVolumeController: Rotary encoder for volume control
- ScreenController: Screen brightness and power management
- HardwareService: Hardware configuration service
- BtRemoteController: Bluetooth HID remote for volume/playback control
- IrRemoteController: IR remote (Apple Remote 1st gen) via TSOP4838 on GPIO17
"""

from backend.hardware.rotary import RotaryVolumeController
from backend.hardware.screen import ScreenController
from backend.hardware.service import HardwareService
from backend.hardware.bt_remote import BtRemoteController
from backend.hardware.ir_remote import IrRemoteController

__all__ = [
    "RotaryVolumeController",
    "ScreenController",
    "HardwareService",
    "BtRemoteController",
    "IrRemoteController",
]
