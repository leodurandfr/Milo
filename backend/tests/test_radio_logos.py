# backend/tests/test_radio_logos.py
"""`StationLogos` and the route in front of it, `GET /api/radio/favicon?url=…`.

Every external station logo reaches every client through here — the kiosk,
Milo-Mac, and the session artwork Milo-iOS puts on the lock screen. What breaks
when these fail: a station shows its generated avatar although a logo exists
(France Culture's 1024 px Wikimedia thumbnail, answered 400), the lock screen
stays blank (WebP handed to iOS), a foreign page renders on `milo.local`, or
the appliance fetches an address on its own LAN.

The outside world is aiohttp and `socket.getaddrinfo`; nothing leaves the
process (the conftest guard refuses any real connect anyway).
"""
import asyncio
import io
import os
import socket
import time
from unittest.mock import Mock

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.sources.radio import logos as logos_module
from backend.sources.radio.logos import DARK_BACKDROP, LIGHT_BACKDROP, StationLogos, repair_url
from backend.sources.radio.routes import setup_radio_routes

BROWSER = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
URLSESSION = "*/*"
LOGO_URL = "http://cdn.example.com/logo.png"


def _png(width, height, mode="RGB", background=(10, 20, 30), center=(220, 30, 30)):
    """A logo: a flat background with a differently colored block in its middle."""
    image = Image.new(mode, (width, height), background)
    image.paste(center, (width // 4, height // 4, 3 * width // 4, 3 * height // 4))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# White ink on nothing, a quarter of the square: BBC Radio 2's case.
SVG = (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
       b'<rect x="2.5" y="2.5" width="5" height="5" fill="#fafafa"/></svg>')


class _Resp:
    """Stands in for an aiohttp response."""

    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content = self

    async def iter_chunked(self, n):
        # Small pieces, as a network delivers them: a reader that stops at the
        # first chunk hands PIL a truncated image.
        for i in range(0, len(self._body), 100):
            yield self._body[i:i + 100]

    async def read(self, n=-1):
        """What aiohttp's `read(n)` does: only what has arrived so far."""
        return self._body[:min(100, len(self._body) if n < 0 else n)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stands in for aiohttp.ClientSession, recording what was fetched and how."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.fetched = []
        self.headers = []

    def get(self, url, **kwargs):
        self.fetched.append(url)
        self.headers.append(kwargs.get("headers") or {})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def logos_dir(tmp_path, monkeypatch):
    """A cache per test: an entry left by one test would answer the next."""
    path = tmp_path / "radio_logos"
    monkeypatch.setattr(StationLogos, "LOGOS_DIR", path)
    return path


@pytest.fixture
def fetches(monkeypatch):
    """Replace the HTTP client; every call opens the same recording session."""
    def _install(*responses):
        session = _Session(responses)
        monkeypatch.setattr(logos_module.aiohttp, "ClientSession", lambda: session)
        return session

    return _install


@pytest.fixture
def dns(monkeypatch):
    """Resolve every hostname to one address of the caller's choosing.

    Patched at `socket.getaddrinfo` — the real outside-world boundary, which is
    what `loop.getaddrinfo` runs in its executor.
    """
    def _install(mapping):
        def _getaddrinfo(host, port, *a, **kw):
            if host not in mapping:
                raise socket.gaierror(-2, "Name or service not known")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], 0))]

        monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)

    return _install


@pytest.fixture
def public(dns):
    dns({"cdn.example.com": "93.184.216.34", "img.example.org": "93.184.216.35"})


def _decode(data):
    return Image.open(io.BytesIO(data))


def _near(pixel, color, tolerance=6):
    """Equal within what lossy WebP moves a flat color by."""
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, color))


