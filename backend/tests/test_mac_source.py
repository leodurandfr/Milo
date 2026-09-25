# backend/tests/test_mac_source.py
"""
MacSource's configuration.

Sessions, the journal, roc-recv's lifecycle and the senders' names are driven
through the measured world in test_mac_sessions.py.
"""
from backend.sources.mac.source import MacSource


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

