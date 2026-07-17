# backend/sources/qobuz/__init__.py
"""Qobuz Connect audio source via the qobuz-proxy sidecar (Family B).

Milō appears as a virtual Qobuz Connect device (qobuz-proxy); the Qobuz app is
the controller and qobuz-proxy renders to ALSA (milo_qobuz). Passive player,
like AirPlay — Milō only displays + plays: it polls the proxy's local HTTP API
(GET /api/status) for now-playing metadata and has no on-device controls.

Usage:
    from backend.sources.qobuz import QobuzSource

    source = QobuzSource(config=config, state_machine=state_machine)
"""
from backend.sources.qobuz.source import QobuzSource

__all__ = ["QobuzSource"]
