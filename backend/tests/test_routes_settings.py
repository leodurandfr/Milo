# backend/tests/test_routes_settings.py
"""
Unit tests for Settings API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.api.settings import create_settings_router


class TestSettingsRoutes:
    """Tests for settings routes"""

    @pytest.fixture
    def mock_volume_service(self):
        """Volume service mock"""
        service = Mock()
        service.reload_volume_limits = AsyncMock(return_value=True)
        service.reload_startup_config = AsyncMock(return_value=True)
        service.reload_volume_steps_config = AsyncMock(return_value=True)
        service.reload_steps_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.system_state = Mock()
        sm.system_state.active_source = Mock()
        sm.system_state.active_source.value = "none"
        sm.transition_to_source = AsyncMock(return_value=True)
        sm.get_current_state = Mock(return_value={"active_source": "none"})
        sm.get_source = Mock(return_value=None)
        sm.update_multiroom_state = AsyncMock()
        sm.update_equalizer_state = AsyncMock()
        sm.broadcast_event = AsyncMock()
        sm.reload_auto_disconnect_for_all_sources = AsyncMock(return_value=True)
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
        controller.current_source_state = "PLAYING"
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
        manager.restart = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def mock_routing_service(self):
        """Routing service mock"""
        service = Mock()
        service.set_multiroom_enabled = AsyncMock(return_value=True)
        service.set_equalizer_effects_enabled = AsyncMock(return_value=True)
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
        mock_volume_service,
        mock_state_machine,
        mock_screen_controller,
        mock_systemd_manager,
        mock_routing_service,
        mock_hardware_service
    ):
        """Fixture to create a TestClient with mocks"""
        app = FastAPI()

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value=None)
        mock_settings.set_setting = AsyncMock(return_value=True)
        mock_settings.load_settings = AsyncMock(return_value={})
        mock_settings._cache = None

        router = create_settings_router(
            volume_service=mock_volume_service,
            state_machine=mock_state_machine,
            screen_controller=mock_screen_controller,
            systemd_manager=mock_systemd_manager,
            routing_service=mock_routing_service,
            hardware_service=mock_hardware_service,
            settings_service=mock_settings
        )

        app.include_router(router, prefix="/api/settings")

        client = TestClient(app)
        client._mock_settings = mock_settings
        client._mock_state_machine = mock_state_machine
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
        """Test PUT /language with valid language (french)"""
        response = client.put("/api/settings/language", json={"language": "french"})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_language_valid_english(self, client):
        """Test PUT /language with valid language (english)"""
        response = client.put("/api/settings/language", json={"language": "english"})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_language_invalid(self, client):
        """Test PUT /language with invalid language - should return 422"""
        response = client.put("/api/settings/language", json={"language": "klingon"})
        assert response.status_code == 422

    def test_set_language_missing_field(self, client):
        """Test PUT /language without language field - should return 422"""
        response = client.put("/api/settings/language", json={})
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
        """Test PUT /volume-limits with valid values"""
        response = client.put("/api/settings/volume-limits", json={
            "min_db": -50.0,
            "max_db": -15.0
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_limits_invalid_range(self, client):
        """Test PUT /volume-limits with range < 6 dB - should return 422"""
        response = client.put("/api/settings/volume-limits", json={
            "min_db": -25.0,
            "max_db": -23.0
        })
        assert response.status_code == 422

    def test_set_volume_limits_min_greater_than_max(self, client):
        """Test PUT /volume-limits with min > max - should return 422"""
        response = client.put("/api/settings/volume-limits", json={
            "min_db": -15.0,
            "max_db": -50.0
        })
        assert response.status_code == 422

    def test_set_volume_limits_out_of_range(self, client):
        """Test PUT /volume-limits with out of range values - should return 422"""
        response = client.put("/api/settings/volume-limits", json={
            "min_db": -90.0,
            "max_db": 10.0
        })
        assert response.status_code == 422

    # Note: /volume-limits/toggle route does not exist - removed tests

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
        """Test PUT /volume-startup with valid values (in dB)"""
        response = client.put("/api/settings/volume-startup", json={
            "startup_volume_db": -30.0,
            "restore_last_volume": True
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_startup_out_of_range(self, client):
        """Test PUT /volume-startup with out of range volume - should return 422"""
        response = client.put("/api/settings/volume-startup", json={
            "startup_volume_db": 10.0,  # Above 0 dB max
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
        """Test PUT /volume-steps with valid value (in dB)"""
        response = client.put("/api/settings/volume-steps", json={
            "step_mobile_db": 3.0
        })
        assert response.status_code == 200

    def test_set_volume_steps_out_of_range(self, client):
        """Test PUT /volume-steps with out of range value - should return 422"""
        response = client.put("/api/settings/volume-steps", json={
            "step_mobile_db": 10.0  # Max is 6 dB
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
        """Test PUT /rotary-steps with valid value (in dB)"""
        response = client.put("/api/settings/rotary-steps", json={
            "step_rotary_db": 2.0
        })
        assert response.status_code == 200

    def test_set_rotary_steps_out_of_range(self, client):
        """Test PUT /rotary-steps with out of range value - should return 422"""
        response = client.put("/api/settings/rotary-steps", json={
            "step_rotary_db": 10.0  # Max is 6 dB
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
        """Test PUT /dock-apps with valid apps"""
        client._mock_settings.load_settings = AsyncMock(return_value={
            "dock": {"enabled_apps": ["spotify", "bluetooth"]}
        })
        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "bluetooth", "settings"]
        })
        assert response.status_code == 200

    def test_set_dock_apps_no_audio_source(self, client):
        """Test PUT /dock-apps without audio source - should return 422"""
        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["settings", "equalizer"]
        })
        assert response.status_code == 422

    def test_set_dock_apps_invalid_app(self, client):
        """Test PUT /dock-apps with invalid app - should return 422"""
        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "invalid_app"]
        })
        assert response.status_code == 422

    # ===================
    # AUDIO DISCONNECT TESTS (global)
    # ===================

    def test_get_audio_disconnect(self, client):
        """Test GET /audio-disconnect"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "auto_disconnect_delay": 10.0
        })
        response = client.get("/api/settings/audio-disconnect")
        assert response.status_code == 200
        assert response.json()["config"]["auto_disconnect_delay"] == 10.0

    def test_set_audio_disconnect_valid(self, client):
        """Test PUT /audio-disconnect with valid value"""
        response = client.put("/api/settings/audio-disconnect", json={
            "auto_disconnect_delay": 15.0
        })
        assert response.status_code == 200
        client._mock_state_machine.reload_auto_disconnect_for_all_sources.assert_awaited()

    def test_set_audio_disconnect_zero_disable(self, client):
        """Test PUT /audio-disconnect with 0 (disabled)"""
        response = client.put("/api/settings/audio-disconnect", json={
            "auto_disconnect_delay": 0.0
        })
        assert response.status_code == 200

    def test_set_audio_disconnect_negative(self, client):
        """Test PUT /audio-disconnect with negative value - should return 422"""
        response = client.put("/api/settings/audio-disconnect", json={
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
        """Test PUT /screen-timeout with valid value"""
        response = client.put("/api/settings/screen-timeout", json={
            "screen_timeout_enabled": True,
            "screen_timeout_seconds": 30
        })
        assert response.status_code == 200

    def test_set_screen_timeout_zero_disable(self, client):
        """Test PUT /screen-timeout with 0 (disabled)"""
        response = client.put("/api/settings/screen-timeout", json={
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
        """Test PUT /screen-brightness with valid value"""
        response = client.put("/api/settings/screen-brightness", json={
            "brightness_on": 7
        })
        assert response.status_code == 200

    def test_set_screen_brightness_out_of_range(self, client):
        """Test PUT /screen-brightness with out of range value - should return 422"""
        response = client.put("/api/settings/screen-brightness", json={
            "brightness_on": 15
        })
        assert response.status_code == 422

    # ===================
    # SCREEN SCREENSAVER TESTS
    # ===================

    def test_get_screen_screensaver(self, client):
        """Test GET /screen-screensaver"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "screensaver_enabled": True, "screensaver_delay_seconds": 15
        })
        response = client.get("/api/settings/screen-screensaver")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_set_screen_screensaver_valid(self, client):
        """Test PUT /screen-screensaver with valid values"""
        response = client.put("/api/settings/screen-screensaver", json={
            "screensaver_enabled": True,
            "screensaver_delay_seconds": 30
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_screen_screensaver_partial(self, client):
        """Test PUT /screen-screensaver with partial update (only enabled)"""
        response = client.put("/api/settings/screen-screensaver", json={
            "screensaver_enabled": False
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_screen_screensaver_delay_out_of_range(self, client):
        """Test PUT /screen-screensaver with delay < 5 - should return 422"""
        response = client.put("/api/settings/screen-screensaver", json={
            "screensaver_delay_seconds": 2
        })
        assert response.status_code == 422

    # ===================
    # PODCAST CREDENTIALS TESTS
    # ===================

    def test_set_podcast_credentials_valid(self, client):
        """Test PUT /podcast-credentials with valid credentials"""
        response = client.put("/api/settings/podcast-credentials", json={
            "taddy_user_id": "user123",
            "taddy_api_key": "key456"
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_podcast_credentials_missing_field(self, client):
        """Test PUT /podcast-credentials with missing field - should return 422"""
        response = client.put("/api/settings/podcast-credentials", json={
            "taddy_user_id": "user123"
        })
        assert response.status_code == 422

    # ===================
    # MAC ROC TESTS
    # ===================

    def test_get_mac_roc(self, client):
        """Test GET /mac-roc"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "target_latency_ms": 200, "latency_profile": "responsive", "frame_length_ms": 7
        })
        response = client.get("/api/settings/mac-roc")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_set_mac_roc_valid(self, client):
        """Test PUT /mac-roc with valid values"""
        response = client.put("/api/settings/mac-roc", json={
            "target_latency_ms": 100,
            "latency_profile": "responsive",
            "frame_length_ms": 7
        })
        assert response.status_code == 200

    def test_set_mac_roc_latency_out_of_range(self, client):
        """Test PUT /mac-roc with latency > 500 - should return 422"""
        response = client.put("/api/settings/mac-roc", json={
            "target_latency_ms": 1000,
            "latency_profile": "responsive",
            "frame_length_ms": 7
        })
        assert response.status_code == 422

    # ===================
    # RADIO SETTINGS TESTS
    # ===================

    def test_get_radio_settings(self, client):
        """Test GET /radio-settings"""
        client._mock_settings.get_setting = AsyncMock(return_value={
            "shazam_enabled": True
        })
        response = client.get("/api/settings/radio-settings")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_set_radio_settings_valid(self, client):
        """Test PUT /radio-settings with valid value"""
        response = client.put("/api/settings/radio-settings", json={
            "shazam_enabled": False
        })
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_radio_settings_missing_field(self, client):
        """Test PUT /radio-settings with missing field - should return 422"""
        response = client.put("/api/settings/radio-settings", json={})
        assert response.status_code == 422

    # ===================
    # INFO ENDPOINTS TESTS
    # ===================

    def test_get_hardware_info(self, client):
        """Test GET /hardware-info"""
        response = client.get("/api/settings/hardware-info")
        assert response.status_code == 200
        assert "hardware" in response.json()
