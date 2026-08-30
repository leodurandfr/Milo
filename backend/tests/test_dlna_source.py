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

from backend.shared.artwork_resolver import RESOLVED_ARTWORK_PX
from backend.sources.dlna.metadata_reader import DlnaBridge, _to_ms
from backend.sources.dlna.server_resolver import MediaServerResolver, host_of
from backend.sources.dlna.source import DLNA_CLIENT_NAME, DlnaSource


def _png(color: str = "navy", size: int = 600) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
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

    src._artwork.resolve = AsyncMock(return_value=None)  # iTunes finds nothing
    await src._on_metadata_update({"title": "Toilet Brush"})
    # What drops it is the bridge saying this track has no art, not the track
    # change itself — the change only starts the hold (see the two tests below).
    await src._on_artwork(None)
    await _let_the_lookup_run()

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


async def _let_the_lookup_run() -> None:
    """Give the spawned text lookup its turn.

    _no_cover_from_sender hands it to `_bg`, so nothing has happened yet when
    the call that triggered it returns — and the suite's off-host guard would
    otherwise be what answers iTunes.
    """
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_track_the_sender_sends_no_cover_for_gets_one_looked_up():
    """A fully tagged track must not land on the status card for want of art.

    Measured on a live control point: DIDL-Lite carrying title, artist and
    album and no <upnp:albumArtURI> at all — so Milō knew everything about the
    track and drew a generic "DLNA" card. Whether a cover is published is the
    control point's choice, not the media server's, so no choice of server
    fixes it; the track text is the thing that is always there. Same fallback
    Bluetooth (AVRCP carries no image) and Radio (ICY carries none) run on.

    The width is asserted because the feature is invisible without it:
    useRichDisplay reads album_art_width, and a cover of unstated size is
    judged exactly as if there were none.
    """
    src = DlnaSource()
    src._artwork.resolve = AsyncMock(return_value="https://itunes/600x600bb.jpg")

    await src._on_metadata_update(
        {"title": "Everyday", "artist": "Jamiroquai", "album": "Travelling Without Moving"}
    )
    await src._on_artwork(None)
    await _let_the_lookup_run()

    src._artwork.resolve.assert_awaited_once_with(
        "Jamiroquai", "Everyday", "Travelling Without Moving"
    )
    assert src.metadata["album_art_url"] == "https://itunes/600x600bb.jpg"
    assert src.metadata["album_art_width"] == RESOLVED_ARTWORK_PX


@pytest.mark.asyncio
async def test_the_senders_own_cover_wins_over_a_looked_up_one():
    """The lookup is a fallback, not a preference. The server knows which
    edition is playing and iTunes is guessing from text, so a cover that came
    with the track must never be displaced by one Milō found for it."""
    src = DlnaSource()
    src._artwork.resolve = AsyncMock(return_value="https://itunes/600x600bb.jpg")
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork(None)
    await _let_the_lookup_run()
    assert src.metadata["album_art_url"] == "https://itunes/600x600bb.jpg"

    # The renderer publishes one after all — a later GENA event, a slow server.
    src._fetch_artwork = AsyncMock(return_value=_PNG)
    await src._on_artwork("http://nas/spaces.jpg")

    assert src.metadata["album_art_url"].startswith("/api/dlna/artwork?v=")
    assert src.metadata["album_art_width"] == 600


@pytest.mark.asyncio
async def test_a_looked_up_cover_does_not_follow_the_next_track():
    """A cover found for one track must not caption the one after it.

    The lookup is keyed to the track it was made for and paired again at
    publish time, which is what enforces this: a track change alone publishes
    a state, and the previous track's cover is exactly what would ride out on
    it. Same defect as the sender's own cover outliving its track, reached
    from the other side.
    """
    src = DlnaSource()
    src._artwork.resolve = AsyncMock(return_value="https://itunes/600x600bb.jpg")
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork(None)
    await _let_the_lookup_run()
    assert src.metadata["album_art_url"] == "https://itunes/600x600bb.jpg"

    # The next track, before any lookup of its own has answered.
    src._artwork.resolve = AsyncMock(return_value=None)
    await src._on_metadata_update(
        {"title": "Toilet Brush", "artist": "Nils Frahm", "album": "Spaces"}
    )

    assert src.metadata["title"] == "Toilet Brush"
    assert "album_art_url" not in src.metadata


@pytest.mark.asyncio
async def test_the_cover_is_held_while_the_next_one_is_fetched():
    """A track change must not blank the cover for the length of an HTTP fetch.

    The bridge dispatches the metadata and the art as independent tasks and
    _fetch_artwork runs up to 10 s, so this is the state published in between.
    Dropping the cover there publishes no album_art_url, and useRichDisplay's
    untrusted-sender gate reads a missing album_art_width as "this sender
    pushes no real cover": AudioPlayerFull is swapped for the AudioSourceStatus
    card and back, for seconds. Same defect as AirPlay's, whose gap is the
    unpaired rtptime rather than a fetch.
    """
    src = DlnaSource()
    src._fetch_artwork = AsyncMock(return_value=_PNG)
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork("http://nas/spaces.jpg")
    held = src.metadata["album_art_url"]

    await src._on_metadata_update({"title": "Toilet Brush", "artist": "Nils Frahm", "album": "Spaces"})

    assert src.metadata["title"] == "Toilet Brush"
    assert src.metadata["album_art_url"] == held
    assert src.metadata["album_art_width"] == 600

    # And the track's own cover, when the fetch lands, takes the hold's place.
    src._fetch_artwork = AsyncMock(return_value=_png("crimson", 450))
    await src._on_artwork("http://nas/brush.jpg")

    assert src.metadata["album_art_url"] != held
    assert src.metadata["album_art_width"] == 450


