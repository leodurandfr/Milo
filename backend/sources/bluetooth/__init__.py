# backend/sources/bluetooth/__init__.py
"""
Bluetooth audio source feature using BlueALSA.

This module provides streaming audio from Bluetooth devices via BlueALSA
with support for auto-pairing via D-Bus agent and single device enforcement.

Family A (mute receiver): control flows through the generic
`/api/audio/control/bluetooth` endpoint — no dedicated routes.py.
"""
from backend.sources.bluetooth.source import BluetoothSource
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.monitor import BlueAlsaMonitor

__all__ = [
    "BluetoothSource",
    "BluetoothAgent",
    "BlueAlsaMonitor",
]
