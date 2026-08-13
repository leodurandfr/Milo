# backend/tests/test_update_service.py
"""
Tests for UpdateService — update orchestration, backup/restore, service management.
"""
import asyncio
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
    @pytest.mark.parametrize("program_key", BINARY_PROGRAMS)
    async def test_dispatches_to_binary_program(self, update_service, program_key):
        """The three tarball-binary programs must all reach the shared flow.

        A missing "asset_url" in the catalog would silently fall through to
        "Update handler not implemented" instead.
        """
        status = {"update_available": True, "latest": {"version": "0.7.0"}}
        expected_result = {"success": True, "message": "updated"}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_binary_program", return_value=expected_result) as mock_update:
                result = await update_service.update_program(program_key)

        assert mock_update.await_args.args[0] == program_key
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_dispatches_to_multiroom(self, update_service):
        status = {"update_available": True, "latest": {"version": "0.29.0"}}
        expected_result = {"success": True}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_multiroom", return_value=expected_result) as mock_update:
                await update_service.update_program("multiroom")

        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_to_shairport_sync(self, update_service):
        status = {"update_available": True, "latest": {"version": "4.3.4"}}
        expected_result = {"success": True}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_shairport_sync", return_value=expected_result) as mock_update:
                await update_service.update_program("shairport-sync")

        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_to_milo(self, update_service):
        status = {"update_available": True, "latest": {"version": "1.0.0"}}
        expected_result = {"success": True}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_milo_app", return_value=expected_result) as mock_update:
                await update_service.update_program("milo")

        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_caught(self, update_service):
        with patch.object(update_service, "get_program_full_status",
                          side_effect=Exception("boom")):
            result = await update_service.update_program("go-librespot")

        assert result["success"] is False
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, update_service):
        callback = AsyncMock()
        status = {"update_available": True, "latest": {"version": "0.7.0"}}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_binary_program", return_value={"success": True}):
                await update_service.update_program("go-librespot", progress_callback=callback)

        callback.assert_called_with("Initializing update...", 0)


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
    def _flow(service, *, service_active, backup=None, download=None, deploy=(True, ""), stop=True):
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
            "_rollback_binary_program": True,
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
        callback = AsyncMock()

        with self._flow(update_service, service_active=True) as mocks:
            result = await update_service._update_binary_program(program_key, status, callback)

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


class TestUpdateMultiroom:
    """Tests for _update_multiroom() orchestration"""

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
        status = {
            "installed": {"versions": {"main": "0.27.0"}},
            "latest": {"version": "0.28.0"}
        }

        with patch.object(update_service, "_download_snapcast_component", return_value={
            "success": True, "deb_path": "/tmp/pkg.deb", "temp_dir": "/tmp/tmp"
        }):
            with patch.object(update_service, "_stop_service", return_value=True):
                call_count = 0
                async def mock_install(path):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        return {"success": True}
                    return {"success": False, "error": "install failed"}

                with patch.object(update_service, "_install_deb_package", side_effect=mock_install):
                    with patch.object(update_service, "_start_service", return_value=True):
                        with patch.object(update_service, "_cleanup_temp_files"):
                            result = await update_service._update_multiroom(status)

        # The discriminator is which half failed: snapserver installed, snapclient
        # did not. "success is False" alone would also pass if snapserver had
        # failed, which is a different outcome (nothing was replaced).
        assert result["success"] is False
        assert "snapclient failed" in result["error"]
        assert call_count == 2


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
