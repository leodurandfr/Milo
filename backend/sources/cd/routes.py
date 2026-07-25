# backend/sources/cd/routes.py
"""
FastAPI routes for CD audio source.

Provides REST API endpoints for:
- Playback: Play a specific track by number (typed)
- Cover art: Serve disc cover images
- Eject: Eject the disc

Generic playback commands (pause/resume/next/prev/seek) flow through
`/api/audio/control/cd` (see backend/api/audio.py).
"""
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.route_helpers import api_error_handler, run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.config.constants import CD_COVERS_DIR
from backend.sources.cd.models import PlayTrackRequest
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


# === Playback Routes ===


@router.post("/play")
async def play_track(
    request: PlayTrackRequest, source: CdSource = Depends(get_source)
) -> Dict[str, Any]:
    """Play a specific track by number (1-based)."""
    return await run_source_command(
        source, "play_track", {"track_number": request.track_number}, "Play track"
    )


@router.post("/eject")
async def eject(source: CdSource = Depends(get_source)) -> Dict[str, Any]:
    """Eject the disc."""
    return await run_source_command(source, "eject", {}, "Eject")


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
