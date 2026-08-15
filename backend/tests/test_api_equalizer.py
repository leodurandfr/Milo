# backend/tests/test_api_equalizer.py
"""
Unit tests for the uniform per-target equalizer API:
``GET/PUT/POST /api/equalizer/target/{target}`` with target ∈ "local" · "<mac>" ·
"zone:<id>".

After Phase 4 the legacy split routes are gone — the bare /status·/filters·/enabled
·/filter·/compressor·/loudness·/preset·/save-custom routes, the /client/{mac}/*
family and the /zone/{id}/* family. Every read/write now flows through one grammar
that the route resolves to (target_type, target_id) and dispatches to the access
layer (MultiroomEqualizerService).
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.equalizer import create_equalizer_router
from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    CompressorSettings,
    LoudnessSettings,
    FilterType,
)


REMOTE_MAC = "dc:a6:32:7e:d3:43"


def _sample_record():
    """A fully-populated EQ record to exercise the wire serialization."""
    return EqualizerSettings(
        enabled=True,
        filters=[
            EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.41, filter_type=FilterType.PEAKING, enabled=True),
        ],
        compressor=CompressorSettings(enabled=True, threshold=-25.0),
        loudness=LoudnessSettings(enabled=True, high_boost=4.0, low_boost=6.0),
        active_preset="rock",
        mono=True,
        custom_gains=[1.0] * 10,
    )


@pytest.fixture
def mock_camilladsp():
    cam = Mock()
    cam.get_status = AsyncMock(return_value={"available": True, "state": "running"})
    cam.get_filters = AsyncMock(return_value=[])
    cam.set_mute = AsyncMock(return_value=True)
    return cam


@pytest.fixture
def mock_mre():
    """Mock MultiroomEqualizerService — the unified access layer."""
    mre = Mock()
    mre.update_filter = AsyncMock(return_value=True)
    mre.update_compressor = AsyncMock(return_value=True)
    mre.update_loudness = AsyncMock(return_value=True)
    mre.update_mono = AsyncMock(return_value=True)
    mre.set_client_equalizer_effects_enabled = AsyncMock(return_value=True)
    mre.set_zone_equalizer_effects_enabled = AsyncMock(return_value=True)
    mre.resolve_preset_gains = AsyncMock(return_value=[1.0] * 10)
    mre.load_preset = AsyncMock(return_value=(True, [1.0] * 10))
    mre.save_custom_preset = AsyncMock(return_value=None)
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
def client(mock_camilladsp, mock_mre, mock_equalizer_router, mock_registry):
    app = FastAPI()
    router = create_equalizer_router(
        camilladsp_service=mock_camilladsp,
        routing_service=Mock(),
        crossover_service=Mock(),
        client_registry_service=mock_registry,
        equalizer_router_service=mock_equalizer_router,
        multiroom_equalizer_service=mock_mre,
    )
    app.include_router(router)
    return TestClient(app)


# =============================================================================
# Uniform per-target read: GET /api/equalizer/target/{target}
#   target ∈ "local" · "<mac>" · "zone:<id>"
# =============================================================================

class TestTargetGet:
    def test_local_target_returns_record_in_wire_shape(self, client, mock_mre):
        mock_mre.get_equalizer = AsyncMock(return_value=_sample_record())
        resp = client.get("/api/equalizer/target/local")
        assert resp.status_code == 200
        body = resp.json()
        assert mock_mre.get_equalizer.await_args.args == ("client", "local")
        assert body["enabled"] is True
        assert body["active_preset"] == "rock"
        assert body["mono"] is True
        assert body["custom_gains"] == [1.0] * 10
        assert body["compressor"]["threshold"] == -25.0
        assert body["loudness"]["high_boost"] == 4.0
        # filters use the frontend wire shape (freq/type), NOT frequency/filter_type
        f = body["filters"][0]
        assert f == {"id": "eq_band_00", "freq": 100, "gain": 3.0, "q": 1.41, "type": "Peaking", "enabled": True}
        # live connection state from the local CamillaDSP
        assert body["state"] == "running"

    def test_remote_target_resolves_to_client_mac(self, client, mock_mre, mock_registry, mock_equalizer_router):
        mock_registry.get_client.return_value = Mock()  # known client
        mock_mre.get_equalizer = AsyncMock(return_value=_sample_record())
        mock_equalizer_router.get_status = AsyncMock(
            return_value={"available": True, "state": "running", "sample_rate": 48000}
        )
        resp = client.get(f"/api/equalizer/target/{REMOTE_MAC}")
        assert resp.status_code == 200
        assert mock_mre.get_equalizer.await_args.args == ("client", REMOTE_MAC)
        # remote connection state comes from the per-client router, not local CamillaDSP
        mock_equalizer_router.get_status.assert_awaited_once_with(REMOTE_MAC)
        assert resp.json()["sample_rate"] == 48000

    def test_a_record_with_no_saved_custom_curve_still_reads(
        self, client, mock_mre, mock_registry, mock_equalizer_router
    ):
        """The neutral record every client starts from must not 500 the EQ page.

        `EqualizerSettings.default()` carries no custom_gains — nothing has been
        saved yet — and `to_dict` omits the key, but the response model requires
        ten numbers for the curve the UI draws. Serving the raw field made a 500
        on any freshly paired satellite, and on every member of a new zone
        (creation writes `default_for_zone()` to them). Found on hardware,
        2026-07-27; every other test here feeds a fully-populated record, which
        is why CI could not see it.
        """
        mock_registry.get_client.return_value = Mock()  # known client
        mock_mre.get_equalizer = AsyncMock(return_value=EqualizerSettings.default())
        mock_equalizer_router.get_status = AsyncMock(
            return_value={"available": True, "state": "running"}
        )
        resp = client.get(f"/api/equalizer/target/{REMOTE_MAC}")

        assert resp.status_code == 200
        gains = resp.json()["custom_gains"]
        assert len(gains) == len(resp.json()["filters"])
        assert all(isinstance(g, (int, float)) for g in gains)

    def test_zone_target_resolves_to_zone(self, client, mock_mre):
        mock_mre.get_equalizer = AsyncMock(return_value=_sample_record())
        resp = client.get("/api/equalizer/target/zone:zone-1")
        assert resp.status_code == 200
        assert mock_mre.get_equalizer.await_args.args == ("zone", "zone-1")

    def test_zone_target_unknown_returns_404(self, client, mock_mre):
        mock_mre.get_equalizer = AsyncMock(return_value=None)
        resp = client.get("/api/equalizer/target/zone:nope")
        assert resp.status_code == 404
        mock_mre.get_equalizer.assert_awaited_once()  # proves the route ran the access layer

    def test_remote_target_unknown_returns_404(self, client, mock_registry, mock_mre):
        mock_registry.get_client.return_value = None  # unknown client
        mock_mre.get_equalizer = AsyncMock(return_value=_sample_record())
        resp = client.get(f"/api/equalizer/target/{REMOTE_MAC}")
        assert resp.status_code == 404
        mock_registry.get_client.assert_called_with(REMOTE_MAC)  # proves the route ran
        mock_mre.get_equalizer.assert_not_awaited()  # short-circuits before the access layer


# =============================================================================
# Uniform per-target writes: PUT/POST /api/equalizer/target/{target}/...
# Every write uses one grammar; the route resolves the target and dispatches to
# the access layer with (target_type, target_id).
# =============================================================================

class TestTargetFilterWrite:
    def test_filter_local_resolves_to_client_local(self, client, mock_mre):
        resp = client.put(
            "/api/equalizer/target/local/filter/eq_band_00",
            json={"freq": 120, "gain": 4.0, "q": 1.0, "filter_type": "Peaking", "enabled": True},
        )
        assert resp.status_code == 200
        kwargs = mock_mre.update_filter.call_args.kwargs
        assert kwargs["target_type"] == "client"
        assert kwargs["target_id"] == "local"
        assert kwargs["filter_id"] == "eq_band_00"
        assert kwargs["frequency"] == 120
        assert kwargs["filter_type"] == "Peaking"

    def test_filter_remote_resolves_to_client_mac(self, client, mock_mre):
        resp = client.put(f"/api/equalizer/target/{REMOTE_MAC}/filter/eq_band_01", json={"gain": 2.0})
        assert resp.status_code == 200
        kwargs = mock_mre.update_filter.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("client", REMOTE_MAC)

    def test_filter_zone_resolves_to_zone(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/zone:z1/filter/eq_band_02", json={"gain": -1.0})
        assert resp.status_code == 200
        kwargs = mock_mre.update_filter.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("zone", "z1")

    def test_filter_unknown_target_returns_404(self, client, mock_mre):
        mock_mre.update_filter.side_effect = ValueError("Client not found: nope")
        resp = client.put("/api/equalizer/target/nope/filter/eq_band_00", json={"gain": 1.0})
        assert resp.status_code == 404
        mock_mre.update_filter.assert_awaited()  # 404 came from the access layer, not a missing route


class TestTargetCompressorLoudnessWrite:
    def test_compressor_zone_dispatches(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/zone:z1/compressor", json={"enabled": True, "ratio": 3.0})
        assert resp.status_code == 200
        kwargs = mock_mre.update_compressor.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("zone", "z1")
        assert kwargs["ratio"] == 3.0

    def test_loudness_local_dispatches(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/local/loudness", json={"enabled": True, "high_boost": 4.0})
        assert resp.status_code == 200
        kwargs = mock_mre.update_loudness.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("client", "local")
        assert kwargs["high_boost"] == 4.0


class TestTargetMonoWrite:
    def test_mono_local_succeeds(self, client, mock_mre):
        """The legacy local /mono route was missing (404); the uniform route fixes it."""
        resp = client.put("/api/equalizer/target/local/mono", json={"enabled": True})
        assert resp.status_code == 200
        kwargs = mock_mre.update_mono.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("client", "local")
        assert kwargs["enabled"] is True

    def test_mono_zone_dispatches(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/zone:z1/mono", json={"enabled": False})
        assert resp.status_code == 200
        kwargs = mock_mre.update_mono.call_args.kwargs
        assert (kwargs["target_type"], kwargs["target_id"]) == ("zone", "z1")

    def test_mono_missing_enabled_returns_400(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/local/mono", json={})
        assert resp.status_code == 400
        mock_mre.update_mono.assert_not_called()


class TestTargetEnabledWrite:
    def test_enabled_client_routes_to_client_primitive(self, client, mock_mre):
        resp = client.put(f"/api/equalizer/target/{REMOTE_MAC}/enabled", json={"enabled": False})
        assert resp.status_code == 200
        args = mock_mre.set_client_equalizer_effects_enabled.call_args.args
        assert args[0] == REMOTE_MAC
        assert args[1] is False
        mock_mre.set_zone_equalizer_effects_enabled.assert_not_called()

    def test_enabled_local_routes_to_client_primitive(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/local/enabled", json={"enabled": True})
        assert resp.status_code == 200
        assert mock_mre.set_client_equalizer_effects_enabled.call_args.args[0] == "local"

    def test_enabled_zone_routes_to_zone_primitive(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/zone:z1/enabled", json={"enabled": False})
        assert resp.status_code == 200
        mock_mre.set_zone_equalizer_effects_enabled.assert_awaited_once_with("z1", False)
        mock_mre.set_client_equalizer_effects_enabled.assert_not_called()

    def test_enabled_missing_returns_400(self, client, mock_mre):
        resp = client.put("/api/equalizer/target/local/enabled", json={})
        assert resp.status_code == 400


class TestTargetPresetWrite:
    def test_preset_client_loads_and_returns_gains(self, client, mock_mre):
        resp = client.post("/api/equalizer/target/local/preset", json={"preset_id": "rock"})
        assert resp.status_code == 200
        mock_mre.load_preset.assert_awaited_once_with("client", "local", "rock")
        # The gains come back from the one load call — the route no longer
        # re-reads the record and re-resolves them to report what it applied.
        assert resp.json()["gains"] == [1.0] * 10
        mock_mre.resolve_preset_gains.assert_not_called()

    def test_preset_zone_loads_zone(self, client, mock_mre):
        resp = client.post("/api/equalizer/target/zone:z1/preset", json={"preset_id": "jazz"})
        assert resp.status_code == 200
        mock_mre.load_preset.assert_awaited_once_with("zone", "z1", "jazz")

    def test_preset_unknown_target_returns_404(self, client, mock_mre):
        mock_mre.load_preset.side_effect = ValueError("Client not found: nope")
        resp = client.post("/api/equalizer/target/nope/preset", json={"preset_id": "rock"})
        assert resp.status_code == 404
        mock_mre.load_preset.assert_awaited()  # 404 came from the access layer


class TestTargetSaveCustomWrite:
    def test_save_custom_client_dispatches(self, client, mock_mre):
        resp = client.post("/api/equalizer/target/local/save-custom")
        assert resp.status_code == 200
        mock_mre.save_custom_preset.assert_awaited_once_with("client", "local")

    def test_save_custom_zone_dispatches(self, client, mock_mre):
        resp = client.post("/api/equalizer/target/zone:z1/save-custom")
        assert resp.status_code == 200
        mock_mre.save_custom_preset.assert_awaited_once_with("zone", "z1")


# =============================================================================
# Global preset catalog: GET /api/equalizer/presets (orthogonal to per-target)
# =============================================================================

class TestPresetsCatalog:
    def test_presets_returns_catalog(self, client, mock_camilladsp):
        mock_camilladsp.get_presets = Mock(return_value=[{"id": "flat", "gains": [0] * 10}])
        mock_camilladsp.get_active_preset = AsyncMock(return_value="flat")
        mock_camilladsp.get_custom_gains = AsyncMock(return_value=[0] * 10)
        resp = client.get("/api/equalizer/presets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["presets"] == [{"id": "flat", "gains": [0] * 10}]
        assert body["active_preset"] == "flat"
        assert body["custom_gains"] == [0] * 10
