# backend/tests/test_bluetooth_source.py
"""
Unit tests for BluetoothSource (features/bluetooth/source.py).

Tests cover:
- BaseAudioSource compliance
- Lifecycle (start, stop, restart)
- Multiroom reroute (release/acquire only the player, keep the A2DP link)
- Device connection tracking
- Command handling
- BlueALSA monitor integration
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.bluetooth.source import BluetoothSource
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.monitor import BlueAlsaMonitor
from backend.core.models.audio_state import SourceState


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
def bluetooth_source(config):
    """Create BluetoothSource with mocked components."""
    source = BluetoothSource(config)

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

    # Mock monitor
    source.monitor = Mock(spec=BlueAlsaMonitor)
    source.monitor.start = AsyncMock(return_value=True)
    source.monitor.stop = AsyncMock()
    source.monitor.set_callbacks = Mock()
    source.monitor.connected_devices = {}

    # Mock settings_service (needed by _do_stop -> _cleanup)
    source._settings_service = Mock()
    source._settings_service.get_setting = AsyncMock(return_value=None)

    return source


class TestBaseClassCompliance:
    """Test that BluetoothSource extends BaseAudioSource correctly."""

    def test_has_required_attributes(self, bluetooth_source):
        """Test required attributes exist."""
        assert bluetooth_source.source_id == "bluetooth"
        assert bluetooth_source.service_name == "milo-bluealsa.service"

class TestBluetoothSourceConfig:
    """Test BluetoothSource configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        source = BluetoothSource()

        assert source.bluetooth_service == "bluetooth.service"
        assert source.bluealsa_service == "milo-bluealsa.service"
        assert source.bluealsa_aplay_service == "milo-bluealsa-aplay.service"
        assert source.stop_bluetooth_on_exit is True
        assert source.auto_agent is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = {
            "bluetooth_service": "custom-bluetooth.service",
            "bluealsa_aplay_service": "custom-aplay.service",
            "stop_bluetooth_on_exit": False,
            "auto_agent": False
        }
        source = BluetoothSource(config)

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

class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_device(self, bluetooth_source):
        """Test state is READY with no device."""
        bluetooth_source.connected_device = None
        bluetooth_source._update_connection_state()

        assert bluetooth_source.state == SourceState.READY

    def test_update_state_with_device(self, bluetooth_source):
        """Test state is ACTIVE with device."""
        bluetooth_source.connected_device = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "iPhone"
        }
        bluetooth_source._update_connection_state()

        assert bluetooth_source.state == SourceState.ACTIVE

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

class TestBluetoothReroute:
    """Multiroom reroute: bounce ONLY bluealsa-aplay, keep the A2DP link alive.

    A multiroom toggle must not kick the phone off (the regression after
    fd23f0e6 made _do_stop tear down bluealsa + bluetooth.service). The reroute
    hooks stop/start only the writer; bluealsa + bluetooth.service stay up, so
    the A2DP link survives. The monitor tracks PCM add/remove from the bluealsa
    daemon (the phone's transport), not from the aplay consumer, so bouncing
    the writer is never seen as a disconnect — no guard needed.
    """

    @pytest.mark.asyncio
    async def test_release_stops_only_player(self, bluetooth_source):
        """release_for_reroute stops bluealsa-aplay and NOT bluealsa /
        bluetooth.service, and keeps connected_device for the acquire half."""
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "iPhone"}

        result = await bluetooth_source.release_for_reroute()

        assert result is True
        bluetooth_source._service_manager.stop.assert_called_once_with("milo-bluealsa-aplay.service")
        # The A2DP link (bluealsa + bluetooth.service) must stay up.
        stopped = [c.args[0] for c in bluetooth_source._service_manager.stop.call_args_list]
        assert "milo-bluealsa.service" not in stopped
        assert "bluetooth.service" not in stopped
        # Device kept so acquire can re-publish ACTIVE.
        assert bluetooth_source.connected_device is not None

    @pytest.mark.asyncio
    async def test_acquire_restarts_player_and_stays_active(self, bluetooth_source):
        """acquire_after_reroute restarts the player and re-publishes ACTIVE for
        the still-connected device."""
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "iPhone"}

        result = await bluetooth_source.acquire_after_reroute()

        assert result is True
        bluetooth_source._service_manager.start.assert_called_once_with("milo-bluealsa-aplay.service")
        assert bluetooth_source.state == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_acquire_failure_returns_false(self, bluetooth_source):
        """A failed player restart is reported; the caller (_apply_transition)
        treats it as best-effort and keeps the transition successful."""
        bluetooth_source._service_manager.start = AsyncMock(return_value=False)

        result = await bluetooth_source.acquire_after_reroute()

        assert result is False

    @pytest.mark.asyncio
    async def test_real_disconnect_during_reroute_is_honored(self, bluetooth_source):
        """No restart guard any more: if the phone genuinely disconnects while
        the writer is down, the monitor's PCMRemoved → _on_device_disconnected
        still clears state (the old _restart_in_progress suppression is gone)."""
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "iPhone"}

        await bluetooth_source.release_for_reroute()
        await bluetooth_source._on_device_disconnected("AA:BB:CC:DD:EE:FF", "iPhone")

        assert bluetooth_source.connected_device is None


class TestBlueAlsaMonitor:
    """Test BlueAlsaMonitor component."""

    def testparse_pcm_path_valid(self):
        """Test parsing valid PCM path."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/source"

        result = monitor.parse_pcm_path(path)

        assert result is not None
        assert result["address"] == "AA:BB:CC:DD:EE:FF"
        assert result["type"] == "a2dp-sink"

    def testparse_pcm_path_invalid_direction(self):
        """Test parsing PCM path with wrong direction."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/sink"

        result = monitor.parse_pcm_path(path)

        assert result is None

    def testparse_pcm_path_short_path(self):
        """Test parsing too short path."""
        monitor = BlueAlsaMonitor()
        path = "/org/bluealsa/hci0"

        result = monitor.parse_pcm_path(path)

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

        assert agent._registered is False
        assert agent.path.startswith("/org/milo/agent_")
