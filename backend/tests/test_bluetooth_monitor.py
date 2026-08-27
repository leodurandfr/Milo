# backend/tests/test_bluetooth_monitor.py
"""`sources/bluetooth/monitor.py` — the BlueALSA feed, and its death.

At 39ff9daf this file sat at 65.7%: `stop()` (26 lines), `start()` (11),
`_read_output()` (10) and `_read_device_name()` (9) had none of their bodies
executed. `test_bluetooth_source.py` drives `_process_line` directly, so the
parsing is covered while the thing that *feeds* it had never run.

This is the one source with two feeds and only one of them guaranteed
(CLAUDE.md): BlueALSA answers *who is connected* and decides ACTIVE vs READY,
BlueZ AVRCP answers *what is playing*. So this file is the whole of Bluetooth's
connection detection — and the module's own docstring names what its loss
costs: "no PCMAdded, no PCMRemoved, so a phone can neither be seen arriving nor
leaving", with no automatic restart by design.

Two things live only here:

* **`_report_lost` firing exactly once, with the process's exit status.** It
  used to be a bare `break` — no log, no return code, nothing anywhere to say
  the feed had gone. The recovery gesture is a source restart, which only
  happens if someone is told.
* **`stop()` releasing all three things it holds** — the read task, the
  `bluealsa-cli` child, and the BlueZ bus — in that order. A child left behind
  keeps a `bluealsa-cli monitor` alive per source switch.

Rule 5: `bluealsa-cli` is a real binary on this appliance and the BlueZ system
bus is live. The package-wide `MessageBus` guard from
`test_bluetooth_source.py` is re-declared here (it is per-module, and covering
one module of a package is not covering the package — the B7 lesson), and no
test lets a spawn through.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from backend.sources.bluetooth import (
    adapter as adapter_module,
    agent as agent_module,
    avrcp as avrcp_module,
    monitor as monitor_module,
)
from backend.sources.bluetooth.monitor import BlueAlsaMonitor


@pytest.fixture(autouse=True)
def never_the_real_system_bus(monkeypatch):
    """See `test_bluetooth_source.py`: four modules of this package open a bus,
    and patching one leaves the others reaching the appliance's live BlueZ."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the appliance's real D-Bus system bus")

    for module in (adapter_module, agent_module, avrcp_module, monitor_module):
        monkeypatch.setattr(module, "MessageBus", refuse, raising=False)


@pytest.fixture(autouse=True)
def never_the_real_bluealsa_cli(monkeypatch):
    """`bluealsa-cli` exists on this host and talks to the live daemon.

    Tests that want a monitor process install their own double; anything else
    fails loudly rather than spawning one.
    """
    async def refuse(*args, **_kwargs):
        raise AssertionError(f"a test tried to spawn {args[0]!r} for real")

    monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", refuse)


class FakeStdout:
    """The monitor child's stdout: a queue of lines, then EOF.

    Bounded by construction — `readline()` answers `b""` once the lines run
    out, which is exactly what the daemon going away looks like. A mutation
    that removes the loop's exit therefore reads EOF forever rather than
    growing anything (the B8a lesson: bound the double, not the delay).
    """

    def __init__(self, *lines):
        self._lines = list(lines)
        self._eof = False
        self.reads = 0

    def at_eof(self):
        # A real StreamReader is not at EOF until a read has actually hit it —
        # answering True on an empty queue skips the loop entirely and the
        # "daemon closed its output" arm becomes unreachable.
        return self._eof

    async def readline(self):
        self.reads += 1
        if self.reads > 200:
            raise RunawayRead("readline called 200 times: the loop lost its exit")
        if self._lines:
            return self._lines.pop(0)
        self._eof = True
        return b""


class RunawayRead(BaseException):
    """See `FakeStdout.readline`."""


def child(*lines, stderr=b"", returncode=None):
    proc = MagicMock()
    proc.stdout = FakeStdout(*lines)
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncMock(return_value=stderr)
    proc.returncode = returncode
    proc.terminate = Mock()
    proc.kill = Mock()
    proc.wait = AsyncMock(return_value=0)
    return proc


