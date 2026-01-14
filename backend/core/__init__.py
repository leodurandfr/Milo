# backend/core/__init__.py
"""
Core module for Milo audio system.

This module provides core services:
- AudioStateMachine: Central state management
- EventBus: Event-driven communication
- SettingsService: Configuration management
- SystemdServiceManager: Systemd service control
- VolumeService: Volume control (in core.volume)
- DSP services (in core.dsp)
- Multiroom services (in core.multiroom)
"""

from backend.core.events import EventBus, Events, get_event_bus
from backend.core.state import AudioStateMachine
from backend.core.settings import SettingsService
from backend.core.systemd import SystemdServiceManager

__all__ = [
    "EventBus",
    "Events",
    "get_event_bus",
    "AudioStateMachine",
    "SettingsService",
    "SystemdServiceManager",
]