@pytest.mark.asyncio
async def test_a_fetch_that_fails_releases_the_held_cover():
    """The hold is granted on the promise that something will replace it.

    A fetch returning nothing — the DMS 404s, the host is gone — breaks that
    promise, and a cover kept then is exactly the whole-track stale cover the
    drop existed to prevent. Best-effort fetching makes this the common failure,
    not an exotic one.
    """
    src = DlnaSource()
    src._fetch_artwork = AsyncMock(return_value=_PNG)
    await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork("http://nas/spaces.jpg")
    assert "album_art_url" in src.metadata

    src._fetch_artwork = AsyncMock(return_value=None)
    src._artwork.resolve = AsyncMock(return_value=None)
    await src._on_metadata_update({"title": "Toilet Brush", "artist": "Nils Frahm", "album": "Spaces"})
    await src._on_artwork("http://nas/brush.jpg")
    await _let_the_lookup_run()

    assert "album_art_url" not in src.metadata
    assert "album_art_width" not in src.metadata
    assert src.get_artwork() is None


def test_the_bridge_says_when_a_new_track_has_no_art():
    """The source's half of the hold is worth nothing without this one.

    It holds its cover across a track change and waits to be told what replaces
    it. A bridge that only ever spoke when there WAS art would leave that hold
    standing for the whole of a coverless track — the defect the hold trades
    against, reintroduced through the other side.
    """
    bridge = _make_bridge()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Says", media_artist="Nils Frahm",
        media_image_url="http://nas/spaces.jpg",
    )
    bridge._dispatch_state()
    # Non-triviality: a bridge dispatching no art at all would satisfy the
    # assertion below for the wrong reason.
    assert bridge._on_artwork.call_args.args == ("http://nas/spaces.jpg",)

    bridge._on_artwork.reset_mock()
    bridge._dmr = _make_dmr(
        transport_state="PLAYING", media_title="Toilet Brush", media_artist="Nils Frahm",
        media_image_url=None,
    )
    bridge._dispatch_state()

    assert bridge._on_artwork.call_count == 1
    assert bridge._on_artwork.call_args.args == (None,)


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


# =============================================================================
# The bridge's supervise loop, the source's own callbacks, and the two guards
# nothing reached: the resolver's cache and the artwork route.
#
# Danger specific to this file: `_connect_and_subscribe` builds an
# `AiohttpNotifyServer`, which LISTENS on a port, and an `AiohttpRequester`,
# which reaches the LAN. `never_the_real_upnp` below makes both RAISE for the
# whole module — they are made to fail, never spied on — and every test that
# needs them installs its own double.
# =============================================================================
import contextlib
import errno
from unittest.mock import Mock

import aiohttp

import backend.sources.dlna.metadata_reader as bridge_mod


@pytest.fixture(autouse=True)
def never_the_real_upnp(monkeypatch):
    """No test opens a notify port or speaks to a renderer on this LAN."""
    def refuse(*_a, **_k):
        raise AssertionError("a test reached the real UPnP stack")

    for name in ("AiohttpNotifyServer", "AiohttpRequester", "UpnpFactory", "DmrDevice"):
        monkeypatch.setattr(bridge_mod, name, refuse)
    monkeypatch.setattr(aiohttp, "ClientSession", refuse)


class _RealBg:
    """A BackgroundTaskSet that actually runs what it is given.

    `_FakeBg` above closes the coroutine, which is right for the dispatch tests
    and wrong for the supervise loop — `start()` spawns the loop through it, so
    with the closing double the loop had never run.
    """

    def __init__(self):
        self.tasks = []

    def spawn(self, coro, label=None):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def cancel_all(self):
        for task in self.tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self.tasks, return_exceptions=True)


async def _until(predicate, timeout=2.0):
    """Bounded poll. Every wait here sits behind a double that a mutation can
    stop feeding, and an unbounded one turns that mutation into a hang."""
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


