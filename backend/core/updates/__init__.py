# backend/core/updates/__init__.py
"""
Update management services for Milo.

This module provides:
- VersionService: Version checking and comparison
- UpdateService: Update execution
- SatelliteUpdateService: Satellite device updates
"""

from backend.core.updates.helpers import compare_versions
from backend.core.updates.version import VersionService
from backend.core.updates.update import UpdateService
from backend.core.updates.satellite import SatelliteUpdateService

__all__ = [
    "compare_versions",
    "VersionService",
    "UpdateService",
    "SatelliteUpdateService",
]