def monitor(**callbacks):
    mon = BlueAlsaMonitor()
    mon.on_connect = callbacks.get("on_connect", AsyncMock())
    mon.on_disconnect = callbacks.get("on_disconnect", AsyncMock())
    mon.set_callbacks(mon.on_connect, mon.on_disconnect,
                      callbacks.get("on_lost", AsyncMock()))
    mon.on_lost = mon._on_lost
    return mon


class TestStartingTheFeed:
    async def test_the_monitor_child_is_spawned_with_the_documented_argv(
        self, monkeypatch
    ):
        """`bluealsa-cli monitor -p` is the whole feed. `-p` is what makes it
        print PCM events rather than a one-shot listing; without it the source
        starts, reports success, and never sees a phone."""
        spawned = {}
        proc = child()

        async def fake_exec(*args, **kwargs):
            spawned["argv"] = list(args)
            return proc

        monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", fake_exec)
        mon = monitor()

        assert await mon.start() is True

        assert spawned["argv"] == ["bluealsa-cli", "monitor", "-p"]
        await mon.stop()

    async def test_a_bluez_bus_that_will_not_connect_does_not_fail_the_start(
        self, monkeypatch, caplog
    ):
        """Fail open: the bus is only used to turn an address into a readable
        name. A dev host without BlueZ must still run, and on the appliance a
        BlueZ that is slow to come up must not cost the whole feed."""
        proc = child()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", fake_exec)
        mon = monitor()

        with caplog.at_level("WARNING", logger="source.bluetooth.monitor"):
            assert await mon.start() is True

        assert mon._bus is None
        assert "names will fall back" in caplog.text
        await mon.stop()

    async def test_a_child_that_cannot_be_spawned_reports_failure(self, monkeypatch):
        """`_do_start` raises on a False here, which is right: a Bluetooth
        source with no connection detection is a source that can never go
        ACTIVE."""
        async def boom(*args, **kwargs):
            raise FileNotFoundError("bluealsa-cli")

        monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", boom)

        assert await monitor().start() is False

    async def test_starting_clears_the_stopped_flag(self, monkeypatch):
        """`_report_lost` and `_read_output` both return early on it, so a flag
        left over from a previous stop makes the new feed silent."""
        proc = child()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", fake_exec)
        mon = monitor()
        mon._stopped = True

        await mon.start()

        assert mon._stopped is False
        await mon.stop()


class TestReadingTheFeed:
    async def test_every_line_reaches_the_parser(self):
        mon = monitor()
        seen = []
        mon._process = child(b"PCMAdded /org/bluealsa/hci0/dev_AA\n", b"\n",
                             b"PCMRemoved /x\n")
        mon._process_line = AsyncMock(side_effect=lambda ln: seen.append(ln))
        mon._report_lost = AsyncMock()  # the EOF after the last line is not the subject
        mon._alive = True

        await mon._read_output()

        assert seen == ["PCMAdded /org/bluealsa/hci0/dev_AA", "PCMRemoved /x"]

    async def test_a_child_with_no_output_stream_is_reported_lost(self):
        """`create_subprocess_exec` can answer a process whose stdout is None
        under fd exhaustion. Reading it would raise inside a bare task."""
        mon = monitor()
        mon._process = MagicMock(stdout=None)
        mon._alive = True
        reported = []
        mon._report_lost = AsyncMock(side_effect=lambda r: reported.append(r))

        await mon._read_output()

        assert "no output stream" in reported[0]

    async def test_the_daemon_closing_its_output_is_reported_lost(self):
        """EOF means the bluealsa daemon went away. This is the case the
        module's docstring is about: connection detection is dead until the
        source is restarted, and nothing else says so."""
        mon = monitor()
        mon._process = child()
        mon._alive = True
        reported = []
        mon._report_lost = AsyncMock(side_effect=lambda r: reported.append(r))

        await mon._read_output()

        assert "closed its output" in reported[0]

    async def test_a_read_error_is_reported_lost(self):
        mon = monitor()
        mon._process = child()
        mon._process.stdout.readline = AsyncMock(side_effect=OSError("fd gone"))
        mon._alive = True
        reported = []
        mon._report_lost = AsyncMock(side_effect=lambda r: reported.append(r))

        await mon._read_output()

        assert "fd gone" in reported[0]

    async def test_a_read_error_during_our_own_stop_is_not_a_loss(self):
        """`stop()` closes the child under the reader. Reporting that as a lost
        feed puts an error banner up on every ordinary source switch."""
        mon = monitor()
        mon._process = child()
        mon._process.stdout.readline = AsyncMock(side_effect=OSError("closed"))
        mon._stopped = True
        mon._report_lost = AsyncMock()

        await mon._read_output()

        mon._report_lost.assert_not_awaited()

    async def test_a_cancelled_reader_is_not_a_loss(self):
        mon = monitor()
        mon._process = child()
        mon._process.stdout.readline = AsyncMock(side_effect=asyncio.CancelledError)
        mon._alive = True
        mon._report_lost = AsyncMock()

        await mon._read_output()

        mon._report_lost.assert_not_awaited()


