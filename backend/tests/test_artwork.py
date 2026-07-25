# backend/tests/test_artwork.py
"""Unit tests for backend.shared.artwork.decode_artwork_dimensions."""
import logging
from io import BytesIO

from PIL import Image

from backend.shared.artwork import decode_artwork_dimensions


def test_decode_dimensions_reads_real_image_header():
    buf = BytesIO()
    Image.new("RGB", (640, 480)).save(buf, format="PNG")
    logger = logging.getLogger("test.artwork")
    assert decode_artwork_dimensions(buf.getvalue(), logger, "DLNA") == (640, 480)


def test_decode_dimensions_degrades_to_zero_on_garbage():
    # (0, 0) < the rich-display threshold → frontend safely drops to the status card.
    logger = logging.getLogger("test.artwork")
    assert decode_artwork_dimensions(b"not an image", logger, "AirPlay") == (0, 0)
