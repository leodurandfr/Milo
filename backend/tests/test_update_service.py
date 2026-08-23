# backend/tests/test_update_service.py
"""
Tests for UpdateService — update orchestration, backup/restore, service management.

Deliberate limit, stated because it looks like debt and is not: these tests patch
the service's own private phases — `_stop_service`, `_run_deploy`,
`_update_binary_program`, `_rollback_milo_to_commit` and their neighbours —
rather than only its collaborators. UpdateService *is* an ordering: stop the
unit, deploy, restart, roll back when the restart fails. The phases are the
behaviour under test, and what these assert is which one ran, in which order,
and which did not run at all. Leaving the real phases in and mocking only
systemd, git, tar and the network would not drop the coupling to names — it
would move it onto the exact `systemctl` argv sequence, which is more brittle
still. So this file is out of scope for any sweep that replaces
`patch.object(service, "_private")` with a collaborator-level failure; the
sibling files where that migration *is* right say so in their own headers.
"""
import asyncio
import logging
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.update import UpdateService
from backend.core.systemd import SystemdServiceManager

# This checkout's root, so a path the service builds can be checked against the
# tree that actually ships rather than against a literal repeated in the test.
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def update_service():
    """Fresh UpdateService instance.

    Injects a real SystemdServiceManager so the service-control helpers (which
    now delegate to it) still exercise the subprocess layer patched by tests.
    """
    with patch.dict("os.environ", {}, clear=True):
        return UpdateService(systemd_manager=SystemdServiceManager())


# The programs served by the one shared _update_binary_program flow. Kept as a
# literal rather than derived from the catalog so a program dropping out of the
# flow is a visible test edit, not a silently shrinking parametrization.
BINARY_PROGRAMS = ["go-librespot", "camilladsp", "navidrome"]


def _make_mock_proc(returncode=0, stdout=b"", stderr=b""):
    """Helper to create a mock subprocess"""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()
    return proc


class TestUpdateServiceInit:
    """Tests for UpdateService initialization"""

    def test_inherits_version_service(self, update_service):
        assert hasattr(update_service, "programs")
        assert hasattr(update_service, "_github_cache")