class TestReportingTheLoss:
    async def test_the_loss_is_logged_at_error_so_the_banner_shows_it(self, caplog):
        """Error level is what reaches `WebSocketLogHandler` and raises the UI
        banner. At warning this failure is invisible to the owner, and the
        recovery gesture — restart the source — is never asked for."""
        mon = monitor()
        mon._alive = True
        mon._process = None

        with caplog.at_level("ERROR", logger="source.bluetooth.monitor"):
            await mon._report_lost("daemon went away")

        assert "connection detection is down" in caplog.text
        assert "daemon went away" in caplog.text

    async def test_the_childs_exit_status_and_stderr_are_carried(self, caplog):
        """What the process left behind is the only diagnosis there is: the
        feed is gone and the source still reports itself started."""
        mon = monitor()
        mon._alive = True
        mon._process = child(stderr=b"D-Bus connection refused", returncode=1)

        with caplog.at_level("ERROR", logger="source.bluetooth.monitor"):
            await mon._report_lost("closed its output")

        assert "exit=1" in caplog.text
        assert "D-Bus connection refused" in caplog.text

    async def test_a_child_that_will_not_exit_does_not_hold_the_report(self):
        """Bounded on purpose: a monitor that closed stdout without exiting
        must not swallow the report, which is the whole point of the method."""
        mon = monitor()
        mon._alive = True
        mon._process = child()
        mon._process.wait = AsyncMock(side_effect=asyncio.TimeoutError)

        await asyncio.wait_for(mon._report_lost("closed its output"), timeout=5)

    async def test_the_source_is_told_once_and_not_again(self):
        """`_read_output` can reach it from two arms in one death. A second
        banner for the same loss is noise on an appliance whose only Bluetooth
        diagnosis is that banner."""
        told = []
        mon = monitor(on_lost=AsyncMock(side_effect=lambda r: told.append(r)))
        mon._alive = True
        mon._process = None

        await mon._report_lost("first")
        await mon._report_lost("second")

        assert told == ["first"]

    async def test_our_own_stop_reports_nothing(self):
        mon = monitor(on_lost=AsyncMock())
        mon._alive = True
        mon._stopped = True

        await mon._report_lost("closing")

        mon._on_lost.assert_not_awaited()

    async def test_a_callback_that_throws_does_not_escape(self):
        """It runs inside the read task; an exception here dies unobserved and
        skips the rest of the teardown."""
        mon = monitor(on_lost=AsyncMock(side_effect=RuntimeError("state busy")))
        mon._alive = True
        mon._process = None

        await mon._report_lost("gone")

    async def test_a_source_with_no_lost_callback_still_logs(self, caplog):
        mon = BlueAlsaMonitor()
        mon._alive = True
        mon._process = None

        with caplog.at_level("ERROR", logger="source.bluetooth.monitor"):
            await mon._report_lost("gone")

        assert "connection detection is down" in caplog.text


