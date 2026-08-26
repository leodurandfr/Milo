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
from backend.sources.bluetooth.adapter import BluetoothAdapter
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth import (
    adapter as adapter_module,
    agent as agent_module,
    avrcp as avrcp_module,
    monitor as monitor_module,
)
from backend.sources.bluetooth.monitor import BlueAlsaMonitor, PCM_REMOVED_PREFIX
from backend.core.models.audio_state import SourceState


@pytest.fixture(autouse=True)
def never_the_real_system_bus(monkeypatch):
    """The appliance's own BlueZ and BlueALSA are on this machine's system bus.

    Measured 2026-08-24 and left open until B7: `BlueAlsaMonitor.start()` reached
    `MessageBus(bus_type=BusType.SYSTEM).connect()` on the live socket at every
    `pytest backend/` run on this host — the only connection the whole suite made
    outside pytest's own temp directories. The bus was used there only to *read*
    BlueZ names on a fail-open path, which is why it was classed benign; a test
    should still not depend on BlueZ being present, and the next thing to reach
    for that bus is not guaranteed to be a read.

    Same shape as `test_bt_remote.py::never_the_real_system_bus`, but over the
    **four** modules of this package that open one — `adapter`, `agent`,
    `avrcp` and `monitor`. Measured: patching `monitor` alone left the two
    connections in place, because `start()` reaches the adapter and the agent
    first. Covering one module of a package is not covering the package.
    """
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the appliance's real D-Bus system bus")

    for module in (adapter_module, agent_module, avrcp_module, monitor_module):
        monkeypatch.setattr(module, "MessageBus", refuse, raising=False)


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

    # Mock adapter (BlueZ D-Bus boundary)
    source.adapter = Mock(spec=BluetoothAdapter)
    source.adapter.power_on = AsyncMock(return_value=True)
    source.adapter.set_discoverable_timeout = AsyncMock(return_value=True)
    source.adapter.set_exposure = AsyncMock(return_value=True)
    source.adapter.set_audio_peers_blocked = AsyncMock(return_value=True)
    source.adapter.close = AsyncMock()

    # Mock settings_service (needed by _do_stop -> _cleanup)
    source._settings_service = Mock()
    source._settings_service.get_setting = AsyncMock(return_value=None)

    return source


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


class TestStartTimeDetectionHandsOverToTheMonitor:
    """The scan at start and the event feed must own one collection.

    `_handle_pcm_removed` fires the departure only for addresses the monitor
    holds. A link found by `_detect_connected_device` (a backend restart over a
    live A2DP session) used to be written to the source alone, so its PCMRemoved
    was read, parsed and dropped: the card kept naming a sender that had left,
    and no other sender could take its place until the source was switched away.
    """

    PCM_PATH = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/source"

    @pytest.fixture
    def source_with_live_pcm(self, bluetooth_source):
        """Real monitor (the collection under test), stubbed bluealsa-cli."""
        monitor = BlueAlsaMonitor()
        monitor.resolve_device_name = AsyncMock(return_value="Phone")
        bluetooth_source.monitor = monitor
        return bluetooth_source

    async def _detect(self, source):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(self.PCM_PATH.encode() + b"\n", b""))
        proc.returncode = 0
        with patch('asyncio.create_subprocess_exec', return_value=proc):
            await source._detect_connected_device()

    @pytest.mark.asyncio
    async def test_detected_pcm_enters_the_monitor_collection(self, source_with_live_pcm):
        await self._detect(source_with_live_pcm)

        assert source_with_live_pcm.connected_device["address"] == "AA:BB:CC:DD:EE:FF"
        assert "AA:BB:CC:DD:EE:FF" in source_with_live_pcm.monitor._connected_devices

    @pytest.mark.asyncio
    async def test_a_detected_pcm_can_then_be_seen_leaving(self, source_with_live_pcm):
        source = source_with_live_pcm
        source.monitor.set_callbacks(
            source._on_device_connected, source._on_device_disconnected
        )
        await self._detect(source)

        await source.monitor._process_line(f"{PCM_REMOVED_PREFIX} {self.PCM_PATH}")

        assert source.connected_device is None


