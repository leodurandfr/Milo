# backend/shared/__init__.py
"""
Shared utilities used across multiple features.

Contains reusable components that are not feature-specific.
"""

from backend.shared.background import BackgroundTaskSet
from backend.shared.mpv import MpvController
from backend.shared.decorators import handle_errors
from backend.shared.artwork import decode_artwork_dimensions

__all__ = ['BackgroundTaskSet', 'MpvController', 'handle_errors', 'decode_artwork_dimensions']
