# backend/tests/test_bluetooth_source.py
"""
Unit tests for BluetoothSource (features/bluetooth/source.py).

Tests cover:
- AudioSource Protocol compliance
- Lifecycle (start, stop, restart)
- Device connection tracking
- EventBus integration
- Command handling
- BlueALSA monitor integration
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.features.bluetooth.source import BluetoothSource
from backend.features.bluetooth.agent import BluetoothAgent
from backend.features.bluetooth.monitor import BlueAlsaMonitor
from backend.core.events import EventBus, Events
from backend.core.audio_source import AudioSource, SourceState


@pytest.fixture
def event_bus():
    """Create EventBus for tests."""
    return EventBus(debug=True)


@pytest.fixture
def config():
    """Default Bluetooth source config."""
    return {
        "bluetooth_service": "bluetooth.service",
        "bluealsa_service": "milo-bluealsa.service",
        "bluealsa_aplay_service": "milo-bluealsa-aplay.service",
        "stop_bluetooth_on_exit": True,
        "auto_agent": True
    }


@pytest.fixture
def bluetooth_source(event_bus, config):
    """Create BluetoothSource with mocked components."""
    source = BluetoothSource(event_bus, config)

    # Mock service manager
    source._service_manager = Mock()
    source._service_manager.start = AsyncMock(return_value=True)
    source._service_manager.stop = AsyncMock(return_value=True)
    source._service_manager.restart = AsyncMock(return_value=True)
    source._service_manager.is_active = AsyncMock(return_value=True)

    # Mock agent
    source.agent = Mock(spec=BluetoothAgent)
    source.agent.register = AsyncMock(return_value=True)
    source.agent.unregister = AsyncMock(return_value=True)
    source.agent.is_registered = False

    # Mock monitor
    source.monitor = Mock(spec=BlueAlsaMonitor)
    source.monitor.start = AsyncMock(return_value=True)
    source.monitor.stop = AsyncMock()
    source.monitor.set_callbacks = Mock()
    source.monitor.connected_devices = {}

    return source


class TestProtocolCompliance:
    """Test AudioSource Protocol compliance."""

    def test_implements_protocol(self, bluetooth_source):
        """Test BluetoothSource implements AudioSource protocol."""
        assert isinstance(bluetooth_source, AudioSource)

    def test_has_required_attributes(self, bluetooth_source):
        """Test required attributes exist."""
        assert bluetooth_source.source_id == "bluetooth"
        assert bluetooth_source.service_name == "milo-bluealsa.service"

    def test_has_required_methods(self, bluetooth_source):
        """Test required methods exist."""
        assert hasattr(bluetooth_source, 'start')
        assert hasattr(bluetooth_source, 'stop')
        assert hasattr(bluetooth_source, 'restart')
        assert hasattr(bluetooth_source, 'status')
        assert hasattr(bluetooth_source, 'command')


class TestBluetoothSourceConfig:
    """Test BluetoothSource configuration."""

    def test_default_config(self, event_bus):
        """Test default configuration values."""
        source = BluetoothSource(event_bus)

        assert source.bluetooth_service == "bluetooth.service"
        assert source.bluealsa_service == "milo-bluealsa.service"
        assert source.bluealsa_aplay_service == "milo-bluealsa-aplay.service"
        assert source.stop_bluetooth_on_exit is True
        assert source.auto_agent is True

    def test_custom_config(self, event_bus):
        """Test custom configuration."""
        config = {
            "bluetooth_service": "custom-bluetooth.service",
            "bluealsa_aplay_service": "custom-aplay.service",
            "stop_bluetooth_on_exit": False,
            "auto_agent": False
        }
        source = BluetoothSource(event_bus, config)

        assert source.bluetooth_service == "custom-bluetooth.service"
        assert source.bluealsa_aplay_service == "custom-aplay.service"
        assert source.stop_bluetooth_on_exit is False
        assert source.auto_agent is False


class TestBluetoothSourceLifecycle:
    """Test BluetoothSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self, bluetooth_source):
        """Test successful start."""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Mock bluetoothctl
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await bluetooth_source.start()

        assert result is True
        bluetooth_source.agent.register.assert_called_once()
        bluetooth_source.monitor.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_service_failure(self, bluetooth_source):
        """Test start fails if service fails."""
        bluetooth_source._service_manager.start = AsyncMock(return_value=False)

        result = await bluetooth_source.start()

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_success(self, bluetooth_source):
        """Test successful stop."""
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test"}

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await bluetooth_source.stop()

        assert result is True
        assert bluetooth_source.connected_device is None
        bluetooth_source.monitor.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_calls_monitor_stop(self, bluetooth_source):
        """Test stop calls monitor.stop()."""
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Test"}

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await bluetooth_source.stop()

        bluetooth_source.monitor.stop.assert_called_once()