class TestExposureFollowsState:
    """Discoverable/pairable/blocked are a function of state, not of start/stop.

    The owner's invariant: Milō is discoverable and connectable only while the
    Bluetooth source runs and no sender holds it. Each transition below used to
    leave one half of it unenforced — a connected device did not hide the
    appliance, and a paired device could still dial in with the source off,
    because bluetooth.service deliberately stays up for the HID remote.
    """

    @pytest.mark.asyncio
    async def test_start_with_no_sender_opens(self, bluetooth_source):
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            mock_exec.return_value = proc
            await bluetooth_source.start()

        bluetooth_source.adapter.set_exposure.assert_called_with(
            discoverable=True, pairable=True
        )
        bluetooth_source.adapter.set_audio_peers_blocked.assert_called_with(
            False, keep_unblocked=None
        )

    @pytest.mark.asyncio
    async def test_a_connected_sender_hides_the_appliance(self, bluetooth_source):
        bluetooth_source._running = True

        await bluetooth_source._on_device_connected("AA:BB:CC:DD:EE:FF", "Phone")

        bluetooth_source.adapter.set_exposure.assert_called_with(
            discoverable=False, pairable=False
        )
        # The holder is exempt: blocking it would drop the audio it is playing.
        bluetooth_source.adapter.set_audio_peers_blocked.assert_called_with(
            True, keep_unblocked="AA:BB:CC:DD:EE:FF"
        )

    @pytest.mark.asyncio
    async def test_a_departing_sender_opens_it_again(self, bluetooth_source):
        bluetooth_source._running = True
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Phone"}

        await bluetooth_source._on_device_disconnected("AA:BB:CC:DD:EE:FF", "Phone")

        bluetooth_source.adapter.set_exposure.assert_called_with(
            discoverable=True, pairable=True
        )
        bluetooth_source.adapter.set_audio_peers_blocked.assert_called_with(
            False, keep_unblocked=None
        )

    @pytest.mark.asyncio
    async def test_stop_blocks_every_sender_including_the_last_one(self, bluetooth_source):
        bluetooth_source._running = True
        bluetooth_source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "Phone"}

        await bluetooth_source._do_stop()

        bluetooth_source.adapter.set_exposure.assert_called_with(
            discoverable=False, pairable=False
        )
        # No exemption here — the source is off, so nothing may dial in.
        bluetooth_source.adapter.set_audio_peers_blocked.assert_called_with(
            True, keep_unblocked=None
        )

    @pytest.mark.asyncio
    async def test_a_refused_property_is_a_failure_not_a_configured_adapter(
        self, bluetooth_source
    ):
        """The whole point of 7.4: bluetoothctl exited 0 whatever happened."""
        bluetooth_source._running = True
        bluetooth_source.adapter.set_exposure = AsyncMock(return_value=False)

        assert await bluetooth_source._apply_exposure() is False

    @pytest.mark.asyncio
    async def test_a_dead_adapter_fails_the_configuration(self, bluetooth_source):
        bluetooth_source.adapter.power_on = AsyncMock(return_value=False)

        assert await bluetooth_source._configure_adapter() is False
        bluetooth_source.adapter.set_exposure.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_start_does_not_leave_the_appliance_open(self, bluetooth_source):
        """ERROR is neither started nor stopped, and nothing revisits it.

        A failure past step 3 has already opened the adapter and unblocked the
        senders; without this the appliance stays discoverable and connectable
        for as long as the source sits in ERROR.
        """
        bluetooth_source.monitor.start = AsyncMock(return_value=False)

        assert await bluetooth_source.start() is False

        bluetooth_source.adapter.set_exposure.assert_called_with(
            discoverable=False, pairable=False
        )
        bluetooth_source.adapter.set_audio_peers_blocked.assert_called_with(
            True, keep_unblocked=None
        )
