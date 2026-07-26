# backend/sources/cd/routes.py
"""
FastAPI routes for CD audio source.

Cover art only: it is the one thing the CD source exposes that a command can't
deliver. Every playback command (play_track/pause/resume/next/prev/seek/eject)
flows through `/api/audio/control/cd` (see backend/api/audio.py).
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.source_dependency import make_source_dependency
from backend.config.constants import CD_COVERS_DIR
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
        # Expected, not an error: the Cover Art Archive has no image for plenty
        # of discs, and the frontend falls back to its own placeholder. Kept
        # below ERROR so it never reaches the WebSocketLogHandler banner.
        logger.debug("Cover not available for disc: %s", disc_id)
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