class TestTheSuperviseLoop:
    """`_run` is what keeps the renderer subscribed for the life of the unit.

    The GENA subscription renews itself while it is alive; this loop exists for
    the case it cannot — gmediarender restarted, the renderer gone. A loop that
    stops retrying means DLNA silently stops reporting anything until the
    backend is restarted.
    """

    @staticmethod
    def _bridge(**overrides):
        bridge = _make_bridge()
        bridge._bg = _RealBg()
        bridge._poll_interval = 0.01
        bridge._retry_delay = 0.01
        for key, value in overrides.items():
            setattr(bridge, key, value)
        return bridge

    async def test_a_successful_subscribe_announces_the_connection_then_polls(self):
        bridge = self._bridge()
        bridge._connect_and_subscribe = AsyncMock()
        bridge._poll_once = AsyncMock()

        await bridge.start()
        try:
            assert await _until(lambda: bridge._poll_once.await_count >= 2)
        finally:
            await asyncio.wait_for(bridge.stop(), 2.0)

        bridge._on_connection.assert_any_await("connected")

    async def test_a_renderer_that_goes_away_is_announced_and_retried(self):
        """gmediarender restarting is the ordinary case. Without the retry the
        source stays up and reports nothing for the rest of the session."""
        bridge = self._bridge()
        attempts = []

        async def connect():
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("Connection refused")

        bridge._connect_and_subscribe = connect
        bridge._poll_once = AsyncMock()
        bridge._teardown = AsyncMock()

        await bridge.start()
        try:
            assert await _until(lambda: len(attempts) >= 3)
        finally:
            await asyncio.wait_for(bridge.stop(), 2.0)

        bridge._on_connection.assert_any_await("disconnected")
        assert bridge._teardown.await_count >= 2, \
            "a failed attempt left its subscription and notify server behind"

    async def test_a_poll_that_fails_reconnects_rather_than_ending_the_loop(self):
        """The subscription is what feeds every metadata event; a poll failing
        means the renderer is gone, so the whole thing is rebuilt."""
        bridge = self._bridge()
        bridge._connect_and_subscribe = AsyncMock()
        bridge._teardown = AsyncMock()
        polls = []

        async def poll():
            polls.append(1)
            if len(polls) == 1:
                raise OSError("renderer vanished")

        bridge._poll_once = poll

        await bridge.start()
        try:
            assert await _until(lambda: bridge._connect_and_subscribe.await_count >= 2)
        finally:
            await asyncio.wait_for(bridge.stop(), 2.0)

    async def test_a_loop_failure_is_logged_where_the_operator_can_see_it(self, caplog):
        bridge = self._bridge()
        bridge._connect_and_subscribe = AsyncMock(side_effect=OSError("no route to host"))
        bridge._teardown = AsyncMock()

        with caplog.at_level("ERROR", logger="source.dlna.bridge"):
            await bridge.start()
            try:
                assert await _until(
                    lambda: any("no route to host" in r.message for r in caplog.records))
            finally:
                await asyncio.wait_for(bridge.stop(), 2.0)

    async def test_a_disconnect_announcement_that_itself_fails_does_not_stop_the_retry(self):
        """The consumer is a source that may be tearing down underneath us; its
        failure must not be what ends the reconnection loop."""
        bridge = self._bridge()
        bridge._on_connection = AsyncMock(side_effect=RuntimeError("source gone"))
        attempts = []

        async def connect():
            attempts.append(1)
            raise OSError("Connection refused")

        bridge._connect_and_subscribe = connect
        bridge._teardown = AsyncMock()

        await bridge.start()
        try:
            assert await _until(lambda: len(attempts) >= 3)
        finally:
            await asyncio.wait_for(bridge.stop(), 2.0)

    async def test_stopping_unsubscribes_and_drains(self):
        """The renderer keeps POSTing GENA callbacks at a notify server that is
        gone otherwise, and the subscription is left to expire on its own."""
        bridge = self._bridge()
        bridge._connect_and_subscribe = AsyncMock()
        bridge._poll_once = AsyncMock()
        bridge._teardown = AsyncMock()

        await bridge.start()
        assert await _until(lambda: bridge._poll_once.await_count >= 1)
        await asyncio.wait_for(bridge.stop(), 2.0)

        bridge._teardown.assert_awaited()
        assert bridge._running is False
        assert all(t.done() or t.cancelled() for t in bridge._bg.tasks)


class TestPolling:
    """Position is polled, not evented: GENA carries transport state and track
    metadata, and the playhead is only readable by asking."""

    async def test_a_position_and_a_duration_are_forwarded_in_milliseconds(self):
        bridge = _make_bridge()
        bridge._dmr = _make_dmr()
        bridge._dmr.async_update = AsyncMock()
        bridge._dmr.media_position = 30
        bridge._dmr.media_duration = 300

        await asyncio.wait_for(bridge._poll_once(), 2.0)
        bridge._on_progress.assert_awaited_once_with(30_000, 300_000)

    async def test_the_renderer_is_refreshed_before_it_is_read(self):
        """`async_update` is the SOAP call; without it the properties answer
        whatever the last GENA event left behind and the bar never moves."""
        bridge = _make_bridge()
        order = []
        bridge._dmr = _make_dmr()
        bridge._dmr.async_update = AsyncMock(side_effect=lambda: order.append("update"))
        bridge._dmr.media_position = 30
        bridge._dmr.media_duration = 300
        bridge._on_progress = AsyncMock(side_effect=lambda p, d: order.append("progress"))

        await asyncio.wait_for(bridge._poll_once(), 2.0)
        assert order == ["update", "progress"]

    async def test_a_renderer_with_no_playhead_reports_nothing(self):
        """A stream has no duration. Forwarded as 0 it would draw a zero-length
        track; the source drops it, but sending it is what makes that necessary."""
        bridge = _make_bridge()
        bridge._dmr = _make_dmr()
        bridge._dmr.async_update = AsyncMock()
        bridge._dmr.media_position = 30
        bridge._dmr.media_duration = None

        await asyncio.wait_for(bridge._poll_once(), 2.0)
        bridge._on_progress.assert_not_awaited()

    async def test_polling_before_the_renderer_exists_is_harmless(self):
        bridge = _make_bridge()
        bridge._dmr = None
        await asyncio.wait_for(bridge._poll_once(), 2.0)
        bridge._on_progress.assert_not_awaited()


