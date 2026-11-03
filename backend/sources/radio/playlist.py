# backend/sources/radio/playlist.py
"""
A station URL that is a playlist file, read for the stream it lists.

A custom station keeps its URL as the user typed it, and a station's site
often hands out a `.pls` or `.m3u` (SomaFM, Radio Paradise, most Icecast and
Shoutcast servers). Handed to mpv as is, with the radio unit's
`reconnect_at_eof=1`, a playlist served without Range is fetched again at every
EOF instead of being expanded — 54 795 GETs in 40 s, measured, and never a
sound. So a playlist is read here, once, at tune time, and mpv is handed the
stream it lists. HLS (`.m3u8`) is a stream mpv plays itself, not one of these.
"""
import asyncio
import html
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("source.radio.playlist")

PLAYLIST_SUFFIXES = (".pls", ".m3u", ".asx", ".xspf")
FETCH_TIMEOUT_S = 5
MAX_BYTES = 64 * 1024

_PLS_ENTRY = re.compile(r"^\s*file\d+\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_XML_ENTRY = re.compile(
    r"<ref\s[^>]*?href\s*=\s*[\"']([^\"']+)[\"']|<location>\s*([^<\s]+)\s*</location>",
    re.IGNORECASE,
)


def is_playlist_url(url: str) -> bool:
    """`url`'s path names a playlist file (its query and fragment aside)."""
    return urlparse(url).path.lower().endswith(PLAYLIST_SUFFIXES)


def _entries(text: str) -> List[str]:
    pls = _PLS_ENTRY.findall(text)
    if pls:
        return pls
    xml = [html.unescape(ref or location) for ref, location in _XML_ENTRY.findall(text)]
    if xml:
        return xml
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def parse_playlist(text: str) -> Optional[str]:
    """The first http(s) stream a `.pls`, `.m3u`, `.asx` or `.xspf` lists, or
    None. An entry that is itself a playlist is passed over: mpv would loop on
    it. So is an HLS playlist served under a `.m3u` name, whose entries are
    segments, and which mpv plays as HLS only when its URL says `.m3u8`."""
    if "#EXT-X-" in text:
        return None
    for entry in _entries(text):
        parsed = urlparse(entry)
        if parsed.scheme.lower() in ("http", "https") and parsed.netloc and not is_playlist_url(entry):
            return entry
    return None


async def resolve_stream_url(url: str) -> Optional[str]:
    """The stream the playlist at `url` lists, or None when it cannot be read
    or lists none."""
    shown = url.split("?", 1)[0]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Playlist %s answered HTTP %s", shown, resp.status)
                    return None
                # Chunk by chunk: `content.read(n)` answers with whatever has
                # arrived so far.
                body = bytearray()
                async for chunk in resp.content.iter_chunked(MAX_BYTES):
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("Playlist %s could not be fetched: %s", shown, str(e) or type(e).__name__)
        return None
    stream = parse_playlist(body[:MAX_BYTES].decode("utf-8-sig", errors="replace"))
    if stream is None:
        logger.warning("Playlist %s lists no http(s) stream mpv can play", shown)
    return stream
