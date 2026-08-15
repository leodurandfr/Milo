# backend/sources/bluetooth/__init__.py
"""Bluetooth audio source (Family C — active player).

Two independent feeds, neither able to answer the other's question: BlueALSA
(monitor.py) watches PCM add/remove and decides presence — ACTIVE vs READY —
while BlueZ AVRCP (avrcp.py) carries the track metadata and the transport Milō
drives from its own UI. An AVRCP target is optional, so the source stays usable
with no metadata at all.

AVRCP carries no cover art over the link, which is why artwork is resolved from
the track text (shared/artwork_resolver.py) instead of read from the sender.

Auto-pairing through a D-Bus agent (agent.py) and single-device enforcement.
Control flows through the generic `/api/audio/control/bluetooth` endpoint — no
dedicated routes.py.
"""
from backend.sources.bluetooth.source import BluetoothSource

__all__ = ["BluetoothSource"]
