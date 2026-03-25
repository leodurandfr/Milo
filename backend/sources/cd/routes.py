# backend/sources/cd/routes.py
"""
FastAPI routes for CD audio source.

Provides REST API endpoints for:
- Status: Get current CD source status
- Drive status: Check drive connection and disc presence
- Playback: Play/pause/stop/seek/navigate tracks
- Disc info: Get disc metadata and track list
- Cover art: Serve disc cover images
- Eject: Eject the disc
"""
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.route_helpers import api_error_handler, run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.config.constants import CD_COVERS_DIR
from backend.sources.cd.models import PlayTrackRequest, SeekRequest
from backend.sources.cd.source import CdSource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cd",
    tags=["cd"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Cd")


def setup_cd_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


# === Status Routes ===


@router.get("/status")
async def get_status(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Get current CD source status."""
    try:
        status = await source.status()
        return {"status": "success", **status}
    except Exception as e:
        logger.error(f"Status error: {e}")
        return {
            "status": "error",
            "message": str(e),
            "service_active": False,
            "is_playing": False,
        }


@router.get("/drive-status")
async def get_drive_status(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get CD drive connection and disc presence status.

    Works even when the CD source is not active — used by DockSettings
    to detect whether a drive is plugged in.
    """
    async with api_error_handler("Drive status", logger):
        return {
            "connected": source.drive_connected,
            "disc_present": source.disc_present,
        }


# === Playback Routes ===


@router.post("/play")
async def play_track(
    request: PlayTrackRequest, source: CdSource = Depends(get_source)
) -> Dict[str, Any]:
    """Play a specific track by number (1-based)."""
    return await run_source_command(
        source, "play_track", {"track_number": request.track_number}, "Play track"
    )


@router.post("/pause")
async def pause(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Pause playback."""
    return await run_source_command(source, "pause", {}, "Pause")


@router.post("/resume")
async def resume(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Resume playback."""
    return await run_source_command(source, "resume", {}, "Resume")


@router.post("/next")
async def next_track(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Skip to next track."""
    return await run_source_command(source, "next_track", {}, "Next track")


@router.post("/prev")
async def prev_track(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Skip to previous track."""
    return await run_source_command(source, "prev_track", {}, "Previous track")


@router.post("/seek")
async def seek(
    request: SeekRequest, source: CdSource = Depends(get_source)
) -> Dict[str, Any]:
    """Seek within the current track."""
    return await run_source_command(
        source, "seek", {"position": request.position}, "Seek"
    )


@router.post("/stop")
async def stop_playback(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Stop playback."""
    return await run_source_command(source, "stop_playback", {}, "Stop")


@router.post("/eject")
async def eject(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Eject the disc."""
    return await run_source_command(source, "eject", {}, "Eject")


# === Disc Info Routes ===


@router.get("/tracks")
async def get_tracks(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Get track list for the current disc."""
    return await run_source_command(source, "get_tracks", {}, "Get tracks")


@router.get("/disc-info")
async def get_disc_info(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Get metadata for the current disc."""
    disc = source.current_disc
    if not disc:
        return {
            "status": "success",
            "disc": None,
            "drive_connected": source.drive_connected,
            "disc_present": source.disc_present,
        }
    return {
        "status": "success",
        "disc": disc.model_dump(),
        "drive_connected": source.drive_connected,
        "disc_present": source.disc_present,
    }


# === Cover Art Route ===


@router.get("/cover/{disc_id}")
async def get_cover(disc_id: str, source: CdSource = Depends(get_source)):
    """Serve cover art image for a disc."""
    # Validate disc_id to prevent path traversal
    safe_name = Path(disc_id).name
    if safe_name != disc_id or "/" in disc_id or "\\" in disc_id:
        logger.error(f"Invalid disc_id (path traversal attempt): {disc_id}")
        raise HTTPException(status_code=400, detail="Invalid disc ID")

    cover_path = source.data_service.get_cover_path(disc_id)
    if not cover_path:
        raise HTTPException(status_code=404, detail="Cover not found")

    # Verify the resolved path is inside the covers directory
    resolved = Path(cover_path).resolve()
    if not str(resolved).startswith(str(Path(CD_COVERS_DIR).resolve())):
        logger.error(f"Cover path escapes covers directory: {resolved}")
        raise HTTPException(status_code=400, detail="Invalid disc ID")

    return FileResponse(
        path=cover_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000",
        },
    )
