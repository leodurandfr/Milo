# backend/tests/test_systemd.py
"""
Tests for SystemdServiceManager — the single centralized privileged-exec path
(sudo systemctl) for service control + power actions (see invariant #1).
"""
import pytest
from unittest.mock import AsyncMock, patch

from backend.core.systemd import SystemdServiceManager


@pytest.fixture
def manager():
    return SystemdServiceManager()


def _make_mock_proc(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = AsyncMock()
    return proc


class TestPower:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["reboot", "poweroff"])
    async def test_power_success(self, manager, action):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            result = await manager.power(action)
        assert result is True
        exec_mock.assert_called_once()
        assert exec_mock.call_args.args[:3] == ("sudo", "systemctl", action)

    @pytest.mark.asyncio
    async def test_power_failure_is_loud(self, manager):
        proc = _make_mock_proc(returncode=1, stderr=b"not authorized")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(manager.logger, "error") as log_error:
                result = await manager.power("reboot")
        assert result is False
        log_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_power_invalid_action(self, manager):
        with pytest.raises(ValueError):
            await manager.power("halt")

    @pytest.mark.asyncio
    async def test_power_delay_flushes_response(self, manager):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
                await manager.power("reboot", delay=2.0)
        sleep_mock.assert_awaited_once_with(2.0)

    @pytest.mark.asyncio
    async def test_power_subprocess_error_is_loud(self, manager):
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("boom")):
            with patch.object(manager.logger, "error") as log_error:
                result = await manager.power("poweroff")
        assert result is False
        log_error.assert_called_once()


class TestRestartSelf:
    @pytest.mark.asyncio
    async def test_restart_self_enqueues_with_no_block(self, manager):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            with patch.object(manager.logger, "error") as log_error:
                await manager.restart_self("milo-backend.service")
        args = exec_mock.call_args.args
        assert args == ("sudo", "systemctl", "restart", "--no-block", "milo-backend.service")
        log_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_self_enqueue_failure_is_loud(self, manager):
        proc = _make_mock_proc(returncode=1, stderr=b"unit not found")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(manager.logger, "error") as log_error:
                await manager.restart_self("milo-backend.service")
        log_error.assert_called_once()


# ============================================================================
# The read paths and the failure arms — invariant #1's other half.
#
# `get_status` had not run at all, and every timeout arm in the file was at
# zero. Both matter for the same reason the argv does: this class is the ONLY
# privileged-exec path in the backend, and each of these arms decides whether a
# refused sudoers rule or a wedged systemctl surfaces or is swallowed.
#
# The suite's appliance probe blocks a real `sudo`/`systemctl` spawn, so a test
# that loses its double fails here instead of restarting the machine.
# ============================================================================

import asyncio
import logging
from unittest.mock import Mock


def _hanging_proc():
    """A process whose `communicate()` never returns.

    `kill` is a plain Mock, not an AsyncMock: `asyncio.subprocess.Process.kill`
    is synchronous, and an AsyncMock would make the production `proc.kill()`
    build a coroutine nobody awaits — the double would then pass while the real
    child kept running.
    """
    proc = Mock()

    async def _never():
        await asyncio.sleep(3600)

    proc.communicate = _never
    proc.returncode = None
    proc.kill = Mock()
    return proc


def _short_wait_for(monkeypatch):
    """Collapse the module's own `asyncio.wait_for` bound, keeping it real.

    `systemd.py` does a bare `import asyncio`, so this attribute IS
    `asyncio.wait_for` for the whole process; a replacement that calls the name
    it just replaced recurses. The real one is captured first (B6's lesson from
    reader.py), so the production code still goes through a genuine wait_for and
    a mutation that removes it is still visible.
    """
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(
        "backend.core.systemd.asyncio.wait_for",
        lambda coro, _timeout: real_wait_for(coro, 0.05),
    )