class TestUpdateHandlerCoverage:
    """Every catalog entry must reach an update handler.

    The catalog is one dict shared with VersionService, so a program can be
    fully declared -- and offered in the UI as updatable -- while update_program
    has no branch for it. That falls through to "Update handler not
    implemented", which no caller can tell apart from a real failure.
    """

    HANDLERS = [
        "_update_milo_app",
        "_update_multiroom",
        "_update_shairport_sync",
        "_update_qobuz_proxy",
        "_update_binary_program",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", list(PROGRAMS))
    async def test_every_program_reaches_a_handler(self, update_service, program_key):
        status = {"update_available": True, "latest": {"version": "9.9.9"}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(update_service, "get_program_full_status", return_value=status))
            handlers = {
                name: stack.enter_context(patch.object(update_service, name, return_value={"success": True}))
                for name in self.HANDLERS
            }
            result = await update_service.update_program(program_key)

        called = [name for name, mock in handlers.items() if mock.await_count]
        assert len(called) == 1, f"{program_key} reached {called or 'no handler'}"
        assert result["success"] is True

        # The three tarball programs share one handler, so reaching it is not
        # enough -- it has to be told which program it is downloading.
        shared = handlers["_update_binary_program"]
        if shared.await_count:
            assert shared.await_args.args[0] == program_key, (
                f"{program_key} reached the shared binary flow as "
                f"{shared.await_args.args[0]!r}"
            )


class TestUpdateProgram:
    """Tests for update_program() dispatch"""

    @pytest.mark.asyncio
    async def test_unsupported_program(self, update_service):
        result = await update_service.update_program("unknown-program")
        assert result["success"] is False
        assert "not supported" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_update_available(self, update_service):
        with patch.object(update_service, "get_program_full_status", return_value={
            "update_available": False
        }):
            result = await update_service.update_program("go-librespot")
        assert result["success"] is False
        assert "No update available" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_caught(self, update_service):
        with patch.object(update_service, "get_program_full_status",
                          side_effect=Exception("boom")):
            result = await update_service.update_program("go-librespot")

        assert result["success"] is False
        assert "boom" in result["error"]


class TestServiceManagement:
    """Tests for _is_service_active, _stop_service, _start_service, _restart_service"""

    @pytest.mark.asyncio
    async def test_is_service_active_true(self, update_service):
        proc = _make_mock_proc(stdout=b"active\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._is_service_active("milo-spotify.service")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_service_active_false(self, update_service):
        proc = _make_mock_proc(stdout=b"inactive\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._is_service_active("milo-spotify.service")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_service_active_error(self, update_service):
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("fail")):
            result = await update_service._is_service_active("milo-spotify.service")
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_service_success(self, update_service):
        proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._stop_service("milo-spotify.service")
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_service_failure(self, update_service):
        proc = _make_mock_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._stop_service("milo-spotify.service")
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_success(self, update_service):
        start_proc = _make_mock_proc(returncode=0)
        check_proc = _make_mock_proc(stdout=b"active\n")

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_proc
            return check_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await update_service._start_service("milo-spotify.service")
        assert result is True

    @pytest.mark.asyncio
    async def test_start_service_fails_to_start(self, update_service):
        proc = _make_mock_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._start_service("milo-spotify.service")
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_not_active_after_start(self, update_service):
        start_proc = _make_mock_proc(returncode=0)
        check_proc = _make_mock_proc(stdout=b"inactive\n")

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return start_proc
            return check_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await update_service._start_service("milo-spotify.service")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_service_success(self, update_service):
        restart_proc = _make_mock_proc(returncode=0)
        check_proc = _make_mock_proc(stdout=b"active\n")

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return restart_proc
            return check_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await update_service._restart_service("milo-backend.service")
        assert result is True

    @pytest.mark.asyncio
    async def test_restart_service_failure(self, update_service):
        proc = _make_mock_proc(returncode=1, stderr=b"failed to restart")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._restart_service("milo-backend.service")
        assert result is False


class TestBackupBinaryProgram:
    """Tests for _backup_binary_program()"""

    @pytest.mark.asyncio
    async def test_successful_backup(self, update_service, tmp_path):
        config = {
            "binary_path": str(tmp_path / "go-librespot"),
            "config_path": str(tmp_path / "config.yml"),
            "backup_path": str(tmp_path / "backups")
        }
        # Create fake binary and config
        (tmp_path / "go-librespot").write_text("binary")
        (tmp_path / "config.yml").write_text("config")

        result = await update_service._backup_binary_program(config)
        assert result["success"] is True
        assert (tmp_path / "backups" / "go-librespot.backup").exists()
        assert (tmp_path / "backups" / "config.yml.backup").exists()

    @pytest.mark.asyncio
    async def test_backup_without_config(self, update_service, tmp_path):
        config = {
            "binary_path": str(tmp_path / "go-librespot"),
            "config_path": str(tmp_path / "config.yml"),
            "backup_path": str(tmp_path / "backups")
        }
        (tmp_path / "go-librespot").write_text("binary")
        # No config file

        result = await update_service._backup_binary_program(config)
        assert result["success"] is True
        assert (tmp_path / "backups" / "go-librespot.backup").exists()
        assert not (tmp_path / "backups" / "config.yml.backup").exists()

    @pytest.mark.asyncio
    async def test_backup_program_without_config_path(self, update_service, tmp_path):
        """CamillaDSP and Navidrome declare no config_path at all."""
        config = {
            "binary_path": str(tmp_path / "camilladsp"),
            "backup_path": str(tmp_path / "backups")
        }
        (tmp_path / "camilladsp").write_text("binary")

        result = await update_service._backup_binary_program(config)
        assert result["success"] is True
        assert (tmp_path / "backups" / "camilladsp.backup").exists()

    @pytest.mark.asyncio
    async def test_backup_missing_binary(self, update_service, tmp_path):
        """No binary means no rollback target, so the update must not start."""
        config = {
            "binary_path": str(tmp_path / "nonexistent"),
            "config_path": str(tmp_path / "config.yml"),
            "backup_path": str(tmp_path / "backups")
        }
        result = await update_service._backup_binary_program(config)
        assert result["success"] is False


class TestBackupShairportSync:
    """Tests for _backup_shairport_sync()"""

    @pytest.mark.asyncio
    async def test_successful_backup(self, update_service, tmp_path):
        config = {
            "binary_path": str(tmp_path / "shairport-sync"),
            "backup_path": str(tmp_path / "backups")
        }
        (tmp_path / "shairport-sync").write_text("binary")

        result = await update_service._backup_shairport_sync(config)
        assert result["success"] is True
        assert (tmp_path / "backups" / "shairport-sync.backup").exists()

    @pytest.mark.asyncio
    async def test_backup_no_binary(self, update_service, tmp_path):
        config = {
            "binary_path": str(tmp_path / "nonexistent"),
            "backup_path": str(tmp_path / "backups")
        }
        # No binary exists, backup should still succeed (just skip)
        result = await update_service._backup_shairport_sync(config)
        assert result["success"] is True


def _rollback_config(tmp_path):
    return {
        "log_name": "go-librespot",
        "backup_path": str(tmp_path / "backups"),
        "binary_path": "/usr/local/bin/go-librespot",
        "service_name": "milo-spotify.service"
    }


class TestRollbackBinaryProgram:
    """Tests for _rollback_binary_program()"""

    @pytest.mark.asyncio
    async def test_no_backup_returns_false(self, update_service, tmp_path):
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()

        with patch.object(update_service, "_stop_service", return_value=True):
            result = await update_service._rollback_binary_program(config, restart_service=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_with_active_service(self, update_service, tmp_path):
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=True):
                    result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_rollback_inactive_service_not_started(self, update_service, tmp_path):
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=True) as mock_start:
                    result = await update_service._rollback_binary_program(config, restart_service=False)

        assert result is True
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_restart_is_not_a_rollback(self, update_service, tmp_path):
        """The binary is back but nothing is running it. Answering True here is
        what let the caller announce a restored program to a silent room.
        """
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=False):
                    result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_stop_leaves_the_binary_alone(self, update_service, tmp_path):
        """install-binary over a running image either fails with "Text file busy"
        or writes a file nothing is executing; either way the restore cannot be
        claimed, so it is not attempted.
        """
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(update_service, "_stop_service", return_value=False):
            with patch.object(update_service, "_run_deploy") as mock_deploy:
                result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is False
        mock_deploy.assert_not_called()


