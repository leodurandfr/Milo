"""The server's half of the satellites' secret (core/multiroom/satellite_http.py).

A satellite pins the first secret it is handed and refuses its update, remote
access and reboot to every request that does not carry it. So what breaks here
breaks on a speaker, silently, at the next push: a key that changes across a
restart, one path that signs differently from the hand-over, or a relay that
sends a request bare. The satellite is faked as it behaves — trust on first
use, a 403 for anything else (milo-client/app/server_secret.py, whose own tests
drive the real thing) — and the key lives under tmp_path.
"""
import logging
import stat
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.multiroom import create_multiroom_router
from backend.core.multiroom.models import Client
from backend.core.multiroom.satellite_http import (
    SATELLITE_SECRET_HEADER,
    SatelliteSecret,
    SatelliteSecretUnavailable,
)

BUREAU = "d8:3a:dd:68:e7:e4"
BUREAU_IP = "192.168.1.60"
CANAPE = "dc:a6:32:7e:d3:43"


class PinningSatellite:
    """Satellites over HTTP, by address: each pins the first secret it is sent
    and answers 403 to a path to root that carries anything else — or, once
    `predates` is set, runs an app from before the secret: no hand-over route,
    and every request taken bare. An address in `following` was just plugged
    back in: its hand-over takes a new secret once, until the pinned one or a
    new one reaches it."""

    TO_ROOT = ("/api/system/ssh", "/api/system/password", "/api/hardware/reboot",
               "/api/system/server-secret")

    def __init__(self):
        self.pins = {}
        self.writes = []
        self.refusals = []
        self.answer_status = None
        self.predates = False
        self.following = set()

    def session(self, *args, **kwargs):
        return _Session(self)

    def receive(self, verb, url, headers):
        ip, path = url.split("//", 1)[1].split(":", 1)[0], "/" + url.split("/", 3)[3]
        if self.answer_status is not None:
            return _Answer(self.answer_status, {"detail": "answered by the test"})
        if verb == "GET":
            return _Answer(200, {"status": "success", "data": {
                "login_user": "milo", "password_is_default": False,
            }})
        pinned_now = False
        if self.predates and path == "/api/system/server-secret":
            return _Answer(404, {"detail": "Not Found"})
        if path in self.TO_ROOT and not self.predates:
            presented = (headers or {}).get(SATELLITE_SECRET_HEADER)
            if presented and ip not in self.pins:
                self.pins[ip] = presented
                pinned_now = True
            elif presented and path == "/api/system/server-secret" and ip in self.following:
                pinned_now = presented != self.pins[ip]
                self.pins[ip] = presented
            if presented == self.pins.get(ip):
                self.following.discard(ip)
            if not presented or presented != self.pins.get(ip):
                self.refusals.append((verb, path))
                return _Answer(403, {"detail": "Refused: it carries another server's secret"})
        self.writes.append((verb, path))
        return _Answer(200, {"status": "success", "data": {"changed": pinned_now}})


class _Answer:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _Session:
    def __init__(self, satellites):
        self._satellites = satellites

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None, **kwargs):
        return self._satellites.receive("GET", url, headers)

    def put(self, url, headers=None, **kwargs):
        return self._satellites.receive("PUT", url, headers)

    def post(self, url, headers=None, **kwargs):
        return self._satellites.receive("POST", url, headers)


@pytest.fixture
def satellites(monkeypatch):
    world = PinningSatellite()
    monkeypatch.setattr(aiohttp, "ClientSession", world.session)
    return world


@pytest.fixture
def relays(satellite_secret):
    """The multiroom router over a registry holding Bureau, a proof of presence given."""
    registry = Mock()
    registry.get_client = Mock(side_effect={
        BUREAU: Client(mac_id=BUREAU, name="Bureau", ip=BUREAU_IP, online=True),
    }.get)
    registry.update_client = AsyncMock()
    registry.set_client_online = AsyncMock()
    presence = Mock()
    presence.require = AsyncMock()
    app = FastAPI()
    app.include_router(create_multiroom_router(
        registry, presence_service=presence, satellite_secret=satellite_secret,
    ))
    return TestClient(app)