class TestGetStatus:
    """`systemctl show` — the read behind `GET /api/system/...` service panels."""

    @pytest.mark.asyncio
    async def test_the_three_properties_asked_for_are_the_three_parsed(self, manager):
        """argv and the parse are two statements of one contract, written 20 lines
        apart. Drop a property from the query and the reader answers its default
        forever — `exit_code` 0 on a unit that died with 1, `state` "unknown" on a
        unit that is running.
        """
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=active\nSubState=running\nExecMainStatus=0\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            status = await manager.get_status("milo-radio")

        argv = exec_mock.call_args.args
        assert argv[:3] == ("systemctl", "show", "milo-radio")
        asked = argv[3].removeprefix("--property=").split(",")
        assert set(asked) == {"ActiveState", "SubState", "ExecMainStatus"}
        assert status == {
            "active": True,
            "running": True,
            "exit_code": 0,
            "state": "active",
            "substate": "running",
        }

    @pytest.mark.asyncio
    async def test_the_status_read_is_not_privileged(self, manager):
        """A read must not go through sudo. Every sudo argv is pinned by
        `/etc/sudoers.d/milo-backend`, and `systemctl show` is not in it — added,
        it would be a grant nothing needs and the contract test would flag it.
        """
        proc = _make_mock_proc(returncode=0, stdout=b"ActiveState=active\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            await manager.get_status("milo-radio")

        assert exec_mock.call_args.args[0] == "systemctl"

    @pytest.mark.asyncio
    async def test_a_failed_dead_unit_reports_its_exit_code(self, manager):
        """`ExecMainStatus` is the only field that says WHY a source died.

        Read as a string it would still be truthy and the panel would show a
        plausible-looking status for a unit that crashed.
        """
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=failed\nSubState=failed\nExecMainStatus=203\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            status = await manager.get_status("milo-go-librespot")

        assert status["exit_code"] == 203
        assert status["active"] is False
        assert status["running"] is False
        assert status["state"] == "failed"

    @pytest.mark.asyncio
    async def test_an_activating_unit_is_neither_active_nor_running(self, manager):
        """`milo-kiosk` sits ~90 s in `deactivating` on a restart, and a source
        unit passes through `activating`. Both must read as not-running, or the
        UI declares a source ready before it can take a command."""
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=activating\nSubState=start\nExecMainStatus=0\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            status = await manager.get_status("milo-radio")

        assert status["active"] is False
        assert status["running"] is False
        assert status["state"] == "activating"
        assert status["substate"] == "start"

    @pytest.mark.asyncio
    async def test_a_oneshot_that_finished_is_active_but_not_running(self, manager):
        """`active` and `running` are read from two different properties.

        A oneshot with `RemainAfterExit` — which is what the ALSA passthrough pin
        and the keytable setup are — sits at `ActiveState=active`,
        `SubState=exited` forever. Deriving `running` from `ActiveState` would
        report a process that exited seconds ago as still running, and the two
        fields would stop being able to disagree at all.
        """
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=active\nSubState=exited\nExecMainStatus=0\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            status = await manager.get_status("milo-alsa-passthrough")

        assert status["active"] is True
        assert status["running"] is False
        assert status["substate"] == "exited"

    @pytest.mark.asyncio
    async def test_an_unparsable_line_does_not_take_the_others_with_it(self, manager):
        """`systemctl show` emits one `key=value` per line and nothing else, but
        a blank trailing line is normal. Splitting unconditionally would raise on
        it and lose the whole status."""
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=active\n\nSubState=running\nExecMainStatus=0\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            status = await manager.get_status("milo-radio")

        assert status["state"] == "active"
        assert status["substate"] == "running"

    @pytest.mark.asyncio
    async def test_a_value_containing_an_equals_sign_keeps_it(self, manager):
        """`split('=', 1)` and not `split('=')`. Not hypothetical for
        `--property` reads in general, and truncating a value silently produces a
        state string that matches nothing."""
        proc = _make_mock_proc(
            returncode=0,
            stdout=b"ActiveState=active\nSubState=running=fast\nExecMainStatus=0\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            status = await manager.get_status("milo-radio")

        assert status["substate"] == "running=fast"

    @pytest.mark.asyncio
    async def test_a_non_zero_systemctl_reports_an_error_envelope(self, manager, caplog):
        """An unknown unit is the common case (a source whose unit was renamed).

        Answered with the default dict, the panel would show "inactive, exit 0" —
        a healthy-looking stopped service — instead of surfacing the mistake.
        """
        proc = _make_mock_proc(returncode=1, stderr=b"Unit milo-nope.service not loaded.")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.ERROR):
                status = await manager.get_status("milo-nope")

        assert status == {"error": "Unable to retrieve status"}
        assert "Error retrieving status" in caplog.text

    @pytest.mark.asyncio
    async def test_a_spawn_that_raises_answers_the_reason(self, manager, caplog):
        """`api/system.py` renders this dict; a raise would 500 the whole
        settings page because one unit could not be probed."""
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no systemctl")):
            with caplog.at_level(logging.ERROR):
                status = await manager.get_status("milo-radio")

        assert status["error"] == "no systemctl"


class TestIsActive:
    """`systemctl is-active` — read by the lifespan to stop lingering source units."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stdout,expected", [
        (b"active\n", True),
        (b"inactive\n", False),
        (b"activating\n", False),
        (b"failed\n", False),
    ])
    async def test_only_active_counts_as_active(self, manager, stdout, expected):
        """`activating` is the one that matters: `_control_service` polls this to
        decide a start succeeded, and treating `activating` as active would
        declare every start done at t=0."""
        proc = _make_mock_proc(returncode=3, stdout=stdout)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await manager.is_active("milo-radio") is expected

    @pytest.mark.asyncio
    async def test_the_liveness_check_is_not_privileged(self, manager):
        """It runs six times per source switch. A sudo here would be six sudo
        sessions per switch, and a grant nothing in the policy file allows."""
        proc = _make_mock_proc(returncode=0, stdout=b"active\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as exec_mock:
            await manager.is_active("milo-radio")

        assert exec_mock.call_args.args == ("systemctl", "is-active", "milo-radio")

    @pytest.mark.asyncio
    async def test_a_wedged_systemctl_is_killed_and_read_as_inactive(
        self, manager, monkeypatch, caplog
    ):
        """Six of these run inside `_control_service`'s settle loop.

        Left unkilled, each wedged probe leaves a child behind for the life of
        the backend; answered as anything but False, a stop that never happened
        is reported as complete and the next source starts over a unit still
        holding the loopback.
        """
        _short_wait_for(monkeypatch)
        proc = _hanging_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.ERROR):
                assert await manager.is_active("milo-radio") is False

        proc.kill.assert_called_once()
        assert "Timeout checking is_active for milo-radio" in caplog.text


class TestControlServiceFailureArms:
    """What `start`/`stop`/`restart` do when systemctl does not come back."""

    @pytest.mark.asyncio
    async def test_a_wedged_systemctl_is_killed_and_reported(
        self, manager, monkeypatch, caplog
    ):
        """A source switch awaits this. Without the kill the `sudo systemctl`
        child outlives the request; without the False the state machine believes
        the unit started and hands it a command it will never receive.
        """
        _short_wait_for(monkeypatch)
        proc = _hanging_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.ERROR):
                assert await manager.start("milo-radio") is False

        proc.kill.assert_called_once()
        assert "took more than 10 seconds" in caplog.text

    @pytest.mark.asyncio
    async def test_a_spawn_that_raises_is_reported_not_swallowed(self, manager, caplog):
        """A broken sudoers rule raises here rather than returning non-zero.

        Swallowed to True, every source switch would report success while
        nothing on the box ever moved.
        """
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("permission denied")):
            with caplog.at_level(logging.ERROR):
                assert await manager.stop("milo-radio") is False

        assert "Unexpected error during stop milo-radio" in caplog.text


class TestRestartSelfFailureArms:
    """The fire-and-forget path, when the enqueue itself will not complete."""

    @pytest.mark.asyncio
    async def test_a_wedged_enqueue_is_killed_and_reported(
        self, manager, monkeypatch, caplog
    ):
        """`--no-block` means this returns in milliseconds normally.

        It is called from an HTTP handler after the response is flushed; a child
        left hanging holds a sudo session open across the restart that follows.
        """
        _short_wait_for(monkeypatch)
        proc = _hanging_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.ERROR):
                await manager.restart_self("milo-backend")

        proc.kill.assert_called_once()
        assert "Timeout enqueuing self-restart of milo-backend" in caplog.text

    @pytest.mark.asyncio
    async def test_a_spawn_that_raises_is_loud(self, manager, caplog):
        """This is the update path's last step. Silent, the operator sees an
        update that "succeeded" and a unit still running the old code."""
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("no sudo")):
            with caplog.at_level(logging.ERROR):
                await manager.restart_self("milo-backend")

        assert "Self-restart of milo-backend failed" in caplog.text


class TestPowerFailureArms:
    """`power()` — reboot and poweroff, the loudest thing the backend can do."""

    @pytest.mark.asyncio
    async def test_a_wedged_power_call_is_killed_and_answers_false(
        self, manager, monkeypatch, caplog
    ):
        """The Restart / Shutdown buttons read this boolean.

        True on a call that never completed tells the user the box is going down
        when it is not, and leaves a `sudo systemctl reboot` child that may still
        fire minutes later.
        """
        _short_wait_for(monkeypatch)
        proc = _hanging_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with caplog.at_level(logging.ERROR):
                assert await manager.power("reboot") is False

        proc.kill.assert_called_once()
        assert "System reboot timed out" in caplog.text
