"""
Pytest fixtures for Milo Client tests.
"""
import pytest
import yaml
from unittest.mock import AsyncMock, patch
import sys
import warnings
from pathlib import Path

# Add app directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# The suite raises no warning of its own; this one is third party: FastAPI's
# TestClient still runs on httpx, and the replacement is upstream's to ship. It
# is raised once, at the first import, so that import is made here with the
# warning silenced — not from a milo-client/pytest.ini, which would ship to
# every satellite in each update tarball for a test setting.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    import fastapi.testclient  # noqa: F401 -- imported for the side effect above


# The CamillaDSP config this tree ships, read and never rewritten by a test.
SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "camilladsp" / "config.yml"


@pytest.fixture
def mock_camilla_client():
    """Mock of CamillaDspClient — the daemon, as the client hands it over.

    Every method is a coroutine now that the client is async, so this is an
    AsyncMock: a MagicMock would hand each call back as an un-awaited object
    that every `if config is None` in the service reads as a live config.
    """
    client = AsyncMock()

    client.get_state.return_value = "Running"

    shipped = yaml.safe_load(SHIPPED_CONFIG.read_text())
    client.get_config.return_value = {
        # A unit already on the shipped device sections: the connect leaves them.
        "devices": shipped["devices"],
        "mixers": shipped["mixers"],
        "filters": {
            "eq_band_1": {"parameters": {"type": "Peaking", "freq": 100, "gain": 0, "q": 1.0}},
            "eq_band_2": {"parameters": {"type": "Peaking", "freq": 1000, "gain": 0, "q": 1.0}},
        },
        "processors": None,  # the shipped config has none; CamillaDSP returns null
        "pipeline": [
            {"type": "Filter", "channels": [0, 1], "names": ["eq_band_1", "eq_band_2"]}
        ]
    }
    client.get_config_file_path.return_value = "/var/lib/milo-client/camilladsp/config.yml"
    client.read_config_file.return_value = client.get_config.return_value

    client.get_volume.return_value = -20.0
    client.get_mute.return_value = False

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


@pytest.fixture
def identity_at_startup(tmp_path):
    """A satellite that came up on v0.1.0 / OLDPAYLOAD, with the identity files
    left writable the way a deploy writes them.

    The two routes that report the identity are re-imported under the patched
    paths, which is what a process start does; they are re-imported again on the
    way out so the next test sees the real paths and a fresh process start.
    """
    import importlib

    import routes.diagnostic
    import routes.health
    import services.app_update as app_update_module

    version = tmp_path / "app-version"
    payload = tmp_path / "app-payload"
    version.write_text("v0.1.0")
    payload.write_text("OLDPAYLOAD")
    try:
        with patch.object(app_update_module, "VERSION_FILE", version), \
                patch.object(app_update_module, "PAYLOAD_FILE", payload):
            importlib.reload(routes.health)
            importlib.reload(routes.diagnostic)
            yield version, payload
    finally:
        importlib.reload(routes.health)
        importlib.reload(routes.diagnostic)
