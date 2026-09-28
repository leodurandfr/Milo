# backend/sources/radio/logos.py
"""
Station logos: every external station image, fetched once, checked, squared, kept.

A station's `favicon` is whatever someone typed into radio-browser's public
directory, and every client draws it through one route, `GET /api/radio/favicon`
— the kiosk, Milo-Mac, Milo-iOS (whose lock screen reads the session artwork,
which is that same route) and the stored favorites. So this is the one place
that decides what a logo is. Measured 2026-09-28 over the directory's 3000 most
played stations, what arrives there is:

- **A Wikimedia thumbnail at a width Wikimedia no longer serves.** Hotlinked
  thumbnails must use a standard width (T414805, https://w.wiki/GHai) and
  everything else answers 400 — France Culture, FIP, France Musique, Mouv' all
  carry `1024px-`. `repair_url` rounds the width up to one that is served.
- **An image in a format one client cannot draw.** iOS draws neither WebP nor
  SVG; Wikimedia hands out WebP to whoever advertises it. Every raster logo is
  therefore stored once as WebP and served as JPEG to a caller that does not
  ask for WebP, as `/api/radio/images/` already does for uploads.
- **Something that is not an image at all.** A page answering 200 with HTML, a
  16 px icon. Re-served from Milō's origin, the first would render foreign HTML
  on `milo.local`; drawn full-screen, the second is a smear. Neither passes:
  the route answers 204 and every client falls back to the station's generated
  avatar.
- **A wide logo.** Every renderer fills a square with `object-fit: cover`,
  which cut the sides off one logo in ten. A logo is padded to a square here,
  once, for all of them.

A dead host used to cost a 5 s timeout on every render, for every client. An
answer that settles the question — a logo, or a server saying there is none —
is now kept on disk; a transport failure is not, since an offline unit would
otherwise remember every logo as missing.
"""
import asyncio
import contextlib
import hashlib
import io
import ipaddress
import logging
import re
import socket
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp
from PIL import Image, ImageStat

from backend.config.constants import RADIO_LOGOS_DIR
from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.shared.persistence import write_bytes_atomically
from backend.sources.radio.data import ImageManager, encode_jpeg, encode_webp, has_alpha

logger = logging.getLogger("source.radio.logos")

# The widths Wikimedia serves to a hotlink; any other answers 400.
WIKIMEDIA_WIDTHS = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)
# Past this a logo only costs bytes: it is downscaled to MAX_DIMENSIONS anyway.
WIKIMEDIA_MAX_WIDTH = 1280
# What an SVG original is rasterized at by Wikimedia on our behalf.
WIKIMEDIA_SVG_WIDTH = 960

_WIKIMEDIA_THUMB = re.compile(
    r"^(https?://upload\.wikimedia\.org/wikipedia/[^/]+/thumb/[0-9a-f]/[0-9a-f]{2}/[^/]+/"
    r"(?:lang[a-z-]+-)?)(\d+)(px-[^/?#]+)$"
)
_WIKIMEDIA_SVG = re.compile(
    r"^(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/([0-9a-f]/[0-9a-f]{2})/([^/?#]+\.svg)$",
    re.IGNORECASE,
)

MAX_REDIRECTS = 3
FETCH_TIMEOUT_S = 5
# Wikimedia answers `429 Retry-After: 1` on a thumbnail it has not rendered
# yet; waiting that second once is what turns it into a logo.
MAX_RETRY_AFTER_S = 2
MAX_BYTES = ImageManager.MAX_FILE_SIZE_BYTES
# Below this a logo upscales into a smear; the generated avatar reads better.
MIN_LOGO_PX = 48
# Decodes, resizes and encodes at once. A search page opens ~40 uncached logos
# together; unbounded, that is every core of the Pi busy with PIL while the
# multiroom stream needs it.
PIL_CONCURRENCY = 2

LOGO_TTL_S = 30 * 24 * 3600
MISS_TTL_S = 6 * 3600

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# A browser-like header set: many station hosts sit behind a WAF (Akamai,
# Cloudflare) that rejects a request carrying only a bare User-Agent. `Accept`
# is the exception to the mimicry: it does not name WebP, so Wikimedia answers
# PNG instead of a format the JPEG rendition would have to undo.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/png,image/jpeg,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def repair_url(url: str) -> str:
    """The URL a logo is actually served at.

    A Wikimedia thumbnail's width is rounded up to the next width Wikimedia
    serves; an SVG original becomes its PNG rendering, which every client can
    draw. Any other URL is returned unchanged.
    """
    url = url.strip()
    thumb = _WIKIMEDIA_THUMB.match(url)
    if thumb:
        wanted = min(int(thumb.group(2)), WIKIMEDIA_MAX_WIDTH)
        width = next(w for w in WIKIMEDIA_WIDTHS if w >= wanted)
        return f"{thumb.group(1)}{width}{thumb.group(3)}"
    svg = _WIKIMEDIA_SVG.match(url)
    if svg:
        base, hashed, name = svg.groups()
        return f"{base}/thumb/{hashed}/{name}/{WIKIMEDIA_SVG_WIDTH}px-{name}.png"
    return url


