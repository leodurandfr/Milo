# backend/core/programs/__init__.py
"""
Program management services for Milo.

This module provides:
- ProgramVersionService: Version checking and comparison
- ProgramUpdateService: Update execution
- SatelliteProgramUpdateService: Satellite device updates
"""

from backend.core.programs.version import ProgramVersionService
from backend.core.programs.update import ProgramUpdateService
from backend.core.programs.satellite import SatelliteProgramUpdateService

__all__ = [
    "ProgramVersionService",
    "ProgramUpdateService",
    "SatelliteProgramUpdateService",
]
