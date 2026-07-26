# backend/sources/radio/__init__.py
"""Internet radio audio source via mpv (Family C — active player).

Station browsing through the Radio Browser API, favourites and custom stations,
plus track identification on streams that carry no usable in-band metadata
(Shazam) and cover-art resolution for those that do.
"""
from backend.sources.radio.source import RadioSource

__all__ = ["RadioSource"]