class TestRepairUrl:
    """Wikimedia answers 400 to a hotlinked thumbnail at a width it does not
    serve (T414805) — which is what radio-browser is full of."""

    BASE = "https://upload.wikimedia.org/wikipedia/fr/thumb/c/c9/France_Culture_-_2008.svg/"

    @pytest.mark.parametrize("asked,served", [
        (1024, 1280), (640, 960), (200, 250), (2560, 1280), (500, 500), (12, 20),
    ])
    def test_a_thumbnail_width_is_rounded_up_to_one_wikimedia_serves(self, asked, served):
        url = f"{self.BASE}{asked}px-France_Culture_-_2008.svg.png"

        assert repair_url(url) == f"{self.BASE}{served}px-France_Culture_-_2008.svg.png"

    def test_a_language_prefixed_thumbnail_keeps_its_prefix(self):
        url = ("https://upload.wikimedia.org/wikipedia/commons/thumb/f/fb/Logo.svg/"
               "langfr-1024px-Logo.svg.png")

        assert repair_url(url).endswith("/langfr-1280px-Logo.svg.png")

    def test_an_svg_original_becomes_a_raster_every_client_can_draw(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/d/d6/France_Culture_logo_2021.svg"

        assert repair_url(url) == (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/"
            "France_Culture_logo_2021.svg/960px-France_Culture_logo_2021.svg.png"
        )

    @pytest.mark.parametrize("url", [
        "https://www.radiofrance.fr/1024px-logo.png",
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Logo.png",
        "https://commons.wikimedia.org/wiki/File:Logo.svg",
    ])
    def test_anything_else_is_left_alone(self, url):
        assert repair_url(url) == url


class TestTheFetch:
    """The URL comes from a public directory anyone may add to: without the
    address check the route is a request-forgery primitive."""

    async def test_wikimedia_is_not_invited_to_answer_webp(self, fetches, public):
        """Wikimedia serves WebP to whoever names it, and iOS cannot draw
        WebP — the rendition would have to undo it on every logo."""
        session = fetches(_Resp(200, _png(200, 200)))

        await StationLogos().get(LOGO_URL, BROWSER)

        assert "webp" not in session.headers[0]["Accept"]

    @pytest.mark.parametrize("address,what", [
        ("127.0.0.1", "this machine"),
        ("192.168.1.60", "a satellite on the LAN"),
        ("10.0.0.1", "an RFC1918 host"),
        ("169.254.1.1", "link-local"),
        ("100.100.100.100", "the carrier-grade NAT range"),
    ])
    async def test_an_address_off_the_public_internet_is_never_fetched(
        self, fetches, dns, address, what
    ):
        dns({"evil.example.com": address})
        session = fetches(_Resp(200, _png(200, 200)))

        assert await StationLogos().get("http://evil.example.com/probe", BROWSER) is None
        assert session.fetched == [], f"the appliance fetched {what}"

    async def test_a_redirect_onto_the_lan_is_refused_at_the_second_hop(self, fetches, dns):
        """Why redirects are followed by hand: `allow_redirects=True` hands the
        decision to aiohttp, and a public 302 walks the fetch past the check."""
        dns({"cdn.example.com": "93.184.216.34", "router.lan": "192.168.1.1"})
        session = fetches(
            _Resp(302, b"", {"Location": "http://router.lan/admin"}),
            _Resp(200, _png(200, 200)),
        )

        assert await StationLogos().get(LOGO_URL, BROWSER) is None
        assert session.fetched == [LOGO_URL]

    async def test_a_redirect_between_public_hosts_is_followed(self, fetches, public):
        """HTTP→HTTPS redirects are routine in the directory."""
        session = fetches(
            _Resp(301, b"", {"Location": "https://img.example.org/logo.png"}),
            _Resp(200, _png(200, 200)),
        )

        assert await StationLogos().get(LOGO_URL, BROWSER) is not None
        assert session.fetched == [LOGO_URL, "https://img.example.org/logo.png"]

    async def test_a_relative_redirect_resolves_against_the_hop_it_came_from(
        self, fetches, public
    ):
        session = fetches(
            _Resp(302, b"", {"Location": "/assets/logo.png"}),
            _Resp(200, _png(200, 200)),
        )

        await StationLogos().get("http://cdn.example.com/a/b.png", BROWSER)

        assert session.fetched[1] == "http://cdn.example.com/assets/logo.png"

    async def test_a_redirect_loop_is_bounded(self, fetches, public):
        session = fetches(*[
            _Resp(302, b"", {"Location": "http://cdn.example.com/loop"})
            for _ in range(10)
        ])

        assert await StationLogos().get("http://cdn.example.com/loop", BROWSER) is None
        assert len(session.fetched) == logos_module.MAX_REDIRECTS + 1

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "//example.com/x",
        "gopher://example.com:1780/",
    ])
    async def test_only_http_and_https_are_fetched(self, fetches, dns, url):
        dns({"example.com": "93.184.216.34"})
        session = fetches(_Resp(200, _png(200, 200)))

        assert await StationLogos().get(url, BROWSER) is None
        assert session.fetched == []

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x",
                                     "http:///no-host"])
    async def test_an_unfetchable_url_is_rejected_before_any_lookup(self, monkeypatch, url):
        """One of these per station in the list: a DNS request for a URL that
        can never be fetched is traffic for nothing."""
        def _never(*a, **kw):
            raise AssertionError(f"resolved a hostname for {url}")

        monkeypatch.setattr(socket, "getaddrinfo", _never)

        assert await logos_module.target_allowed(url) is False

    async def test_a_hostname_that_does_not_resolve_is_refused_rather_than_fetched(
        self, fetches, dns
    ):
        dns({})
        session = fetches(_Resp(200, _png(200, 200)))

        assert await StationLogos().get("http://nowhere.invalid/logo.png", BROWSER) is None
        assert session.fetched == []

    async def test_a_thumbnail_not_rendered_yet_is_asked_again_once(self, fetches, public):
        """Wikimedia answers `429 Retry-After: 1` on a thumbnail it has not
        rendered yet; a grid of logos loaded at once hits it."""
        session = fetches(
            _Resp(429, b"", {"Retry-After": "0"}),
            _Resp(200, _png(200, 200)),
        )

        assert await StationLogos().get(LOGO_URL, BROWSER) is not None
        assert len(session.fetched) == 2

    async def test_a_long_rate_limit_is_not_waited_out_nor_remembered(self, fetches, public):
        """A busy host is not a host without a logo: the next render asks again."""
        session = fetches(
            _Resp(429, b"", {"Retry-After": "30"}),
            _Resp(200, _png(200, 200)),
        )
        logos = StationLogos()

        assert await logos.get(LOGO_URL, BROWSER) is None
        assert len(session.fetched) == 1
        assert await logos.get(LOGO_URL, BROWSER) is not None

    async def test_a_server_error_is_not_remembered(self, fetches, public):
        """A CDN answering 503 under load has a logo, just not this second."""
        session = fetches(_Resp(503), _Resp(200, _png(200, 200)))
        logos = StationLogos()

        assert await logos.get(LOGO_URL, BROWSER) is None
        assert await logos.get(LOGO_URL, BROWSER) is not None
        assert len(session.fetched) == 2

    async def test_an_unreachable_host_is_none_and_not_remembered(self, fetches, public):
        """Remembering a transport failure would, on a unit that is offline for
        a minute, mark every logo in the list as missing for hours."""
        session = fetches(
            aiohttp.ClientConnectionError("network unreachable"),
            _Resp(200, _png(200, 200)),
        )
        logos = StationLogos()

        assert await logos.get(LOGO_URL, BROWSER) is None
        assert await logos.get(LOGO_URL, BROWSER) is not None
        assert len(session.fetched) == 2


