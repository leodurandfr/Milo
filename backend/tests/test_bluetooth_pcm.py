# backend/tests/test_bluetooth_pcm.py
"""Golden-sample guard for the BlueALSA PCM-event contract.

`bluealsa-cli monitor -p` is a stable programmatic interface, but its event
tokens (`PCMAdded`/`PCMRemoved`) and PCM object-path structure are still
scraped. These tests pin the parser + prefix dispatch against verbatim samples
so a change is a deliberate, reviewed edit — and cover the BlueZ D-Bus
name-resolution fail-open path.
"""
import pytest
from unittest.mock import AsyncMock, Mock

from backend.sources.bluetooth.monitor import (
    BlueAlsaMonitor,
    PCM_ADDED_PREFIX,
    PCM_REMOVED_PREFIX,
)


# --- Golden verbatim bluealsa-cli monitor lines / PCM paths ----------------
A2DP_SOURCE_PATH = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/source"
PCM_ADDED_LINE = f"{PCM_ADDED_PREFIX} {A2DP_SOURCE_PATH}"
PCM_REMOVED_LINE = f"{PCM_REMOVED_PREFIX} {A2DP_SOURCE_PATH}"
# Non-A2DP-source paths that must be rejected
A2DP_SINK_DIRECTION = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/a2dpsnk/sink"
NON_A2DP_PROFILE = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_FF/hfphf/source"
SHORT_PATH = "/org/bluealsa/hci0"


@pytest.fixture
def monitor():
    return BlueAlsaMonitor()


class TestParsePcmPath:
    def test_valid_a2dp_source(self, monitor):
        info = monitor.parse_pcm_path(A2DP_SOURCE_PATH)
        assert info == {
            "address": "AA:BB:CC:DD:EE:FF",
            "path": A2DP_SOURCE_PATH,
            "type": "a2dp-sink",
        }

    @pytest.mark.parametrize("path", [A2DP_SINK_DIRECTION, NON_A2DP_PROFILE, SHORT_PATH, ""])
    def test_rejected_paths(self, monitor, path):
        assert monitor.parse_pcm_path(path) is None


class TestProcessLineDispatch:
    @pytest.mark.asyncio
    async def test_pcm_added_triggers_connect(self, monitor):
        seen = []

        # Both callbacks are awaited by the handlers, and the source passes
        # coroutine functions (`_on_device_connected`/`_on_device_disconnected`).
        # A sync lambda returns None, `await None` raises, and `@handle_errors`
        # logs it away *after* the append — so the assertion held while the
        # handler never reached its end.
        async def _connect(addr, name):
            seen.append(("connect", addr, name))

        async def _disconnect(addr, name):
            seen.append(("disconnect", addr, name))

        monitor.set_callbacks(on_connect=_connect, on_disconnect=_disconnect)
        # Avoid real D-Bus name lookup
        async def _name(addr):
            return "Phone"
        monitor.resolve_device_name = _name

        await monitor._process_line(PCM_ADDED_LINE)

        assert seen == [("connect", "AA:BB:CC:DD:EE:FF", "Phone")]

    @pytest.mark.asyncio
    async def test_pcm_removed_triggers_disconnect(self, monitor):
        seen = []

        async def _connect(addr, name):
            return None

        async def _disconnect(addr, name):
            seen.append(("disconnect", addr, name))

        monitor.set_callbacks(on_connect=_connect, on_disconnect=_disconnect)
        # Pre-populate connected state so removal is honored
        monitor._connected_devices["AA:BB:CC:DD:EE:FF"] = {"name": "Phone"}

        await monitor._process_line(PCM_REMOVED_LINE)

        assert seen == [("disconnect", "AA:BB:CC:DD:EE:FF", "Phone")]

    @pytest.mark.asyncio
    async def test_unrelated_line_ignored(self, monitor):
        # Should not raise; no dispatch
        await monitor._process_line("SomeOtherEvent /org/bluealsa/hci0")


class TestResolveDeviceNameFailOpen:
    @pytest.mark.asyncio
    async def test_no_bus_falls_back(self, monitor):
        # No D-Bus connection (dev machine / BlueZ absent) -> synthetic name.
        assert monitor._bus is None
        name = await monitor.resolve_device_name("AA:BB:CC:DD:EE:FF")
        assert name == "Device AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_returns_dbus_name(self, monitor):
        monitor._bus = object()  # truthy -> lookup path taken

        async def _name(path):
            assert path == "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
            return "Léo's Phone"
        monitor._read_device_name = _name

        assert await monitor.resolve_device_name("aa:bb:cc:dd:ee:ff") == "Léo's Phone"

    @pytest.mark.asyncio
    async def test_lookup_error_falls_back(self, monitor):
        monitor._bus = object()

        async def _boom(path):
            raise RuntimeError("bluez wedged")
        monitor._read_device_name = _boom

        assert await monitor.resolve_device_name("AA:BB:CC:DD:EE:FF") == "Device AA:BB:CC:DD:EE:FF"


class TestFeedDeath:
    """The read loop used to exit through a bare `break`: no log, no return
    code read, no callback. The source stayed "started" with connection
    detection permanently mute — a phone could neither be seen arriving nor
    leaving, and nothing anywhere said why.
    """

    @staticmethod
    def _dead_process(returncode=1, stderr=b""):
        """A bluealsa-cli that closed its output — an empty readline is EOF."""
        proc = AsyncMock()
        proc.stdout = AsyncMock()
        proc.stdout.at_eof = Mock(return_value=False)
        proc.stdout.readline = AsyncMock(return_value=b"")
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(return_value=stderr)
        proc.wait = AsyncMock(return_value=returncode)
        proc.returncode = returncode
        return proc

    @pytest.mark.asyncio
    async def test_eof_is_logged_with_what_the_process_left(self, monitor, caplog):
        monitor._process = self._dead_process(stderr=b"bluealsa: connection refused")
        monitor._alive = True

        await monitor._read_output()

        assert "connection refused" in caplog.text
        assert "exit=1" in caplog.text
        assert monitor.alive is False

    @pytest.mark.asyncio
    async def test_eof_reaches_the_source(self, monitor):
        seen = []

        async def on_lost(reason):
            seen.append(reason)

        monitor.set_callbacks(AsyncMock(), AsyncMock(), on_lost)
        monitor._process = self._dead_process()
        monitor._alive = True

        await monitor._read_output()

        assert len(seen) == 1, "the source was never told the feed died"

    @pytest.mark.asyncio
    async def test_our_own_stop_is_not_a_death(self, monitor, caplog):
        """stop() closes the same stream; that must stay silent."""
        on_lost = AsyncMock()
        monitor.set_callbacks(AsyncMock(), AsyncMock(), on_lost)
        monitor._process = self._dead_process(returncode=0)
        monitor._alive = True
        monitor._stopped = True

        await monitor._read_output()

        on_lost.assert_not_awaited()
        assert "lost" not in caplog.text
