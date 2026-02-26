# backend/features/airplay/routes.py
"""
FastAPI routes for AirPlay 2 audio source.

Provides REST API endpoints for:
- Status: Get current AirPlay source status with metadata
- Restart: Restart shairport-sync service
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Callable, Optional

from backend.features.airplay.source import AirPlaySource

router = APIRouter(
    prefix="/airplay",
    tags=["airplay"],
    responses={404: {"description": "Not found"}},
)

_source_provider: Optional[Callable[[], AirPlaySource]] = None


def setup_airplay_routes(source_provider: Callable[[], AirPlaySource]) -> APIRouter:
    """Configure routes with source provider."""
    global _source_provider
    _source_provider = source_provider
    return router


def get_source() -> AirPlaySource:
    """Dependency to get AirPlaySource instance."""
    if _source_provider is None:
        raise HTTPException(status_code=503, detail="AirPlay source not configured")
    source = _source_provider()
    if source is None:
        raise HTTPException(status_code=503, detail="AirPlay source not available")
    return source


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
        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Restart completed"),
            "error": result.get("error"),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}",
        }
