#!/usr/bin/env python3
"""
Milo Client - API Service for Snapclient Management and Equalizer Control
Version: 2.0 - Feature-based architecture
"""
import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from services import EqualizerService, SnapclientService, AppUpdateService, CamillaDSPUpdateService
from routes import create_health_router, create_snapclient_router, create_equalizer_router, create_app_update_router, create_hardware_router, create_camilladsp_update_router, create_diagnostic_router
from routes.health import get_hostname
from services.registration import register_with_main_milo

# Constants
API_PORT = 8001

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def _sd_notify_ready():
    """Notify systemd that the service is ready (no external dependency)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"READY=1", addr)
    logger.info("sd_notify: READY=1 sent to systemd")


# Create service instances
equalizer_service = EqualizerService()
snapclient_service = SnapclientService()
app_update_service = AppUpdateService()
camilladsp_update_service = CamillaDSPUpdateService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.

    CamillaDSP starts MUTED (-m flag in systemd service) and stays muted until
    the backend pushes the correct volume.
    """
    logger.info("Milo Client API starting up...")

    # No connect attempt here on purpose. milo-client-camilladsp.service is ordered
    # After=milo-client.service, and this unit is Type=notify — so CamillaDSP does
    # not even start until the READY=1 sent a few lines below. A cold-boot connect
    # can therefore never succeed; it only bought ~4.5 s of sleeps and an ERROR in
    # the journal on every boot. The loop owns every attempt, the first included,
    # and it also restores volume/mute, which the startup connect never did.
    if equalizer_service.available:
        equalizer_service.start_connection_loop()
    else:
        logger.warning("CamillaDSP client library not available")

    logger.info("Milo Client API startup complete")

    # Signal systemd that the service is ready (Type=notify).
    # This unblocks milo-client-snapclient.service so snapclient only
    # connects to snapserver AFTER our API is accepting requests.
    _sd_notify_ready()

    # Register with main Milo (background task, retries until successful)
    registration_task = asyncio.create_task(register_with_main_milo())

    yield  # Application runs here

    registration_task.cancel()
    await equalizer_service.stop_connection_loop()
    logger.info("Milo Client API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Milo Client API",
    description="API for Milo client management",
    version="2.0.0",
    lifespan=lifespan
)

# Register routers
app.include_router(create_health_router(equalizer_service, snapclient_service, app_update_service, camilladsp_update_service))
app.include_router(create_snapclient_router(snapclient_service))
app.include_router(create_equalizer_router(equalizer_service))
app.include_router(create_app_update_router(app_update_service))
app.include_router(create_camilladsp_update_router(camilladsp_update_service))
app.include_router(create_hardware_router())
app.include_router(create_diagnostic_router())


# Main entry point
if __name__ == "__main__":
    logger.info(f"Starting Milo Client API on port {API_PORT}")
    logger.info(f"Hostname: {get_hostname()}")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        access_log=True
    )