async def test_a_restart_hands_each_satellite_the_secret_it_pinned(tmp_path):
    """A key made anew at every start is a server every satellite refuses
    after its first restart."""
    key_file = tmp_path / "satellite-key"
    first = SatelliteSecret(key_file=key_file)
    assert await first.initialize()

    after_restart = SatelliteSecret(key_file=key_file)
    assert await after_restart.initialize()

    assert await after_restart.headers(BUREAU) == await first.headers(BUREAU)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600, "the key is readable by no other user"


async def test_each_satellite_is_handed_its_own_secret(satellite_secret):
    """One satellite taken over learns nothing that opens another."""
    assert await satellite_secret.headers(BUREAU) != await satellite_secret.headers(CANAPE)
    # The registry keys a speaker by its lowercase MAC; the heartbeat's, by
    # its validator, the same: a pending speaker pins what its admission sends.
    assert await satellite_secret.headers(BUREAU.upper()) == await satellite_secret.headers(BUREAU)


async def test_a_key_that_does_not_parse_is_refused_not_replaced(tmp_path, caplog):
    """Replaced, it would be a new key: every satellite already paired would
    refuse this server from then on, with nothing saying why."""
    key_file = tmp_path / "satellite-key"
    key_file.write_text("not a key\n")
    secret = SatelliteSecret(key_file=key_file)

    with caplog.at_level(logging.ERROR):
        assert await secret.initialize() is False

    assert key_file.read_text() == "not a key\n"
    assert any("does not hold a key" in r.getMessage() for r in caplog.records)
    with pytest.raises(SatelliteSecretUnavailable):
        await secret.headers(BUREAU)


async def test_every_path_to_root_signs_with_the_secret_the_admission_handed_over(
    satellite_secret, satellites, relays,
):
    """The satellite pins what reaches it first and refuses anything else: one
    path signing otherwise is a speaker whose panel or reboot stopped working."""
    await satellite_secret.hand_over(BUREAU, BUREAU_IP)

    assert relays.put(f"/api/multiroom/clients/{BUREAU}/ssh", json={"enabled": True}).status_code == 200
    assert relays.put(f"/api/multiroom/clients/{BUREAU}/ssh", json={"enabled": False}).status_code == 200
    assert relays.post(
        f"/api/multiroom/clients/{BUREAU}/password", json={"password": "correct horse"},
    ).status_code == 200
    assert relays.put(
        f"/api/multiroom/clients/{BUREAU}/audio", json={"audio_id": "hifiberry_amp2"},
    ).status_code == 200

    assert satellites.refusals == []
    assert ("POST", "/api/hardware/reboot") in satellites.writes


@pytest.mark.parametrize("method,path,body", [
    ("put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": False}),
    ("put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": True}),
    ("post", f"/api/multiroom/clients/{BUREAU}/password", {"password": "correct horse"}),
    ("put", f"/api/multiroom/clients/{BUREAU}/audio", {"audio_id": "hifiberry_dacplus_pro"}),
], ids=["closing ssh", "opening ssh", "password", "dac card, its trim first"])
async def test_a_satellite_that_pinned_another_server_is_refused_before_anything_moves(
    tmp_path, satellite_secret, satellites, method, path, body,
):
    """A server reflashed since, or a host that reached the speaker first.
    Found out only at the last request, the trim would be gone (the speaker
    louder), the card on disk for its next power cut, and the owner's gesture
    spent on a request the satellite then refuses."""
    stranger = SatelliteSecret(key_file=tmp_path / "another-key")
    await stranger.hand_over(BUREAU, BUREAU_IP)
    satellites.writes.clear()
    registry = Mock()
    registry.get_client = Mock(return_value=Client(
        mac_id=BUREAU, name="Bureau", ip=BUREAU_IP, online=True, gain_db=8.0,
    ))
    registry.set_client_gain = AsyncMock()
    presence = Mock()
    presence.require = AsyncMock()
    app = FastAPI()
    app.include_router(create_multiroom_router(
        registry, presence_service=presence, satellite_secret=satellite_secret,
    ))

    response = getattr(TestClient(app), method)(path, json=body)

    assert response.status_code == 409, response.text
    assert satellites.refusals == [("PUT", "/api/system/server-secret")]
    assert satellites.writes == []
    registry.set_client_gain.assert_not_awaited()
    presence.require.assert_not_awaited()


