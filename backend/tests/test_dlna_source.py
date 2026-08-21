# backend/tests/test_dlna_source.py
"""
Unit tests for the DLNA source and its UPnP control-point bridge.

Covers the pure, non-trivial logic that has no other guard:
- DlnaBridge._to_ms: async-upnp-client position/duration → milliseconds, across
  the int/float-seconds shape (0.47) and the tolerated timedelta.
- DlnaBridge._dispatch_state: GENA resends the FULL state on every event, so the
  bridge must emit each field only when it actually changed (title/artist/album,
  transport state, artwork).
- DlnaBridge: the media-origin URL it derives for server identification.
- MediaServerResolver: which SSDP responses count as a media server, and what
  makes a host ambiguous — both measured against a live LAN, both invisible to
  any other guard here.
- DlnaSource: passive (rejects every command) + artwork helpers + the
  source-bar label.

Artwork dimension decoding itself lives in backend.shared.artwork and is
covered by test_artwork.py.
"""
import asyncio
import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest
from PIL import Image

from async_upnp_client.utils import CaseInsensitiveDict

from backend.sources.dlna.metadata_reader import DlnaBridge, _to_ms
from backend.sources.dlna.server_resolver import MediaServerResolver, host_of
from backend.sources.dlna.source import DLNA_CLIENT_NAME, DlnaSource


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
        current_track_uri=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_bridge():
    bridge = DlnaBridge(
        description_url="http://127.0.0.1:49494/description.xml",
        on_metadata=AsyncMock(),
        on_play_state=AsyncMock(),
        on_artwork=AsyncMock(),
        on_media_origin=AsyncMock(),
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


# === DlnaBridge: the media-origin URL ========================================

def test_origin_prefers_the_track_uri_over_the_art_url():
    """The track URI is the content itself; the art URL is only a standby. A
    server that hosts covers elsewhere would otherwise name the wrong host."""
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        media_title="Says",
        current_track_uri="http://192.168.1.254:52424/track/1.flac",
        media_image_url="http://cdn.example/art.jpg",
    )
    bridge._dispatch_state()
    bridge._on_media_origin.assert_called_once_with("http://192.168.1.254:52424/track/1.flac")


def test_origin_falls_back_to_the_art_url_without_a_track_uri():
    """CurrentTrackURI is optional in what a renderer publishes; DIDL-Lite art
    comes from the same media server, so it answers the same question."""
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(media_title="Says", media_image_url="http://nas:8200/art.jpg")
    bridge._dispatch_state()
    bridge._on_media_origin.assert_called_once_with("http://nas:8200/art.jpg")


def test_origin_is_silent_when_the_renderer_offers_neither():
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(media_title="Says")
    bridge._dispatch_state()
    bridge._on_media_origin.assert_not_called()


# === MediaServerResolver =====================================================

def _response(location, st="urn:schemas-upnp-org:device:MediaServer:1", usn=None):
    """An SSDP search response as async-upnp-client hands it over."""
    return CaseInsensitiveDict({
        "LOCATION": location,
        "ST": st,
        "USN": usn or f"uuid:abc::{st}",
    })


def _patch_search(*responses):
    """Stand in for the network: async_search replies with these, once."""
    async def _fake_search(async_callback, **kwargs):
        for headers in responses:
            await async_callback(headers)
    return patch("backend.sources.dlna.server_resolver.async_search", _fake_search)


def _patch_description(**by_location):
    """Stand in for the device-description fetch (UpnpFactory + HTTP)."""
    def _factory(_requester, **_kwargs):
        async def _create(location):
            if location not in by_location:
                raise RuntimeError(f"unreachable: {location}")
            return SimpleNamespace(friendly_name=by_location[location])
        return SimpleNamespace(async_create_device=_create)
    return patch("backend.sources.dlna.server_resolver.UpnpFactory", _factory)


@pytest.mark.asyncio
async def test_resolver_names_the_media_server():
    with _patch_search(_response("http://192.168.1.254:52424/device.xml")), \
            _patch_description(**{"http://192.168.1.254:52424/device.xml": "Freebox Server"}):
        assert await MediaServerResolver().resolve("192.168.1.254") == "Freebox Server"


@pytest.mark.asyncio
async def test_resolver_ignores_a_responder_that_is_not_a_media_server():
    """Measured on the test LAN: a Hue bridge answers a MediaServer M-SEARCH
    with ST upnp:rootdevice. Trusting the search target instead of the response
    labels DLNA playback 'Hue Bridge'."""
    with _patch_search(
        _response("http://192.168.1.29:80/description.xml", st="upnp:rootdevice",
                  usn="uuid:2f402f80::upnp:rootdevice"),
        _response("http://192.168.1.29:80/description.xml",
                  st="urn:schemas-upnp-org:device:basic:1", usn="uuid:2f402f80"),
    ), _patch_description(**{"http://192.168.1.29:80/description.xml": "Hue Bridge"}):
        assert await MediaServerResolver().resolve("192.168.1.29") is None


@pytest.mark.asyncio
async def test_resolver_treats_repeated_answers_as_one_server():
    """Also measured: one device answers an M-SEARCH several times for a single
    LOCATION. Counting responses instead of LOCATIONs reads that as ambiguity
    and throws away a name it had."""
    location = "http://192.168.1.254:52424/device.xml"
    with _patch_search(_response(location), _response(location), _response(location)), \
            _patch_description(**{location: "Freebox Server"}):
        assert await MediaServerResolver().resolve("192.168.1.254") == "Freebox Server"