class TestTeardown:
    """Both halves are best-effort and independent: a renderer that has already
    gone cannot be unsubscribed from, and failing there must not leave the
    notify server listening."""

    async def test_it_unsubscribes_and_closes_the_notify_server(self):
        bridge = _make_bridge()
        bridge._dmr = Mock(async_unsubscribe_services=AsyncMock())
        bridge._server = Mock(async_stop_server=AsyncMock())
        dmr, server = bridge._dmr, bridge._server

        await asyncio.wait_for(bridge._teardown(), 2.0)

        dmr.async_unsubscribe_services.assert_awaited_once()
        server.async_stop_server.assert_awaited_once()
        assert bridge._dmr is None and bridge._server is None

    async def test_an_unreachable_renderer_still_frees_the_notify_port(self):
        """This is the common case — the renderer going away is WHY we tear
        down. A port left bound makes the next subscribe fail too."""
        bridge = _make_bridge()
        bridge._dmr = Mock(async_unsubscribe_services=AsyncMock(
            side_effect=OSError("Connection refused")))
        bridge._server = Mock(async_stop_server=AsyncMock())
        server = bridge._server

        await asyncio.wait_for(bridge._teardown(), 2.0)

        server.async_stop_server.assert_awaited_once()
        assert bridge._server is None

    async def test_it_forgets_what_it_had_seen(self):
        """The next subscription starts from a renderer that will re-announce
        everything; keeping the old memory means the first full state is read as
        'nothing changed' and the player stays blank."""
        bridge = _make_bridge()
        bridge._last_state = "PLAYING"
        bridge._last_meta = ("Says", "Nils Frahm", "Spaces")
        bridge._last_art = "http://dms/art.jpg"
        bridge._last_origin = "http://dms/track.flac"

        await asyncio.wait_for(bridge._teardown(), 2.0)

        assert (bridge._last_state, bridge._last_meta,
                bridge._last_art, bridge._last_origin) == (None, None, None, None)

    async def test_tearing_down_twice_is_harmless(self):
        bridge = _make_bridge()
        await asyncio.wait_for(bridge._teardown(), 2.0)
        await asyncio.wait_for(bridge._teardown(), 2.0)


def _dlna_source():
    src = DlnaSource()
    src._bg = Mock()
    src._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    return src


class TestTransportState:
    """`_on_play_state` — what the renderer's transport state does to the source.

    The `stop` arm is the one with a condition on it, and it is not cosmetic: at
    subscribe time an idle gmediarender reports STOPPED, and arming the idle
    timer on that would bounce a source nobody has used yet.
    """

    async def test_playing_marks_the_source_playing_and_the_device_in_use(self):
        src = _dlna_source()
        await src._on_play_state("play")
        assert src._is_playing is True
        assert src._device_connected is True

    async def test_pausing_arms_the_idle_timer(self):
        src = _dlna_source()
        src._start_pause_timer = Mock()
        await src._on_play_state("pause")
        assert src._is_playing is False
        src._start_pause_timer.assert_called_once()

    async def test_a_stop_from_a_controller_that_was_using_us_arms_the_timer(self):
        src = _dlna_source()
        src._start_pause_timer = Mock()
        await src._on_play_state("play")
        await src._on_play_state("stop")
        assert src._is_playing is False
        src._start_pause_timer.assert_called_once()

    async def test_an_idle_renderer_reporting_stopped_at_subscribe_stays_quiet(self):
        """This is what `_connect_and_subscribe`'s first `_dispatch_state` sends
        on every boot. Armed here, the source restarts itself for a session that
        never existed."""
        src = _dlna_source()
        src._start_pause_timer = Mock()
        await src._on_play_state("stop")
        src._start_pause_timer.assert_not_called()

    async def test_playing_cancels_a_pending_idle_timer(self):
        src = _dlna_source()
        src._cancel_pause_timer = Mock()
        await src._on_play_state("play")
        src._cancel_pause_timer.assert_called_once()

    async def test_the_published_metadata_carries_the_play_state(self):
        src = _dlna_source()
        await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})
        await src._on_play_state("play")
        assert src._metadata["is_playing"] is True
        await src._on_play_state("pause")
        assert src._metadata["is_playing"] is False


class TestBridgeConnection:
    """`conn`/`disc` from the bridge mean the RENDERER is reachable, not that a
    controller is pushing — the baseline READY state."""

    async def test_a_connected_bridge_does_not_claim_a_device_is_playing(self):
        src = _dlna_source()
        await src._on_connection("connected")
        assert src._device_connected is False
        assert src._is_playing is False

    async def test_a_renderer_that_goes_away_resets_the_session(self):
        """gmediarender restarting must not leave the last track on screen for a
        renderer that no longer holds it."""
        src = _dlna_source()
        src._cancel_pause_timer = Mock()
        await src._on_metadata_update({"title": "Says", "artist": "Nils Frahm"})
        src._artwork_data, src._artwork_mime = b"bytes", "image/png"
        src._metadata["album_art_url"] = "/api/dlna/artwork?v=abc"

        await src._on_connection("disconnected")

        assert src._device_connected is False
        assert src._is_playing is False
        assert not {"title", "artist", "album_art_url"} & set(src._metadata)
        assert src.get_artwork() is None
        src._cancel_pause_timer.assert_called_once()


