# backend/features/bluetooth/__init__.py
"""
Bluetooth audio source feature using BlueALSA.

This module provides streaming audio from Bluetooth devices via BlueALSA
with support for auto-pairing via D-Bus agent and single device enforcement.

Usage:
    from backend.features.bluetooth import BluetoothSource, router

    # Create source
    source = BluetoothSource(event_bus, config)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.bluetooth.source import BluetoothSource
from backend.features.bluetooth.routes import router, setup_bluetooth_routes
from backend.features.bluetooth.agent import BluetoothAgent
from backend.features.bluetooth.monitor import BlueAlsaMonitor

__all__ = [
    "BluetoothSource",
    "router",
    "setup_bluetooth_routes",
    "BluetoothAgent",
    "BlueAlsaMonitor"
]
