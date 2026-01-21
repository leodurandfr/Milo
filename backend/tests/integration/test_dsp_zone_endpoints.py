# backend/tests/integration/test_dsp_zone_endpoints.py
"""
Integration tests for Story 4-7: API Endpoints for DSP

Tests cover:
- AC1: Zone filter update (PATCH /api/dsp/zone/{zone_id}/filter/{filter_id})
- AC2: Zone compressor control (PATCH /api/dsp/zone/{zone_id}/compressor)
- AC3: Zone loudness control (PATCH /api/dsp/zone/{zone_id}/loudness)
- AC4: Zone DSP bypass (PATCH /api/dsp/zone/{zone_id}/enabled)
- AC5: Zone preset loading (POST /api/dsp/zone/{zone_id}/preset) - exists
- AC6: Client proxy routes verification
- AC7: Presets list endpoint (GET /api/dsp/presets)

These tests verify:
- Zone endpoints propagate to all ONLINE clients
- OFFLINE clients are gracefully skipped
- WebSocket events are broadcast with correct data
- Error handling for zone not found, client errors, etc.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import HTTPException

from backend.api.dsp import create_dsp_router
from backend.api.models import DspFilterUpdateRequest, DspCompressorRequest, DspLoudnessRequest
from backend.core.multiroom.models import Zone, Client


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_dsp_service():
    """Create mock DSP service with common methods"""
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
    """Create mock routing service for DSP enabled state"""
    rs = Mock()
    rs.dsp_effects_enabled = True
    rs.set_dsp_effects_enabled = AsyncMock(return_value=True)
    return rs


@pytest.fixture
def mock_proxy_service():
    """Create mock proxy service for remote client communication"""
    proxy = Mock()
    proxy.check_available = AsyncMock(return_value=True)
    proxy.request = AsyncMock(return_value={"status": "success"})
    return proxy


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
def dsp_router(mock_dsp_service, mock_state_machine, mock_routing_service,
               mock_proxy_service, mock_client_registry_service):
    """Create DSP router with all mocked dependencies"""
    return create_dsp_router(
        dsp_service=mock_dsp_service,
        state_machine=mock_state_machine,
        routing_service=mock_routing_service,
        proxy_service=mock_proxy_service,
        client_registry_service=mock_client_registry_service
    )


# =============================================================================
# AC1: Zone Filter Update
# =============================================================================

class TestAC1ZoneFilterUpdate:
    """AC1: Zone filter update propagates to ONLINE clients"""

    @pytest.mark.asyncio
    async def test_zone_filter_update_propagates_to_online_clients(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service,
        mock_client_registry_service
    ):
        """Should update filter on local and online remote clients"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
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
        payload = DspFilterUpdateRequest(gain=3.0)
        result = await route_fn("zone_test123", "eq_band_00", payload)

        # Verify local DSP service was called
        mock_dsp_service.set_filter.assert_called_once()

        # Verify proxy was called for online remote client
        mock_proxy_service.request.assert_called()
        proxy_call = mock_proxy_service.request.call_args
        assert "milo-client-kitchen" in str(proxy_call) or "192.168.1.100" in str(proxy_call)

        # Verify WebSocket event was broadcast
        mock_state_machine.broadcast_event.assert_called()
        event_call = mock_state_machine.broadcast_event.call_args
        assert event_call[0][0] == "dsp"
        assert event_call[0][1] == "zone_filter_changed"

    @pytest.mark.asyncio
    async def test_zone_filter_update_skips_offline_clients(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service,
        mock_client_registry_service
    ):
        """Should skip offline clients gracefully"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'filter' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = DspFilterUpdateRequest(gain=3.0)
        result = await route_fn("zone_test123", "eq_band_00", payload)

        # Verify offline client was not called via proxy
        for call in mock_proxy_service.request.call_args_list:
            assert "milo-client-bedroom" not in str(call)
            assert "192.168.1.101" not in str(call)

        # Verify offline_clients in response
        assert result.get("offline_clients") == ["112233445566"]

    @pytest.mark.asyncio
    async def test_zone_filter_update_returns_404_for_unknown_zone(
        self, mock_dsp_service, mock_state_machine, mock_client_registry_service
    ):
        """Should return 404 for unknown zone"""
        mock_client_registry_service.get_zone = Mock(return_value=None)

        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            client_registry_service=mock_client_registry_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'filter' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = DspFilterUpdateRequest(gain=3.0)

        with pytest.raises(HTTPException) as exc:
            await route_fn("unknown_zone", "eq_band_00", payload)

        assert exc.value.status_code == 404


# =============================================================================
# AC2: Zone Compressor Control
# =============================================================================

class TestAC2ZoneCompressorControl:
    """AC2: Zone compressor control propagates to ONLINE clients"""

    @pytest.mark.asyncio
    async def test_zone_compressor_update_propagates_correctly(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service,
        mock_client_registry_service
    ):
        """Should update compressor on all online clients"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'compressor' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone compressor route not found"

        payload = DspCompressorRequest(enabled=True, threshold=-20)
        result = await route_fn("zone_test123", payload)

        # Verify local DSP service was called
        mock_dsp_service.set_compressor.assert_called_once()

        # Verify WebSocket event
        mock_state_machine.broadcast_event.assert_called()
        event_call = mock_state_machine.broadcast_event.call_args
        assert event_call[0][1] == "zone_compressor_changed"

        # Verify response format
        assert result["status"] in ["success", "partial"]
        assert result["zone_id"] == "zone_test123"
        assert "applied_to" in result