class TestPolledProgress:
    """Broadcasts are rate-limited to 30 s; the frontend interpolates locally
    between them (useSourceProgress)."""

    async def test_the_first_snapshot_is_broadcast(self):
        src = _dlna_source()
        src.broadcast_position_update = Mock()
        await src._on_progress(30_000, 300_000)

        src.broadcast_position_update.assert_called_once_with(30_000, 300_000)
        assert src._metadata["position"] == 30_000
        assert src._metadata["duration"] == 300_000

    async def test_a_second_snapshot_inside_the_window_updates_without_broadcasting(self):
        """The poll runs far more often than 30 s; broadcasting each one would
        push a full state to every client on every tick for no new information."""
        src = _dlna_source()
        await src._on_progress(30_000, 300_000)
        src.broadcast_position_update = Mock()

        await src._on_progress(31_000, 300_000)

        src.broadcast_position_update.assert_not_called()
        assert src._metadata["position"] == 31_000, \
            "the rate limit also swallowed the metadata update"

    async def test_a_snapshot_past_the_window_is_broadcast_again(self):
        src = _dlna_source()
        await src._on_progress(30_000, 300_000)
        src._last_progress_broadcast -= 31.0
        src.broadcast_position_update = Mock()

        await src._on_progress(61_000, 300_000)
        src.broadcast_position_update.assert_called_once()

    async def test_a_track_with_no_duration_is_dropped(self):
        """An internet radio pushed over DLNA has none; taken as 0 the player
        draws a zero-length bar and the position clamps to it."""
        src = _dlna_source()
        src.broadcast_position_update = Mock()

        await src._on_progress(30_000, 0)

        src.broadcast_position_update.assert_not_called()
        assert "duration" not in src._metadata


class TestFetchingTheCover:
    """The art URL points at the media server, not at the renderer, so the fetch
    is a plain HTTP GET onto the LAN — best-effort by design."""

    @staticmethod
    def _session(monkeypatch, *, status=200, body=b"", error=None):
        resp = AsyncMock()
        resp.status = status
        resp.read = AsyncMock(return_value=body)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)

        session = AsyncMock()
        session.get = Mock(side_effect=error) if error else Mock(return_value=ctx)
        outer = AsyncMock()
        outer.__aenter__ = AsyncMock(return_value=session)
        outer.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("backend.sources.dlna.source.aiohttp.ClientSession",
                            Mock(return_value=outer))
        return session

    async def test_a_cover_that_is_served_comes_back_as_bytes(self, monkeypatch):
        src = _dlna_source()
        self._session(monkeypatch, body=_PNG)
        assert await asyncio.wait_for(
            src._fetch_artwork("http://dms/art.jpg"), 2.0) == _PNG

    async def test_a_media_server_that_answers_an_error_yields_no_cover(self, monkeypatch, caplog):
        """A 404 on album art is routine — the DIDL-Lite URL outlives the file.
        Returning the error body would be stored and served as an image."""
        src = _dlna_source()
        self._session(monkeypatch, status=404, body=b"<html>Not Found</html>")

        with caplog.at_level("WARNING", logger=src._logger.name):
            assert await asyncio.wait_for(
                src._fetch_artwork("http://dms/art.jpg"), 2.0) is None
        assert any("404" in r.message for r in caplog.records)

    async def test_a_media_server_that_cannot_be_reached_yields_no_cover(self, monkeypatch):
        """Failure is silent by design: no cover simply means the placeholder."""
        src = _dlna_source()
        self._session(monkeypatch, error=OSError("Connection reset by peer"))
        assert await asyncio.wait_for(
            src._fetch_artwork("http://dms/art.jpg"), 2.0) is None

    async def test_a_cover_is_dropped_if_the_track_moved_on_during_the_fetch(self, monkeypatch):
        """The fetch is a LAN round trip. Published late, the outgoing track's
        cover sits on the incoming one until the track after that."""
        src = _dlna_source()
        await src._on_metadata_update({"title": "One", "artist": "Nils Frahm"})

        async def slow_read():
            await src._on_metadata_update({"title": "Two", "artist": "Nils Frahm"})
            return _PNG

        resp = AsyncMock(status=200)
        resp.read = slow_read
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = AsyncMock()
        session.get = Mock(return_value=ctx)
        outer = AsyncMock()
        outer.__aenter__ = AsyncMock(return_value=session)
        outer.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("backend.sources.dlna.source.aiohttp.ClientSession",
                            Mock(return_value=outer))

        await asyncio.wait_for(src._on_artwork("http://dms/art.jpg"), 2.0)
        assert src.get_artwork() is None
        assert "album_art_url" not in src._metadata


