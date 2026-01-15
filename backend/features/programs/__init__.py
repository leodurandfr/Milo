# backend/features/programs/__init__.py
"""
Program management services for Milo.

This module provides:
- ProgramVersionService: Version checking and comparison
- ProgramUpdateService: Update execution
- SatelliteProgramUpdateService: Satellite device updates
"""

from backend.features.programs.version import ProgramVersionService
from backend.features.programs.update import ProgramUpdateService
from backend.features.programs.satellite import SatelliteProgramUpdateService

__all__ = [
    "ProgramVersionService",
    "ProgramUpdateService",
    "SatelliteProgramUpdateService",
]
