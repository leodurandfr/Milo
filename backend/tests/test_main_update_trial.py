"""What the backend does once its socket listens: READY=1, and the end of a Milō
update's trial.

What breaks when these fail. READY=1 is what makes milo-backend.service
started (Type=notify): never sent, every start fails on TimeoutStartSec and
the update guard counts each one, so a release that works is rolled back two
starts later. Sent before the socket listens, a release that hangs in uvicorn's
bind would be called started. The commit is what keeps a release that held:
never run, the journal stays on trial, every later update and every reboot is
refused, and the next restart is counted against a release that worked. Run
before HOLD_S, a release that dies a minute in is kept.

The guard is a real executable in tmp_path standing in for
/usr/local/bin/milo-update-guard, so what is asserted is what a process was
asked and what came of its exit; the update service is a double, its own
journal handling is test_update_service.py's — except in
TestTheGuardsNotCommitted, which drives the real guard and the real service,
because what is asserted there is how the two read each other.
"""
import asyncio
import json
import logging
import os
import socket
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import uvicorn

import backend.main as main
from backend.core.systemd import SystemdServiceManager
from backend.core.updates import milo_journal as mj
from backend.core.updates.update import UpdateService
from backend.tests.golden.harness import settle
from backend.tests.test_update_guard import GUARD as REAL_GUARD, Server

ON_TRIAL = {
    "phase": "verify", "from_version": "v0.1.4", "to_version": "v0.1.5",
    "item": None, "index": None, "total": None,
}


@pytest.fixture
def guard(tmp_path, monkeypatch):
    """A milo-update-guard that records its argv and exits with the codes queued
    in `codes` (one line per call, 0 once they run out)."""
    calls, codes = tmp_path / "calls", tmp_path / "codes"
    codes.write_text("")
    script = tmp_path / "milo-update-guard"
    script.write_text(f"""#!/bin/sh
echo "$*" >> {calls}
code=$(sed -n "$(wc -l < {calls})p" {codes})
if [ -n "$code" ] && [ "$code" != 0 ]; then
    echo "ERROR: milo-update-guard: the update to v0.1.5 could not be committed: OSError: No space left" >&2
    exit "$code"
fi
""")
    script.chmod(0o755)
    monkeypatch.setattr(main, "GUARD", str(script))
    monkeypatch.setattr(main, "COMMIT_RETRY_S", 0)

    class Guard:
        def calls(self):
            return calls.read_text().splitlines() if calls.exists() else []

        def fail_next(self, *exit_codes):
            codes.write_text("".join(f"{c}\n" for c in exit_codes))

    return Guard()


@pytest.fixture
def updates(monkeypatch):
    service = MagicMock()
    service.milo_update_state = MagicMock(return_value=None)
    service.run_post_commit = AsyncMock()
    service.release_stale_inhibitor = AsyncMock()
    service.hand_stranded_journal_to_the_guard = AsyncMock()
    service.let_go_of_the_trial = AsyncMock()
    monkeypatch.setattr(main, "update_service", service)
    return service


