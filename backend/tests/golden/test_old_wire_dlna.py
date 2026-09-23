"""DLNA's old wire, scenario by scenario (see harness.py for the rules).

A stimulus is what the outside world says: gmediarender's renderer state as a
GENA event (or a GetPositionInfo poll) exposes it, the media server's artwork
over HTTP, the LAN's answer to an SSDP sweep, and the iTunes lookup.
"""
import asyncio
import struct
import zlib
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.dlna import metadata_reader as bridge_module
from backend.sources.dlna import server_resolver as resolver_module
from backend.sources.dlna import source as dlna_module
from backend.sources.dlna.source import DlnaSource
from backend.tests.golden.harness import (
    AsyncioProxy, TickGate, Wire, check_recording, make_settings,
    make_state_machine, make_systemd, settle,
)

FREEBOX = "http://192.168.1.254:8200"
SYNOLOGY = "http://192.168.1.10:50001"
FREEBOX_DESC = f"{FREEBOX}/rootDesc.xml"
SYNOLOGY_DESC = f"{SYNOLOGY}/desc/device.xml"


def _png(width: int, height: int, tag: bytes) -> bytes:
    """A cover as a media server serves it: a PNG whose header carries its size.

    Built by hand rather than encoded by Pillow, so the bytes — and the md5 the
    artwork URL carries onto the wire — cannot move with a library upgrade.
    """
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00" + tag)
        + chunk(b"IEND", b"")
    )


KIND_OF_BLUE = _png(800, 800, b"kind of blue")
SPACES = _png(500, 500, b"spaces")

_real_sleep = asyncio.sleep


async def _instant_settle_sleep(delay: float, *a, **k):
    """The unit's 1.5 s settle and a 1 s auto-stop pass at once; the default
    120 s pause timer still waits, so it only fires if a scenario asks."""
    return await _real_sleep(0 if delay <= 1.5 else delay, *a, **k)


# === The outside world ===

class Renderer:
    """gmediarender as the bridge's DmrDevice reads it, field for field."""

    def __init__(self, reachable: asyncio.Event) -> None:
        self._reachable = reachable
        self.transport_state: Optional[str] = "NO_MEDIA_PRESENT"
        self.media_title: Optional[str] = None
        self.media_artist: Optional[str] = None
        self.media_album_artist: Optional[str] = None
        self.media_album_name: Optional[str] = None
        self.media_image_url: Optional[str] = None
        self.current_track_uri: Optional[str] = None
        # Seconds, as async-upnp-client hands them over.
        self.media_position: Optional[float] = None
        self.media_duration: Optional[float] = None
        self.on_event = None
        self.alive = True

    async def async_subscribe_services(self, auto_resubscribe: bool = False) -> None:
        # The subscription is a network round trip to gmediarender, and the
        # state it reports comes back over it: never inside the transition
        # that started the renderer. Answering instantly would put the
        # connect-time READY on whichever side of `transitioning` the source's
        # own scheduling happens to leave it — see Dlna.select().
        await self._reachable.wait()

    async def async_unsubscribe_services(self) -> None:
        return None

    async def async_update(self) -> None:
        if not self.alive:
            raise ConnectionError("gmediarender is gone")


class _RendererDescription:
    """The UpnpFactory the bridge builds gmediarender's device from."""

    def __init__(self, requester, non_strict: bool = False) -> None:
        return None

    async def async_create_device(self, description_url: str) -> object:
        return object()


class _NotifyServer:
    def __init__(self, requester, source) -> None:
        self.event_handler = object()

    async def async_start_server(self) -> None:
        return None

    async def async_stop_server(self) -> None:
        return None


class _Http:
    """aiohttp as the source uses it to fetch a media server's artwork."""

    def __init__(self, files: Dict[str, bytes]) -> None:
        self._files = files

    def ClientTimeout(self, total: float) -> None:
        return None

    def ClientSession(self) -> "_Http":
        return self

    async def __aenter__(self) -> "_Http":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def get(self, url: str, timeout=None) -> "_Http._Reply":
        return _Http._Reply(self._files.get(url))

    class _Reply:
        def __init__(self, body: Optional[bytes]) -> None:
            self.status = 200 if body is not None else 404
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def read(self) -> bytes:
            return self._body


