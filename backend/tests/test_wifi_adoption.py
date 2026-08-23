"""WifiAdoptionService — the three guards that stand in front of nmcli.

What breaks when these fail: `POST /api/discovery/adopt-speaker` carries a
caller-chosen `ssid` (`AdoptSpeakerRequest.ssid` is a free-form `str`), and the
first thing `_connect_to_hotspot` does with it is `nmcli connection delete
<ssid>`. The `ssid != HOTSPOT_NAME` check is the only thing between that body
and the deletion of any NetworkManager profile on this server, its own home
wifi included. The hotspot check is what stops a fresh server from deleting the
setup AP the operator is connected through, then trying to adopt itself.
Consumer: `api/discovery.py::adopt_speaker`, which maps `AdoptionError.code`
onto an HTTP status.

Why this file exists: measured 2026-08-23, the module ran at 23 % of its lines
under the whole suite — the `def` lines and `__init__`, nothing else. No test
had ever built an AdoptionError.
"""
import pytest
from unittest.mock import MagicMock

from backend.core.multiroom import wifi_adoption
from backend.core.multiroom.wifi_adoption import AdoptionError, WifiAdoptionService
from backend.core.network.service import HOTSPOT_NAME


AUDIO = {
    "audio_id": "hifiberry-dacplus",
    "speaker_name": "Bureau",
    "speaker_type": "bookshelf",
}


@pytest.fixture
def service(monkeypatch):
    """The service with every subprocess spawn wired to explode.

    A spy asserted afterwards would be too late to be honest here: this checkout
    runs on the appliance, and a guard that lets go spawns `nmcli connection
    delete` against this very host's NetworkManager. The spawn has to fail, not
    be recorded.
    """
    def _never(*args, **kwargs):
        raise AssertionError(f"a subprocess was spawned past the guards: {args}")

    monkeypatch.setattr(wifi_adoption.asyncio, "create_subprocess_exec", _never)
    network = MagicMock()
    network.hotspot_active = False
    return WifiAdoptionService(network_service=network)


async def test_a_foreign_ssid_is_refused_before_nmcli_runs(service):
    """The one guard between a request body and `nmcli connection delete <name>`."""
    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid="preconfigured", wifi_ssid="Maison", wifi_password="secret", **AUDIO
        )

    assert exc.value.code == "invalid_ssid"
    # The refused name is echoed back: proof this is the SSID branch and not
    # some other AdoptionError raised further down the flow.
    assert "preconfigured" in exc.value.detail


async def test_adoption_is_refused_while_the_server_broadcasts_the_setup_hotspot(service):
    """Both profiles are named `Milō` — adopting from hotspot mode deletes ours."""
    service.network_service.hotspot_active = True

    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid=HOTSPOT_NAME, wifi_ssid="Maison", wifi_password="secret", **AUDIO
        )

    assert exc.value.code == "server_in_hotspot_mode"


async def test_an_empty_target_wifi_is_refused_before_nmcli_runs(service):
    """Without it the server drops its LAN for ~30 s to push a config that
    cannot work, and the speaker comes back on no network at all."""
    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid=HOTSPOT_NAME, wifi_ssid="", wifi_password="", **AUDIO
        )

    assert exc.value.code == "invalid_target_wifi"
