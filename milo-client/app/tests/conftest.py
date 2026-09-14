"""
Pytest fixtures for Milo Client tests.
"""
import pytest
from unittest.mock import AsyncMock, patch
import sys
from pathlib import Path

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_camilla_client():
    """Mock of CamillaDspClient — the daemon, as the client hands it over.

    Every method is a coroutine now that the client is async, so this is an
    AsyncMock: a MagicMock would hand each call back as an un-awaited object
    that every `if config is None` in the service reads as a live config.
    """
    client = AsyncMock()

    client.get_state.return_value = "Running"

    client.get_config.return_value = {
        "filters": {
            "eq_band_1": {"parameters": {"type": "Peaking", "freq": 100, "gain": 0, "q": 1.0}},
            "eq_band_2": {"parameters": {"type": "Peaking", "freq": 1000, "gain": 0, "q": 1.0}},
        },
        "processors": {},
        "pipeline": [
            {"type": "Filter", "channels": [0, 1], "names": ["eq_band_1", "eq_band_2"]}
        ]
    }
    client.get_config_file_path.return_value = "/var/lib/milo-client/camilladsp/config.yml"
    client.read_config_file.return_value = client.get_config.return_value

    client.get_volume.return_value = -20.0
    client.get_mute.return_value = False

    client.get_capture_peak.return_value = [-30.0, -30.0]
    client.get_playback_peak.return_value = [-25.0, -25.0]

    return client


@pytest.fixture
def equalizer_service(mock_camilla_client, tmp_path):
    """EqualizerService with mocked CamillaDSP client, persisting under tmp_path.

    config_file is redirected because a setter now fails when the persist leg
    fails: pointed at the real /var/lib path these tests would assert success on
    a write that never happened, which is the bug they are meant to cover.
    """
    with patch("services.equalizer.CamillaDspClient", return_value=mock_camilla_client):
        from services.equalizer import EqualizerService
        service = EqualizerService(config_file=str(tmp_path / "config.yml"))
        service._client = mock_camilla_client
        service._connected = True
        return service


@pytest.fixture
def snapclient_service():
    """SnapclientService instance."""
    from services.snapclient import SnapclientService
    return SnapclientService()