class _Lan:
    """The LAN as an SSDP sweep and a description fetch see it."""

    def __init__(self) -> None:
        # (LOCATION, ST, friendlyName) of each device that answers the M-SEARCH.
        self.devices: List[Tuple[str, str, str]] = [
            (FREEBOX_DESC, "urn:schemas-upnp-org:device:MediaServer:1", "Freebox Server"),
            (SYNOLOGY_DESC, "urn:schemas-upnp-org:device:MediaServer:1", "DiskStation"),
            ("http://192.168.1.40:80/description.xml", "upnp:rootdevice", "Hue Bridge"),
        ]

    async def async_search(self, async_callback, timeout, search_target) -> None:
        for location, st, _ in self.devices:
            await async_callback({"st": st, "usn": f"uuid:x::{st}", "location": location})

    def factory(self, requester, non_strict: bool = False) -> "_Lan":
        return self

    async def async_create_device(self, location: str):
        name = next(n for loc, _, n in self.devices if loc == location)
        return SimpleNamespace(friendly_name=name)


class _ITunes:
    """The iTunes cover lookup ArtworkResolver performs, answered from a table."""

    covers = {
        ("Nils Frahm", "Says", "Spaces"): "https://is1.example/nils/spaces/600x600bb.jpg",
    }

    def __init__(self, settings_service=None) -> None:
        return None

    async def resolve(self, artist: str, title: str, album: str = "") -> Optional[str]:
        return self.covers.get((artist, title, album))


class _Clock:
    """The loop clock as the source reads it (`get_running_loop().time()`).

    The source compares a polled position with where its own interpolation
    would be, and rate-limits confirmations by loop time; a real clock would
    make both depend on how fast the test ran. Time moves here only when a
    scenario lets a poll interval (10 s) go by.
    """

    def __init__(self) -> None:
        self.now = 5000.0

    def time(self) -> float:
        return self.now


class _SourceAsyncio(AsyncioProxy):
    def __init__(self, clock: _Clock) -> None:
        super().__init__(self._no_sleep)
        self._clock = clock

    async def _no_sleep(self, delay: float, *a, **k) -> None:
        raise AssertionError(f"unexpected sleep({delay}) in the DLNA source")

    def get_running_loop(self) -> _Clock:
        return self._clock


# === The adapter ===

class Dlna:
    """Adapter: how each outside-world stimulus reaches DlnaSource today."""

    def __init__(self, monkeypatch, settings=None):
        self.clock = _Clock()
        self.poll_gate = TickGate()
        self.reachable = asyncio.Event()
        self.renderer = Renderer(self.reachable)
        self.lan = _Lan()
        self.http_files: Dict[str, bytes] = {
            f"{FREEBOX}/art/kind-of-blue.png": KIND_OF_BLUE,
            f"{SYNOLOGY}/art/spaces.png": SPACES,
        }
        # gmediarender over UPnP: the device description, the GENA notify
        # server and the DmrDevice built on them all resolve to `self.renderer`.
        monkeypatch.setattr(bridge_module, "AiohttpRequester", lambda *a, **k: None)
        monkeypatch.setattr(bridge_module, "UpnpFactory", _RendererDescription)
        monkeypatch.setattr(bridge_module, "AiohttpNotifyServer", _NotifyServer)
        monkeypatch.setattr(bridge_module, "DmrDevice", lambda device, event_handler: self.renderer)
        monkeypatch.setattr(bridge_module, "get_local_ip", lambda *a: "192.168.1.50")
        # The poll interval and the reconnect delay are one gate: each opening
        # is one pass of the bridge's supervise loop.
        monkeypatch.setattr(bridge_module, "asyncio", AsyncioProxy(self.poll_gate.sleep))
        monkeypatch.setattr(resolver_module, "async_search", self.lan.async_search)
        monkeypatch.setattr(resolver_module, "UpnpFactory", self.lan.factory)
        monkeypatch.setattr(resolver_module, "AiohttpRequester", lambda *a, **k: None)
        monkeypatch.setattr(dlna_module, "aiohttp", _Http(self.http_files))
        monkeypatch.setattr(dlna_module, "ArtworkResolver", _ITunes)
        monkeypatch.setattr(dlna_module, "asyncio", _SourceAsyncio(self.clock))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(_instant_settle_sleep))
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = DlnaSource(
            {"host": "192.168.1.50"},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.DLNA, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.DLNA)
        await settle()
        # The renderer's subscription answers only once the transition is
        # over; it stays open for every later (re)subscribe.
        self.reachable.set()
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def renderer_says(self, **fields):
        """The renderer's state changes and it sends a GENA LastChange event."""
        for name, value in fields.items():
            assert hasattr(self.renderer, name), name
            setattr(self.renderer, name, value)
        self.renderer.on_event(None, [])
        await settle()

    async def poll(self, position: Optional[float] = None):
        """A poll interval goes by; the renderer now reports `position` (s)."""
        if position is not None:
            self.renderer.media_position = position
        self.clock.now += 10.0
        await self.poll_gate.tick()

    def renderer_restarts(self):
        """gmediarender goes away; the one systemd brings back has no media."""
        self.renderer.alive = False
        self.renderer = Renderer(self.reachable)


