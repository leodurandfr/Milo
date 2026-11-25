# backend/tests/test_api_satellite_push.py
"""
The four /api/multiroom routes that reach a satellite over HTTP, and what they
do when it answers badly.

Why this file exists: measured 2026-08-25, every failure arm of
`_send_audio_config_and_reboot`, `_push_volume_control` and
`get_client_hardware` was at 0 %, and `delete_client` had never been entered.

`_send_audio_config_and_reboot`'s own comment carries the incident these arms
were written for: a fleet where every script-installed satellite answered 500 to
the reboot — its apply-hardware helper missing from its own rootfs tree — still
reported a successful pairing, for as long as one flashed unit worked. The rule
that came out of it is the subtle part, and it is a pair:

  * an *answered* non-200 on either step is fatal, because the satellite is up
    and refused: the overlay written a moment earlier will never take effect;
  * a *dropped connection* on the reboot step is a warning, because that is
    exactly what a satellite which really is rebooting looks like.

Get that pair backwards in either direction and the wizard either reports every
successful pairing as a failure, or every failed one as a success.
"""
import asyncio
import logging

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api.multiroom import create_multiroom_router
from backend.config.constants import CLIENT_API_PORT
from backend.core.multiroom.models import Client


MAC = "dc:a6:32:7e:d3:43"
IP = "192.168.1.60"


