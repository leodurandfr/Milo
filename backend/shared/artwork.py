# backend/shared/artwork.py
"""Artwork helpers shared by passive-player sources (AirPlay, DLNA)."""

import logging
from io import BytesIO
from typing import Tuple

from PIL import Image


def decode_artwork_dimensions(
    data: bytes, logger: logging.Logger, source_label: str
) -> Tuple[int, int]:
    """Return artwork (width, height) in pixels, (0, 0) on failure.

    Reads the image header only (Pillow is lazy — no full decode), so this
    is a microsecond CPU op on already-in-memory bytes, not blocking I/O.
    On failure we return (0, 0): the frontend treats sub-threshold artwork
    as untrustworthy and falls back to the status card, so a decode error
    safely degrades to "no rich player" rather than showing a bad cover.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            return img.width, img.height
    except Exception as e:
        logger.warning(f"Failed to decode {source_label} artwork dimensions: {e}")
        return 0, 0
