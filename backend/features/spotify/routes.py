# backend/features/spotify/routes.py
"""
FastAPI routes for Spotify audio source.

Provides REST API endpoints for:
- Status: Get current Spotify source status with metadata
- Restart: Restart go-librespot service
- Connect: Refresh metadata connection
- Fresh Status: Direct status from go-librespot API

Usage:
    from backend.features.spotify import router, SpotifySource

    source = SpotifySource(config=config)
    setup_spotify_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
import logging

import aiohttp
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Dict, Any

from backend.api.route_helpers import run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.features.spotify.source import SpotifySource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/spotify",
    tags=["spotify"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Spotify")


def setup_spotify_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@router.get("/status")
async def get_status(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """Get current Spotify source status with metadata."""
    try:
        if source.has_active_session:
            await source._refresh_metadata()

        status = await source.status()
        return {"status": "success", **status}

    except Exception as e:
        logger.error("Failed to get Spotify status: %s", e)
        return {
            "status": "error",
            "message": str(e),
            "state": "error",
            "metadata": {},
            "is_playing": False,
            "device_connected": False
        }


@router.get("/fresh-status")
async def get_fresh_status(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get fresh status directly from go-librespot API.

    This endpoint calls go-librespot API server-side to avoid CORS issues.
    """
    if not source.api_url:
        raise HTTPException(status_code=500, detail="API URL not configured")

    try:
        api_url = f"{source.api_url}/status"
        timeout = aiohttp.ClientTimeout(total=3)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"go-librespot API returned status {response.status}"
                    )

                fresh_data = await response.json()

                transformed_metadata = {}

                if fresh_data.get("track"):
                    transformed_metadata = SpotifySource.transform_track_metadata(fresh_data["track"])

                transformed_metadata["is_playing"] = (
                    not fresh_data.get("paused", True) and
                    not fresh_data.get("stopped", True)
                )

                return {
                    "status": "success",
                    "fresh_metadata": transformed_metadata,
                    "device_connected": bool(fresh_data.get("track")),
                    "raw_data": fresh_data,
                    "source": "go-librespot-api"
                }

    except HTTPException:
        raise
    except aiohttp.ClientConnectorError:
        logger.error("Cannot connect to go-librespot API")
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to go-librespot API - server may not be running"
        )
    except TimeoutError:
        logger.error("Timeout connecting to go-librespot API")
        raise HTTPException(
            status_code=504,
            detail="Timeout connecting to go-librespot API"
        )
    except Exception as e:
        logger.error("Unexpected error fetching fresh status: %s", e)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.post("/restart")
async def restart_service(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """Restart the go-librespot service."""
    return await run_source_command(source, "restart_service", {}, "Restart")


@router.post("/connect")
async def refresh_connection(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """Refresh metadata connection to go-librespot."""
    return await run_source_command(source, "refresh_metadata", {}, "Connection refresh")


@router.post("/command/{command}")
async def send_command(
    command: str,
    source: SpotifySource = Depends(get_source)
) -> Dict[str, Any]:
    """Send playback command to Spotify."""
    return await run_source_command(source, command, {}, f"Command {command}")


@router.post("/seek")
async def seek_to_position(
    position_ms: int = Query(..., description="Position in milliseconds"),
    source: SpotifySource = Depends(get_source)
) -> Dict[str, Any]:
    """Seek to position in current track."""
    return await run_source_command(source, "seek", {"position_ms": position_ms}, "Seek")


@router.get("/info")
async def get_info(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """Get Spotify source configuration information."""
    try:
        status = await source.status()

        return {
            "status": "success",
            "source_id": source.source_id,
            "service_name": source.service_name,
            "api_url": source.api_url,
            "service_active": status.get("service_active", False),
            "auto_disconnect_config": status.get("auto_disconnect_config", {})
        }

    except Exception as e:
        logger.error("Failed to get Spotify info: %s", e)
        raise HTTPException(status_code=500, detail=f"Info error: {str(e)}")
