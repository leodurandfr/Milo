# backend/api/lyrics.py
"""Lyrics lookup route (transverse Lyrics app).

GET /api/lyrics?artist=&title=&album=&duration= — resolves synced/plain lyrics
for the now-playing track via LyricsService (LRCLIB + disk cache). `duration` is
milliseconds (as carried in playback metadata). A miss is a normal 200 with
found=false — not an error — so the Lyrics app shows a clean empty state.
"""
import logging
from typing import Optional

from fastapi import APIRouter

from backend.api.route_helpers import api_error_handler

logger = logging.getLogger(__name__)


def create_lyrics_router(lyrics_service):
    router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])

    @router.get("")
    async def get_lyrics(
        artist: str,
        title: str,
        album: Optional[str] = None,
        duration: Optional[int] = None,
    ):
        async with api_error_handler("Lyrics lookup", logger):
            result = await lyrics_service.get_lyrics(
                artist=artist, title=title, album=album, duration_ms=duration
            )
            return {"status": "success", **result}

    return router
