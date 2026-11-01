# backend/sources/airplay/routes.py
"""
FastAPI routes for AirPlay 2 audio source.

Provides REST API endpoints for:
- Artwork: Serve current album artwork as binary image, immutable only under
  the `?v=<hash>` that names it
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.api.responses import image_responses
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


@router.get("/artwork", response_class=Response, responses=image_responses("image/jpeg", "image/png"))
async def get_artwork(
    v: Annotated[Optional[str], Query(
        description="The cover's content hash, as the published URL carries it")] = None,
    source: AirPlaySource = Depends(get_source),
) -> Response:
    """Serve current AirPlay artwork as binary image.

    `v` is the content hash the published URL carries: only the cover it names
    is served, and cached for good. A superseded or unknown `v` is a 404, never
    the current cover, which a client would file under the old URL forever.
    Without `v` the answer names whatever is on air and is never cached.
    """
    result = source.get_artwork()
    if not result:
        # Expected, not an error: plenty of senders push no cover at all, and
        # the frontend falls back to its own placeholder. Kept below ERROR so
        # it never reaches the WebSocketLogHandler banner.
        logger.debug("No AirPlay artwork available")
        raise HTTPException(status_code=404, detail="No artwork available")

    if v is not None and v != source.artwork_version:
        # Expected too: a client that read the state just before a track change.
        logger.debug(f"AirPlay artwork version {v} superseded")
        raise HTTPException(status_code=404, detail="Artwork version superseded")

    data, mime_type = result
    cache = "private, max-age=31536000, immutable" if v is not None else "no-store"
    return Response(content=data, media_type=mime_type, headers={"Cache-Control": cache})
