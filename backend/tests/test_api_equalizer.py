# backend/tests/test_api_equalizer.py
"""
Unit tests for /api/equalizer/ per-client routes (Phase 3 — Option A).

After unification the REMOTE per-client EQ writes flow through the single access
layer (MultiroomEqualizerService) instead of the old `equalizer_router +
_persist_remote` duplicate path. The LOCAL client keeps its dedicated non-scoped
routes (it has no registry MAC when multiroom is off), so those are unchanged.

These tests lock that contract:
- remote `/client/{mac}/{filter,compressor,loudness,mono}` → multiroom_equalizer_service.update_*
- remote `/client/{mac}/enabled` → multiroom_equalizer_service.set_client_equalizer_effects_enabled
- the equalizer_router is NOT used for those writes (single write path)
- local non-scoped routes still drive CamillaDSP directly
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.equalizer import create_equalizer_router


REMOTE_MAC = "dc:a6:32:7e:d3:43"


@pytest.fixture
def mock_camilladsp():
    cam = Mock()
    cam.get_status = AsyncMock(return_value={"available": True, "state": "running"})
    cam.get_filters = AsyncMock(return_value=[])
    cam.get_compressor = AsyncMock(return_value={"enabled": False})
    cam.get_loudness = AsyncMock(return_value={"enabled": False})
    cam.get_mono = AsyncMock(return_value=False)
    cam.load_preset = AsyncMock(return_value=True)
    cam.save_custom_gains = AsyncMock(return_value=None)
    cam.set_active_preset = AsyncMock(return_value=None)
    cam.set_mute = AsyncMock(return_value=True)
    return cam


@pytest.fixture
def mock_state_machine():
    sm = Mock()
    sm.broadcast_event = AsyncMock()
    sm.system_state = Mock(active_source=None)
    return sm


@pytest.fixture
def mock_mre():
    """Mock MultiroomEqualizerService — the unified access layer."""
    mre = Mock()
    mre.update_filter = AsyncMock(return_value=True)
    mre.update_compressor = AsyncMock(return_value=True)
    mre.update_loudness = AsyncMock(return_value=True)
    mre.update_mono = AsyncMock(return_value=True)
    mre.set_client_equalizer_effects_enabled = AsyncMock(return_value=True)
    mre.get_client_equalizer = AsyncMock(return_value=None)
    return mre


@pytest.fixture
def mock_equalizer_router():
    """The equalizer_router must NOT be used for per-client writes after unification."""
    router = Mock()
    router.is_local_client = Mock(return_value=False)
    router.update_filter = AsyncMock(return_value={"status": "success"})
    router.set_compressor = AsyncMock(return_value={"status": "success"})
    router.set_loudness = AsyncMock(return_value={"status": "success"})
    router.set_mono = AsyncMock(return_value={"status": "success"})
    router.set_equalizer_enabled = AsyncMock(return_value={"status": "success"})
    return router


@pytest.fixture
def mock_registry():
    reg = Mock()
    reg.get_client = Mock(return_value=None)
    reg.get_client_equalizer = Mock(return_value=None)
    reg.set_client_equalizer = AsyncMock()
    return reg


@pytest.fixture
def client(mock_camilladsp, mock_state_machine, mock_mre, mock_equalizer_router, mock_registry):
    app = FastAPI()
    router = create_equalizer_router(
        camilladsp_service=mock_camilladsp,
        state_machine=mock_state_machine,
        settings_service=Mock(),
        routing_service=Mock(),
        crossover_service=Mock(),
        proxy_service=Mock(),
        client_registry_service=mock_registry,
        equalizer_router_service=mock_equalizer_router,
        multiroom_equalizer_service=mock_mre,
        volume_service=None,
    )
    app.include_router(router)
    return TestClient(app)


# =============================================================================
# Remote per-client writes route through the unified access layer
# =============================================================================

class TestRemoteClientFilterRoute:
    def test_filter_routes_through_access_layer(self, client, mock_mre):
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/filter/eq_band_00",
            json={"freq": 120, "gain": 4.0, "q": 1.0, "filter_type": "Peaking", "enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_mre.update_filter.assert_awaited_once()
        kwargs = mock_mre.update_filter.call_args.kwargs
        assert kwargs["target_type"] == "client"
        assert kwargs["target_id"] == REMOTE_MAC
        assert kwargs["filter_id"] == "eq_band_00"
        assert kwargs["frequency"] == 120
        assert kwargs["gain"] == 4.0
        assert kwargs["q"] == 1.0
        assert kwargs["filter_type"] == "Peaking"
        assert kwargs["enabled"] is True

    def test_filter_does_not_use_equalizer_router(self, client, mock_equalizer_router):
        client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/filter/eq_band_00",
            json={"gain": 2.0},
        )
        mock_equalizer_router.update_filter.assert_not_called()

    def test_filter_not_found_returns_404(self, client, mock_mre):
        mock_mre.update_filter.side_effect = ValueError("Filter not found: nope")
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/filter/nope",
            json={"gain": 1.0},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestRemoteClientCompressorRoute:
    def test_compressor_routes_through_access_layer(self, client, mock_mre):
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/compressor",
            json={"enabled": True, "threshold": -25.0, "ratio": 3.0},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_mre.update_compressor.assert_awaited_once()
        kwargs = mock_mre.update_compressor.call_args.kwargs
        assert kwargs["target_type"] == "client"
        assert kwargs["target_id"] == REMOTE_MAC
        assert kwargs["enabled"] is True
        assert kwargs["threshold"] == -25.0
        assert kwargs["ratio"] == 3.0

    def test_compressor_does_not_use_equalizer_router(self, client, mock_equalizer_router):
        client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/compressor",
            json={"enabled": False},
        )
        mock_equalizer_router.set_compressor.assert_not_called()


class TestRemoteClientLoudnessRoute:
    def test_loudness_routes_through_access_layer(self, client, mock_mre):
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/loudness",
            json={"enabled": True, "high_boost": 4.0, "low_boost": 6.0},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_mre.update_loudness.assert_awaited_once()
        kwargs = mock_mre.update_loudness.call_args.kwargs
        assert kwargs["target_type"] == "client"
        assert kwargs["target_id"] == REMOTE_MAC
        assert kwargs["enabled"] is True
        assert kwargs["high_boost"] == 4.0
        assert kwargs["low_boost"] == 6.0

    def test_loudness_does_not_use_equalizer_router(self, client, mock_equalizer_router):
        client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/loudness",
            json={"enabled": False},
        )
        mock_equalizer_router.set_loudness.assert_not_called()


class TestRemoteClientMonoRoute:
    def test_mono_routes_through_access_layer(self, client, mock_mre):
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/mono",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_mre.update_mono.assert_awaited_once()
        kwargs = mock_mre.update_mono.call_args.kwargs
        assert kwargs["target_type"] == "client"
        assert kwargs["target_id"] == REMOTE_MAC
        assert kwargs["enabled"] is True

    def test_mono_missing_enabled_returns_400(self, client, mock_mre):
        resp = client.put(f"/api/equalizer/client/{REMOTE_MAC}/mono", json={})
        assert resp.status_code == 400
        mock_mre.update_mono.assert_not_called()

    def test_mono_does_not_use_equalizer_router(self, client, mock_equalizer_router):
        client.put(f"/api/equalizer/client/{REMOTE_MAC}/mono", json={"enabled": True})
        mock_equalizer_router.set_mono.assert_not_called()


class TestRemoteClientEnabledRoute:
    def test_enabled_routes_through_access_layer(self, client, mock_mre):
        resp = client.put(
            f"/api/equalizer/client/{REMOTE_MAC}/enabled",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_mre.set_client_equalizer_effects_enabled.assert_awaited_once()
        args = mock_mre.set_client_equalizer_effects_enabled.call_args.args
        assert args[0] == REMOTE_MAC
        assert args[1] is False

    def test_enabled_does_not_use_equalizer_router(self, client, mock_equalizer_router):
        client.put(f"/api/equalizer/client/{REMOTE_MAC}/enabled", json={"enabled": True})
        mock_equalizer_router.set_equalizer_enabled.assert_not_called()


# =============================================================================
# Local non-scoped routes are unchanged (drive CamillaDSP directly)
# =============================================================================

class TestLocalRoutesUnchanged:
    def test_local_preset_drives_camilladsp(self, client, mock_camilladsp):
        resp = client.put("/api/equalizer/preset/rock")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        mock_camilladsp.load_preset.assert_awaited_once_with("rock")

    def test_local_save_custom_drives_camilladsp(self, client, mock_camilladsp):
        resp = client.post("/api/equalizer/save-custom")
        assert resp.status_code == 200
        mock_camilladsp.save_custom_gains.assert_awaited_once()
        mock_camilladsp.set_active_preset.assert_awaited_once_with("custom")
