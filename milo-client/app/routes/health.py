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
from services.app_update import PAYLOAD_FILE, VERSION_FILE, AppUpdateService, read_identity
from services.camilladsp_update import CamillaDSPUpdateService

logger = logging.getLogger(__name__)

# What this process is, read once when it came up. An update writes the identity
# files before it schedules the restart, so read per request they would name the
# code a refused restart never ran — and the server, which compares the payload,
# would call the unit current and never push it again.
APP_STARTED_AT = int(time.time())
APP_VERSION = read_identity(VERSION_FILE)
APP_PAYLOAD = read_identity(PAYLOAD_FILE)


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
    app_update_service: AppUpdateService,
    camilladsp_update_service: CamillaDSPUpdateService
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
            snapclient_version, snapclient_running, camilladsp_version = await asyncio.gather(
                snapclient_service.get_installed_version(),
                snapclient_service.is_service_running(),
                camilladsp_update_service.get_installed_version()
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
                    # `version` is what the server displays beside its own row;
                    # `payload` is what it compares to decide on a push.
                    "version": APP_VERSION,
                    "payload": APP_PAYLOAD,
                    "started_at": APP_STARTED_AT,
                    "update_in_progress": app_update_service.update_in_progress
                },
                "camilladsp": {
                    "version": camilladsp_version,
                    "update_in_progress": camilladsp_update_service.update_in_progress
                },
                "update_in_progress": snapclient_service.update_in_progress,
                "timestamp": int(time.time())
            }

        except Exception as e:
            logger.error(f"Error getting status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
