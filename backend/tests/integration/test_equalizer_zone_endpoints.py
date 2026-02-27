# backend/tests/integration/test_equalizer_zone_endpoints.py
"""
Integration tests for Story 4-7: API Endpoints for Equalizer

Tests cover:
- AC1: Zone filter update (PATCH /api/equalizer/zone/{zone_id}/filter/{filter_id})
- AC2: Zone compressor control (PATCH /api/equalizer/zone/{zone_id}/compressor)
- AC3: Zone loudness control (PATCH /api/equalizer/zone/{zone_id}/loudness)
- AC4: Zone Equalizer bypass (PATCH /api/equalizer/zone/{zone_id}/enabled)
- AC5: Zone preset loading (POST /api/equalizer/zone/{zone_id}/preset) - exists
- AC6: Client proxy routes verification
- AC7: Presets list endpoint (GET /api/equalizer/presets)

These tests verify:
- Zone endpoints call multiroom_equalizer_service methods
- Error handling for zone not found, client errors, etc.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import HTTPException

from backend.api.equalizer import create_equalizer_router
from backend.api.models import EqualizerFilterUpdateRequest, EqualizerCompressorRequest, EqualizerLoudnessRequest
from backend.core.multiroom.models import Zone, Client


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_camilladsp_service():
    """Create mock Equalizer service with common methods"""
    service = Mock()
    service.get_filters = AsyncMock(return_value=[
        {"id": "eq_band_00", "freq": 31, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True},
        {"id": "eq_band_01", "freq": 63, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True}
    ])
    service.set_filter = AsyncMock(return_value=True)
    service.set_compressor = AsyncMock(return_value=True)
    service.set_loudness = AsyncMock(return_value=True)
    service.get_presets = Mock(return_value=[
        {"id": "flat", "name": "Flat", "gains": [0]*10},
        {"id": "jazz", "name": "Jazz", "gains": [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]}
    ])
    service.get_active_preset = AsyncMock(return_value="flat")
    service.get_manual_gains = AsyncMock(return_value=[0]*10)
    service.load_preset = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_state_machine():
    """Create mock state machine for WebSocket broadcasting"""
    sm = Mock()
    sm.broadcast_event = AsyncMock()
    return sm


@pytest.fixture
def mock_routing_service():
    """Create mock routing service for Equalizer enabled state"""
    rs = Mock()
    rs.equalizer_effects_enabled = True
    rs.set_equalizer_effects_enabled = AsyncMock(return_value=True)
    return rs


@pytest.fixture
def mock_proxy_service():
    """Create mock proxy service for remote client communication"""
    proxy = Mock()
    proxy.check_available = AsyncMock(return_value=True)
    proxy.request = AsyncMock(return_value={"status": "success"})
    return proxy


@pytest.fixture
def mock_equalizer_router_service():
    """Create mock Equalizer router service for local/remote client checks"""
    service = Mock()
    # Local client returns True, remote clients return False
    service.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    # Add async methods used by client proxy routes
    service.update_filter = AsyncMock(return_value={"status": "success"})
    service.set_compressor = AsyncMock(return_value={"status": "success"})
    service.set_loudness = AsyncMock(return_value={"status": "success"})
    service.set_enabled = AsyncMock(return_value={"status": "success"})
    return service


@pytest.fixture
def mock_multiroom_equalizer_service():
    """Create mock multiroom Equalizer service for zone operations"""
    service = Mock()
    service.load_zone_preset = AsyncMock(return_value=True)
    service.update_filter = AsyncMock(return_value=True)
    service.update_compressor = AsyncMock(return_value=True)
    service.update_loudness = AsyncMock(return_value=True)
    service.set_zone_equalizer_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_client_registry_service():
    """Create mock client registry service with zone and clients"""
    registry = Mock()

    # Create test zone with 3 clients: local, online remote, offline remote
    test_zone = Zone(
        id="zone_test123",
        name="Living Room",
        client_ids=["local", "dca6327ed343", "112233445566"]
    )

    # Create client objects (Client model uses ip for hostname resolution)
    local_client = Client(
        mac_id="local",
        name="Milo Main",
        ip="127.0.0.1",
        online=True
    )
    online_remote = Client(
        mac_id="dca6327ed343",
        name="Kitchen",
        ip="milo-client-kitchen",  # IP can be hostname or IP address
        online=True
    )
    offline_remote = Client(
        mac_id="112233445566",
        name="Bedroom",
        ip="milo-client-bedroom",
        online=False
    )

    registry.get_zone = Mock(return_value=test_zone)
    registry.get_client = Mock(side_effect=lambda mac_id: {
        "local": local_client,
        "dca6327ed343": online_remote,
        "112233445566": offline_remote
    }.get(mac_id))

    return registry


@pytest.fixture
def equalizer_router(mock_camilladsp_service, mock_state_machine, mock_routing_service,
               mock_proxy_service, mock_client_registry_service, mock_equalizer_router_service,
               mock_multiroom_equalizer_service):
    """Create Equalizer router with all mocked dependencies"""
    return create_equalizer_router(
        camilladsp_service=mock_camilladsp_service,
        state_machine=mock_state_machine,
        routing_service=mock_routing_service,
        proxy_service=mock_proxy_service,
        client_registry_service=mock_client_registry_service,
        equalizer_router_service=mock_equalizer_router_service,
        multiroom_equalizer_service=mock_multiroom_equalizer_service
    )


# =============================================================================
# AC1: Zone Filter Update
# =============================================================================

class TestAC1ZoneFilterUpdate:
    """AC1: Zone filter update delegates to multiroom_equalizer_service"""

    @pytest.mark.asyncio
    async def test_zone_filter_update_calls_multiroom_service(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should delegate filter update to multiroom_equalizer_service"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        # Get the route function directly
        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'filter' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone filter route not found"

        # Call the endpoint
        payload = EqualizerFilterUpdateRequest(gain=3.0, freq=125)
        result = await route_fn("zone_test123", "eq_band_00", payload)

        # Verify multiroom_equalizer_service was called
        mock_multiroom_equalizer_service.update_filter.assert_called_once_with(
            target_type="zone",
            target_id="zone_test123",
            filter_id="eq_band_00",
            frequency=125,
            gain=3.0,
            q=None,
            filter_type=None,
            enabled=None
        )

        # Verify response format
        assert result["status"] == "success"
        assert result["zone_id"] == "zone_test123"

    @pytest.mark.asyncio
    async def test_zone_filter_update_returns_404_for_unknown_zone(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should return 404 for unknown zone"""
        # Configure multiroom_equalizer_service to raise ValueError for unknown zone
        mock_multiroom_equalizer_service.update_filter = AsyncMock(
            side_effect=ValueError("Zone not found: unknown_zone")
        )

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'filter' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = EqualizerFilterUpdateRequest(gain=3.0)

        with pytest.raises(HTTPException) as exc:
            await route_fn("unknown_zone", "eq_band_00", payload)

        assert exc.value.status_code == 404


