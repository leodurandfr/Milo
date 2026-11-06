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
import errno
import socket

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from unittest.mock import AsyncMock, Mock, patch

from backend.api.models import RegisterClientRequest
from backend.api.multiroom import create_multiroom_router
from backend.core.equalizer.client_proxy import SatelliteUnreachable
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.equalizer_router import EqualizerRouter
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
    service.set_client_gain = AsyncMock()
    service.unregister_client = AsyncMock(return_value=True)
    return service


@pytest.fixture
def satellite_dsp():
    """The satellite's EQ API as the proxy reaches it: what it was sent, in order."""
    proxy = Mock()
    proxy.request = AsyncMock(return_value={"status": "success", "gain_db": 0.0})
    return proxy


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
def client(registry, pending, satellite_dsp):
    """A test client whose transport reports the satellite's own address.

    Starlette defaults `request.client.host` to "testclient", which is exactly
    the mismatch the route is built to refuse — every request would 403 and
    nothing below the gate would ever run.
    """
    eq_router = EqualizerRouter(registry, None, satellite_dsp)
    eq_router.set_local_mac_source(lambda: "aa:bb:cc:dd:ee:ff")
    app = FastAPI()
    app.include_router(create_multiroom_router(
        registry, pending_clients_service=pending, equalizer_router=eq_router,
    ))
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

    def test_a_heartbeat_dac_flip_clears_the_trim_first(
        self, client, registry, satellite_dsp
    ):
        """A satellite reports its own DAC card (hardware.json is its copy), and
        the registry flag is what the router reads before it sends a trim. Moved
        first, it would skip the clear for good — and the satellite reloads the
        trim from its config at every boot: +8 dB over a level held at unity.
        """
        order = []
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True,
            volume_control=True, gain_db=8.0,
        )
        satellite_dsp.request.side_effect = (
            lambda ip, method, path, body=None:
                order.append((ip, method, path, body)) or {"status": "success"}
        )
        registry.update_client.side_effect = (
            lambda mac, **fields: order.append(("registry", mac, fields))
        )

        response = client.post("/api/multiroom/register-client", json={
            **BODY, "hardware_configured": True, "volume_control": False,
        })

        assert response.status_code == 200
        assert order == [
            (SAT_IP, "PUT", "/equalizer/gain", {"gain_db": 0.0}),
            ("registry", SAT_MAC, {"volume_control": False}),
        ]
        registry.set_client_gain.assert_awaited_once_with(SAT_MAC, 0.0)

    @pytest.mark.parametrize("status_code", [400, 503])
    def test_a_heartbeat_dac_flip_the_dsp_refused_keeps_the_flag(
        self, client, registry, satellite_dsp, status_code
    ):
        """The flag waits for its trim, and the heartbeat still answers: the
        satellite reads nothing from a refusal, and the next heartbeat, 15 s
        later, carries the same flip and tries the clear again.
        """
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True,
            volume_control=True, gain_db=8.0,
        )
        satellite_dsp.request.side_effect = SatelliteUnreachable(
            SAT_IP, f"Client error: {status_code}", status_code=status_code
        )

        response = client.post("/api/multiroom/register-client", json={
            **BODY, "hardware_configured": True, "volume_control": False,
        })

        assert response.status_code == 200
        satellite_dsp.request.assert_awaited_once()
        registry.update_client.assert_not_awaited()
        registry.set_client_gain.assert_not_awaited()

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