class TestStoppingTheFeed:
    """26 uncovered lines, and it holds three things at once."""

    def _started(self):
        mon = monitor()
        mon._process = child()
        mon._bus = MagicMock()
        mon._alive = True
        mon._connected_devices = {"AA:BB": "Phone"}
        return mon

    async def test_the_read_task_is_cancelled_and_dropped(self):
        mon = self._started()

        async def forever():
            await asyncio.Event().wait()

        mon._read_task = asyncio.create_task(forever())
        task = mon._read_task

        await mon.stop()

        assert task.cancelled()
        assert mon._read_task is None

    async def test_the_monitor_child_is_terminated_and_dropped(self):
        """A `bluealsa-cli monitor` left running per source switch accumulates
        against the same daemon."""
        mon = self._started()
        proc = mon._process

        await mon.stop()

        proc.terminate.assert_called_once()
        assert mon._process is None

    async def test_a_child_that_ignores_terminate_is_killed(self):
        """`bluealsa-cli` blocked on a D-Bus call does not act on SIGTERM. The
        bound is what stops the source switch waiting on it."""
        mon = self._started()
        proc = mon._process
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, 0])

        await mon.stop()

        proc.kill.assert_called_once()

    async def test_a_child_that_already_exited_is_not_signalled(self):
        """`terminate()` on a reaped process raises ProcessLookupError, and the
        feed dying on its own is the ordinary case here."""
        mon = self._started()
        mon._process.returncode = 0
        proc = mon._process

        await mon.stop()

        proc.terminate.assert_not_called()

    async def test_a_terminate_that_races_the_exit_is_absorbed(self):
        mon = self._started()
        mon._process.terminate = Mock(side_effect=ProcessLookupError)

        await mon.stop()

        assert mon._process is None

    async def test_an_unexpected_failure_still_drops_the_child(self, caplog):
        """The `finally` is what makes the next `start()` clean; without it a
        failed stop leaves a reference that the next start would abandon."""
        mon = self._started()
        mon._process.terminate = Mock(side_effect=RuntimeError("signal refused"))

        with caplog.at_level("ERROR", logger="source.bluetooth.monitor"):
            await mon.stop()

        assert mon._process is None
        assert "signal refused" in caplog.text

    async def test_the_bluez_bus_is_disconnected_and_dropped(self):
        """It is a live system-bus connection; one leaks per source switch."""
        mon = self._started()
        bus = mon._bus

        await mon.stop()

        bus.disconnect.assert_called_once()
        assert mon._bus is None

    async def test_a_bus_that_will_not_disconnect_is_absorbed(self):
        mon = self._started()
        mon._bus.disconnect = Mock(side_effect=RuntimeError("already gone"))

        await mon.stop()

        assert mon._bus is None

    async def test_the_known_devices_are_forgotten(self):
        """They are what `_process_line` matches a PCMRemoved against; kept,
        a phone that disconnected while stopped is never seen leaving."""
        mon = self._started()

        await mon.stop()

        assert mon._connected_devices == {}

    async def test_the_stopped_flag_is_set_before_anything_is_torn_down(self):
        """The reader tests it to tell our own stop from a lost feed. Set after
        the cancel, every ordinary source switch raises the "feed lost" banner.
        """
        mon = self._started()
        seen = {}

        async def watching():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seen["stopped"] = mon._stopped
                raise

        mon._read_task = asyncio.create_task(watching())
        await asyncio.sleep(0)

        await mon.stop()

        assert seen["stopped"] is True

    async def test_stopping_a_monitor_that_never_started_is_harmless(self):
        """`_do_start` calls it on the failure path, and `_cleanup` on every
        stop whether the feed came up or not."""
        await BlueAlsaMonitor().stop()