# =============================================================================
# AC2: Zone Compressor Control
# =============================================================================

class TestAC2ZoneCompressorControl:
    """AC2: Zone compressor control delegates to multiroom_equalizer_service"""

    @pytest.mark.asyncio
    async def test_zone_compressor_update_calls_multiroom_service(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should delegate compressor update to multiroom_equalizer_service"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'compressor' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone compressor route not found"

        payload = EqualizerCompressorRequest(enabled=True, threshold=-20)
        result = await route_fn("zone_test123", payload)

        # Verify multiroom_equalizer_service was called
        mock_multiroom_equalizer_service.update_compressor.assert_called_once_with(
            target_type="zone",
            target_id="zone_test123",
            enabled=True,
            threshold=-20,
            ratio=None,
            attack=None,
            release=None,
            makeup_gain=None
        )

        # Verify response format
        assert result["status"] == "success"
        assert result["zone_id"] == "zone_test123"


# =============================================================================
# AC3: Zone Loudness Control
# =============================================================================

class TestAC3ZoneLoudnessControl:
    """AC3: Zone loudness control delegates to multiroom_equalizer_service"""

    @pytest.mark.asyncio
    async def test_zone_loudness_update_calls_multiroom_service(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should delegate loudness update to multiroom_equalizer_service"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'loudness' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone loudness route not found"

        payload = EqualizerLoudnessRequest(enabled=True, high_boost=3.0)
        result = await route_fn("zone_test123", payload)

        # Verify multiroom_equalizer_service was called
        mock_multiroom_equalizer_service.update_loudness.assert_called_once_with(
            target_type="zone",
            target_id="zone_test123",
            enabled=True,
            high_boost=3.0,
            low_boost=None
        )

        # Verify response format
        assert result["status"] == "success"
        assert result["zone_id"] == "zone_test123"


# =============================================================================
# AC4: Zone Equalizer Bypass
# =============================================================================

class TestAC4ZoneDspBypass:
    """AC4: Zone Equalizer bypass delegates to multiroom_equalizer_service"""

    @pytest.mark.asyncio
    async def test_zone_equalizer_enabled_update_calls_multiroom_service(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should delegate Equalizer enabled update to multiroom_equalizer_service"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'enabled' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone enabled route not found"

        # Create mock request
        mock_request = AsyncMock()
        mock_request.json = AsyncMock(return_value={"enabled": False})

        result = await route_fn("zone_test123", mock_request)

        # Verify multiroom_equalizer_service was called
        mock_multiroom_equalizer_service.set_zone_equalizer_effects_enabled.assert_called_once_with(
            "zone_test123", False
        )

        # Verify response format
        assert result["status"] == "success"
        assert result["enabled"] is False

    @pytest.mark.asyncio
    async def test_zone_equalizer_enabled_requires_enabled_field(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should return 400 if enabled field is missing"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'enabled' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        mock_request = AsyncMock()
        mock_request.json = AsyncMock(return_value={})  # Missing 'enabled'

        with pytest.raises(HTTPException) as exc:
            await route_fn("zone_test123", mock_request)

        assert exc.value.status_code == 400


# =============================================================================
# AC6: Client Proxy Routes
# =============================================================================

class TestAC6ClientProxyRoutes:
    """AC6: Client proxy routes work for standalone clients"""

    @pytest.mark.asyncio
    async def test_client_filter_proxy_works(
        self, mock_camilladsp_service, mock_state_machine, mock_proxy_service,
        mock_equalizer_router_service, mock_multiroom_equalizer_service
    ):
        """Should proxy filter update to remote client"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        # Find client filter route
        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and '/client/' in route.path and '/filter/' in route.path:
                if route.methods == {'PUT'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Client filter route not found"

        # Create mock request
        mock_request = AsyncMock()
        mock_request.json = AsyncMock(return_value={"gain": 3.0})

        # Configure equalizer_router_service to handle the call
        mock_equalizer_router_service.update_filter = AsyncMock(return_value={"status": "success"})

        result = await route_fn("milo-client-01", "eq_band_00", mock_request)

        # Verify equalizer_router_service was called
        mock_equalizer_router_service.update_filter.assert_called()

    @pytest.mark.asyncio
    async def test_client_compressor_proxy_works(
        self, mock_camilladsp_service, mock_state_machine, mock_proxy_service,
        mock_equalizer_router_service, mock_multiroom_equalizer_service
    ):
        """Should proxy compressor update to remote client"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        # Find client compressor route
        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and '/client/' in route.path and '/compressor' in route.path:
                if route.methods == {'PUT'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Client compressor route not found"

        mock_request = AsyncMock()
        mock_request.json = AsyncMock(return_value={"enabled": True})

        result = await route_fn("milo-client-01", mock_request)

        # Verify equalizer_router_service was called
        mock_equalizer_router_service.set_compressor.assert_called()


# =============================================================================
# AC7: Presets List
# =============================================================================

class TestAC7PresetsList:
    """AC7: Presets list returns all available presets"""

    @pytest.mark.asyncio
    async def test_presets_endpoint_returns_all_presets(
        self, mock_camilladsp_service, mock_state_machine, mock_equalizer_router_service, mock_multiroom_equalizer_service
    ):
        """Should return all builtin presets with manual gains and active preset"""
        # Set up mock with all 21 presets + manual gains
        full_presets = [{"id": f"preset_{i}", "name": f"Preset {i}", "gains": [0]*10} for i in range(21)]
        mock_camilladsp_service.get_presets = Mock(return_value=full_presets)

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        # Find presets GET route
        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and route.path == '/api/equalizer/presets':
                if route.methods == {'GET'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Presets route not found"

        result = await route_fn()

        # Verify response format
        assert "presets" in result
        assert len(result["presets"]) == 21
        assert "manual_gains" in result
        assert "active_preset" in result


# =============================================================================
# Error Handling
# =============================================================================

class TestErrorHandling:
    """Test error handling scenarios"""

    @pytest.mark.asyncio
    async def test_zone_endpoint_returns_error_when_service_fails(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should return error status when multiroom_equalizer_service fails"""
        mock_multiroom_equalizer_service.update_compressor = AsyncMock(return_value=False)

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'compressor' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = EqualizerCompressorRequest(enabled=True)
        result = await route_fn("zone_test123", payload)

        # Should return error status
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_zone_preset_calls_multiroom_service(
        self, mock_camilladsp_service, mock_state_machine, mock_multiroom_equalizer_service, mock_equalizer_router_service
    ):
        """Should delegate preset loading to multiroom_equalizer_service"""
        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp_service,
            state_machine=mock_state_machine,
            equalizer_router_service=mock_equalizer_router_service,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'preset' in route.path:
                if route.methods == {'POST'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone preset route not found"

        # Create mock payload
        from backend.api.models import EqualizerPresetRequest
        payload = EqualizerPresetRequest(preset_id="jazz")

        result = await route_fn("zone_test123", payload)

        # Verify multiroom_equalizer_service was called
        mock_multiroom_equalizer_service.load_zone_preset.assert_called_once_with(
            "zone_test123", "jazz"
        )

        # Verify response format
        assert result["status"] == "success"
        assert result["zone_id"] == "zone_test123"
        assert result["preset_id"] == "jazz"
