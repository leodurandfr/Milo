"""The CamillaDSP client's own lifecycle: connect, reconnect, disconnect, tear down.

`CamillaDSPService` is the appliance's *only* attenuation stage — the card's own
mixer is pinned at unity by `milo-alsa-passthrough` — so everything in this file
sits between a daemon restart and the level in the room. None of it had ever run:
the suite injected `_client` by hand and started from `_connected = True`, which
skips the whole connect path, the reconnection loop, the teardown and the status
read.

What that left unmeasured, in order of what it costs:

* the reconnect is the only thing that re-pushes volume and EQ after CamillaDSP
  restarts (it is `PartOf=milo-backend.service`, so it follows every backend
  restart). `_restore_after_reconnect` calls the volume callback *first*, by
  design — a restarted daemon is at its own default gain until something tells it
  otherwise.
* `_connection_ready` is what `VolumeService._apply_startup_volume` waits on for
  10 s at every boot. Only the *loop* sets it on a failed connect; a connect that
  fails without the loop noticing costs ten seconds of the boot and then applies
  no volume at all.
* `_connect_once` clearing `_client` on failure is what every `if not self._client`
  guard in the file relies on.

The `never_the_real_camilladsp` fixture below is the reason this file can exist
at all: the daemon answers on 127.0.0.1:1234 on this machine, and the suite's
network guard lets loopback through on purpose.
"""
import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from backend.core.equalizer.service import CamillaDSPService, CamillaDspState
from backend.shared.persistence import SchemaVersionMismatch


class ReachedTheLiveDaemon(BaseException):
    """Raised when a double was bypassed and the real daemon was about to be built.

    Derived from `BaseException`, not `Exception`: `_connect_once` wraps the
    construction in `except Exception` and answers False, so an ordinary error
    here would be swallowed and the run would stay green with the guard breached.
    """


