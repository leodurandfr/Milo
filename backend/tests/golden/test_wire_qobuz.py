"""Qobuz's wire, scenario by scenario (see harness.py for the rules).

The outside world is qobuz-proxy's GET /api/status as milo-qobuz extends it,
polled about once a second. FakeQobuzProxy answers it; the real QobuzMonitor runs over it with its poll
sleep gated, so one `tick()` is one poll and a stimulus is the payload the
proxy answers from then on. Qobuz takes no command (Family B).
"""
import copy
import json

import aiohttp
import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.qobuz import account as account_module
from backend.sources.qobuz import monitor as monitor_module
from backend.sources.qobuz import source as qobuz_module
from backend.sources.qobuz.source import QobuzSource
from backend.tests.golden.harness import (
    AsyncioProxy, LiveProcessWatch, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)

NIGHTCALL = {
    "title": "Nightcall",
    "artist": "Kavinsky",
    "album": "OutRun",
    "album_art_url": "https://static.qobuz.example/nightcall.jpg",
}
ALIVE = {
    "title": "Alive",
    "artist": "Daft Punk",
    "album": "Homework",
    "album_art_url": "https://static.qobuz.example/alive.jpg",
}


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return copy.deepcopy(self._payload)


class _Exchange:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeQobuzProxy:
    """qobuz-proxy's local HTTP API, answering whatever the scenario last set."""

    def __init__(self):
        self.http_status = 200
        self.authenticated = True
        self.speaker_status = "idle"
        self.now_playing = None
        self.player_state = "stopped"
        self.renderer_active = False
        self.polls = 0

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a, **k):
        return self

    def get(self, url, *a, **k):
        self.polls += 1
        speaker = {
            "id": "mil",
            "name": "Milō",
            "status": self.speaker_status,
            "config": {"audio_device": "milo_qobuz"},
            "player_state": self.player_state,
            "renderer_active": self.renderer_active,
        }
        if self.now_playing is not None:
            speaker["now_playing"] = copy.deepcopy(self.now_playing)
        payload = {"auth": {"authenticated": self.authenticated}, "speakers": [speaker]}
        return _Exchange(_Response(self.http_status, payload))

    async def close(self):
        return None

    # -- what the proxy reports ---------------------------------------------

    def reports(self, speaker_status, track=None, position_ms=None, duration_ms=None):
        """The app's session on this speaker, in upstream's words. Its "idle"
        under a session is a track loading (measured: ~100 ms at every skip)."""
        self.speaker_status = speaker_status
        self.renderer_active = True
        self.player_state = {"idle": "loading"}.get(speaker_status, speaker_status)
        if track is None:
            self.now_playing = {} if speaker_status in ("playing", "paused") else None
            return
        self.now_playing = {**track, "position_ms": position_ms, "duration_ms": duration_ms}


    def app_leaves(self):
        """The app picks another output: SET_ACTIVE(false), the player stopped."""
        self.speaker_status, self.now_playing = "idle", None
        self.renderer_active, self.player_state = False, "stopped"


class _AiohttpProxy:
    """The monitor module's `aiohttp`, with ClientSession answered by the fake."""

    def __init__(self, session):
        self.ClientSession = session

    def __getattr__(self, name):
        return getattr(aiohttp, name)


