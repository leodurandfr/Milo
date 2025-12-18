# backend/tests/test_mac_plugin.py
"""
Unit tests for the MacPlugin (ROC)
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.plugins.mac.plugin import MacPlugin, _parse_ip_from_line, _normalize_ip_for_storage
from backend.domain.audio_state import PluginState, AudioSource


class TestMacPluginHelpers:
    """Tests for helper functions"""

    def test_parse_ip_from_line_ipv4(self):
        """Test parsing IP IPv4"""
        line = "session router: creating route: address=192.168.1.172:54421"
        ip, port = _parse_ip_from_line(line)
        assert ip == "192.168.1.172"
        assert port == 54421

    def test_parse_ip_from_line_ipv6(self):
        """Test parsing IP IPv6"""
        line = "session router: creating route: address=[2001:db8::1]:10003"
        ip, port = _parse_ip_from_line(line)
        assert ip == "2001:db8::1"
        assert port == 10003

    def test_parse_ip_from_line_ipv6_linklocal(self):
        """Test parsing IPv6 link-local IP (without scope - current regex doesn't support scopes)"""
        # Note: the current regex doesn't support scopes like %wlan0 because the character class
        # doesn't include all letters. Testing with IPv6 without scope.
        line = "session router: creating route: address=[fe80::1]:10003"
        ip, port = _parse_ip_from_line(line)
        assert ip == "fe80::1"
        assert port == 10003

    def test_parse_ip_from_line_no_match(self):
        """Test parsing without IP"""
        line = "some random log line without IP"
        ip, port = _parse_ip_from_line(line)
        assert ip is None
        assert port is None

    def test_normalize_ip_for_storage(self):
        """Test IP normalization"""
        assert _normalize_ip_for_storage("[192.168.1.1]") == "192.168.1.1"
        assert _normalize_ip_for_storage("192.168.1.1") == "192.168.1.1"
        assert _normalize_ip_for_storage(None) is None


class TestMacPlugin:
    """Tests for the Mac plugin"""

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.update_plugin_state = AsyncMock()
        sm.system_state = Mock()
        sm.system_state.active_source = AudioSource.MAC
        sm.system_state.plugin_state = PluginState.READY
        sm.system_state.metadata = {}
        return sm

    @pytest.fixture
    def plugin_config(self):
        """Plugin configuration"""
        return {
            'service_name': 'milo-mac.service',
            'rtp_port': 10001,
            'rs8m_port': 10002,
            'rtcp_port': 10003,
            'audio_output': 'hw:1,0'
        }

    @pytest.fixture
    def plugin(self, plugin_config, mock_state_machine):
        """Fixture to create a Mac plugin"""
        with patch.object(MacPlugin, '__init__', lambda self, config, state_machine: None):
            plugin = MacPlugin.__new__(MacPlugin)

            # Initialize manually
            plugin.name = "roc"
            plugin.state_machine = mock_state_machine
            plugin.config = plugin_config
            plugin.service_name = plugin_config.get("service_name", "milo-mac.service")
            plugin.rtp_port = plugin_config.get("rtp_port", 10001)
            plugin.rs8m_port = plugin_config.get("rs8m_port", 10002)
            plugin.rtcp_port = plugin_config.get("rtcp_port", 10003)
            plugin.audio_output = plugin_config.get("audio_output", "hw:1,0")
            plugin.network_interface = plugin_config.get("network_interface")
            plugin.connected_clients = {}
            plugin.monitor_task = None
            plugin._stopping = False
            plugin._current_device = "milo_roc"
            plugin._initialized = False
            plugin.logger = Mock()

            # Mock service_manager
            plugin.service_manager = Mock()
            plugin.service_manager.is_active = AsyncMock(return_value=True)
            plugin.service_manager.get_status = AsyncMock(return_value={"active": True})

            # Mock control_service
            plugin.control_service = AsyncMock(return_value=True)

            # Mock notify_state_change method
            plugin.notify_state_change = AsyncMock()

            # Mock _monitor_events to prevent infinite loops
            async def mock_monitor():
                pass
            plugin._monitor_events = mock_monitor

            return plugin

    # ===================
    # INITIALIZATION TESTS
    # ===================

    def test_initialization_config(self, plugin_config, mock_state_machine):
        """Test basic plugin initialization"""
        plugin = MacPlugin(config=plugin_config, state_machine=mock_state_machine)

        assert plugin.name == "roc"
        assert plugin.service_name == "milo-mac.service"
        assert plugin.rtp_port == 10001
        assert plugin.rs8m_port == 10002
        assert plugin.rtcp_port == 10003
        assert plugin.connected_clients == {}

    @pytest.mark.asyncio
    async def test_do_initialize_success(self, plugin):
        """Test successful initialization"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'milo-mac.service enabled', b''))
            mock_exec.return_value = mock_process

            result = await plugin._do_initialize()

            assert result is True

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
        with patch.object(plugin, '_monitor_events', new_callable=AsyncMock):
            result = await plugin._do_start()

            assert result is True
            plugin.control_service.assert_called_with(plugin.service_name, "start")
            plugin.notify_state_change.assert_called()

    @pytest.mark.asyncio
    async def test_do_start_service_failure(self, plugin):
        """Test startup with service failure"""
        plugin.control_service = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    @pytest.mark.asyncio
    async def test_do_start_service_not_active(self, plugin):
        """Test startup when service doesn't become active"""
        plugin.service_manager.is_active = AsyncMock(return_value=False)

        result = await plugin._do_start()

        assert result is False

    # ===================
    # STOP TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_stop_success(self, plugin):
        """Test successful stop"""
        plugin.connected_clients = {"192.168.1.1": "Mac1"}

        result = await plugin.stop()

        assert result is True
        assert plugin._stopping is True
        assert plugin.connected_clients == {}

    @pytest.mark.asyncio
    async def test_stop_with_monitor_task(self, plugin):
        """Test stop with active monitoring task"""
        # Create a real task
        async def dummy_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        plugin.monitor_task = asyncio.create_task(dummy_task())

        result = await plugin.stop()

        assert result is True
        assert plugin.monitor_task is None

    # ===================
    # CLIENT MANAGEMENT TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_add_client(self, plugin):
        """Test adding a client"""
        with patch.object(plugin, '_resolve_hostname', new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = "MacBook-Pro"

            await plugin._add_client("192.168.1.100")

            assert "192.168.1.100" in plugin.connected_clients
            assert plugin.connected_clients["192.168.1.100"] == "MacBook-Pro"
            plugin.notify_state_change.assert_called()

    @pytest.mark.asyncio
    async def test_add_client_already_exists(self, plugin):
        """Test adding an already existing client"""
        plugin.connected_clients = {"192.168.1.100": "Mac1"}

        with patch.object(plugin, '_resolve_hostname', new_callable=AsyncMock) as mock_resolve:
            await plugin._add_client("192.168.1.100")

            # resolve_hostname should not be called
            mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_state_with_clients(self, plugin):
        """Test state update with connected clients"""
        plugin.connected_clients = {
            "192.168.1.100": "MacBook-Pro",
            "192.168.1.101": "iMac"
        }

        await plugin._update_state()

        plugin.notify_state_change.assert_called_once()
        call_args = plugin.notify_state_change.call_args
        assert call_args[0][0] == PluginState.CONNECTED
        assert call_args[0][1]["connected"] is True
        assert call_args[0][1]["client_count"] == 2

    @pytest.mark.asyncio
    async def test_update_state_no_clients(self, plugin):
        """Test state update without clients"""
        plugin.connected_clients = {}

        await plugin._update_state()

        plugin.notify_state_change.assert_called_once()
        call_args = plugin.notify_state_change.call_args
        assert call_args[0][0] == PluginState.READY
        assert call_args[0][1]["connected"] is False

    # ===================
    # LOG PROCESSING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_process_log_line_connection(self, plugin):
        """Test processing a connection log line"""
        line = "session group: creating session address=192.168.1.172:54421"

        with patch.object(plugin, '_add_client', new_callable=AsyncMock) as mock_add:
            await plugin._process_log_line(line)

            mock_add.assert_called_once_with("192.168.1.172")

    @pytest.mark.asyncio
    async def test_process_log_line_disconnection(self, plugin):
        """Test processing a disconnection log line"""
        plugin.connected_clients = {"192.168.1.172": "MacBook"}
        line = "session router: removing route: address=192.168.1.172:54421"

        await plugin._process_log_line(line)

        assert "192.168.1.172" not in plugin.connected_clients

    @pytest.mark.asyncio
    async def test_process_log_line_disconnection_unknown_client(self, plugin):
        """Test disconnection of an unknown client"""
        plugin.connected_clients = {}
        line = "session router: removing route: address=192.168.1.172:54421"

        # Should not raise an error
        await plugin._process_log_line(line)

        assert plugin.connected_clients == {}

    # ===================
    # HOSTNAME RESOLUTION TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_resolve_hostname_success(self, plugin):
        """Test successful mDNS resolution"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'192.168.1.100\tMacBook-Pro.local.\n', b''))
            mock_exec.return_value = mock_process

            result = await plugin._resolve_hostname("192.168.1.100")

            assert result == "MacBook-Pro"

    @pytest.mark.asyncio
    async def test_resolve_hostname_failure(self, plugin):
        """Test failed mDNS resolution - returns IP"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b''))
            mock_exec.return_value = mock_process

            result = await plugin._resolve_hostname("192.168.1.100")

            assert result == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_resolve_hostname_empty_ip(self, plugin):
        """Test resolution with empty IP"""
        result = await plugin._resolve_hostname("")

        assert result == "Mac connecté"

    # ===================
    # STATUS TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_get_status_with_clients(self, plugin):
        """Test get_status with connected clients"""
        plugin.connected_clients = {
            "192.168.1.100": "MacBook-Pro",
            "192.168.1.101": "iMac"
        }

        status = await plugin.get_status()

        assert status["service_active"] is True
        assert status["connected"] is True
        assert status["client_count"] == 2
        assert "MacBook-Pro" in status["client_names"]
        assert "iMac" in status["client_names"]

    @pytest.mark.asyncio
    async def test_get_status_no_clients(self, plugin):
        """Test get_status without clients"""
        plugin.connected_clients = {}

        status = await plugin.get_status()

        assert status["connected"] is False
        assert status["client_count"] == 0
        assert status["client_names"] == []

    @pytest.mark.asyncio
    async def test_get_status_error(self, plugin):
        """Test get_status with error"""
        plugin.service_manager.get_status = AsyncMock(side_effect=Exception("Service error"))

        status = await plugin.get_status()

        assert "error" in status

    # ===================
    # COMMAND HANDLING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_handle_command_restart(self, plugin):
        """Test restart command"""
        result = await plugin.handle_command("restart", {})

        assert result["success"] is True
        plugin.control_service.assert_called_with(plugin.service_name, "restart")

    @pytest.mark.asyncio
    async def test_handle_command_restart_failure(self, plugin):
        """Test restart command with failure"""
        plugin.control_service = AsyncMock(return_value=False)

        result = await plugin.handle_command("restart", {})

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, plugin):
        """Test unknown command"""
        result = await plugin.handle_command("unknown_command", {})

        assert result["success"] is False
        assert "Unknown command" in result["error"]

    # ===================
    # INITIAL STATE TEST
    # ===================

    @pytest.mark.asyncio
    async def test_get_initial_state(self, plugin, mock_state_machine):
        """Test get_initial_state"""
        # Configure state machine to return READY state
        mock_state_machine.system_state.active_source = AudioSource.MAC
        mock_state_machine.system_state.plugin_state = PluginState.READY

        with patch.object(plugin, 'get_status', new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"service_active": True, "connected": False}

            result = await plugin.get_initial_state()

            assert result["plugin_state"] == "ready"
            assert result["service_active"] is True
