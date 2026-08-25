# backend/tests/test_api_register_client.py
"""
POST /api/multiroom/register-client — the one route every satellite calls.

Why this file exists: measured 2026-08-25, the route's 26 lines ran at 0 % under
the whole suite, and `RegisterClientRequest`'s two validators had never been
entered either. It is the only surface a satellite reaches on its own — at boot,
then every 15 s for as long as it lives — and it carries three distinct paths
that nothing separated:

* the IP-origin gate, which is what keeps a body from naming somebody else's
  address. The registry's IP is what `_push_snapclient_config` and
  `_send_audio_config_and_reboot` aim at, so a wrong one sends a satellite's
  configuration and its reboot to whatever host the body named;
* the wifi-adopted first boot — `hardware_configured=true` with nothing in the
  registry yet — which stages the name and speaker type the wizard collected;
* the reinstall — a MAC already in the registry re-appearing unconfigured, whose
  stale entry must be gone *before* the new pending record is staged.

`validate_mac_id` is the other half: the registry, the per-client EQ records and
`settings.json: multiroom.client_equalizer[mac]` are all keyed by this string.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from unittest.mock import AsyncMock, Mock

from backend.api.models import RegisterClientRequest
from backend.api.multiroom import create_multiroom_router
from backend.core.multiroom.models import Client


SAT_IP = "192.168.1.153"
SAT_MAC = "dc:a6:32:7e:d3:43"

BODY = {
    "mac_id": SAT_MAC,
    "ip": SAT_IP,
    "hardware_configured": False,
    "audio_id": "hifiberry_amp2",
    "volume_control": True,
}


def _pending(**over):
    return {
        "mac_id": SAT_MAC, "ip": SAT_IP, "hardware_configured": False,
        "audio_id": "hifiberry_amp2", "volume_control": True,
        "name": None, "speaker_type": "bookshelf", **over,
    }


@pytest.fixture
def registry():
    service = Mock()
    service.get_client = Mock(return_value=None)
    service.update_client = AsyncMock()
    service.unregister_client = AsyncMock(return_value=True)
    return service


@pytest.fixture
def pending():
    service = Mock()
    service.register_client = AsyncMock(side_effect=lambda **kw: _pending(**{
        k: v for k, v in kw.items() if k != "mac_id"
    }))
    service.update_client = AsyncMock(return_value=_pending(name="Bureau"))
    service.get_all_clients = Mock(return_value=[])
    return service


@pytest.fixture
def client(registry, pending):
    """A test client whose transport reports the satellite's own address.

    Starlette defaults `request.client.host` to "testclient", which is exactly
    the mismatch the route is built to refuse — every request would 403 and
    nothing below the gate would ever run.
    """
    app = FastAPI()
    app.include_router(create_multiroom_router(registry, pending_clients_service=pending))
    return TestClient(app, client=(SAT_IP, 41000))


# =============================================================================
# The origin gate
# =============================================================================

class TestOriginGate:

    def test_a_body_naming_another_host_is_refused(self, registry, pending):
        """The declared IP is what the registry stores and what every later push
        aims at: the snapclient buffer config, the audio card, the reboot. A
        satellite that names the wrong address redirects all three at a host that
        never asked for them.
        """
        app = FastAPI()
        app.include_router(create_multiroom_router(registry, pending_clients_service=pending))
        impostor = TestClient(app, client=("192.168.1.99", 41000))

        response = impostor.post("/api/multiroom/register-client", json=BODY)

        assert response.status_code == 403
        pending.register_client.assert_not_awaited()

    def test_an_ipv4_mapped_ipv6_origin_still_matches(self, registry, pending):
        """A dual-stack satellite arrives as `::ffff:192.168.1.153`. Compared
        raw it never matches its own body, so it 403s at every heartbeat and
        never appears in the wizard — on a network where nothing looks wrong.
        """
        app = FastAPI()
        app.include_router(create_multiroom_router(registry, pending_clients_service=pending))
        dual_stack = TestClient(app, client=(f"::ffff:{SAT_IP}", 41000))

        response = dual_stack.post("/api/multiroom/register-client", json=BODY)

        assert response.status_code == 200
        pending.register_client.assert_awaited_once()


# =============================================================================
# The three paths below the gate
# =============================================================================

class TestConfiguredClient:

    def test_a_known_configured_client_is_not_staged_as_pending(
        self, client, registry, pending
    ):
        """Its heartbeat runs every 15 s. Staging it would put an already-paired
        speaker back in the wizard's "new speaker" list, for good.
        """
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True, volume_control=True
        )

        response = client.post(
            "/api/multiroom/register-client", json={**BODY, "hardware_configured": True}
        )

        assert response.status_code == 200
        pending.register_client.assert_not_awaited()
        registry.update_client.assert_not_awaited()

    def test_a_card_change_reported_by_the_satellite_reaches_the_registry(
        self, client, registry
    ):
        """The satellite owns this flag — it re-sends hardware.json's value every
        15 s, so the registry is the copy that drifts. A registry saying the
        speaker attenuates when its DAC does not is a room played at the wrong
        level.
        """
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True, volume_control=True
        )

        client.post("/api/multiroom/register-client", json={
            **BODY, "hardware_configured": True, "volume_control": False,
        })

        registry.update_client.assert_awaited_once_with(SAT_MAC, volume_control=False)

    def test_a_wifi_adopted_client_arrives_configured_and_unknown(
        self, client, registry, pending
    ):
        """The wifi flow writes the card and the identity on the speaker itself,
        so its first boot reports `hardware_configured=true` with nothing in the
        registry. Without the staging below that branch, the name and speaker
        type the operator typed during adoption are lost and the speaker joins
        as an unnamed one.
        """
        response = client.post("/api/multiroom/register-client", json={
            **BODY, "hardware_configured": True,
            "name": "Bureau", "speaker_type": "tower",
        })

        assert response.status_code == 200
        pending.register_client.assert_awaited_once()
        pending.update_client.assert_awaited_once_with(
            SAT_MAC, name="Bureau", speaker_type="tower"
        )
        assert response.json()["client"]["name"] == "Bureau"


class TestReinstall:

    def test_a_reinstalled_client_loses_its_stale_registry_entry_first(
        self, client, registry, pending
    ):
        """Order, not presence: both calls happen either way. Unregistering
        after the staging is what wipes the record just written, and the speaker
        never reaches the wizard — it is the second unregister that decides.
        """
        order = []
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=False
        )
        registry.unregister_client = AsyncMock(side_effect=lambda m: order.append("unregister"))
        pending.register_client.side_effect = lambda **kw: order.append("stage") or _pending()

        response = client.post("/api/multiroom/register-client", json=BODY)

        assert response.status_code == 200
        assert order == ["unregister", "stage"]

    def test_a_fresh_client_is_staged_with_what_it_declared(
        self, client, registry, pending
    ):
        """The wizard reads this record; an audio_id or IP dropped here is a
        speaker the operator cannot configure.
        """
        response = client.post("/api/multiroom/register-client", json=BODY)

        assert response.status_code == 200
        registry.unregister_client.assert_not_awaited()
        pending.register_client.assert_awaited_once_with(
            mac_id=SAT_MAC, ip=SAT_IP, hardware_configured=False,
            audio_id="hifiberry_amp2", volume_control=True,
        )

    def test_an_identity_the_pending_store_will_not_take_still_answers_a_client(
        self, client, pending
    ):
        """`update_client` returns None for an unknown MAC. Without the fallback
        the satellite gets `client: null` and its own registration parser has
        nothing to read.
        """
        pending.update_client = AsyncMock(return_value=None)

        response = client.post(
            "/api/multiroom/register-client", json={**BODY, "name": "Bureau"}
        )

        assert response.status_code == 200
        assert response.json()["client"]["mac_id"] == SAT_MAC


# =============================================================================
# What the payload is allowed to be
# =============================================================================

class TestRegisterClientRequest:

    def test_the_mac_is_lowercased_before_anything_keys_on_it(self):
        """`eth0/address` is lowercase on Linux, but the registry, the zones and
        `multiroom.client_equalizer[mac]` are all keyed by this string — an
        uppercase one from any other source is a second speaker holding half the
        settings of the first.
        """
        request = RegisterClientRequest(
            mac_id=SAT_MAC.upper(), ip=SAT_IP, hardware_configured=True
        )

        assert request.mac_id == SAT_MAC

    @pytest.mark.parametrize("mac", [
        "dc:a6:32:7e:d3:4g",   # right length, not hex
        "dc-a6-32-7e-d3-43",   # right length, wrong separator
        "dc:a6:32:7e:d3:433",  # too long
    ])
    def test_a_string_that_is_not_a_mac_is_refused(self, mac):
        """It becomes a registry key, a zone member and a settings.json section
        name; nothing downstream re-checks it.
        """
        with pytest.raises(ValidationError):
            RegisterClientRequest(mac_id=mac, ip=SAT_IP, hardware_configured=True)

    def test_a_blank_name_is_dropped_rather_than_stored(self):
        """A name of spaces would be staged as the speaker's display name, and
        the multiroom list would show a row with no label at all.
        """
        request = RegisterClientRequest(
            mac_id=SAT_MAC, ip=SAT_IP, hardware_configured=True, name="   "
        )

        assert request.name is None

    def test_a_padded_name_is_stored_trimmed(self):
        request = RegisterClientRequest(
            mac_id=SAT_MAC, ip=SAT_IP, hardware_configured=True, name="  Bureau  "
        )

        assert request.name == "Bureau"
