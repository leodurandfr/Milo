# backend/sources/bluetooth/__init__.py
"""Bluetooth audio source via BlueALSA (Family A — mute receiver).

Auto-pairing through a D-Bus agent (agent.py) and single-device enforcement.
Control flows through the generic `/api/audio/control/bluetooth` endpoint — no
dedicated routes.py.
"""
from backend.sources.bluetooth.source import BluetoothSource

__all__ = ["BluetoothSource"]