class TestNamingADevice:
    """`_read_device_name` — what turns `AA:BB:CC:DD:EE:FF` into "iPhone de Léo"."""

    def _bus_answering(self, **props):
        variants = {k: MagicMock(value=v) for k, v in props.items()}
        interface = MagicMock()
        interface.call_get = AsyncMock(side_effect=lambda _iface, prop: variants[prop])
        obj = MagicMock()
        obj.get_interface = Mock(return_value=interface)
        bus = MagicMock()
        bus.introspect = AsyncMock()
        bus.get_proxy_object = Mock(return_value=obj)
        return bus

    async def test_the_alias_is_preferred_over_the_name(self):
        """`Alias` is the name the owner set on the phone; `Name` is what the
        device advertises. The card shows what the owner recognises."""
        mon = monitor()
        mon._bus = self._bus_answering(Alias="iPhone de Léo", Name="iPhone")

        assert await mon._read_device_name("/org/bluez/hci0/dev_AA") == "iPhone de Léo"

    async def test_the_name_is_the_fallback(self):
        mon = monitor()
        mon._bus = self._bus_answering(Alias=None, Name="iPhone")

        assert await mon._read_device_name("/org/bluez/hci0/dev_AA") == "iPhone"

    async def test_a_property_bluez_refuses_is_skipped(self):
        """BlueZ answers `org.freedesktop.DBus.Error.InvalidArgs` for a
        property a device does not carry; that must fall through to the next
        one, not abandon the lookup."""
        mon = monitor()
        bus = self._bus_answering(Name="iPhone")
        interface = bus.get_proxy_object.return_value.get_interface.return_value
        interface.call_get = AsyncMock(
            side_effect=lambda _iface, prop: (_ for _ in ()).throw(RuntimeError("InvalidArgs"))
            if prop == "Alias" else MagicMock(value="iPhone")
        )
        mon._bus = bus

        assert await mon._read_device_name("/org/bluez/hci0/dev_AA") == "iPhone"

    async def test_a_device_bluez_knows_nothing_about_answers_none(self):
        mon = monitor()
        mon._bus = self._bus_answering(Alias=None, Name=None)

        assert await mon._read_device_name("/org/bluez/hci0/dev_AA") is None

    async def test_a_bus_with_no_name_falls_back_to_the_address(self):
        """The card must show something; a phone with no resolvable name is
        still a connected phone."""
        mon = monitor()
        mon._bus = None

        assert await mon.resolve_device_name("AA:BB:CC:DD:EE:FF") == (
            "Device AA:BB:CC:DD:EE:FF"
        )

    async def test_a_bluez_that_hangs_falls_back_to_the_address(self):
        """The resolution runs on the connect path; unbounded it holds the card
        blank for as long as BlueZ stays silent."""
        mon = monitor()
        mon._bus = MagicMock()

        async def never(_path):
            await asyncio.Event().wait()

        mon._read_device_name = never

        with patch.object(monitor_module.asyncio, "wait_for",
                          AsyncMock(side_effect=asyncio.TimeoutError)):
            name = await mon.resolve_device_name("AA:BB:CC:DD:EE:FF")

        assert name == "Device AA:BB:CC:DD:EE:FF"

    async def test_a_bluez_that_raises_falls_back_to_the_address(self):
        mon = monitor()
        mon._bus = MagicMock()
        mon._read_device_name = AsyncMock(side_effect=RuntimeError("no such object"))

        assert await mon.resolve_device_name("AA:BB:CC:DD:EE:FF") == (
            "Device AA:BB:CC:DD:EE:FF"
        )

    async def test_a_resolved_name_is_what_the_card_shows(self):
        mon = monitor()
        mon._bus = MagicMock()
        mon._read_device_name = AsyncMock(return_value="iPhone de Léo")

        assert await mon.resolve_device_name("AA:BB:CC:DD:EE:FF") == "iPhone de Léo"