class TestRunDeploy:
    """Tests for _run_deploy() — the privileged milo-deploy-update wrapper.

    Every program's install and rollback goes through it, so its
    (ok, output) contract is what turns a failed deploy into a rollback.
    """

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self, update_service):
        proc = _make_mock_proc(stdout=b"installed\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            ok, output = await update_service._run_deploy("install-binary", "/tmp/x", "/usr/local/bin/x")
        assert ok is True
        assert output == "installed"

    @pytest.mark.asyncio
    async def test_failure_returns_stderr(self, update_service):
        proc = _make_mock_proc(returncode=1, stderr=b"permission denied")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            ok, output = await update_service._run_deploy("install-binary", "/tmp/x", "/usr/local/bin/x")
        assert ok is False
        assert "permission denied" in output


class TestInstallDebPackage:
    """Tests for _install_deb_package()"""

    @pytest.mark.asyncio
    async def test_successful_install(self, update_service):
        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._install_deb_package("/tmp/snapserver.deb")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_deploy_failure(self, update_service):
        with patch.object(update_service, "_run_deploy", return_value=(False, "Package installation failed")):
            result = await update_service._install_deb_package("/tmp/snapserver.deb")
        assert result["success"] is False


class TestGetDebianCodename:
    """Tests for _get_debian_codename()"""

    @pytest.mark.asyncio
    async def test_detects_bookworm(self, update_service):
        proc = _make_mock_proc(stdout=b"bookworm\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._get_debian_codename()
        assert result == "bookworm"

    @pytest.mark.asyncio
    async def test_detects_trixie(self, update_service):
        proc = _make_mock_proc(stdout=b"trixie\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._get_debian_codename()
        assert result == "trixie"

    @pytest.mark.asyncio
    async def test_empty_output_fallback(self, update_service):
        proc = _make_mock_proc(stdout=b"\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._get_debian_codename()
        assert result == "bookworm"

    @pytest.mark.asyncio
    async def test_exception_fallback(self, update_service):
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("fail")):
            result = await update_service._get_debian_codename()
        assert result == "bookworm"


class TestGetCurrentCommit:
    """Tests for _get_current_commit()"""

    @pytest.mark.asyncio
    async def test_returns_commit_hash(self, update_service):
        proc = _make_mock_proc(stdout=b"abc123def456\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._get_current_commit("/home/milo/milo")
        assert result == "abc123def456"

    @pytest.mark.asyncio
    async def test_an_unreadable_head_is_reported_at_error(self, update_service, caplog):
        """The empty string is what _update_milo_app reads as "no commit to roll
        back to", and it then skips the automatic rollback in silence. The log is
        the only thing that says the update started without a rollback point.
        """
        proc = _make_mock_proc(returncode=128, stderr=b"fatal: not a git repository")

        with caplog.at_level(logging.ERROR):
            with patch("asyncio.create_subprocess_exec", return_value=proc):
                result = await update_service._get_current_commit("/home/milo/milo")

        assert result == ""
        assert "rollback" in caplog.text.lower()
        assert "not a git repository" in caplog.text


class TestCleanupTempFiles:
    """Tests for _cleanup_temp_files()"""

    @pytest.mark.asyncio
    async def test_cleanup_existing_dir(self, update_service, tmp_path):
        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()
        (temp_dir / "file.txt").write_text("test")

        await update_service._cleanup_temp_files(str(temp_dir))
        assert not temp_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_none(self, update_service):
        # Should not raise
        await update_service._cleanup_temp_files(None)

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent(self, update_service):
        # Should not raise
        await update_service._cleanup_temp_files("/nonexistent/path")


class TestCanUpdateProgram:
    """Tests for can_update_program()"""

    @pytest.mark.asyncio
    async def test_unsupported_program(self, update_service):
        result = await update_service.can_update_program("unknown-program")
        assert result["can_update"] is False
        assert "not supported" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_no_sudo(self, update_service):
        with patch.object(update_service, "_run_deploy", return_value=(False, "not accessible")):
            result = await update_service.can_update_program("go-librespot")
        assert result["can_update"] is False
        assert "deploy wrapper" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_no_update_available(self, update_service):
        sudo_proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=sudo_proc):
            with patch.object(update_service, "get_program_full_status", return_value={
                "update_available": False
            }):
                result = await update_service.can_update_program("go-librespot")
        assert result["can_update"] is False

    @pytest.mark.asyncio
    async def test_can_update(self, update_service):
        sudo_proc = _make_mock_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=sudo_proc):
            with patch.object(update_service, "get_program_full_status", return_value={
                "update_available": True,
                "latest": {"version": "0.7.0"}
            }):
                result = await update_service.can_update_program("go-librespot")
        assert result["can_update"] is True
        assert result["available_version"] == "0.7.0"

    @pytest.mark.asyncio
    async def test_sudo_exception(self, update_service):
        with patch.object(update_service, "_run_deploy", return_value=(False, "exception occurred")):
            result = await update_service.can_update_program("go-librespot")
        assert result["can_update"] is False
        assert "deploy wrapper" in result["reason"].lower()


class TestUpdateBinaryProgram:
    """Tests for _update_binary_program() — the single flow behind go-librespot,
    CamillaDSP and Navidrome.

    The mocks stand for the outside world the flow drives (systemd, the deploy
    wrapper, the download); the assertions are what it did to them, and in
    which order, under which failure.
    """

    @staticmethod
    @contextmanager
    def _flow(service, *, service_active, backup=None, download=None, deploy=(True, ""), stop=True,
              rollback=True):
        """Stack the collaborators, leaving the flow under test real."""
        returns = {
            "_is_service_active": service_active,
            "_backup_binary_program": backup or {"success": True},
            "_download_binary_program": download or {
                "success": True, "binary_path": "/tmp/bin", "temp_dir": "/tmp/dl"
            },
            "_run_deploy": deploy,
            "_verify_binary_program": {"success": True},
            "_stop_service": stop,
            "_start_service": True,
            "_rollback_binary_program": rollback,
            "_cleanup_temp_files": None,
        }
        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }
            # The flow waits for the kernel to release the running binary.
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            yield mocks

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", BINARY_PROGRAMS)
    async def test_successful_update_with_active_service(self, update_service, program_key):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=True) as mocks:
            result = await update_service._update_binary_program(program_key, status)

        assert result["success"] is True
        mocks["_stop_service"].assert_awaited_once()
        mocks["_start_service"].assert_awaited_once()
        mocks["_rollback_binary_program"].assert_not_awaited()
        assert mocks["_run_deploy"].await_args.args[0] == "install-binary"

    @pytest.mark.asyncio
    async def test_on_demand_program_leaves_inactive_service_stopped(self, update_service):
        """go-librespot's Spotify service is on-demand: updating must not start it."""
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=False) as mocks:
            result = await update_service._update_binary_program("go-librespot", status)

        assert result["success"] is True
        mocks["_stop_service"].assert_not_awaited()
        mocks["_start_service"].assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", ["camilladsp", "navidrome"])
    async def test_always_on_program_restarts_even_when_inactive(self, update_service, program_key):
        """CamillaDSP and Navidrome are always-on: the binary cannot be swapped
        while it is in use, and the service must be back up afterwards — whatever
        state it happened to be in when the update started.
        """
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=False) as mocks:
            result = await update_service._update_binary_program(program_key, status)

        assert result["success"] is True
        mocks["_stop_service"].assert_awaited_once()
        mocks["_start_service"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_backup_failure_aborts_before_download(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(
            update_service, service_active=False,
            backup={"success": False, "error": "Backup failed"}
        ) as mocks:
            result = await update_service._update_binary_program("go-librespot", status)

        assert result["success"] is False
        assert "Backup failed" in result["error"]
        mocks["_download_binary_program"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_download_failure_aborts_before_stopping(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(
            update_service, service_active=True,
            download={"success": False, "error": "HTTP 404"}
        ) as mocks:
            result = await update_service._update_binary_program("go-librespot", status)

        assert result["success"] is False
        mocks["_stop_service"].assert_not_awaited()
        mocks["_run_deploy"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_failure_aborts_without_touching_the_binary(self, update_service):
        """A service that will not stop would make install-binary hit "Text file
        busy"; aborting keeps the working binary in place.
        """
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=True, stop=False) as mocks:
            result = await update_service._update_binary_program("camilladsp", status)

        assert result["success"] is False
        mocks["_run_deploy"].assert_not_awaited()
        mocks["_cleanup_temp_files"].assert_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", BINARY_PROGRAMS)
    async def test_install_failure_rolls_back_and_cleans_up(self, update_service, program_key):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(
            update_service, service_active=True, deploy=(False, "install failed")
        ) as mocks:
            result = await update_service._update_binary_program(program_key, status)

        assert result["success"] is False
        assert "install failed" in result["error"]
        mocks["_rollback_binary_program"].assert_awaited_once()
        mocks["_cleanup_temp_files"].assert_awaited()

    @pytest.mark.asyncio
    async def test_a_rollback_that_failed_reads_differently_from_one_that_worked(self, update_service):
        """The update failure is reported truthfully either way; whether the
        previous binary is running again is the half the owner can act on, and
        it used to be dropped. The route logs this string at error level, so it
        is what reaches the UI banner.
        """
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=True, deploy=(False, "install failed"),
                        rollback=True):
            restored = await update_service._update_binary_program("camilladsp", status)

        with self._flow(update_service, service_active=True, deploy=(False, "install failed"),
                        rollback=False):
            stranded = await update_service._update_binary_program("camilladsp", status)

        assert restored["success"] is stranded["success"] is False
        assert restored["error"] != stranded["error"]
        assert "manual intervention" in stranded["error"].lower()
        assert "manual intervention" not in restored["error"].lower()


class TestUpdateMultiroom:
    """Tests for _update_multiroom() orchestration"""

    # Named by the catalog, so a renamed unit surfaces here rather than in a literal.
    SERVICES = PROGRAMS["multiroom"]["services"]
    STATUS = {"installed": {"versions": {"main": "0.27.0"}}, "latest": {"version": "0.28.0"}}

    @staticmethod
    @contextmanager
    def _flow(service, *, active, install=None):
        """Stack the collaborators, leaving _update_multiroom itself real.

        `active` maps each multiroom unit to what systemd reported before the
        update; `install` overrides the two _install_deb_package results.
        """
        with ExitStack() as stack:
            mocks = {
                "_is_service_active": stack.enter_context(patch.object(
                    service, "_is_service_active", side_effect=lambda svc: active[svc]
                )),
                "_download_snapcast_component": stack.enter_context(patch.object(
                    service, "_download_snapcast_component",
                    return_value={"success": True, "deb_path": "/tmp/pkg.deb", "temp_dir": "/tmp/dl"},
                )),
                "_install_deb_package": stack.enter_context(patch.object(
                    service, "_install_deb_package",
                    side_effect=install or [{"success": True}, {"success": True}],
                )),
            }
            for name, value in (("_stop_service", True), ("_start_service", True), ("_cleanup_temp_files", None)):
                mocks[name] = stack.enter_context(patch.object(service, name, return_value=value))
            yield mocks

    @staticmethod
    def _started(mocks):
        """The units _start_service was actually asked to start."""
        return [call.args[0] for call in mocks["_start_service"].await_args_list]

    @pytest.mark.asyncio
    async def test_units_inactive_before_the_update_stay_stopped(self, update_service):
        """The snapcast units have no WantedBy — nothing reconciles them after an
        update. Starting them in direct mode leaves snapclient holding
        hw:Loopback,0,0, so the next direct-mode source plays silence until reboot.
        """
        active = dict.fromkeys(self.SERVICES, False)

        with self._flow(update_service, active=active) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_units_active_before_the_update_come_back(self, update_service):
        """Multiroom mode: the update must not leave the appliance mute."""
        active = dict.fromkeys(self.SERVICES, True)

        with self._flow(update_service, active=active) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == list(self.SERVICES)

    @pytest.mark.asyncio
    async def test_restore_is_decided_per_unit(self, update_service):
        """A server-only unit (snapserver up, no local snapclient) must not gain one."""
        active = {self.SERVICES[0]: True, self.SERVICES[1]: False}

        with self._flow(update_service, active=active) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == [self.SERVICES[0]]

    @pytest.mark.asyncio
    async def test_install_failure_does_not_start_inactive_units(self, update_service):
        """The recovery branches restore the prior state, they do not impose one."""
        active = dict.fromkeys(self.SERVICES, False)

        with self._flow(update_service, active=active, install=[{"success": False, "error": "dpkg"}]) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_failure_before_the_stop_starts_nothing(self, update_service):
        """The download phase runs before anything is stopped, so its except branch
        has no prior state to restore — and must not invent one.
        """
        active = dict.fromkeys(self.SERVICES, True)

        with self._flow(update_service, active=active) as mocks:
            mocks["_download_snapcast_component"].side_effect = RuntimeError("network down")
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_a_unit_that_did_not_come_back_reads_differently(self, update_service):
        """A snapcast unit that stayed down after a failed install leaves the
        appliance mute — the opposite of the recovery the error claimed.
        """
        active = dict.fromkeys(self.SERVICES, True)
        install = [{"success": False, "error": "dpkg"}]

        with self._flow(update_service, active=active, install=install) as mocks:
            mocks["_start_service"].return_value = True
            restored = await update_service._update_multiroom(self.STATUS)

        with self._flow(update_service, active=active, install=install) as mocks:
            mocks["_start_service"].return_value = False
            stranded = await update_service._update_multiroom(self.STATUS)

        assert restored["success"] is stranded["success"] is False
        assert restored["error"] != stranded["error"]
        assert "manual intervention" in stranded["error"].lower()

    @pytest.mark.asyncio
    async def test_server_download_failure(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.27.0"}},
            "latest": {"version": "0.28.0"}
        }

        with patch.object(update_service, "_download_snapcast_component", return_value={
            "success": False, "error": "HTTP 404"
        }):
            result = await update_service._update_multiroom(status)

        assert result["success"] is False
        assert "snapserver" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_client_download_failure_cleans_server(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.27.0"}},
            "latest": {"version": "0.28.0"}
        }

        call_count = 0
        async def mock_download(component, version):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "deb_path": "/tmp/s.deb", "temp_dir": "/tmp/s"}
            return {"success": False, "error": "HTTP 404"}

        with patch.object(update_service, "_download_snapcast_component", side_effect=mock_download):
            with patch.object(update_service, "_cleanup_temp_files") as mock_cleanup:
                result = await update_service._update_multiroom(status)

        assert result["success"] is False
        mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapclient_install_failure_partial_success(self, update_service):
        install = [{"success": True}, {"success": False, "error": "install failed"}]

        with self._flow(update_service, active=dict.fromkeys(self.SERVICES, True), install=install) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        # The discriminator is which half failed: snapserver installed, snapclient
        # did not. "success is False" alone would also pass if snapserver had
        # failed, which is a different outcome (nothing was replaced).
        assert result["success"] is False
        assert "snapclient failed" in result["error"]
        assert mocks["_install_deb_package"].await_count == 2


class TestUpdateMiloApp:
    """Tests for _update_milo_app() orchestration"""

    @pytest.mark.asyncio
    async def test_not_git_repo(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.0.1"}},
            "latest": {"version": "1.0.0"}
        }

        with patch("pathlib.Path.exists", return_value=False):
            result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "git repository" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_dirty_working_tree(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.0.1"}},
            "latest": {"version": "1.0.0"}
        }

        commit_proc = _make_mock_proc(stdout=b"abc123\n")
        fetch_proc = _make_mock_proc(returncode=0)
        status_proc = _make_mock_proc(stdout=b" M modified_file.py\n")

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return commit_proc
            elif call_count == 2:
                return fetch_proc
            return status_proc

        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "local changes" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_a_failed_git_status_aborts_before_the_pull(self, update_service):
        """A non-zero `git status --porcelain` yields the same empty stdout as a
        clean tree. Reading only stdout let the update pull over local changes it
        never managed to look for.
        """
        status = {
            "installed": {"versions": {"main": "0.0.1"}},
            "latest": {"version": "1.0.0"}
        }

        procs = {
            "rev-parse": _make_mock_proc(stdout=b"abc123\n"),
            "fetch": _make_mock_proc(returncode=0),
            "status": _make_mock_proc(returncode=128, stderr=b"fatal: bad object"),
        }
        spawned = []

        async def mock_exec(*args, **kwargs):
            spawned.append(args)
            return procs[next(a for a in args if a in procs)]

        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "git status" in result["error"].lower()
        assert not any("pull" in args for args in spawned)

    @pytest.mark.asyncio
    async def test_git_fetch_timeout(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.0.1"}},
            "latest": {"version": "1.0.0"}
        }

        commit_proc = _make_mock_proc(stdout=b"abc123\n")
        fetch_proc = _make_mock_proc()
        fetch_proc.kill = AsyncMock()
        fetch_proc.wait = AsyncMock()

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return commit_proc
            return fetch_proc

        async def mock_wait_for(coro, **kwargs):
            # First wait_for is for git fetch
            raise asyncio.TimeoutError()

        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                with patch("asyncio.wait_for", side_effect=mock_wait_for):
                    with patch.object(update_service, "_rollback_milo_to_commit", return_value=True) as mock_rollback:
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "timed out" in result["error"].lower()
        # The rollback must have actually run, not merely be claimed in the message.
        mock_rollback.assert_awaited_once()
        assert "rolled back" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_git_fetch_failure(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.0.1"}},
            "latest": {"version": "1.0.0"}
        }

        commit_proc = _make_mock_proc(stdout=b"abc123\n")
        fetch_proc = _make_mock_proc(returncode=1, stderr=b"fatal: error")

        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return commit_proc
            return fetch_proc

        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "Git fetch failed" in result["error"]


class TestMiloAppPythonDependencies:
    """The pip step of a Milo update — the only step that can brick the unit.

    A release adding a direct dependency (aiohttp-retry did, imported by
    sources/radio/shazam.py) leaves the backend importing a module absent from
    the venv on the post-update reboot. Two things are asserted: pip is run
    against a requirements file this checkout really ships, and a pip that
    fails aborts into the rollback instead of rebooting on a half-built venv.
    """

    @staticmethod
    def _routed_exec(*, pip_proc=None):
        """Answer each subprocess by command, recording every argv.

        git rev-parse must yield a commit or the rollback branch is never
        armed; git status must yield nothing or the flow stops on a dirty tree.
        """
        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            if args[0].endswith("pip3"):
                return pip_proc or _make_mock_proc()
            if "rev-parse" in args:
                return _make_mock_proc(stdout=b"abc123def456\n")
            return _make_mock_proc()

        return calls, mock_exec

    @staticmethod
    def _pip_call(calls):
        return next((c for c in calls if c[0].endswith("pip3")), None)

    @contextmanager
    def _milo_flow(self, service, *, pip_proc=None):
        """Everything outside the update itself, stubbed."""
        calls, mock_exec = self._routed_exec(pip_proc=pip_proc)
        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch.object(service, "_sync_system_files"))
            stack.enter_context(patch.object(service, "_run_deploy", return_value=(True, "")))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            yield calls

    @pytest.mark.asyncio
    async def test_pip_runs_against_a_requirements_file_the_repo_ships(self, update_service):
        """The path was `backend/requirements.txt`, which has never existed.

        Anchored on the tree instead of on a literal: whatever path the service
        builds, resolved inside this checkout, must be a file that is there.
        """
        status = {"installed": {"versions": {"main": "0.0.1"}}, "latest": {"version": "1.0.0"}}

        with self._milo_flow(update_service) as calls:
            result = await update_service._update_milo_app(status)

        assert result["success"] is True
        pip_call = self._pip_call(calls)
        assert pip_call is not None, f"pip never ran; commands were {calls}"
        assert pip_call[1:3] == ("install", "-r")

        git_path = update_service.programs["milo"]["git_path"]
        requirements = Path(pip_call[3]).relative_to(git_path)
        assert (REPO_ROOT / requirements).is_file(), f"{requirements} is not in the repo"

    @pytest.mark.asyncio
    async def test_pip_failure_aborts_the_update_and_rolls_back(self, update_service):
        """The step had no returncode check, so a broken venv still rebooted."""
        status = {"installed": {"versions": {"main": "0.0.1"}}, "latest": {"version": "1.0.0"}}
        pip_proc = _make_mock_proc(returncode=1, stderr=b"No matching distribution found")

        with ExitStack() as stack:
            calls = stack.enter_context(self._milo_flow(update_service, pip_proc=pip_proc))
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "pip install failed" in result["error"]
        assert "No matching distribution found" in result["error"]
        rollback.assert_awaited_once()
        # The reboot must not have been reached: only pip ran after the build.
        assert self._pip_call(calls) == calls[-1]

    @pytest.mark.asyncio
    async def test_pip_timeout_kills_the_process(self, update_service):
        """A hung pip must not be left running behind a failed update."""
        status = {"installed": {"versions": {"main": "0.0.1"}}, "latest": {"version": "1.0.0"}}
        pip_proc = _make_mock_proc()
        pip_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with ExitStack() as stack:
            stack.enter_context(self._milo_flow(update_service, pip_proc=pip_proc))
            stack.enter_context(patch.object(update_service, "_rollback_milo_to_commit", return_value=True))
            result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "pip install timed out" in result["error"]
        pip_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_reinstalls_dependencies_from_the_same_path(self, update_service):
        """The rollback carried the same wrong path, so a rollback restored the
        code but left the venv holding the failed update's packages.
        """
        calls, mock_exec = self._routed_exec()

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch.object(update_service, "_sync_system_files"))
            stack.enter_context(patch.object(update_service, "_restart_service", return_value=True))
            stack.enter_context(patch.object(update_service._systemd, "restart_self", return_value=True))
            result = await update_service._rollback_milo_to_commit("abc123def456")

        assert result is True
        pip_call = self._pip_call(calls)
        assert pip_call is not None, f"pip never ran; commands were {calls}"

        git_path = update_service.programs["milo"]["git_path"]
        requirements = Path(pip_call[3]).relative_to(git_path)
        assert (REPO_ROOT / requirements).is_file(), f"{requirements} is not in the repo"