class TestTheResolverCache:
    """One SSDP sweep costs seconds of MX wait, and the same media server serves
    track after track."""

    async def test_a_known_host_is_answered_without_a_second_sweep(self):
        resolver = MediaServerResolver()
        resolver._cache["192.168.1.50"] = "Synology DS"
        resolver._sweep = AsyncMock()

        assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) == "Synology DS"
        resolver._sweep.assert_not_awaited()

    async def test_a_host_that_answered_nothing_is_remembered_too(self):
        """Re-sweeping for a host nobody claims would pay the MX wait again on
        every track, for ever, to reach the same answer."""
        resolver = MediaServerResolver()
        resolver._cache["192.168.1.50"] = None
        resolver._sweep = AsyncMock()

        assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) is None
        resolver._sweep.assert_not_awaited()

    async def test_no_host_at_all_is_answered_without_a_sweep(self):
        resolver = MediaServerResolver()
        resolver._sweep = AsyncMock()
        assert await asyncio.wait_for(resolver.resolve(""), 2.0) is None
        resolver._sweep.assert_not_awaited()

    async def test_two_callers_for_one_host_pay_for_a_single_sweep(self):
        """Both would otherwise wait out the MX window, and the second would be
        answering the first's question."""
        resolver = MediaServerResolver()
        sweeps = []

        async def sweep():
            sweeps.append(1)
            await asyncio.sleep(0.02)
            return {"192.168.1.50": "Synology DS"}

        resolver._sweep = sweep
        both = await asyncio.wait_for(asyncio.gather(
            resolver.resolve("192.168.1.50"), resolver.resolve("192.168.1.50")), 2.0)

        assert both == ["Synology DS", "Synology DS"]
        assert len(sweeps) == 1

    async def test_a_known_host_is_answered_while_another_host_is_being_swept(self):
        """The check before the lock is not a duplicate of the one inside it.

        A sweep is seconds of MX wait and it holds the lock for all of them; a
        host already in the cache has to come back now, or the source-bar label
        for a track from a known server waits out a discovery it has nothing to
        do with.
        """
        resolver = MediaServerResolver()
        resolver._cache["192.168.1.50"] = "Synology DS"
        release = asyncio.Event()

        async def slow_sweep():
            await release.wait()
            return {"192.168.1.99": "Other"}

        resolver._sweep = slow_sweep
        sweeping = asyncio.create_task(resolver.resolve("192.168.1.99"))
        await asyncio.sleep(0)          # let it take the lock

        known = asyncio.create_task(resolver.resolve("192.168.1.50"))
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if known.done():
                    break
            assert known.done(), \
                "a cached host queued behind an unrelated sweep"
            assert known.result() == "Synology DS"
        finally:
            release.set()
            await asyncio.wait_for(sweeping, 2.0)

    async def test_a_sweep_that_times_out_leaves_the_host_unknown(self, caplog):
        """A clean negative is cached; a transient fault must NOT be, or one bad
        moment pins the static label for the rest of the session."""
        resolver = MediaServerResolver()

        async def hang():
            await asyncio.sleep(30)

        resolver._sweep = hang
        with patch("backend.sources.dlna.server_resolver._RESOLVE_TIMEOUT", 0.01), \
                caplog.at_level("WARNING", logger="source.dlna.resolver"):
            assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) is None

        assert "192.168.1.50" not in resolver._cache
        assert any("exceeded" in r.message for r in caplog.records)

    async def test_a_sweep_that_fails_leaves_the_host_unknown(self):
        resolver = MediaServerResolver()
        resolver._sweep = AsyncMock(side_effect=OSError("Network is unreachable"))

        assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) is None
        assert "192.168.1.50" not in resolver._cache

    async def test_one_sweep_banks_every_server_it_found(self):
        """A second media server on the LAN then costs no second sweep."""
        resolver = MediaServerResolver()
        resolver._sweep = AsyncMock(return_value={
            "192.168.1.50": "Synology DS", "192.168.1.51": "Plex",
        })

        assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) == "Synology DS"
        resolver._sweep = AsyncMock()
        assert await asyncio.wait_for(resolver.resolve("192.168.1.51"), 2.0) == "Plex"
        resolver._sweep.assert_not_awaited()

    async def test_a_host_no_sweep_claimed_is_banked_as_a_clean_negative(self):
        resolver = MediaServerResolver()
        resolver._sweep = AsyncMock(return_value={"192.168.1.51": "Plex"})

        assert await asyncio.wait_for(resolver.resolve("192.168.1.50"), 2.0) is None
        assert resolver._cache["192.168.1.50"] is None


class TestTheArtworkRoute:
    """`GET /api/dlna/artwork` — the cover fetched from the media server, served
    to the player. Same contract as AirPlay's."""

    @staticmethod
    def _client(source):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.sources.dlna.routes import setup_dlna_routes
        app = FastAPI()
        app.include_router(setup_dlna_routes(lambda: source), prefix="/api")
        return TestClient(app)

    def test_the_cover_is_served_with_the_type_it_was_stored_as(self):
        src = DlnaSource()
        src._artwork_data, src._artwork_mime = _PNG, "image/png"

        resp = self._client(src).get("/api/dlna/artwork")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == _PNG

    def test_it_is_cached_privately_and_immutably(self):
        src = DlnaSource()
        src._artwork_data, src._artwork_mime = _PNG, "image/png"
        cache = self._client(src).get("/api/dlna/artwork").headers["cache-control"]
        assert "private" in cache and "immutable" in cache

    def test_a_track_with_no_cover_is_a_404_and_not_an_error(self, caplog):
        """The DIDL-Lite of plenty of tracks carries no art URL. Logged at ERROR
        this would raise the WebSocket error banner on an ordinary track."""
        with caplog.at_level("ERROR", logger="backend.sources.dlna.routes"):
            resp = self._client(DlnaSource()).get("/api/dlna/artwork")

        assert resp.status_code == 404
        assert caplog.records == []


