# backend/shared/__init__.py
"""
Shared utilities used across multiple features.

Contains reusable components that are not feature-specific.
"""

from backend.shared.mpv import MpvController

__all__ = ['MpvController']
