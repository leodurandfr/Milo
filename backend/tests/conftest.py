# backend/tests/conftest.py
"""
Pytest configuration - Shared fixtures for all tests
"""
import asyncio
import copy
import logging

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from backend.config.constants import ERROR_LOG_FILE
from backend.core.models.audio_state import SourceState


@pytest.fixture(scope="session", autouse=True)
def keep_the_suite_out_of_the_operator_log():
    """Detach the production errors.log handler for the whole run.

    `backend/main.py` attaches a RotatingFileHandler on /var/lib/milo/errors.log
    at import time, and three test modules import it. On CI that path does not
    exist and the handler fails open — but this repo is also checked out *on the
    appliance*, where it does exist, so a plain `pytest` writes its own fixtures
    ("Multiroom update failed: network down", "unknown zone: nonexistent") into
    the log an operator reads. Session-scoped rather than module-level: the
    import happens during collection, before any fixture can run.
    """
    root = logging.getLogger()
    for handler in [
        h for h in root.handlers
        if getattr(h, "baseFilename", None) == str(ERROR_LOG_FILE)
    ]:
        root.removeHandler(handler)


@pytest.fixture(scope="session", autouse=True)
def keep_the_suite_out_of_the_live_env_files(tmp_path_factory):
    """Repoint the three env writers at a temp dir for the whole run.

    Same reason as the handler above, one directory further: this checkout is
    also the appliance, so a plain `pytest backend/` rewrote the real
    `/var/lib/milo/*.env`. `_detect_initial_state()` reaches
    `regenerate_env_files()`, and a `Mock()` settings service resolves every
    value to its default — so the run quietly reset the local snapclient's ALSA
    buffer to 80 ms while the satellites kept the stored 120, which is exactly
    the between-rooms divergence the setting exists to prevent. It surfaced only
    because the file was read by hand; nothing failed, and on CI the write fails
    open against a path that does not exist.

    Session-scoped and autouse rather than opt-in: three writers are reached
    through several call paths, and a test that acquires one more must not have
    to know this exists. `test_routing_env.py` still repoints them per-test —
    function-scoped monkeypatch wins over this, which is what that file wants.
    """
    from backend.core.multiroom.routing import MacEnv, RoutingEnv, SnapclientEnv

    tmp = tmp_path_factory.mktemp("env")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(RoutingEnv, "PATH", str(tmp / "routing.env"))
        mp.setattr(MacEnv, "PATH", str(tmp / "mac.env"))
        mp.setattr(SnapclientEnv, "PATH", str(tmp / "snapclient.env"))
        yield


async def drain_background_tasks() -> None:
    """Run to completion every task the unit under test spawned.

    Notification handlers hand their slow work to BackgroundTaskSet rather than
    blocking the snapserver message loop, so the effect a test asserts often
    lands one task later. Draining is deterministic where a sleep is not.
    """
    for _ in range(10):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def attach_registry_broadcaster(registry, state_machine) -> None:
    """Forward ClientRegistryService events to state_machine.broadcast().

    Mirrors what SnapcastWebSocketService.set_registry does in production —
    the registry itself is a pure store, so tests that previously relied on
    `registry.set_state_machine(state_machine)` use this helper instead.
    """
    from backend.core.multiroom.client_registry import REGISTRY_EVENT_CLASSES

    async def _forward(event_type: str, data: dict) -> None:
        await state_machine.broadcast(REGISTRY_EVENT_CLASSES[event_type](**data))

    registry.subscribe(_forward)


def events_of(broadcast_mock, category: str, type_: str) -> list:
    """Typed events of a (category, type) pair captured by a mocked
    `state_machine.broadcast` (AsyncMock)."""
    return [
        c.args[0] for c in broadcast_mock.call_args_list
        if c.args[0].CATEGORY == category and c.args[0].TYPE == type_
    ]



@pytest.fixture
def no_satellite_network(monkeypatch):
    """Keep fire-and-forget pushes to a satellite's API off the real network.

    SnapcastWebSocketService opens its own aiohttp session to reach a client on
    CLIENT_API_PORT. In a test that IP is unroutable, so the push sits on a TCP
    connect until it times out — invisible while nothing awaited the task, and
    seconds of wall clock once ``drain_background_tasks`` does.
    """
    import aiohttp

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return ""

        async def json(self):
            return {}

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def put(self, *args, **kwargs):
            return _Response()

        def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)


@pytest.fixture
def mock_ws_manager():
    """Mock of WebSocketManager"""
    manager = Mock()
    manager.broadcast_dict = AsyncMock()
    return manager


