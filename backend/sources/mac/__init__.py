# backend/sources/mac/__init__.py
"""
Mac audio source feature using ROC toolkit.

This module provides streaming audio from Mac computers via ROC
(Roc Opus Codec) with support for multiple simultaneous connections.

Family A (mute receiver): control flows from the Mac sender, no rich
metadata exposed. No routes.py — commands are dispatched through the
generic `/api/audio/control/mac` endpoint.
"""
from backend.sources.mac.source import MacSource

__all__ = ["MacSource"]
