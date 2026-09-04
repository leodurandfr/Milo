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
import hashlib
import logging
import tarfile
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.update import UpdateService
from backend.core.systemd import SystemdServiceManager

# This checkout's root, so a path the service builds can be checked against the
# tree that actually ships rather than against a literal repeated in the test.
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def update_service(mock_settings_service):
    """Fresh UpdateService instance.

    Injects a real SystemdServiceManager so the service-control helpers (which
    now delegate to it) still exercise the subprocess layer patched by tests.
    The satellite service is a double: it reaches a second physical Pi, and the
    tests that care about the fleet push assert what the update did to it.
    """
    satellites = Mock()
    satellites.push_client_app_to_fleet = AsyncMock(return_value=[])
    mock_settings_service._storage["updates.forced_versions"] = {}
    with patch.dict("os.environ", {}, clear=True):
        return UpdateService(systemd_manager=SystemdServiceManager(),
                             satellite_update_service=satellites,
                             settings_service=mock_settings_service)


# The programs served by the one shared _update_binary_program flow. Kept as a
# literal rather than derived from the catalog so a program dropping out of the
# flow is a visible test edit, not a silently shrinking parametrization.
BINARY_PROGRAMS = ["go-librespot", "camilladsp", "navidrome"]

# What the offer hands `_update_milo_app`. The release is named by its TAG, not
# only by its version: the tag is what `git checkout` is given and what the
# frontend asset URL is built from, so a status carrying only a version is a
# status the install cannot act on.
MILO_STATUS = {
    "installed": {"versions": {"main": "0.1.0"}, "raw_version": "v0.1.0"},
    "latest": {"status": "success", "version": "0.2.0", "tag_name": "v0.2.0"},
}


@contextmanager
def frontend_from_the_release(service):
    """Stand in for the release-asset install of `frontend/dist`.

    It is a download plus a checksum plus a directory swap inside the live
    checkout — its own tests drive it directly. Every flow test above it needs
    it to have happened, not to happen.
    """
    with patch.object(service, "_install_release_frontend", new=AsyncMock()) as install:
        yield install


def _recording_aiofiles_open(sink):
    """Stand in for `aiofiles.open` and bank what was written, by path.

    The real call targets `/var/lib/milo/shairport-sync-version`, which the
    conftest guard refuses — so the choice is between asserting nothing and
    standing in for the writer. This banks the payload, which is the thing that
    matters: the catalog reads that file back to decide the installed version.
    """
    class _Writer:
        def __init__(self, path):
            self.path = path

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def write(self, data):
            sink[str(self.path)] = data

    def _open(path, mode="r", *a, **kw):
        return _Writer(path)

    return _open


@pytest.fixture(autouse=True)
def never_a_real_process():
    """The default spawn RAISES, for the whole file.

    This service is the one that modifies the appliance: its argv includes
    `git -C /home/milo/milo reset --hard`, `git pull`, `sudo milo-deploy-update`,
    `npm run build`, `make install DESTDIR=…` and `tar -xzf`, and `git_path` is
    the live production checkout that neighbouring sessions share. A spawn that
    escapes a double here is not a slow test, it is an irreversible edit of the
    working tree. Every test below re-patches this name for its own scope; what
    this covers is everything that does not.
    """
    async def _refuse(program, *args, **kwargs):
        raise AssertionError(
            f"a real process was spawned: {program} {' '.join(map(str, args))}"
        )

    with patch("asyncio.create_subprocess_exec", new=_refuse):
        yield


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


@contextmanager
def github_unreachable():
    """Stand in for the GitHub transport, refusing every call.

    A Milō update now reconciles the dependency set before it reboots, and that
    reads `releases/latest` for each dependency. A test about the app's own
    steps must stand in for that transport rather than reach it — this suite
    runs ON the appliance, and the conftest guard fails the run otherwise.
    Refusing makes every dependency read as "no update available", so the
    reconciliation is a no-op and the app steps are what is left under test.
    """
    with patch("backend.core.updates.update.aiohttp.ClientSession",
               side_effect=Exception("no network in tests")):
        yield


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
    async def test_a_unit_that_will_not_stop_aborts_before_the_deb(self, update_service):
        """The only stop in the subsystem whose verdict used to be dropped.

        A unit that refuses to stop keeps its image loaded, so the .deb lands
        under the running process; `_restore_multiroom_services` then "starts"
        something that never stopped and reports it restored, and the update
        answers success. The row reads the new version off the new binary on
        disk while the process serving audio is still the old one.
        """
        active = dict.fromkeys(self.SERVICES, True)

        with self._flow(update_service, active=active) as mocks:
            mocks["_stop_service"].return_value = False
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self.SERVICES[0] in result["error"]
        assert mocks["_install_deb_package"].await_count == 0

    @pytest.mark.asyncio
    async def test_the_units_stopped_before_that_one_are_started_back(self, update_service):
        """Aborting mid-phase must leave the appliance as it was, not half stopped.

        The second unit refuses; the first was already stopped by then, and is
        the one the room needs back.
        """
        active = dict.fromkeys(self.SERVICES, True)

        with self._flow(update_service, active=active) as mocks:
            mocks["_stop_service"].side_effect = [True, False]
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == list(self.SERVICES)
        assert "restored" in result["error"].lower()

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
    """The gates a Milo update passes before it touches anything."""

    @pytest.mark.asyncio
    async def test_not_git_repo(self, update_service):
        with patch("pathlib.Path.exists", return_value=False):
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "git repository" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_dirty_working_tree(self, update_service):
        """`git checkout --force <tag>` over uncommitted work destroys it."""
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
                result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "local changes" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_a_failed_git_status_aborts_before_the_checkout(self, update_service):
        """A non-zero `git status --porcelain` yields the same empty stdout as a
        clean tree. Reading only stdout let the update check out a tag over
        local changes it never managed to look for.
        """
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
                result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "git status" in result["error"].lower()
        assert not any("checkout" in args for args in spawned)

    @pytest.mark.asyncio
    async def test_a_fetch_that_hangs_is_reported_without_rolling_back(self, update_service):
        """Two minutes is the ceiling; a stalled fetch against an unreachable
        remote would otherwise hold the update open for ever.

        And there is nothing to roll back: the fetch writes into `.git` and
        moves no ref, so the tree is still on the release it booted. Rolling
        back here used to mean a full reinstall and a reboot over a network
        blip — the timeout and the non-zero exit now answer the same way,
        which is the answer the non-zero exit always gave.
        """
        commit_proc = _make_mock_proc(stdout=b"abc123\n")
        fetch_proc = _make_mock_proc()

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return commit_proc if call_count == 1 else fetch_proc

        async def mock_wait_for(coro, **kwargs):
            raise asyncio.TimeoutError()

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.wait_for", side_effect=mock_wait_for))
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "timed out" in result["error"].lower()
        assert "Git fetch failed" in result["error"]
        rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_git_fetch_failure(self, update_service):
        commit_proc = _make_mock_proc(stdout=b"abc123\n")
        fetch_proc = _make_mock_proc(returncode=1, stderr=b"fatal: error")

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return commit_proc if call_count == 1 else fetch_proc

        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                result = await update_service._update_milo_app(MILO_STATUS)

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
            stack.enter_context(frontend_from_the_release(service))
            stack.enter_context(patch.object(service, "_sync_system_files"))
            stack.enter_context(patch.object(service, "_run_deploy", return_value=(True, "")))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(github_unreachable())
            yield calls

    @pytest.mark.asyncio
    async def test_pip_runs_against_a_requirements_file_the_repo_ships(self, update_service):
        """The path was `backend/requirements.txt`, which has never existed.

        Anchored on the tree instead of on a literal: whatever path the service
        builds, resolved inside this checkout, must be a file that is there.
        """
        with self._milo_flow(update_service) as calls:
            result = await update_service._update_milo_app(MILO_STATUS)

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
        pip_proc = _make_mock_proc(returncode=1, stderr=b"No matching distribution found")

        with ExitStack() as stack:
            calls = stack.enter_context(self._milo_flow(update_service, pip_proc=pip_proc))
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "pip install failed" in result["error"]
        assert "No matching distribution found" in result["error"]
        rollback.assert_awaited_once()
        # The reboot must not have been reached: only pip ran after the build.
        assert self._pip_call(calls) == calls[-1]

    @pytest.mark.asyncio
    async def test_pip_timeout_kills_the_process(self, update_service):
        """A hung pip must not be left running behind a failed update."""
        pip_proc = _make_mock_proc()
        pip_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with ExitStack() as stack:
            stack.enter_context(self._milo_flow(update_service, pip_proc=pip_proc))
            stack.enter_context(patch.object(update_service, "_rollback_milo_to_commit", return_value=True))
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "pip install failed" in result["error"]
        assert "Timed out" in result["error"]
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