class TestWhatIsALogo:
    async def test_a_page_answered_200_is_not_a_logo_and_is_remembered(self, fetches, public):
        """29 of the directory's top 3000 favicons are HTML pages. Re-served
        from Milō's origin they would render on `milo.local`; refetched on every
        render they cost a round-trip each time."""
        session = fetches(_Resp(200, b"<!doctype html><html>" + b"x" * 400))
        logos = StationLogos()

        assert await logos.get(LOGO_URL, BROWSER) is None
        assert await logos.get(LOGO_URL, BROWSER) is None
        assert len(session.fetched) == 1

    async def test_a_host_answering_no_is_remembered(self, fetches, public):
        session = fetches(_Resp(404))
        logos = StationLogos()

        assert await logos.get(LOGO_URL, BROWSER) is None
        assert await logos.get(LOGO_URL, BROWSER) is None
        assert len(session.fetched) == 1

    async def test_an_icon_too_small_to_draw_is_left_to_the_avatar(self, fetches, public):
        fetches(_Resp(200, _png(40, 40)))

        assert await StationLogos().get(LOGO_URL, BROWSER) is None

    async def test_a_wide_logo_is_padded_to_a_square_in_its_own_background(
        self, fetches, public
    ):
        """Every renderer fills a square with `object-fit: cover`, which cut the
        sides off one logo in ten."""
        fetches(_Resp(200, _png(400, 100)))

        data, _ = await StationLogos().get(LOGO_URL, BROWSER)
        image = _decode(data).convert("RGB")

        assert image.size == (400, 400)
        corner = image.getpixel((0, 0))
        assert all(abs(a - b) <= 4 for a, b in zip(corner, (10, 20, 30))), corner
        middle = image.getpixel((200, 200))
        assert middle[0] > 180, "the logo itself must sit in the middle"

    async def test_a_dark_logo_on_transparency_is_flattened_onto_white(self, fetches, public):
        """Radio Meuh is black on nothing: it vanished on the screensaver's black."""
        fetches(_Resp(200, _png(400, 100, mode="RGBA", background=(0, 0, 0, 0),
                                center=(20, 20, 20, 255))))

        image = _decode((await StationLogos().get(LOGO_URL, BROWSER))[0])

        assert image.mode == "RGB" and image.size == (400, 400)
        for xy in [(0, 0), (20, 200)]:  # the padding, then the logo's own transparency
            assert _near(image.getpixel(xy), LIGHT_BACKDROP), (xy, image.getpixel(xy))
        assert max(image.getpixel((200, 200))) < 60, "the logo itself must keep its color"

    async def test_a_white_logo_on_transparency_is_flattened_onto_the_dark_ground(
        self, fetches, public
    ):
        """BBC Radio 2 is white on nothing: on white it was an empty square."""
        fetches(_Resp(200, _png(400, 100, mode="RGBA", background=(0, 0, 0, 0),
                                center=(250, 250, 250, 255))))

        image = _decode((await StationLogos().get(LOGO_URL, BROWSER))[0])

        assert _near(image.getpixel((0, 0)), DARK_BACKDROP), image.getpixel((0, 0))
        assert min(image.getpixel((200, 200))) > 230

    async def test_a_white_tile_with_transparent_corners_stays_on_white(self, fetches, public):
        """A light logo that fills its square (WDR, a white disc) is a tile:
        white extends it, where a dark ground would draw dark corners round it."""
        disc = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        ImageDraw.Draw(disc).ellipse((0, 0, 199, 199), fill=(250, 250, 250, 255))
        disc.paste((0, 60, 140, 255), (80, 80, 120, 120))
        buffer = io.BytesIO()
        disc.save(buffer, format="PNG")
        fetches(_Resp(200, buffer.getvalue()))

        image = _decode((await StationLogos().get(LOGO_URL, BROWSER))[0])

        assert _near(image.getpixel((0, 0)), LIGHT_BACKDROP), image.getpixel((0, 0))

    async def test_a_truncated_image_is_not_a_logo(self, fetches, public):
        body = _png(200, 200)
        fetches(_Resp(200, body[: len(body) // 2]))

        assert await StationLogos().get(LOGO_URL, BROWSER) is None

    @pytest.mark.parametrize("prefix", [
        b"<!-- Generator: Adobe Illustrator 27.0 -->\n",
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">\n',
    ])
    async def test_an_svg_is_recognized_behind_a_comment_or_doctype(
        self, fetches, public, prefix
    ):
        """What vector editors export. Read as a raster, it failed and was
        remembered as missing."""
        fetches(_Resp(200, prefix + SVG.split(b"\n", 1)[1]))

        assert await StationLogos().get(LOGO_URL, BROWSER) is not None

    async def test_a_page_that_inlines_an_svg_is_still_a_page(self, fetches, public):
        fetches(_Resp(200, b"<!doctype html><html><body><svg></svg></body></html>"))

        assert await StationLogos().get(LOGO_URL, BROWSER) is None

    async def test_a_large_logo_is_bounded(self, fetches, public):
        fetches(_Resp(200, _png(2000, 2000)))

        data, _ = await StationLogos().get(LOGO_URL, BROWSER)

        assert max(_decode(data).size) <= 1024


class TestNegotiation:
    """One URL, two formats: browsers take WebP, iOS draws neither WebP nor
    SVG (see `get_station_image`)."""

    @pytest.mark.parametrize("accept,media_type", [
        (BROWSER, "image/webp"), (URLSESSION, "image/jpeg"),
    ])
    async def test_an_svg_is_drawn_for_every_client(self, fetches, public, accept, media_type):
        """Served verbatim, an SVG reached browsers only: the iOS lock screen
        stayed blank, and nothing chose a ground for it."""
        fetches(_Resp(200, SVG))

        data, served = await StationLogos().get(LOGO_URL, accept)
        image = _decode(data).convert("RGB")

        assert served == media_type
        assert image.size == (1024, 1024)
        assert _near(image.getpixel((0, 0)), DARK_BACKDROP), "white ink, sparse: the dark ground"
        assert min(image.getpixel((512, 512))) > 230, "the logo itself is drawn"

    async def test_an_svg_fetches_nothing_it_references(self, fetches, public):
        """A directory entry is anyone's to edit: rendering it must not make the
        unit request a URL (or read a file) the SVG names."""
        svg = (b'<svg xmlns="http://www.w3.org/2000/svg" '
               b'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
               b'<image xlink:href="http://192.168.1.1/admin" width="10" height="10"/>'
               b'<image xlink:href="file:///etc/passwd" width="10" height="10"/>'
               b'<rect x="2" y="2" width="6" height="6" fill="#123456"/></svg>')
        fetches(_Resp(200, svg))

        # cairosvg fetches with urllib, outside the faked session: a real
        # connect is refused by the conftest guard, and a fetched file is no
        # image — either way the logo would come back None.
        assert await StationLogos().get(LOGO_URL, BROWSER) is not None

    async def test_an_svg_that_outlasts_its_budget_is_no_logo(
        self, fetches, public, monkeypatch, logos_dir
    ):
        """Nested <use> made a 1.2 KB SVG cost 14 s of CPU; uncapped, two such
        logos held both PIL slots and the Pi the multiroom stream needs."""
        monkeypatch.setattr(logos_module, "SVG_RENDER_TIMEOUT_S", 0.01)
        fetches(_Resp(200, SVG))

        assert await StationLogos().get(LOGO_URL, BROWSER) is None
        assert [p.suffix for p in logos_dir.iterdir()] == [".miss"]

    async def test_a_browser_gets_webp(self, fetches, public):
        fetches(_Resp(200, _png(200, 200)))

        data, media_type = await StationLogos().get(LOGO_URL, BROWSER)

        assert media_type == "image/webp"
        assert _decode(data).format == "WEBP"

    async def test_a_caller_that_does_not_name_webp_gets_jpeg(self, fetches, public):
        fetches(_Resp(200, _png(200, 200)))

        data, media_type = await StationLogos().get(LOGO_URL, URLSESSION)

        assert media_type == "image/jpeg"
        assert data[:3] == b"\xff\xd8\xff"

class TestTheCache:
    async def test_a_logo_is_fetched_once(self, fetches, public):
        session = fetches(_Resp(200, _png(200, 200)))
        logos = StationLogos()

        first = await logos.get(LOGO_URL, BROWSER)

        assert await StationLogos().get(LOGO_URL, BROWSER) == first
        assert len(session.fetched) == 1

    async def test_tiles_asking_at_once_share_one_fetch(self, fetches, public):
        session = fetches(_Resp(200, _png(200, 200)))
        logos = StationLogos()

        results = await asyncio.gather(*(logos.get(LOGO_URL, BROWSER) for _ in range(5)))

        assert all(r is not None for r in results)
        assert len(session.fetched) == 1

    async def test_an_expired_logo_is_fetched_again(self, fetches, public, logos_dir):
        session = fetches(_Resp(200, _png(200, 200)), _Resp(200, _png(300, 300)))
        logos = StationLogos()
        await logos.get(LOGO_URL, BROWSER)
        stale = time.time() - logos_module.LOGO_TTL_S - 60
        for path in logos_dir.iterdir():
            os.utime(path, (stale, stale))

        data, _ = await logos.get(LOGO_URL, BROWSER)

        assert len(session.fetched) == 2
        assert _decode(data).size == (300, 300)

    async def test_initialize_drops_what_expired_and_keeps_the_rest(self, logos_dir):
        logos_dir.mkdir(parents=True)
        fresh = logos_dir / "a.webp"
        expired_logo = logos_dir / "b.webp"
        expired_miss = logos_dir / "c.miss"
        leftover = logos_dir / "d.webp.123.4.tmp"
        for path in (fresh, expired_logo, expired_miss, leftover):
            path.write_bytes(b"x")
        old = time.time() - logos_module.LOGO_TTL_S - 60
        os.utime(expired_logo, (old, old))
        old_miss = time.time() - logos_module.MISS_TTL_S - 60
        os.utime(expired_miss, (old_miss, old_miss))

        await StationLogos().initialize()

        assert sorted(p.name for p in logos_dir.iterdir()) == ["a.webp"]


class TestTheRoute:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        source = Mock()
        source.logos = StationLogos()
        app.include_router(setup_radio_routes(lambda: source), prefix="/api")
        return TestClient(app)

    def test_a_logo_is_served_so_nothing_it_carries_can_run(self, client, fetches, public):
        """The bytes come from a host the directory named, served from Milō's
        own origin: a tab opened on this URL must not run anything they carry."""
        fetches(_Resp(200, _png(200, 200)))

        response = client.get("/api/radio/favicon", params={"url": LOGO_URL},
                              headers={"Accept": BROWSER})

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"].startswith("default-src 'none'")
        assert response.headers["vary"] == "Accept"
        assert response.headers["access-control-allow-origin"] == "*"

    def test_no_logo_is_204_so_every_client_draws_its_avatar(self, client, fetches, public):
        fetches(_Resp(404))

        response = client.get("/api/radio/favicon", params={"url": LOGO_URL})

        assert response.status_code == 204
        assert response.content == b""
