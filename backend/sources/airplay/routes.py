# backend/sources/airplay/routes.py
"""
FastAPI routes for AirPlay 2 audio source.

Provides REST API endpoints for:
- Artwork: Serve current album artwork as binary image
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

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
