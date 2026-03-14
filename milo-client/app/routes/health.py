"""
Health check routes for Milo Client.
"""
import asyncio
import platform
import time
import logging
from fastapi import APIRouter, HTTPException

from services.equalizer import EqualizerService
from services.snapclient import SnapclientService
from services.app_update import AppUpdateService

logger = logging.getLogger(__name__)


def get_system_uptime() -> int:
    """Gets the system uptime in seconds."""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            return int(uptime_seconds)
    except Exception:
        return 0


def get_hostname() -> str:
    """Gets the system hostname."""
    return platform.node()


def create_health_router(
    equalizer_service: EqualizerService,
    snapclient_service: SnapclientService,
    app_update_service: AppUpdateService
) -> APIRouter:
    """Creates health router with injected dependencies."""
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health_check():
        """Health endpoint with Equalizer readiness status."""
        return {
            "status": "healthy",
            "timestamp": int(time.time()),
            "hostname": get_hostname(),
            "equalizer_ready": equalizer_service.connected
        }

    @router.get("/status")
    async def get_status():
        """Gets the complete client status."""
        try:
            hostname = get_hostname()
            uptime = get_system_uptime()

            # Parallel subprocess calls for faster response
            snapclient_version, snapclient_running = await asyncio.gather(
                snapclient_service.get_installed_version(),
                snapclient_service.is_service_running()
            )

            return {
                "hostname": hostname,
                "uptime": uptime,
                "snapclient": {
                    "version": snapclient_version,
                    "running": snapclient_running,
                    "status": "running" if snapclient_running else "stopped"
                },
                "app": {
                    "version": app_update_service.get_app_version(),
                    "update_in_progress": app_update_service.update_in_progress
                },
                "update_in_progress": snapclient_service.update_in_progress,
                "timestamp": int(time.time())
            }

        except Exception as e:
            logger.error(f"Error getting status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
