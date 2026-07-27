# backend/api/lyrics.py
"""Lyrics lookup route (transverse Lyrics app).

GET /api/lyrics?artist=&title=&album=&duration= — resolves synced/plain lyrics
for the now-playing track via LyricsService (LRCLIB + disk cache). `duration` is
milliseconds (as carried in playback metadata). A miss is a normal 200 with
status=success + found=false — not an error — so the Lyrics app shows a clean
empty state.

An unreachable LRCLIB is a 200 with status=error (resilience pattern): the app
shows that same empty state, but must not cache it as a genuine "no lyrics" —
the service deliberately caches nothing in that case so a reopen retries.
"""
import logging
from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter

from backend.api.route_helpers import api_error_handler
from backend.core.lyrics import LyricsUnavailable

if TYPE_CHECKING:
    from backend.core.lyrics.service import LyricsService


logger = logging.getLogger(__name__)


def create_lyrics_router(lyrics_service: "LyricsService"):
    router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])

    @router.get("")
    async def get_lyrics(
        artist: str,
        title: str,
        album: Optional[str] = None,
        duration: Optional[int] = None,
    ):
        async with api_error_handler("Lyrics lookup", logger):
            try:
                result = await lyrics_service.get_lyrics(
                    artist=artist, title=title, album=album, duration_ms=duration
                )
            except LyricsUnavailable:
                logger.warning("Lyrics lookup unavailable for %s - %s", artist, title)
                return {
                    "status": "error",
                    "found": False,
                    "synced": None,
                    "plain": None,
                }
            return {"status": "success", **result}

    return router
