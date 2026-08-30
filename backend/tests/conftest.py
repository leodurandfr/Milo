# backend/tests/conftest.py
"""
Pytest configuration - Shared fixtures for all tests
"""
import asyncio
import builtins
import copy
import errno
import logging
import os
import socket
from pathlib import Path

import aiofiles.threadpool
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from backend.config.constants import ERROR_LOG_FILE, MILO_DATA_DIR
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


@pytest.fixture(scope="session", autouse=True)
def keep_the_suite_out_of_the_live_store_folders(tmp_path_factory):
    """Repoint the two stores that create their own folder, for the whole run.

    Same reason as the env writers above, one directory further again:
    `VolumeStateStore` and `ImageManager` each `mkdir` their folder under
    /var/lib/milo from `__init__`, so every fixture that builds a volume store or
    anything holding a radio source reached the live path -- 235 tests, none of
    which repoints anything. Reading is the visible half: nothing repoints
    `STORAGE_PATH` in `integration/test_multiroom_zones.py`, so `initialize()`
    there loaded the appliance's real last_volume.json -- two live satellite MACs
    at whatever level the operator last left the knob -- and loaded nothing on
    CI. Same test, two starting states, decided by the host.

    Session-scoped and autouse for the same reason as the env fixture: both
    constructors are reached through many paths, and a test that acquires one
    more must not have to know this exists. The files that already repoint these
    two per-test keep winning -- function-scoped monkeypatch undoes first.
    """
    from backend.core.volume.state import VolumeStateStore
    from backend.sources.radio.data import ImageManager

    tmp = tmp_path_factory.mktemp("stores")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(VolumeStateStore, "STORAGE_PATH", tmp / "last_volume.json")
        mp.setattr(ImageManager, "IMAGES_DIR", tmp / "radio_images")
        yield


_OFF_HOST_CONNECTS: list = []