class _Satellite:
    """A scripted satellite API: one reply per (verb, path suffix)."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.sent = []

    def _reply_for(self, url):
        for suffix, reply in self.replies.items():
            if url.endswith(suffix):
                return reply
        if url.endswith("/api/system/server-secret"):
            # It holds this server's secret already.
            return {"status": 200, "json": {"status": "success", "data": {"changed": False}}}
        return {"status": 200, "json": {}}

    def install(self, monkeypatch):
        satellite = self

        class _Response:
            def __init__(self, reply):
                self.status = reply["status"]
                self._json = reply.get("json", {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def text(self):
                return ""

            async def json(self):
                return self._json

        def _verb(method):
            def call(self, url, **kwargs):
                satellite.sent.append((method, url))
                reply = satellite._reply_for(url)
                if isinstance(reply, Exception):
                    raise reply
                return _Response(reply)
            return call

        class _Session:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            get = _verb("get")
            put = _verb("put")
            post = _verb("post")

        monkeypatch.setattr(aiohttp, "ClientSession", _Session)
        return satellite


@pytest.fixture
def registry():
    known = Client(mac_id=MAC, name="Bureau", ip=IP, online=True, volume_control=True)
    service = Mock()
    service.get_client = Mock(return_value=known)
    service.update_client = AsyncMock(return_value=known)
    service.set_client_online = AsyncMock()
    service.unregister_client = AsyncMock(return_value=True)
    return service


@pytest.fixture
def pending():
    """`get_all_clients` answers a dict keyed by MAC, not a list — captured from
    `PendingClientsService`, whose response model declares `Dict[str, Any]`."""
    service = Mock()
    service.get_all_clients = Mock(return_value={MAC: {"mac_id": MAC, "ip": IP}})
    return service


@pytest.fixture
def client(registry, pending, satellite_secret):
    app = FastAPI()
    app.include_router(create_multiroom_router(
        registry, pending_clients_service=pending, satellite_secret=satellite_secret,
    ))
    return TestClient(app)


# =============================================================================
# The audio card + reboot push
# =============================================================================

class TestAudioConfigPush:

    def test_a_satellite_that_refuses_the_card_is_a_502(
        self, client, registry, monkeypatch
    ):
        """The overlay never lands, so the card does not change. Reporting
        success here is the pairing that looked done and produced no sound.
        """
        _Satellite({"/api/hardware/audio": {"status": 500}}).install(monkeypatch)

        response = client.put(
            f"/api/multiroom/clients/{MAC}/audio", json={"audio_id": "hifiberry_amp2"}
        )

        assert response.status_code == 502
        registry.update_client.assert_not_awaited()

    def test_a_satellite_that_answers_and_refuses_to_reboot_is_a_502(
        self, client, monkeypatch
    ):
        """This is the measured incident: a satellite whose apply-hardware
        helper is missing from its own rootfs tree answers 500 to the reboot.
        The overlay is on its disk and inert until something reboots it, so the
        card has not changed and the pairing has not happened.
        """
        satellite = _Satellite({"/api/hardware/reboot": {"status": 500}}).install(monkeypatch)

        response = client.put(
            f"/api/multiroom/clients/{MAC}/audio", json={"audio_id": "hifiberry_amp2"}
        )

        assert response.status_code == 502
        assert [(verb, url.split(":8001", 1)[1]) for verb, url in satellite.sent] == [
            ("put", "/api/system/server-secret"),
            ("put", "/api/hardware/audio"),
            ("post", "/api/hardware/reboot"),
        ], "the card must be written before the reboot is asked for, once the reboot's secret is taken"

    @pytest.mark.parametrize("dropped", [
        aiohttp.ClientError("connection reset"),
        asyncio.TimeoutError(),
    ])
    def test_a_connection_dropped_on_the_reboot_is_success_not_a_failure(
        self, client, registry, monkeypatch, dropped, caplog
    ):
        """A satellite that really is rebooting drops the connection mid-reply.
        Treating that as fatal reports every successful card change as failed,
        and the operator retries a reboot that already happened. The warning
        names the failure's class: a timeout's str() is empty, and interpolated
        alone it ended the line on a colon.
        """
        _Satellite({"/api/hardware/reboot": dropped}).install(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="backend.api.multiroom"):
            response = client.put(
                f"/api/multiroom/clients/{MAC}/audio", json={"audio_id": "hifiberry_amp2"}
            )

        assert response.status_code == 200
        [line] = [r.getMessage() for r in caplog.records if "Reboot request" in r.getMessage()]
        assert type(dropped).__name__ in line
        # A rebooting client is not an offline one: marking it so drops it out
        # of the multiroom list for the whole boot it was just asked to take.
        registry.set_client_online.assert_not_awaited()

    def test_a_satellite_that_cannot_be_reached_at_all_is_marked_offline(
        self, client, registry, monkeypatch
    ):
        """Unreachable on the *first* step means it was already down. Leaving it
        online keeps the multiroom list showing a speaker that is not there, and
        every later push aims at it again.
        """
        _Satellite({"/api/hardware/audio": aiohttp.ClientError("no route")}).install(monkeypatch)

        response = client.put(
            f"/api/multiroom/clients/{MAC}/audio", json={"audio_id": "hifiberry_amp2"}
        )

        assert response.status_code == 502
        registry.set_client_online.assert_awaited_once_with(MAC, False)


# =============================================================================
# The volume_control push
# =============================================================================

class TestVolumeControlPush:

    def test_the_flag_reaches_the_satellite_before_the_registry(
        self, client, registry, monkeypatch
    ):
        """The satellite owns this flag and re-sends it every 15 s. Writing the
        registry first and pushing after means a push that fails is undone by
        the next heartbeat, silently, with a 200 already answered.
        """
        satellite = _Satellite({
            "/api/hardware": {"status": 200, "json": {"audio": {"id": "hifiberry_amp2"}}},
        }).install(monkeypatch)

        response = client.patch(
            f"/api/multiroom/clients/{MAC}", json={"volume_control": False}
        )

        assert response.status_code == 200
        assert satellite.sent[0][0] == "get", "the satellite's own card was not read first"
        assert satellite.sent[-1] == ("put", f"http://{IP}:{CLIENT_API_PORT}/api/hardware/audio")
        registry.update_client.assert_awaited_once()

    def test_a_satellite_with_no_card_configured_is_a_400(self, client, registry, monkeypatch):
        """Writing `volume_control` onto `audio_id: none` hands the satellite a
        hardware.json with no overlay, which its next apply turns into a unit
        with no sound card at all.
        """
        _Satellite({
            "/api/hardware": {"status": 200, "json": {"audio": {"id": "none"}}},
        }).install(monkeypatch)

        response = client.patch(
            f"/api/multiroom/clients/{MAC}", json={"volume_control": False}
        )

        assert response.status_code == 400
        registry.update_client.assert_not_awaited()

    def test_a_satellite_that_refuses_the_flag_leaves_the_registry_alone(
        self, client, registry, monkeypatch
    ):
        _Satellite({
            "/api/hardware": {"status": 200, "json": {"audio": {"id": "hifiberry_amp2"}}},
            "/api/hardware/audio": {"status": 500},
        }).install(monkeypatch)

        response = client.patch(
            f"/api/multiroom/clients/{MAC}", json={"volume_control": False}
        )

        assert response.status_code == 502
        registry.update_client.assert_not_awaited()

    def test_the_local_client_is_never_pushed_to(self, client, registry, monkeypatch):
        """`milo-client` runs on satellites only, never on the server, so there
        is nothing listening on 127.0.0.1:8001. A push there is a request that
        cannot be answered, and changing the main unit's own volume management
        would come back a 502.
        """
        satellite = _Satellite().install(monkeypatch)
        local = Client(mac_id="local", name="Main", ip="127.0.0.1",
                       online=True, volume_control=True)
        registry.get_client.return_value = local
        registry.update_client = AsyncMock(return_value=local)

        response = client.patch("/api/multiroom/clients/local", json={"volume_control": False})

        assert response.status_code == 200
        assert satellite.sent == []
        registry.update_client.assert_awaited_once()

    def test_a_flag_that_is_not_changing_is_not_pushed_at_all(
        self, client, registry, monkeypatch
    ):
        """Renaming a speaker must not wake its satellite's HTTP surface; the
        registry already holds this value and a push here is a round trip that
        can only fail.
        """
        satellite = _Satellite().install(monkeypatch)

        response = client.patch(
            f"/api/multiroom/clients/{MAC}", json={"name": "Salon", "volume_control": True}
        )

        assert response.status_code == 200
        assert satellite.sent == []


# =============================================================================
# The reads
# =============================================================================

class TestHardwareRead:

    def test_the_satellite_answer_is_passed_through(self, client, monkeypatch):
        _Satellite({
            "/api/hardware": {"status": 200, "json": {"audio": {"id": "hifiberry_amp4"}}},
        }).install(monkeypatch)

        body = client.get(f"/api/multiroom/clients/{MAC}/hardware").json()

        assert body == {"audio": {"id": "hifiberry_amp4"}}

    def test_a_refusal_is_relayed_with_the_satellite_reason(self, client, registry, monkeypatch):
        """"Client returned an error" told the owner nothing the satellite had
        said; and an answered refusal is a satellite that is there."""
        _Satellite({
            "/api/hardware": {"status": 500, "json": {"detail": "hardware.json is unreadable"}},
        }).install(monkeypatch)

        response = client.get(f"/api/multiroom/clients/{MAC}/hardware")

        assert response.status_code == 502
        assert "hardware.json is unreadable" in response.json()["detail"]
        registry.set_client_online.assert_not_awaited()

    def test_an_unknown_client_is_a_404_before_any_request_goes_out(
        self, client, registry, monkeypatch
    ):
        satellite = _Satellite().install(monkeypatch)
        registry.get_client.return_value = None

        response = client.get(f"/api/multiroom/clients/{MAC}/hardware")

        assert response.status_code == 404
        assert satellite.sent == []


# =============================================================================
# The two ways a relay fails
# =============================================================================

# The three relays, each with the request whose failure is staged.
RELAYS = {
    "hardware read": (
        "/api/hardware", lambda c: c.get(f"/api/multiroom/clients/{MAC}/hardware"),
    ),
    "volume_control push": (
        "/api/hardware", lambda c: c.patch(f"/api/multiroom/clients/{MAC}", json={"volume_control": False}),
    ),
    "card change": (
        "/api/hardware/audio",
        lambda c: c.put(f"/api/multiroom/clients/{MAC}/audio", json={"audio_id": "hifiberry_amp2"}),
    ),
}


class TestARelayTellsSlowFromGone:
    """No answer in time is a satellite that is there, not one that is gone.

    The guard of the SSH, password and network routes held that rule while the
    three older relays marked a slow satellite offline: a loaded Pi that took
    more than 5 s over GET /api/hardware — opening its page, changing who owns
    its level — vanished from Settings › Multiroom while it played, until the
    next sweep, and a PUT it may have applied was answered "Cannot reach".
    """

    @pytest.mark.parametrize("relay", RELAYS)
    def test_a_slow_satellite_stays_online_and_answers_504(
        self, client, registry, monkeypatch, relay
    ):
        path, call = RELAYS[relay]
        _Satellite({path: asyncio.TimeoutError()}).install(monkeypatch)

        response = call(client)

        assert response.status_code == 504
        registry.set_client_online.assert_not_awaited()
        registry.update_client.assert_not_awaited()

    @pytest.mark.parametrize("relay", RELAYS)
    def test_a_satellite_that_cannot_be_reached_is_marked_offline_and_answers_502(
        self, client, registry, monkeypatch, relay
    ):
        path, call = RELAYS[relay]
        _Satellite({path: aiohttp.ClientConnectionError("no route")}).install(monkeypatch)

        response = call(client)

        assert response.status_code == 502
        registry.set_client_online.assert_awaited_once_with(MAC, False)
        registry.update_client.assert_not_awaited()


class TestDeleteAndPendingList:

    def test_deleting_a_client_reports_what_the_registry_did(self, client, registry):
        response = client.delete(f"/api/multiroom/clients/{MAC}")

        assert response.status_code == 200
        registry.unregister_client.assert_awaited_once_with(MAC)

    def test_deleting_a_client_that_is_not_there_is_a_404(self, client, registry):
        """The button is the one way out of a stale row in the multiroom list.
        A 200 over a registry that removed nothing leaves the row where it was
        and the user pressing again.
        """
        registry.unregister_client = AsyncMock(return_value=False)

        response = client.delete(f"/api/multiroom/clients/{MAC}")

        assert response.status_code == 404

    def test_the_pending_list_is_what_the_wizard_reads(self, client, pending):
        body = client.get("/api/multiroom/pending-clients").json()

        assert body["clients"] == pending.get_all_clients.return_value