class TestMiloAppLastSteps:
    """The two steps of a Milo update whose result was never read.

    `_sync_system_files` only warned, and `_run_deploy("reboot")` was called
    without looking at what it answered — so an update that copied no unit file,
    or one the reboot refused, still reported `success: True` to the UI and to
    Milo-Mac. The rollback's npm steps had neither timeout nor returncode check,
    which is the same silence one layer down: a rollback that rebuilt nothing
    logged "completed successfully".
    """

    STATUS = {"installed": {"versions": {"main": "0.0.1"}}, "latest": {"version": "1.0.0"}}

    @staticmethod
    def _exec(*, npm_proc=None):
        """Answer each subprocess by command; npm is the one under test here."""
        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
            if args[0] == "npm":
                return npm_proc or _make_mock_proc()
            if "rev-parse" in args:
                return _make_mock_proc(stdout=b"abc123def456\n")
            return _make_mock_proc()

        return calls, mock_exec

    @pytest.mark.asyncio
    async def test_a_failed_system_files_sync_aborts_the_update(self, update_service):
        """The units the new code expects were not copied — that is not a warning."""
        calls, mock_exec = self._exec()

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            deploy = stack.enter_context(
                patch.object(update_service, "_run_deploy", return_value=(False, "cp: permission denied"))
            )
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(self.STATUS)

        assert result["success"] is False
        assert "cp: permission denied" in result["error"]
        rollback.assert_awaited_once()
        # The reboot must never have been asked for.
        assert [c.args[0] for c in deploy.await_args_list] == ["sync-system-files"]

    @pytest.mark.asyncio
    async def test_a_refused_reboot_is_reported_without_rolling_back(self, update_service):
        """The code is pulled, built and synced — undoing it over a refused
        reboot would be worse than reporting it. Only the answer changes.
        """
        calls, mock_exec = self._exec()

        async def deploy(*args, **kwargs):
            return (False, "sudo: a password is required") if args[0] == "reboot" else (True, "")

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(patch.object(update_service, "_run_deploy", side_effect=deploy))
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(self.STATUS)

        assert result["success"] is False
        assert "reboot" in result["error"].lower()
        assert "sudo: a password is required" in result["error"]
        rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rollback_that_cannot_rebuild_the_frontend_fails(self, update_service):
        """A rollback leaving a broken dist/ must not log "completed successfully"."""
        npm_proc = _make_mock_proc(returncode=1, stderr=b"ENOSPC: no space left on device")
        calls, mock_exec = self._exec(npm_proc=npm_proc)

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch.object(update_service, "_sync_system_files"))
            restart = stack.enter_context(
                patch.object(update_service._systemd, "restart_self", return_value=True)
            )
            result = await update_service._rollback_milo_to_commit("abc123def456")

        assert result is False
        # Nothing after the failed build ran: no pip, no self-restart.
        assert not [c for c in calls if c[0].endswith("pip3")]
        restart.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_hung_npm_does_not_freeze_the_rollback(self, update_service):
        """Unbounded, this is the plausible one: the backend is never restarted
        and the update key stays in active_updates, so the UI shows an update
        running forever.
        """
        npm_proc = _make_mock_proc()
        npm_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        calls, mock_exec = self._exec(npm_proc=npm_proc)

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch.object(update_service, "_sync_system_files"))
            result = await update_service._rollback_milo_to_commit("abc123def456")

        assert result is False
        npm_proc.kill.assert_called_once()


