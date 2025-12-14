# backend/tests/test_routes_settings.py
"""
Unit tests for Settings API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, patch
from backend.presentation.api.routes.settings import create_settings_router


class TestSettingsRoutes:
    """Tests for settings routes"""

    @pytest.fixture
    def mock_ws_manager(self):
        """WebSocket manager mock"""
        manager = Mock()
        manager.broadcast_dict = AsyncMock()
        return manager

    @pytest.fixture
    def mock_volume_service(self):
        """Volume service mock"""
        service = Mock()
        service.reload_volume_limits = AsyncMock(return_value=True)
        service.reload_startup_config = AsyncMock(return_value=True)
        service.reload_volume_steps_config = AsyncMock(return_value=True)
        service.reload_rotary_steps_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.system_state = Mock()
        sm.system_state.active_source = Mock()
        sm.system_state.active_source.value = "none"
        sm.transition_to_source = AsyncMock(return_value=True)
        sm.get_current_state = AsyncMock(return_value={"active_source": "none"})
        sm.get_plugin = Mock(return_value=None)
        sm.update_multiroom_state = AsyncMock()
        sm.update_equalizer_state = AsyncMock()
        return sm

    @pytest.fixture
    def mock_screen_controller(self):
        """Screen controller mock"""
        controller = Mock()
        controller.reload_timeout_config = AsyncMock(return_value=True)
        controller.brightness_on = 5
        controller.screen_type = "official"
        controller.screen_on = True
        controller.timeout_seconds = 10
        controller.last_activity_time = 0
        controller.current_plugin_state = "PLAYING"
        controller._update_screen_commands = Mock()
        controller._screen_cmd = AsyncMock()
        controller.on_touch_detected = AsyncMock()
        return controller

    @pytest.fixture
    def mock_systemd_manager(self):
        """Systemd manager mock"""
        manager = Mock()
        manager.start = AsyncMock(return_value=True)
        manager.stop = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def mock_routing_service(self):
        """Routing service mock"""
        service = Mock()
        service.set_multiroom_enabled = AsyncMock(return_value=True)
        service.set_equalizer_enabled = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_hardware_service(self):
        """Hardware service mock"""
        service = Mock()
        service.get_screen_info = Mock(return_value={
            "type": "official",
            "resolution": {"width": 800, "height": 480}
        })
        return service

    @pytest.fixture
    def client(
        self,
        mock_ws_manager,
        mock_volume_service,
        mock_state_machine,
        mock_screen_controller,
        mock_systemd_manager,
        mock_routing_service,
        mock_hardware_service
    ):
        """Fixture to create a TestClient with mocks"""
        app = FastAPI()

        with patch('backend.presentation.api.routes.settings.SettingsService') as mock_settings_class:
            mock_settings = Mock()
            mock_settings.get_setting = AsyncMock(return_value=None)
            mock_settings.set_setting = AsyncMock(return_value=True)
            mock_settings.load_settings = AsyncMock(return_value={})
            mock_settings._cache = None
            mock_settings_class.return_value = mock_settings

            router = create_settings_router(
                ws_manager=mock_ws_manager,
                volume_service=mock_volume_service,
                state_machine=mock_state_machine,
                screen_controller=mock_screen_controller,
                systemd_manager=mock_systemd_manager,
                routing_service=mock_routing_service,
                hardware_service=mock_hardware_service
            )

            app.include_router(router, prefix="/api/settings")

            client = TestClient(app)
            client._mock_settings = mock_settings
            return client

    # ===================
    # LANGUAGE TESTS
    # ===================

    def test_get_language(self, client):
        """Test GET /language"""
        client._mock_settings.get_setting = AsyncMock(return_value='french')
        response = client.get("/api/settings/language")
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_language_valid_french(self, client):
        """Test POST /language with valid language (french)"""
        response = client.post("/api/settings/language", json={"language": "french"})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_language_valid_english(self, client):
        """Test POST /language with valid language (english)"""
        response = client.post("/api/settings/language", json={"language": "english"})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_language_invalid(self, client):
        """Test POST /language with invalid language - should return 422"""
        response = client.post("/api/settings/language", json={"language": "klingon"})
        assert response.status_code == 422

    def test_set_language_missing_field(self, client):
        """Test POST /language without language field - should return 422"""
        response = client.post("/api/settings/language", json={})
        assert response.status_code == 422

    # ===================
    # VOLUME LIMITS TESTS (dB-based)
    # ===================

    def test_get_volume_limits(self, client):
        """Test GET /volume-limits"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "limit_min_db": -80.0, "limit_max_db": -21.0
        })
        response = client.get("/api/settings/volume-limits")
        assert response.status_code == 200
        assert "limits" in response.json()

    def test_set_volume_limits_valid(self, client):
        """Test POST /volume-limits with valid values"""
        response = client.post("/api/settings/volume-limits", json={
            "min_db": -50.0,
            "max_db": -15.0
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_limits_invalid_range(self, client):
        """Test POST /volume-limits with range < 6 dB - should return 422"""
        response = client.post("/api/settings/volume-limits", json={
            "min_db": -25.0,
            "max_db": -23.0
        })
        assert response.status_code == 422

    def test_set_volume_limits_min_greater_than_max(self, client):
        """Test POST /volume-limits with min > max - should return 422"""
        response = client.post("/api/settings/volume-limits", json={
            "min_db": -15.0,
            "max_db": -50.0
        })
        assert response.status_code == 422

    def test_set_volume_limits_out_of_range(self, client):
        """Test POST /volume-limits with out of range values - should return 422"""
        response = client.post("/api/settings/volume-limits", json={
            "min_db": -90.0,
            "max_db": 10.0
        })
        assert response.status_code == 422

    def test_toggle_volume_limits_enable(self, client):
        """Test POST /volume-limits/toggle enabled=true"""
        response = client.post("/api/settings/volume-limits/toggle", json={
            "enabled": True
        })
        assert response.status_code == 200

    def test_toggle_volume_limits_disable(self, client):
        """Test POST /volume-limits/toggle enabled=false"""
        response = client.post("/api/settings/volume-limits/toggle", json={
            "enabled": False
        })
        assert response.status_code == 200

    # ===================
    # VOLUME STARTUP TESTS
    # ===================

    def test_get_volume_startup(self, client):
        """Test GET /volume-startup"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "startup_volume": 37, "restore_last_volume": False
        })
        response = client.get("/api/settings/volume-startup")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_set_volume_startup_valid(self, client):
        """Test POST /volume-startup with valid values"""
        response = client.post("/api/settings/volume-startup", json={
            "startup_volume": 50,
            "restore_last_volume": True
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_startup_out_of_range(self, client):
        """Test POST /volume-startup with out of range volume - should return 422"""
        response = client.post("/api/settings/volume-startup", json={
            "startup_volume": 150,
            "restore_last_volume": False
        })
        assert response.status_code == 422

    # ===================
    # VOLUME STEPS TESTS
    # ===================

    def test_get_volume_steps(self, client):
        """Test GET /volume-steps"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "mobile_volume_steps": 5
        })
        response = client.get("/api/settings/volume-steps")
        assert response.status_code == 200

    def test_set_volume_steps_valid(self, client):
        """Test POST /volume-steps with valid value"""
        response = client.post("/api/settings/volume-steps", json={
            "mobile_volume_steps": 7
        })
        assert response.status_code == 200

    def test_set_volume_steps_out_of_range(self, client):
        """Test POST /volume-steps with out of range value - should return 422"""
        response = client.post("/api/settings/volume-steps", json={
            "mobile_volume_steps": 15
        })
        assert response.status_code == 422

    # ===================
    # ROTARY STEPS TESTS
    # ===================

    def test_get_rotary_steps(self, client):
        """Test GET /rotary-steps"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "rotary_volume_steps": 2
        })
        response = client.get("/api/settings/rotary-steps")
        assert response.status_code == 200

    def test_set_rotary_steps_valid(self, client):
        """Test POST /rotary-steps with valid value"""
        response = client.post("/api/settings/rotary-steps", json={
            "rotary_volume_steps": 3
        })
        assert response.status_code == 200

    def test_set_rotary_steps_out_of_range(self, client):
        """Test POST /rotary-steps with out of range value - should return 422"""
        response = client.post("/api/settings/rotary-steps", json={
            "rotary_volume_steps": 20
        })
        assert response.status_code == 422

    # ===================
    # DOCK APPS TESTS
    # ===================

    def test_get_dock_apps(self, client):
        """Test GET /dock-apps"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "enabled_apps": ["spotify", "bluetooth", "settings"]
        })
        response = client.get("/api/settings/dock-apps")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_set_dock_apps_valid(self, client):
        """Test POST /dock-apps with valid apps"""
        client._mock_settings.load_settings = AsyncMock(return_value={
            "dock": {"enabled_apps": ["spotify", "bluetooth"]}
        })
        response = client.post("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "bluetooth", "settings"]
        })
        assert response.status_code == 200

    def test_set_dock_apps_no_audio_source(self, client):
        """Test POST /dock-apps without audio source - should return 422"""
        response = client.post("/api/settings/dock-apps", json={
            "enabled_apps": ["settings", "equalizer"]
        })
        assert response.status_code == 422

    def test_set_dock_apps_invalid_app(self, client):
        """Test POST /dock-apps with invalid app - should return 422"""
        response = client.post("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "invalid_app"]
        })
        assert response.status_code == 422

    # ===================
    # SPOTIFY DISCONNECT TESTS
    # ===================

    def test_get_spotify_disconnect(self, client):
        """Test GET /spotify-disconnect"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "auto_disconnect_delay": 10.0
        })
        response = client.get("/api/settings/spotify-disconnect")
        assert response.status_code == 200

    def test_set_spotify_disconnect_valid(self, client):
        """Test POST /spotify-disconnect with valid value"""
        response = client.post("/api/settings/spotify-disconnect", json={
            "auto_disconnect_delay": 15.0
        })
        assert response.status_code == 200

    def test_set_spotify_disconnect_zero_disable(self, client):
        """Test POST /spotify-disconnect with 0 (disabled)"""
        response = client.post("/api/settings/spotify-disconnect", json={
            "auto_disconnect_delay": 0.0
        })
        assert response.status_code == 200

    def test_set_spotify_disconnect_negative(self, client):
        """Test POST /spotify-disconnect with negative value - should return 422"""
        response = client.post("/api/settings/spotify-disconnect", json={
            "auto_disconnect_delay": -5.0
        })
        assert response.status_code == 422

    # ===================
    # SCREEN TESTS
    # ===================

    def test_get_screen_timeout(self, client):
        """Test GET /screen-timeout"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "timeout_seconds": 10
        })
        response = client.get("/api/settings/screen-timeout")
        assert response.status_code == 200

    def test_set_screen_timeout_valid(self, client):
        """Test POST /screen-timeout with valid value"""
        response = client.post("/api/settings/screen-timeout", json={
            "screen_timeout_enabled": True,
            "screen_timeout_seconds": 30
        })
        assert response.status_code == 200

    def test_set_screen_timeout_zero_disable(self, client):
        """Test POST /screen-timeout with 0 (disabled)"""
        response = client.post("/api/settings/screen-timeout", json={
            "screen_timeout_enabled": False,
            "screen_timeout_seconds": 0
        })
        assert response.status_code == 200

    def test_get_screen_brightness(self, client):
        """Test GET /screen-brightness"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "brightness_on": 5
        })
        response = client.get("/api/settings/screen-brightness")
        assert response.status_code == 200

    def test_set_screen_brightness_valid(self, client):
        """Test POST /screen-brightness with valid value"""
        response = client.post("/api/settings/screen-brightness", json={
            "brightness_on": 7
        })
        assert response.status_code == 200

    def test_set_screen_brightness_out_of_range(self, client):
        """Test POST /screen-brightness with out of range value - should return 422"""
        response = client.post("/api/settings/screen-brightness", json={
            "brightness_on": 15
        })
        assert response.status_code == 422

    # ===================
    # INFO ENDPOINTS TESTS
    # ===================

    def test_get_hardware_info(self, client):
        """Test GET /hardware-info"""
        response = client.get("/api/settings/hardware-info")
        assert response.status_code == 200
        assert "hardware" in response.json()
