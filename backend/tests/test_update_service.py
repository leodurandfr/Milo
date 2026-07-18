# backend/tests/test_update_service.py
"""
Tests for UpdateService — update orchestration, backup/restore, service management.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from backend.core.updates.update import UpdateService
from backend.core.systemd import SystemdServiceManager


@pytest.fixture
def update_service():
    """Fresh UpdateService instance.

    Injects a real SystemdServiceManager so the service-control helpers (which
    now delegate to it) still exercise the subprocess layer patched by tests.
    """
    with patch.dict("os.environ", {}, clear=True):
        return UpdateService(systemd_manager=SystemdServiceManager())


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

    def test_update_config_present(self, update_service):
        expected_keys = {"milo", "go-librespot", "shairport-sync", "multiroom", "camilladsp", "qobuz-proxy", "navidrome"}
        assert set(update_service.update_config.keys()) == expected_keys


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
    async def test_dispatches_to_go_librespot(self, update_service):
        status = {"update_available": True, "latest": {"version": "0.7.0"}}
        expected_result = {"success": True, "message": "updated"}

        with patch.object(update_service, "get_program_full_status", return_value=status):
            with patch.object(update_service, "_update_go_librespot", return_value=expected_result) as mock_update:
                result = await update_service.update_program("go-librespot")

        mock_update.assert_called_once()
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
            with patch.object(update_service, "_update_go_librespot", return_value={"success": True}):
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


class TestBackupGoLibrespot:
    """Tests for _backup_go_librespot()"""

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

        result = await update_service._backup_go_librespot(config)
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

        result = await update_service._backup_go_librespot(config)
        assert result["success"] is True
        assert (tmp_path / "backups" / "go-librespot.backup").exists()
        assert not (tmp_path / "backups" / "config.yml.backup").exists()

    @pytest.mark.asyncio
    async def test_backup_missing_binary(self, update_service, tmp_path):
        config = {
            "binary_path": str(tmp_path / "nonexistent"),
            "config_path": str(tmp_path / "config.yml"),
            "backup_path": str(tmp_path / "backups")
        }
        result = await update_service._backup_go_librespot(config)
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


class TestRollbackGoLibrespot:
    """Tests for _rollback_go_librespot()"""

    @pytest.mark.asyncio
    async def test_no_backup_returns_false(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/go-librespot",
            "service_name": "milo-spotify.service"
        }
        (tmp_path / "backups").mkdir()

        with patch.object(update_service, "_stop_service", return_value=True):
            result = await update_service._rollback_go_librespot(config, service_was_active=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_rollback_with_active_service(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/go-librespot",
            "service_name": "milo-spotify.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=True):
                    result = await update_service._rollback_go_librespot(config, service_was_active=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_rollback_inactive_service_not_started(self, update_service, tmp_path):
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/go-librespot",
            "service_name": "milo-spotify.service"
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch.object(update_service, "_stop_service", return_value=True):
                with patch.object(update_service, "_start_service", return_value=True) as mock_start:
                    result = await update_service._rollback_go_librespot(config, service_was_active=False)

        assert result is True
        mock_start.assert_not_called()


class TestInstallGoLibrespotBinary:
    """Tests for _install_go_librespot_binary()"""

    @pytest.mark.asyncio
    async def test_successful_install(self, update_service):
        proc = _make_mock_proc()
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._install_go_librespot_binary("/tmp/go-librespot")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_copy_failure(self, update_service):
        proc = _make_mock_proc(returncode=1, stderr=b"permission denied")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await update_service._install_go_librespot_binary("/tmp/go-librespot")
        assert result["success"] is False
        assert "permission denied" in result["error"]


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


class TestUpdateGoLibrespot:
    """Tests for _update_go_librespot() orchestration"""

    @pytest.mark.asyncio
    async def test_successful_update_service_active(self, update_service):
        status = {
            "installed": {"versions": {"main": "0.6.1"}},
            "latest": {"version": "0.7.0"}
        }
        callback = AsyncMock()

        with patch.object(update_service, "_is_service_active", return_value=True):
            with patch.object(update_service, "_backup_go_librespot", return_value={"success": True}):
                with patch.object(update_service, "_download_go_librespot", return_value={
                    "success": True, "binary_path": "/tmp/bin", "temp_dir": "/tmp/dl"
                }):
                    with patch.object(update_service, "_stop_service", return_value=True):
                        with patch.object(update_service, "_install_go_librespot_binary", return_value={"success": True}):
                            with patch.object(update_service, "_start_service", return_value=True):
                                with patch.object(update_service, "_verify_go_librespot_update", return_value={"success": True}):
                                    with patch.object(update_service, "_cleanup_temp_files"):
                                        with patch("pathlib.Path.exists", return_value=False):
                                            result = await update_service._update_go_librespot(status, callback)

        assert result["success"] is True
        assert result["new_version"] == "0.7.0"
        assert result["service_restarted"] is True

    @pytest.mark.asyncio
    async def test_backup_failure_aborts(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with patch.object(update_service, "_is_service_active", return_value=False):
            with patch.object(update_service, "_backup_go_librespot", return_value={
                "success": False, "error": "Backup failed"
            }):
                result = await update_service._update_go_librespot(status)

        assert result["success"] is False
        assert "Backup failed" in result["error"]

    @pytest.mark.asyncio
    async def test_download_failure_aborts(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with patch.object(update_service, "_is_service_active", return_value=False):
            with patch.object(update_service, "_backup_go_librespot", return_value={"success": True}):
                with patch.object(update_service, "_download_go_librespot", return_value={
                    "success": False, "error": "HTTP 404"
                }):
                    result = await update_service._update_go_librespot(status)

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_install_failure_triggers_rollback(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with patch.object(update_service, "_is_service_active", return_value=True):
            with patch.object(update_service, "_backup_go_librespot", return_value={"success": True}):
                with patch.object(update_service, "_download_go_librespot", return_value={
                    "success": True, "binary_path": "/tmp/bin", "temp_dir": "/tmp/dl"
                }):
                    with patch.object(update_service, "_stop_service", return_value=True):
                        with patch.object(update_service, "_install_go_librespot_binary", return_value={
                            "success": False, "error": "install failed"
                        }):
                            with patch.object(update_service, "_rollback_go_librespot") as mock_rollback:
                                result = await update_service._update_go_librespot(status)

        mock_rollback.assert_called_once()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_service_inactive_skips_stop_start(self, update_service):
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with patch.object(update_service, "_is_service_active", return_value=False):
            with patch.object(update_service, "_backup_go_librespot", return_value={"success": True}):
                with patch.object(update_service, "_download_go_librespot", return_value={
                    "success": True, "binary_path": "/tmp/bin", "temp_dir": "/tmp/dl"
                }):
                    with patch.object(update_service, "_install_go_librespot_binary", return_value={"success": True}):
                        with patch.object(update_service, "_stop_service") as mock_stop:
                            with patch.object(update_service, "_start_service") as mock_start:
                                with patch.object(update_service, "_cleanup_temp_files"):
                                    with patch("pathlib.Path.exists", return_value=False):
                                        result = await update_service._update_go_librespot(status)

        mock_stop.assert_not_called()
        mock_start.assert_not_called()
        assert result["success"] is True
        assert result["service_restarted"] is False


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

        assert result["success"] is False
        assert result.get("partial_success") is True


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
                    with patch.object(update_service, "_rollback_milo_to_commit", return_value=True):
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            result = await update_service._update_milo_app(status)

        assert result["success"] is False
        assert "timed out" in result["error"].lower()
        assert result.get("rolled_back") is True

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


class TestVerifyGoLibrespotUpdate:
    """Tests for _verify_go_librespot_update()"""

    @pytest.mark.asyncio
    async def test_binary_missing(self, update_service):
        with patch("pathlib.Path.exists", return_value=False):
            result = await update_service._verify_go_librespot_update("0.7.0")
        assert result["success"] is False
        assert "binary not found" in result["error"]

    @pytest.mark.asyncio
    async def test_service_not_running(self, update_service):
        proc = _make_mock_proc(stdout=b"inactive\n")
        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", return_value=proc):
                result = await update_service._verify_go_librespot_update("0.7.0")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_verification_success(self, update_service):
        proc = _make_mock_proc(stdout=b"active\n")
        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", return_value=proc):
                result = await update_service._verify_go_librespot_update("0.7.0")
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
