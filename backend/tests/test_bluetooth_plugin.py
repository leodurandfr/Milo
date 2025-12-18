# backend/tests/test_bluetooth_plugin.py
"""
Unit tests for the BluetoothPlugin
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.plugins.bluetooth.plugin import BluetoothPlugin
from backend.domain.audio_state import PluginState, AudioSource


class TestBluetoothPlugin:
    """Tests for the Bluetooth plugin"""

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.update_plugin_state = AsyncMock()
        sm.system_state = Mock()
        sm.system_state.active_source = AudioSource.BLUETOOTH
        sm.system_state.plugin_state = PluginState.READY
        sm.system_state.metadata = {}
        return sm

    @pytest.fixture
    def plugin_config(self):
        """Plugin configuration"""
        return {
            'service_name': 'milo-bluealsa.service',
            'bluetooth_service': 'bluetooth.service',
            'stop_bluetooth_on_exit': True,
            'auto_agent': True
        }

    @pytest.fixture
    def plugin(self, plugin_config, mock_state_machine):
        """Fixture to create a Bluetooth plugin"""
        with patch.object(BluetoothPlugin, '__init__', lambda self, config, state_machine: None):
            plugin = BluetoothPlugin.__new__(BluetoothPlugin)

            # Manually call the original __init__ with mocked components
            plugin.name = "bluetooth"
            plugin.state_machine = mock_state_machine
            plugin.config = plugin_config
            plugin.bluetooth_service = plugin_config.get("bluetooth_service", "bluetooth.service")
            plugin.bluealsa_service = plugin_config.get("service_name", "milo-bluealsa.service")
            plugin.bluealsa_aplay_service = "milo-bluealsa-aplay.service"
            plugin.stop_bluetooth = plugin_config.get("stop_bluetooth_on_exit", True)
            plugin.auto_agent = plugin_config.get("auto_agent", True)
            plugin.service_name = plugin.bluealsa_service
            plugin.connected_device = None
            plugin._auto_connecting = False
            plugin._current_device = "milo_bluetooth"
            plugin._monitoring_task = None
            plugin._first_connected_device = None
            plugin._initialized = False
            plugin.logger = Mock()

            # Mock the components
            plugin.agent = Mock()
            plugin.agent.register = AsyncMock(return_value=True)
            plugin.agent.unregister = AsyncMock()

            plugin.monitor = Mock()
            plugin.monitor.set_callbacks = Mock()
            plugin.monitor.start_monitoring = AsyncMock(return_value=True)
            plugin.monitor.stop_monitoring = AsyncMock()

            plugin.playback = Mock()
            plugin.playback.start_playback = AsyncMock(return_value=True)
            plugin.playback.stop_playback = AsyncMock()
            plugin.playback.stop_all_playback = AsyncMock()

            plugin.service_manager = Mock()
            plugin.service_manager.is_active = AsyncMock(return_value=True)

            # Mock control_service
            plugin.control_service = AsyncMock(return_value=True)

            # Mock notify_state_change
            plugin.notify_state_change = AsyncMock()

            return plugin

    # ===================
    # INITIALIZATION TESTS
    # ===================

    def test_initialization_config(self, plugin_config, mock_state_machine):
        """Test basic plugin initialization"""
        with patch('backend.infrastructure.plugins.bluetooth.plugin.BluetoothAgent'), \
             patch('backend.infrastructure.plugins.bluetooth.plugin.BlueAlsaMonitor'), \
             patch('backend.infrastructure.plugins.bluetooth.plugin.BlueAlsaPlayback'):

            plugin = BluetoothPlugin(config=plugin_config, state_machine=mock_state_machine)

            assert plugin.name == "bluetooth"
            assert plugin.bluealsa_service == "milo-bluealsa.service"
            assert plugin.bluetooth_service == "bluetooth.service"
            assert plugin.stop_bluetooth is True
            assert plugin.auto_agent is True
            assert plugin.connected_device is None

    @pytest.mark.asyncio
    async def test_do_initialize_success(self, plugin):
        """Test successful initialization"""
        with patch.object(plugin, '_check_dependencies', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True

            result = await plugin._do_initialize()

            assert result is True
            plugin.monitor.set_callbacks.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_initialize_missing_dependencies(self, plugin):
        """Test initialization with missing dependencies"""
        with patch.object(plugin, '_check_dependencies', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = False

            result = await plugin._do_initialize()

            assert result is False

    @pytest.mark.asyncio
    async def test_check_dependencies_success(self, plugin):
        """Test dependency check - success"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.wait = AsyncMock()
            mock_exec.return_value = mock_process

            result = await plugin._check_dependencies()

            assert result is True
            # Verify that 'which' was called for each dependency
            assert mock_exec.call_count == 2

    @pytest.mark.asyncio
    async def test_check_dependencies_missing(self, plugin):
        """Test dependency check - missing"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1  # Non trouvé
            mock_process.wait = AsyncMock()
            mock_exec.return_value = mock_process

            result = await plugin._check_dependencies()

            assert result is False

    # ===================
    # START TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_do_start_success(self, plugin):
        """Test successful startup"""
        with patch.object(plugin, '_configure_adapter', new_callable=AsyncMock) as mock_config:
            mock_config.return_value = True

            result = await plugin._do_start()

            assert result is True
            # Verify that the services have been started
            assert plugin.control_service.call_count >= 3  # bluetooth, bluealsa, aplay

    @pytest.mark.asyncio
    async def test_do_start_service_failure(self, plugin):
        """Test startup with service failure"""
        plugin.control_service = AsyncMock(return_value=False)

        with patch.object(plugin, '_cleanup', new_callable=AsyncMock):
            result = await plugin._do_start()

            assert result is False

    @pytest.mark.asyncio
    async def test_configure_adapter(self, plugin):
        """Test Bluetooth adapter configuration"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = mock_process

            result = await plugin._configure_adapter()

            assert result is True
            mock_exec.assert_called_once()

    # ===================
    # STOP TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_stop_success(self, plugin):
        """Test successful stop"""
        plugin.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test Device"}

        with patch.object(plugin, '_cleanup', new_callable=AsyncMock), \
             patch.object(plugin, '_run_bluetoothctl_command', new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = True

            result = await plugin.stop()

            assert result is True
            assert plugin.connected_device is None

    @pytest.mark.asyncio
    async def test_stop_without_stopping_bluetooth(self, plugin):
        """Test stop without stopping bluetooth service"""
        plugin.stop_bluetooth = False

        with patch.object(plugin, '_cleanup', new_callable=AsyncMock), \
             patch.object(plugin, '_run_bluetoothctl_command', new_callable=AsyncMock):

            result = await plugin.stop()

            assert result is True
            # control_service should not be called for stop
            # because stop_bluetooth is False

    # ===================
    # CONNECTION CALLBACKS TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_on_device_connected(self, plugin):
        """Test device connection callback"""
        address = "AA:BB:CC:DD:EE:FF"
        name = "Test Phone"

        await plugin._on_device_connected(address, name)

        assert plugin.connected_device is not None
        assert plugin.connected_device["address"] == address
        assert plugin.connected_device["name"] == name
        plugin.notify_state_change.assert_called_once()
        plugin.playback.start_playback.assert_called_once_with(address)

    @pytest.mark.asyncio
    async def test_on_device_connected_already_connected(self, plugin):
        """Test callback when a device is already connected"""
        plugin.connected_device = {"address": "11:22:33:44:55:66", "name": "First Device"}

        await plugin._on_device_connected("AA:BB:CC:DD:EE:FF", "Second Device")

        # The first device remains connected
        assert plugin.connected_device["address"] == "11:22:33:44:55:66"

    @pytest.mark.asyncio
    async def test_on_device_disconnected(self, plugin):
        """Test device disconnection callback"""
        address = "AA:BB:CC:DD:EE:FF"
        name = "Test Phone"
        plugin.connected_device = {"address": address, "name": name}

        await plugin._on_device_disconnected(address, name)

        assert plugin.connected_device is None
        plugin.playback.stop_playback.assert_called_once_with(address)
        plugin.notify_state_change.assert_called_with(PluginState.READY, {"device_connected": False})

    @pytest.mark.asyncio
    async def test_on_device_disconnected_different_device(self, plugin):
        """Test disconnection callback for a different device"""
        plugin.connected_device = {"address": "11:22:33:44:55:66", "name": "First Device"}

        # Disconnection of another device
        await plugin._on_device_disconnected("AA:BB:CC:DD:EE:FF", "Other Device")

        # The connected device should not be affected
        assert plugin.connected_device is not None
        assert plugin.connected_device["address"] == "11:22:33:44:55:66"

    # ===================
    # COMMAND HANDLING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_handle_command_disconnect(self, plugin):
        """Test disconnect command"""
        plugin.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test"}

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = mock_process

            result = await plugin.handle_command("disconnect", {})

            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handle_command_disconnect_no_device(self, plugin):
        """Test disconnect command without connected device"""
        plugin.connected_device = None

        result = await plugin.handle_command("disconnect", {})

        assert result["success"] is False
        assert "No device" in result["error"]

    @pytest.mark.asyncio
    async def test_handle_command_restart_audio(self, plugin):
        """Test restart_audio command"""
        plugin.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test"}

        result = await plugin.handle_command("restart_audio", {})

        assert result["success"] is True
        plugin.playback.stop_playback.assert_called_once()
        plugin.playback.start_playback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_restart_bluealsa(self, plugin):
        """Test restart_bluealsa command"""
        result = await plugin.handle_command("restart_bluealsa", {})

        assert result["success"] is True
        plugin.control_service.assert_called_with(plugin.bluealsa_service, "restart")

    @pytest.mark.asyncio
    async def test_handle_command_toggle_agent_disable(self, plugin):
        """Test toggle_agent command (disable)"""
        plugin.auto_agent = True

        result = await plugin.handle_command("toggle_agent", {})

        assert result["success"] is True
        assert plugin.auto_agent is False
        plugin.agent.unregister.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_toggle_agent_enable(self, plugin):
        """Test toggle_agent command (enable)"""
        plugin.auto_agent = False

        result = await plugin.handle_command("toggle_agent", {})

        assert result["success"] is True
        assert plugin.auto_agent is True
        plugin.agent.register.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, plugin):
        """Test unknown command"""
        result = await plugin.handle_command("unknown_command", {})

        assert result["success"] is False
        assert "Unknown command" in result["error"]

    # ===================
    # STATUS TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_get_status_connected(self, plugin):
        """Test get_status with connected device"""
        plugin.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test Phone"}
        plugin.auto_agent = True
        plugin._current_device = "milo_bluetooth"

        status = await plugin.get_status()

        assert status["device_connected"] is True
        assert status["device_name"] == "Test Phone"
        assert status["device_address"] == "AA:BB:CC:DD:EE:FF"
        assert status["auto_agent"] is True
        assert status["current_device"] == "milo_bluetooth"

    @pytest.mark.asyncio
    async def test_get_status_not_connected(self, plugin):
        """Test get_status without connected device"""
        plugin.connected_device = None

        status = await plugin.get_status()

        assert status["device_connected"] is False
        assert status["device_name"] is None
        assert status["device_address"] is None

    @pytest.mark.asyncio
    async def test_get_status_error(self, plugin):
        """Test get_status with error"""
        plugin.service_manager.is_active = AsyncMock(side_effect=Exception("Service error"))
        plugin._current_device = "milo_bluetooth"

        status = await plugin.get_status()

        assert status["device_connected"] is False
        assert "error" in status

    # ===================
    # RESTART TEST
    # ===================

    @pytest.mark.asyncio
    async def test_restart(self, plugin):
        """Test restart (bluealsa-aplay only)"""
        result = await plugin.restart()

        assert result is True
        plugin.control_service.assert_called_with(plugin.bluealsa_aplay_service, "restart")

    # ===================
    # CLEANUP TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_cleanup(self, plugin):
        """Test resource cleanup"""
        # Setup: no active monitoring task
        plugin._monitoring_task = None
        plugin._first_connected_device = "AA:BB:CC:DD:EE:FF"

        await plugin._cleanup()

        plugin.playback.stop_all_playback.assert_called_once()
        plugin.monitor.stop_monitoring.assert_called_once()
        assert plugin._first_connected_device is None

    @pytest.mark.asyncio
    async def test_cleanup_with_monitoring_task(self, plugin):
        """Test cleanup with active monitoring task"""
        # Create a real async task for the test
        async def dummy_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        plugin._monitoring_task = asyncio.create_task(dummy_task())
        plugin._first_connected_device = "AA:BB:CC:DD:EE:FF"

        await plugin._cleanup()

        plugin.playback.stop_all_playback.assert_called_once()
        plugin.monitor.stop_monitoring.assert_called_once()
        assert plugin._first_connected_device is None
        # The task should be cancelled
        assert plugin._monitoring_task.cancelled() or plugin._monitoring_task.done()
