"""
Pytest fixtures for Milo Client tests.
"""
import asyncio

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
    """A satellite that came up on v0.1.0 / OLDPAYLOAD, with its state directory
    (identity, trial) under tmp_path, writable the way a deploy writes it.

    `routes.health`, which reads the identity for both routes that report it, is
    re-imported under the patched paths, which is what a process start does; it
    is re-imported again on the way out so the next test sees the real paths and
    a fresh process start.
    """
    import importlib

    import routes.health
    import services.app_update as app_update_module

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    version = state / app_update_module.VERSION_NAME
    payload = state / app_update_module.PAYLOAD_NAME
    version.write_text("v0.1.0")
    payload.write_text("OLDPAYLOAD")
    try:
        with patch.object(app_update_module, "STATE_DIR", state):
            importlib.reload(routes.health)
            yield version, payload
    finally:
        importlib.reload(routes.health)


# The real guard, shipped beside the app in this tree.
UPDATE_GUARD = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-update-guard"


@pytest.fixture
def update_world(tmp_path):
    """Where an update keeps its trial, redirected under tmp_path: the state
    directory, a venv reached through a symlink as on a unit, and the real
    `milo-client-update-guard`, which the updater hands these same paths.

    REPO_DIR is left to the test, which lays out the live app/ it needs.
    """
    import services.app_update as app_update_module

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    real_venv = tmp_path / "real" / "venv"
    (real_venv / "bin").mkdir(parents=True)
    (real_venv / "marker").write_text("OLD")
    venv = tmp_path / "venv"
    venv.symlink_to(real_venv)
    with patch.object(app_update_module, "STATE_DIR", state), \
            patch.object(app_update_module, "VENV_DIR", venv), \
            patch.object(app_update_module, "GUARD", str(UPDATE_GUARD)):
        yield state, real_venv


# The secret a satellite's server hands over: a SHA-256 HMAC in hex.
SERVER_SECRET = "5e" * 32
ANOTHER_SERVER_SECRET = "a7" * 32


@pytest.fixture(autouse=True)
def secret_file(tmp_path, monkeypatch):
    """Where this satellite pins its server's secret, under tmp_path for every
    test: none ever reads or writes the unit's own pin, and none starts with one."""
    import server_secret

    path = tmp_path / "server-secret"
    monkeypatch.setattr(server_secret, "SECRET_FILE", path)
    return path


@pytest.fixture(autouse=True)
def server_addresses_file(tmp_path, monkeypatch):
    """Where this satellite keeps its server's addresses, under tmp_path for
    every test, which starts with none read and none kept."""
    from services import registration

    path = tmp_path / "server-addresses"
    monkeypatch.setattr(registration, "SERVER_ADDRESSES_FILE", path)
    monkeypatch.setattr(registration, "_server_addresses", None)
    monkeypatch.setattr(registration, "_stored", None)
    monkeypatch.setattr(registration, "_store_failing", False)
    return path


@pytest.fixture(autouse=True)
def boot_trace(tmp_path, monkeypatch):
    """Where this satellite reads how its last stop went, under tmp_path for
    every test, on a boot whose id is `boot_id`; no test starts in the window."""
    import server_secret

    trace = tmp_path / "boot"
    trace.mkdir()
    boot_id = trace / "boot_id"
    boot_id.write_text("3f1c9b2e-boot-one\n")
    monkeypatch.setattr(server_secret, "CLEAN_STOP_FILE", trace / "clean-stop")
    monkeypatch.setattr(server_secret, "BOOT_FILE", trace / "started-boot")
    monkeypatch.setattr(server_secret, "BOOT_ID_FILE", boot_id)
    monkeypatch.setattr(server_secret, "_window_ends", None)
    # Bound to the loop of the first test that waits on it otherwise.
    monkeypatch.setattr(server_secret, "_follow_lock", asyncio.Lock())
    return trace


@pytest.fixture
def server(secret_file):
    """A satellite that pinned SERVER_SECRET; the headers its server sends."""
    import server_secret

    secret_file.write_text(f"{SERVER_SECRET}\n")
    return {server_secret.SECRET_HEADER: SERVER_SECRET}


@pytest.fixture
def own_password(tmp_path, monkeypatch):
    """A unit whose `milo` password was set: the marker milo-set-password leaves."""
    from routes import system

    marker = tmp_path / "password-changed"
    marker.touch()
    monkeypatch.setattr(system, "PASSWORD_CHANGED_MARKER", str(marker))
    return marker


@pytest.fixture
def account(monkeypatch):
    """Whether the unit has the `milo` account — the passwd database is the outside world here."""
    from routes import system

    def present(exists):
        def getpwnam(name):
            if exists and name == system.LOGIN_USER:
                return object()
            raise KeyError(name)
        monkeypatch.setattr(system.pwd, "getpwnam", getpwnam)
    return present