class TestDependencyReconciliation:
    """A Milō update installs the app *and* the dependency set validated with it.

    One sequence, never one transaction and never deferred to the next boot.
    Deferring would put a multi-minute source compile behind a dark screen with
    the backend down; a transaction would mean composing seven independent
    rollbacks, whose half-applied states are worse than either end.

    The subtle half is *which* set. `git pull` replaces `dependencies.env` under
    a process that read it minutes earlier and cached its GitHub answers for an
    hour — so a reconciliation that skips either refresh compares the unit
    against the versions it already runs and does nothing at all, silently. That
    is the shape both of the first two tests exist to catch.

    Phases are patched rather than collaborators, per this file's header: what
    is under test here is which program was reached, with which version, and
    which was not reached at all.
    """

    # Version strings no manifest will ever hold, so a dispatch carrying one can
    # only have come from the file written by the test.
    BUMPED = "9.87.65"

    @classmethod
    def _manifest_bumping(cls, tmp_path, *keys):
        """The real manifest with the named lines rewritten to BUMPED.

        Derived, not retyped: a hand-written stand-in would stop resembling the
        file under test the first time its shape changed.
        """
        from backend.core.updates.dependency_versions import MANIFEST_PATH

        lines = MANIFEST_PATH.read_text().splitlines()
        out = list(lines)
        for key in keys:
            out = [f"{key}={cls.BUMPED}" if ln.startswith(f"{key}=") else ln for ln in out]
            assert any(ln == f"{key}={cls.BUMPED}" for ln in out), f"{key} is not declared"
        path = tmp_path / "dependencies.env"
        path.write_text("\n".join(out) + "\n")
        return path

    @staticmethod
    @contextmanager
    def _reconciling(service, manifest=None, *, installed=b"1.0.0", tag="v1.0.0", dispatch=None):
        """Drive the real status chain, and record what was dispatched.

        `installed` answers every version command and `tag` every GitHub fetch,
        so each program's own regex decides which of them read as installed —
        the same filter production applies.
        """
        dispatched = []
        answer = dispatch or (lambda *_a: {"success": True})

        async def record(program_key, status):
            dispatched.append((program_key, status["latest"]["version"]))
            result = answer(program_key, status)
            if result is None:
                raise RuntimeError("tar: unexpected EOF")
            return result

        async def exec_(*args, **kwargs):
            return _make_mock_proc(stdout=installed)

        class _Response:
            status = 200

            async def json(self):
                return {"tag_name": tag, "published_at": None, "html_url": None}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def get(self, *a, **kw):
                return _Response()

        with ExitStack() as stack:
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=exec_))
            stack.enter_context(
                patch("backend.core.updates.update.aiohttp.ClientSession", return_value=_Session())
            )
            stack.enter_context(patch.object(service, "_dispatch_update", side_effect=record))
            if manifest is not None:
                stack.enter_context(
                    patch("backend.core.updates.dependency_versions.MANIFEST_PATH", manifest)
                )
            yield dispatched

    @pytest.mark.asyncio
    async def test_the_manifest_is_re_read_after_the_pull(self, update_service, tmp_path):
        """The set that reaches the unit is the one the *pulled* commit declares.

        The process imported `dependencies.env` at startup; the pull replaced it
        seconds ago. Reconciling against the in-memory copy installs the set the
        unit already has, reports success, and leaves the bump the maintainer
        just shipped unapplied — on every unit, with nothing to notice.
        """
        before = update_service.programs["navidrome"]["validated_version"]
        assert before != self.BUMPED

        manifest = self._manifest_bumping(tmp_path, "NAVIDROME_VERSION")
        with self._reconciling(update_service, manifest) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == []
        assert ("navidrome", self.BUMPED) in dispatched

    @pytest.mark.asyncio
    async def test_a_warm_github_cache_does_not_hold_the_old_pin(self, update_service, tmp_path):
        """A bump lands inside the hour the fetch is cached for — always.

        The fetch happens when the settings screen is opened, minutes before the
        update it leads to. What is cached is the release GitHub returned; which
        version the unit should run is resolved on every call, so the pulled
        manifest applies to a cache that is still warm.
        """
        update_service._github_cache["github_navidrome"] = {
            "status": "success",
            "version": update_service.programs["navidrome"]["validated_version"],
            "tag_name": "v1.0.0",
            "published_at": None,
            "html_url": None,
        }
        update_service._last_github_fetch["github_navidrome"] = time.time()

        manifest = self._manifest_bumping(tmp_path, "NAVIDROME_VERSION")
        with self._reconciling(update_service, manifest) as dispatched:
            await update_service._reconcile_dependencies()

        assert ("navidrome", self.BUMPED) in dispatched

    @pytest.mark.asyncio
    async def test_a_dependency_already_at_the_validated_version_is_left_alone(self, update_service):
        """Reconciling is not reinstalling: the common case must touch nothing.

        Every dependency is at its validated version on a healthy unit, so a
        reconciliation that dispatched regardless would stop and restart every
        audio service — and recompile shairport-sync — on every app update.
        """
        installed = update_service.programs["navidrome"]["validated_version"].encode()
        with self._reconciling(update_service, installed=installed, tag="v1.0.0") as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == []
        assert [k for k, _ in dispatched if k == "navidrome"] == []

    @pytest.mark.asyncio
    async def test_a_version_forced_on_purpose_is_left_alone(self, update_service, mock_settings_service):
        """The trial must survive the Milo update it was started before.

        Milo updates land far more often than dependency bumps, so a
        reconciliation that overwrote the override would end most trials within
        hours — and silently, since the row would go back to reading "up to
        date" on the manifest's version.
        """
        forced = "9.87.65"
        update_service.programs["navidrome"]["validated_version"] = "0.63.2"
        mock_settings_service._storage["updates.forced_versions"] = {"navidrome": forced}

        with self._reconciling(update_service, installed=forced.encode(), tag="v1.0.0") as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == []
        assert [k for k, _ in dispatched if k == "navidrome"] == []

    @pytest.mark.asyncio
    async def test_a_unit_above_the_manifest_by_accident_is_brought_back(self, update_service):
        """The manifest is authoritative in both directions, not just upwards.

        A yanked release, a set rolled back, a half-applied install: nothing
        records those, and comparing "is the manifest newer" would leave the
        unit above it for good — indistinguishable, from then on, from a version
        someone chose.
        """
        validated = update_service.programs["navidrome"]["validated_version"]
        assert validated != "9.87.65"

        with self._reconciling(update_service, installed=b"9.87.65", tag="v1.0.0") as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == []
        assert ("navidrome", validated) in dispatched

    @pytest.mark.asyncio
    async def test_the_app_itself_is_never_reconciled(self, update_service, tmp_path):
        """`milo` reaching the dispatcher re-enters `_update_milo_app`.

        The reconciliation runs *inside* that flow, so dispatching `milo` would
        pull, rebuild and reboot from within a pull-rebuild-reboot — recursively,
        each level holding the update key the route claimed.
        """
        # A tag ahead of the installed version, so `milo` is exactly what the
        # loop *would* pick up: unpinned, and an update genuinely available. A
        # fixture where it is already current would leave this guard untested
        # while reading green.
        manifest = self._manifest_bumping(tmp_path, "NAVIDROME_VERSION")
        with self._reconciling(update_service, manifest, tag="v2.0.0") as dispatched:
            await update_service._reconcile_dependencies()

        reached = [k for k, _ in dispatched]
        assert "milo" not in reached
        assert "navidrome" in reached, "the loop dispatched nothing at all"

    @pytest.mark.asyncio
    async def test_one_failed_dependency_does_not_stop_the_others(self, update_service, tmp_path):
        """Reported, not fatal — and never fatal to the programs behind it.

        Each flow restores itself on failure, so a failed step leaves the unit
        on the previous version of that one dependency: the state every unit is
        already in for anything nobody clicked. Aborting the sequence there
        would leave the rest of the set behind for no gain.
        """
        manifest = self._manifest_bumping(tmp_path, "GO_LIBRESPOT_VERSION", "NAVIDROME_VERSION")

        def dispatch(program_key, status):
            if program_key == "go-librespot":
                return {"success": False, "error": "download failed"}
            return {"success": True}

        with self._reconciling(update_service, manifest, dispatch=dispatch) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == ["go-librespot"]
        # go-librespot precedes navidrome in the catalog, so navidrome having
        # been reached at all is the proof the loop did not stop on the failure.
        reached = [k for k, _ in dispatched]
        assert reached.index("go-librespot") < reached.index("navidrome")

    @pytest.mark.asyncio
    async def test_a_dependency_that_raises_is_caught(self, update_service, tmp_path):
        """This runs past the point where the app can still be rolled back.

        `_update_milo_app` rolls back to the original commit on any exception,
        and by the time the set is reconciled the app is pulled, built and
        synced. An exception escaping here would undo a good update over a
        dependency — and leave the dependencies that already moved ahead of the
        app that was just reverted.
        """
        manifest = self._manifest_bumping(tmp_path, "NAVIDROME_VERSION")

        with self._reconciling(update_service, manifest, dispatch=lambda *_a: None) as dispatched:
            failed = await update_service._reconcile_dependencies()

        # It returned at all — that is the assertion. Every program it reached
        # raised, and every one of them is reported rather than propagated.
        assert failed == [k for k, _ in dispatched]
        assert "navidrome" in failed


    @pytest.mark.asyncio
    async def test_the_set_is_installed_after_the_sync_and_before_the_reboot(self, update_service):
        """Placement is the whole design, and both edges matter.

        *After* the sync: everything above that line can still roll the app back
        to the original commit, and rolling back with the dependencies already
        moved leaves the two out of step. *Before* the reboot: deferring the
        install to the next boot puts a multi-minute source compile behind a
        dark screen, with the backend down and nothing anywhere to say why —
        the most invisible place it could possibly run.

        The failures it reports ride the envelope too, for the one path that
        survives to return one (a refused reboot); otherwise the journal and
        `installed != validated` on the dependency rows are what is left.
        """
        order = []
        calls, mock_exec = TestMiloAppLastSteps._exec()

        async def deploy(*args, **kwargs):
            order.append(args[0])
            return (True, "")

        async def reconcile():
            order.append("reconcile")
            return ["shairport-sync"]

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(frontend_from_the_release(update_service))
            stack.enter_context(patch.object(update_service, "_run_deploy", side_effect=deploy))
            stack.enter_context(
                patch.object(update_service, "_reconcile_dependencies", side_effect=reconcile)
            )
            result = await update_service._update_milo_app(TestMiloAppLastSteps.STATUS)

        assert order == ["sync-system-files", "reconcile", "reboot"]
        assert result["success"] is True
        assert result["dependency_failures"] == ["shairport-sync"]


    async def test_the_satellites_are_pushed_after_the_set_and_before_the_reboot(self, update_service):
        """A Milō update replaces the `milo-client/` tree the satellites run, so
        the fleet goes stale the moment the pull lands — all of it, at once. The
        press that updates the appliance carries them, or nobody does until
        someone thinks to open the satellite rows.

        Both edges again. *After* the set, because a satellite is discovered
        through the local snapserver, which the reconciliation may have just
        restarted. *Before* the reboot, because this process is what drives the
        push. And what it leaves behind rides the envelope, like a dependency.
        """
        order = []
        calls, mock_exec = TestMiloAppLastSteps._exec()

        async def deploy(*args, **kwargs):
            order.append(args[0])
            return (True, "")

        async def reconcile():
            order.append("reconcile")
            return []

        async def push():
            order.append("push-satellites")
            return ["Canapé"]

        update_service._satellites.push_client_app_to_fleet = AsyncMock(side_effect=push)

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(frontend_from_the_release(update_service))
            stack.enter_context(patch.object(update_service, "_run_deploy", side_effect=deploy))
            stack.enter_context(
                patch.object(update_service, "_reconcile_dependencies", side_effect=reconcile)
            )
            result = await update_service._update_milo_app(TestMiloAppLastSteps.STATUS)

        assert order == ["sync-system-files", "reconcile", "push-satellites", "reboot"]
        assert result["success"] is True
        assert result["satellite_failures"] == ["Canapé"]


class TestMiloAppLastSteps:
    """The two steps of a Milo update whose result was never read.

    `_sync_system_files` only warned, and `_run_deploy("reboot")` was called
    without looking at what it answered — so an update that copied no unit file,
    or one the reboot refused, still reported `success: True` to the UI and to
    Milo-Mac.
    """

    STATUS = MILO_STATUS

    @staticmethod
    def _exec():
        """Answer each subprocess by command, recording every argv."""
        calls = []

        async def mock_exec(*args, **kwargs):
            calls.append(args)
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
            stack.enter_context(frontend_from_the_release(update_service))
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
        """The release is checked out, installed and synced — undoing it over a
        refused reboot would be worse than reporting it. Only the answer changes.
        """
        calls, mock_exec = self._exec()

        async def deploy(*args, **kwargs):
            return (False, "sudo: a password is required") if args[0] == "reboot" else (True, "")

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=mock_exec))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(frontend_from_the_release(update_service))
            stack.enter_context(patch.object(update_service, "_run_deploy", side_effect=deploy))
            stack.enter_context(github_unreachable())
            rollback = stack.enter_context(
                patch.object(update_service, "_rollback_milo_to_commit", return_value=True)
            )
            result = await update_service._update_milo_app(self.STATUS)

        assert result["success"] is False
        assert "reboot" in result["error"].lower()
        assert "sudo: a password is required" in result["error"]
        rollback.assert_not_awaited()


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


class TestBinaryProgramDownloadTempDir:
    """A download that fails must not leave its scratch directory behind.

    Same duty as the snapcast component below, on the path that carries three
    of the six programs — go-librespot, CamillaDSP and Navidrome — and the one
    the snapcast docstring cites as its reference ("as _download_binary_program
    already does"). Only the success path hands `temp_dir` back for
    _cleanup_temp_files to release; every other exit owns it. On this appliance
    /tmp is a tmpfs, and an update retried after a network failure grows the
    leak once per attempt with nothing that ever collects it.

    The mocks stand for the outside world this function talks to — GitHub and
    tar — and the assertion is what it left on disk afterwards.
    """

    CONFIG = PROGRAMS["go-librespot"]

    @staticmethod
    @contextmanager
    def _sandboxed_tmp(tmp_path):
        """Redirect mkdtemp into tmp_path and record every directory it creates."""
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(dir=None):  # noqa: A002 -- mirrors tempfile.mkdtemp's kwarg
            path = real_mkdtemp(dir=str(tmp_path))
            created.append(Path(path))
            return path

        with patch("backend.core.updates.update.tempfile.mkdtemp", fake_mkdtemp):
            yield created

    @staticmethod
    def _session(status=200, payload=b"tarball-bytes"):
        """A stand-in for aiohttp serving one response to one GET."""

        class _Content:
            @staticmethod
            async def iter_chunked(_size):
                yield payload

        class _Response:
            def __init__(self):
                self.status = status
                self.content = _Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            @staticmethod
            def get(_url):
                return _Response()

        return lambda **_kwargs: _Session()

    @staticmethod
    def _tar(returncode=0, produces=None):
        """A stand-in for the tar process; honours -C and can drop a file in it."""

        async def _exec(*args, **_kwargs):
            if returncode == 0 and produces:
                dest = Path(args[args.index("-C") + 1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / produces).write_bytes(b"the new binary")
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = returncode
            return proc

        return _exec

    @pytest.mark.asyncio
    async def test_a_refused_download_removes_its_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(tmp_path) as created:
            with patch("backend.core.updates.update.aiohttp.ClientSession",
                       side_effect=RuntimeError("network down")):
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is False
        assert "network down" in result["error"]
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived the failed download"

    @pytest.mark.asyncio
    async def test_an_http_error_removes_its_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(tmp_path) as created:
            with patch("backend.core.updates.update.aiohttp.ClientSession", self._session(status=404)):
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is False
        assert "404" in result["error"]
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived an HTTP error"

    @pytest.mark.asyncio
    async def test_a_failed_extraction_removes_its_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(tmp_path) as created:
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session()))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    self._tar(returncode=1)))
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is False
        assert "extract" in result["error"].lower()
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived a failed extraction"

    @pytest.mark.asyncio
    async def test_an_archive_without_the_binary_removes_its_temp_dir(self, update_service, tmp_path):
        """tar succeeded and unpacked something, but not the binary we came for."""
        with self._sandboxed_tmp(tmp_path) as created:
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session()))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    self._tar(produces="README.md")))
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is False
        assert "Binary not found" in result["error"]
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived an archive we rejected"

    @pytest.mark.asyncio
    async def test_the_success_path_hands_its_directory_to_the_caller(self, update_service, tmp_path):
        """The one exit that must NOT clean up: the caller releases it later."""
        binary_name = Path(self.CONFIG["binary_path"]).name

        with self._sandboxed_tmp(tmp_path) as created:
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session()))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    self._tar(produces=binary_name)))
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is True
        assert Path(result["binary_path"]).is_file(), "the path handed back is not the unpacked binary"
        assert Path(result["binary_path"]).name == binary_name
        assert len(created) == 1
        assert result["temp_dir"] == str(created[0])
        assert created[0].exists(), "the success path must leave the directory for the caller"

        await update_service._cleanup_temp_files(result["temp_dir"])
        assert not created[0].exists()


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


