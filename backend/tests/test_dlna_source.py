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

Artwork dimension decoding itself lives in backend.shared.artwork and is
covered by test_artwork.py.
"""
import asyncio
import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from PIL import Image

from backend.sources.dlna.metadata_reader import DlnaBridge, _to_ms
from backend.sources.dlna.source import DlnaSource


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (600, 600), "navy").save(buf, format="PNG")
    return buf.getvalue()


_PNG = _png()


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


def test_a_track_change_re_dispatches_the_very_same_cover():
    """Two tracks off one album carry the identical art URL. The source drops
    the cover with the track it belonged to, so an URL still deduped against
    the previous track's would leave the second one showing its glyph."""
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Says",
        media_image_url="http://nas/spaces.jpg",
    )
    bridge._dispatch_state()

    bridge._dmr.media_title = "Says (Live)"
    bridge._dispatch_state()

    assert bridge._on_artwork.call_args_list == [
        call("http://nas/spaces.jpg"), call("http://nas/spaces.jpg")
    ]


def test_forgetting_the_last_seen_state_re_emits_all_of_it():
    """What the auto-stop reset needs. GENA resends full state and the bridge
    forwards only what moved, so a consumer that cleared its own copy while the
    renderer kept publishing the same track is never told again."""
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Says", media_artist="Nils Frahm",
        media_image_url="http://nas/spaces.jpg",
    )
    bridge._dispatch_state()

    bridge.forget_last_seen()
    bridge._dispatch_state()

    assert bridge._on_play_state.call_count == 2
    assert bridge._on_metadata.call_count == 2
    assert bridge._on_artwork.call_count == 2


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


@pytest.mark.asyncio
async def test_a_new_track_does_not_inherit_the_previous_cover():
    """The cover is fetched for one track and cached in memory. Kept across a
    track change it is what the full-screen player draws for the whole of the
    next one — and for a track the renderer publishes no art for at all, for
    the rest of the session."""
    src = DlnaSource()
    src._fetch_artwork = AsyncMock(return_value=_PNG)
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork("http://nas/spaces.jpg")
    assert src.metadata["album_art_url"].startswith("/api/dlna/artwork?v=")

    await src._on_metadata_update({"title": "Toilet Brush"})

    assert src.metadata["title"] == "Toilet Brush"
    assert "album_art_url" not in src.metadata
    assert "album_art_width" not in src.metadata
    assert src.get_artwork() is None


@pytest.mark.asyncio
async def test_a_cover_still_in_flight_does_not_land_on_the_next_track():
    """The fetch runs up to 10 s and the bridge dispatches each callback as an
    independent task, so the previous track's cover can arrive after the next
    one is already on screen — and stay there until the track after that,
    since the renderer publishes no second art event for it."""
    src = DlnaSource()

    fetching = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(url):
        fetching.set()
        await release.wait()
        return _PNG

    src._fetch_artwork = slow_fetch
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})

    art = asyncio.create_task(src._on_artwork("http://nas/spaces.jpg"))
    await asyncio.wait_for(fetching.wait(), timeout=1)

    await src._on_metadata_update({"title": "Toilet Brush", "artist": "Nils Frahm", "album": "Spaces"})
    release.set()
    await asyncio.wait_for(art, timeout=1)

    assert src.metadata["title"] == "Toilet Brush"
    assert "album_art_url" not in src.metadata
    assert src.get_artwork() is None


@pytest.mark.asyncio
async def test_the_same_track_again_keeps_its_cover():
    """GENA resends the full DIDL-Lite payload; the bridge dedupes, but the
    source is also reached by its own paths. Clearing on anything but a real
    change would flicker the cover off on every one."""
    src = DlnaSource()
    src._fetch_artwork = AsyncMock(return_value=_PNG)
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork("http://nas/spaces.jpg")

    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})

    assert "album_art_url" in src.metadata


@pytest.mark.asyncio
async def test_auto_stop_lets_the_bridge_say_it_all_again():
    """The idle timeout clears the source's own copy of the track while the
    renderer goes on publishing it. Without forgetting the bridge's last-seen
    state there is nothing left to change, so nothing is re-emitted and the
    resume draws the status card for the rest of the track."""
    src = DlnaSource()
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Says", media_artist="Nils Frahm",
    )
    src._bridge = bridge
    bridge._dispatch_state()

    await src._on_auto_stop()
    bridge._dispatch_state()

    assert bridge._on_metadata.call_count == 2
    assert bridge._on_play_state.call_count == 2


def test_get_artwork_is_none_when_empty():
    assert DlnaSource().get_artwork() is None


def test_get_artwork_returns_data_and_mime():
    src = DlnaSource()
    src._artwork_data = b"bytes"
    src._artwork_mime = "image/png"
    assert src.get_artwork() == (b"bytes", "image/png")