@pytest.mark.asyncio
async def test_resolver_declines_a_host_running_two_servers():
    """Two media servers on one host (a NAS running minidlna next to Plex):
    nothing in the track URL says which one served it, so there is no name to
    give — only a guess, which is worse than the static label."""
    with _patch_search(
        _response("http://nas:8200/rootDesc.xml", usn="uuid:aaa::urn:schemas-upnp-org:device:MediaServer:1"),
        _response("http://nas:32469/description.xml", usn="uuid:bbb::urn:schemas-upnp-org:device:MediaServer:1"),
    ), _patch_description(**{
        "http://nas:8200/rootDesc.xml": "minidlna",
        "http://nas:32469/description.xml": "Plex Media Server",
    }):
        assert await MediaServerResolver().resolve("nas") is None


@pytest.mark.asyncio
async def test_resolver_sweeps_once_per_host_hit_or_miss():
    """A sweep costs the full MX wait whether or not anything replies, and the
    answer does not change between tracks — so neither answer may re-sweep."""
    sweeps = 0

    async def _counting_search(async_callback, **kwargs):
        nonlocal sweeps
        sweeps += 1
        await async_callback(_response("http://nas:8200/rootDesc.xml"))

    with patch("backend.sources.dlna.server_resolver.async_search", _counting_search), \
            _patch_description(**{"http://nas:8200/rootDesc.xml": "minidlna"}):
        resolver = MediaServerResolver()
        assert await resolver.resolve("nas") == "minidlna"
        assert await resolver.resolve("nas") == "minidlna"
        assert await resolver.resolve("192.168.1.99") is None
        assert await resolver.resolve("192.168.1.99") is None

    # One for the hit, one for the host nothing claimed. The second lookup of
    # each is served from cache.
    assert sweeps == 2


@pytest.mark.asyncio
async def test_resolver_retries_after_a_failed_sweep():
    """A miss is cached, a failure is not: caching the network being down would
    pin the static label for the rest of the session."""
    attempts = 0

    async def _flaky_search(async_callback, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("network unreachable")
        await async_callback(_response("http://nas:8200/rootDesc.xml"))

    with patch("backend.sources.dlna.server_resolver.async_search", _flaky_search), \
            _patch_description(**{"http://nas:8200/rootDesc.xml": "minidlna"}):
        resolver = MediaServerResolver()
        assert await resolver.resolve("nas") is None
        assert await resolver.resolve("nas") == "minidlna"


@pytest.mark.asyncio
async def test_resolver_declines_a_server_whose_description_is_unreachable():
    """Answering SSDP is not serving HTTP; a device that stops there has no
    friendlyName to read."""
    with _patch_search(_response("http://nas:8200/rootDesc.xml")), _patch_description():
        assert await MediaServerResolver().resolve("nas") is None


def test_host_of_survives_a_malformed_url():
    """urlparse raises on a broken IPv6 literal, and the URL comes from whatever
    control point pushed the track — an exception here would kill the callback."""
    assert host_of("http://[not-an-address/track.flac") is None
    assert host_of("http://192.168.1.254:52424/track.flac") == "192.168.1.254"


# === DlnaSource: the source-bar label ========================================

@pytest.mark.asyncio
async def test_label_is_the_static_one_until_the_server_is_named():
    """Resolution takes seconds; the player renders immediately and must show
    something in the meantime."""
    src = DlnaSource()
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})
    assert src.metadata["client_name"] == DLNA_CLIENT_NAME


@pytest.mark.asyncio
async def test_label_becomes_the_resolved_server_name():
    src = DlnaSource()
    src._server_resolver.resolve = AsyncMock(return_value="Freebox Server")
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})

    await src._on_media_origin("http://192.168.1.254:52424/track/1.flac")

    src._server_resolver.resolve.assert_awaited_once_with("192.168.1.254")
    assert src.metadata["client_name"] == "Freebox Server"


@pytest.mark.asyncio
async def test_a_second_track_from_the_same_server_does_not_resolve_again():
    src = DlnaSource()
    src._server_resolver.resolve = AsyncMock(return_value="Freebox Server")
    await src._on_media_origin("http://192.168.1.254:52424/track/1.flac")
    await src._on_media_origin("http://192.168.1.254:52424/track/2.flac")
    src._server_resolver.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unresolved_server_falls_back_silently():
    src = DlnaSource()
    src._server_resolver.resolve = AsyncMock(return_value=None)
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})

    await src._on_media_origin("http://192.168.1.99:8200/track/1.flac")

    assert src.metadata["client_name"] == DLNA_CLIENT_NAME


@pytest.mark.asyncio
async def test_a_new_server_drops_the_previous_name_before_resolving():
    """The old server's name is wrong for the new host the moment the host
    changes, and the sweep that replaces it takes seconds — long enough to
    caption a whole track with the wrong source."""
    src = DlnaSource()
    src._server_resolver.resolve = AsyncMock(return_value="Freebox Server")
    await src._on_media_origin("http://192.168.1.254:52424/track/1.flac")
    assert src.metadata["client_name"] == "Freebox Server"

    labels = []
    src._server_resolver.resolve = AsyncMock(
        side_effect=lambda host: labels.append(src.metadata["client_name"]) or "minidlna"
    )
    await src._on_media_origin("http://nas:8200/track/9.flac")

    assert labels == [DLNA_CLIENT_NAME]
    assert src.metadata["client_name"] == "minidlna"


@pytest.mark.asyncio
async def test_going_idle_returns_the_label_to_the_static_one():
    """An idle renderer is serving nobody, so it names nobody. The bridge
    re-emits the origin on resume, which the resolver answers from cache."""
    src = DlnaSource()
    src._server_resolver.resolve = AsyncMock(return_value="Freebox Server")
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})
    await src._on_media_origin("http://192.168.1.254:52424/track/1.flac")
    assert src.metadata["client_name"] == "Freebox Server"

    await src._on_auto_stop()

    assert src.metadata["client_name"] == DLNA_CLIENT_NAME
