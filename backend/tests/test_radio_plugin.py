# backend/tests/test_radio_plugin.py
"""
Unit tests for the RadioPlugin
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.plugins.radio.plugin import RadioPlugin
from backend.domain.audio_state import PluginState, AudioSource


class TestRadioPlugin:
    """Tests for the Radio plugin"""

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.update_plugin_state = AsyncMock()
        sm.system_state = Mock()
        sm.system_state.active_source = AudioSource.RADIO
        sm.system_state.plugin_state = PluginState.READY
        sm.system_state.metadata = {}
        return sm

    @pytest.fixture
    def mock_settings_service(self):
        """Settings service mock"""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def plugin_config(self):
        """Plugin configuration"""
        return {
            'service_name': 'milo-radio.service',
            'ipc_socket': '/tmp/milo-radio-ipc.sock'
        }

    @pytest.fixture
    def plugin(self, plugin_config, mock_state_machine, mock_settings_service):
        """Fixture to create a Radio plugin"""
        with patch('backend.infrastructure.plugins.radio.plugin.RadioDataService'), \
             patch('backend.infrastructure.plugins.radio.plugin.MpvController') as mock_mpv, \
             patch('backend.infrastructure.plugins.radio.plugin.StationManager') as mock_sm, \
             patch('backend.infrastructure.plugins.radio.plugin.RadioBrowserAPI') as mock_api:

            # Mock MpvController
            mpv_instance = Mock()
            mpv_instance.connect = AsyncMock(return_value=True)
            mpv_instance.disconnect = AsyncMock()
            mpv_instance.is_connected = True
            mpv_instance.stop = AsyncMock(return_value=True)
            mpv_instance.load_stream = AsyncMock(return_value=True)
            mpv_instance.is_playing = AsyncMock(return_value=False)
            mpv_instance.get_status = AsyncMock(return_value={"connected": True})
            mpv_instance.get_property = AsyncMock(return_value=None)
            mock_mpv.return_value = mpv_instance

            # Mock StationManager
            sm_instance = Mock()
            sm_instance.initialize = AsyncMock()
            sm_instance.is_favorite = Mock(return_value=False)
            sm_instance.add_favorite = AsyncMock(return_value=True)
            sm_instance.remove_favorite = AsyncMock(return_value=True)
            sm_instance.mark_as_broken = AsyncMock(return_value=True)
            sm_instance.reset_broken_stations = AsyncMock(return_value=True)
            sm_instance.get_stats = Mock(return_value={'favorites_count': 0, 'broken_stations_count': 0})
            mock_sm.return_value = sm_instance

            # Mock RadioBrowserAPI
            api_instance = Mock()
            api_instance.get_station_by_id = AsyncMock(return_value={
                'id': 'test123',
                'name': 'Test Radio',
                'url': 'http://stream.test.com/radio.mp3',
                'country': 'France',
                'genre': 'Pop',
                'favicon': 'http://test.com/icon.png',
                'bitrate': 128,
                'codec': 'mp3'
            })
            api_instance.close = AsyncMock()
            api_instance.increment_station_clicks = AsyncMock()
            mock_api.return_value = api_instance

            plugin = RadioPlugin(
                config=plugin_config,
                state_machine=mock_state_machine,
                settings_service=mock_settings_service
            )

            # Override the created instances with our mocks
            plugin.mpv = mpv_instance
            plugin.station_manager = sm_instance
            plugin.radio_api = api_instance

            # Mock service_manager
            plugin.service_manager = Mock()
            plugin.service_manager.is_active = AsyncMock(return_value=True)
            plugin.service_manager.get_status = AsyncMock(return_value={"active": True})

            # Mock control_service
            plugin.control_service = AsyncMock(return_value=True)

            # Mock notify_state_change
            plugin.notify_state_change = AsyncMock()

            # Mock _monitor_playback to avoid infinite loops
            async def mock_monitor():
                pass
            plugin._monitor_playback = mock_monitor

            return plugin

    # ===================
    # INITIALIZATION TESTS
    # ===================

    def test_initialization_config(self, plugin_config, mock_state_machine, mock_settings_service):
        """Test basic plugin initialization"""
        with patch('backend.infrastructure.plugins.radio.plugin.RadioDataService'), \
             patch('backend.infrastructure.plugins.radio.plugin.MpvController'), \
             patch('backend.infrastructure.plugins.radio.plugin.StationManager'), \
             patch('backend.infrastructure.plugins.radio.plugin.RadioBrowserAPI'):

            plugin = RadioPlugin(
                config=plugin_config,
                state_machine=mock_state_machine,
                settings_service=mock_settings_service
            )

            assert plugin.name == "radio"
            assert plugin.service_name == "milo-radio.service"
            assert plugin.current_station is None
            assert plugin._is_playing is False
            assert plugin._is_buffering is False

    @pytest.mark.asyncio
    async def test_do_initialize_success(self, plugin):
        """Test successful initialization"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'milo-radio.service enabled', b''))
            mock_process.wait = AsyncMock()
            mock_exec.return_value = mock_process

            result = await plugin._do_initialize()

            assert result is True
            plugin.station_manager.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_initialize_service_not_found(self, plugin):
        """Test initialization with service not found"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'not found'))
            mock_exec.return_value = mock_process

            result = await plugin._do_initialize()

            assert result is False

    # ===================
    # START TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_do_start_success(self, plugin):
        """Test successful startup"""
        result = await plugin._do_start()

        assert result is True
        plugin.control_service.assert_called_with(plugin.service_name, "start")
        plugin.mpv.connect.assert_called_once()
        plugin.notify_state_change.assert_called()

    @pytest.mark.asyncio
    async def test_do_start_service_failure(self, plugin):
        """Test startup with service failure"""
        plugin.control_service = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    @pytest.mark.asyncio
    async def test_do_start_mpv_connection_failure(self, plugin):
        """Test startup with mpv connection failure"""
        plugin.mpv.connect = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    # ===================
    # STOP TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_stop_success(self, plugin):
        """Test successful stop"""
        plugin.current_station = {"id": "test", "name": "Test Radio"}
        plugin._is_playing = True

        result = await plugin.stop()

        assert result is True
        assert plugin.current_station is None
        assert plugin._is_playing is False
        plugin.mpv.stop.assert_called_once()
        plugin.mpv.disconnect.assert_called_once()
        plugin.notify_state_change.assert_called_with(PluginState.INACTIVE)

    @pytest.mark.asyncio
    async def test_stop_with_monitor_task(self, plugin):
        """Test stop with active monitoring task"""
        async def dummy_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        plugin._monitor_task = asyncio.create_task(dummy_task())

        result = await plugin.stop()

        assert result is True

    # ===================
    # COMMAND HANDLING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_handle_command_play_station(self, plugin):
        """Test play_station command"""
        result = await plugin.handle_command("play_station", {"station_id": "test123"})

        assert result["success"] is True
        plugin.mpv.load_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_play_station_no_id(self, plugin):
        """Test play_station command without station_id"""
        result = await plugin.handle_command("play_station", {})

        assert result["success"] is False
        assert "station_id required" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_play_station_not_found(self, plugin):
        """Test play_station command with station not found"""
        plugin.radio_api.get_station_by_id = AsyncMock(return_value=None)

        result = await plugin.handle_command("play_station", {"station_id": "unknown"})

        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_play_station_stream_failure(self, plugin):
        """Test play_station command with stream failure"""
        plugin.mpv.load_stream = AsyncMock(return_value=False)

        result = await plugin.handle_command("play_station", {"station_id": "test123"})

        assert result["success"] is False
        plugin.station_manager.mark_as_broken.assert_called_once_with("test123")

    @pytest.mark.asyncio
    async def test_handle_command_stop_playback(self, plugin):
        """Test stop_playback command"""
        plugin.current_station = {"id": "test", "name": "Test Radio"}
        plugin._is_playing = True

        result = await plugin.handle_command("stop_playback", {})

        assert result["success"] is True
        assert plugin.current_station is None
        assert plugin._is_playing is False
        plugin.mpv.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_add_favorite(self, plugin):
        """Test add_favorite command"""
        result = await plugin.handle_command("add_favorite", {
            "station_id": "test123",
            "station": {"id": "test123", "name": "Test Radio"}
        })

        assert result["success"] is True
        plugin.station_manager.add_favorite.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_add_favorite_no_id(self, plugin):
        """Test add_favorite command without station_id"""
        result = await plugin.handle_command("add_favorite", {})

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_handle_command_remove_favorite(self, plugin):
        """Test remove_favorite command"""
        result = await plugin.handle_command("remove_favorite", {"station_id": "test123"})

        assert result["success"] is True
        plugin.station_manager.remove_favorite.assert_called_once_with("test123")

    @pytest.mark.asyncio
    async def test_handle_command_mark_broken(self, plugin):
        """Test mark_broken command"""
        result = await plugin.handle_command("mark_broken", {"station_id": "test123"})

        assert result["success"] is True
        plugin.station_manager.mark_as_broken.assert_called_once_with("test123")

    @pytest.mark.asyncio
    async def test_handle_command_reset_broken(self, plugin):
        """Test reset_broken command"""
        result = await plugin.handle_command("reset_broken", {})

        assert result["success"] is True
        plugin.station_manager.reset_broken_stations.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, plugin):
        """Test unknown command"""
        result = await plugin.handle_command("unknown_command", {})

        assert result["success"] is False
        assert "Unsupported command" in result["error"]

    # ===================
    # STATUS TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_get_status_idle(self, plugin):
        """Test get_status in idle state"""
        status = await plugin.get_status()

        assert status["service_active"] is True
        assert status["mpv_connected"] is True
        assert status["is_playing"] is False
        assert status["current_station"] is None

    @pytest.mark.asyncio
    async def test_get_status_playing(self, plugin):
        """Test get_status while playing"""
        plugin.current_station = {"id": "test", "name": "Test Radio"}
        plugin._is_playing = True
        plugin._metadata = {"station_name": "Test Radio", "is_playing": True}

        status = await plugin.get_status()

        assert status["is_playing"] is True
        assert status["current_station"]["name"] == "Test Radio"

    @pytest.mark.asyncio
    async def test_get_status_error(self, plugin):
        """Test get_status with error"""
        plugin.service_manager.get_status = AsyncMock(side_effect=Exception("Service error"))

        status = await plugin.get_status()

        assert status["service_active"] is False
        assert "error" in status

    # ===================
    # RESTART TEST
    # ===================

    @pytest.mark.asyncio
    async def test_restart(self, plugin):
        """Test restart"""
        plugin.current_station = {"id": "test", "name": "Test Radio"}
        plugin._is_playing = True

        result = await plugin.restart()

        assert result is True
        assert plugin.current_station is None
        assert plugin._is_playing is False
        plugin.mpv.disconnect.assert_called_once()
        plugin.control_service.assert_called_with(plugin.service_name, "restart")

    @pytest.mark.asyncio
    async def test_restart_service_failure(self, plugin):
        """Test restart with service failure"""
        plugin.control_service = AsyncMock(return_value=False)

        result = await plugin.restart()

        assert result is False

    # ===================
    # METADATA UPDATE TEST
    # ===================

    @pytest.mark.asyncio
    async def test_update_metadata(self, plugin):
        """Test metadata update"""
        plugin.current_station = {
            'id': 'test123',
            'name': 'Test Radio',
            'url': 'http://stream.test.com',
            'country': 'France',
            'genre': 'Pop',
            'favicon': 'http://test.com/icon.png',
            'bitrate': 128,
            'codec': 'mp3'
        }
        plugin._is_playing = True
        plugin._is_buffering = False

        await plugin._update_metadata()

        assert plugin._metadata["station_id"] == "test123"
        assert plugin._metadata["station_name"] == "Test Radio"
        assert plugin._metadata["is_playing"] is True
        assert plugin._metadata["buffering"] is False

    @pytest.mark.asyncio
    async def test_update_metadata_no_station(self, plugin):
        """Test metadata update without station"""
        plugin.current_station = None

        await plugin._update_metadata()

        assert plugin._metadata.get("station_id") is None

    # ===================
    # INITIAL STATE TEST
    # ===================

    @pytest.mark.asyncio
    async def test_get_initial_state(self, plugin):
        """Test de get_initial_state"""
        result = await plugin.get_initial_state()

        assert "service_active" in result
        assert "mpv_connected" in result
        assert "is_playing" in result