@pytest.fixture
def mock_routing_service():
    """Mock of routing service"""
    service = Mock()
    service.get_state = Mock()
    service.set_multiroom_enabled = AsyncMock(return_value=True)
    service.set_equalizer_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_source():
    """Mock of an audio source"""
    source = Mock()
    source.initialize = AsyncMock(return_value=True)
    source.start = AsyncMock(return_value=True)
    source.stop = AsyncMock(return_value=True)
    # Multiroom reroute hooks (default = stop()/start() on the real base class);
    # mocked as awaitables so _apply_transition can call them on this Mock.
    source.release_for_reroute = AsyncMock(return_value=True)
    source.acquire_after_reroute = AsyncMock(return_value=True)
    # No `status` here on purpose: architecture/test_source_conformance.py
    # forbids one on a real source (status is broadcast over WS, never polled),
    # and no test read it. The shared mock every reader opens first should not
    # teach the opposite of the contract.
    source.is_initialized = True
    source.state = SourceState.READY
    source.metadata = {}
    return source


@pytest.fixture
def mock_settings_service():
    """Mock of SettingsService — stateful for routing-style read-after-write tests.

    Reads via ``get_setting`` / ``get_setting_sync`` and writes via
    ``set_setting`` / ``set_setting_strict`` share an in-memory dict at
    ``service._storage``. Tests that need a starting value seed
    ``_storage`` directly (or override any of the mocks). Other tests that
    only assert call signatures continue to work unchanged — the mocks are
    still AsyncMock/Mock so ``assert_called_with`` etc. remain available.
    """
    service = Mock()
    service._storage: dict = {}

    def _get_sync(key):
        return service._storage.get(key)

    async def _get_async(key):
        return service._storage.get(key)

    async def _set_async(key, value):
        service._storage[key] = value
        return True

    async def _set_strict(key, value):
        service._storage[key] = value

    service.get_setting_sync = Mock(side_effect=_get_sync)
    service.get_setting = AsyncMock(side_effect=_get_async)
    service.set_setting = AsyncMock(side_effect=_set_async)
    service.set_setting_strict = AsyncMock(side_effect=_set_strict)
    service.load_settings = AsyncMock(return_value={})
    service.save_settings = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_async_lock():
    """Mock of asyncio.Lock for tests"""
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    return lock


class CamillaDaemonDouble:
    """The CamillaDSP daemon's config surface, as pyCamillaDSP exposes it.

    Stands in for the daemon itself so that `CamillaDSPService._get_config` and
    `_set_config` run for real. Those two are thin wrappers over
    `client.config.*`, and patching them out replaces the boundary *together
    with* the logic wrapped around it — notably the fallback that reads the
    config file when the daemon answers `active()` with None.

    `active()` hands out a deep copy and `set_active()` stores one, the way a
    WebSocket round-trip does: a caller that mutates the graph it read cannot
    reach back into the daemon's copy, so a write-then-read assertion means
    something.
    """

    FILE_PATH = "/var/lib/milo/camilladsp/config.yml"

    def __init__(self):
        self._active = {"filters": {}, "processors": {}, "pipeline": []}
        self._on_disk = None
        self.pushed_configs: list = []

    # --- test-facing seeding and reading ---

    def load(self, config: dict) -> None:
        """Seed the graph the daemon is currently running."""
        self._active = copy.deepcopy(config)

    def go_inactive(self, on_disk: dict = None) -> None:
        """Stop processing: `active()` answers None and `config.yml` holds `on_disk`.

        This is the state a CamillaDSP daemon sits in between streams, and the
        only one in which `_get_config` reaches for the file.
        """
        self._active = None
        self._on_disk = copy.deepcopy(on_disk)

    @property
    def active_config(self) -> dict:
        """The graph the daemon holds right now."""
        return copy.deepcopy(self._active)

    @property
    def last_pushed(self) -> dict:
        """The most recent graph written to the daemon."""
        assert self.pushed_configs, "no config was pushed to CamillaDSP"
        return self.pushed_configs[-1]

    # --- what pyCamillaDSP's client.config serves ---

    def active(self):
        return copy.deepcopy(self._active)

    def set_active(self, config):
        self._active = copy.deepcopy(config)
        self.pushed_configs.append(copy.deepcopy(config))

    def file_path(self):
        return self.FILE_PATH

    def read_and_parse_file(self, path):
        return copy.deepcopy(self._on_disk)


@pytest.fixture
def camilla_daemon():
    """The daemon `mock_camilla_client` talks to. See CamillaDaemonDouble."""
    return CamillaDaemonDouble()


@pytest.fixture
def mock_camilla_client(camilla_daemon):
    """Mock of pyCamillaDSP's CamillaClient — the outside world for CamillaDSPService.

    Injected as `service._client`, which is the whole point: the service's own
    config helpers then run for real instead of being patched away. Modelled on
    milo-client/app/tests/conftest.py, with the config graph made stateful
    because the server's tests assert on what was *written*, where the
    satellite's only read it back.
    """
    client = MagicMock()

    client.general.state.return_value = "Running"

    client.config.active.side_effect = camilla_daemon.active
    client.config.set_active.side_effect = camilla_daemon.set_active
    client.config.file_path.side_effect = camilla_daemon.file_path
    client.config.read_and_parse_file.side_effect = camilla_daemon.read_and_parse_file

    client.volume.main_volume.return_value = -20.0
    client.volume.main_mute.return_value = False

    client.levels.capture_peak.return_value = [-30.0, -30.0]
    client.levels.playback_peak.return_value = [-25.0, -25.0]

    return client
