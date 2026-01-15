#!/usr/bin/env python3
"""
Milo Client - API Service for Snapclient Management and DSP Control
Version: 2.0 - Feature-based architecture
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from services import DSPService, SnapclientService
from routes import create_health_router, create_snapclient_router, create_dsp_router
from routes.health import get_hostname

# Constants
API_PORT = 8001

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create service instances
dsp_service = DSPService()
snapclient_service = SnapclientService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.

    CamillaDSP starts MUTED (-m flag in systemd service).
    This lifespan just connects - the backend will push
    the correct volume and unmute when ready.
    """
    logger.info("Milo Client API starting up...")

    # Connect to CamillaDSP (stays muted until backend pushes correct volume)
    if dsp_service.available:
        max_retries = 10
        retry_delay = 0.5  # seconds

        for attempt in range(max_retries):
            connected = await dsp_service.connect()
            if connected:
                logger.info(
                    f"[{time.time():.3f}] STARTUP: CamillaDSP connected on attempt {attempt + 1}, "
                    "MUTED, waiting for backend"
                )
                break
            else:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"CamillaDSP connection attempt {attempt + 1}/{max_retries} failed, "
                        f"retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to CamillaDSP after all retries")
    else:
        logger.warning("CamillaDSP client library not available")

    logger.info("Milo Client API startup complete")

    yield  # Application runs here

    logger.info("Milo Client API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Milo Client API",
    description="API for Milo client management",
    version="2.0.0",
    lifespan=lifespan
)

# Register routers
app.include_router(create_health_router(dsp_service, snapclient_service))
app.include_router(create_snapclient_router(snapclient_service))
app.include_router(create_dsp_router(dsp_service))


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