class TestDlnaLifecycle:
    """`_do_start` brings up gmediarender and the control-point bridge. Its
    error arm is the interesting half: starting DLNA with the network down is an
    expected outcome, not a fault to put on the system-error banner."""

    @staticmethod
    def _source(monkeypatch, bridge=None):
        src = _dlna_source()
        src._start_service_and_wait = AsyncMock(return_value=True)
        src._load_auto_stop_config = AsyncMock()
        src._update_connection_state = Mock()
        made = bridge or Mock(start=AsyncMock(), stop=AsyncMock(),
                              forget_last_seen=Mock())
        monkeypatch.setattr("backend.sources.dlna.source.DlnaBridge",
                            Mock(return_value=made))
        monkeypatch.setattr("backend.sources.dlna.source.get_local_ip",
                            Mock(return_value="192.168.1.10"))
        return src, made

    async def test_the_bridge_is_pointed_at_the_renderer_on_this_host(self, monkeypatch):
        """gmediarender does not listen on loopback, so the description URL has
        to carry the LAN address the renderer actually advertises on."""
        src, bridge = self._source(monkeypatch)

        assert await asyncio.wait_for(src._do_start(), 2.0) is True
        assert src._description_url.startswith("http://192.168.1.10:")
        assert src._description_url.endswith("/description.xml")
        bridge.start.assert_awaited_once()

    async def test_every_callback_is_wired(self, monkeypatch):
        """Same failure mode as AirPlay: an un-wired one disables its branch in
        `_dispatch_state` with nothing to show for it."""
        src, _bridge = self._source(monkeypatch)
        from backend.sources.dlna.source import DlnaBridge as Patched

        await asyncio.wait_for(src._do_start(), 2.0)
        kwargs = Patched.call_args.kwargs
        assert kwargs["on_metadata"] == src._on_metadata_update
        assert kwargs["on_play_state"] == src._on_play_state
        assert kwargs["on_artwork"] == src._on_artwork
        assert kwargs["on_media_origin"] == src._on_media_origin
        assert kwargs["on_progress"] == src._on_progress
        assert kwargs["on_connection"] == src._on_connection

    async def test_a_renderer_that_will_not_start_builds_no_bridge(self, monkeypatch):
        src, bridge = self._source(monkeypatch)
        src._start_service_and_wait = AsyncMock(return_value=False)

        assert await asyncio.wait_for(src._do_start(), 2.0) is False
        bridge.start.assert_not_awaited()

    async def test_starting_with_the_network_down_is_a_warning_not_an_error(
            self, monkeypatch, caplog):
        """ENETUNREACH is the link saying there is nothing to advertise on. At
        ERROR it reaches the WebSocketLogHandler banner and sits on top of the
        status card that already says the same thing."""
        src, bridge = self._source(monkeypatch)
        bridge.start = AsyncMock(side_effect=OSError(errno.ENETUNREACH, "Network is unreachable"))

        with caplog.at_level("WARNING", logger=src._logger.name):
            assert await asyncio.wait_for(src._do_start(), 2.0) is False

        levels = {r.levelname for r in caplog.records if "Start failed" in r.message}
        assert levels == {"WARNING"}, f"the expected no-network case logged {levels}"

    async def test_any_other_start_failure_is_still_an_error(self, monkeypatch, caplog):
        """The warning arm is narrow on purpose — a renderer that is genuinely
        broken must still reach the operator."""
        src, bridge = self._source(monkeypatch)
        bridge.start = AsyncMock(side_effect=OSError(errno.ECONNREFUSED, "Connection refused"))

        with caplog.at_level("WARNING", logger=src._logger.name):
            assert await asyncio.wait_for(src._do_start(), 2.0) is False

        levels = {r.levelname for r in caplog.records if "Start failed" in r.message}
        assert levels == {"ERROR"}

    async def test_a_failed_start_tears_the_bridge_down(self, monkeypatch):
        """Left behind it keeps a notify port bound and its subscription alive,
        and the next start builds a second one alongside it."""
        src, bridge = self._source(monkeypatch)
        src._load_auto_stop_config = AsyncMock(side_effect=RuntimeError("settings gone"))

        assert await asyncio.wait_for(src._do_start(), 2.0) is False
        assert src._bridge is None

    async def test_cleanup_stops_the_bridge_and_forgets_the_session(self, monkeypatch):
        src, bridge = self._source(monkeypatch)
        await asyncio.wait_for(src._do_start(), 2.0)
        src._device_connected = True
        src._artwork_data, src._artwork_mime = _PNG, "image/png"

        await asyncio.wait_for(src._cleanup(), 2.0)

        bridge.stop.assert_awaited_once()
        assert src._bridge is None
        assert src._device_connected is False
        assert src.get_artwork() is None

    async def test_cleaning_up_twice_is_harmless(self, monkeypatch):
        src, _bridge = self._source(monkeypatch)
        await asyncio.wait_for(src._cleanup(), 2.0)
        await asyncio.wait_for(src._cleanup(), 2.0)


