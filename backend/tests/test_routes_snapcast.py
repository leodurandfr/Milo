# backend/tests/test_routes_snapcast.py
"""
Unit tests for the snapcast half of the /api/routing router.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.api.routing import create_routing_router
from backend.core.multiroom.routing import DEFAULT_SNAPCLIENT_CONFIG, SNAPCLIENT_LIMITS
from backend.core.settings import SettingsWriteError


class TestSnapcastRoutes:
    """Tests for Snapcast routes"""

    @pytest.fixture
    def mock_routing_service(self):
        """Routing service mock"""
        service = Mock()
        service.get_state = Mock(return_value={'multiroom_enabled': True})
        return service

    @pytest.fixture
    def mock_snapcast_service(self):
        """Snapcast service mock"""
        service = Mock()
        service.is_available = AsyncMock(return_value=True)
        service.get_clients = AsyncMock(return_value=[
            {"id": "client1", "name": "Client 1", "volume": 50, "muted": False, "host": "milo", "ip": "127.0.0.1", "camilladsp_id": "local"},
            {"id": "client2", "name": "Client 2", "volume": 75, "muted": True, "host": "remote", "ip": "192.168.1.100", "camilladsp_id": "192.168.1.100"}
        ])
        service.get_detailed_clients = AsyncMock(return_value=[
            {"id": "client1", "name": "Client 1", "volume": 50, "muted": False, "host": "milo"},
        ])
        service.get_server_config = AsyncMock(return_value={"version": "0.27.0"})
        service.set_volume = AsyncMock(return_value=True)
        service.set_mute = AsyncMock(return_value=True)
        service.set_client_name = AsyncMock(return_value=True)
        service.update_server_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.broadcast = AsyncMock()
        # Mock volume_service with async broadcast_volume_state for mute tests
        sm.volume_service = Mock()
        sm.volume_service.broadcast_volume_state = AsyncMock()
        return sm

    @pytest.fixture
    def client(self, mock_routing_service, mock_snapcast_service, mock_state_machine):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_routing_router(
            mock_routing_service,
            mock_state_machine,
            mock_snapcast_service,
        )
        app.include_router(router)
        client = TestClient(app)
        client._mock_routing = mock_routing_service
        client._mock_snapcast = mock_snapcast_service
        client._mock_state_machine = mock_state_machine
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
        assert {p["id"] for p in body["capabilities"]["presets"]} == {"responsive", "balanced", "robust"}

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


class TestSnapclientBufferSetting:
    """`snapclient_buffer_time` / `snapclient_fragments` travel a path of their own.

    The route pops both out of `config` before SnapcastService sees it — they
    belong to snapclient.env, not snapserver.conf — so the validators that used
    to sit in `SnapcastService._validate_config` were never reached: an
    out-of-range value was persisted, written to the local env and pushed to
    every satellite, each end clamping it its own way. This route is the only
    gate the pair passes through.
    """

    @pytest.fixture
    def settings_service(self):
        store = {}
        svc = Mock()
        svc.get_setting = AsyncMock(side_effect=lambda key: store.get(key))
        svc.set_settings_strict = AsyncMock(side_effect=lambda updates: store.update(updates))
        svc.store = store
        return svc

    @pytest.fixture
    def client(self, settings_service):
        routing_service = Mock()
        routing_service.service_manager = Mock()
        routing_service.service_manager.restart = AsyncMock(return_value=True)

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
        the validators that used to sit in `_validate_config` for them covered
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
            return TestStoredFragmentsReachBothSidesClamped._Response()

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return ""

    @pytest.fixture
    def settings_service(self):
        store = {'multiroom.snapclient_fragments': TestStoredFragmentsReachBothSidesClamped.OUT_OF_RANGE}
        svc = Mock()
        svc.get_setting = AsyncMock(side_effect=lambda key: store.get(key))
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
        routing_service = Mock()
        routing_service.service_manager = Mock()
        routing_service.service_manager.restart = AsyncMock(return_value=True)

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
        satellite = self._RecordingSatellite()
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