# =============================================================================
# AC3: Zone Loudness Control
# =============================================================================

class TestAC3ZoneLoudnessControl:
    """AC3: Zone loudness control propagates to ONLINE clients"""

    @pytest.mark.asyncio
    async def test_zone_loudness_update_propagates_correctly(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service,
        mock_client_registry_service
    ):
        """Should update loudness on all online clients"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'loudness' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        assert route_fn is not None, "Zone loudness route not found"

        payload = DspLoudnessRequest(enabled=True)
        result = await route_fn("zone_test123", payload)

        # Verify local DSP service was called
        mock_dsp_service.set_loudness.assert_called_once()

        # Verify WebSocket event
        mock_state_machine.broadcast_event.assert_called()
        event_call = mock_state_machine.broadcast_event.call_args
        assert event_call[0][1] == "zone_loudness_changed"

        # Verify response format
        assert result["status"] in ["success", "partial"]
        assert result["zone_id"] == "zone_test123"


# =============================================================================
# AC4: Zone DSP Bypass
# =============================================================================

class TestAC4ZoneDspBypass:
    """AC4: Zone DSP bypass propagates to ONLINE clients"""

    @pytest.mark.asyncio
    async def test_zone_dsp_enabled_update_propagates_correctly(
        self, mock_dsp_service, mock_state_machine, mock_routing_service,
        mock_proxy_service, mock_client_registry_service
    ):
        """Should update DSP enabled state on all online clients"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            routing_service=mock_routing_service,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
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

        # Verify routing service was called for local
        mock_routing_service.set_dsp_effects_enabled.assert_called_with(False)

        # Verify WebSocket event
        mock_state_machine.broadcast_event.assert_called()
        event_call = mock_state_machine.broadcast_event.call_args
        assert event_call[0][1] == "zone_enabled_changed"

        # Verify response format
        assert result["status"] in ["success", "partial"]
        assert result["enabled"] is False


# =============================================================================
# AC6: Client Proxy Routes
# =============================================================================

class TestAC6ClientProxyRoutes:
    """AC6: Client proxy routes work for standalone clients"""

    @pytest.mark.asyncio
    async def test_client_filter_proxy_works(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service
    ):
        """Should proxy filter update to remote client"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service
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

        result = await route_fn("milo-client-01", "eq_band_00", mock_request)

        # Verify proxy was called
        mock_proxy_service.request.assert_called()

    @pytest.mark.asyncio
    async def test_client_proxy_skips_unavailable_client(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service
    ):
        """Should skip unavailable client gracefully"""
        mock_proxy_service.check_available = AsyncMock(return_value=False)

        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service
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

        result = await route_fn("milo-client-offline", mock_request)

        # Verify skipped response
        assert result["status"] == "skipped"
        assert result["reason"] == "client_unavailable"


# =============================================================================
# AC7: Presets List
# =============================================================================

class TestAC7PresetsList:
    """AC7: Presets list returns all available presets"""

    @pytest.mark.asyncio
    async def test_presets_endpoint_returns_all_presets(
        self, mock_dsp_service, mock_state_machine
    ):
        """Should return all builtin presets with manual gains and active preset"""
        # Set up mock with all 21 presets + manual gains
        full_presets = [{"id": f"preset_{i}", "name": f"Preset {i}", "gains": [0]*10} for i in range(21)]
        mock_dsp_service.get_presets = Mock(return_value=full_presets)

        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine
        )

        # Find presets GET route
        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and route.path == '/api/dsp/presets':
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
    async def test_zone_endpoint_returns_500_without_registry(
        self, mock_dsp_service, mock_state_machine
    ):
        """Should return 500 if registry service not available"""
        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            client_registry_service=None
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'compressor' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = DspCompressorRequest(enabled=True)

        with pytest.raises(HTTPException) as exc:
            await route_fn("zone_test123", payload)

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_partial_success_when_some_clients_fail(
        self, mock_dsp_service, mock_state_machine, mock_proxy_service,
        mock_client_registry_service
    ):
        """Should return partial status when some clients fail"""
        # Make proxy fail for remote client
        mock_proxy_service.request = AsyncMock(return_value={"status": "error", "message": "Connection refused"})

        router = create_dsp_router(
            dsp_service=mock_dsp_service,
            state_machine=mock_state_machine,
            proxy_service=mock_proxy_service,
            client_registry_service=mock_client_registry_service
        )

        route_fn = None
        for route in router.routes:
            if hasattr(route, 'path') and 'zone' in route.path and 'loudness' in route.path:
                if route.methods == {'PATCH'}:
                    route_fn = route.endpoint
                    break

        payload = DspLoudnessRequest(enabled=True)
        result = await route_fn("zone_test123", payload)

        # Should return partial status
        assert result["status"] == "partial"
        assert "errors" in result
