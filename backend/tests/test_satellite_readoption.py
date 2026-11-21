"""A speaker that answers another server is announced, and follows this one once plugged back in.

A reflashed server holds a new satellite key, so every speaker that pinned the
old one refuses its update, remote access and reboot. Owner decision: Multiroom
says so — "another server's speaker: unplug it, then plug it back in" — and the
gesture moves it, because a satellite plugged back in takes a new server's
secret on the hand-over for a few minutes (milo-client/app/server_secret.py,
whose own tests drive that window).

What breaks when these fail: the panel shows a speaker as fine while every
update and reboot to it is refused, or the gesture moves nothing because no
hand-over reaches the speaker while it would follow, or every heartbeat of a
foreign speaker is a request to it, or a speaker merely waiting to be
configured pins this server before anyone configured it. The satellite is the fake of
test_satellite_secret.py, which pins on first use and refuses anything else;
the registry and the pending store are the real ones, on tmp_path.
"""
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import httpx
import pytest
from fastapi import FastAPI

from backend.api.multiroom import create_multiroom_router
from backend.core.multiroom import pending_clients as pending_clients_module
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import RegistryEventType
from backend.core.multiroom.pending_clients import PendingClientsService
from backend.core.multiroom.satellite_http import SATELLITE_SECRET_HEADER, SatelliteSecret
from backend.tests.test_satellite_secret import BUREAU, BUREAU_IP, PinningSatellite

HAND_OVER = ("PUT", "/api/system/server-secret")
# What the server before the reflash handed Bureau: a key this one never holds.
OLD_SERVERS_SECRET = "0d" * 32


@pytest.fixture
def satellites(monkeypatch):
    world = PinningSatellite()
    monkeypatch.setattr(aiohttp, "ClientSession", world.session)
    return world


@pytest.fixture
def pending_file(monkeypatch, tmp_path):
    path = tmp_path / "pending_clients.json"
    monkeypatch.setattr(pending_clients_module, "PENDING_CLIENTS_FILE", path)
    return path


def _pending_store(state_machine=None):
    if state_machine is None:
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
    return PendingClientsService(state_machine=state_machine)


@pytest.fixture
async def world(tmp_path, pending_file):
    """This server: its key, its registry, its pending store, the secret
    recording into both, and the multiroom router over them."""
    registry = ClientRegistryService(settings_service=None)
    broadcasts = MagicMock()
    broadcasts.broadcast = AsyncMock()
    pending = _pending_store(broadcasts)
    await pending.initialize()
    secret = SatelliteSecret(
        key_file=tmp_path / "satellite-key",
        client_registry_service=registry, pending_clients_service=pending,
    )
    events = []

    async def record(event_type, data):
        events.append((event_type, data))

    registry.subscribe(record)
    app = FastAPI()
    app.include_router(create_multiroom_router(
        registry, pending_clients_service=pending, satellite_secret=secret,
    ))
    yield registry, pending, secret, events, app, broadcasts
    await pending.shutdown()


async def _heartbeat(app, **body):
    """One registration POST from Bureau; its background tasks run before it returns."""
    transport = httpx.ASGITransport(app=app, client=(BUREAU_IP, 41000))
    async with httpx.AsyncClient(transport=transport, base_url="http://milo") as satellite:
        response = await satellite.post("/api/multiroom/register-client", json={
            "mac_id": BUREAU, "ip": BUREAU_IP, "hardware_configured": False,
            "audio_id": "none", **body,
        })
    assert response.status_code == 200, response.text


def _hand_overs(fake):
    return [r for r in fake.writes + fake.refusals if r == HAND_OVER]


def _pending_events(broadcasts):
    """The pending-client events the panel was sent, in order."""
    return [call.args[0] for call in broadcasts.broadcast.await_args_list]


async def test_a_configured_speaker_that_answers_another_server_is_announced(world, satellites):
    """The reflashed server's first sight of a speaker that kept running: its
    first heartbeat is asked ahead of its admission — the only one with
    multiroom off —, and the refusal reaches the panel."""
    _, pending, _, _, app, broadcasts = world
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET

    await _heartbeat(app, hardware_configured=True)

    assert pending.get_client(BUREAU)["other_server"] is True
    announced = [e for e in _pending_events(broadcasts) if e.action == "updated"]
    assert announced and announced[-1].client["other_server"] is True


async def test_a_known_speaker_is_not_asked_at_every_heartbeat(world, satellites):
    """Four heartbeats a minute: each would be a refused request in the
    speaker's journal, for as long as nobody unplugs it."""
    _, _, _, _, app, _ = world
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    await _heartbeat(app, hardware_configured=True)

    for _ in range(3):
        await _heartbeat(app, hardware_configured=True)

    assert len(_hand_overs(satellites)) == 1


