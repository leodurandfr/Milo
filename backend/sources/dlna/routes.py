# backend/sources/dlna/routes.py
"""
FastAPI routes for the DLNA / UPnP renderer audio source.

Provides REST API endpoints for:
- Artwork: Serve current album artwork as binary image (fetched from the DMS,
  cached in memory by the source).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from backend.api.source_dependency import make_source_dependency
from backend.sources.dlna.source import DlnaSource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dlna",
    tags=["dlna"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("DLNA")


def setup_dlna_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@router.get("/artwork")
async def get_artwork(source: DlnaSource = Depends(get_source)) -> Response:
    """Serve current DLNA artwork as binary image."""
    result = source.get_artwork()
    if not result:
        # Expected, not an error: the DMS may expose no cover for the track,
        # and the frontend falls back to its own placeholder. Kept below ERROR
        # so it never reaches the WebSocketLogHandler banner.
        logger.debug("No DLNA artwork available")
        raise HTTPException(status_code=404, detail="No artwork available")

    data, mime_type = result
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