@pytest.fixture(autouse=True)
def never_the_real_camilladsp(monkeypatch):
    """Make the real `CamillaClient` impossible to build for the whole file.

    This checkout *is* the appliance and CamillaDSP is listening on
    127.0.0.1:1234 right now. `conftest.keep_the_suite_off_the_network` refuses
    connects *off this host* only — loopback is deliberately left open (mpv, the
    Tidal socket, D-Bus all need it) — so nothing else stands between a test that
    loses its double and the live attenuation stage. Reaching it would not merely
    read: `_restore_after_reconnect` pushes volume, EQ, compressor, loudness and
    mono into the daemon that is playing in the room.

    Fail the access rather than spy on it, the same way `test_wifi_adoption` does
    with nmcli.
    """
    import camilladsp

    def _refuse(*args, **kwargs):
        raise ReachedTheLiveDaemon(
            f"a test built the real CamillaClient{args!r} — it would have "
            "connected to the daemon driving this room"
        )

    monkeypatch.setattr(camilladsp, "CamillaClient", _refuse)


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A service whose persistence points at `tmp_path`, never at the operator's EQ.

    `_load_saved_config` READS `STORAGE_PATH` and `cleanup` WRITES it. The write
    is refused by the appliance-data guard, but the read is not — pointed at the
    real file the tests would inherit whatever curve the owner last dialled in.
    """
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_path / "equalizer.json")
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    settings.set_setting = AsyncMock()
    svc = CamillaDSPService(settings_service=settings)
    svc.state_machine = Mock()
    svc.state_machine.broadcast = AsyncMock()
    return svc


@pytest.fixture
def client_factory(monkeypatch):
    """Install a `CamillaClient` double and hand back the calls it recorded.

    Returns `(built, client)`: `built` collects one `(host, port)` per
    construction, so a test can state that a second connect did NOT build a
    second client — which is what the `_connected` early return is for.
    """
    import camilladsp

    built: list = []
    client = MagicMock()
    client.general.state.return_value = "Running"
    client.volume.main_volume.return_value = -20.0
    client.volume.main_mute.return_value = False
    client.levels.capture_peak.return_value = [-30.0, -30.0]
    client.levels.playback_peak.return_value = [-25.0, -25.0]

    def _build(host, port):
        built.append((host, port))
        return client

    monkeypatch.setattr(camilladsp, "CamillaClient", _build)
    return built, client


class TestConnectOnce:
    """One connection attempt: what it publishes, and what it leaves behind when it fails."""

    async def test_a_successful_connect_addresses_the_configured_daemon(
        self, service, client_factory
    ):
        """host/port come from the service, not from pycamilladsp's own defaults.

        A satellite runs its own daemon; the server addresses only its own. A
        connect that ignored the constructor arguments would still succeed here,
        on the loopback daemon — which is the accident this asserts against.
        """
        built, _ = client_factory
        service.host, service.port = "10.0.0.9", 4321

        assert await service._connect_once() is True

        assert built == [("10.0.0.9", 4321)]

    async def test_a_successful_connect_reads_the_daemon_state_back(
        self, service, client_factory
    ):
        """`is_volume_control_available()` gates every volume write on `_state`.

        Left at DISCONNECTED after a successful connect, the service is connected
        and refuses to change the level: the knob turns and nothing happens.
        """
        _, client = client_factory
        client.general.state.return_value = "Paused"

        await service._connect_once()

        assert service.connected is True
        assert service.state is CamillaDspState.PAUSED
        assert service.is_volume_control_available() is True

    async def test_a_successful_connect_releases_the_startup_waiter(
        self, service, client_factory
    ):
        """`VolumeService._apply_startup_volume` blocks on this event for 10 s.

        It is the boot's rendezvous: without the set, startup volume is applied
        ten seconds late at best, and skipped at worst.
        """
        assert not service._connection_ready.is_set()

        await service._connect_once()

        assert service._connection_ready.is_set()
        assert await service.wait_for_connection(timeout=0.01) is True

    async def test_a_successful_connect_announces_the_state_it_found(
        self, service, client_factory
    ):
        """The frontend's equalizer view reads `equalizer/state_changed`.

        Without the broadcast the UI keeps showing "disconnected" over a daemon
        that came back, and the EQ controls stay greyed out until something else
        happens to emit.
        """
        _, client = client_factory
        client.general.state.return_value = "Inactive"

        await service._connect_once()

        event = service.state_machine.broadcast.await_args.args[0]
        assert event.CATEGORY == "equalizer"
        assert event.TYPE == "state_changed"
        assert event.state == "inactive"

    async def test_an_already_connected_service_does_not_build_a_second_client(
        self, service, client_factory
    ):
        """Four callers can race here — the loop, `connect()`, and two route paths.

        A second client would replace a live one mid-command; the first one's
        pending executor call then writes through a socket nobody owns.
        """
        built, _ = client_factory
        await service._connect_once()

        assert await service._connect_once() is True

        assert len(built) == 1

    async def test_a_refused_connect_drops_the_client_it_half_built(self, service, monkeypatch):
        """Every guard in the service is `if not self._client`.

        `CamillaClient(...)` succeeds before `connect()` is attempted, so a
        failure leaves a live-looking handle on a socket that was never opened.
        Kept, `_run` would hand it commands forever and each one would raise.
        """
        import camilladsp

        client = MagicMock()
        client.connect.side_effect = OSError("connection refused")
        monkeypatch.setattr(camilladsp, "CamillaClient", lambda host, port: client)

        assert await service._connect_once() is False

        assert service._client is None
        assert service.connected is False
        assert service.state is CamillaDspState.DISCONNECTED

    async def test_a_refused_connect_leaves_the_startup_waiter_armed(self, service, monkeypatch):
        """The failed attempt does NOT release the waiter — the loop does, later.

        This is the division of labour between `_connect_once` and
        `_connection_loop`, and the reason the loop has its own `is_set()` check.
        Setting it here would tell `_apply_startup_volume` the daemon is ready
        when it is not, and the startup volume would be pushed into nothing.
        """
        import camilladsp

        client = MagicMock()
        client.connect.side_effect = OSError("connection refused")
        monkeypatch.setattr(camilladsp, "CamillaClient", lambda host, port: client)

        await service._connect_once()

        assert not service._connection_ready.is_set()

    async def test_a_missing_pycamilladsp_is_reported_and_not_retried_blindly(
        self, service, monkeypatch, caplog
    ):
        """The import lives inside the function so a host without the wheel still boots.

        Reported at error because on the appliance the package is always there:
        seeing this line means the venv is broken, and no amount of reconnecting
        will fix it.
        """
        import builtins

        real_import = builtins.__import__

        def _no_camilladsp(name, *args, **kwargs):
            if name == "camilladsp":
                raise ImportError("No module named 'camilladsp'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_camilladsp)

        with caplog.at_level(logging.ERROR):
            assert await service._connect_once() is False

        assert "pycamilladsp not installed" in caplog.text

    async def test_connect_is_the_public_name_for_one_attempt(self, service, client_factory):
        """`AudioRoutingService` calls `connect()` at startup, not the private twin."""
        built, _ = client_factory

        assert await service.connect() is True

        assert len(built) == 1


class TestConnectionLoop:
    """The reconnection loop: back-off, the idle wait, and who releases the boot."""

    @pytest.fixture
    def sleeps(self, monkeypatch):
        """Record every sleep the loop asks for and return immediately.

        The real delays are 5 s growing to 30 s. Recorded rather than skipped
        because the back-off *is* the behaviour under test: a loop that lost its
        growth would hammer a dead daemon at 5 s forever, and a loop that lost
        its cap would take half a minute to notice one that came back.
        """
        recorded: list = []
        real_sleep = asyncio.sleep

        async def _sleep(delay, *args, **kwargs):
            recorded.append(delay)
            return await real_sleep(0)

        monkeypatch.setattr("backend.core.equalizer.service.asyncio.sleep", _sleep)
        return recorded

    @staticmethod
    def _stop_after(service, sleeps_wanted):
        """Fail every connection, and stop the loop after `sleeps_wanted` back-offs.

        The flag is cleared on the attempt AFTER the last one we want to sleep
        for, because `_connection_loop` re-reads `self._running` between the
        attempt and its sleep — clearing it on attempt N drops N's own back-off.

        Bounded on purpose: `while self._running` against a double that never
        says stop is the shape that exhausted this machine's memory during B8a.
        A `StopIteration` would be swallowed by the loop's own `except
        Exception`, so the counter flips `_running` instead.
        """
        calls = {"n": 0}

        async def _attempt():
            calls["n"] += 1
            if calls["n"] > sleeps_wanted:
                service._running = False
            return False

        return _attempt, calls

    async def test_a_dead_daemon_releases_the_boot_instead_of_holding_it(
        self, service, sleeps
    ):
        """The loop sets `_connection_ready` when the connect failed.

        This is the whole reason `_apply_startup_volume` finishes on a unit whose
        CamillaDSP is down: the waiter is released with `_connected` still False,
        so it logs "not connected after 10s" and moves on instead of the boot
        stalling. Losing it costs ten seconds of every degraded boot.
        """
        attempt, _ = self._stop_after(service, 1)
        service._connect_once = attempt

        await service._connection_loop()

        assert service._connection_ready.is_set()
        assert service.connected is False

    async def test_the_back_off_grows_between_attempts(self, service, sleeps):
        """5 s, then 7.5 s, then 11.25 s — the daemon is `Restart=always`.

        A flat retry is not harmless here: every attempt builds a client and
        opens a socket, and the loop is running for the whole life of the process.
        """
        attempt, _ = self._stop_after(service, 3)
        service._connect_once = attempt

        await service._connection_loop()

        assert sleeps == [
            service.RECONNECT_DELAY,
            service.RECONNECT_DELAY * 1.5,
            service.RECONNECT_DELAY * 1.5 * 1.5,
        ]

    async def test_the_back_off_stops_growing_at_the_cap(self, service, sleeps):
        """Without the cap the delay passes a minute after ~20 minutes down.

        CamillaDSP comes back with the backend it is `PartOf=`; a client that has
        drifted to a multi-minute wait leaves the room silent long after the
        daemon is up.
        """
        service.RECONNECT_DELAY = service.MAX_RECONNECT_DELAY
        attempt, _ = self._stop_after(service, 3)
        service._connect_once = attempt

        await service._connection_loop()

        assert sleeps == [service.MAX_RECONNECT_DELAY] * 3

    async def test_a_reconnected_daemon_gets_its_state_pushed_back(self, service, sleeps):
        """`_restore_after_reconnect` is the only re-push after a daemon restart.

        Skipped, the daemon keeps whatever pipeline it restarted with — its own
        defaults — while Milō and the UI still report the user's settings. The
        volume callback inside it is what stands between that and a room at the
        daemon's default gain.
        """
        restored = []
        service._restore_after_reconnect = AsyncMock(side_effect=lambda: restored.append(1))

        async def _connect():
            service._connected = True
            service._running = False
            return True

        service._connect_once = _connect

        await service._connection_loop()

        assert restored == [1]

    async def test_the_back_off_resets_once_a_connection_lands(self, service, sleeps):
        """A daemon that flaps must not inherit the delay grown while it was gone.

        Two failures push the delay to 11.25 s; the connect that follows brings
        it back to 5 s, so the *next* drop is noticed in five seconds and not in
        eleven.
        """
        service._restore_after_reconnect = AsyncMock()
        outcomes = [False, False, True, False]
        calls = {"n": 0}

        async def _attempt():
            calls["n"] += 1
            if calls["n"] > len(outcomes):
                service._running = False
                return False
            return outcomes[calls["n"] - 1]

        service._connect_once = _attempt

        await service._connection_loop()

        assert sleeps == [5.0, 7.5, 5.0, 7.5]

    async def test_a_cancelled_loop_stops_without_sleeping_again(self, service, sleeps):
        """Teardown cancels this task; the `break` is what lets `cleanup` join it.

        Swallowed into the generic `except Exception` instead, the loop would
        answer a cancel with another back-off sleep and `cleanup` would block
        until systemd's 10 s TimeoutStopSec killed the process.

        The counter is not decoration: a double that raises forever turns the
        `break`-less version of this loop into a busy spin rather than a failure,
        which is the shape that took this machine down in B8a. Bound every path
        the mutation can open, not only the one the passing test walks.
        """
        calls = {"n": 0}

        async def _cancelled():
            calls["n"] += 1
            if calls["n"] >= 3:
                service._running = False
            raise asyncio.CancelledError

        service._connect_once = _cancelled

        await service._connection_loop()

        assert calls["n"] == 1
        assert sleeps == []

    async def test_a_raising_attempt_does_not_kill_the_loop(self, service, sleeps, caplog):
        """A loop body that dies stops reconnecting for the life of the process.

        Nothing restarts it: `initialize` spawns it once. The room would then
        stay on whatever the daemon holds until the backend is restarted by hand.
        """
        calls = {"n": 0}

        async def _boom():
            calls["n"] += 1
            if calls["n"] >= 2:
                service._running = False
            raise RuntimeError("socket exploded")

        service._connect_once = _boom

        with caplog.at_level(logging.ERROR):
            await service._connection_loop()

        assert calls["n"] == 2
        assert "Connection loop error" in caplog.text

    async def test_the_idle_wait_ends_when_a_command_marks_us_disconnected(
        self, service, sleeps
    ):
        """`_run` clears `_connected` on the first failed command; this is the pickup.

        There is no other signal — pycamilladsp does not push a close event — so
        the inner idle loop polling `_connected` is the entire detection path for
        a daemon that went away mid-session.
        """
        service._restore_after_reconnect = AsyncMock()
        ticks = {"n": 0}
        real_sleep = asyncio.sleep

        async def _sleep(delay, *args, **kwargs):
            sleeps.append(delay)
            ticks["n"] += 1
            if ticks["n"] == 2:
                service._connected = False
            if ticks["n"] >= 3:
                service._running = False
            return await real_sleep(0)

        import backend.core.equalizer.service as mod
        mod.asyncio.sleep = _sleep
        try:
            async def _connect():
                service._connected = True
                return True

            service._connect_once = _connect

            await service._connection_loop()
        finally:
            mod.asyncio.sleep = real_sleep

        assert ticks["n"] == 3


class TestInitialize:
    """Startup: what must happen before the loop is allowed to run."""

    async def test_initialize_loads_the_saved_curve_before_connecting(
        self, service, monkeypatch
    ):
        """Order matters: the loop's first success calls `_restore_after_reconnect`.

        Connect first and the daemon is handed the in-memory defaults — a flat
        EQ, no mono — and the user's saved curve is then overwritten by the
        persist that follows. The saved state has to be in the cache before the
        connection loop can push anything.
        """
        order: list = []
        service._load_saved_config = AsyncMock(side_effect=lambda: order.append("load"))

        async def _loop():
            order.append("loop")

        service._connection_loop = _loop

        assert await service.initialize() is True
        await service._reconnect_task

        assert order == ["load", "loop"]

    async def test_a_schema_mismatch_is_re_raised_for_the_boot_banner(self, service):
        """`dependencies.py::init_async` turns this into the banner + SystemExit(1).

        Swallowed into the generic arm below it, the boot would continue with the
        in-memory defaults and the very next persist would overwrite the file the
        operator was told to inspect.
        """
        service._load_saved_config = AsyncMock(
            side_effect=SchemaVersionMismatch("equalizer.json", 1, 2)
        )

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    async def test_a_schema_mismatch_still_releases_the_startup_waiter(self, service):
        """The boot is dying, but it must not hang on the way out.

        `VolumeService` is in the same `asyncio.gather`; left waiting it holds
        the whole init for its 10 s timeout before the banner can be printed.
        """
        service._load_saved_config = AsyncMock(
            side_effect=SchemaVersionMismatch("equalizer.json", 1, 2)
        )

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

        assert service._connection_ready.is_set()

    async def test_any_other_failure_gives_up_without_blocking_the_boot(
        self, service, caplog
    ):
        """Fail open: the appliance boots without CamillaDSP, silent but alive.

        The `_connection_ready.set()` here is the same rendezvous as above — the
        difference from the schema arm is that this one answers False instead of
        re-raising, so the rest of the services carry on.
        """
        service._load_saved_config = AsyncMock(side_effect=RuntimeError("disk on fire"))

        with caplog.at_level(logging.ERROR):
            assert await service.initialize() is False

        assert service._connection_ready.is_set()
        assert "Error initializing CamillaDSP service" in caplog.text


class TestWaitForConnection:
    """The boot rendezvous, read from the waiting side."""

    async def test_an_already_connected_service_answers_without_waiting(self, service):
        """The fast path exists because the event may have been consumed at boot."""
        service._connected = True

        assert await service.wait_for_connection(timeout=0.01) is True

    async def test_a_waiter_that_times_out_answers_false_rather_than_raising(
        self, service, caplog
    ):
        """`_apply_startup_volume` reads the boolean; a raise would abort the whole
        volume init and leave the local client with no level applied at all."""
        with caplog.at_level(logging.WARNING):
            assert await service.wait_for_connection(timeout=0.01) is False

        assert "connection wait timed out" in caplog.text

    async def test_a_released_waiter_still_reports_the_real_connection_state(self, service):
        """The loop sets the event on FAILURE too, so the event alone means nothing.

        Reading the event instead of `_connected` is how a dead daemon would be
        reported as ready, and the startup volume pushed into a service with no
        client behind it.
        """
        service._connection_ready.set()

        assert await service.wait_for_connection(timeout=0.01) is False


class TestDisconnect:
    """Letting go of the daemon, including when it will not let go back."""

    async def test_disconnect_clears_the_handle_and_announces_it(self, service, client_factory):
        _, client = client_factory
        await service._connect_once()
        service.state_machine.broadcast.reset_mock()

        await service.disconnect()

        client.disconnect.assert_called_once()
        assert service._client is None
        assert service.connected is False
        assert service.state is CamillaDspState.DISCONNECTED
        assert service.state_machine.broadcast.await_args.args[0].state == "disconnected"

    async def test_a_daemon_that_refuses_to_hang_up_is_dropped_anyway(
        self, service, client_factory, caplog
    ):
        """A dying daemon often errors on `disconnect` — that is the normal case.

        Kept `_connected` on the way out, the next `_connect_once` takes its
        early return and the service is wedged on a dead socket with no path back.
        """
        _, client = client_factory
        client.disconnect.side_effect = OSError("broken pipe")
        await service._connect_once()

        with caplog.at_level(logging.WARNING):
            await service.disconnect()

        assert service._client is None
        assert service.connected is False
        assert "Error disconnecting from CamillaDSP" in caplog.text


class TestDaemonState:
    """Mapping pycamilladsp's ProcessingState onto ours."""

    @pytest.mark.parametrize("reported,expected", [
        ("ProcessingState.RUNNING", CamillaDspState.RUNNING),
        ("ProcessingState.PAUSED", CamillaDspState.PAUSED),
        ("ProcessingState.INACTIVE", CamillaDspState.INACTIVE),
    ])
    async def test_each_processing_state_maps_to_its_own(
        self, service, client_factory, reported, expected
    ):
        """PAUSED is the one that matters: it is the silence-pause failure mode,
        and `is_volume_control_available` must still say yes in it."""
        _, client = client_factory
        client.general.state.return_value = reported
        service._client = client
        service._connected = True

        assert await service._get_daemon_state() is expected

    async def test_an_unknown_state_is_read_as_inactive_not_disconnected(
        self, service, client_factory
    ):
        """Fail open: a pycamilladsp release that adds a state must not make the
        volume control disappear. INACTIVE keeps `is_volume_control_available`
        true; DISCONNECTED would refuse every write."""
        _, client = client_factory
        client.general.state.return_value = "ProcessingState.STARTING"
        service._client = client
        service._connected = True

        service._state = await service._get_daemon_state()

        assert service._state is CamillaDspState.INACTIVE
        assert service.is_volume_control_available() is True

    async def test_no_client_means_disconnected(self, service):
        assert await service._get_daemon_state() is CamillaDspState.DISCONNECTED

    async def test_a_daemon_that_raises_answers_disconnected(self, service, client_factory):
        """`@handle_errors(default=DISCONNECTED)` — the read is on the connect path,
        so a raise here must not take the connection down with it."""
        _, client = client_factory
        client.general.state.side_effect = OSError("gone")
        service._client = client
        service._connected = True

        assert await service._get_daemon_state() is CamillaDspState.DISCONNECTED


class TestGetStatus:
    """`GET /api/equalizer/status` — the payload the settings screen reads."""

    async def test_a_disconnected_service_says_so_without_touching_a_client(self, service):
        status = await service.get_status()

        assert status == {
            "available": False,
            "state": "disconnected",
            "message": "CamillaDSP not connected",
        }

    async def test_a_connected_status_carries_the_whole_dsp_snapshot(
        self, service, client_factory
    ):
        """One request answers the screen; a missing key renders an empty control.

        The sub-reads are real calls into the service, so a status that stopped
        asking the daemon would show the cache instead of the truth.
        """
        _, client = client_factory
        await service._connect_once()

        status = await service.get_status()

        assert status["available"] is True
        assert status["state"] == "running"
        assert status["host"] == service.host
        assert status["port"] == service.port
        assert len(status["filters"]) == 10
        assert status["compressor"] == service._compressor
        assert status["loudness"] == service._loudness
        assert status["mono"] is False
        assert status["volume"] == {"main": -20.0, "mute": False}

    async def test_the_sample_rate_is_read_only_while_the_daemon_is_running(
        self, service, client_factory
    ):
        """`rate.capture()` answers nothing useful on an inactive daemon.

        Asked anyway it is one more round-trip per status poll for a value the
        screen would render as a stale rate from the previous stream.
        """
        _, client = client_factory
        client.rate.capture.return_value = 44100
        await service._connect_once()

        running = await service.get_status()

        client.general.state.return_value = "ProcessingState.INACTIVE"
        inactive = await service.get_status()

        assert running["sample_rate"] == 44100
        assert "sample_rate" not in inactive
        client.rate.capture.assert_called_once()

    async def test_a_failed_rate_probe_does_not_cost_the_rest_of_the_status(
        self, service, client_factory
    ):
        """Debug-level on purpose: it is the one optional field of the payload.

        Escalated to the outer `except`, a daemon that dropped the rate query
        would render the whole EQ screen as unavailable.
        """
        _, client = client_factory
        client.rate.capture.side_effect = OSError("no rate")
        await service._connect_once()

        status = await service.get_status()

        assert status["available"] is True
        assert "sample_rate" not in status

    async def test_a_status_that_blew_up_reports_unavailable_with_the_reason(
        self, service, client_factory
    ):
        """The route returns HTTP 200 with the error inside (resilience pattern),
        so this dict is what the screen renders — the reason has to be in it."""
        _, client = client_factory
        await service._connect_once()
        service.get_filters = AsyncMock(side_effect=RuntimeError("graph unreadable"))

        status = await service.get_status()

        assert status["available"] is False
        assert status["state"] == "disconnected"
        assert "graph unreadable" in status["error"]


class TestCleanup:
    """Teardown, executed rather than read.

    `test_service_wiring::test_task_set_owners_drain_where_they_tear_down` already
    turns a mutation of this method red — but it parses the source and runs
    nothing, so all thirteen lines below stayed at zero coverage while looking
    protected. That AST rule proves `cancel_all()` is *written* here; these prove
    it is *reached*, and in the order the shutdown depends on.
    """

    async def test_cleanup_flushes_a_pending_persist_before_dropping_it(
        self, service, tmp_path
    ):
        """A 3 s EQ drag leaves one debounced write armed ~1 s out.

        Cancelled without the flush, the last drag is lost on every restart —
        the band the user just moved springs back on the next boot.
        """
        service._filters[0]["gain"] = 7.5
        service.schedule_persist()

        await service.cleanup()

        assert (tmp_path / "equalizer.json").exists()
        import json
        saved = json.loads((tmp_path / "equalizer.json").read_text())
        assert saved["filters"][0]["gain"] == 7.5

    async def test_cleanup_drains_the_task_set_it_owns(self, service):
        """`BackgroundTaskSet` outlives the service without this.

        The debounced persist lives in it, and a task still holding
        `STORAGE_PATH` while the process exits is how a truncated write happens.
        """
        drained = []
        service._bg.cancel_all = AsyncMock(side_effect=lambda: drained.append(1))

        await service.cleanup()

        assert drained == [1]

    async def test_cleanup_stops_the_connection_loop_and_joins_it(self, service):
        """`_running = False` alone is not enough — the loop may be inside a 30 s
        sleep. The cancel is what makes teardown fit inside systemd's 10 s stop
        timeout; without the await, the process can exit mid-reconnect."""
        started = asyncio.Event()

        async def _loop():
            started.set()
            await asyncio.sleep(3600)

        service._reconnect_task = asyncio.create_task(_loop())
        await started.wait()

        await service.cleanup()

        assert service._running is False
        assert service._reconnect_task.done()

    async def test_cleanup_lets_go_of_the_daemon(self, service, client_factory):
        """The daemon survives the backend (`PartOf=`, so it restarts with it).

        A socket left open by an exiting process is held until the kernel reaps
        it, and CamillaDSP refuses a second control connection while the first
        is alive — the restarted backend then cannot take its own DSP back.
        """
        _, client = client_factory
        await service._connect_once()

        await service.cleanup()

        client.disconnect.assert_called_once()
        assert service._client is None

    async def test_cleanup_shuts_down_the_executor(self, service):
        """The executor is a real thread pool; one per service instance.

        Left running it keeps a non-daemon thread alive and the interpreter with
        it, which is the difference between a clean stop and systemd's SIGKILL.
        """
        await service.cleanup()

        with pytest.raises(RuntimeError):
            service._executor.submit(lambda: None)


class TestPersistState:
    """What actually reaches `equalizer.json`."""

    async def test_the_persisted_record_carries_the_whole_live_cache(
        self, service, tmp_path
    ):
        """One file holds the local client's entire EQ; a field missing from this
        dict is a setting that silently resets at the next boot."""
        import json

        service._active_preset = "rock"
        service._mono = True
        service._custom_gains = [1.0] * 10
        service._compressor["enabled"] = True
        service._loudness["enabled"] = True
        service._filters[0]["gain"] = -3.5

        await service.persist_state()

        saved = json.loads((tmp_path / "equalizer.json").read_text())
        assert saved["schema_version"] == service.SCHEMA_VERSION
        assert saved["active_preset"] == "rock"
        assert saved["mono"] is True
        assert saved["custom_gains"] == [1.0] * 10
        assert saved["compressor"]["enabled"] is True
        assert saved["loudness"]["enabled"] is True
        assert saved["filters"][0]["gain"] == -3.5
        assert "timestamp" in saved

    async def test_persist_state_cancels_a_pending_debounce_first(self, service):
        """Otherwise the deliberate write (a preset load) is followed a second
        later by the debounced one, which snapshots the same cache again — two
        fsyncs of the same bytes, and a race on the temp file name."""
        service.schedule_persist()
        pending = service._persist_debounce_task

        await service.persist_state()

        assert pending.cancelled() or pending.done()

    async def test_a_write_that_fails_is_logged_and_does_not_propagate(
        self, service, monkeypatch, caplog
    ):
        """Every caller is a UI gesture; a raise here would turn a band drag into
        an HTTP 500 over a purely cosmetic durability failure."""
        async def _boom(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(
            "backend.core.equalizer.service.save_versioned_json", _boom
        )

        with caplog.at_level(logging.ERROR):
            await service.persist_state()

        assert "Error persisting equalizer state" in caplog.text

    async def test_the_saved_file_reloads_into_an_identical_cache(self, service, tmp_path):
        """The round trip is the contract: `_load_saved_config` at boot must
        rebuild what `_persist_state_async` wrote, or a restart silently reverts
        the user's EQ to defaults."""
        service._active_preset = "jazz"
        service._mono = True
        service._custom_gains = [2.0] * 10
        service._filters[0]["gain"] = 6.0
        await service.persist_state()

        reloaded = CamillaDSPService(settings_service=service.settings_service)
        reloaded.STORAGE_PATH = Path(tmp_path / "equalizer.json")
        await reloaded._load_saved_config()

        assert reloaded._active_preset == "jazz"
        assert reloaded._mono is True
        assert reloaded._custom_gains == [2.0] * 10
        assert reloaded._filters[0]["gain"] == 6.0
