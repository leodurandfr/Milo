# backend/tests/test_api_discovery.py
"""
/api/discovery — the three routes of the wifi adoption wizard.

Why this file exists: measured 2026-08-25, `backend/api/discovery.py` ran at
55.3 % of its lines and none of its three routes had ever been entered.

The piece that carries the most is `_ADOPTION_CLIENT_ERROR_CODES`, a table
translating `AdoptionError.code` into an HTTP status. The codes are raised in
`core/multiroom/wifi_adoption.py`; the table lives here; nothing confronted the
two. A code added there and not here becomes a 500, and the wizard shows
"internal error" for a speaker that is simply already configured, or for a home
wifi the user mistyped. `test_wifi_adoption.py` covers the raising side and
cannot see this.

The two reads matter for a smaller reason each: the scan must return nothing
while this device is itself broadcasting the setup hotspot (a fresh server
adopting itself deletes the AP the operator is connected through), and the
credentials read must degrade to `available: false` rather than fail, or an
ethernet-only server cannot open the wizard at all.
"""
import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api.discovery import _ADOPTION_CLIENT_ERROR_CODES, create_discovery_router
from backend.core.multiroom.wifi_adoption import AdoptionError
from backend.core.network.service import HOTSPOT_NAME


WIFI_ADOPTION = Path(__file__).resolve().parents[1] / "core/multiroom/wifi_adoption.py"

ADOPT = {
    "ssid": HOTSPOT_NAME,
    "audio_id": "hifiberry_amp2",
    "speaker_name": "Bureau",
    "speaker_type": "bookshelf",
    "wifi_ssid": "Maison",
    "wifi_password": "secret",
}


@pytest.fixture
def network():
    service = Mock()
    service.hotspot_active = False
    service.scan_networks = AsyncMock(return_value=[])
    service.get_active_wifi_credentials = AsyncMock(return_value=None)
    return service


@pytest.fixture
def adoption():
    service = Mock()
    service.adopt_speaker = AsyncMock(return_value={"ip": "192.168.1.60"})
    return service


@pytest.fixture
def client(network, adoption):
    app = FastAPI()
    app.include_router(create_discovery_router(network, adoption))
    return TestClient(app)


def _codes_raised_in_the_service() -> set:
    """Every literal `AdoptionError` code the service can raise, from its AST."""
    tree = ast.parse(WIFI_ADOPTION.read_text())
    codes = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", None) == "AdoptionError"
                and node.exc.args and isinstance(node.exc.args[0], ast.Constant)):
            codes.add(node.exc.args[0].value)
    return codes


class TestErrorCodeTable:

    def test_every_code_the_service_raises_has_a_status(self):
        """Read out of the service's own source, so this cannot pass by agreeing
        with a list typed here. An unmapped code falls through to 500, and the
        wizard reports an internal failure for a state it could have named.
        """
        raised = _codes_raised_in_the_service()
        assert len(raised) >= 8, f"only {len(raised)} codes found — has the AST shape moved?"

        assert raised <= set(_ADOPTION_CLIENT_ERROR_CODES), (
            f"unmapped, so served as 500: {sorted(raised - set(_ADOPTION_CLIENT_ERROR_CODES))}"
        )

    def test_no_status_is_mapped_for_a_code_nothing_raises(self):
        """The other direction: an entry left behind after a rename is a status
        that can never be served, and the code that replaced it answers 500.
        """
        orphans = set(_ADOPTION_CLIENT_ERROR_CODES) - _codes_raised_in_the_service()
        assert not orphans, f"mapped but never raised: {sorted(orphans)}"

    @pytest.mark.parametrize("code,status", sorted(_ADOPTION_CLIENT_ERROR_CODES.items()))
    def test_each_mapped_code_reaches_the_wizard_as_its_own_status(
        self, client, adoption, code, status
    ):
        """The wizard branches on the status before it reads the body: 4xx is
        shown to the user as something to fix, 502 as the speaker's fault.
        """
        adoption.adopt_speaker.side_effect = AdoptionError(code, "detail")

        response = client.post("/api/discovery/adopt-speaker", json=ADOPT)

        assert response.status_code == status
        assert response.json()["detail"]["code"] == code

    def test_an_unexpected_failure_is_a_500_that_still_names_itself(
        self, client, adoption
    ):
        """The body shape has to hold across both arms — the wizard reads
        `detail.code` whatever went wrong.
        """
        adoption.adopt_speaker.side_effect = RuntimeError("nmcli segfaulted")

        response = client.post("/api/discovery/adopt-speaker", json=ADOPT)

        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "internal_error"

    def test_a_successful_adoption_passes_the_wizard_its_payload(self, client, adoption):
        response = client.post("/api/discovery/adopt-speaker", json=ADOPT)

        assert response.status_code == 200
        assert response.json() == {"status": "success", "data": {"ip": "192.168.1.60"}}
        adoption.adopt_speaker.assert_awaited_once_with(
            ssid=HOTSPOT_NAME, audio_id="hifiberry_amp2", speaker_name="Bureau",
            speaker_type="bookshelf", wifi_ssid="Maison", wifi_password="secret",
        )


class TestScan:

    def test_only_the_setup_hotspot_is_offered_for_adoption(self, client, network):
        """Every other SSID in range is somebody's home network; offering one
        sends the server off its own LAN to push a config at a stranger's router.
        """
        network.scan_networks.return_value = [
            Mock(ssid="Maison", signal=70),
            Mock(ssid=HOTSPOT_NAME, signal=44),
            Mock(ssid="Livebox-A1B2", signal=30),
        ]

        data = client.get("/api/discovery/wifi-speakers").json()["data"]

        assert data["hotspots"] == [{"ssid": HOTSPOT_NAME, "signal": 44}]

    def test_a_server_in_hotspot_mode_scans_for_nothing(self, client, network):
        """Both profiles are named `Milō`. A fresh server that adopts what it
        sees adopts itself, deleting the AP the operator is connected through.
        """
        network.hotspot_active = True
        network.scan_networks.side_effect = AssertionError(
            "a scan was started while this device is the hotspot"
        )

        data = client.get("/api/discovery/wifi-speakers").json()["data"]

        assert data["hotspots"] == []


class TestServerCredentials:

    def test_an_ethernet_only_server_reports_no_credentials_rather_than_failing(
        self, client
    ):
        """The wizard pre-fills the speaker's target wifi from the server's own.
        A failure here would close the wizard on every wired server instead of
        falling back to typing the password.
        """
        data = client.get("/api/discovery/server-wifi-creds").json()["data"]

        assert data == {"available": False}

    def test_the_active_wifi_is_handed_over_for_the_prefill(self, client, network):
        network.get_active_wifi_credentials.return_value = {
            "ssid": "Maison", "password": "secret",
        }

        data = client.get("/api/discovery/server-wifi-creds").json()["data"]

        assert data == {"available": True, "ssid": "Maison", "password": "secret"}
