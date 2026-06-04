"""
Pytest fixtures for Milo Client tests.
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
import sys
from pathlib import Path

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_camilla_client():
    """Mock CamillaDSP client."""
    client = MagicMock()

    # General
    client.general.state.return_value = "Running"

    # Config
    client.config.active.return_value = {
        "filters": {
            "eq_band_1": {"parameters": {"type": "Peaking", "freq": 100, "gain": 0, "q": 1.0}},
            "eq_band_2": {"parameters": {"type": "Peaking", "freq": 1000, "gain": 0, "q": 1.0}},
        },
        "processors": {},
        "pipeline": [
            {"type": "Filter", "channels": [0, 1], "names": ["eq_band_1", "eq_band_2"]}
        ]
    }
    client.config.file_path.return_value = "/var/lib/milo-client/camilladsp/config.yml"
    client.config.set_active = Mock()
    client.config.read_and_parse_file = Mock(return_value=client.config.active.return_value)

    # Volume
    client.volume.main_volume.return_value = -20.0
    client.volume.main_mute.return_value = False
    client.volume.set_main_volume = Mock()
    client.volume.set_main_mute = Mock()

    # Levels
    client.levels.capture_peak.return_value = [-30.0, -30.0]
    client.levels.playback_peak.return_value = [-25.0, -25.0]

    return client


@pytest.fixture
def equalizer_service(mock_camilla_client):
    """EqualizerService with mocked CamillaDSP client."""
    with patch("services.equalizer.CAMILLADSP_AVAILABLE", True), \
         patch("services.equalizer.CamillaClient", return_value=mock_camilla_client):
        from services.equalizer import EqualizerService
        service = EqualizerService()
        service._client = mock_camilla_client
        service._connected = True
        return service


@pytest.fixture
def snapclient_service():
    """SnapclientService instance."""
    from services.snapclient import SnapclientService
    return SnapclientService()