async def target_allowed(url: str) -> bool:
    """False when `url` is not http(s), or names an address on this LAN.

    The URL comes from a public directory anyone may add to, so without this the
    route is a request-forgery primitive: a station naming
    `http://192.168.1.1/` would make this appliance fetch it. Checked on every
    redirect hop, so a public host cannot bounce the fetch onto the LAN.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, None, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        # `is_global` is the one predicate that covers loopback, RFC1918,
        # link-local and the 100.64/10 carrier-grade NAT range.
        if not address.is_global:
            return False
    return True


class _NoAnswer(Exception):
    """The logo's host could not be asked: refused target, unreachable, timed out.

    Kept apart from a host that answered "no", because only the latter is worth
    remembering — an offline unit would otherwise cache every logo as missing.
    """


def _retry_delay(header: Optional[str]) -> Optional[float]:
    """The wait a 429 asks for, if it is one worth making."""
    try:
        delay = float(header) if header is not None else None
    except ValueError:
        return None
    if delay is None or not 0 <= delay <= MAX_RETRY_AFTER_S:
        return None
    return delay


async def _download(url: str) -> Optional[bytes]:
    """The body the host serves for `url`, or None when it answers without one.

    Raises _NoAnswer when the host could not be asked at all.
    """
    target = url
    hops = 0
    retried = False
    try:
        async with aiohttp.ClientSession() as session:
            while True:
                if not await target_allowed(target):
                    raise _NoAnswer(f"refused target {target}")
                async with session.get(
                    target,
                    timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S),
                    allow_redirects=False,
                    headers=_HEADERS,
                ) as resp:
                    if resp.status in _REDIRECT_STATUSES:
                        location = resp.headers.get("Location")
                        hops += 1
                        if not location or hops > MAX_REDIRECTS:
                            return None
                        target = urljoin(target, location)
                        continue
                    if resp.status == 429 and not retried:
                        delay = _retry_delay(resp.headers.get("Retry-After"))
                        if delay is not None:
                            retried = True
                            await asyncio.sleep(delay)
                            continue
                    if resp.status == 429 or resp.status >= 500:
                        # Busy or broken for now is not "no logo": asking
                        # again later may work.
                        raise _NoAnswer(f"HTTP {resp.status}")
                    if resp.status != 200:
                        return None
                    # Chunk by chunk to the end: `content.read(n)` answers with
                    # whatever has arrived so far, which truncated real logos.
                    body = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        body += chunk
                        if len(body) > MAX_BYTES:
                            return None
                    return bytes(body)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise _NoAnswer(str(e) or type(e).__name__) from e


def _is_svg(body: bytes) -> bool:
    """An SVG document, whatever precedes its root: an XML declaration, a
    generator comment, a doctype. A page that merely inlines one has `<html`."""
    head = body[:4096].lower()
    return b"<svg" in head and b"<html" not in head


def _border_fill(image: Image.Image) -> Tuple[int, ...]:
    """What to pad a logo with so the padding reads as its own background.

    Transparent when any border pixel is — the logo then sits on whatever the
    client draws behind it. Otherwise the border's mean color, which extends a
    flat background seamlessly.
    """
    w, h = image.size
    strips = [
        image.crop((0, 0, w, 1)), image.crop((0, h - 1, w, h)),
        image.crop((0, 0, 1, h)), image.crop((w - 1, 0, w, h)),
    ]
    stats = [(ImageStat.Stat(s), s.width * s.height) for s in strips]
    if image.mode == "RGBA" and min(st.extrema[3][0] for st, _ in stats) < 250:
        return (0, 0, 0, 0)
    total = sum(n for _, n in stats)
    rgb = tuple(round(sum(st.mean[band] * n for st, n in stats) / total) for band in range(3))
    return rgb + (255,) if image.mode == "RGBA" else rgb


def _square(image: Image.Image) -> Image.Image:
    w, h = image.size
    if w == h:
        return image
    image = image.convert("RGBA" if has_alpha(image) else "RGB")
    side = max(w, h)
    canvas = Image.new(image.mode, (side, side), _border_fill(image))
    canvas.paste(image, ((side - w) // 2, (side - h) // 2))
    return canvas


def _process(body: bytes) -> Optional[Tuple[bytes, str]]:
    """The body as a stored logo — (bytes, suffix) — or None if it is not one.

    Blocking (PIL): call via `to_thread`.
    """
    if _is_svg(body):
        return body, ".svg"
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.load()
            if min(image.size) < MIN_LOGO_PX:
                return None
            image.thumbnail(ImageManager.MAX_DIMENSIONS, Image.Resampling.LANCZOS)
            return encode_webp(_square(image)), ".webp"
    except Exception as e:
        # Bytes from any host on the internet: PIL's decoders answer a malformed
        # file with OSError, EOFError, SyntaxError, struct.error, IndexError…
        # Whatever it is, it is not a logo — and it must not become a 500.
        logger.debug("Undecodable station logo: %s: %s", type(e).__name__, e)
        return None


class StationLogos:
    """The disk cache behind `GET /api/radio/favicon`, keyed by repaired URL."""

    LOGOS_DIR: Path = RADIO_LOGOS_DIR

    def __init__(self) -> None:
        self.logger = logger
        # One fetch per logo however many tiles ask for it at once.
        self._inflight: Dict[str, asyncio.Task] = {}
        self._bg = BackgroundTaskSet(logger, "radio_logos")
        self._pil = asyncio.Semaphore(PIL_CONCURRENCY)

    @handle_errors(default=None)
    async def initialize(self) -> None:
        """Create the directory and drop what has expired.

        A cache that cannot sweep is no reason for radio not to start: the
        fetch path creates the directory again on its first write.
        """
        await asyncio.to_thread(self._sweep)

    def _sweep(self) -> None:
        self.LOGOS_DIR.mkdir(parents=True, exist_ok=True)
        for path in self.LOGOS_DIR.iterdir():
            ttl = MISS_TTL_S if path.suffix == ".miss" else LOGO_TTL_S
            if path.suffix == ".tmp" or not self._fresh(path, ttl):
                path.unlink(missing_ok=True)

    async def cleanup(self) -> None:
        """Cancel the fetches in flight (backend teardown)."""
        await self._bg.cancel_all()

    @staticmethod
    def _fresh(path: Path, ttl: float) -> bool:
        try:
            return time.time() - path.stat().st_mtime < ttl
        except FileNotFoundError:
            return False

    async def get(self, url: str, accept: str) -> Optional[Tuple[bytes, str]]:
        """The logo for `url` as (bytes, media type) the caller can draw, or None.

        `accept` is the caller's own Accept header: a browser names WebP and
        SVG, iOS's URLSession sends `*/*` and draws neither.
        """
        repaired = repair_url(url)
        key = hashlib.sha256(repaired.encode()).hexdigest()
        entry = await self._entry(key, repaired)
        if entry is None:
            return None
        data, suffix = entry
        if suffix == ".svg":
            return (data, "image/svg+xml") if "image/svg+xml" in accept else None
        if "image/webp" in accept:
            return data, "image/webp"
        return await self._jpeg(key, data), "image/jpeg"

    async def _entry(self, key: str, url: str) -> Optional[Tuple[bytes, str]]:
        for suffix in (".webp", ".svg"):
            path = self.LOGOS_DIR / f"{key}{suffix}"
            if self._fresh(path, LOGO_TTL_S):
                try:
                    async with aiofiles.open(path, "rb") as f:
                        return await f.read(), suffix
                except FileNotFoundError:
                    break  # expired and swept under us: fetch it again
        if self._fresh(self.LOGOS_DIR / f"{key}.miss", MISS_TTL_S):
            return None

        task = self._inflight.get(key)
        if task is None:
            task = self._bg.spawn(self._resolve(key, url), label="fetch")
            if task is None:
                return None
            self._inflight[key] = task
            task.add_done_callback(lambda _, k=key: self._inflight.pop(k, None))
        # Shielded: a client that gives up leaves the fetch to finish for the next.
        return await asyncio.shield(task)

    async def _resolve(self, key: str, url: str) -> Optional[Tuple[bytes, str]]:
        try:
            body = await _download(url)
        except _NoAnswer as e:
            logger.debug("Station logo %s not reachable, not remembered: %s", url, e)
            return None

        entry = None
        if body:
            async with self._pil:
                entry = await asyncio.to_thread(_process, body)
        try:
            if entry is None:
                logger.debug("Station logo %s is not a usable image", url)
                await write_bytes_atomically(self.LOGOS_DIR / f"{key}.miss", b"")
            else:
                data, suffix = entry
                await write_bytes_atomically(self.LOGOS_DIR / f"{key}{suffix}", data)
                # A rendition of the previous version must not outlive it.
                (self.LOGOS_DIR / f"{key}.jpg").unlink(missing_ok=True)
                (self.LOGOS_DIR / f"{key}.miss").unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not cache station logo %s: %s", url, e)
        return entry

    async def _jpeg(self, key: str, webp: bytes) -> bytes:
        path = self.LOGOS_DIR / f"{key}.jpg"
        with contextlib.suppress(FileNotFoundError):
            async with aiofiles.open(path, "rb") as f:
                return await f.read()

        def _convert() -> bytes:
            with Image.open(io.BytesIO(webp)) as image:
                return encode_jpeg(image)

        async with self._pil:
            jpeg = await asyncio.to_thread(_convert)
        try:
            await write_bytes_atomically(path, jpeg)
        except OSError as e:
            logger.warning("Could not keep JPEG rendition %s: %s", path.name, e)
        return jpeg