class TestTheTrial:
    async def test_a_release_on_trial_is_committed_then_finished(self, guard, updates, monkeypatch):
        monkeypatch.setattr(main, "HOLD_S", 0)
        updates.milo_update_state.return_value = ON_TRIAL
        order = []
        updates.run_post_commit = AsyncMock(side_effect=lambda: order.append(guard.calls()))

        await main._finish_milo_update()

        assert guard.calls() == ["commit"]
        assert order == [["commit"]], "the post-commit phase ran before the trial was committed"

    async def test_nothing_is_committed_before_the_release_has_held(self, guard, updates, monkeypatch):
        """Dying in the hold is the failure the trial counts: committed early,
        the release that died a minute in would be the one kept."""
        monkeypatch.setattr(main, "HOLD_S", 3600)
        updates.milo_update_state.return_value = ON_TRIAL

        finishing = asyncio.create_task(main._finish_milo_update())
        await settle()
        try:
            assert not finishing.done()
            assert guard.calls() == []
            updates.run_post_commit.assert_not_awaited()
        finally:
            finishing.cancel()

    async def test_a_failed_commit_is_tried_again_until_it_lands(self, guard, updates, monkeypatch, caplog):
        """Left open over a release that held, the trial refuses every update
        and every reboot, and rolls that release back two restarts later. An
        attempt that fails is a warning: one that lands after it is no error."""
        monkeypatch.setattr(main, "HOLD_S", 0)
        updates.milo_update_state.return_value = ON_TRIAL
        guard.fail_next(1, 1)

        with caplog.at_level(logging.WARNING, logger=main.logger.name):
            await main._finish_milo_update()

        assert guard.calls() == ["commit"] * 3
        updates.run_post_commit.assert_awaited_once()
        # Asked once, as after every exit 0: a no-op over the committed journal.
        (cause,) = updates.let_go_of_the_trial.await_args.args
        assert "commits failed" not in cause
        failures = [r for r in caplog.records if "commit failed" in r.message]
        assert len(failures) == 2
        assert failures[0].message.endswith("No space left"), "the guard's own line is the diagnosis"
        assert all(r.levelno == logging.WARNING for r in failures)

    async def test_a_commit_that_keeps_failing_lets_go_of_the_unit(self, guard, updates, monkeypatch, caplog):
        """A guard that cannot write the journal (a card remounted read-only)
        fails the same way every minute: tried for good, the update screen
        stayed up and the reboot stayed refused until the unit was unplugged.
        Past COMMIT_ATTEMPTS the unit is let go of, with the guard's own line,
        and nothing is said at error here — the update service says it once."""
        monkeypatch.setattr(main, "HOLD_S", 0)
        monkeypatch.setattr(main, "COMMIT_ATTEMPTS", 3)
        updates.milo_update_state.return_value = ON_TRIAL
        updates.let_go_of_the_trial = AsyncMock()
        guard.fail_next(1, 1, 1, 1, 1)

        with caplog.at_level(logging.WARNING, logger=main.logger.name):
            await main._finish_milo_update()

        assert guard.calls() == ["commit"] * 3
        updates.let_go_of_the_trial.assert_awaited_once()
        (cause,) = updates.let_go_of_the_trial.await_args.args
        assert cause.endswith("No space left")
        # Asked for anyway: a no-op unless the let-go found the journal committed.
        updates.run_post_commit.assert_awaited_once()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_a_guard_that_hangs_is_killed_and_tried_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "GUARD_TIMEOUT_S", 0.2)
        pid_file = tmp_path / "pid"
        script = tmp_path / "milo-update-guard"
        script.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
        script.chmod(0o755)
        monkeypatch.setattr(main, "GUARD", str(script))

        exited, cause = await main._commit_milo_trial()
        assert not exited
        assert "did not finish" in cause

        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    @pytest.mark.parametrize("state", [
        None,
        {**ON_TRIAL, "phase": "dependencies"},
    ], ids=["no-journal", "committed"])
    async def test_without_a_trial_the_guard_is_not_asked(self, guard, updates, state):
        """A committed journal found at start is a post-commit phase a restart
        cut: it resumes at once, with no second hold."""
        updates.milo_update_state.return_value = state

        await main._finish_milo_update()

        assert guard.calls() == []
        updates.run_post_commit.assert_awaited_once()


    async def test_a_journal_the_guard_left_open_is_handed_back_to_it_first(self, guard, updates):
        """Before the trial is read: a backend running over an install the guard
        did not put back holds the update screen until something acts on it."""
        order = []
        updates.hand_stranded_journal_to_the_guard = AsyncMock(side_effect=lambda: order.append("hand"))
        updates.milo_update_state = MagicMock(side_effect=lambda: order.append("state"))

        await main._finish_milo_update()

        assert order[:2] == ["hand", "state"]


