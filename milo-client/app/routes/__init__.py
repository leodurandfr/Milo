"""
API route factories for Milo Client.
"""
from routes.health import create_health_router
from routes.snapclient import create_snapclient_router
from routes.dsp import create_dsp_router

__all__ = [
    "create_health_router",
    "create_snapclient_router",
    "create_dsp_router"
]
