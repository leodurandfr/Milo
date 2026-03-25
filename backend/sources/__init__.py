# backend/features/__init__.py
"""
Feature-based audio source modules.

Each feature is a self-contained module with:
- source.py: AudioSource implementation
- routes.py: FastAPI router

Example:
    from backend.features.mac import MacSource, router as mac_router
"""