class Qobuz:
    """Adapter: how each outside-world stimulus reaches QobuzSource today."""

    def __init__(self, monkeypatch, tmp_path):
        self.proxy = FakeQobuzProxy()
        self.gate = TickGate()
        monkeypatch.setattr(monitor_module, "aiohttp", _AiohttpProxy(self.proxy))
        monkeypatch.setattr(monitor_module, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", LiveProcessWatch)
        # The volume-policy flag lives under /var/lib/milo on a unit.
        self.volume_flag = tmp_path / "allow_app_volume"
        monkeypatch.setattr(qobuz_module, "QOBUZ_VOLUME_FLAG", self.volume_flag)
        # A unit whose account is logged in: the sidecar's token cache, which
        # the source reads at boot (availability.qobuz).
        credentials = tmp_path / "credentials.json"
        credentials.write_text(json.dumps({"user_id": "1", "user_auth_token": "t"}))
        monkeypatch.setattr(account_module, "QOBUZ_CREDENTIALS_FILE", credentials)
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = QobuzSource(
            None,
            state_machine=self.machine,
            settings_service=make_settings({"qobuz": {"allow_app_volume": False}}),
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.QOBUZ, self.source)

    async def select(self):
        if not self.source.is_initialized:
            # The backend's boot (initialize_services), long before a select.
            await self.source.initialize()
        await self.machine.transition_to_source(AudioSource.QOBUZ)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def tick(self, times=1):
        """`times` polls of /api/status go by."""
        await self.gate.tick(times)


@pytest.fixture
def qobuz(monkeypatch, tmp_path):
    return Qobuz(monkeypatch, tmp_path)


async def _phone_starts(qobuz, track=NIGHTCALL, duration_ms=258000):
    """The Qobuz app casts a track: two trackless ticks, then the track."""
    qobuz.proxy.reports("playing")
    await qobuz.tick(2)
    qobuz.proxy.reports("playing", track, 0, duration_ms)
    await qobuz.tick()


async def test_select_and_leave(qobuz):
    await qobuz.select()
    await qobuz.tick(3)                          # idle polls publish nothing
    await qobuz.wire.snapshot_rest()
    await qobuz.deselect()
    assert qobuz.volume_flag.read_text() == "0"
    check_recording("qobuz", "select_and_leave", qobuz.wire)


async def test_session_opens_and_plays(qobuz):
    await qobuz.select()
    await _phone_starts(qobuz)
    for position in (1000, 2000, 3000):          # the poll is the progress feed
        qobuz.proxy.reports("playing", NIGHTCALL, position, 258000)
        await qobuz.tick()
    await qobuz.wire.snapshot_rest()
    await qobuz.deselect()
    check_recording("qobuz", "session_opens_and_plays", qobuz.wire)


async def test_session_opens_without_a_track(qobuz):
    await qobuz.select()
    qobuz.proxy.reports("playing")
    await qobuz.tick(5)                          # past the trackless grace
    await qobuz.wire.snapshot_rest()
    qobuz.proxy.reports("playing", NIGHTCALL, 4000, 258000)
    await qobuz.tick()
    await qobuz.deselect()
    check_recording("qobuz", "session_opens_without_a_track", qobuz.wire)


async def test_pause_resume_from_phone(qobuz):
    await qobuz.select()
    await _phone_starts(qobuz)
    qobuz.proxy.reports("paused", NIGHTCALL, 5000, 258000)
    await qobuz.tick(2)
    await qobuz.wire.snapshot_rest()
    qobuz.proxy.reports("playing", NIGHTCALL, 5000, 258000)
    await qobuz.tick()
    await qobuz.deselect()
    check_recording("qobuz", "pause_resume_from_phone", qobuz.wire)


async def test_track_change_blips(qobuz):
    await qobuz.select()
    await _phone_starts(qobuz)
    qobuz.proxy.reports("playing", NIGHTCALL, 257000, 258000)
    await qobuz.tick()
    qobuz.proxy.reports("idle")                  # the between-tracks blip
    await qobuz.tick()
    qobuz.proxy.reports("playing")               # now_playing still empty
    await qobuz.tick()
    qobuz.proxy.reports("playing", ALIVE, 0, 0)  # length not resolved yet
    await qobuz.tick()
    await qobuz.wire.snapshot_rest()
    qobuz.proxy.reports("playing", ALIVE, 1000, 320000)
    await qobuz.tick()
    await qobuz.deselect()
    check_recording("qobuz", "track_change_blips", qobuz.wire)


async def test_phone_disconnects(qobuz):
    await qobuz.select()
    await _phone_starts(qobuz)
    qobuz.proxy.app_leaves()
    await qobuz.tick(3)                          # held through the idle grace
    await qobuz.wire.snapshot_rest()
    await qobuz.tick()                           # then committed to READY
    await qobuz.tick(2)
    await qobuz.wire.snapshot_rest()
    await qobuz.deselect()
    check_recording("qobuz", "phone_disconnects", qobuz.wire)


async def test_account_and_proxy_hiccups(qobuz):
    await qobuz.select()
    qobuz.proxy.authenticated = False            # the account logs out
    await qobuz.tick()
    await qobuz.wire.snapshot_rest()
    qobuz.proxy.authenticated = True             # and back in
    await qobuz.tick()
    await _phone_starts(qobuz)
    qobuz.proxy.http_status = 500                # the proxy answers 5xx: ticks skipped
    await qobuz.tick(4)
    await qobuz.wire.snapshot_rest()
    qobuz.proxy.http_status = 200
    qobuz.proxy.reports("playing", NIGHTCALL, 9000, 258000)
    await qobuz.tick()
    await qobuz.deselect()
    check_recording("qobuz", "account_and_proxy_hiccups", qobuz.wire)
