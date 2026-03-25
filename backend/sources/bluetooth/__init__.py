# backend/sources/bluetooth/__init__.py
"""
Bluetooth audio source feature using BlueALSA.

This module provides streaming audio from Bluetooth devices via BlueALSA
with support for auto-pairing via D-Bus agent and single device enforcement.

Usage:
    from backend.sources.bluetooth import BluetoothSource, router

    # Create source
    source = BluetoothSource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.sources.bluetooth.source import BluetoothSource
from backend.sources.bluetooth.routes import router, setup_bluetooth_routes
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.monitor import BlueAlsaMonitor

__all__ = [
    "BluetoothSource",
    "router",
    "setup_bluetooth_routes",
    "BluetoothAgent",
    "BlueAlsaMonitor"
]