# =========================================================================== #
# shairport-sync: the compile-from-source chain
# =========================================================================== #
#
# The only program Milō builds on the unit rather than downloading. Six methods,
# none of which had a line executed. Its failure mode is quiet by construction:
# a build that drops a `./configure` flag produces a working binary that has no
# metadata pipe — AirPlay keeps playing and stops showing titles and covers,
# which is exactly the regression this appliance already lived through once.


class RecordingSpawn:
    """Records every argv and answers from a per-program router.

    Stands in for `autoreconf`, `./configure`, `make`, `nproc` and `tar`. An
    unrouted program answers success, so a test asserts on what it asked for
    rather than on the shape of a fixture it wrote itself.
    """

    def __init__(self, router=None, hang=()):
        self.calls: list[dict] = []
        self._router = router or (lambda argv: (0, b"", b""))
        self._hang = set(hang)
        self.killed: list[str] = []

    async def __call__(self, program, *args, **kwargs):
        argv = (program, *args)
        self.calls.append({"argv": argv, "cwd": kwargs.get("cwd")})
        proc = AsyncMock()
        recorder = self

        if program in self._hang:
            async def times_out(input=None):
                raise asyncio.TimeoutError()
            proc.communicate = times_out
            proc.kill = lambda: recorder.killed.append(program)
            proc.wait = AsyncMock()
            proc.returncode = None
            return proc

        rc, out, err = self._router(argv)
        proc.communicate = AsyncMock(return_value=(out, err))
        proc.returncode = rc
        proc.kill = lambda: recorder.killed.append(program)
        proc.wait = AsyncMock()
        return proc

    def programs(self) -> list[str]:
        return [c["argv"][0] for c in self.calls]

    def argv_for(self, program) -> tuple:
        for call in self.calls:
            if call["argv"][0] == program:
                return call["argv"]
        raise AssertionError(f"{program} was never spawned: {self.programs()}")

    def call_for(self, program) -> dict:
        for call in self.calls:
            if call["argv"][0] == program:
                return call
        raise AssertionError(f"{program} was never spawned: {self.programs()}")


@contextmanager
def spawning(router=None, hang=()):
    fake = RecordingSpawn(router, hang)
    with patch("asyncio.create_subprocess_exec", new=fake):
        yield fake


class FakeResponse:
    def __init__(self, status=200, body=b"tarball"):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def content(self):
        body = self._body

        class _Content:
            @staticmethod
            async def iter_chunked(size):
                for i in range(0, len(body), size):
                    yield body[i:i + size]
        return _Content()


class FakeSession:
    """Stands in for aiohttp; records the URL and answers one response."""

    def __init__(self, response):
        self.urls: list[str] = []
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.urls.append(url)
        return self._response


@contextmanager
def downloading(status=200, body=b"tarball"):
    session = FakeSession(FakeResponse(status, body))
    with patch("backend.core.updates.update.aiohttp.ClientSession",
               return_value=session):
        yield session


class TestDownloadShairportSource:

    @pytest.mark.asyncio
    async def test_the_tarball_is_fetched_for_the_upstream_tag_verbatim(self, update_service, tmp_path):
        """The catalog's `version_regex` strips the `v`, so rebuilding a URL
        from the parsed version would ask GitHub for `refs/tags/4.3.7` when the
        release is `4.3.7`, or the reverse — the update then fails with a 404 on
        a release that exists."""
        with downloading() as session, spawning(), \
                patch("tempfile.mkdtemp", return_value=str(tmp_path)):
            (tmp_path / "shairport-sync-4.3.7").mkdir()
            result = await update_service._download_shairport_sync_source("4.3.7")

        assert session.urls == [
            "https://github.com/mikebrady/shairport-sync/archive/refs/tags/4.3.7.tar.gz"
        ]
        assert result["success"] is True
        assert result["source_dir"] == str(tmp_path / "shairport-sync-4.3.7")

    @pytest.mark.asyncio
    async def test_a_missing_release_leaves_no_temp_directory_behind(self, update_service, tmp_path):
        """Every failure exit removes its own directory: nothing downstream is
        handed a `temp_dir` to clean up, so a leak here is permanent and grows
        by one unpacked source tree per attempt."""
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(status=404), spawning(), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_shairport_sync_source("9.9.9")

        assert result["success"] is False
        assert "404" in result["error"]
        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_a_corrupt_tarball_is_reported_with_tars_own_reason(self, update_service, tmp_path):
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(), spawning(lambda argv: (2, b"", b"gzip: unexpected end of file")), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_shairport_sync_source("4.3.7")

        assert result["success"] is False
        assert "unexpected end of file" in result["error"]
        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_an_archive_with_no_source_tree_is_refused(self, update_service, tmp_path):
        """`tar` exits 0 on an archive holding only files; the next phase would
        then run `autoreconf` in a directory that does not exist."""
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(), spawning(), patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_shairport_sync_source("4.3.7")

        assert result["success"] is False
        assert "No directory" in result["error"]
        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_the_bytes_that_arrive_are_the_bytes_that_are_extracted(self, update_service, tmp_path):
        """The archive is streamed in 8 KiB chunks; a reassembly that dropped or
        reordered one would hand `tar` a file it refuses, which reads as a
        network problem."""
        payload = bytes(range(256)) * 100
        with downloading(body=payload) as _, spawning() as spawn, \
                patch("tempfile.mkdtemp", return_value=str(tmp_path)):
            (tmp_path / "shairport-sync-4.3.7").mkdir()
            await update_service._download_shairport_sync_source("4.3.7")

        archive = Path(spawn.argv_for("tar")[2])
        assert archive.read_bytes() == payload


class TestConfigureShairportSync:

    FLAGS = PROGRAMS["shairport-sync"]["configure_flags"]

    @pytest.mark.asyncio
    async def test_configure_receives_the_catalog_flags_unchanged(self, update_service):
        """These flags ARE the feature set. A build without `--with-metadata-pipe`
        installs a shairport-sync that plays perfectly and never writes
        `/tmp/shairport-sync-metadata`, so AirPlay loses every title, artist and
        cover — silently, and only until someone looks at the screen. Compared
        against the catalog rather than a literal, so dropping one there is a
        red test and not a quieter appliance."""
        with spawning() as spawn:
            result = await update_service._configure_shairport_sync("/src", self.FLAGS)

        assert result["success"] is True
        assert spawn.argv_for("./configure") == ("./configure", *self.FLAGS)

    @pytest.mark.asyncio
    async def test_both_steps_run_in_the_extracted_source_tree(self, update_service):
        """`autoreconf` and `./configure` are relative to the unpacked tree; run
        from the backend's own cwd they would regenerate the build system of
        this repository."""
        with spawning() as spawn:
            await update_service._configure_shairport_sync("/src", self.FLAGS)

        assert spawn.call_for("autoreconf")["cwd"] == "/src"
        assert spawn.call_for("./configure")["cwd"] == "/src"

    @pytest.mark.asyncio
    async def test_a_failed_autoreconf_does_not_reach_configure(self, update_service):
        """`./configure` does not exist until autoreconf has generated it, so
        running it anyway turns a readable "autoreconf failed" into "No such
        file or directory"."""
        def router(argv):
            if argv[0] == "autoreconf":
                return (1, b"", b"aclocal: command not found")
            return (0, b"", b"")

        with spawning(router) as spawn:
            result = await update_service._configure_shairport_sync("/src", self.FLAGS)

        assert result["success"] is False
        assert "aclocal" in result["error"]
        assert "./configure" not in spawn.programs()

    @pytest.mark.asyncio
    async def test_a_configure_that_refuses_a_flag_is_reported(self, update_service):
        """The usual real failure: a build dependency missing after a distro
        upgrade. Its stderr names the library, and that string is the whole
        diagnosis the owner gets."""
        def router(argv):
            if argv[0] == "./configure":
                return (1, b"", b"configure: error: Avahi library not found")
            return (0, b"", b"")

        with spawning(router):
            result = await update_service._configure_shairport_sync("/src", self.FLAGS)

        assert result["success"] is False
        assert "Avahi library not found" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stalled", ["autoreconf", "./configure"])
    async def test_a_stalled_step_is_killed_and_reaped(self, update_service, stalled):
        """Both are bounded at five minutes. An unbounded one holds the update
        key in `active_updates` for ever and the UI shows a build in progress
        that will never end."""
        with spawning(hang=[stalled]) as spawn:
            result = await update_service._configure_shairport_sync("/src", self.FLAGS)

        assert result["success"] is False
        assert "timed out" in result["error"]
        assert spawn.killed == [stalled]


class TestCompileShairportSync:

    @pytest.mark.asyncio
    async def test_the_build_is_parallelised_over_the_cores_this_pi_reports(self, update_service):
        """A serial build of shairport-sync on a Pi takes minutes longer than
        the 15-minute ceiling allows for on a loaded unit."""
        def router(argv):
            if argv[0] == "nproc":
                return (0, b"4\n", b"")
            return (0, b"", b"")

        with spawning(router) as spawn:
            result = await update_service._compile_shairport_sync("/src")

        assert result["success"] is True
        assert spawn.argv_for("make") == ("make", "-j4")
        assert spawn.call_for("make")["cwd"] == "/src"

    @pytest.mark.asyncio
    async def test_a_host_that_cannot_count_its_cores_still_builds(self, update_service):
        """`make -j` with an empty argument is `make -j`, which spawns an
        unbounded number of compilers and takes the Pi's memory with it."""
        with spawning(lambda argv: (0, b"", b"")) as spawn:
            await update_service._compile_shairport_sync("/src")

        assert spawn.argv_for("make") == ("make", "-j2")

    @pytest.mark.asyncio
    async def test_a_build_error_is_reported_by_its_tail(self, update_service):
        """gcc's output is thousands of lines and the error is at the end; the
        head would be the banner. 500 characters is what reaches the UI banner."""
        noise = b"warning: unused variable\n" * 500
        with spawning(lambda argv: (2, b"", noise + b"error: ao.h: No such file")):
            result = await update_service._compile_shairport_sync("/src")

        assert result["success"] is False
        assert "ao.h: No such file" in result["error"]

    @pytest.mark.asyncio
    async def test_a_build_that_never_finishes_is_killed_and_reaped(self, update_service):
        with spawning(hang=["make"]) as spawn:
            result = await update_service._compile_shairport_sync("/src")

        assert result["success"] is False
        assert "timed out" in result["error"]
        assert spawn.killed == ["make"]


