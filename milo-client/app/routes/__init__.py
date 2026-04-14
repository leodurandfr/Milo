"""
API route factories for Milo Client.
"""
from routes.health import create_health_router
from routes.snapclient import create_snapclient_router
from routes.equalizer import create_equalizer_router
from routes.app_update import create_app_update_router
from routes.hardware import create_hardware_router
from routes.camilladsp_update import create_camilladsp_update_router

__all__ = [
    "create_health_router",
    "create_snapclient_router",
    "create_equalizer_router",
    "create_app_update_router",
    "create_hardware_router",
    "create_camilladsp_update_router",
]
