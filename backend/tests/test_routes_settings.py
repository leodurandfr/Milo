# backend/tests/test_routes_settings.py
"""
Unit tests for Settings API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from unittest.mock import Mock, AsyncMock, patch
from backend.api.models import HardwareConfigRequest
from backend.api.responses import BulkSettingsResponse
from backend.api.settings import create_settings_router
from backend.core.settings import SettingsService


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
        sm.broadcast = AsyncMock()
        sm.reload_auto_stop_for_all_sources = AsyncMock(return_value=True)
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
        controller.apply_screen_config = AsyncMock(return_value=True)
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
        return service

    @pytest.fixture
    def mock_hardware_service(self):
        """Hardware service mock"""
        service = Mock()
        service.get_screen_info = Mock(return_value={
            "type": "official",
            "resolution": {"width": 800, "height": 480}
        })
        service.get_full_config = Mock(return_value={
            "audio": {"id": "hifiberry_amp2"},
            "screen": {"type": "waveshare_7_usb", "resolution": {"width": 1024, "height": 600}},
            "rotary_encoder": {"enabled": True, "clk_pin": 22, "dt_pin": 27, "sw_pin": 23},
            "ir_remote": {"enabled": False, "gpio_pin": 17},
        })
        service.get_volume_control = Mock(return_value=False)
        return service

    @pytest.fixture
    def mock_multiroom_equalizer_service(self):
        """Multiroom equalizer service mock — owns the dock's master toggle."""
        service = Mock()
        service.set_local_equalizer_effects_enabled = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def client(
        self,
        mock_volume_service,
        mock_state_machine,
        mock_screen_controller,
        mock_systemd_manager,
        mock_routing_service,
        mock_hardware_service,
        mock_multiroom_equalizer_service
    ):
        """Fixture to create a TestClient with mocks"""
        app = FastAPI()

        # The real service guarantees every declared section, keys included, so
        # the mock answers from the same declaration: a route that reads
        # screen['screensaver_enabled'] must be testable without a fallback that
        # cannot happen in production.
        defaults = SettingsService().defaults

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(side_effect=lambda key: defaults.get(key))
        mock_settings.set_setting = AsyncMock(return_value=True)
        mock_settings.set_settings = AsyncMock(return_value=True)
        mock_settings.load_settings = AsyncMock(return_value={})
        mock_settings._cache = None

        router = create_settings_router(
            volume_service=mock_volume_service,
            state_machine=mock_state_machine,
            screen_controller=mock_screen_controller,
            systemd_manager=mock_systemd_manager,
            routing_service=mock_routing_service,
            hardware_service=mock_hardware_service,
            settings_service=mock_settings,
            multiroom_equalizer_service=mock_multiroom_equalizer_service
        )

        app.include_router(router, prefix="/api/settings")

        client = TestClient(app)
        client._mock_settings = mock_settings
        client._mock_state_machine = mock_state_machine
        client._mock_multiroom_equalizer_service = mock_multiroom_equalizer_service
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

    def test_set_dock_apps_persists_the_effects_that_did_run(self, client, mock_routing_service):
        """A batch that dies halfway must leave the tile list describing what ran.

        The whole list used to be written after the loop, so a failure on the
        second operation kept a tile for the function the first one had just
        switched off — a dock advertising something already gone, and the
        consumers never told.
        """
        client._mock_settings.load_settings = AsyncMock(return_value={
            "dock": {"enabled_apps": ["spotify", "multiroom"]}
        })
        # Disables run first: multiroom goes, then the equalizer enable fails.
        client._mock_multiroom_equalizer_service.set_local_equalizer_effects_enabled = AsyncMock(
            return_value=False
        )

        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "equalizer"]
        })

        assert response.status_code == 500
        mock_routing_service.set_multiroom_enabled.assert_awaited_once()

        dock_writes = [
            c.args[1] for c in client._mock_settings.set_setting.call_args_list
            if c.args[0] == "dock.enabled_apps"
        ]
        assert dock_writes == [["spotify"]], "the applied disable was not persisted on its own"

        broadcasts = [c.args[0] for c in client._mock_state_machine.broadcast.call_args_list]
        assert [b.config.enabled_apps for b in broadcasts] == [["spotify"]]

    def test_set_dock_apps_reports_a_failed_multiroom_transition(self, client, mock_routing_service):
        """A 200 on a transition that did not happen leaves the UI permanently wrong.

        Swallowing a False persisted "multiroom on" against an appliance still in
        direct mode — a disagreement no later action reconciles, and one the user
        has no way to see. Nothing ran before the failure here, so nothing at all
        may be written (the partial case is the test above).
        """
        client._mock_settings.load_settings = AsyncMock(return_value={
            "dock": {"enabled_apps": ["spotify"]}
        })
        mock_routing_service.set_multiroom_enabled = AsyncMock(return_value=False)

        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "multiroom"]
        })

        assert response.status_code == 500
        written = [c.args[0] for c in client._mock_settings.set_setting.call_args_list]
        assert "dock.enabled_apps" not in written

    def test_set_dock_apps_reports_a_failed_equalizer_toggle(self, client):
        """Same contract on the equalizer branch, which has its own service call —
        again with the failure as the first operation, so nothing is written."""
        client._mock_settings.load_settings = AsyncMock(return_value={
            "dock": {"enabled_apps": ["spotify", "equalizer"]}
        })
        client._mock_multiroom_equalizer_service.set_local_equalizer_effects_enabled = AsyncMock(
            return_value=False
        )

        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify"]
        })

        assert response.status_code == 500
        written = [c.args[0] for c in client._mock_settings.set_setting.call_args_list]
        assert "dock.enabled_apps" not in written

    def test_set_dock_apps_invalid_app(self, client):
        """Test PUT /dock-apps with invalid app - should return 422"""
        response = client.put("/api/settings/dock-apps", json={
            "enabled_apps": ["spotify", "invalid_app"]
        })
        assert response.status_code == 422

    # ===================
    # AUDIO STOP TESTS (global)
    # ===================

    def test_set_audio_stop_valid(self, client):
        """Test PUT /audio-stop with valid value"""
        response = client.put("/api/settings/audio-stop", json={
            "auto_stop_delay": 15.0
        })
        assert response.status_code == 200
        client._mock_state_machine.reload_auto_stop_for_all_sources.assert_awaited()

    def test_set_audio_stop_zero_disable(self, client):
        """Test PUT /audio-stop with 0 (disabled)"""
        response = client.put("/api/settings/audio-stop", json={
            "auto_stop_delay": 0.0
        })
        assert response.status_code == 200

    def test_set_audio_stop_negative(self, client):
        """Test PUT /audio-stop with negative value - should return 422"""
        response = client.put("/api/settings/audio-stop", json={
            "auto_stop_delay": -5.0
        })
        assert response.status_code == 422

    # ===================
    # SCREEN TESTS
    # ===================

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

    def test_apply_brightness_reports_a_panel_that_refused(self, client, mock_screen_controller):
        """The route used to manufacture its own success: it awaited the apply
        and answered "brightness_applied" whatever the backlight had done."""
        mock_screen_controller.apply_screen_config = AsyncMock(return_value=False)

        response = client.post("/api/settings/screen-brightness/apply", json={
            "brightness_on": 7
        })

        assert response.status_code == 502

    def test_apply_brightness_success(self, client):
        response = client.post("/api/settings/screen-brightness/apply", json={
            "brightness_on": 7
        })
        assert response.status_code == 200
        assert response.json()["brightness_applied"] == 7

    # ===================
    # SCREEN SCREENSAVER TESTS
    # ===================

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
    # MAC ROC TESTS
    # ===================

    def test_set_mac_roc_valid(self, client):
        """Test PUT /mac-roc with valid values"""
        # MacEnv.regenerate() writes mac.env to /var/lib/milo — mock it for hermeticity
        with patch("backend.api.settings.MacEnv.regenerate"):
            response = client.put("/api/settings/mac-roc", json={
                "target_latency_ms": 100,
                "latency_profile": "responsive",
                "frame_length_ms": 6
            })
        assert response.status_code == 200

    def test_set_mac_roc_latency_out_of_range(self, client):
        """Test PUT /mac-roc with latency > 500 - should return 422"""
        response = client.put("/api/settings/mac-roc", json={
            "target_latency_ms": 1000,
            "latency_profile": "responsive",
            "frame_length_ms": 6
        })
        assert response.status_code == 422

    # ===================
    # RADIO SETTINGS TESTS
    # ===================

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

    # ===================
    # MAC ROC EFFECTS
    # ===================

    def test_set_mac_roc_applies_the_four_effects_it_documents(self, client, mock_systemd_manager):
        """PUT /mac-roc's whole body is unobserved by a status-code assertion.

        The route persists the three ROC values, regenerates mac.env, restarts
        the ROC receiver and broadcasts the new config; emptying it entirely
        leaves FastAPI answering 200 with a null body, which
        `test_set_mac_roc_valid` accepts. Each effect is the only thing that
        makes the sender's latency setting reach the sender at all.
        """
        with patch("backend.api.settings.MacEnv.regenerate", new=AsyncMock()) as regenerate:
            response = client.put("/api/settings/mac-roc", json={
                "target_latency_ms": 100,
                "latency_profile": "responsive",
                "frame_length_ms": 6,
            })

        expected = {
            "target_latency_ms": 100,
            "latency_profile": "responsive",
            "frame_length_ms": 6,
        }
        assert response.json() == {
            "status": "success", "config": expected, "service_restarted": True
        }
        client._mock_settings.set_setting.assert_awaited_once_with("mac", expected)
        regenerate.assert_awaited_once_with(expected)
        mock_systemd_manager.restart.assert_awaited_once_with("milo-mac.service")

        broadcast = client._mock_state_machine.broadcast.call_args.args[0]
        assert broadcast.TYPE == "mac_roc_changed"
        assert broadcast.config.model_dump() == expected

    def test_set_mac_roc_refuses_when_the_save_fails(self, client):
        """A 200 on a save that did not happen leaves the Mac panel showing a
        latency the daemon was never given, and no later action reconciles it.
        Nothing after the save may run either — mac.env must not describe a
        configuration settings.json does not hold."""
        client._mock_settings.set_setting = AsyncMock(return_value=False)

        with patch("backend.api.settings.MacEnv.regenerate", new=AsyncMock()) as regenerate:
            response = client.put("/api/settings/mac-roc", json={
                "target_latency_ms": 100,
                "latency_profile": "responsive",
                "frame_length_ms": 6,
            })

        assert response.status_code == 500
        regenerate.assert_not_awaited()

    def test_set_mac_roc_reports_a_receiver_that_did_not_restart(self, client, mock_systemd_manager):
        """The settings ARE saved and mac.env IS written when the restart fails,
        so this is a 200 — but `service_restarted` is the only thing telling the
        panel the running daemon is still on the old frame length."""
        mock_systemd_manager.restart = AsyncMock(return_value=False)

        with patch("backend.api.settings.MacEnv.regenerate", new=AsyncMock()):
            response = client.put("/api/settings/mac-roc", json={
                "target_latency_ms": 100,
                "latency_profile": "responsive",
                "frame_length_ms": 6,
            })

        assert response.status_code == 200
        assert response.json()["service_restarted"] is False
        client._mock_state_machine.broadcast.assert_awaited_once()

    # ===================
    # SCREEN ACTIVITY (the loopback guard)
    # ===================

    def test_screen_activity_from_the_kiosk_wakes_the_panel(self, client, mock_screen_controller):
        """The Pi's own kiosk loads http://localhost, so nginx forwards
        X-Real-IP: 127.0.0.1. That is the only case that may touch the screen."""
        response = client.post("/api/settings/screen-activity", headers={"X-Real-IP": "127.0.0.1"})

        assert response.json() == {"status": "success", "activity_time_reset": True}
        mock_screen_controller.on_touch_detected.assert_awaited_once()

    def test_screen_activity_from_ipv6_loopback_wakes_the_panel(self, client, mock_screen_controller):
        """`::1` is the same kiosk reached over IPv6; leaving it out would make
        waking the panel depend on which family the loopback resolved to."""
        response = client.post("/api/settings/screen-activity", headers={"X-Real-IP": "::1"})

        assert response.json()["activity_time_reset"] is True
        mock_screen_controller.on_touch_detected.assert_awaited_once()

    def test_screen_activity_from_a_remote_browser_is_acknowledged_and_ignored(
        self, client, mock_screen_controller
    ):
        """The same frontend runs on milo.local from a phone. Without this guard
        every remote tap lights the panel in the room — at night, with nobody
        there, and with no trace anywhere that a remote client caused it."""
        response = client.post("/api/settings/screen-activity", headers={"X-Real-IP": "192.168.1.42"})

        assert response.json() == {"status": "success", "activity_time_reset": False}
        mock_screen_controller.on_touch_detected.assert_not_awaited()

    def test_screen_activity_trusts_the_header_over_the_socket(self, client, mock_screen_controller):
        """nginx sets X-Real-IP authoritatively and every request reaches the
        backend over loopback, so the socket address says nothing. Reading the
        peer instead of the header would wake the panel for every remote tap."""
        response = client.post("/api/settings/screen-activity", headers={"X-Real-IP": "10.0.0.7"})

        assert response.json()["activity_time_reset"] is False
        mock_screen_controller.on_touch_detected.assert_not_awaited()

    # ===================
    # THE SHARED SETTINGS SPINE (_handle_setting_update)
    # ===================

    def test_a_setting_that_could_not_be_saved_is_a_500(self, client):
        """One spine serves sixteen settings routes. A save that returns False
        answering 200 would leave every settings panel showing a value
        settings.json does not hold."""
        client._mock_settings.set_setting = AsyncMock(return_value=False)

        response = client.put("/api/settings/language", json={"language": "french"})

        assert response.status_code == 500
        client._mock_state_machine.broadcast.assert_not_awaited()

    def test_a_reload_that_raises_is_reported_not_swallowed(self, client, mock_volume_service):
        """The value IS stored when the reload fails, so the write stands and the
        event goes out — `reload_success` in the HTTP response is the only channel
        that tells the panel the running service is still on the old limits.
        useSettingsAPI reads it there and nowhere else."""
        mock_volume_service.reload_volume_limits = AsyncMock(side_effect=RuntimeError("nope"))

        response = client.put("/api/settings/volume-limits", json={"min_db": -60.0, "max_db": -20.0})

        assert response.status_code == 200
        assert response.json()["reload_success"] is False
        client._mock_state_machine.broadcast.assert_awaited_once()

    # ===================
    # THE PARTIAL-UPDATE TWIN: SCREEN COLOR FILTER
    # ===================

    def test_set_screen_color_filter_writes_both_keys(self, client):
        """Same shape as /screen-screensaver, and the only route pair in this
        file where the setter builds its own update dict — a key spelled wrong
        here writes nothing and still answers 200."""
        response = client.put("/api/settings/screen-color-filter", json={
            "enabled": True, "warmth": 72
        })

        assert response.status_code == 200
        client._mock_settings.set_settings.assert_awaited_once_with({
            "screen.color_filter_enabled": True,
            "screen.color_filter_warmth": 72,
        })
        assert response.json()["config"] == {"enabled": True, "warmth": 72}

    def test_set_screen_color_filter_leaves_the_omitted_half_alone(self, client):
        """A partial payload must not write the key it does not carry, and the
        broadcast must still describe the whole filter — the stored value for the
        half that was not sent, not a null the UI would render as off."""
        response = client.put("/api/settings/screen-color-filter", json={"warmth": 72})

        client._mock_settings.set_settings.assert_awaited_once_with(
            {"screen.color_filter_warmth": 72}
        )
        stored = SettingsService().defaults["screen"]
        assert response.json()["config"] == {
            "enabled": stored["color_filter_enabled"], "warmth": 72
        }

    # ===================
    # THE REMAINING WRITE ROUTES
    # ===================

    def test_set_bt_remote_steps_writes_the_bt_key(self, client):
        """Four step routes share one spine and differ only by the key they
        write; a copy-paste between them is silent and moves the wrong remote."""
        response = client.put("/api/settings/bt-remote-steps", json={"step_bt_remote_db": 3.0})

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with("volume.step_bt_remote_db", 3.0)
        assert client._mock_state_machine.broadcast.call_args.args[0].TYPE == "bt_remote_steps_changed"

    def test_set_ir_remote_steps_writes_the_ir_key(self, client):
        """The IR half of the pair above."""
        response = client.put("/api/settings/ir-remote-steps", json={"step_ir_remote_db": 3.0})

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with("volume.step_ir_remote_db", 3.0)
        assert client._mock_state_machine.broadcast.call_args.args[0].TYPE == "ir_remote_steps_changed"

    def test_set_screen_ui_scale_writes_the_scale(self, client):
        """The kiosk re-renders on this event; a wrong key leaves the panel at
        the old scale with the slider showing the new one."""
        response = client.put("/api/settings/screen-ui-scale", json={"ui_scale": 1.15})

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with("screen.ui_scale", 1.15)
        assert response.json()["config"] == {"ui_scale": 1.15}

    def test_set_music_library_settings_writes_the_section(self, client):
        """Decides whether the library shows one tab per storage space or one
        merged catalog — a stored value the browse view reads on every open."""
        response = client.put("/api/settings/music-library-settings", json={
            "separate_storages": False
        })

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with(
            "music_library", {"separate_storages": False}
        )
        assert response.json()["config"] == {"separate_storages": False}

    def test_set_qobuz_settings_hands_the_flag_to_the_running_source(self, client, mock_state_machine):
        """The flag decides whether qobuz-proxy keeps unity gain (CamillaDSP owns
        volume) or follows the Qobuz app's slider. Storing it without telling the
        running source leaves the two disagreeing until the next restart."""
        source = Mock()
        source.on_allow_app_volume_changed = AsyncMock(return_value=True)
        mock_state_machine.get_source = Mock(return_value=source)

        response = client.put("/api/settings/qobuz-settings", json={"allow_app_volume": True})

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with(
            "qobuz", {"allow_app_volume": True}
        )
        source.on_allow_app_volume_changed.assert_awaited_once_with(True)
        assert response.json()["reload_success"] is True

    def test_set_qobuz_settings_survives_a_source_that_refuses(self, client, mock_state_machine):
        """The write stands and the event goes out; only `reload_success` says
        the running proxy did not take it."""
        source = Mock()
        source.on_allow_app_volume_changed = AsyncMock(side_effect=RuntimeError("no proxy"))
        mock_state_machine.get_source = Mock(return_value=source)

        response = client.put("/api/settings/qobuz-settings", json={"allow_app_volume": True})

        assert response.status_code == 200
        assert response.json()["reload_success"] is False
        client._mock_state_machine.broadcast.assert_awaited_once()

    def test_set_spotify_settings_passes_apply_now_not_the_duration(self, client, mock_state_machine):
        """`apply_now` is request-only: True restarts go-librespot so the new
        crossfade takes effect at once, False just stores it. Handing the source
        the duration instead would restart on every save."""
        source = Mock()
        source.on_spotify_settings_changed = AsyncMock(return_value=True)
        mock_state_machine.get_source = Mock(return_value=source)

        response = client.put("/api/settings/spotify-settings", json={
            "crossfade_duration": 6000, "apply_now": True
        })

        assert response.status_code == 200
        client._mock_settings.set_setting.assert_awaited_once_with(
            "spotify", {"crossfade_duration": 6000}
        )
        source.on_spotify_settings_changed.assert_awaited_once_with(True)
        assert response.json()["config"] == {"crossfade_duration": 6000}

    # ===================
    # HARDWARE CONFIG (read side)
    # ===================

    def test_get_hardware_config_resolves_the_volume_control_it_was_not_given(self, client):
        """`volume_control` decides whether CamillaDSP attenuates or the card's
        own mixer does. A stored config from before the key existed carries no
        such key, and the Hardware page would render the checkbox as off — the
        DAC-attenuating state the appliance is built to avoid."""
        body = client.get("/api/settings/hardware-config").json()

        assert body["status"] == "success"
        assert body["current"]["audio"]["volume_control"] is False

    def test_hardware_config_offers_no_pin_its_own_validator_rejects(self, client):
        """The dropdown's pins and the request model's bounds are two spellings
        of one list. When they drift the page offers a pin that comes back 422
        with no field to blame — the reason SELECTABLE_GPIO_PINS exists."""
        from backend.hardware.registry import AUDIO_CARDS, SCREENS

        options = client.get("/api/settings/hardware-config").json()["options"]
        pins = [entry["value"] for entry in options["gpio_pins"]]

        assert len(pins) >= 10, f"only {len(pins)} pins offered — the route is not building them"
        assert {entry["value"] for entry in options["audio_cards"]} == set(AUDIO_CARDS)
        assert {entry["value"] for entry in options["screens"]} == set(SCREENS)

        for pin in pins:
            spares = [p for p in pins if p != pin][:3]
            HardwareConfigRequest(**{
                "audio": {"id": "hifiberry_amp2"},
                "screen": {"type": "waveshare_7_usb"},
                "rotary_encoder": {
                    "enabled": True,
                    "clk_pin": spares[0], "dt_pin": spares[1], "sw_pin": spares[2],
                },
                "ir_remote": {"enabled": True, "gpio_pin": pin},
            })