class TestVerifyBinaryProgram:
    """Tests for _verify_binary_program()"""

    @pytest.mark.asyncio
    async def test_binary_missing(self, update_service):
        config = update_service.programs["go-librespot"]
        with patch("pathlib.Path.exists", return_value=False):
            result = await update_service._verify_binary_program(config, True)
        assert result["success"] is False
        assert "binary not found" in result["error"]

    @pytest.mark.asyncio
    async def test_service_not_running(self, update_service):
        config = update_service.programs["camilladsp"]
        proc = _make_mock_proc(stdout=b"inactive\n")
        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", return_value=proc):
                result = await update_service._verify_binary_program(config, True)
        assert result["success"] is False
        assert "not running" in result["error"]

    @pytest.mark.asyncio
    async def test_service_not_checked_when_left_stopped(self, update_service):
        """A go-librespot update that deliberately left the service stopped must
        still verify as successful — only the binary is checked.
        """
        config = update_service.programs["go-librespot"]
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(update_service, "_is_service_active") as mock_active:
                result = await update_service._verify_binary_program(config, False)
        assert result["success"] is True
        mock_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verification_success(self, update_service):
        config = update_service.programs["navidrome"]
        proc = _make_mock_proc(stdout=b"active\n")
        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", return_value=proc):
                result = await update_service._verify_binary_program(config, True)
        assert result["success"] is True


