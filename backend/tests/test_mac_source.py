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

from backend.sources.mac.source import MacSource
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
        async def _empty_follow(*args, **kwargs):
            """Stand-in for follow_unit: an async generator that yields nothing."""
            return
            yield  # pragma: no cover -- marks this a generator

        # read_unit (startup scan) returns no lines; follow_unit yields nothing
        # and completes, so the monitor task ends on its own.
        with patch('backend.sources.mac.source.read_unit', new=AsyncMock(return_value=[])), \
             patch('backend.sources.mac.source.follow_unit', new=_empty_follow):

            result = await mac_source.start()

            # Cancel monitoring task for cleanup (already done in practice)
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
        # `_do_stop` awaits the task after cancelling it, and an AsyncMock
        # instance is not awaitable — the whole teardown died there, swallowed,
        # with only `cancel()` having run. A real task is the only double that
        # reaches the end of the method.
        async def _forever():
            await asyncio.sleep(3600)

        task = asyncio.create_task(_forever())
        await asyncio.sleep(0)
        mac_source._monitor_task = task

        await mac_source.stop()

        assert task.cancelled()
        assert mac_source._monitor_task is None


class TestConnectionState:
    """Test connection state management."""

    def test_update_state_no_clients(self, mac_source):
        """Test state is READY with no clients."""
        mac_source.connected_clients = {}
        mac_source._update_connection_state()

        assert mac_source.state == SourceState.READY

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


class TestHostnameResolution:
    """What name a ROC sender's IP resolves to, across the avahi CLIs.

    The mocked boundary is the subprocess: what is asserted is the name the
    card ends up showing, given what the network answered.
    """

    @staticmethod
    def _avahi(reverse="", forward="", browse=""):
        """Stand in for avahi-resolve/avahi-browse: argv → stdout."""
        async def _exec(*args, **kwargs):
            argv = list(args)
            if argv[0] == "avahi-browse":
                out = browse
            elif "-a" in argv:
                out = reverse
            else:
                out = forward

            proc = Mock()
            proc.returncode = 0 if out else 1
            proc.communicate = AsyncMock(return_value=(out.encode(), b""))
            return proc

        return _exec

    # One Mac, streaming ROC from 192.168.1.173 while advertising Bonjour on
    # 192.168.1.21 — its private Wi-Fi address.
    BROWSE = (
        r"=;eth0;IPv4;Mac\032mini\032de\032L\195\169o;SSH Remote Terminal;local;"
        "Mac-mini-de-Leo.local;192.168.1.21;22;"
    )
    PRIVATE_REVERSE = "192.168.1.173\ta8fca8ba-7a2f-4862-8934-70b031dd2eab.home"
    PRIVATE_FORWARD = "a8fca8ba-7a2f-4862-8934-70b031dd2eab.local\t192.168.1.21"

    @pytest.mark.asyncio
    async def test_private_hostname_yields_the_bonjour_name(self, mac_source):
        """macOS's rotating hostname resolves, so only Bonjour gives a real name."""
        with patch('asyncio.create_subprocess_exec', new=self._avahi(
            reverse=self.PRIVATE_REVERSE, forward=self.PRIVATE_FORWARD, browse=self.BROWSE
        )):
            assert await mac_source._resolve_hostname("192.168.1.173") == "Mac mini de Léo"

    @pytest.mark.asyncio
    async def test_private_hostname_never_reaches_the_ui(self, mac_source):
        """With no Bonjour answer, the bare IP beats a UUID nobody can read."""
        with patch('asyncio.create_subprocess_exec', new=self._avahi(
            reverse=self.PRIVATE_REVERSE, forward=self.PRIVATE_FORWARD
        )):
            assert await mac_source._resolve_hostname("192.168.1.173") == "192.168.1.173"

    @pytest.mark.asyncio
    async def test_hostname_is_the_fallback_when_bonjour_is_silent(self, mac_source):
        """A router's lowercased PTR is still recased by the forward lookup."""
        with patch('asyncio.create_subprocess_exec', new=self._avahi(
            reverse="192.168.1.173\tmac-mini-de-leo.home",
            forward="Mac-mini-de-Leo.local\t192.168.1.21",
        )):
            assert await mac_source._resolve_hostname("192.168.1.173") == "Mac-mini-de-Leo"

    @pytest.mark.asyncio
    async def test_unresolvable_sender_keeps_its_ip(self, mac_source):
        with patch('asyncio.create_subprocess_exec', new=self._avahi()):
            assert await mac_source._resolve_hostname("192.168.1.173") == "192.168.1.173"


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
