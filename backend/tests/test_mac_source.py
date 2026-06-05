# backend/tests/test_mac_source.py
"""
Unit tests for MacSource (features/mac/source.py).

Tests cover:
- BaseAudioSource compliance
- Lifecycle (start, stop, restart)
- Connection tracking
- Command handling
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.mac.source import MacSource, _parse_ip_from_line, _normalize_ip
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState


@pytest.fixture
def config():
    """Default Mac source config."""
    return {
        "rtp_port": 10001,
        "rs8m_port": 10002,
        "rtcp_port": 10003,
        "audio_output": "hw:1,0"
    }


@pytest.fixture
def mac_source(config):
    """Create MacSource with mocked service manager."""
    source = MacSource(config)
    # Mock service manager methods
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)
    return source


class TestBaseClassCompliance:
    """Test that MacSource extends BaseAudioSource correctly."""

    def test_extends_base_audio_source(self, mac_source):
        """Test MacSource extends BaseAudioSource."""
        assert isinstance(mac_source, BaseAudioSource)

    def test_has_required_attributes(self, mac_source):
        """Test required attributes exist."""
        assert mac_source.source_id == "mac"
        assert mac_source.service_name == "milo-mac.service"

    def test_has_required_methods(self, mac_source):
        """Test required methods exist."""
        assert hasattr(mac_source, 'start')
        assert hasattr(mac_source, 'stop')
        assert hasattr(mac_source, 'status')
        assert hasattr(mac_source, 'command')


class TestMacSourceConfig:
    """Test MacSource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = MacSource()

        assert source.rtp_port == 10001
        assert source.rs8m_port == 10002
        assert source.rtcp_port == 10003
        assert source.audio_output == "hw:1,0"

    def test_custom_config(self):
        """Test custom configuration."""
        config = {
            "rtp_port": 20001,
            "rs8m_port": 20002,
            "rtcp_port": 20003,
            "audio_output": "hw:2,0",
            "network_interface": "eth0"
        }
        source = MacSource(config)

        assert source.rtp_port == 20001
        assert source.audio_output == "hw:2,0"
        assert source.network_interface == "eth0"


