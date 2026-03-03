# backend/core/__init__.py
"""
Core module for Milo audio system.

This module provides core services:
- AudioStateMachine: Central state management
- SettingsService: Configuration management
- SystemdServiceManager: Systemd service control
- VolumeService: Volume control (in core.volume)
- Equalizer services (in core.equalizer)
- Multiroom services (in core.multiroom)
"""

from backend.core.state import AudioStateMachine
from backend.core.settings import SettingsService
from backend.core.systemd import SystemdServiceManager

__all__ = [
    "AudioStateMachine",
    "SettingsService",
    "SystemdServiceManager",
]