class TestHardwareConfigRequest:
    """PUT /hardware-config's request model — checked without the route, which
    applies the config and reboots the unit."""

    @staticmethod
    def _payload(ir_gpio_pin, ir_enabled=True):
        """A config valid in every respect but the IR data line."""
        return {
            "audio": {"id": "hifiberry_amp2"},
            "screen": {"type": "waveshare_7_usb"},
            "rotary_encoder": {"enabled": True, "clk_pin": 22, "dt_pin": 27, "sw_pin": 23},
            "ir_remote": {"enabled": ir_enabled, "gpio_pin": ir_gpio_pin},
        }

    def test_a_free_pin_is_accepted(self):
        """The negative case below has to fail for its own reason, not because
        the rest of the payload was invalid."""
        assert HardwareConfigRequest(**self._payload(17)).ir_remote.gpio_pin == 17

    def test_the_ir_line_cannot_reuse_a_rotary_pin(self):
        """Two peripherals, one GPIO header, one form. Nothing else compares
        them, and the route reboots — so an accepted collision comes back as a
        remote (or an encoder) that simply does not respond."""
        with pytest.raises(ValidationError):
            HardwareConfigRequest(**self._payload(22))

    def test_a_disabled_ir_remote_frees_its_pin(self):
        """The stored pin of a disabled remote drives nothing; rejecting it
        would block a legitimate encoder config."""
        assert HardwareConfigRequest(**self._payload(22, ir_enabled=False))


