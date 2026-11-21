"""
The one way a satellite downloads a release asset: streamed to disk and checked
against the digest the server sent, from its dependencies.env.

The check here refuses a bad file before the service it replaces is stopped.
It is not the last one: the root installer checks its own copy again, because
this file then sits where any process running as milo-client can rewrite it.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_S = 300

# What the installers read, in a digest's place, as "the server's manifest
# declares none for this file": a version past it.
NO_DIGEST = "-"


async def download_release_asset(url: str, dest: Path, sha256: Optional[str]) -> None:
    """Stream `url` to `dest` and check it against `sha256`.

    Raises on anything but a 200 and on a file that is not the one declared.
    `sha256` is None only when the server names a version its manifest does not
    declare, and that is said in the log.
    """
    name = url.rsplit("/", 1)[-1]
    digest = hashlib.sha256()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)) as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"Download failed: HTTP {response.status}")
            async with aiofiles.open(dest, "wb") as f:
                async for chunk in response.content.iter_chunked(8192):
                    digest.update(chunk)
                    await f.write(chunk)
    actual = digest.hexdigest()
    if sha256 is None:
        logger.warning(f"{name}: no declared digest (a version past the server's manifest), sha256 {actual}")
    elif actual != sha256:
        raise RuntimeError(f"{name}: checksum mismatch, declared {sha256}, downloaded {actual}")
