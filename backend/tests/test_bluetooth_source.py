# backend/tests/test_bluetooth_source.py
"""
BluetoothSource's configuration, and the pairing agent's identity. (The
BlueALSA PCM path parser is pinned in test_bluetooth_pcm.py.)

The source's behaviour — lifecycle, exposure, the link, the player — is driven
through the measured world in test_bluetooth_behavior.py and
test_bluetooth_sessions.py.
"""
import pytest

from backend.sources.bluetooth.source import BluetoothSource
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth import (
    adapter as adapter_module,
    agent as agent_module,
    avrcp as avrcp_module,
    monitor as monitor_module,
)


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
