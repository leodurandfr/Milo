# backend/tests/test_mac_source.py
"""
MacSource's configuration and the name a sender's address resolves to.

Sessions, the journal and roc-recv's lifecycle are driven through the measured
world in test_mac_sessions.py.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.sources.mac.source import MacSource


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