class TestIdentityAfterAdmission:
    """The other order of a wifi-adopted first boot, the one measured on Bureau:
    the satellite signals READY, snapclient starts and connects, and the first
    heartbeat leaves 5 s later. `Client.OnConnect` has then admitted the speaker
    under the fallback name, so the identity can only land on the heartbeat.

    The registry is the real one: what is asserted is the record the multiroom
    list and the crossover read, not the arguments of a call.
    """

    @pytest.fixture
    async def admitted(self):
        """A registry where OnConnect admitted the satellite before any heartbeat,
        with no pending identity: under its host's display name, the
        "Milō Client" Bureau was measured to carry."""
        reg = ClientRegistryService(settings_service=None)
        await reg.register_client(SAT_MAC, "Milō Client", SAT_IP, host="milo-client")
        events = []

        async def record(event_type, data):
            events.append((event_type, data))

        reg.subscribe(record)
        return reg, events

    @staticmethod
    def _satellite(registry, pending):
        app = FastAPI()
        app.include_router(create_multiroom_router(registry, pending_clients_service=pending))
        transport = httpx.ASGITransport(app=app, client=(SAT_IP, 41000))
        return httpx.AsyncClient(transport=transport, base_url="http://milo")

    async def _heartbeat(self, registry, pending, **identity):
        async with self._satellite(registry, pending) as satellite:
            response = await satellite.post("/api/multiroom/register-client", json={
                **BODY, "hardware_configured": True, **identity,
            })
        assert response.status_code == 200
        return response

    async def test_a_wifi_adopted_identity_lands_when_its_snapclient_was_admitted_first(
        self, admitted, pending
    ):
        """Without it the speaker stays "Milō Client" for good, and a subwoofer
        stays a bookshelf: `apply_zone_crossover` gives it no filter and it
        plays full range.
        """
        reg, _ = admitted

        await self._heartbeat(reg, pending, name="Salon", speaker_type="subwoofer")

        landed = reg.get_client(SAT_MAC)
        assert (landed.name, landed.speaker_type) == ("Salon", "subwoofer")
        pending.register_client.assert_not_awaited()

    async def test_a_heartbeat_never_reverts_a_rename(self, admitted, pending):
        """identity.json is never rewritten by the server, so the satellite sends
        the wizard's name at every heartbeat for as long as it lives. A rename
        the operator made afterwards must survive them.
        """
        reg, _ = admitted
        await self._heartbeat(reg, pending, name="Salon", speaker_type="subwoofer")
        await reg.update_client(SAT_MAC, name="Cuisine")

        for _ in range(3):
            await self._heartbeat(reg, pending, name="Salon", speaker_type="subwoofer")

        assert reg.get_client(SAT_MAC).name == "Cuisine"

    async def test_a_heartbeat_with_no_name_never_reverts_a_hand_picked_type(
        self, admitted, pending
    ):
        """Only a name closes the window: a type alone would leave the record on
        the fallback name, and every heartbeat would set the wizard's type back
        over the one the operator picked, rebroadcasting the crossover each time.
        """
        reg, events = admitted
        await reg.update_client(SAT_MAC, speaker_type="tower")
        events.clear()

        for _ in range(3):
            await self._heartbeat(reg, pending, speaker_type="subwoofer")

        assert reg.get_client(SAT_MAC).speaker_type == "tower"
        assert events == []

    async def test_an_identity_already_applied_writes_nothing_again(self, admitted, pending):
        """The heartbeat runs every 15 s: a write each time would persist
        settings.json and broadcast a `client_updated` four times a minute.
        """
        reg, events = admitted
        await self._heartbeat(reg, pending, name="Salon", speaker_type="subwoofer")
        events.clear()

        for _ in range(3):
            await self._heartbeat(reg, pending, name="Salon", speaker_type="subwoofer")

        assert events == []


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


# =============================================================================
# The server's addresses, in every answer
# =============================================================================

def _ioctl_answering(addresses):
    """SIOCGIFADDR as the kernel answers it: the interface's IPv4 at bytes 20-24,
    or EADDRNOTAVAIL for an interface with none."""
    def ioctl(fd, request, ifreq):
        name = ifreq[:16].rstrip(b"\0").decode()
        address = addresses.get(name)
        if address is None:
            raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        return ifreq[:20] + socket.inet_aton(address) + ifreq[24:]
    return ioctl


class TestServerAddresses:
    """The heartbeat answer lists every address the server answers on, wired first.

    A satellite whose stream drops tries them in that order. Without the list it
    can only follow `milo.local`, which keeps answering the wired address for the
    6 s NetworkManager takes to withdraw it after the cable goes: measured
    2026-11-06, ~10 s of silence on a real server cable pull.
    """

    @pytest.mark.parametrize("body, known", [
        ({**BODY, "hardware_configured": True}, True),    # a paired speaker's heartbeat
        ({**BODY, "hardware_configured": True}, False),   # wifi-adopted, not admitted yet
        (BODY, False),                                     # a speaker the wizard has not set up
    ])
    def test_every_answer_carries_the_addresses_wired_first(
        self, client, registry, body, known
    ):
        if known:
            registry.get_client.return_value = Client(
                mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True, volume_control=True
            )
        world = {"eth0": "192.168.1.55", "wlan0": "192.168.1.39"}
        with patch("fcntl.ioctl", side_effect=_ioctl_answering(world)):
            response = client.post("/api/multiroom/register-client", json=body)

        assert response.status_code == 200
        assert response.json()["server_addresses"] == ["192.168.1.55", "192.168.1.39"]

    def test_an_interface_without_an_address_is_left_out(self, client, registry):
        """The server on WiFi alone: its cable gone, or never plugged."""
        registry.get_client.return_value = Client(
            mac_id=SAT_MAC, name="Bureau", ip=SAT_IP, online=True, volume_control=True
        )
        with patch("fcntl.ioctl", side_effect=_ioctl_answering({"wlan0": "192.168.1.39"})):
            response = client.post(
                "/api/multiroom/register-client", json={**BODY, "hardware_configured": True}
            )

        assert response.json()["server_addresses"] == ["192.168.1.39"]
