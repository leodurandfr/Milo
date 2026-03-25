# backend/sources/airplay/routes.py
"""
FastAPI routes for AirPlay 2 audio source.

Provides REST API endpoints for:
- Status: Get current AirPlay source status with metadata
- Artwork: Serve current album artwork as binary image
- Restart: Restart shairport-sync service
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from typing import Dict, Any

from backend.api.route_helpers import run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.sources.airplay.source import AirPlaySource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/airplay",
    tags=["airplay"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("AirPlay")


def setup_airplay_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@router.get("/status")
async def get_status(source: AirPlaySource = Depends(get_source)) -> Dict[str, Any]:
    """Get current AirPlay source status with metadata."""
    try:
        status = await source.status()
        return {"status": "success", **status}
    except Exception as e:
        logger.error("Failed to get AirPlay status: %s", e)
        return {
            "status": "error",
            "message": str(e),
            "state": "error",
            "metadata": {},
            "is_playing": False,
            "device_connected": False,
        }


@router.get("/artwork")
async def get_artwork(source: AirPlaySource = Depends(get_source)) -> Response:
    """Serve current AirPlay artwork as binary image."""
    result = source.get_artwork()
    if not result:
        raise HTTPException(status_code=404, detail="No artwork available")

    data, mime_type = result
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.post("/restart")
async def restart_service(source: AirPlaySource = Depends(get_source)) -> Dict[str, Any]:
    """Restart shairport-sync service."""
    return await run_source_command(source, "restart_service", {}, "Restart")