class TestVerifyShairportSyncUpdate:
    """Tests for _verify_shairport_sync_update()"""

    @pytest.mark.asyncio
    async def test_binary_missing(self, update_service):
        config = {"binary_path": "/usr/local/bin/shairport-sync", "service_name": "milo-airplay.service"}
        with patch("pathlib.Path.exists", return_value=False):
            result = await update_service._verify_shairport_sync_update(config, service_was_active=True)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_service_not_running_when_expected(self, update_service):
        config = {"binary_path": "/usr/local/bin/shairport-sync", "service_name": "milo-airplay.service"}
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(update_service, "_is_service_active", return_value=False):
                result = await update_service._verify_shairport_sync_update(config, service_was_active=True)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_service_active(self, update_service):
        config = {"binary_path": "/usr/local/bin/shairport-sync", "service_name": "milo-airplay.service"}
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(update_service, "_is_service_active", return_value=True):
                result = await update_service._verify_shairport_sync_update(config, service_was_active=True)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_service_was_inactive(self, update_service):
        config = {"binary_path": "/usr/local/bin/shairport-sync", "service_name": "milo-airplay.service"}
        with patch("pathlib.Path.exists", return_value=True):
            result = await update_service._verify_shairport_sync_update(config, service_was_active=False)
        assert result["success"] is True