class TestBluetoothSourceStatus:
    """Test BluetoothSource status method."""

    @pytest.mark.asyncio
    async def test_status_no_device(self, bluetooth_source):
        """Test status with no device connected."""
        status = await bluetooth_source.status()

        assert "state" in status
        assert status["device_connected"] is False
        assert status["device_name"] is None
        assert status["device_address"] is None

    @pytest.mark.asyncio
    async def test_status_with_device(self, bluetooth_source):
        """Test status with connected device."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }

        status = await bluetooth_source.status()

        assert status["device_connected"] is True
        assert status["device_name"] == "iPhone"
        assert status["device_address"] == "AA:BB:CC:DD:EE:FF"


class TestBluetoothSourceCommands:
    """Test BluetoothSource command handling."""

    @pytest.mark.asyncio
    async def test_disconnect_no_device(self, bluetooth_source):
        """Test disconnect with no device."""
        result = await bluetooth_source.command("disconnect", {})

        assert result["success"] is False
        assert "No device connected" in result["error"]

    @pytest.mark.asyncio
    async def test_disconnect_with_device(self, bluetooth_source):
        """Test disconnect with connected device."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await bluetooth_source.command("disconnect", {})

        assert result["success"] is True

    # Note: restart_audio, restart_bluealsa, and toggle_agent commands
    # are not part of the current BluetoothSource API (only "disconnect" is supported)

    @pytest.mark.asyncio
    async def test_unknown_command(self, bluetooth_source):
        """Test unknown command returns error."""
        result = await bluetooth_source.command("unknown_cmd", {})

        assert result["success"] is False
        assert "error" in result


class TestBluetoothSourceEventBus:
    """Test BluetoothSource EventBus integration."""

    @pytest.mark.asyncio
    async def test_start_emits_event(self, bluetooth_source, event_bus):
        """Test start emits SOURCE_STARTED event."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STARTED, handler)

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await bluetooth_source.start()

        assert len(received) == 1
        assert received[0]["source"] == "bluetooth"

    @pytest.mark.asyncio
    async def test_stop_emits_event(self, bluetooth_source, event_bus):
        """Test stop emits SOURCE_STOPPED event."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STOPPED, handler)

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await bluetooth_source.stop()

        assert len(received) == 1
        assert received[0]["source"] == "bluetooth"


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_device(self, bluetooth_source):
        """Test state is READY with no device."""
        bluetooth_source.connected_device = None
        bluetooth_source._update_connection_state()

        assert bluetooth_source.state == SourceState.READY

    def test_update_state_with_device(self, bluetooth_source):
        """Test state is CONNECTED with device."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }
        bluetooth_source._update_connection_state()

        assert bluetooth_source.state == SourceState.CONNECTED

    @pytest.mark.asyncio
    async def test_on_device_connected(self, bluetooth_source):
        """Test device connection callback."""
        await bluetooth_source._on_device_connected("AA:BB:CC:DD:EE:FF", "iPhone")

        assert bluetooth_source.connected_device is not None
        assert bluetooth_source.connected_device["address"] == "AA:BB:CC:DD:EE:FF"
        assert bluetooth_source.connected_device["name"] == "iPhone"

    @pytest.mark.asyncio
    async def test_on_device_disconnected(self, bluetooth_source):
        """Test device disconnection callback."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }

        await bluetooth_source._on_device_disconnected("AA:BB:CC:DD:EE:FF", "iPhone")

        assert bluetooth_source.connected_device is None

    @pytest.mark.asyncio
    async def test_on_device_disconnected_ignored_during_restart(self, bluetooth_source):
        """Test disconnect is ignored during restart."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }
        bluetooth_source._restart_in_progress = True

        await bluetooth_source._on_device_disconnected("AA:BB:CC:DD:EE:FF", "iPhone")

        # Device should still be connected
        assert bluetooth_source.connected_device is not None


class TestBlueAlsaMonitor:
    """Test BlueAlsaMonitor component."""

    def test_parse_pcm_path_valid(self):
        """Test parsing valid PCM path."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/source"

        result = monitor._parse_pcm_path(path)

        assert result is not None
        assert result["address"] == "AA:BB:CC:DD:EE:FF"
        assert result["type"] == "a2dp-sink"

    def test_parse_pcm_path_invalid_direction(self):
        """Test parsing PCM path with wrong direction."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/sink"

        result = monitor._parse_pcm_path(path)

        assert result is None

    def test_parse_pcm_path_short_path(self):
        """Test parsing too short path."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0"

        result = monitor._parse_pcm_path(path)

        assert result is None


class TestBluetoothAgent:
    """Test BluetoothAgent component."""

    def test_agent_path_unique(self):
        """Test agent paths are unique."""
        agent1 = BluetoothAgent()
        agent2 = BluetoothAgent()

        assert agent1.path != agent2.path

    def test_agent_initial_state(self):
        """Test agent initial state."""
        agent = BluetoothAgent()

        assert agent.is_registered is False
        assert agent.path.startswith("/org/milo/agent_")
