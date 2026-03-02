# backend/features/airplay/routes.py
"""
FastAPI routes for AirPlay 2 audio source.

Provides REST API endpoints for:
- Status: Get current AirPlay source status with metadata
- Restart: Restart shairport-sync service
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from backend.api.source_dependency import make_source_dependency
from backend.features.airplay.source import AirPlaySource

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
        return {
            "status": "ok",
            "state": status.get("state", "unknown"),
            "service_active": status.get("service_active", False),
            "device_connected": status.get("device_connected", False),
            "is_playing": status.get("is_playing", False),
            "metadata": status.get("metadata", {}),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "state": "error",
            "metadata": {},
            "is_playing": False,
            "device_connected": False,
        }


@router.post("/restart")
async def restart_service(source: AirPlaySource = Depends(get_source)) -> Dict[str, Any]:
    """Restart shairport-sync service."""
    try:
        result = await source.command("restart_service", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Restart failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restart error: {str(e)}")
