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

    source = SpotifySource(event_bus, config)
    setup_spotify_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
import aiohttp
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Dict, Any, Callable, Optional

from backend.features.spotify.source import SpotifySource

router = APIRouter(
    prefix="/spotify",
    tags=["spotify"],
    responses={404: {"description": "Not found"}},
)

# Source provider function
_source_provider: Optional[Callable[[], SpotifySource]] = None


def setup_spotify_routes(source_provider: Callable[[], SpotifySource]) -> APIRouter:
    """
    Configure routes with source provider.

    Args:
        source_provider: Function returning SpotifySource instance

    Returns:
        Configured router
    """
    global _source_provider
    _source_provider = source_provider
    return router


def get_source() -> SpotifySource:
    """Dependency to get SpotifySource instance."""
    if _source_provider is None:
        raise HTTPException(status_code=503, detail="Spotify source not configured")
    source = _source_provider()
    if source is None:
        raise HTTPException(status_code=503, detail="Spotify source not available")
    return source


@router.get("/status")
async def get_status(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get current Spotify source status with metadata.

    Returns:
        Status dict with state, device info, and metadata
    """
    try:
        # Refresh metadata if session is active
        if source.has_active_session:
            await source._refresh_metadata()

        status = await source.status()

        return {
            "status": "ok",
            "state": status.get("state", "unknown"),
            "service_active": status.get("service_active", False),
            "device_connected": status.get("device_connected", False),
            "is_playing": status.get("is_playing", False),
            "ws_connected": status.get("ws_connected", False),
            "metadata": status.get("metadata", {}),
            "auto_disconnect_config": status.get("auto_disconnect_config", {})
        }

    except Exception as e:
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

    Returns:
        Status with metadata from go-librespot
    """
    try:
        if not source.api_url:
            return {
                "status": "error",
                "message": "API URL not configured",
                "source": "config_error"
            }

        api_url = f"{source.api_url}/status"
        timeout = aiohttp.ClientTimeout(total=3)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    fresh_data = await response.json()

                    transformed_metadata = {}

                    if fresh_data.get("track"):
                        track = fresh_data["track"]
                        transformed_metadata = {
                            "title": track.get("name"),
                            "artist": ", ".join(track.get("artist_names", [])) if track.get("artist_names") else None,
                            "album": track.get("album_name"),
                            "album_art_url": track.get("album_cover_url"),
                            "duration": track.get("duration", 0),
                            "position": track.get("position", 0),
                            "uri": track.get("uri"),
                        }

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
                else:
                    return {
                        "status": "error",
                        "message": f"go-librespot API returned status {response.status}",
                        "source": "go-librespot-api"
                    }

    except aiohttp.ClientConnectorError:
        return {
            "status": "error",
            "message": "Cannot connect to go-librespot API - server may not be running",
            "source": "connection_error"
        }
    except TimeoutError:
        return {
            "status": "error",
            "message": "Timeout connecting to go-librespot API",
            "source": "timeout_error"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}",
            "source": "unexpected_error"
        }


@router.post("/restart")
async def restart_service(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """
    Restart the go-librespot service.

    Returns:
        Result of restart operation
    """
    try:
        result = await source.command("restart_service", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Restart completed"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }


@router.post("/connect")
async def refresh_connection(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """
    Refresh metadata connection to go-librespot.

    Returns:
        Result with refreshed metadata
    """
    try:
        result = await source.command("refresh_metadata", {})

        if result.get("success"):
            return {
                "status": "success",
                "message": "Connection refreshed successfully",
                "metadata": result.get("metadata", {})
            }
        else:
            return {
                "status": "warning",
                "message": f"Refresh issue: {result.get('error')}",
                "metadata": {}
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection refresh error: {str(e)}"
        }


@router.post("/command/{command}")
async def send_command(
    command: str,
    source: SpotifySource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Send playback command to Spotify.

    Args:
        command: Command name (play, pause, next, prev, etc.)

    Returns:
        Result of command execution
    """
    try:
        result = await source.command(command, {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", f"Command {command} executed"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Command error: {str(e)}"
        }


@router.post("/seek")
async def seek_to_position(
    position_ms: int = Query(..., description="Position in milliseconds"),
    source: SpotifySource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Seek to position in current track.

    Args:
        position_ms: Target position in milliseconds

    Returns:
        Result of seek operation
    """
    try:
        result = await source.command("seek", {"position_ms": position_ms})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", f"Seeked to {position_ms}ms"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Seek error: {str(e)}"
        }


@router.get("/info")
async def get_info(source: SpotifySource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get Spotify source configuration information.

    Returns:
        Configuration and service details
    """
    try:
        status = await source.status()

        return {
            "status": "ok",
            "source_id": source.source_id,
            "service_name": source.service_name,
            "api_url": source.api_url,
            "service_active": status.get("service_active", False),
            "auto_disconnect_config": status.get("auto_disconnect_config", {})
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }
