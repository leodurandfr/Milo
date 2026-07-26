# backend/sources/__init__.py
"""Audio source modules — one subpackage per source.

Each subpackage's layout follows its family (see CLAUDE.md § Audio sources).
Every package exports exactly one name, the `{Name}Source` class that
`dependencies.py` instantiates; anything else is imported from its own
submodule, so the facade can't drift into a second, unused API surface.
"""
