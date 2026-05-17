# backend/sources/__init__.py
"""
Audio source modules.

Each source lives in its own subpackage. Layout depends on the source's
family (see CLAUDE.md § Audio Source Architecture):
- Family A (mute receiver): source.py only.
- Family B (passive player) / Family C (active player): source.py + routes.py.

Example (family C, data-rich source with a router):
    from backend.sources.radio import RadioSource, router as radio_router
"""
