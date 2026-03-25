# backend/sources/mac/__init__.py
"""
Mac audio source feature using ROC toolkit.

This module provides streaming audio from Mac computers via ROC
(Roc Opus Codec) with support for multiple simultaneous connections.
"""
from backend.sources.mac.source import MacSource

__all__ = ["MacSource"]
