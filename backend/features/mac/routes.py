# backend/features/mac/routes.py
"""
FastAPI routes for Mac audio source.

Provides REST API endpoints for:
- Status: Get current Mac source status
- Restart: Restart the ROC service
- Connections: List connected Mac clients

Usage:
    from backend.features.mac import router, MacSource

    source = MacSource(config=config)
    setup_mac_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
import logging

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from backend.api.route_helpers import run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.features.mac.source import MacSource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/mac",
    tags=["mac"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Mac")


def setup_mac_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@router.get("/status")
async def get_status(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """Get current Mac source status."""
    try:
        status = await source.status()
        return {"status": "success", **status}
    except Exception as e:
        logger.error("Failed to get Mac status: %s", e)
        return {
            "status": "error",
            "message": str(e),
            "state": "error"
        }


@router.post("/restart")
async def restart_service(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """Restart the Mac audio service."""
    return await run_source_command(source, "restart_service", {}, "Restart")


@router.get("/connections")
async def get_connections(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """Get list of connected Mac clients."""
    return await run_source_command(source, "get_connections", {}, "Connections")


@router.get("/info")
async def get_info(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """Get Mac source configuration information."""
    try:
        status = await source.status()

        return {
            "status": "success",
            "configuration": {
                "rtp_port": status.get("rtp_port", 10001),
                "rs8m_port": status.get("rs8m_port", 10002),
                "rtcp_port": status.get("rtcp_port", 10003),
                "audio_output": status.get("audio_output", "hw:1,0")
            },
            "service": {
                "name": source.service_name,
                "active": status.get("service_active", False)
            }
        }

    except Exception as e:
        logger.error("Failed to get Mac info: %s", e)
        raise HTTPException(status_code=500, detail=f"Info error: {str(e)}")
