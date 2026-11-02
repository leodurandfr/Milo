# backend/tests/test_routes_snapcast.py
"""
Unit tests for the snapcast half of the /api/routing router.
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, Mock, AsyncMock, patch
from backend.api.routing import create_routing_router
from backend.core.multiroom.routing import (
    DEFAULT_SNAPCLIENT_CONFIG,
    SNAPCLIENT_LIMITS,
    AudioRoutingService,
)
from backend.core.multiroom.snapcast import (
    BUFFER_MS_RANGE,
    NETWORK_PRESETS,
    SUPPORTED_CODECS,
    SnapcastService,
)
from backend.core.models.audio_state import AudioSource
from backend.core.settings import SettingsWriteError
from backend.core.state import AudioStateMachine, SystemAudioState
from backend.tests.test_snapcast_server_config import SpawnedPastTheGuards


def _routing_service(settings_service):
    """The real routing service over a systemd double.

    Real because the server-config route takes its lock and reads the mode
    under it from `routing.multiroom_enabled`: a Mock has no lock to hold, so a
    write racing a toggle would pass here and fail on the unit.
    """
    systemd = Mock()
    systemd.restart = AsyncMock(return_value=True)
    return AudioRoutingService(settings_service=settings_service, systemd_manager=systemd)


class _RecordingSatellite:
    """An aiohttp session stand-in recording what the push sent."""

    def __init__(self):
        self.puts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def put(self, url, json=None, **kwargs):
        self.puts.append((url, json))
        return _Response()


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return ""


class TestSnapcastRoutes:
    """Tests for Snapcast routes"""

    @pytest.fixture
    def routing_service(self, mock_settings_service):
        mock_settings_service._storage["routing.multiroom_enabled"] = True
        return _routing_service(mock_settings_service)

    @pytest.fixture
    def mock_snapcast_service(self):
        """Snapcast service mock"""
        service = Mock()
        service.is_available = AsyncMock(return_value=True)
        service.get_server_config = AsyncMock(return_value={"version": "0.27.0"})
        service.update_server_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.volume_service = Mock()
        return sm

    @pytest.fixture
    def client(self, routing_service, mock_snapcast_service, mock_state_machine):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_routing_router(
            routing_service,
            mock_state_machine,
            mock_snapcast_service,
        )
        app.include_router(router)
        client = TestClient(app)
        client._mock_snapcast = mock_snapcast_service
        return client

    # ===================
    # SERVER CONFIG TESTS
    # ===================

    def test_get_server_config(self, client):
        """Test GET /api/routing/snapcast/server-config"""
        response = client.get("/api/routing/snapcast/server-config")
        assert response.status_code == 200
        body = response.json()
        assert "config" in body
        # Capabilities are the single source for the UI codec/preset options
        assert body["capabilities"]["codecs"]
        # Derived, not restated: the payload's job is to carry whatever the
        # backend declares, and a hardcoded id list here only ever fails when
        # someone changes that set on purpose.
        assert ({p["id"] for p in body["capabilities"]["presets"]}
                == {p["id"] for p in NETWORK_PRESETS})

    def test_get_server_config_unavailable(self, client):
        """Test GET /api/routing/snapcast/server-config when unavailable"""
        client._mock_snapcast.is_available = AsyncMock(return_value=False)
        response = client.get("/api/routing/snapcast/server-config")
        assert response.status_code == 200
        assert response.json()["config"] is None
        assert "error" in response.json()
        # Static capabilities are served even when snapserver is down
        assert response.json()["capabilities"]["codecs"]

    def test_update_server_config(self, client):
        """Test PUT /api/routing/snapcast/server-config"""
        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"buffer": 1000}}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_update_server_config_failure_is_not_a_200(self, client):
        """A rejected config must surface as an HTTP error, not a 200 body flag.

        The route is not /status-style: hiding a failed snapserver write behind
        200 would leave the UI showing the new config as applied.
        """
        client._mock_snapcast.update_server_config = AsyncMock(return_value=False)
        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"buffer": 1000}}
        )
        assert response.status_code == 502

    def test_a_write_is_refused_while_snapserver_is_down(self, client, mock_settings_service):
        """Direct mode stops both snapcast units, and every step of this route
        ends in a `systemctl restart` — a verb that STARTS a stopped unit.

        Applying a config there would raise snapserver and snapclient behind
        AudioRoutingService's back, and snapclient would take hw:Loopback,0,0
        while routing.env still points milo_roc at it: the next direct-mode
        source opens a busy device and plays silence until a reboot. Nothing
        may run before the probe — a persisted buffer_time pushed to every
        satellite for a server that is not there is the same lie one layer up.

        The gate is the mode, not a liveness probe: snapserver enabled but down
        is exactly when rewriting its conf is the repair, and a probe would 409
        that away. `test_a_write_survives_a_dead_snapserver_while_multiroom_is_on`
        holds that half.

        MultiroomSettings.vue hides the button in direct mode, which is why this
        never fired; a route may not rely on a v-if.
        """
        mock_settings_service._storage["routing.multiroom_enabled"] = False

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"buffer": 1000, "snapclient_buffer_time": 80}}
        )

        assert response.status_code == 409
        client._mock_snapcast.update_server_config.assert_not_awaited()

    def test_a_write_survives_a_dead_snapserver_while_multiroom_is_on(self, client):
        """The other half of the gate above: an enabled-but-down snapserver must
        still be configurable.

        update_server_config writes snapserver.conf BEFORE restarting, so a conf
        the daemon refused is repaired by rewriting it. Gating on liveness would
        answer 409 to every retry and strand a failed unit with no route back
        from the settings page.
        """
        client._mock_snapcast.is_available = AsyncMock(return_value=False)

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"buffer_ms": 700}}
        )

        assert response.status_code == 200
        client._mock_snapcast.update_server_config.assert_awaited_once()


class TestSnapclientBufferSetting:
    """`snapclient_buffer_time` / `snapclient_fragments` travel a path of their own.

    The route pops both out of `config` before SnapcastService sees it — they
    belong to snapclient.env, not snapserver.conf — so the validators that used
    to sit in `SnapcastService.validate_config` were never reached: an
    out-of-range value was persisted, written to the local env and pushed to
    every satellite, each end clamping it its own way. This route is the only
    gate the pair passes through.
    """

    @pytest.fixture
    def settings_service(self):
        store = {"routing.multiroom_enabled": True}
        svc = Mock()
        svc.get_setting = AsyncMock(side_effect=lambda key: store.get(key))
        svc.get_setting_sync = Mock(side_effect=lambda key: store.get(key))
        svc.set_settings_strict = AsyncMock(side_effect=lambda updates: store.update(updates))
        svc.store = store
        return svc

    @pytest.fixture
    def client(self, settings_service):
        routing_service = _routing_service(settings_service)

        snapcast_service = Mock()
        snapcast_service.is_available = AsyncMock(return_value=True)
        snapcast_service.get_server_config = AsyncMock(return_value={"buffer_ms": 1000})
        snapcast_service.update_server_config = AsyncMock(return_value=True)

        state_machine = Mock()
        state_machine.broadcast = AsyncMock()

        app = FastAPI()
        app.include_router(create_routing_router(
            routing_service, state_machine, snapcast_service,
            settings_service=settings_service,
        ))
        c = TestClient(app)
        c.routing_service = routing_service
        c.snapcast_service = snapcast_service
        return c

    def test_get_reports_the_declared_default_when_nothing_is_stored(self, client):
        """The route used to answer with its own `80`, a second declaration of
        a default DEFAULT_SNAPCLIENT_CONFIG already owns."""
        body = client.get("/api/routing/snapcast/server-config").json()
        assert body["config"]["snapclient_buffer_time"] == DEFAULT_SNAPCLIENT_CONFIG["buffer_time"]

    def test_get_reports_the_stored_value(self, client, settings_service):
        settings_service.store['multiroom.snapclient_buffer_time'] = 150
        body = client.get("/api/routing/snapcast/server-config").json()
        assert body["config"]["snapclient_buffer_time"] == 150

    @pytest.mark.parametrize("field,value", [
        ("snapclient_buffer_time", SNAPCLIENT_LIMITS["buffer_time"][1] + 1),
        ("snapclient_buffer_time", SNAPCLIENT_LIMITS["buffer_time"][0] - 1),
        ("snapclient_buffer_time", "120"),
        ("snapclient_fragments", SNAPCLIENT_LIMITS["fragments"][1] + 1),
        ("snapclient_fragments", SNAPCLIENT_LIMITS["fragments"][0] - 1),
    ])
    def test_out_of_range_is_rejected_and_nothing_is_written(
        self, client, settings_service, field, value, monkeypatch
    ):
        """Rejected at the door — before settings.json, before snapclient.env,
        before the push to the satellites and before the snapserver restart."""
        regenerate = AsyncMock()
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", regenerate)

        payload = {"snapclient_buffer_time": 120, "snapclient_fragments": 4}
        payload[field] = value
        response = client.put("/api/routing/snapcast/server-config", json={"config": payload})

        assert response.status_code == 400
        settings_service.set_settings_strict.assert_not_called()
        regenerate.assert_not_called()
        client.snapcast_service.update_server_config.assert_not_called()
        client.routing_service.service_manager.restart.assert_not_called()

    def test_a_refused_settings_write_is_not_a_200(
        self, client, settings_service, monkeypatch
    ):
        """The route pushes the pair to every satellite and restarts snapserver.

        A swallowed settings.json write leaves the fleet on values the file
        never took — and losing them on the next boot. `set_settings` returned
        False here and the route answered 200; the strict variant raises and
        `api_error_handler` turns it into a 500.
        """
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", AsyncMock())
        settings_service.set_settings_strict = AsyncMock(
            side_effect=SettingsWriteError("disk full")
        )

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"snapclient_buffer_time": 120, "snapclient_fragments": 4}},
        )

        assert response.status_code == 500
        client.snapcast_service.update_server_config.assert_not_called()
        client.routing_service.service_manager.restart.assert_not_called()

    def test_the_pair_never_reaches_the_snapserver_config_writer(self, client, monkeypatch):
        """Both keys belong to snapclient.env, not snapserver.conf.

        They leave `config` before SnapcastService sees the body — which is why
        the validators that used to sit in `validate_config` for them covered
        nothing at all, and why this route has to gate them itself.
        """
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", AsyncMock())

        client.put("/api/routing/snapcast/server-config", json={"config": {
            "buffer_ms": 1000, "snapclient_buffer_time": 120, "snapclient_fragments": 4,
        }})

        (written,), _ = client.snapcast_service.update_server_config.call_args
        assert written == {"buffer_ms": 1000}

    def test_an_accepted_value_reaches_the_env_and_the_local_snapclient(
        self, client, settings_service, monkeypatch
    ):
        regenerate = AsyncMock()
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", regenerate)

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"snapclient_buffer_time": SNAPCLIENT_LIMITS["buffer_time"][1]}}
        )

        assert response.status_code == 200
        high = SNAPCLIENT_LIMITS["buffer_time"][1]
        assert settings_service.store['multiroom.snapclient_buffer_time'] == high
        # Fragments were not part of the request: the declared default carries.
        regenerate.assert_called_once_with(high, DEFAULT_SNAPCLIENT_CONFIG["fragments"])
        client.routing_service.service_manager.restart.assert_awaited_once()


class TestStoredFragmentsReachBothSidesClamped:
    """A stored `fragments` must reach the local env and the satellites as one value.

    The route validates an *explicit* fragments against SNAPCLIENT_LIMITS, but a
    request that carries only `buffer_time` re-reads fragments from settings.json,
    and that read used to skip the clamp. `SnapclientEnv.regenerate` clamps its
    own input, so the local speaker was bounded at 8 while the satellites got the
    stored value raw — and answered 422, leaving one house on two ALSA buffer
    settings with nothing but a warning in the log.
    """

    OUT_OF_RANGE = SNAPCLIENT_LIMITS["fragments"][1] + 4
    CLAMPED = SNAPCLIENT_LIMITS["fragments"][1]

    @pytest.fixture
    def settings_service(self):
        store = {
            "routing.multiroom_enabled": True,
            'multiroom.snapclient_fragments': TestStoredFragmentsReachBothSidesClamped.OUT_OF_RANGE,
        }
        svc = Mock()
        svc.get_setting = AsyncMock(side_effect=lambda key: store.get(key))
        svc.get_setting_sync = Mock(side_effect=lambda key: store.get(key))
        svc.set_settings_strict = AsyncMock(side_effect=lambda updates: store.update(updates))
        svc.store = store
        return svc

    @pytest.fixture
    def registry(self):
        svc = Mock()
        svc.get_online_clients = Mock(return_value=[
            Mock(ip="192.168.1.153", name="Canape"),
        ])
        return svc

    @pytest.fixture
    def client(self, settings_service, registry):
        routing_service = _routing_service(settings_service)

        snapcast_service = Mock()
        snapcast_service.is_available = AsyncMock(return_value=True)
        snapcast_service.update_server_config = AsyncMock(return_value=True)

        state_machine = Mock()
        state_machine.broadcast = AsyncMock()

        app = FastAPI()
        app.include_router(create_routing_router(
            routing_service, state_machine, snapcast_service,
            settings_service=settings_service,
            client_registry_service=registry,
        ))
        return TestClient(app)

    def test_both_consumers_receive_the_same_clamped_value(self, client, monkeypatch):
        regenerate = AsyncMock()
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", regenerate)
        satellite = _RecordingSatellite()
        monkeypatch.setattr(
            "backend.api.routing.aiohttp.ClientSession", lambda **kw: satellite
        )

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"snapclient_buffer_time": 120}},
        )

        assert response.status_code == 200
        # The local env writer clamps whatever it is handed — assert on its input,
        # which is where the two values used to diverge.
        (_, env_fragments), _ = regenerate.call_args
        assert env_fragments == self.CLAMPED
        assert len(satellite.puts) == 1
        _, pushed = satellite.puts[0]
        assert pushed["fragments"] == self.CLAMPED


class TestAnInvalidServerBodyChangesNothing:
    """A snapserver half the service refuses must stop the write before its
    first effect.

    Steps 1-3 persist the snapclient pair, rewrite snapclient.env and push it to
    every satellite, each of which restarts its snapclient; the snapserver half
    used to be validated only at step 4. A body with a bad codec, an
    out-of-range buffer or an unknown key then answered 502 with settings.json
    and the whole fleet already on the new buffer_time and the local speaker on
    the old one. The verdict here is the real SnapcastService's, so the test
    follows the validator rather than restating it.
    """

    UNSUPPORTED_CODEC = "mp3"

    @pytest.fixture
    def settings_service(self):
        store = {"routing.multiroom_enabled": True}
        svc = Mock()
        svc.get_setting = AsyncMock(side_effect=lambda key: store.get(key))
        svc.get_setting_sync = Mock(side_effect=lambda key: store.get(key))
        svc.set_settings_strict = AsyncMock(side_effect=lambda updates: store.update(updates))
        return svc

    @pytest.fixture
    def snapserver_systemd(self):
        manager = MagicMock()
        manager.restart = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def snapcast_service(self, snapserver_systemd, tmp_path, monkeypatch):
        """The real service, its conf in the tmpdir and every spawn wired to
        explode: this checkout is the appliance and its helper writes /etc."""

        def _never(*args, **kwargs):
            raise SpawnedPastTheGuards(f"a subprocess was spawned past the doubles: {args}")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _never)
        svc = SnapcastService(systemd_manager=snapserver_systemd)
        svc.snapserver_conf = tmp_path / "snapserver.conf"
        return svc

    @pytest.fixture
    def routing_service(self, settings_service):
        return _routing_service(settings_service)

    @pytest.fixture
    def client(self, settings_service, routing_service, snapcast_service):
        registry = Mock()
        registry.get_online_clients = Mock(return_value=[
            SimpleNamespace(ip="192.168.1.153", name="Canape"),
        ])
        state_machine = Mock()
        state_machine.broadcast = AsyncMock()

        app = FastAPI()
        app.include_router(create_routing_router(
            routing_service, state_machine, snapcast_service,
            settings_service=settings_service,
            client_registry_service=registry,
        ))
        return TestClient(app)

    @pytest.mark.parametrize("server_half", [
        {"codec": UNSUPPORTED_CODEC},
        {"buffer_ms": BUFFER_MS_RANGE[0] - 1},
        {"bogus": 1},
    ], ids=["unsupported-codec", "buffer-below-range", "unknown-key"])
    def test_an_invalid_server_key_writes_and_pushes_nothing(
        self, client, settings_service, routing_service, snapserver_systemd,
        server_half, monkeypatch,
    ):
        assert self.UNSUPPORTED_CODEC not in SUPPORTED_CODECS
        regenerate = AsyncMock()
        monkeypatch.setattr("backend.api.routing.SnapclientEnv.regenerate", regenerate)
        satellite = _RecordingSatellite()
        monkeypatch.setattr(
            "backend.api.routing.aiohttp.ClientSession", lambda **kw: satellite
        )

        response = client.put(
            "/api/routing/snapcast/server-config",
            json={"config": {"snapclient_buffer_time": 120, **server_half}},
        )

        assert response.status_code == 400
        settings_service.set_settings_strict.assert_not_called()
        regenerate.assert_not_called()
        assert satellite.puts == []
        snapserver_systemd.restart.assert_not_called()
        routing_service.service_manager.restart.assert_not_called()


class TestMultiroomToggle:
    """PUT /api/routing/multiroom — the route Milo-Mac's manifest pins.

    Measured 2026-08-25: the body ran at 0 %. It is the one switch between
    direct and multiroom mode, which is a full ALSA re-route (the source
    restarts, snapserver and snapclient start or stop, routing.env is
    rewritten), and it has an out-of-checkout consumer that reads the response
    it returns.
    """

    @pytest.fixture
    def services(self):
        routing = Mock()
        routing.set_multiroom_enabled = AsyncMock(return_value=True)
        state_machine = Mock()
        state_machine.system_state = SystemAudioState(active_source=AudioSource.RADIO)
        return routing, state_machine

    @pytest.fixture
    def client(self, services):
        routing, state_machine = services
        app = FastAPI()
        app.include_router(create_routing_router(routing, state_machine, Mock()))
        return TestClient(app)

    def test_an_idle_appliance_switches_with_no_source_to_carry(self, client, services):
        routing, state_machine = services
        state_machine.system_state.active_source = AudioSource.NONE

        response = client.put("/api/routing/multiroom", json={"enabled": False})

        assert response.status_code == 200
        assert response.json()["source"] == "none"
        routing.set_multiroom_enabled.assert_awaited_once_with(False)

    def test_the_answer_reports_the_mode_that_was_asked_for(self, client):
        """Milo-Mac reads `multiroom_enabled` off this body to settle its own
        toggle; an answer that echoes the old mode leaves its switch flipping
        back under the user's finger.
        """
        body = client.put("/api/routing/multiroom", json={"enabled": True}).json()

        assert body == {"status": "success", "multiroom_enabled": True, "source": "radio"}

    def test_a_refused_transition_is_not_a_200(self, client, services):
        """The ALSA re-route can fail half-way — snapserver refusing the
        loopback is the usual one. Answering success there is a UI showing
        multiroom on with the audio still in direct mode.
        """
        routing, _ = services
        routing.set_multiroom_enabled = AsyncMock(return_value=False)

        response = client.put("/api/routing/multiroom", json={"enabled": True})

        assert response.status_code == 500


class TestAToggleRacingASourceSwitch:
    """The route against a real state machine and a real routing service, with
    only the outside world mocked: systemd, the WebSocket clients, and the two
    sources' own lifecycle.

    What breaks when this fails: a source picked while the multiroom toggle is
    in flight is followed by a reroute of the source it replaced — the stopped
    one is started again next to the new one, two daemons write to the same
    ALSA device, and the room plays the wrong thing or nothing. The toggle
    announces itself to the UI and waits 100 ms before it reaches the lock, so
    the window is a tap wide.
    """

    @pytest.fixture
    def appliance(self, mock_settings_service):
        import asyncio
        from unittest.mock import patch
        from backend.core.multiroom.routing import AudioRoutingService
        from backend.core.models.audio_state import NetworkRequirement
        from backend.core.models.audio_wire import SourceView
        from backend.core.state import AudioStateMachine
        from backend.tests.conftest import free_mailbox

        systemd = Mock()
        systemd.start = AsyncMock(return_value=True)
        systemd.stop = AsyncMock(return_value=True)
        systemd.is_active = AsyncMock(return_value=True)
        routing = AudioRoutingService(
            settings_service=mock_settings_service, systemd_manager=systemd
        )
        routing._initial_detection_done = True
        mock_settings_service._storage["routing.multiroom_enabled"] = True

        state_machine = AudioStateMachine()
        state_machine.routing_service = routing
        routing.set_state_machine(state_machine)
        state_machine.ALSA_RELEASE_SETTLE_S = 0

        sources = {}
        for name in (AudioSource.RADIO, AudioSource.PODCAST):
            source = Mock()
            source.start = AsyncMock(return_value=True)
            source.stop = AsyncMock(return_value=True)
            source.release_for_reroute = AsyncMock(return_value=True)
            source.acquire_after_reroute = AsyncMock(return_value=True)
            source.hold_mailbox = free_mailbox()
            source.view = SourceView()
            source.availability = Mock(return_value=None)
            source.NETWORK_REQUIREMENT = NetworkRequirement.NONE
            state_machine.register_source(name, source)
            sources[name] = source
        state_machine.system_state.active_source = AudioSource.RADIO

        # The toggle's first outward act is the "multiroom_disabling" broadcast.
        # Holding it there is holding the request between its start and the lock.
        announced = asyncio.Event()
        release = asyncio.Event()

        async def _deliver(envelope):
            if envelope.get("type") == "multiroom_disabling":
                announced.set()
                await release.wait()

        state_machine.ws_manager = Mock()
        state_machine.ws_manager.broadcast_dict = AsyncMock(side_effect=_deliver)

        app = FastAPI()
        app.include_router(create_routing_router(routing, state_machine, Mock()))
        with patch("backend.core.multiroom.routing.RoutingEnv.regenerate"):
            yield app, state_machine, sources, announced, release

    async def test_the_reroute_carries_the_source_picked_during_the_toggle(self, appliance):
        import asyncio
        import httpx

        app, state_machine, sources, announced, release = appliance
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://milo") as client:
            toggle = asyncio.create_task(
                client.put("/api/routing/multiroom", json={"enabled": False})
            )
            await announced.wait()

            assert await state_machine.transition_to_source(AudioSource.PODCAST)
            release.set()
            response = await toggle

        assert response.status_code == 200
        radio, podcast = sources[AudioSource.RADIO], sources[AudioSource.PODCAST]
        radio.acquire_after_reroute.assert_not_called()
        radio.release_for_reroute.assert_not_called()
        podcast.release_for_reroute.assert_awaited_once()
        podcast.acquire_after_reroute.assert_awaited_once()
        assert response.json()["source"] == "podcast"


class _JournaledSystemd:
    """systemd as both the routing service and the route drive it: every verb is
    journaled in call order, and the first `stop` can be held on an Event."""

    def __init__(self, journal):
        self.journal = journal
        self.hold_stop = None
        self.stopping = asyncio.Event()

    async def start(self, unit):
        self.journal.append(("start", unit))
        return True

    async def stop(self, unit):
        self.journal.append(("stop", unit))
        if not self.stopping.is_set():
            self.stopping.set()
            if self.hold_stop is not None:
                await self.hold_stop.wait()
        return True

    async def restart(self, unit):
        self.journal.append(("restart", unit))
        return True

    async def is_active(self, unit):
        return True


class TestAServerConfigWriteRacingTheToggle:
    """PUT /snapcast/server-config against a real routing service and a real
    state machine, with only the outside world mocked: systemd, snapserver's
    conf writer, the env files and the WebSocket clients.

    What breaks when this fails: every step of an Apply ends in a `systemctl
    restart`, a verb that STARTS a stopped unit. A multiroom disable landing
    while an Apply is in flight stops both snapcast units and writes routing.env
    in direct mode, then the Apply restarts them: snapclient holds
    hw:Loopback,0,0 while routing.env hands it to the direct-mode source, which
    plays silence until a reboot. Two Applies at once interleave their
    settings, env and conf writes, leaving the house on a buffer neither asked
    for. The Apply pushes to every satellite first (up to 10 s), so the window
    is wide.
    """

    # Long enough for an unserialized request to run to its end here (it
    # awaits nothing real), never a measure of anything: a serialized one waits
    # it out and the verdict is the same whatever the value.
    LET_IT_RUN_S = 0.5

    @pytest.fixture
    def appliance(self, mock_settings_service):
        journal = []
        systemd = _JournaledSystemd(journal)
        routing = AudioRoutingService(
            settings_service=mock_settings_service, systemd_manager=systemd
        )
        routing._initial_detection_done = True
        mock_settings_service._storage["routing.multiroom_enabled"] = True

        async def _persist(updates):
            journal.append(("settings", updates["multiroom.snapclient_buffer_time"]))
            mock_settings_service._storage.update(updates)

        mock_settings_service.set_settings_strict = AsyncMock(side_effect=_persist)

        async def _env(buffer_time, fragments):
            journal.append(("env", buffer_time))

        # No source is active, so the reroute runs the output switch directly.
        state_machine = AudioStateMachine()
        state_machine.routing_service = routing
        routing.set_state_machine(state_machine)
        state_machine.ws_manager = Mock()
        state_machine.ws_manager.broadcast_dict = AsyncMock()

        # snapserver's conf writer, which restarts snapserver on success. Set
        # `writing` and `held` to hold the first call until `held` is set.
        snapcast = Mock()
        snapcast.writing = snapcast.held = None

        async def _write(config):
            journal.append(("server-config", config["buffer_ms"]))
            if snapcast.writing is not None and not snapcast.writing.is_set():
                snapcast.writing.set()
                await snapcast.held.wait()
            return True

        snapcast.update_server_config = AsyncMock(side_effect=_write)

        app = FastAPI()
        app.include_router(create_routing_router(
            routing, state_machine, snapcast, settings_service=mock_settings_service,
        ))
        with patch("backend.core.multiroom.routing.RoutingEnv.regenerate"), \
                patch("backend.core.multiroom.routing.SnapclientEnv.regenerate",
                      AsyncMock(side_effect=_env)):
            yield SimpleNamespace(
                app=app, routing=routing, systemd=systemd, snapcast=snapcast,
                journal=journal, settings=mock_settings_service._storage,
            )

    @staticmethod
    def _apply(client, buffer_ms, buffer_time):
        return client.put("/api/routing/snapcast/server-config", json={"config": {
            "buffer_ms": buffer_ms,
            "snapclient_buffer_time": buffer_time,
            "snapclient_fragments": 4,
        }})

    @staticmethod
    def _client(appliance):
        transport = httpx.ASGITransport(app=appliance.app)
        return httpx.AsyncClient(transport=transport, base_url="http://milo")

    async def test_a_server_config_write_racing_a_multiroom_disable_never_restarts_snapcast(
        self, appliance
    ):
        """The disable is held between its first stop and its second: the mode
        is still persisted on, and the write must not read it there."""
        appliance.systemd.hold_stop = asyncio.Event()
        async with self._client(appliance) as client:
            disable = asyncio.create_task(
                client.put("/api/routing/multiroom", json={"enabled": False})
            )
            await appliance.systemd.stopping.wait()

            write = asyncio.create_task(self._apply(client, 700, 120))
            await asyncio.wait({write}, timeout=self.LET_IT_RUN_S)
            appliance.systemd.hold_stop.set()
            disabled, written = await disable, await write

        assert disabled.status_code == 200
        assert written.status_code == 409
        appliance.snapcast.update_server_config.assert_not_awaited()
        assert [unit for verb, unit in appliance.journal if verb in ("start", "restart")] == []
        assert appliance.settings["routing.multiroom_enabled"] is False

    async def test_a_multiroom_disable_waits_for_a_server_config_write_in_flight(
        self, appliance
    ):
        """The write is held in snapserver's conf writer, past its mode check:
        the disable runs after its last restart, so the units end stopped."""
        snapcast, routing = appliance.snapcast, appliance.routing
        snapcast.writing, snapcast.held = asyncio.Event(), asyncio.Event()
        async with self._client(appliance) as client:
            write = asyncio.create_task(self._apply(client, 700, 120))
            await snapcast.writing.wait()

            disable = asyncio.create_task(
                client.put("/api/routing/multiroom", json={"enabled": False})
            )
            await asyncio.wait({disable}, timeout=self.LET_IT_RUN_S)
            snapcast.held.set()
            written, disabled = await write, await disable

        assert written.status_code == 200
        assert disabled.status_code == 200
        assert [
            (verb, unit) for verb, unit in appliance.journal if verb in ("start", "stop", "restart")
        ] == [
            ("restart", routing.snapclient_service),
            ("stop", routing.snapclient_service),
            ("stop", routing.snapserver_service),
        ]
        assert appliance.settings["routing.multiroom_enabled"] is False

    async def test_two_concurrent_server_config_puts_are_serialized(self, appliance):
        """Two Applies a second apart (the kiosk and a phone): each one's
        settings, env, conf and restart land together, the second's last."""
        snapcast, client_unit = appliance.snapcast, appliance.routing.snapclient_service
        snapcast.writing, snapcast.held = asyncio.Event(), asyncio.Event()
        async with self._client(appliance) as client:
            first = asyncio.create_task(self._apply(client, 700, 120))
            await snapcast.writing.wait()

            second = asyncio.create_task(self._apply(client, 900, 200))
            await asyncio.wait({second}, timeout=self.LET_IT_RUN_S)
            snapcast.held.set()
            responses = [await first, await second]

        assert [r.status_code for r in responses] == [200, 200]
        assert appliance.journal == [
            ("settings", 120), ("env", 120), ("server-config", 700), ("restart", client_unit),
            ("settings", 200), ("env", 200), ("server-config", 900), ("restart", client_unit),
        ]
        assert appliance.settings["multiroom.snapclient_buffer_time"] == 200