class TestRollbackShairportSync:
    """Tests for _rollback_shairport_sync()"""

    @pytest.mark.asyncio
    async def test_no_backup(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service"
        }
        (tmp_path / "backups").mkdir()

        result = await update_service._rollback_shairport_sync(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_restarts_if_was_active(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=True) as mock_start:
                    result = await update_service._rollback_shairport_sync(config, service_was_active=True)

        assert result is True
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_no_restart_if_inactive(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service") as mock_start:
                    result = await update_service._rollback_shairport_sync(config, service_was_active=False)

        assert result is True
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_restart_is_not_a_rollback(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=False):
                    result = await update_service._rollback_shairport_sync(config, service_was_active=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_stop_leaves_the_binary_alone(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")

        with patch.object(update_service, "_stop_service", return_value=False):
            with patch.object(update_service, "_run_deploy") as mock_deploy:
                result = await update_service._rollback_shairport_sync(config, service_was_active=True)

        assert result is False
        mock_deploy.assert_not_called()


class TestUpdateShairportSync:
    """The compile-from-source path, on its recovery branches.

    Nothing is compiled here: the mocks stand for the outside world the flow
    drives (systemd, the tarball, configure/make, the deploy wrapper) and the
    assertion is what the flow told the caller once its rollback had run.
    """

    STATUS = {
        "installed": {"versions": {"main": "4.3.7"}},
        "latest": {"version": "4.3.8", "tag_name": "4.3.8"},
    }

    @staticmethod
    @contextmanager
    def _flow(service, *, install=None, rollback=True):
        returns = {
            "_is_service_active": True,
            "_backup_shairport_sync": {"success": True},
            "_download_shairport_sync_source": {
                "success": True, "temp_dir": "/tmp/sps", "source_dir": "/tmp/sps/src"
            },
            "_configure_shairport_sync": {"success": True},
            "_compile_shairport_sync": {"success": True},
            "_install_shairport_sync": install or {"success": True},
            "_verify_shairport_sync_update": {"success": True},
            "_stop_service": True,
            "_start_service": True,
            "_rollback_shairport_sync": rollback,
            "_cleanup_temp_files": None,
        }
        with ExitStack() as stack:
            yield {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }

    @pytest.mark.asyncio
    async def test_a_rollback_that_failed_reads_differently_from_one_that_worked(self, update_service):
        install = {"success": False, "error": "Installation failed: install-binary refused"}

        with self._flow(update_service, install=install, rollback=True):
            restored = await update_service._update_shairport_sync(self.STATUS)

        with self._flow(update_service, install=install, rollback=False):
            stranded = await update_service._update_shairport_sync(self.STATUS)

        assert restored["success"] is stranded["success"] is False
        assert "install-binary refused" in restored["error"]
        assert restored["error"] != stranded["error"]
        assert "manual intervention" in stranded["error"].lower()


class TestUpdateQobuzProxy:
    """The venv-upgrade path, on its recovery branches."""

    STATUS = {
        "installed": {"versions": {"main": "0.4.0"}},
        "latest": {"version": "0.5.0", "tag_name": "v0.5.0"},
    }

    @staticmethod
    @contextmanager
    def _flow(service, *, pip=(True, ""), start=True, rollback=True):
        """_run_local serves the pip upgrade then the patch script, in that order."""
        with ExitStack() as stack:
            mocks = {
                "_run_local": stack.enter_context(patch.object(
                    service, "_run_local", side_effect=[pip, (True, "")]
                )),
                "_start_service": stack.enter_context(patch.object(
                    service, "_start_service", return_value=start
                )),
            }
            for name, value in (
                ("_is_service_active", True),
                ("_backup_qobuz_venv", {"success": True}),
                ("_verify_qobuz_update", {"success": True}),
                ("_stop_service", True),
                ("_rollback_qobuz_venv", rollback),
                ("_cleanup_qobuz_backup", None),
            ):
                mocks[name] = stack.enter_context(patch.object(service, name, return_value=value))
            yield mocks

    @pytest.mark.asyncio
    async def test_a_rollback_that_failed_reads_differently_from_one_that_worked(self, update_service):
        pip = (False, "No matching distribution")

        with self._flow(update_service, pip=pip, rollback=True):
            restored = await update_service._update_qobuz_proxy(self.STATUS)

        with self._flow(update_service, pip=pip, rollback=False):
            stranded = await update_service._update_qobuz_proxy(self.STATUS)

        assert restored["success"] is stranded["success"] is False
        assert restored["error"] != stranded["error"]
        assert "manual intervention" in stranded["error"].lower()

    @pytest.mark.asyncio
    async def test_a_sidecar_that_does_not_come_back_is_not_a_successful_update(self, update_service):
        """The venv is upgraded, patched and verified — only the restart failed.
        Reporting success left Qobuz selectable and dead, and rolling a verified
        venv back over a systemd refusal would be worse, so it is not done.
        """
        with self._flow(update_service, start=False) as mocks:
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result["success"] is False
        assert "restart" in result["error"].lower()
        mocks["_rollback_qobuz_venv"].assert_not_awaited()


class TestSnapcastComponentDownloadTempDir:
    """A download that fails must not leave its scratch directory in /tmp.

    Only the success path hands `temp_dir` back, and _cleanup_temp_files is what
    releases it. Every other exit owns the directory itself: on this appliance
    /tmp is a tmpfs, and a Multiroom update retried after a network failure
    grows the leak once per attempt with nothing that ever collects it.
    """

    @staticmethod
    @contextmanager
    def _sandboxed_tmp(service, tmp_path):
        """Redirect mkdtemp into tmp_path and record every directory it creates."""
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(dir=None):  # noqa: A002 -- mirrors tempfile.mkdtemp's kwarg
            path = real_mkdtemp(dir=str(tmp_path))
            created.append(Path(path))
            return path

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                service, "_get_debian_codename", AsyncMock(return_value="bookworm")
            ))
            stack.enter_context(patch("backend.core.updates.update.tempfile.mkdtemp", fake_mkdtemp))
            yield created

    @pytest.mark.asyncio
    async def test_unknown_component_creates_no_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(update_service, tmp_path) as created:
            result = await update_service._download_snapcast_component("snapfoo", "0.31.0")

        assert result["success"] is False
        assert "Unknown component" in result["error"]
        assert created == [], "a scratch directory was created for a component we reject"

    @pytest.mark.asyncio
    async def test_failed_download_removes_its_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(update_service, tmp_path) as created:
            with patch("backend.core.updates.update.aiohttp.ClientSession",
                       side_effect=RuntimeError("network down")):
                result = await update_service._download_snapcast_component("snapclient", "0.31.0")

        assert result["success"] is False
        assert "network down" in result["error"]
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived the failed download"