class TestSubscribing:
    """`_connect_and_subscribe` builds the control point. It was never entered:
    the whole of it is async-upnp-client wiring, and the autouse guard above
    makes every one of those names raise unless a test installs a double.

    Three details in it are load-bearing and none of them is obvious.
    """

    @staticmethod
    def _stack(monkeypatch, *, local_ip="192.168.1.10"):
        requester = Mock(name="requester")
        device = Mock(name="device")
        factory = Mock(async_create_device=AsyncMock(return_value=device))
        server = Mock(async_start_server=AsyncMock(), event_handler=Mock(name="handler"))
        dmr = Mock(async_subscribe_services=AsyncMock(),
                   transport_state="STOPPED", media_title="", media_artist="",
                   media_album_artist="", media_album_name="",
                   media_image_url=None, current_track_uri=None)
        made = {}

        monkeypatch.setattr(bridge_mod, "AiohttpRequester", Mock(return_value=requester))
        monkeypatch.setattr(bridge_mod, "UpnpFactory", Mock(return_value=factory))
        monkeypatch.setattr(bridge_mod, "AiohttpNotifyServer",
                            Mock(side_effect=lambda *a, **k: made.setdefault(
                                "notify", (a, k)) and server or server))
        monkeypatch.setattr(bridge_mod, "DmrDevice", Mock(return_value=dmr))
        monkeypatch.setattr(bridge_mod, "get_local_ip", Mock(return_value=local_ip))
        return SimpleNamespace(requester=requester, device=device, factory=factory,
                               server=server, dmr=dmr, made=made)

    async def test_the_notify_server_binds_to_the_lan_address_not_loopback(self, monkeypatch):
        """gmediarender POSTs its GENA callbacks back to us and does not listen
        on loopback, so a notify server bound to 127.0.0.1 subscribes fine and
        then never receives a single event — the player stays blank while the
        renderer plays."""
        stack = self._stack(monkeypatch)
        bridge = _make_bridge()

        await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)

        _args, kwargs = bridge_mod.AiohttpNotifyServer.call_args
        assert kwargs["source"] == ("192.168.1.10", 0), \
            "the notify server was not bound to the address the renderer routes to"
        stack.server.async_start_server.assert_awaited_once()

    async def test_the_subscription_renews_itself(self, monkeypatch):
        """GENA subscriptions expire. Without auto_resubscribe the bridge goes
        quiet after the timeout with no error anywhere — the supervise loop is
        still happily polling, so nothing retries either."""
        stack = self._stack(monkeypatch)
        bridge = _make_bridge()

        await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)
        stack.dmr.async_subscribe_services.assert_awaited_once_with(auto_resubscribe=True)

    async def test_the_renderer_is_read_once_at_subscribe_time(self, monkeypatch):
        """GENA only sends what CHANGES. A renderer already playing when the
        source starts sends nothing until the next track, so without this first
        read the player is empty for the rest of the current one."""
        stack = self._stack(monkeypatch)
        stack.dmr.transport_state = "PLAYING"
        stack.dmr.media_title = "Says"
        stack.dmr.media_artist = "Nils Frahm"
        bridge = _make_bridge()
        bridge._bg = _RealBg()

        await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)
        await asyncio.gather(*bridge._bg.tasks, return_exceptions=True)

        bridge._on_play_state.assert_awaited_with("play")
        bridge._on_metadata.assert_awaited()

    async def test_gena_events_are_routed_back_into_the_dispatch(self, monkeypatch):
        """`on_event` is the only thing async-upnp-client calls when a NOTIFY
        arrives; unset, the subscription is live and nothing reads it."""
        stack = self._stack(monkeypatch)
        bridge = _make_bridge()

        await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)
        assert stack.dmr.on_event == bridge._on_dmr_event

        stack.dmr.transport_state = "PLAYING"
        bridge._bg = _RealBg()
        bridge._on_dmr_event(None, None)
        await asyncio.gather(*bridge._bg.tasks, return_exceptions=True)
        bridge._on_play_state.assert_awaited_with("play")

    async def test_the_device_is_built_from_the_description_url(self, monkeypatch):
        stack = self._stack(monkeypatch)
        bridge = _make_bridge()

        await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)
        stack.factory.async_create_device.assert_awaited_once_with(
            bridge._description_url)

    async def test_a_renderer_that_is_not_there_raises_for_the_loop_to_retry(self, monkeypatch):
        """Swallowed here, the supervise loop would treat a failed subscribe as
        success and poll a renderer it never reached."""
        self._stack(monkeypatch)
        bridge_mod.UpnpFactory.return_value.async_create_device = AsyncMock(
            side_effect=OSError("Connection refused"))
        bridge = _make_bridge()

        with pytest.raises(OSError):
            await asyncio.wait_for(bridge._connect_and_subscribe(), 2.0)

    async def test_dispatching_before_a_renderer_exists_is_harmless(self):
        """`_teardown` sets `_dmr` to None while a GENA callback may still be in
        flight — async-upnp-client calls it straight from the loop."""
        bridge = _make_bridge()
        bridge._dmr = None
        bridge._dispatch_state()
        bridge._on_play_state.assert_not_awaited()