def track(title, artist, album, art=None, uri=None, duration=None, position=0.0):
    return dict(
        media_title=title, media_artist=artist, media_album_name=album,
        media_image_url=art, current_track_uri=uri,
        media_duration=duration, media_position=position,
    )


SO_WHAT = track("So What", "Miles Davis", "Kind of Blue",
                art=f"{FREEBOX}/art/kind-of-blue.png",
                uri=f"{FREEBOX}/media/so-what.flac", duration=562.0)
FREDDIE = track("Freddie Freeloader", "Miles Davis", "Kind of Blue",
                art=f"{FREEBOX}/art/kind-of-blue.png",
                uri=f"{FREEBOX}/media/freddie.flac", duration=586.0)
SAYS = track("Says", "Nils Frahm", "Spaces",
             uri=f"{FREEBOX}/media/says.flac", duration=498.0)
UNKNOWN = track("Untitled 3", "Nobody", "Demos",
                uri=f"{FREEBOX}/media/untitled.flac", duration=120.0)
SPACES_TRACK = track("Hammers", "Nils Frahm", "Spaces",
                     art=f"{SYNOLOGY}/art/spaces.png",
                     uri=f"{SYNOLOGY}/media/hammers.flac", duration=390.0)


@pytest.fixture
def dlna(monkeypatch):
    return Dlna(monkeypatch)


async def _controller_starts_so_what(dlna: Dlna):
    await dlna.renderer_says(transport_state="TRANSITIONING", **SO_WHAT)
    await dlna.renderer_says(transport_state="PLAYING")


async def test_select_and_leave(dlna):
    await dlna.select()
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "select_and_leave", dlna.wire)


async def test_controller_plays_a_track_with_cover(dlna):
    await dlna.select()
    await _controller_starts_so_what(dlna)
    await dlna.wire.snapshot_rest()
    await dlna.poll(position=10.0)               # confirms the interpolation
    await dlna.poll(position=20.0)
    await dlna.poll(position=200.0)              # a seek on the controller
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "controller_plays_a_track_with_cover", dlna.wire)


async def test_controller_pauses_and_resumes(dlna):
    await dlna.select()
    await _controller_starts_so_what(dlna)
    await dlna.poll(position=10.0)
    await dlna.renderer_says(transport_state="PAUSED_PLAYBACK")
    await dlna.wire.snapshot_rest()
    await dlna.poll()                            # paused: the playhead holds
    await dlna.renderer_says(transport_state="PLAYING")
    await dlna.poll(position=20.0)
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "controller_pauses_and_resumes", dlna.wire)


async def test_track_changes_and_the_cover_follows(dlna):
    await dlna.select()
    await _controller_starts_so_what(dlna)
    await dlna.renderer_says(**FREDDIE)          # same album, same art URL
    await dlna.wire.snapshot_rest()
    await dlna.renderer_says(**SAYS)             # no art: looked up from the text
    await dlna.wire.snapshot_rest()
    await dlna.renderer_says(**UNKNOWN)          # no art, and nothing found
    await dlna.wire.snapshot_rest()
    await dlna.renderer_says(**SPACES_TRACK)     # served by another media server
    await dlna.poll(position=10.0)
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "track_changes_and_the_cover_follows", dlna.wire)


async def test_controller_stops(dlna):
    await dlna.select()
    await _controller_starts_so_what(dlna)
    await dlna.poll(position=10.0)
    await dlna.renderer_says(transport_state="STOPPED", media_position=0.0)
    await dlna.wire.snapshot_rest()
    await dlna.poll()
    await dlna.deselect()
    check_recording("dlna", "controller_stops", dlna.wire)


async def test_pause_auto_stops_then_resumes(monkeypatch):
    dlna = Dlna(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await dlna.select()
    await _controller_starts_so_what(dlna)
    await dlna.poll(position=10.0)
    await dlna.renderer_says(transport_state="PAUSED_PLAYBACK")  # the 1 s timer fires
    await dlna.wire.snapshot_rest()
    await dlna.renderer_says(transport_state="PLAYING")          # same track, re-announced
    await dlna.poll(position=20.0)
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "pause_auto_stops_then_resumes", dlna.wire)


async def test_renderer_restarts_mid_track(dlna):
    await dlna.select()
    await _controller_starts_so_what(dlna)
    dlna.renderer_restarts()
    await dlna.poll(position=10.0)               # the poll finds it gone
    await dlna.wire.snapshot_rest()
    await dlna.poll()                            # the reconnect delay: subscribes again
    await dlna.wire.snapshot_rest()
    await dlna.renderer_says(transport_state="PLAYING", **SAYS)
    await dlna.wire.snapshot_rest()
    await dlna.deselect()
    check_recording("dlna", "renderer_restarts_mid_track", dlna.wire)
