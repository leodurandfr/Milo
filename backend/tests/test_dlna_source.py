# backend/tests/test_dlna_source.py
"""
Unit tests for the DLNA source and its UPnP control-point bridge.

Covers the pure, non-trivial logic that has no other guard:
- DlnaBridge._to_ms: async-upnp-client position/duration → milliseconds, across
  the int/float-seconds shape (0.47) and the tolerated timedelta.
- DlnaBridge._dispatch_state: GENA resends the FULL state on every event, so the
  bridge must emit each field only when it actually changed (title/artist/album,
  transport state, artwork).
- DlnaSource: passive (rejects every command) + artwork helpers.
"""
import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from PIL import Image

from backend.sources.dlna.metadata_reader import DlnaBridge, _to_ms
from backend.sources.dlna.source import DlnaSource


# === _to_ms ==================================================================

@pytest.mark.parametrize("value,expected", [
    (None, None),
    (0, 0),                                  # position 0 is a real value, not "missing"
    (5, 5000),
    (5.5, 5500),
    (datetime.timedelta(seconds=3), 3000),   # tolerated for cross-version robustness
    ("not-a-number", None),                  # no total_seconds() → None, not a crash
])
def test_to_ms(value, expected):
    assert _to_ms(value) == expected


# === _dispatch_state change-detection ========================================

class _FakeBg:
    """Stand-in for BackgroundTaskSet: _dispatch_state hands us the callback
    coroutines via spawn(); close them so the AsyncMock callbacks record their
    calls without a running loop (and without 'coroutine never awaited')."""

    def spawn(self, coro, label=None):
        coro.close()


def _make_dmr(**overrides):
    """A minimal stand-in for DmrDevice exposing only the properties the bridge
    reads (all default to an idle/empty renderer)."""
    base = dict(
        transport_state="STOPPED",
        media_title="",
        media_artist="",
        media_album_artist="",
        media_album_name="",
        media_image_url=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_bridge():
    bridge = DlnaBridge(
        description_url="http://127.0.0.1:49494/description.xml",
        on_metadata=AsyncMock(),
        on_play_state=AsyncMock(),
        on_artwork=AsyncMock(),
        on_progress=AsyncMock(),
        on_connection=AsyncMock(),
    )
    bridge._bg = _FakeBg()
    return bridge


def test_dispatch_emits_each_field_once_then_stays_silent():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING",
        media_title="Song", media_artist="Artist", media_album_name="Album",
        media_image_url="http://nas/art.jpg",
    )

    bridge._dispatch_state()
    bridge._on_play_state.assert_called_once_with("play")
    bridge._on_metadata.assert_called_once_with(
        {"title": "Song", "artist": "Artist", "album": "Album"})
    bridge._on_artwork.assert_called_once_with("http://nas/art.jpg")

    # GENA resends the identical full state — nothing changed, nothing re-emits.
    bridge._dispatch_state()
    bridge._on_play_state.assert_called_once()
    bridge._on_metadata.assert_called_once()
    bridge._on_artwork.assert_called_once()


def test_dispatch_reemits_only_the_changed_transport_state():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(transport_state="PLAYING", media_title="Song")
    bridge._dispatch_state()

    bridge._dmr.transport_state = "PAUSED_PLAYBACK"
    bridge._dispatch_state()

    assert bridge._on_play_state.call_args_list == [call("play"), call("pause")]
    bridge._on_metadata.assert_called_once()  # unchanged → not re-emitted


def test_dispatch_maps_stopped_and_no_media_to_stop():
    for state in ("STOPPED", "NO_MEDIA_PRESENT"):
        bridge = _make_bridge()
        bridge._dmr = _make_dmr(transport_state=state)
        bridge._dispatch_state()
        bridge._on_play_state.assert_called_once_with("stop")


def test_dispatch_ignores_unmapped_transport_state():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(transport_state="TRANSITIONING")
    bridge._dispatch_state()
    bridge._on_play_state.assert_not_called()


def test_dispatch_skips_fully_empty_metadata():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(transport_state="PLAYING")  # title/artist/album all ""
    bridge._dispatch_state()
    bridge._on_metadata.assert_not_called()


def test_dispatch_falls_back_to_album_artist():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Song",
        media_artist="", media_album_artist="AlbumArtist",
    )
    bridge._dispatch_state()
    bridge._on_metadata.assert_called_once_with(
        {"title": "Song", "artist": "AlbumArtist", "album": ""})


# === DlnaSource (Family B, passive) ==========================================

@pytest.mark.asyncio
async def test_source_rejects_every_command():
    """No COMMANDS registered — playback is driven by the external sender."""
    src = DlnaSource()
    result = await src.command("play", {})
    assert result["success"] is False


def test_get_artwork_is_none_when_empty():
    assert DlnaSource().get_artwork() is None


def test_get_artwork_returns_data_and_mime():
    src = DlnaSource()
    src._artwork_data = b"bytes"
    src._artwork_mime = "image/png"
    assert src.get_artwork() == (b"bytes", "image/png")


def test_decode_dimensions_reads_real_image_header():
    buf = BytesIO()
    Image.new("RGB", (640, 480)).save(buf, format="PNG")
    assert DlnaSource()._decode_artwork_dimensions(buf.getvalue()) == (640, 480)


def test_decode_dimensions_degrades_to_zero_on_garbage():
    # (0, 0) < the rich-display threshold → frontend safely drops to the status card.
    assert DlnaSource()._decode_artwork_dimensions(b"not an image") == (0, 0)