class TestBulkSettings:
    """`GET /api/settings/bulk` driven against the real SettingsService document.

    Why it exists: four rules already surround this route and not one of them
    runs it. The two in tests/architecture/test_settings_defaults.py read its
    AST, the frontend's settingsBulkContract.test.js reads api/responses.py as
    text, and tests/contracts/test_response_models.py feeds
    `BulkSettingsResponse` a hand-written dict. So the failure the route's design
    deliberately chooses — no fallback anywhere, because a default restated here
    could only ever disagree with `SettingsService.defaults` — was the one
    nothing could see: a key the validator stops emitting raises inside the
    handler and answers 500, and this is the single read that fills every
    settings panel and the only settings route Milo-Mac calls.

    The stored document comes from the production validator, never a fixture, so
    a section renamed in `SettingsService.defaults` surfaces here.
    """

    # Wire path → stored path. The route's own content is a projection, so the
    # pairing IS the contract.
    PROJECTION = {
        ("language",): ("language",),
        ("volume_limits", "min_db"): ("volume", "limit_min_db"),
        ("volume_limits", "max_db"): ("volume", "limit_max_db"),
        ("volume_startup", "startup_volume_db"): ("volume", "startup_volume_db"),
        ("volume_startup", "restore_last_volume"): ("volume", "restore_last_volume"),
        ("rotary_steps", "step_rotary_db"): ("volume", "step_rotary_db"),
        ("bt_remote_steps", "step_bt_remote_db"): ("volume", "step_bt_remote_db"),
        ("ir_remote_steps", "step_ir_remote_db"): ("volume", "step_ir_remote_db"),
        ("dock_apps", "enabled_apps"): ("dock", "enabled_apps"),
        ("audio_stop", "auto_stop_delay"): ("audio", "auto_stop_delay"),
        ("screen_timeout", "screen_timeout_seconds"): ("screen", "timeout_seconds"),
        ("screen_brightness", "brightness_on"): ("screen", "brightness_on"),
        ("screen_ui_scale", "ui_scale"): ("screen", "ui_scale"),
        ("screen_screensaver", "screensaver_enabled"): ("screen", "screensaver_enabled"),
        ("screen_screensaver", "screensaver_delay_seconds"): ("screen", "screensaver_delay_seconds"),
        ("screen_color_filter", "enabled"): ("screen", "color_filter_enabled"),
        ("screen_color_filter", "warmth"): ("screen", "color_filter_warmth"),
        ("radio_settings", "shazam_enabled"): ("radio", "shazam_enabled"),
        ("music_library_settings", "separate_storages"): ("music_library", "separate_storages"),
        ("qobuz_settings", "allow_app_volume"): ("qobuz", "allow_app_volume"),
        ("spotify_settings", "crossfade_duration"): ("spotify", "crossfade_duration"),
        ("mac_roc", "target_latency_ms"): ("mac", "target_latency_ms"),
        ("mac_roc", "latency_profile"): ("mac", "latency_profile"),
        ("mac_roc", "frame_length_ms"): ("mac", "frame_length_ms"),
    }

    @staticmethod
    def _stored():
        """The document `SettingsService` guarantees for a unit with no file yet."""
        return SettingsService()._validate_and_merge({})

    @staticmethod
    def _client(document):
        """The real router over a settings service that holds `document`."""
        settings = Mock()
        settings.get_all_settings = AsyncMock(return_value=document)
        settings.get_setting = AsyncMock(side_effect=lambda key: document.get(key))

        app = FastAPI()
        app.include_router(
            create_settings_router(
                volume_service=Mock(),
                state_machine=Mock(),
                screen_controller=Mock(),
                systemd_manager=Mock(),
                routing_service=Mock(),
                hardware_service=Mock(),
                settings_service=settings,
                multiroom_equalizer_service=Mock(),
            ),
            prefix="/api/settings",
        )
        return TestClient(app)

    def test_bulk_serves_every_category_the_response_model_declares(self):
        """The key set is read off `BulkSettingsResponse`, which is what FastAPI
        enforces on the way out and what the frontend store destructures. A
        category the route stops building comes back as a 500, not a partial
        payload — response_model declares every field required."""
        response = self._client(self._stored()).get("/api/settings/bulk")

        assert response.status_code == 200, response.text
        declared = set(BulkSettingsResponse.model_fields)
        assert len(declared) >= 15, f"{len(declared)} fields declared — the model is not the bulk one"
        assert set(response.json()) == declared

    # A different value of the same type for each leaf that needs one spelled out.
    NUDGES = {
        ("language",): "french",
        ("mac", "latency_profile"): "gradual",
        ("mac", "frame_length_ms"): 6,
        ("dock", "enabled_apps"): ["radio"],
    }

    @classmethod
    def _nudge(cls, path, value):
        if path in cls.NUDGES:
            return cls.NUDGES[path]
        if isinstance(value, bool):
            return not value
        if isinstance(value, float):
            return value + 1.0
        if isinstance(value, int):
            return value + 1
        raise AssertionError(f"no nudge for {path}: {value!r} — extend NUDGES")

    @staticmethod
    def _leaves(body):
        """Every wire path of the bulk payload, minus the envelope."""
        out = {}
        for key, value in body.items():
            if key == "status":
                continue
            if isinstance(value, dict):
                out.update({(key, k): v for k, v in value.items()})
            else:
                out[(key,)] = value
        return out

    @classmethod
    def _read(cls, tree, path):
        for key in path:
            tree = tree[key]
        return tree

    @classmethod
    def _write(cls, tree, path, value):
        for key in path[:-1]:
            tree = tree[key]
        tree[path[-1]] = value

    def test_each_stored_leaf_moves_exactly_the_wire_leaf_it_feeds(self):
        """One stored value changed at a time, against the whole payload.

        Asserting `body[group][key] == stored[section][stored_key]` cannot see a
        swap between two leaves that hold the same value — and the four
        `step_*_db` all default to 2.0, `screensaver_enabled` and
        `shazam_enabled` are both True. Moving one leaf and requiring exactly one
        wire leaf to follow discriminates them: a wire key fed from the wrong
        stored key moves twice for one edit and not at all for the other.
        """
        assert len(self.PROJECTION) >= 20, "the projection table is not the bulk one"
        baseline = self._leaves(self._client(self._stored()).get("/api/settings/bulk").json())
        assert len(baseline) >= 23, f"{len(baseline)} wire leaves — the route built a shell"

        for wire_path, stored_path in self.PROJECTION.items():
            stored = self._stored()
            before = self._read(stored, stored_path)
            self._write(stored, stored_path, self._nudge(stored_path, before))
            assert self._read(stored, stored_path) != before, f"{stored_path} nudge did nothing"

            after = self._leaves(self._client(stored).get("/api/settings/bulk").json())
            moved = {p for p, v in after.items() if baseline[p] != v}
            assert moved == {wire_path}, f"{'.'.join(stored_path)} moved {sorted(moved)}"

    def test_bulk_derives_the_timeout_switch_from_the_stored_seconds(self):
        """`screen_timeout_enabled` is the one field with no stored twin: the
        route computes it from the seconds, 0 meaning never. Storing a switch of
        its own is what would let the two disagree."""
        stored = self._stored()
        stored["screen"]["timeout_seconds"] = 0
        assert self._client(stored).get("/api/settings/bulk").json()["screen_timeout"] == {
            "screen_timeout_enabled": False, "screen_timeout_seconds": 0,
        }

        stored["screen"]["timeout_seconds"] = 30
        assert self._client(stored).get("/api/settings/bulk").json()["screen_timeout"] == {
            "screen_timeout_enabled": True, "screen_timeout_seconds": 30,
        }

    def test_bulk_fails_loud_on_a_section_the_validator_stopped_emitting(self):
        """The designed behaviour, and the reason no fallback may be added here:
        a missing section must reach the operator as an error, not as a default
        rendered like a stored value. `test_bulk_route_restates_no_default` keeps
        the fallbacks out; this keeps the consequence honest."""
        stored = self._stored()
        del stored["mac"]

        with pytest.raises(KeyError):
            self._client(stored).get("/api/settings/bulk")