class TestTheGuardsNotCommitted:
    """`milo-update-guard commit` exits 0 without committing too: over a
    restore in progress (or a failure recorded beside the journal), and over a
    HEAD that is not the release on trial. Read as "nothing left to do", the
    trial was never let go of: the update screen stayed on 'verify' and
    `refuse_while_milo_updates` answered 409 to the reboot and the power-off
    until the unit was unplugged."""

    @pytest.fixture
    def on_trial(self, tmp_path, monkeypatch, mock_settings_service):
        if os.geteuid() == 0:
            pytest.skip("the guard refuses to run as root")
        server = Server(tmp_path / "server").live()
        server.on_trial()
        satellites = Mock()
        satellites.push_client_app_to_fleet = AsyncMock(return_value=[])
        mock_settings_service._storage["updates.forced_versions"] = {}
        with patch.dict("os.environ", {}, clear=True):
            service = UpdateService(systemd_manager=SystemdServiceManager(),
                                    satellite_update_service=satellites,
                                    settings_service=mock_settings_service)
        service.programs["milo"]["state_path"] = str(server.state)
        service.programs["milo"]["backup_path"] = str(server.backup)
        completed = AsyncMock()
        service.set_milo_update_listener(AsyncMock(), completed)
        for name, value in server.env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(main, "update_service", service)
        monkeypatch.setattr(main, "GUARD", str(REAL_GUARD))
        monkeypatch.setattr(main, "HOLD_S", 0)
        monkeypatch.setattr(main, "COMMIT_RETRY_S", 0)
        return server, service, completed

    def _restore_in_progress(self, server):
        (server.state / mj.ROLLING_BACK_NAME).write_text(
            json.dumps({"detail": "cut", "runs": 1, "step": "venv"}))

    def _head_elsewhere(self, server):
        server.git("checkout", "-q", "--force", "v0.1.0")

    @pytest.mark.parametrize("not_committable", ["_restore_in_progress", "_head_elsewhere"])
    async def test_a_trial_the_guard_did_not_commit_is_let_go_of(
            self, on_trial, not_committable, caplog):
        server, service, completed = on_trial
        getattr(self, not_committable)(server)
        assert service.milo_update_state()["phase"] == "verify"
        said = server.guard("commit")
        assert said.returncode == 0, "the case is the guard's exit 0"
        (why,) = [line for line in said.stderr.splitlines() if "not committed" in line]

        with caplog.at_level(logging.ERROR, logger=service.update_logger.name):
            await main._finish_milo_update()

        assert server.journal()[mj.KEY_STEP] == mj.TRIAL_STEP, "the guard committed after all"
        assert service.milo_update_active is False, "the reboot and the power-off stay refused"
        assert service.milo_update_state() is None, "the update screen stays up"
        completed.assert_awaited_once_with(False)
        (error,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert why.strip() in error.message, "the guard's reason is the only diagnosis"

    async def test_a_trial_the_guard_committed_is_not_let_go_of(self, on_trial, monkeypatch):
        """The same exit 0 after a commit: the let-go reads the journal
        committed and does nothing, and the post-commit phase (which reaches
        GitHub and the satellites, so stood in for) is what follows."""
        server, service, completed = on_trial
        post_commit = AsyncMock()
        monkeypatch.setattr(service, "run_post_commit", post_commit)

        await main._finish_milo_update()

        assert server.journal()[mj.KEY_STEP] == mj.COMMITTED_STEP
        completed.assert_not_awaited()
        post_commit.assert_awaited_once()


class TestOnceListening:
    async def test_ready_goes_to_systemd_s_notify_socket(self, tmp_path, monkeypatch):
        """The real datagram, read where systemd would read it."""
        path = str(tmp_path / "notify")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.bind(path)
            monkeypatch.setenv("NOTIFY_SOCKET", path)

            assert main._sd_notify(b"READY=1") is True
            assert notify.recv(64) == b"READY=1"

    def test_outside_systemd_nothing_is_sent(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert main._sd_notify(b"READY=1") is False

    async def test_ready_then_the_sweep_then_the_trial(self, updates, monkeypatch):
        """READY=1 first: the units the sweep looks at are held behind it. The
        trial last: a dependency installed by the post-commit phase restarts
        units the sweep would otherwise stop under it."""
        order = []
        monkeypatch.setattr(main, "_sd_notify", lambda message: order.append(message) or True)
        machine = MagicMock()
        machine.stop_lingering_units = AsyncMock(side_effect=lambda _s: order.append("sweep"))
        monkeypatch.setattr(main, "state_machine", machine)
        updates.run_post_commit = AsyncMock(side_effect=lambda: order.append("post-commit"))
        listening = asyncio.Event()

        once = asyncio.create_task(main._once_listening(listening))
        await settle()
        assert order == [], "nothing may happen before the socket listens"
        listening.set()
        await once

        assert order == [b"READY=1", "sweep", "post-commit"]

    async def test_a_sweep_that_raises_does_not_cost_the_commit(self, guard, updates, monkeypatch):
        monkeypatch.setattr(main, "_sd_notify", lambda message: True)
        monkeypatch.setattr(main, "HOLD_S", 0)
        machine = MagicMock()
        machine.stop_lingering_units = AsyncMock(side_effect=RuntimeError("D-Bus gone"))
        monkeypatch.setattr(main, "state_machine", machine)
        updates.milo_update_state.return_value = ON_TRIAL
        listening = asyncio.Event()
        listening.set()

        await main._once_listening(listening)

        assert guard.calls() == ["commit"]
        updates.run_post_commit.assert_awaited_once()

    async def test_a_ready_that_cannot_be_sent_does_not_cost_the_rest(self, updates, monkeypatch, caplog):
        """The start then fails on TimeoutStartSec, which the guard counts; until
        then the unit serves, and its lingering units must still be swept."""
        def refuse(message):
            raise ConnectionRefusedError("notify socket gone")

        monkeypatch.setattr(main, "_sd_notify", refuse)
        machine = MagicMock()
        machine.stop_lingering_units = AsyncMock()
        monkeypatch.setattr(main, "state_machine", machine)
        listening = asyncio.Event()
        listening.set()

        with caplog.at_level(logging.ERROR, logger=main.logger.name):
            await main._once_listening(listening)

        assert any("READY=1 could not be sent" in r.message for r in caplog.records)
        machine.stop_lingering_units.assert_awaited_once()
        updates.run_post_commit.assert_awaited_once()


class TestTheServer:
    @pytest.mark.parametrize("started", [True, False])
    async def test_listening_is_marked_only_once_uvicorn_has_bound(self, monkeypatch, started):
        """uvicorn binds after the lifespan's startup; a bind that failed leaves
        `started` false, and READY=1 must then never go out."""
        async def bound(self, sockets=None):
            self.started = started

        monkeypatch.setattr(uvicorn.Server, "startup", bound)
        monkeypatch.setattr(main, "_listening", asyncio.Event())

        await main._Server(uvicorn.Config(main.app)).startup()

        assert main._listening.is_set() is started