class TestInstallShairportSync:

    BINARY = PROGRAMS["shairport-sync"]["binary_path"]

    @staticmethod
    def _stage(staging_root):
        """Lay out what `make install DESTDIR=` produces."""
        staged = Path(staging_root) / "usr/local/bin"
        staged.mkdir(parents=True)
        (staged / "shairport-sync").write_text("#!/bin/sh\n")

    @pytest.mark.asyncio
    async def test_the_binary_is_staged_unprivileged_then_installed_by_the_wrapper(
            self, update_service, tmp_path):
        """Invariant 1: `make install` as root would write wherever the upstream
        Makefile decides — man pages, /etc, systemd units. Staging into a
        DESTDIR as the milo user and then handing one file to
        `milo-deploy-update install-binary` is what keeps the privileged step to
        a single argument-scoped copy."""
        staging = tmp_path / "stage"
        staging.mkdir()
        self._stage(staging)
        deploy = AsyncMock(return_value=(True, ""))
        with spawning() as spawn, patch("tempfile.mkdtemp", return_value=str(staging)), \
                patch.object(update_service, "_run_deploy", deploy):
            result = await update_service._install_shairport_sync("/src")

        assert result["success"] is True
        assert spawn.argv_for("make") == ("make", "install", f"DESTDIR={staging}")
        deploy.assert_awaited_once_with(
            "install-binary", str(staging / "usr/local/bin/shairport-sync"), self.BINARY
        )

    @pytest.mark.asyncio
    async def test_a_staging_run_that_produced_no_binary_is_refused(self, update_service, tmp_path):
        """`make install` can exit 0 having installed only the man pages when
        the build tree is stale. Passing the missing path to install-binary
        would ask the privileged wrapper to copy a file that is not there."""
        staging = tmp_path / "stage"
        staging.mkdir()
        deploy = AsyncMock(return_value=(True, ""))
        with spawning(), patch("tempfile.mkdtemp", return_value=str(staging)), \
                patch.object(update_service, "_run_deploy", deploy):
            result = await update_service._install_shairport_sync("/src")

        assert result["success"] is False
        assert "not found in staging" in result["error"]
        deploy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_install_carries_the_wrappers_reason(self, update_service, tmp_path):
        staging = tmp_path / "stage"
        staging.mkdir()
        self._stage(staging)
        with spawning(), patch("tempfile.mkdtemp", return_value=str(staging)), \
                patch.object(update_service, "_run_deploy",
                             AsyncMock(return_value=(False, "sudo: a password is required"))):
            result = await update_service._install_shairport_sync("/src")

        assert result["success"] is False
        assert "a password is required" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["ok", "staging-failed", "no-binary", "install-refused"])
    async def test_the_staging_directory_is_removed_on_every_path(
            self, update_service, tmp_path, outcome):
        """A full shairport-sync install tree is tens of megabytes under /tmp,
        which is tmpfs on this appliance — four failed update attempts would eat
        the RAM the audio path needs. The removal is in a `finally`, so it is
        the exit paths that have to be checked, not the happy one."""
        staging = tmp_path / "stage"
        staging.mkdir()
        if outcome != "no-binary":
            self._stage(staging)

        router = (lambda argv: (1, b"", b"no rule to make target"))\
            if outcome == "staging-failed" else None
        deploy_result = (False, "refused") if outcome == "install-refused" else (True, "")

        with spawning(router), patch("tempfile.mkdtemp", return_value=str(staging)), \
                patch.object(update_service, "_run_deploy",
                             AsyncMock(return_value=deploy_result)):
            await update_service._install_shairport_sync("/src")

        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_a_staging_step_that_stalls_is_killed_and_reaped(self, update_service, tmp_path):
        staging = tmp_path / "stage"
        staging.mkdir()
        with spawning(hang=["make"]) as spawn, \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._install_shairport_sync("/src")

        assert "timed out" in result["error"]
        assert spawn.killed == ["make"]
        assert not staging.exists()