class TestMacSourceLifecycle:
    """Test MacSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self, mac_source):
        """Test successful start."""
        # Mock subprocess calls
        with patch('asyncio.create_subprocess_shell') as mock_shell, \
             patch('asyncio.create_subprocess_exec') as mock_exec:

            # Mock log check
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_shell.return_value = mock_proc

            # Mock journalctl (will be cancelled)
            mock_journal = AsyncMock()
            mock_journal.stdout = AsyncMock()
            mock_journal.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_journal.returncode = None
            mock_journal.terminate = Mock()
            mock_journal.wait = AsyncMock()
            mock_exec.return_value = mock_journal

            result = await mac_source.start()

            # Cancel monitoring task for cleanup
            if mac_source._monitor_task:
                mac_source._monitor_task.cancel()
                try:
                    await mac_source._monitor_task
                except asyncio.CancelledError:
                    pass

        assert result is True
        mac_source._service_manager.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_service_failure(self, mac_source):
        """Test start fails if service fails."""
        mac_source._service_manager.start = AsyncMock(return_value=False)

        result = await mac_source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, mac_source):
        """Test successful stop."""
        # Start first
        mac_source._monitor_task = None
        mac_source.connected_clients = {"192.168.1.1": "TestMac"}

        result = await mac_source.stop()

        assert result is True
        assert len(mac_source.connected_clients) == 0
        mac_source._service_manager.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_monitor(self, mac_source):
        """Test stop cancels monitoring task."""
        # Create a mock task
        mock_task = AsyncMock()
        mock_task.cancel = Mock()
        mac_source._monitor_task = mock_task

        await mac_source.stop()

        mock_task.cancel.assert_called_once()


class TestMacSourceStatus:
    """Test MacSource status method."""

    @pytest.mark.asyncio
    async def test_status_no_connections(self, mac_source):
        """Test status with no connections."""
        status = await mac_source.status()

        assert "state" in status
        assert status["connected"] is False
        assert status["client_names"] == []
        assert status["rtp_port"] == 10001

    @pytest.mark.asyncio
    async def test_status_with_connections(self, mac_source):
        """Test status with connected clients."""
        mac_source.connected_clients = {
            "192.168.1.1": "MacBook-Pro",
            "192.168.1.2": "iMac"
        }

        status = await mac_source.status()

        assert status["connected"] is True
        assert "MacBook-Pro" in status["client_names"]
        assert "iMac" in status["client_names"]


class TestMacSourceCommands:
    """Test MacSource command handling."""

    @pytest.mark.asyncio
    async def test_get_connections_command(self, mac_source):
        """Test get_connections command."""
        mac_source.connected_clients = {"192.168.1.1": "MacBook"}

        result = await mac_source.command("get_connections", {})

        assert result["success"] is True
        assert result["connection_count"] == 1
        assert "192.168.1.1" in result["connections"]

    @pytest.mark.asyncio
    async def test_unknown_command(self, mac_source):
        """Test unknown command returns error."""
        result = await mac_source.command("unknown_cmd", {})

        assert result["success"] is False
        assert "error" in result


class TestIPParsing:
    """Test IP address parsing helpers."""

    def test_parse_ipv4(self):
        """Test parsing IPv4 address."""
        line = "session router: creating route: address=192.168.1.100:10003"
        ip, port = _parse_ip_from_line(line)

        assert ip == "192.168.1.100"
        assert port == 10003

    def test_parse_ipv6(self):
        """Test parsing IPv6 address."""
        line = "session router: creating route: address=[2001:db8::1]:10003"
        ip, port = _parse_ip_from_line(line)

        assert ip == "2001:db8::1"
        assert port == 10003

    def test_parse_no_match(self):
        """Test no match returns None."""
        line = "some random log line"
        ip, port = _parse_ip_from_line(line)

        assert ip is None
        assert port is None

    def test_normalize_ip(self):
        """Test IP normalization."""
        assert _normalize_ip("[192.168.1.1]") == "192.168.1.1"
        assert _normalize_ip("192.168.1.1") == "192.168.1.1"
        assert _normalize_ip(None) is None


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_clients(self, mac_source):
        """Test state is WAITING with no clients."""
        mac_source.connected_clients = {}
        mac_source._update_connection_state()

        assert mac_source.state == SourceState.WAITING

    def test_update_state_with_clients(self, mac_source):
        """Test state is ACTIVE with clients."""
        mac_source.connected_clients = {"192.168.1.1": "TestMac"}
        mac_source._update_connection_state()

        assert mac_source.state == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_add_client(self, mac_source):
        """Test adding a client."""
        with patch.object(mac_source, '_resolve_hostname', return_value="MacBook"):
            await mac_source._add_client("192.168.1.100")

        assert "192.168.1.100" in mac_source.connected_clients
        assert mac_source.connected_clients["192.168.1.100"] == "MacBook"

    @pytest.mark.asyncio
    async def test_add_client_already_exists(self, mac_source):
        """Test adding existing client is no-op."""
        mac_source.connected_clients = {"192.168.1.1": "ExistingMac"}

        with patch.object(mac_source, '_resolve_hostname') as mock_resolve:
            await mac_source._add_client("192.168.1.1")

        # Should not call resolve for existing client
        mock_resolve.assert_not_called()


class TestLogProcessing:
    """Test log line processing."""

    @pytest.mark.asyncio
    async def test_process_connection_log(self, mac_source):
        """Test processing connection log."""
        with patch.object(mac_source, '_add_client') as mock_add:
            await mac_source._process_log_line(
                "session group: creating session address=192.168.1.100:10003"
            )

        mock_add.assert_called_once_with("192.168.1.100")

    @pytest.mark.asyncio
    async def test_process_disconnection_log(self, mac_source):
        """Test processing disconnection log."""
        mac_source.connected_clients = {"192.168.1.100": "TestMac"}

        await mac_source._process_log_line(
            "session router: removing route: address=192.168.1.100:10003"
        )

        assert "192.168.1.100" not in mac_source.connected_clients

    @pytest.mark.asyncio
    async def test_process_route_creation_log(self, mac_source):
        """Test processing route creation log."""
        with patch.object(mac_source, '_add_client') as mock_add:
            await mac_source._process_log_line(
                "creating new route address=192.168.1.100:10003"
            )

        mock_add.assert_called_once()