async def test_a_satellite_whose_app_predates_the_secret_still_takes_the_relays(
    satellite_secret, satellites, relays,
):
    """Its app requires no secret and has no hand-over route: refused for the
    404, a speaker one push behind would lose its panel and its reboot until
    a push nobody could make reached it."""
    satellites.predates = True

    response = relays.put(f"/api/multiroom/clients/{BUREAU}/ssh", json={"enabled": False})

    assert response.status_code == 200, response.text
    assert ("PUT", "/api/system/ssh") in satellites.writes


@pytest.mark.parametrize("method,path,body", [
    ("put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": False}),
    ("put", f"/api/multiroom/clients/{BUREAU}/audio", {"audio_id": "hifiberry_amp2"}),
    ("put", f"/api/multiroom/clients/{BUREAU}/audio", {"audio_id": "hifiberry_dacplus_pro"}),
], ids=["ssh", "audio card and reboot", "dac card, its trim first"])
def test_a_server_without_its_key_sends_nothing(satellites, method, path, body):
    """Refused here rather than sent bare, and before anything moves: a card
    written for a reboot the satellite then refuses changes the room's output
    at its next power cut, and a trim dropped for it leaves the speaker louder."""
    registry = Mock()
    registry.get_client = Mock(return_value=Client(
        mac_id=BUREAU, name="Bureau", ip=BUREAU_IP, online=True, gain_db=8.0,
    ))
    registry.set_client_gain = AsyncMock()
    app = FastAPI()
    app.include_router(create_multiroom_router(registry))

    response = getattr(TestClient(app), method)(path, json=body)

    assert response.status_code == 503
    assert satellites.writes == []
    registry.set_client_gain.assert_not_awaited()


async def test_a_satellite_that_holds_another_servers_secret_is_said_once(
    satellite_secret, satellites, caplog,
):
    """Once, since every admission tries again; at warning, since Settings ›
    Multiroom announces it with the gesture that moves it — at error, a
    reflashed server would open its banner once per speaker on top of that."""
    satellites.answer_status = 403

    with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.satellite_http"):
        await satellite_secret.hand_over(BUREAU, BUREAU_IP)
        await satellite_secret.hand_over(BUREAU, BUREAU_IP)

    said = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [r.levelno for r in said] == [logging.WARNING]
    assert "holds another server's" in said[0].getMessage()


async def test_a_satellite_whose_app_predates_the_hand_over_is_said_once_at_info(
    satellite_secret, satellites, caplog,
):
    """Every admission of a speaker one push behind meets the 404: at warning,
    or said each time, it would fill the banner for as long as it waits."""
    satellites.predates = True

    with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.satellite_http"):
        await satellite_secret.hand_over(BUREAU, BUREAU_IP)
        await satellite_secret.hand_over(BUREAU, BUREAU_IP)

    said = [r for r in caplog.records if "predates" in r.getMessage()]
    assert [r.levelno for r in said] == [logging.INFO]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_server_with_no_key_says_so_once(tmp_path, satellites, caplog):
    """The boot already said it; every admission after it meets the same
    missing key, and at error each time it would bury the banner."""
    key_file = tmp_path / "satellite-key"
    key_file.write_text("not a key\n")
    secret = SatelliteSecret(key_file=key_file)

    with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.satellite_http"):
        await secret.initialize()
        await secret.hand_over(BUREAU, BUREAU_IP)
        await secret.hand_over(CANAPE, BUREAU_IP)

    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1
    assert satellites.writes == []
