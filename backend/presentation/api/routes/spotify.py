# backend/presentation/api/routes/spotify.py
"""
API routes for Spotify plugin (uses go-librespot internally)
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
import subprocess
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from backend.domain.audio_state import PluginState

router = APIRouter(
    prefix="/spotify",
    tags=["spotify"],
    responses={404: {"description": "Not found"}},
)

spotify_plugin_dependency = None

def setup_spotify_routes(plugin_provider):
    """
    Configures Spotify routes with plugin reference

    Args:
        plugin_provider: Function that returns Spotify plugin instance
    """
    global spotify_plugin_dependency
    spotify_plugin_dependency = plugin_provider
    return router

def get_spotify_plugin():
    """Dependency to get Spotify plugin"""
    if spotify_plugin_dependency is None:
        raise HTTPException(
            status_code=500,
            detail="Spotify plugin not initialized. Call setup_spotify_routes first."
        )
    return spotify_plugin_dependency()

@router.get("/fresh-status")
async def get_fresh_spotify_status():
    """
    Gets fresh status directly from go-librespot API
    This endpoint calls go-librespot API server-side to avoid CORS issues
    """
    try:
        api_url = "http://localhost:3678/status"

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

                    transformed_metadata["is_playing"] = not fresh_data.get("paused", True) and not fresh_data.get("stopped", True)

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
    except asyncio.TimeoutError:
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

@router.get("/status")
async def get_spotify_status(plugin = Depends(get_spotify_plugin)):
    """Gets current Spotify status with all metadata"""
    try:
        if plugin.session:
            await plugin._refresh_metadata()

        status = await plugin.get_status()

        return {
            "status": "ok",
            "is_active": plugin.current_state == PluginState.CONNECTED,
            "plugin_state": plugin.current_state.value,
            "device_connected": status.get("device_connected", False),
            "is_playing": status.get("is_playing", False),
            "metadata": status.get("metadata", {}),
            "ws_connected": status.get("ws_connected", False),
            "auto_disconnect_config": status.get("auto_disconnect_config", {})
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "plugin_state": plugin.current_state.value if plugin else "unknown",
            "metadata": {},
            "is_playing": False,
            "device_connected": False,
            "ws_connected": False,
            "auto_disconnect_config": {}
        }

@router.post("/connect")
async def restart_spotify_connection(plugin = Depends(get_spotify_plugin)):
    """Restarts connection with go-librespot"""
    try:
        result = await plugin.handle_command("refresh_metadata", {})

        if result.get("success"):
            return {
                "status": "success",
                "message": "Connection to go-librespot restarted successfully",
                "details": result
            }
        else:
            return {
                "status": "warning",
                "message": f"Problem refreshing metadata: {result.get('error')}",
                "details": result
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection restart error: {str(e)}"
        }

@router.post("/restart")
async def restart_spotify_service(plugin = Depends(get_spotify_plugin)):
    """Completely restarts go-librespot process"""
    try:
        result = await plugin.handle_command("restart", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Restart completed"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}"
        }

@router.post("/force-disconnect")
async def force_spotify_disconnect(plugin = Depends(get_spotify_plugin)):
    """Forces sending of disconnect event for Spotify"""
    try:
        result = await plugin.handle_command("force_disconnect", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Forced disconnect"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Disconnect event error: {str(e)}"
        }

@router.get("/logs")
async def get_spotify_logs(
    lines: int = Query(20, gt=0, le=200),
    plugin = Depends(get_spotify_plugin)
):
    """Gets last lines of go-librespot logs"""
    try:
        process_info = await plugin.get_process_info()

        if not process_info.get("running"):
            return {
                "status": "error",
                "message": "The go-librespot process is not running"
            }

        return {
            "status": "warning",
            "message": "Log retrieval not yet implemented in new structure",
            "process_info": process_info
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}"
        }
