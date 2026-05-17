# backend/core/__init__.py
"""
Core module for Milo audio system.

Submodules:
- backend.core.state — AudioStateMachine
- backend.core.settings — SettingsService
- backend.core.systemd — SystemdServiceManager
- backend.core.volume / equalizer / multiroom — domain services

Import the names you need directly from the submodule (e.g.
`from backend.core.state import AudioStateMachine`). The package
intentionally does NOT eagerly re-export them: doing so re-enters
`backend.config.constants` mid-load via `core.state -> core.models
-> volume -> config.constants`, which raises ImportError because
the constants module is still partially initialized.
"""