class TestUpdateShairportSyncFlow:
    """The ordering of the compile-from-source flow, on the branches the
    existing `TestUpdateShairportSync` does not reach.

    Same shape as its `_flow` helper and the same stated limit: the phases are
    the behaviour, and what is asserted is which one ran, in which order, and
    which did not run at all.
    """

    STATUS = {
        "installed": {"versions": {"main": "4.3.7"}},
        "latest": {"version": "4.3.8", "tag_name": "4.3.8"},
    }

    @staticmethod
    @contextmanager
    def _flow(service, *, was_active=True, overrides=None, version_file=True):
        returns = {
            "_is_service_active": was_active,
            "_backup_shairport_sync": {"success": True},
            "_download_shairport_sync_source": {
                "success": True, "temp_dir": "/tmp/sps", "source_dir": "/tmp/sps/src"
            },
            "_configure_shairport_sync": {"success": True},
            "_compile_shairport_sync": {"success": True},
            "_install_shairport_sync": {"success": True},
            "_verify_shairport_sync_update": {"success": True},
            "_stop_service": True,
            "_start_service": True,
            "_rollback_shairport_sync": True,
            "_cleanup_temp_files": None,
        }
        returns.update(overrides or {})
        written = {}

        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }
            if version_file:
                stack.enter_context(patch("backend.core.updates.update.aiofiles.open",
                                          new=_recording_aiofiles_open(written)))
            mocks["_written"] = written
            yield mocks

    @pytest.mark.asyncio
    async def test_the_installed_version_is_written_where_the_catalog_reads_it_back(
            self, update_service):
        """`catalog["shairport-sync"]["commands"]["main"]` is
        `cat /var/lib/milo/shairport-sync-version || shairport-sync --version`.
        The compiled binary's own `--version` carries a build suffix the release
        tag does not, so without this file the comparison never matches and the
        panel offers the same update for ever."""
        with self._flow(update_service) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result == {"success": True}
        assert flow["_written"] == {"/var/lib/milo/shairport-sync-version": "4.3.8"}

    @pytest.mark.asyncio
    async def test_a_version_file_that_cannot_be_written_does_not_fail_the_update(
            self, update_service, caplog):
        """The binary is installed and running by this point; failing the update
        over a bookkeeping file would trigger a rollback that replaces a good
        new build with the old one."""
        with self._flow(update_service, version_file=False), \
                patch("backend.core.updates.update.aiofiles.open",
                      side_effect=PermissionError("read-only")), \
                caplog.at_level(logging.WARNING):
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result == {"success": True}
        assert "read-only" in caplog.text

    @pytest.mark.asyncio
    async def test_an_inactive_airplay_service_is_neither_stopped_nor_started(self, update_service):
        """`milo-airplay` is on-demand: it runs while the AirPlay source is
        selected and not otherwise. Starting it after an update would put a
        receiver on the network the owner did not ask for, and it would hold the
        ALSA device against whichever source is actually playing."""
        with self._flow(update_service, was_active=False) as flow:
            assert await update_service._update_shairport_sync(self.STATUS) == {"success": True}

        flow["_stop_service"].assert_not_called()
        flow["_start_service"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_backup_stops_before_anything_is_downloaded(self, update_service):
        """No backup means no rollback point; the flow must not reach the
        install that would need one."""
        with self._flow(update_service, overrides={
            "_backup_shairport_sync": {"success": False, "error": "Backup failed: read-only"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        flow["_download_shairport_sync_source"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_download_that_failed_is_returned_verbatim(self, update_service):
        """It cleaned up its own temp dir already, and nothing was replaced, so
        neither a cleanup nor a rollback belongs here."""
        with self._flow(update_service, overrides={
            "_download_shairport_sync_source": {"success": False, "error": "HTTP 404"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["error"] == "HTTP 404"
        flow["_cleanup_temp_files"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phase", ["_configure_shairport_sync", "_compile_shairport_sync"])
    async def test_a_build_failure_cleans_up_without_rolling_back(self, update_service, phase):
        """Nothing has been installed yet, and the running binary was never
        stopped. A rollback here would stop a working AirPlay receiver to
        restore the binary it is already running."""
        with self._flow(update_service, overrides={
            phase: {"success": False, "error": "boom"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["error"] == "boom"
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/sps")
        flow["_rollback_shairport_sync"].assert_not_called()
        flow["_stop_service"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_stop_aborts_before_the_install(self, update_service):
        """install-binary on a running unit either hits "Text file busy" or
        replaces the image under a live process."""
        with self._flow(update_service, overrides={"_stop_service": False}) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        flow["_install_shairport_sync"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_service_that_will_not_come_back_rolls_back(self, update_service):
        """The new binary is in place and the unit is down: leaving it there is
        an AirPlay receiver that never returns."""
        with self._flow(update_service, overrides={"_start_service": False}) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        flow["_rollback_shairport_sync"].assert_called_once_with(
            PROGRAMS["shairport-sync"], True
        )
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/sps")

    @pytest.mark.asyncio
    async def test_a_verification_that_fails_rolls_back_and_says_why(self, update_service):
        with self._flow(update_service, overrides={
            "_verify_shairport_sync_update": {
                "success": False, "error": "shairport-sync service not running after update"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert "not running after update" in result["error"]
        assert "Rolled back" in result["error"]
        flow["_rollback_shairport_sync"].assert_called_once()

    @pytest.mark.asyncio
    async def test_an_unexpected_failure_still_releases_the_source_tree(self, update_service):
        """The unpacked source is tens of megabytes on a tmpfs /tmp."""
        with self._flow(update_service, overrides={
            "_verify_shairport_sync_update": RuntimeError("systemd went away"),
        }) as flow:
            flow["_verify_shairport_sync_update"].side_effect = RuntimeError("systemd went away")
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/sps")


# =========================================================================== #
# qobuz-proxy: a pip package inside a venv Milō owns
# =========================================================================== #
#
# The whole venv is copied aside and restored on any failure, and the restore is
# `rm -rf` followed by `mv`. That ordering is the reason this block is worth
# pinning: between the two the sidecar has no interpreter at all, so a guard
# that lets the sequence start on a unit that would not stop, or a step whose
# failure is not reported, leaves Qobuz unrunnable with the UI reporting a
# rollback.

QOBUZ = PROGRAMS["qobuz-proxy"]


class LocalCommands:
    """Stands in for `_run_local`'s subprocess: records argv, answers by prefix."""

    def __init__(self, failures=None):
        self.calls: list[tuple[str, ...]] = []
        self._failures = failures or {}

    async def __call__(self, *args, timeout=120):
        self.calls.append(tuple(args))
        for needle, answer in self._failures.items():
            if needle in args or any(needle in str(a) for a in args):
                return answer
        return (True, "")

    def verbs(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]


class TestBackupQobuzVenv:

    @pytest.mark.asyncio
    async def test_a_stale_backup_is_removed_before_the_new_one_is_taken(
            self, update_service, tmp_path):
        """`cp -a` onto an existing directory nests the copy inside it, so the
        restore would `mv` a venv-containing-a-venv into place and every shebang
        would point one level too high."""
        config = {"backup_path": str(tmp_path / "backups"), "venv_path": "/var/lib/milo/qobuz/venv"}
        (tmp_path / "backups" / "venv").mkdir(parents=True)

        local = LocalCommands()
        with patch.object(update_service, "_run_local", local):
            assert (await update_service._backup_qobuz_venv(config))["success"] is True

        assert local.verbs() == ["rm", "cp"]
        assert local.calls[0][:2] == ("rm", "-rf")

    @pytest.mark.asyncio
    async def test_a_first_backup_skips_the_removal(self, update_service, tmp_path):
        config = {"backup_path": str(tmp_path / "backups"), "venv_path": "/var/lib/milo/qobuz/venv"}
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local):
            await update_service._backup_qobuz_venv(config)

        assert local.verbs() == ["cp"]

    @pytest.mark.asyncio
    async def test_the_copy_preserves_the_venvs_symlinks(self, update_service, tmp_path):
        """A venv is mostly symlinks to the system interpreter; a plain
        recursive copy dereferences them and the backup is a 200 MB tree whose
        `bin/python` is a real file with the wrong `sys.prefix`."""
        config = {"backup_path": str(tmp_path / "backups"), "venv_path": "/var/lib/milo/qobuz/venv"}
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local):
            await update_service._backup_qobuz_venv(config)

        assert local.calls[0][:2] == ("cp", "-a")
        assert local.calls[0][2] == "/var/lib/milo/qobuz/venv"

    @pytest.mark.asyncio
    async def test_a_backup_that_could_not_be_written_stops_the_update(
            self, update_service, tmp_path):
        """No backup means the rollback below has nothing to restore."""
        config = {"backup_path": str(tmp_path / "backups"), "venv_path": "/var/lib/milo/qobuz/venv"}
        local = LocalCommands(failures={"cp": (False, "No space left on device")})
        with patch.object(update_service, "_run_local", local):
            result = await update_service._backup_qobuz_venv(config)

        assert result["success"] is False
        assert "No space left" in result["error"]

    @pytest.mark.asyncio
    async def test_a_stale_backup_that_cannot_be_removed_stops_the_update(
            self, update_service, tmp_path):
        """Carrying on would `cp -a` into the stale directory, which is the
        nested-venv restore above."""
        config = {"backup_path": str(tmp_path / "backups"), "venv_path": "/var/lib/milo/qobuz/venv"}
        (tmp_path / "backups" / "venv").mkdir(parents=True)
        local = LocalCommands(failures={"rm": (False, "Permission denied")})
        with patch.object(update_service, "_run_local", local):
            result = await update_service._backup_qobuz_venv(config)

        assert result["success"] is False
        assert "Backup cleanup failed" in result["error"]


class TestRollbackQobuzVenv:

    @staticmethod
    def _config(tmp_path, with_backup=True):
        config = dict(QOBUZ, backup_path=str(tmp_path / "backups"),
                      venv_path=str(tmp_path / "venv"))
        if with_backup:
            (tmp_path / "backups" / "venv").mkdir(parents=True)
        return config

    @pytest.mark.asyncio
    async def test_with_no_backup_there_is_no_rollback_to_claim(self, update_service, tmp_path, caplog):
        """Reporting True here is the worst answer available: the caller would
        tell the owner "rolled back to previous version" over a venv that was
        never touched, or worse, one half-upgraded."""
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local), \
                patch.object(update_service, "_stop_service", return_value=True), \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_qobuz_venv(
                self._config(tmp_path, with_backup=False)) is False

        assert local.calls == []
        assert "No qobuz-proxy venv backup" in caplog.text

    @pytest.mark.asyncio
    async def test_a_sidecar_that_will_not_stop_keeps_its_venv(self, update_service, tmp_path):
        """The next step is `rm -rf` on the interpreter a running process is
        executing from. The unit has Restart=always, so "not stopped" is the
        normal answer when systemd is racing us."""
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local), \
                patch.object(update_service, "_stop_service", return_value=False):
            assert await update_service._rollback_qobuz_venv(self._config(tmp_path)) is False

        assert local.calls == []

    @pytest.mark.asyncio
    async def test_the_venv_is_restored_to_the_same_absolute_path(self, update_service, tmp_path):
        """A venv's console scripts carry an absolute shebang. Restoring beside
        the original instead of onto it gives a tree whose `bin/qobuz-proxy`
        points at a path that no longer exists."""
        config = self._config(tmp_path)
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local), \
                patch.object(update_service, "_stop_service", return_value=True), \
                patch.object(update_service, "_start_service", return_value=True):
            assert await update_service._rollback_qobuz_venv(config, True) is True

        assert local.verbs() == ["rm", "mv"]
        assert local.calls[0] == ("rm", "-rf", config["venv_path"])
        assert local.calls[1] == ("mv", str(tmp_path / "backups" / "venv"), config["venv_path"])

    @pytest.mark.asyncio
    async def test_a_removal_that_failed_does_not_move_the_backup_onto_it(
            self, update_service, tmp_path, caplog):
        """`mv` onto a directory that still exists nests the backup inside it,
        and the only copy of the working venv is then unreachable."""
        local = LocalCommands(failures={"rm": (False, "Device or resource busy")})
        with patch.object(update_service, "_run_local", local), \
                patch.object(update_service, "_stop_service", return_value=True), \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_qobuz_venv(self._config(tmp_path)) is False

        assert local.verbs() == ["rm"]
        assert "rollback (rm) failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_move_that_failed_is_reported_because_nothing_is_left(
            self, update_service, tmp_path, caplog):
        """The removal already ran: Qobuz has no venv at all at this point. The
        False is what turns the caller's message into "manual intervention
        required" instead of "rolled back"."""
        local = LocalCommands(failures={"mv": (False, "Invalid cross-device link")})
        with patch.object(update_service, "_run_local", local), \
                patch.object(update_service, "_stop_service", return_value=True), \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_qobuz_venv(self._config(tmp_path)) is False

        assert "rollback (mv) failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_sidecar_that_was_stopped_is_left_stopped(self, update_service, tmp_path):
        """It starts on demand when the user next selects Qobuz; starting it
        here would put it on the bus for a source nobody chose."""
        start = AsyncMock(return_value=True)
        with patch.object(update_service, "_run_local", LocalCommands()), \
                patch.object(update_service, "_stop_service", return_value=True), \
                patch.object(update_service, "_start_service", start):
            assert await update_service._rollback_qobuz_venv(
                self._config(tmp_path), service_was_active=False) is True

        start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_restored_venv_whose_service_will_not_start_is_not_a_rollback(
            self, update_service, tmp_path, caplog):
        with patch.object(update_service, "_run_local", LocalCommands()), \
                patch.object(update_service, "_stop_service", return_value=True), \
                patch.object(update_service, "_start_service", return_value=False), \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_qobuz_venv(
                self._config(tmp_path), service_was_active=True) is False

        assert "did not start" in caplog.text


class TestVerifyQobuzUpdate:

    @pytest.mark.asyncio
    async def test_the_verification_imports_the_module_our_patches_live_in(self, update_service):
        """The patches are re-applied to `qobuz_proxy.backends.local.stream`; an
        import of the package alone would pass on a venv whose patched module
        failed to compile, and Qobuz would then fail at the first track."""
        local = LocalCommands(failures={"-c": (True, "1.4.2")})
        with patch.object(update_service, "_run_local", local):
            assert await update_service._verify_qobuz_update(QOBUZ, "1.4.2") == {"success": True}

        argv = local.calls[0]
        assert argv[0] == f"{QOBUZ['venv_path']}/bin/python"
        assert "qobuz_proxy.backends.local.stream" in argv[2]

    @pytest.mark.asyncio
    async def test_a_version_that_did_not_move_is_a_failed_update(self, update_service):
        """pip exits 0 when it resolves a cached wheel and installs nothing.
        Without this the update reports success, the panel keeps offering the
        same version, and the backup is deleted."""
        local = LocalCommands(failures={"-c": (True, "1.4.1")})
        with patch.object(update_service, "_run_local", local):
            result = await update_service._verify_qobuz_update(QOBUZ, "1.4.2")

        assert result["success"] is False
        assert "1.4.1" in result["error"] and "1.4.2" in result["error"]

    @pytest.mark.asyncio
    async def test_the_version_is_read_from_the_last_line_of_the_output(self, update_service):
        """A venv python prints deprecation warnings to stdout before the value;
        reading the first line would compare a warning against a version."""
        noisy = "DeprecationWarning: pkg_resources is deprecated\n1.4.2"
        local = LocalCommands(failures={"-c": (True, noisy)})
        with patch.object(update_service, "_run_local", local):
            assert await update_service._verify_qobuz_update(QOBUZ, "1.4.2") == {"success": True}

    @pytest.mark.asyncio
    async def test_an_import_that_raises_is_reported_with_pythons_own_traceback(self, update_service):
        local = LocalCommands(failures={"-c": (False, "ModuleNotFoundError: qobuz_proxy")})
        with patch.object(update_service, "_run_local", local):
            result = await update_service._verify_qobuz_update(QOBUZ, "1.4.2")

        assert result["success"] is False
        assert "ModuleNotFoundError" in result["error"]

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_a_mismatch_not_a_match(self, update_service):
        """`out.strip()` empty means the interpreter printed nothing; treating
        that as the expected version would pass every verification."""
        local = LocalCommands(failures={"-c": (True, "   ")})
        with patch.object(update_service, "_run_local", local):
            assert (await update_service._verify_qobuz_update(QOBUZ, "1.4.2"))["success"] is False


class TestCleanupQobuzBackup:

    @pytest.mark.asyncio
    async def test_the_backup_is_dropped_once_the_update_is_verified(self, update_service, tmp_path):
        """It is a full copy of the venv under /var/lib/milo; keeping it doubles
        the sidecar's footprint on the SD card for ever."""
        config = dict(QOBUZ, backup_path=str(tmp_path))
        (tmp_path / "venv").mkdir()
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local):
            await update_service._cleanup_qobuz_backup(config)

        assert local.calls == [("rm", "-rf", str(tmp_path / "venv"))]

    @pytest.mark.asyncio
    async def test_no_backup_means_nothing_to_remove(self, update_service, tmp_path):
        local = LocalCommands()
        with patch.object(update_service, "_run_local", local):
            await update_service._cleanup_qobuz_backup(dict(QOBUZ, backup_path=str(tmp_path)))

        assert local.calls == []

    @pytest.mark.asyncio
    async def test_a_cleanup_that_raises_does_not_fail_the_update(self, update_service, tmp_path, caplog):
        """It is the last step of a successful update; a leftover backup is a
        disk-space nuisance, not a reason to report the upgrade failed."""
        config = dict(QOBUZ, backup_path=str(tmp_path))
        (tmp_path / "venv").mkdir()
        with patch.object(update_service, "_run_local",
                          side_effect=OSError("read-only file system")), \
                caplog.at_level(logging.WARNING):
            await update_service._cleanup_qobuz_backup(config)

        assert "read-only file system" in caplog.text


class TestUpdateQobuzProxyFlow:
    """The ordering of the sidecar upgrade, on the branches its existing tests
    do not reach. Same stated limit as the rest of this file."""

    STATUS = {"latest": {"version": "1.4.2", "tag_name": "v1.4.2"}}

    @staticmethod
    @contextmanager
    def _flow(service, *, was_active=True, overrides=None):
        returns = {
            "_is_service_active": was_active,
            "_backup_qobuz_venv": {"success": True},
            "_stop_service": True,
            "_start_service": True,
            "_verify_qobuz_update": {"success": True},
            "_rollback_qobuz_venv": True,
            "_cleanup_qobuz_backup": None,
        }
        returns.update(overrides or {})
        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }
            local = LocalCommands((overrides or {}).pop("_local_failures", None))
            mocks["_local"] = stack.enter_context(
                patch.object(service, "_run_local", local)) and local
            yield mocks

    @pytest.mark.asyncio
    async def test_pip_is_pinned_to_the_upstream_tag_and_the_local_extra(self, update_service):
        """Two halves, both load-bearing. `[local]` pulls the extra that ships
        the local backend Milō streams through — without it the sidecar imports
        and answers nothing. And the ref is the *tag* (`v1.4.2`), not the parsed
        version: pip resolves `git+…@1.4.2` to nothing and installs the default
        branch, which is not a release at all."""
        with self._flow(update_service) as flow:
            assert await update_service._update_qobuz_proxy(self.STATUS) == {"success": True}

        pip_argv = flow["_local"].calls[0]
        assert pip_argv[0] == f"{QOBUZ['venv_path']}/bin/pip"
        assert pip_argv[1:3] == ("install", "--upgrade")
        assert pip_argv[3] == (
            "qobuz-proxy[local] @ "
            "git+https://github.com/leolobato/qobuz-proxy@v1.4.2"
        )

    @pytest.mark.asyncio
    async def test_the_release_is_asked_with_the_venvs_own_interpreter(
            self, update_service):
        """The check imports qobuz-proxy, so it has to run with the interpreter
        whose site-packages hold the version just installed. Run with the
        backend's own python it imports nothing and reports success."""
        with self._flow(update_service) as flow:
            await update_service._update_qobuz_proxy(self.STATUS)

        check_argv = flow["_local"].calls[1]
        assert check_argv == (f"{QOBUZ['venv_path']}/bin/python",
                              "/usr/local/bin/milo-qobuz", "--check")

    @pytest.mark.asyncio
    async def test_a_release_that_moved_what_milo_adapts_rolls_the_venv_back(self, update_service):
        """Asked before the restart, because neither adaptation fails audibly.

        A sidecar started without them plays, and reports a volume Milō does not
        want honoured and a progress bar that never moves — no error anywhere.
        """
        local = LocalCommands({"milo-qobuz": (False, "QobuzPlayer.duration_ms: gone")})
        with self._flow(update_service) as flow, \
                patch.object(update_service, "_run_local", local):
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result["success"] is False
        assert "duration_ms" in result["error"]
        assert "Rolled back" in result["error"]
        flow["_rollback_qobuz_venv"].assert_called_once()

    @pytest.mark.asyncio
    async def test_a_pip_that_refused_rolls_back_and_never_checks(self, update_service):
        local = LocalCommands({"install": (False, "Could not find a version")})
        with self._flow(update_service) as flow, \
                patch.object(update_service, "_run_local", local):
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert "Could not find a version" in result["error"]
        assert len(local.calls) == 1
        flow["_rollback_qobuz_venv"].assert_called_once()

    @pytest.mark.asyncio
    async def test_a_failed_backup_stops_before_pip_touches_the_venv(self, update_service):
        with self._flow(update_service, overrides={
            "_backup_qobuz_venv": {"success": False, "error": "venv backup failed: ENOSPC"},
        }) as flow:
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result["error"] == "venv backup failed: ENOSPC"
        assert flow["_local"].calls == []
        flow["_rollback_qobuz_venv"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_sidecar_that_will_not_stop_rolls_back_before_pip(self, update_service):
        """The unit is `Restart=always`; upgrading the venv under a process that
        is still running gives half-old, half-new imports."""
        with self._flow(update_service, overrides={"_stop_service": False}) as flow:
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result["success"] is False
        assert flow["_local"].calls == []
        flow["_rollback_qobuz_venv"].assert_called_once()

    @pytest.mark.asyncio
    async def test_an_already_stopped_sidecar_is_upgraded_without_touching_systemd(
            self, update_service):
        """The route deactivates Qobuz before calling this, so inactive is the
        normal state. Starting it afterwards would leave a sidecar running for a
        source the user has left."""
        with self._flow(update_service, was_active=False) as flow:
            assert await update_service._update_qobuz_proxy(self.STATUS) == {"success": True}

        flow["_stop_service"].assert_not_called()
        flow["_start_service"].assert_not_called()
        flow["_cleanup_qobuz_backup"].assert_called_once()

    @pytest.mark.asyncio
    async def test_a_verified_upgrade_whose_service_will_not_restart_is_not_rolled_back(
            self, update_service):
        """Same call as the refused reboot in `_update_milo_app`: the venv is
        upgraded and verified, and undoing a good update because systemd refused
        would be worse. The backup is kept for the same reason."""
        with self._flow(update_service, overrides={"_start_service": False}) as flow:
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result["success"] is False
        assert "Restart it manually" in result["error"]
        flow["_rollback_qobuz_venv"].assert_not_called()
        flow["_cleanup_qobuz_backup"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_verification_rolls_back_and_keeps_the_backup(self, update_service):
        with self._flow(update_service, overrides={
            "_verify_qobuz_update": {"success": False, "error": "Version mismatch after update"},
        }) as flow:
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert "Version mismatch" in result["error"]
        flow["_rollback_qobuz_venv"].assert_called_once()
        flow["_cleanup_qobuz_backup"].assert_not_called()

    @pytest.mark.asyncio
    async def test_the_backup_is_only_dropped_once_everything_passed(self, update_service):
        with self._flow(update_service) as flow:
            await update_service._update_qobuz_proxy(self.STATUS)
        flow["_cleanup_qobuz_backup"].assert_called_once()

    @pytest.mark.asyncio
    async def test_a_crash_before_the_backup_reports_no_rollback_outcome(self, update_service):
        """Nothing was copied aside, so "rolled back" and "rollback also failed"
        would both be lies; the bare exception text is the honest answer."""
        with self._flow(update_service) as flow:
            flow["_backup_qobuz_venv"].side_effect = OSError("disk gone")
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert result == {"success": False, "error": "disk gone"}
        flow["_rollback_qobuz_venv"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_crash_after_the_backup_rolls_back(self, update_service):
        with self._flow(update_service) as flow:
            flow["_verify_qobuz_update"].side_effect = OSError("systemd gone")
            result = await update_service._update_qobuz_proxy(self.STATUS)

        assert "Rolled back" in result["error"]
        flow["_rollback_qobuz_venv"].assert_called_once()


class TestRunLocal:
    """The unprivileged twin of `_run_deploy`, used for every venv operation."""

    @pytest.mark.asyncio
    async def test_stdout_is_returned_on_success(self, update_service):
        with spawning(lambda argv: (0, b"1.4.2\n", b"")):
            assert await update_service._run_local("python", "-c", "x") == (True, "1.4.2")

    @pytest.mark.asyncio
    async def test_stderr_is_preferred_when_the_command_failed(self, update_service):
        with spawning(lambda argv: (1, b"noise", b"ERROR: no matching distribution")):
            ok, out = await update_service._run_local("pip", "install", "x")
        assert (ok, out) == (False, "ERROR: no matching distribution")

    @pytest.mark.asyncio
    async def test_stdout_is_the_fallback_when_a_failure_says_nothing(self, update_service):
        """pip writes its resolver errors to stdout, not stderr; an empty
        message would reach the UI as a bare "pip install failed: "."""
        with spawning(lambda argv: (1, b"ERROR: Could not build wheels", b"")):
            ok, out = await update_service._run_local("pip", "install", "x")
        assert (ok, out) == (False, "ERROR: Could not build wheels")

    @pytest.mark.asyncio
    async def test_a_command_that_hangs_is_killed_and_reaped(self, update_service):
        """`pip install` from a git ref compiles wheels on the Pi; a stalled one
        would hold the update key for ever with no ceiling."""
        with spawning(hang=["pip"]) as spawn:
            ok, out = await update_service._run_local("pip", "install", "x", timeout=5)

        assert ok is False and "Timed out after 5s" == out
        assert spawn.killed == ["pip"]

    @pytest.mark.asyncio
    async def test_a_binary_that_does_not_exist_is_reported_not_raised(self, update_service):
        """The venv's pip is gone exactly when a rollback needs to run."""
        async def missing(*a, **k):
            raise FileNotFoundError("/var/lib/milo/qobuz/venv/bin/pip")

        with patch("asyncio.create_subprocess_exec", new=missing):
            ok, out = await update_service._run_local("/var/lib/milo/qobuz/venv/bin/pip")

        assert ok is False and "bin/pip" in out


# =========================================================================== #
# The residue: the recovery arms nothing had entered
# =========================================================================== #

class TestBinaryProgramRecoveryArms:
    """`_update_binary_program` is the shared flow for go-librespot, CamillaDSP
    and Navidrome. Its forward path is covered; these are the three exits taken
    after the unit was already stopped and the binary already swapped."""

    CONFIG_KEY = "camilladsp"

    @staticmethod
    @contextmanager
    def _flow(service, *, overrides=None):
        returns = {
            "_is_service_active": True,
            "_backup_binary_program": {"success": True},
            "_download_binary_program": {
                "success": True, "binary_path": "/tmp/dl/camilladsp", "temp_dir": "/tmp/dl"},
            "_stop_service": True,
            "_start_service": True,
            "_run_deploy": (True, ""),
            "_verify_binary_program": {"success": True},
            "_rollback_binary_program": True,
            "_cleanup_temp_files": None,
        }
        returns.update(overrides or {})
        with ExitStack() as stack:
            yield {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }

    STATUS = {"latest": {"version": "3.0.1"}}

    @pytest.mark.asyncio
    async def test_a_unit_that_will_not_come_back_rolls_back_and_releases_its_download(
            self, update_service):
        """CamillaDSP is always in the audio path: a unit that does not restart
        is an appliance with no sound at all until someone intervenes."""
        with self._flow(update_service, overrides={"_start_service": False}) as flow:
            result = await update_service._update_binary_program(self.CONFIG_KEY, self.STATUS)

        assert result["success"] is False
        assert "Rolled back" in result["error"]
        flow["_rollback_binary_program"].assert_called_once_with(PROGRAMS[self.CONFIG_KEY], True)
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/dl")

    @pytest.mark.asyncio
    async def test_a_verification_that_fails_rolls_back_with_its_own_reason(self, update_service):
        with self._flow(update_service, overrides={
            "_verify_binary_program": {"success": False, "error": "CamillaDSP binary not found after update"},
        }) as flow:
            result = await update_service._update_binary_program(self.CONFIG_KEY, self.STATUS)

        assert "binary not found after update" in result["error"]
        flow["_rollback_binary_program"].assert_called_once()

    @pytest.mark.asyncio
    async def test_a_successful_update_releases_its_download(self, update_service):
        """The tarball plus the unpacked binary is tens of megabytes on a tmpfs
        /tmp; the success path is the one that runs on every update."""
        with self._flow(update_service) as flow:
            assert await update_service._update_binary_program(
                self.CONFIG_KEY, self.STATUS) == {"success": True}

        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/dl")
        flow["_rollback_binary_program"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_crash_before_the_unit_was_stopped_reports_no_rollback(self, update_service):
        """Nothing was replaced, so "rolled back to previous version" would be a
        sentence about an event that did not happen."""
        with self._flow(update_service) as flow:
            flow["_download_binary_program"].side_effect = OSError("connection reset")
            result = await update_service._update_binary_program(self.CONFIG_KEY, self.STATUS)

        assert result == {"success": False, "error": "connection reset"}
        flow["_rollback_binary_program"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_crash_after_the_unit_was_stopped_rolls_back(self, update_service):
        with self._flow(update_service) as flow:
            flow["_verify_binary_program"].side_effect = OSError("systemd gone")
            result = await update_service._update_binary_program(self.CONFIG_KEY, self.STATUS)

        assert "Rolled back" in result["error"]
        flow["_rollback_binary_program"].assert_called_once()
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/dl")


class TestDownloadSnapcastComponent:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("component", ["snapserver", "snapclient"])
    async def test_the_package_name_carries_the_arch_and_this_units_debian(
            self, update_service, tmp_path, component):
        """snapcast publishes one .deb per (component, arch, distro). Asking for
        the bookworm build on a trixie unit installs and then fails to start on
        a missing libstdc++ symbol — after the running snapserver was stopped."""
        with downloading() as session, \
                patch.object(update_service, "_get_debian_codename", return_value="trixie"), \
                patch("tempfile.mkdtemp", return_value=str(tmp_path)):
            result = await update_service._download_snapcast_component(component, "0.31.0")

        assert result["success"] is True
        assert session.urls == [
            "https://github.com/badaix/snapcast/releases/download/v0.31.0/"
            f"{component}_0.31.0-1_arm64_trixie.deb"
        ]
        assert Path(result["deb_path"]).name == f"{component}_0.31.0-1_arm64_trixie.deb"

    @pytest.mark.asyncio
    async def test_an_unknown_component_creates_nothing_at_all(self, update_service):
        """The temp dir is made only once the name is known, so this exit hands
        the caller no `temp_dir` to release — and must therefore not have made
        one."""
        made = []
        with patch("tempfile.mkdtemp", side_effect=lambda **kw: made.append(1)):
            result = await update_service._download_snapcast_component("snapproxy", "0.31.0")

        assert result["success"] is False
        assert made == []

    @pytest.mark.asyncio
    async def test_a_missing_release_removes_its_temp_dir(self, update_service, tmp_path):
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(status=404), \
                patch.object(update_service, "_get_debian_codename", return_value="bookworm"), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_snapcast_component("snapserver", "9.9.9")

        assert result["success"] is False
        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_a_download_that_raises_removes_its_temp_dir(self, update_service, tmp_path):
        staging = tmp_path / "dl"
        staging.mkdir()
        with patch("backend.core.updates.update.aiohttp.ClientSession",
                   side_effect=OSError("connection reset")), \
                patch.object(update_service, "_get_debian_codename", return_value="bookworm"), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_snapcast_component("snapclient", "0.31.0")

        assert result["success"] is False
        assert not staging.exists()


class TestRunDeployTimeout:

    @pytest.mark.asyncio
    async def test_a_deploy_wrapper_that_hangs_is_killed_and_reaped(self, update_service):
        """Every privileged step goes through this. `install-deb` runs
        `apt-get -f install`, which can sit on a lock for ever."""
        with spawning(hang=["sudo"]) as spawn:
            ok, out = await update_service._run_deploy("install-deb", "/tmp/x.deb", timeout=7)

        assert (ok, out) == (False, "Timed out after 7s")
        assert spawn.killed == ["sudo"]

    @pytest.mark.asyncio
    async def test_a_missing_wrapper_is_reported_not_raised(self, update_service):
        """`can_update_program` calls this to decide whether updates are offered
        at all; a raise there would take the whole programs panel down."""
        async def missing(*a, **k):
            raise FileNotFoundError("sudo")

        with patch("asyncio.create_subprocess_exec", new=missing):
            ok, out = await update_service._run_deploy("check", timeout=5)

        assert ok is False and "sudo" in out


class TestCleanupTempFilesFailure:

    @pytest.mark.asyncio
    async def test_a_directory_that_cannot_be_removed_does_not_fail_the_update(
            self, update_service, tmp_path, caplog):
        """It runs on the success path of every flow; a raise here would turn a
        completed update into a failure and trigger a rollback of good code."""
        with patch("shutil.rmtree", side_effect=OSError("device busy")), \
                caplog.at_level(logging.WARNING):
            await update_service._cleanup_temp_files(str(tmp_path))

        assert "device busy" in caplog.text


class TestRollbackMiloToCommit:
    """The most destructive method in the backend: `git -C <git_path> checkout
    --force <sha>` where `git_path` is the production checkout.

    Every spawn is doubled and the fixture at the top of this file makes an
    escaped one raise, because a checkout that ran for real would discard the
    working tree of whoever is on the box.
    """

    STATUS = MILO_STATUS

    @staticmethod
    def _router(*, checkout=(0, b"", b""), pip=(0, b"", b"")):
        def route(argv):
            if "checkout" in argv:
                return checkout
            if str(argv[0]).endswith("pip3"):
                return pip
            return (0, b"", b"")
        return route

    @pytest.mark.asyncio
    async def test_the_checkout_names_the_repo_and_the_saved_commit(self, update_service):
        """`-C <git_path>` is what keeps the checkout inside the tree being
        updated rather than the backend's own working directory, and the commit
        is the one `_get_current_commit` banked before the update."""
        with spawning(self._router()) as spawn, \
                patch.object(update_service, "_sync_system_files"), \
                patch.object(update_service, "_restart_service", return_value=True), \
                patch.object(update_service._systemd, "restart_self", new=AsyncMock()):
            assert await update_service._rollback_milo_to_commit("abc123def") is True

        git_path = update_service.programs["milo"]["git_path"]
        assert spawn.argv_for("git") == (
            "git", "-C", git_path, "checkout", "--force", "abc123def")

    @pytest.mark.asyncio
    async def test_a_checkout_that_failed_stops_before_rebuilding_anything(
            self, update_service, caplog):
        """The tree is still on the broken release. Reinstalling the venv
        against it and restarting the backend would make the failed update
        permanent instead of merely failed."""
        with spawning(self._router(checkout=(128, b"", b"fatal: bad object abc123def"))) as spawn, \
                patch.object(update_service, "_sync_system_files") as sync, \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_milo_to_commit("abc123def") is False

        assert "bad object" in caplog.text
        assert spawn.programs() == ["git"]
        sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_frontend_is_restored_only_when_the_update_had_swapped_it(
            self, update_service):
        """`frontend/dist` is not in git, so the checkout back does not bring
        the previous one with it — but the backup only holds it once the install
        got as far as the swap. Restoring unconditionally would deploy the
        *previous* release's frontend over a tree that never left the current
        one, which is worse than the failure being rolled back.
        """
        with spawning(self._router()), \
                patch.object(update_service, "_sync_system_files"), \
                patch.object(update_service, "_restart_service", return_value=True), \
                patch.object(update_service._systemd, "restart_self", new=AsyncMock()), \
                patch.object(update_service, "_restore_release_frontend") as restore:
            assert await update_service._rollback_milo_to_commit("abc123def") is True
            restore.assert_not_called()

            assert await update_service._rollback_milo_to_commit(
                "abc123def", restore_frontend=True) is True
            restore.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_frontend_that_cannot_be_restored_is_not_a_rollback(self, update_service):
        """The tree is back on the previous release but nginx still serves the
        failed one's assets. Reporting True sends the caller's message "Rolled
        back to previous version" over a unit showing the wrong app.
        """
        with spawning(self._router()), \
                patch.object(update_service, "_sync_system_files") as sync, \
                patch.object(update_service, "_restore_release_frontend",
                             side_effect=Exception("No frontend backup to restore")):
            assert await update_service._rollback_milo_to_commit(
                "abc123def", restore_frontend=True) is False
        sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_venv_that_cannot_be_rebuilt_is_not_a_rollback(self, update_service):
        """The tree is back on the old commit but the venv still holds the new
        release's dependencies. Reporting True sends the caller's message
        "Rolled back to previous version" over an appliance that will not boot."""
        with spawning(self._router(pip=(1, b"", b"No matching distribution found"))), \
                patch.object(update_service, "_sync_system_files") as sync:
            assert await update_service._rollback_milo_to_commit("abc123def") is False
        sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_pip_that_hangs_is_killed_and_does_not_freeze_the_rollback(self, update_service):
        """Unbounded, the rollback never reaches the backend restart and the UI
        shows an update running for ever."""
        fake = RecordingSpawn(self._router())

        async def spawn_with_hanging_pip(program, *args, **kwargs):
            proc = await fake(program, *args, **kwargs)
            if str(program).endswith("pip3"):
                async def times_out(input=None):
                    raise asyncio.TimeoutError()
                proc.communicate = times_out
            return proc

        with patch("asyncio.create_subprocess_exec", new=spawn_with_hanging_pip), \
                patch.object(update_service, "_sync_system_files") as sync:
            assert await update_service._rollback_milo_to_commit("abc123def") is False
        sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_kiosk_that_stays_down_is_reported_but_not_fatal(self, update_service, caplog):
        """A black screen the owner has to be told about; the tree is back on
        the previous commit and the backend restart below is what puts the unit
        back in service, so calling the rollback failed here would be wrong."""
        restart_self = AsyncMock()
        with spawning(self._router()), \
                patch.object(update_service, "_sync_system_files"), \
                patch.object(update_service, "_restart_service", return_value=False), \
                patch.object(update_service._systemd, "restart_self", restart_self), \
                caplog.at_level(logging.ERROR):
            assert await update_service._rollback_milo_to_commit("abc123def") is True

        assert "Kiosk failed to restart" in caplog.text
        restart_self.assert_awaited_once_with("milo-backend.service")

    @pytest.mark.asyncio
    async def test_the_backend_restarts_itself_last(self, update_service):
        """Restarting our own unit tears this process down mid-call, so anything
        after it does not run — the kiosk restart and the system-file sync have
        to be finished before it is issued."""
        order = []
        with spawning(self._router()), \
                patch.object(update_service, "_sync_system_files",
                             side_effect=lambda: order.append("sync") and None), \
                patch.object(update_service, "_restart_service",
                             side_effect=lambda unit: order.append(unit) or True), \
                patch.object(update_service._systemd, "restart_self",
                             side_effect=lambda unit: order.append("self")):
            await update_service._rollback_milo_to_commit("abc123def")

        assert order == ["sync", "milo-kiosk.service", "self"]


class TestMiloAppCheckoutArms:

    STATUS = MILO_STATUS

    @staticmethod
    def _router(*, checkout=(0, b"", b"")):
        def route(argv):
            if "rev-parse" in argv:
                return (0, b"abc123def456\n", b"")
            if "checkout" in argv:
                return checkout
            return (0, b"", b"")
        return route

    @pytest.mark.asyncio
    async def test_a_checkout_that_failed_rolls_the_tree_back(self, update_service):
        """A checkout can leave the tree on a half-written index; the saved
        commit is the only way back to something that boots."""
        with spawning(self._router(checkout=(1, b"", b"fatal: reference is not a tree"))), \
                patch("pathlib.Path.exists", return_value=True), \
                patch.object(update_service, "_rollback_milo_to_commit",
                             return_value=True) as rollback:
            result = await update_service._update_milo_app(self.STATUS)

        assert "reference is not a tree" in result["error"]
        assert "Rolled back" in result["error"]
        rollback.assert_awaited_once_with("abc123def456", restore_frontend=False)

    @pytest.mark.asyncio
    async def test_a_checkout_that_hangs_is_killed_and_rolls_back(self, update_service):
        """Two minutes is the ceiling; a checkout blocked on a busy card would
        otherwise hold the update open for ever."""
        fake = RecordingSpawn(self._router())

        async def spawn_with_hanging_checkout(program, *args, **kwargs):
            proc = await fake(program, *args, **kwargs)
            if "checkout" in args:
                async def times_out(input=None):
                    raise asyncio.TimeoutError()
                proc.communicate = times_out
            return proc

        with patch("asyncio.create_subprocess_exec", new=spawn_with_hanging_checkout), \
                patch("pathlib.Path.exists", return_value=True), \
                patch.object(update_service, "_rollback_milo_to_commit",
                             return_value=True) as rollback:
            result = await update_service._update_milo_app(self.STATUS)

        assert "Timed out" in result["error"]
        rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failure_with_no_saved_commit_reports_no_rollback_outcome(self, update_service):
        """`_get_current_commit` answers "" when HEAD cannot be read, and
        `git checkout --force ""` would be worse than no rollback at all. The
        bare error is then the honest answer — there is no outcome to describe."""
        def route(argv):
            if "rev-parse" in argv:
                return (128, b"", b"fatal: not a git repository")
            if "checkout" in argv:
                return (1, b"", b"fatal: reference is not a tree")
            return (0, b"", b"")

        with spawning(route), patch("pathlib.Path.exists", return_value=True), \
                patch.object(update_service, "_rollback_milo_to_commit") as rollback:
            result = await update_service._update_milo_app(self.STATUS)

        assert result["success"] is False
        assert "Rolled back" not in result["error"]
        assert "Rollback also failed" not in result["error"]
        rollback.assert_not_called()



class TestTheInstallTarget:
    """Which release an update installs, for each of the three gestures.

    One flow serves all three — the ordinary update, the trial of what upstream
    published past the manifest, and the return that ends such a trial — and it
    reads its version from `latest`. Pointing `latest` at the wrong release is
    an install that succeeds, verifies, and lands the wrong version.
    """

    @staticmethod
    def _status(*, version, upstream_version, ahead, validated=None, update_available=False):
        latest = {
            "status": "success",
            "version": version,
            "tag_name": f"v{version}",
            "html_url": f"https://example.invalid/{version}",
            "published_at": None,
            "upstream": {
                "version": upstream_version,
                "tag_name": f"v{upstream_version}",
                "html_url": f"https://example.invalid/{upstream_version}",
                "published_at": "2026-05-21T00:00:00Z",
                "ahead": ahead,
            },
        }
        if validated:
            latest["validated"] = {
                "version": validated,
                "tag_name": f"v{validated}",
                "html_url": f"https://example.invalid/{validated}",
                "published_at": None,
            }
        return {"latest": latest, "update_available": update_available}

    def test_the_upstream_target_installs_what_upstream_published(self):
        """The trial must reach the tag GitHub answered with, not the pinned one.

        Every flow builds its download from `latest.tag_name` — a source
        checkout, a .deb filename, a pip git ref. Leaving the pinned tag in
        place would reinstall the version already running and report success.
        """
        selected = UpdateService._select_target(
            self._status(version="5.2.2", upstream_version="5.2.3", ahead=True), "upstream"
        )

        assert selected["latest"]["version"] == "5.2.3"
        assert selected["latest"]["tag_name"] == "v5.2.3"
        assert selected["latest"]["html_url"].endswith("5.2.3")

    def test_nothing_is_installed_when_upstream_is_not_ahead(self):
        """`ahead` is the only admission: it is measured against what runs."""
        assert UpdateService._select_target(
            self._status(version="5.2.2", upstream_version="5.2.2", ahead=False), "upstream"
        ) is None

    def test_the_return_installs_the_manifest_version(self):
        """Ending a trial is a downgrade, so `update_available` is false for it.

        Gating the return on that flag is what would strand a unit on a version
        the manifest does not declare, with the button visible and inert.
        """
        selected = UpdateService._select_target(
            self._status(
                version="5.2.3", upstream_version="5.2.3", ahead=False,
                validated="5.2.2", update_available=False,
            ),
            "validated",
        )

        assert selected["latest"]["version"] == "5.2.2"
        assert selected["latest"]["tag_name"] == "v5.2.2"

    def test_an_ordinary_update_installs_the_offered_release(self):
        """No override in play: the offered release is the manifest's."""
        selected = UpdateService._select_target(
            self._status(
                version="0.9.0", upstream_version="0.9.0", ahead=False, update_available=True
            ),
            "validated",
        )

        assert selected["latest"]["version"] == "0.9.0"

    def test_a_unit_already_at_the_manifest_version_installs_nothing(self):
        assert UpdateService._select_target(
            self._status(version="0.9.0", upstream_version="0.9.0", ahead=False), "validated"
        ) is None


class TestForcedVersionBookkeeping:
    """The record of "this unit runs a version past the manifest".

    It is what survives a reboot, so it decides what the reconciliation on the
    next Milo update leaves alone — and, wrongly kept, what it would hold back
    for good.
    """

    @pytest.mark.asyncio
    async def test_a_successful_trial_is_recorded(self, update_service, mock_settings_service):
        status = {"latest": {"version": "5.2.3"}}
        await update_service._record_forced_version("shairport-sync", "upstream", status)

        assert mock_settings_service._storage["updates.forced_versions"] == {
            "shairport-sync": "5.2.3"
        }

    @pytest.mark.asyncio
    async def test_returning_to_the_manifest_drops_the_record(self, update_service, mock_settings_service):
        """Otherwise the unit reads as off-pin while running the pinned version.

        The reconciliation would then skip a program it is meant to own, and the
        row would keep offering a return to the version already installed.
        """
        update_service.programs["shairport-sync"]["validated_version"] = "5.2.2"
        mock_settings_service._storage["updates.forced_versions"] = {"shairport-sync": "5.2.3"}

        await update_service._record_forced_version(
            "shairport-sync", "validated", {"latest": {"version": "5.2.2"}}
        )

        assert mock_settings_service._storage["updates.forced_versions"] == {}

    @pytest.mark.asyncio
    async def test_an_override_the_manifest_caught_up_with_is_erased(self, update_service, mock_settings_service):
        """Pruned against the *pulled* manifest, on disk, not only on read.

        A reading that never lands would re-derive the same answer forever while
        the stale entry stayed one bump away from pinning the unit backwards.
        """
        update_service.programs["navidrome"]["validated_version"] = "0.64.0"
        mock_settings_service._storage["updates.forced_versions"] = {"navidrome": "0.63.9"}

        await update_service._prune_forced_versions()

        assert mock_settings_service._storage["updates.forced_versions"] == {}

    @pytest.mark.asyncio
    async def test_a_record_that_cannot_be_written_is_not_swallowed(
            self, update_service, mock_settings_service):
        """An unrecorded trial is an off-pin unit that reads as up to date.

        The row derives everything from the record: without it, installed sits
        above the manifest, `update_available` is false, no return button is
        drawn, and nothing anywhere says the unit runs something nobody
        validated. Reporting the update as successful would seal that.
        """
        mock_settings_service.set_setting_strict = AsyncMock(
            side_effect=OSError("No space left on device")
        )

        with pytest.raises(OSError):
            await update_service._record_forced_version(
                "shairport-sync", "upstream", {"latest": {"version": "5.2.3"}}
            )

    @pytest.mark.asyncio
    async def test_a_live_override_survives_the_pruning(self, update_service, mock_settings_service):
        update_service.programs["navidrome"]["validated_version"] = "0.63.2"
        mock_settings_service._storage["updates.forced_versions"] = {"navidrome": "0.64.0"}

        await update_service._prune_forced_versions()

        assert mock_settings_service._storage["updates.forced_versions"] == {"navidrome": "0.64.0"}


class ReleaseAssets:
    """Stands in for GitHub's release download host.

    Serves exactly the URLs it was given and 404s everything else, which is what
    makes "the install asked for the asset of the tag it was offered" an
    assertion rather than a coincidence.
    """

    def __init__(self, files: dict):
        self.files = files
        self.requested: list[str] = []

    def session(self, *args, **kwargs):
        assets = self

        class _Response:
            def __init__(self, url):
                assets.requested.append(url)
                self._body = assets.files.get(url)
                self.status = 200 if self._body is not None else 404

            @property
            def content(self):
                body = self._body

                class _Content:
                    async def iter_chunked(self, size):
                        for start in range(0, len(body), size):
                            yield body[start:start + size]

                return _Content()

            async def text(self):
                return self._body.decode()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def get(self, url, **kw):
                return _Response(url)

        return _Session()


def _frontend_tarball(tmp_path, marker=b"<!doctype html>release build"):
    """A real gzipped tar carrying `dist/index.html`, and its sha256.

    Built rather than faked: the install verifies a digest and then looks inside
    the archive for `dist/index.html`, so a stand-in that is not really a tar
    would pass a test the production path fails.
    """
    src = tmp_path / "src" / "dist"
    src.mkdir(parents=True)
    (src / "index.html").write_bytes(marker)
    archive = tmp_path / "milo-frontend.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(str(src), arcname="dist")
    body = archive.read_bytes()
    return body, hashlib.sha256(body).hexdigest()


class TestTheReleaseInstall:
    """A Milo update installs the release it offered, and the frontend that
    release was built with.

    Both halves used to be untrue at once. The offer came from
    `releases/latest`; the install ran `git pull origin main`, so what landed
    was main at the moment of the click — a tree nobody tagged, tested or
    published, different for every unit that pressed the button on a different
    day, and impossible to withhold because there was nothing to withhold. The
    frontend was then rebuilt on the Pi with `npm install`, resolving the
    dependency tree against whatever the registry offered that day.

    What is asserted here is the pair: the tag named by the offer is the ref
    that gets checked out, and `frontend/dist` comes off the release rather than
    out of a compiler.
    """

    @staticmethod
    def _router(spawned, *, checkout=(0, b"", b"")):
        def route(argv):
            spawned.append(argv)
            if "rev-parse" in argv:
                return (0, b"abc123def456\n", b"")
            if "checkout" in argv:
                return checkout
            return (0, b"", b"")

        return route

    @contextmanager
    def _flow(self, service, spawned, *, checkout=(0, b"", b"")):
        """Everything a Milo update does past the frontend, stubbed."""
        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(spawning(self._router(spawned, checkout=checkout)))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(patch.object(service, "_install_python_dependencies"))
            stack.enter_context(patch.object(service, "_sync_system_files"))
            stack.enter_context(patch.object(service, "_reconcile_dependencies", return_value=[]))
            stack.enter_context(patch.object(service, "_run_deploy", return_value=(True, "")))
            yield

    @pytest.mark.asyncio
    async def test_the_install_checks_out_the_tag_the_offer_named(self, update_service):
        """The version is not enough: "0.2.0" is not a ref, `v0.2.0` is. The
        offer carries the tag GitHub published precisely so the install has
        something to check out.
        """
        spawned = []
        with self._flow(update_service, spawned), \
                frontend_from_the_release(update_service) as frontend:
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is True
        git_path = update_service.programs["milo"]["git_path"]
        assert ("git", "-C", git_path, "checkout", "--force", "v0.2.0") in spawned
        frontend.assert_awaited_once_with("v0.2.0")

    @pytest.mark.asyncio
    async def test_no_branch_is_ever_fetched_or_checked_out(self, update_service):
        """The whole point. A `pull`, or a ref named `main`, means the unit is
        installing the tip of a branch again — which is not what was offered,
        and cannot be withheld.
        """
        spawned = []
        with self._flow(update_service, spawned), frontend_from_the_release(update_service):
            await update_service._update_milo_app(MILO_STATUS)

        assert spawned, "no git command ran at all"
        flat = [" ".join(map(str, argv)) for argv in spawned]
        assert not [c for c in flat if " pull" in c], flat
        assert not [c for c in flat if c.endswith(" main") or " main " in c], flat
        # The fetch carries its own refspec: a unit flashed from a release image
        # is cloned at one tag and fetches nothing else without it.
        assert any("fetch origin --tags --force" in c for c in flat), flat

    @pytest.mark.asyncio
    async def test_the_update_never_invokes_npm(self, update_service):
        """Two units updated a month apart resolved `npm install` against
        whatever the registry offered that day, so what a release was tested
        with and what a unit ran were two different trees — and a bad hour at
        the registry blocked the whole fleet from updating at all.
        """
        spawned = []
        with self._flow(update_service, spawned), frontend_from_the_release(update_service):
            await update_service._update_milo_app(MILO_STATUS)

        assert spawned, "no command ran at all"
        assert not [argv for argv in spawned if str(argv[0]).endswith("npm")], spawned

    @pytest.mark.asyncio
    async def test_the_frontend_comes_out_of_the_release_asset(self, update_service, tmp_path):
        """The real install, against a real tarball: what nginx ends up serving
        is the bytes the release published.
        """
        body, digest = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body, f"{url}.sha256": f"{digest}  milo-frontend.tar.gz".encode()})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets):
            await update_service._install_release_frontend("v0.2.0")

        assert (dist / "index.html").read_bytes() == b"<!doctype html>release build"
        assert assets.requested == [url, f"{url}.sha256"]

    @pytest.mark.asyncio
    async def test_the_previous_frontend_is_kept_for_the_rollback(self, update_service, tmp_path):
        """`frontend/dist` is not in git, so the checkout back cannot restore
        it. Moving it aside is the only reason a rolled-back update serves the
        app it was serving before.
        """
        body, digest = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body, f"{url}.sha256": digest.encode()})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets):
            await update_service._install_release_frontend("v0.2.0")
        update_service._restore_release_frontend()

        assert (dist / "index.html").read_bytes() == b"the previous release"

    @pytest.mark.asyncio
    async def test_a_checksum_mismatch_installs_nothing(self, update_service, tmp_path):
        """A truncated download extracts into a tree that is *almost* a
        frontend, and nginx serves it without complaining. The digest is the
        only thing between that and the owner.
        """
        body, _ = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body, f"{url}.sha256": b"0" * 64})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets), pytest.raises(Exception, match="checksum mismatch"):
            await update_service._install_release_frontend("v0.2.0")

        assert (dist / "index.html").read_bytes() == b"the previous release"

    @pytest.mark.asyncio
    async def test_a_release_publishing_no_frontend_fails_the_update(self, update_service, tmp_path):
        """A tag pushed without CI, or a build that did not finish. The update
        must stop at the download rather than reboot onto whatever `dist/`
        happened to be there.
        """
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({})
        self._point_at(update_service, tmp_path)

        with self._extracting(assets), pytest.raises(Exception, match="HTTP 404"):
            await update_service._install_release_frontend("v0.2.0")

        assert assets.requested == [url]

    @pytest.mark.asyncio
    async def test_a_frontend_that_cannot_be_installed_rolls_the_update_back(self, update_service):
        """It happens after the checkout, so the tree is already on the new
        release: the failure has to undo it, and it must not claim the frontend
        was swapped when it was not.
        """
        spawned = []
        with self._flow(update_service, spawned), \
                patch.object(update_service, "_install_release_frontend",
                             side_effect=Exception("Frontend checksum mismatch for v0.2.0")), \
                patch.object(update_service, "_rollback_milo_to_commit",
                             return_value=True) as rollback:
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert "checksum mismatch" in result["error"]
        rollback.assert_awaited_once_with("abc123def456", restore_frontend=False)

    @pytest.mark.asyncio
    async def test_the_fleet_is_pushed_from_the_tree_the_checkout_left(self, update_service):
        """The satellite tarball is built out of the repo directory, so the two
        halves ship from one commit only if the push happens after the checkout.
        Before it, every satellite would be handed the release the server just
        left.
        """
        order = []

        def router(argv):
            if "rev-parse" in argv:
                return (0, b"abc123def456\n", b"")
            if "checkout" in argv:
                order.append("checkout")
            return (0, b"", b"")

        async def push():
            order.append("push-satellites")
            return []

        update_service._satellites.push_client_app_to_fleet = AsyncMock(side_effect=push)

        with ExitStack() as stack:
            stack.enter_context(patch("pathlib.Path.exists", return_value=True))
            stack.enter_context(spawning(router))
            stack.enter_context(patch("asyncio.sleep", new_callable=AsyncMock))
            stack.enter_context(frontend_from_the_release(update_service))
            stack.enter_context(patch.object(update_service, "_install_python_dependencies"))
            stack.enter_context(patch.object(update_service, "_sync_system_files"))
            stack.enter_context(patch.object(update_service, "_reconcile_dependencies", return_value=[]))
            stack.enter_context(patch.object(update_service, "_run_deploy", return_value=(True, "")))
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is True
        assert order == ["checkout", "push-satellites"]

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _point_at(service, tmp_path):
        """Move the milo entry's two paths into tmp_path.

        `programs` is deep-copied per instance, so this cannot reach the live
        checkout the real entry names — which is the whole reason these tests
        may run the real swap at all.
        """
        service.programs["milo"]["git_path"] = str(tmp_path / "repo")
        service.programs["milo"]["backup_path"] = str(tmp_path / "backups")
        return tmp_path / "repo" / "frontend" / "dist"

    @staticmethod
    @contextmanager
    def _extracting(assets):
        """Serve the assets, and let `tar` extract for real, in process.

        The archive is a genuine tarball and the extraction is genuine — what
        is stood in for is the spawn, which the file-wide fixture refuses.
        """
        async def spawn(program, *args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            if program == "tar":
                argv = list(args)
                archive = argv[argv.index("-xzf") + 1]
                dest = argv[argv.index("-C") + 1]
                with tarfile.open(archive) as tar:
                    tar.extractall(dest, filter="data")
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.kill = Mock()
            proc.wait = AsyncMock()
            return proc

        with ExitStack() as stack:
            stack.enter_context(patch("asyncio.create_subprocess_exec", new=spawn))
            stack.enter_context(patch(
                "backend.core.updates.update.aiohttp.ClientSession",
                side_effect=lambda *a, **k: assets.session(),
            ))
            yield