@pytest.fixture(scope="session", autouse=True)
def keep_the_suite_off_the_network():
    """Refuse every streaming connect off this host, and record who tried.

    Third of the same family as the two above, one layer further out. Those keep
    the run out of the appliance's operator log and its env files; this one keeps
    it off the appliance's *network*, where the same accident is louder. A
    reconnection sync pushes the snapclient buffer config to whatever IP the
    registry holds, and more than one fixture holds the address of a live
    satellite — so a plain `pytest backend/` rewrote that unit's ALSA buffer to
    the resolved default and restarted its snapclient, twice per run, cutting the
    sound in an occupied room. Nothing failed here: the push is fire-and-forget.

    Datagram and Unix sockets (mpv, the Tidal daemon, D-Bus) are left alone —
    only a streaming connect off loopback can carry a command away.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _refuse(sock, address) -> bool:
        if sock.type != socket.SOCK_STREAM or not isinstance(address, tuple):
            return False
        host = address[0]
        if host in ("localhost", "::1", "") or host.startswith("127."):
            return False
        _OFF_HOST_CONNECTS.append((
            os.environ.get("PYTEST_CURRENT_TEST", "<unknown test>").removesuffix(" (call)"),
            f"{host}:{address[1]}",
        ))
        return True

    def connect(self, address):
        if _refuse(self, address):
            raise ConnectionRefusedError(errno.ECONNREFUSED, "refused by the test suite")
        return real_connect(self, address)

    def connect_ex(self, address):
        return errno.ECONNREFUSED if _refuse(self, address) else real_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


@pytest.fixture(autouse=True)
def fail_when_a_test_reaches_off_this_host():
    """Fail the test that tried, naming it and the address it aimed at.

    Refusing the connect is not enough on its own: every caller of the satellite
    API logs a warning and returns, so a blocked push leaves the run green and
    the leak in place — which is how three tests kept a real speaker restarting
    for a month without a red line anywhere.
    """
    already = len(_OFF_HOST_CONNECTS)
    yield
    escaped = _OFF_HOST_CONNECTS[already:]
    if escaped:
        tried = ", ".join(f"{where} -> {target}" for where, target in escaped)
        pytest.fail(
            "reached off this host; stand in for the transport instead "
            f"(`no_satellite_network` covers the satellite API): {tried}",
            pytrace=False,
        )


_APPLIANCE_WRITES: list = []


@pytest.fixture(scope="session", autouse=True)
def keep_the_suite_out_of_the_appliance_data():
    """Refuse every write under /var/lib/milo, and record who tried.

    Fourth of the same family: the three above keep the run out of the operator
    log, the env files and the network. This one covers what was left — the
    persisted data itself. Measured on 2026-08-25: an eviscration of
    `load_versioned_json` made a caller save an empty record over the real
    `radio_data.json`, and 22 favourites plus 22 metadata overrides went to 122
    bytes. Nothing failed; the file is written through the same atomic
    tmp-then-`os.replace` as every other store, and the suite is checked out ON
    the appliance, so the live path is the default path.

    The mutation is what exposed it, but it is not the defect: on an untouched
    tree the same save rewrites the file with identical content, which is
    invisible and permanent. Only `settings.json` and the env files had a guard.

    `aiofiles` is patched separately and by name: it binds `sync_open = open` at
    import time, so it holds the *original* builtins.open and a patch installed
    here is invisible to it. Every store that writes a temp file and renames is
    still caught at the `os.replace`, but a direct `aiofiles.open(..., 'wb')` is
    not -- which is exactly how the station images are written. `os.makedirs` is
    the same shape of hole and is patched for the same reason: it never reaches
    `Path.mkdir`.

    Creating a directory is refused outright, with no "it is already there"
    exemption. That exemption asked the filesystem a question about the *host*
    rather than about the test: on the appliance every store's own folder exists,
    so it never fired and the run was green; on CI none of them exist, so the
    same commit turned 262 tests green here into setup errors there. The false
    positives it was narrowed against are gone at their source instead -- the two
    stores that make their own folder are repointed at a temp dir by
    `keep_the_suite_out_of_the_live_store_folders`.
    """
    real_open = builtins.open
    real_sync_open = aiofiles.threadpool.sync_open
    real_replace, real_rename, real_remove = os.replace, os.rename, os.remove
    real_write_text, real_write_bytes = Path.write_text, Path.write_bytes
    real_unlink, real_mkdir = Path.unlink, Path.mkdir
    real_makedirs = os.makedirs
    root = str(MILO_DATA_DIR)

    def _refuse(target) -> bool:
        try:
            path = os.fspath(target)
        except TypeError:
            return False
        if not str(path).startswith(root):
            return False
        _APPLIANCE_WRITES.append((
            os.environ.get("PYTEST_CURRENT_TEST", "<unknown test>").removesuffix(" (call)"),
            str(path),
        ))
        return True

    def _deny(path):
        raise PermissionError(errno.EACCES, "refused by the test suite", str(path))

    def open_(file, mode="r", *a, **kw):
        if any(c in str(mode) for c in "wxa+") and _refuse(file):
            _deny(file)
        return real_open(file, mode, *a, **kw)

    def replace_(src, dst, *a, **kw):
        if _refuse(dst):
            _deny(dst)
        return real_replace(src, dst, *a, **kw)

    def rename_(src, dst, *a, **kw):
        if _refuse(dst):
            _deny(dst)
        return real_rename(src, dst, *a, **kw)

    def remove_(path, *a, **kw):
        if _refuse(path):
            _deny(path)
        return real_remove(path, *a, **kw)

    def write_text_(self, *a, **kw):
        if _refuse(self):
            _deny(self)
        return real_write_text(self, *a, **kw)

    def write_bytes_(self, *a, **kw):
        if _refuse(self):
            _deny(self)
        return real_write_bytes(self, *a, **kw)

    def unlink_(self, *a, **kw):
        if _refuse(self):
            _deny(self)
        return real_unlink(self, *a, **kw)

    def mkdir_(self, *a, **kw):
        if _refuse(self):
            _deny(self)
        return real_mkdir(self, *a, **kw)

    def makedirs_(name, *a, **kw):
        if _refuse(name):
            _deny(name)
        return real_makedirs(name, *a, **kw)

    def sync_open_(file, mode="r", *a, **kw):
        if any(c in str(mode) for c in "wxa+") and _refuse(file):
            _deny(file)
        return real_sync_open(file, mode, *a, **kw)

    builtins.open = open_
    aiofiles.threadpool.sync_open = sync_open_
    os.replace, os.rename, os.remove = replace_, rename_, remove_
    Path.write_text, Path.write_bytes = write_text_, write_bytes_
    Path.unlink, Path.mkdir = unlink_, mkdir_
    os.makedirs = makedirs_
    try:
        yield
    finally:
        builtins.open = real_open
        aiofiles.threadpool.sync_open = real_sync_open
        os.replace, os.rename, os.remove = real_replace, real_rename, real_remove
        Path.write_text, Path.write_bytes = real_write_text, real_write_bytes
        Path.unlink, Path.mkdir = real_unlink, real_mkdir
        os.makedirs = real_makedirs


@pytest.fixture(autouse=True)
def fail_when_a_test_writes_appliance_state():
    """Fail the test that tried, naming it and the file it aimed at.

    Same reason as its network twin: every persistence path here is wrapped in a
    logged best-effort, so a refused write leaves the run green and the leak in
    place. The refusal names the test; without this the operator finds the
    damage days later, by hand.
    """
    already = len(_APPLIANCE_WRITES)
    yield
    escaped = _APPLIANCE_WRITES[already:]
    if escaped:
        tried = ", ".join(f"{where} -> {target}" for where, target in escaped)
        pytest.fail(
            "wrote the appliance's own data; point the store at `tmp_path` "
            f"instead: {tried}",
            pytrace=False,
        )


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
    """Stand in for the satellite's HTTP surface, and record what was sent to it.

    SnapcastWebSocketService opens its own aiohttp session to reach a client on
    CLIENT_API_PORT, fire-and-forget. Where that IP is unroutable the push sits
    on a TCP connect until it times out — seconds of wall clock once
    ``drain_background_tasks`` awaits it. Where it is not, it arrives: this
    checkout is also the appliance, so the fixtures naming a live satellite were
    restarting that unit's snapclient on every run.

    Yields the `(method, url, json)` the unit tried to send, so a test asserting
    on a push reads this stand-in instead of declaring a second one.
    """
    import aiohttp

    sent = []

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

    def _verb(method):
        def call(self, url, **kwargs):
            sent.append((method, url, kwargs.get("json")))
            return _Response()

        return call

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        get = _verb("get")
        put = _verb("put")
        post = _verb("post")

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return sent


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