async def test_a_configured_speaker_plugged_back_in_follows_this_server(world, satellites):
    """The gesture: the heartbeat of a speaker just plugged back in says it
    would follow, and is handed this server's secret — the announcement goes."""
    _, pending, secret, _, app, broadcasts = world
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    await _heartbeat(app, hardware_configured=True)
    satellites.following.add(BUREAU_IP)

    await _heartbeat(app, hardware_configured=True, accepts_new_server=True)

    assert satellites.pins[BUREAU_IP] == (await secret.headers(BUREAU))[SATELLITE_SECRET_HEADER]
    assert pending.get_client(BUREAU)["other_server"] is False
    assert _pending_events(broadcasts)[-1].client["other_server"] is False


async def test_a_speaker_waiting_to_be_configured_is_not_asked_at_its_heartbeats(world, satellites):
    """Its first contact is the reboot that configures it, a gesture someone
    makes. Asked at its heartbeat, a speaker merely on the network would pin
    this server — and, left waiting through this server's reflash, refuse the
    next one and need the unplug gesture for nothing."""
    _, pending, _, _, app, _ = world

    for _ in range(3):
        await _heartbeat(app)

    assert _hand_overs(satellites) == []
    assert BUREAU_IP not in satellites.pins
    assert "other_server" not in pending.get_client(BUREAU)


async def test_a_speaker_waiting_to_be_configured_follows_once_plugged_back_in(world, satellites):
    """One that holds another server's secret — a configure that pinned it
    and went no further — is asked only once it says it would follow."""
    _, pending, secret, _, app, _ = world
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    await _heartbeat(app)
    assert _hand_overs(satellites) == []
    satellites.following.add(BUREAU_IP)

    await _heartbeat(app, accepts_new_server=True)

    assert satellites.pins[BUREAU_IP] == (await secret.headers(BUREAU))[SATELLITE_SECRET_HEADER]
    assert pending.get_client(BUREAU)["other_server"] is False


async def test_a_restarted_backend_asks_its_pending_speakers_again(world, satellites, pending_file):
    """The file keeps an answer from before the restart, which says nothing
    of the speaker now: kept, a speaker moved meanwhile would stay announced."""
    _, pending, _, _, app, _ = world
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    await _heartbeat(app, hardware_configured=True)
    assert pending.get_client(BUREAU)["other_server"] is True

    restarted = _pending_store()
    await restarted.initialize()
    try:
        assert "other_server" not in restarted.get_client(BUREAU)
    finally:
        await restarted.shutdown()


async def test_an_admitted_speaker_that_answers_another_server_is_announced(world, satellites):
    """Its admission hands it the secret; the refusal lands on its record,
    which the panel draws."""
    registry, _, secret, events, _, _ = world
    await registry.register_client(BUREAU, "Bureau", BUREAU_IP, host="milo-client")
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    events.clear()

    await secret.hand_over(BUREAU, BUREAU_IP)  # what the admission spawns

    assert registry.get_client(BUREAU).other_server is True
    assert [(t, d["client"]["other_server"]) for t, d in events] == [
        (RegistryEventType.CLIENT_UPDATED, True)
    ]


async def test_an_admitted_speaker_plugged_back_in_follows_this_server(world, satellites):
    """With multiroom off on this server, no admission follows the gesture:
    the heartbeat that says the speaker would follow is the one hand-over."""
    registry, _, secret, _, app, _ = world
    await registry.register_client(BUREAU, "Bureau", BUREAU_IP, host="milo-client")
    satellites.pins[BUREAU_IP] = OLD_SERVERS_SECRET
    await secret.hand_over(BUREAU, BUREAU_IP)
    satellites.following.add(BUREAU_IP)

    await _heartbeat(app, hardware_configured=True, accepts_new_server=True)

    assert registry.get_client(BUREAU).other_server is False
    assert satellites.pins[BUREAU_IP] == (await secret.headers(BUREAU))[SATELLITE_SECRET_HEADER]


async def test_an_admitted_speakers_heartbeat_hands_nothing_over(world, satellites):
    """Its admission asks it; a heartbeat that says nothing changed asks nothing."""
    registry, _, _, _, app, _ = world
    await registry.register_client(BUREAU, "Bureau", BUREAU_IP, host="milo-client")

    await _heartbeat(app, hardware_configured=True)

    assert _hand_overs(satellites) == []
