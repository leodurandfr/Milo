# backend/tests/test_update_service.py
"""
Tests for UpdateService — update orchestration, backup/restore, service management.

Deliberate limit, stated because it looks like debt and is not: the tests of
the dependency flows patch the service's own private phases — `_stop_service`,
`_run_deploy`, `_update_binary_program` and their neighbours — rather than only
its collaborators. UpdateService *is* an ordering: stop the
unit, deploy, restart, roll back when the restart fails. The phases are the
behaviour under test, and what these assert is which one ran, in which order,
and which did not run at all. Leaving the real phases in and mocking only
systemd, git, tar and the network would not drop the coupling to names — it
would move it onto the exact `systemctl` argv sequence, which is more brittle
still. So this file is out of scope for any sweep that replaces
`patch.object(service, "_private")` with a collaborator-level failure; the
sibling files where that migration *is* right say so in their own headers.

The Milō app update is the exception, and its tests (`MiloUnit` and the classes
after it) patch nothing of the service: what it owes is a journal that is true
at every instant and a handover when a step fails, and both are facts about the
processes it spawns and the files it leaves, read off one recording stand-in.
"""
import asyncio
import errno
import hashlib
import json
import logging
import os
import tarfile
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

from backend.config.constants import DEPLOY_UPDATE_CMD
from backend.core.updates import dpkg_lock
from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.update import UpdateService
from backend.api.programs import create_programs_router
from backend.core.models.audio_state import AudioSource
from backend.core.systemd import SystemdServiceManager
from backend.tests.release_signing import make_key, manifest_for

# This checkout's root, so a path the service builds can be checked against the
# tree that actually ships rather than against a literal repeated in the test.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Captured before any test patches it: `MiloUnit` runs ssh-keygen for real, so
# a signature is verified by the program a unit verifies it with.
_REAL_EXEC = asyncio.create_subprocess_exec


@pytest.fixture
def update_service(mock_settings_service, tmp_path):
    """Fresh UpdateService instance.

    Injects a real SystemdServiceManager so the service-control helpers (which
    now delegate to it) still exercise the subprocess layer patched by tests.
    The satellite service is a double: it reaches a second physical Pi, and the
    tests that care about the fleet push assert what the update did to it.

    The Milō update's journal and backups are pointed into tmp_path: on the
    appliance the live paths are the default ones, and a journal open there
    would refuse every update this file drives.
    """
    satellites = Mock()
    satellites.push_client_app_to_fleet = AsyncMock(return_value=[])
    mock_settings_service._storage["updates.forced_versions"] = {}
    with patch.dict("os.environ", {}, clear=True):
        service = UpdateService(systemd_manager=SystemdServiceManager(),
                                satellite_update_service=satellites,
                                settings_service=mock_settings_service)
    service.programs["milo"]["state_path"] = str(tmp_path / "milo-state")
    service.programs["milo"]["backup_path"] = str(tmp_path / "milo-backups")
    return service


# The programs served by the one shared _update_binary_program flow. Kept as a
# literal rather than derived from the catalog so a program dropping out of the
# flow is a visible test edit, not a silently shrinking parametrization.
BINARY_PROGRAMS = ["go-librespot", "camilladsp", "navidrome"]

# What the offer hands `_update_milo_app`. The release is named by its TAG, not
# only by its version: the tag is what `git checkout` is given and what the
# frontend asset URL is built from, so a status carrying only a version is a
# status the install cannot act on. The installed tag names the way back.
MILO_STATUS = {
    "installed": {"versions": {"main": "0.1.0"}, "raw_version": "v0.1.0", "release_tag": "v0.1.0"},
    "latest": {"status": "success", "version": "0.2.0", "tag_name": "v0.2.0"},
}


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
    proc.kill = Mock()  # asyncio.subprocess.Process.kill is synchronous
    proc.wait = AsyncMock()
    return proc


@contextmanager
def github_unreachable():
    """Stand in for the GitHub transport, refusing every call.

    A Milō update now reconciles the dependency set before it reboots, and that
    reads `releases/latest` for each dependency. A test about the app's own
    steps must stand in for that transport rather than reach it — this suite
    runs ON the appliance, and the conftest guard fails the run otherwise.
    Refusing leaves every dependency without an offer, so the reconciliation
    dispatches nothing — each installed one is reported as failed, which these
    tests do not read — and the app steps are what is left under test.
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
    """Tests for _stop_service, _start_service"""

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

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch.object(update_service, "_start_service", return_value=True) as mock_start:
            result = await update_service._rollback_binary_program(config, restart_service=True)
        assert result is False
        mock_start.assert_awaited_once_with("milo-spotify.service")

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
        """install-binary under a running process writes a file nothing is
        executing, so the restore cannot be claimed, and it is not attempted.
        """
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(update_service, "_stop_service", return_value=False), \
             patch.object(update_service, "_start_service", return_value=True):
            with patch.object(update_service, "_run_deploy") as mock_deploy:
                result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is False
        mock_deploy.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_rollback_install_still_starts_the_unit(self, update_service, tmp_path):
        """The update stopped the unit, and `Restart=on-failure` does not undo an
        explicit stop: a rollback that left before its start kept every room
        silent until a reboot. One start, and the rollback is still not claimed."""
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch.object(update_service, "_run_deploy", return_value=(False, "install-binary refused")), \
             patch.object(update_service, "_start_service", return_value=True) as mock_start:
            result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is False
        mock_start.assert_awaited_once_with("milo-spotify.service")

    @pytest.mark.asyncio
    async def test_a_rollback_whose_copy_raises_still_starts_the_unit(self, update_service, tmp_path):
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch("backend.core.updates.update.shutil.copy2",
                   side_effect=OSError(errno.ENOSPC, "No space left on device")), \
             patch.object(update_service, "_run_deploy") as mock_deploy, \
             patch.object(update_service, "_start_service", return_value=True) as mock_start:
            result = await update_service._rollback_binary_program(config, restart_service=True)

        assert result is False
        mock_deploy.assert_not_called()
        mock_start.assert_awaited_once_with("milo-spotify.service")

    @pytest.mark.asyncio
    async def test_a_rollback_needs_no_new_space_in_tmp(self, update_service, tmp_path):
        """/tmp is RAM, and the rollback runs exactly when an update may just have
        filled it (a shairport-sync build tree, a download): the copy install-binary
        is handed lives on disk, and does not outlive the rollback."""
        config = _rollback_config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")
        handed, asked = [], []
        real_mkstemp = tempfile.mkstemp

        def _mkstemp(*args, dir=None, **kwargs):
            # The directory asked for is recorded; the file itself stays in tmp_path.
            asked.append(Path(dir))
            return real_mkstemp(*args, dir=tmp_path, **kwargs)

        async def _deploy(*args, **kwargs):
            handed.append(Path(args[1]))
            return True, ""

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch("backend.core.updates.update.tempfile.mkstemp", side_effect=_mkstemp), \
             patch.object(update_service, "_run_deploy", side_effect=_deploy), \
             patch.object(update_service, "_start_service", return_value=True):
            assert await update_service._rollback_binary_program(config, restart_service=True) is True

        assert asked and not asked[0].is_relative_to("/tmp")
        # milo-deploy-update's validate_temp_path accepts /tmp/* and /var/tmp/* only.
        assert asked[0].is_relative_to("/var/tmp")
        assert len(handed) == 1 and handed[0].name.startswith("milo-rollback-")
        assert not handed[0].exists(), "the copy does not outlive the rollback"


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
        with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
            result = await update_service._install_deb_package("/tmp/snapserver.deb", "e" * 64)
        assert result["success"] is True
        # The wrapper checks its own copy against the digest before apt runs.
        assert spawn.call_args.args == ("sudo", "-n", DEPLOY_UPDATE_CMD, "install-deb", "/tmp/snapserver.deb", "e" * 64)

    @pytest.mark.asyncio
    async def test_no_declared_digest_is_said_in_the_wrappers_word(self, update_service):
        """A version past the manifest reaches the wrapper as `-`, never as an
        empty argument the wrapper would refuse or a digest invented here."""
        with patch.object(update_service, "_run_deploy", return_value=(True, "")) as deploy:
            await update_service._install_deb_package("/tmp/snapserver.deb", None)
        assert deploy.await_args.args == ("install-deb", "/tmp/snapserver.deb", "-")

    @pytest.mark.asyncio
    async def test_deploy_failure(self, update_service):
        with patch.object(update_service, "_run_deploy", return_value=(False, "Package installation failed")):
            result = await update_service._install_deb_package("/tmp/snapserver.deb", "e" * 64)
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
    async def test_an_unreadable_codename_fails_the_multiroom_update(self, update_service):
        """No codename, no snapcast update: the .deb is built per Debian release.

        If this fails, a unit whose /etc/os-release names no codename installs
        another release's package — bookworm's, under a trixie image — with a
        green update, or downloads before anything has said which one it needs.
        """
        proc = _make_mock_proc(stdout=b"\n")
        stop = AsyncMock(return_value=True)
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
                patch("backend.core.updates.update.aiohttp.ClientSession") as session, \
                patch.object(update_service, "_stop_service", stop):
            result = await update_service._update_multiroom(TestUpdateMultiroom.STATUS)

        assert result["success"] is False
        assert "codename" in result["error"]
        session.assert_not_called()
        stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_is_not_a_codename(self, update_service):
        proc = _make_mock_proc(returncode=1, stdout=b"trixie\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(RuntimeError, match="codename"):
                await update_service._get_debian_codename()


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
    async def test_no_sudo(self, update_service, caplog):
        """A broken sudoers rule for milo-deploy-update: the route's refusal is
        a warning, so this is the one place the server keeps a trace of why."""
        denied = b"sudo: a password is required"
        with patch("asyncio.create_subprocess_exec",
                   return_value=_make_mock_proc(returncode=1, stderr=denied)), \
                caplog.at_level(logging.ERROR):
            result = await update_service.can_update_program("go-librespot")
        assert result["can_update"] is False
        assert "deploy wrapper" in result["reason"].lower()
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == [
            "milo-deploy-update is not accessible through sudo: sudo: a password is required"
        ]

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
            "_backup_binary_program": backup or {"success": True},
            "_download_binary_program": download or {
                "success": True, "archive_path": "/tmp/dl/asset.tar.gz", "member": "asset",
                "sha256": "d" * 64, "temp_dir": "/tmp/dl",
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
            mocks["probe_active"] = stack.enter_context(patch.object(
                service._systemd, "probe_active", AsyncMock(return_value=service_active)))
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
        # The archive and its digest, never a binary unpacked by milo: the
        # wrapper checks its own copy and unpacks that.
        assert mocks["_run_deploy"].await_args.args == (
            "install-release-binary", "/tmp/dl/asset.tar.gz", "asset",
            PROGRAMS[program_key]["binary_path"], "d" * 64,
        )

    @pytest.mark.asyncio
    async def test_a_version_the_manifest_does_not_declare_reaches_the_wrapper_as_no_digest(
            self, update_service):
        """An upstream trial has no digest, and the wrapper is told so in its
        own word, `-`, rather than handed an empty argument it would refuse."""
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}
        download = {"success": True, "archive_path": "/tmp/dl/asset.tar.gz", "member": "asset",
                    "sha256": None, "temp_dir": "/tmp/dl"}

        with self._flow(update_service, service_active=True, download=download) as mocks:
            await update_service._update_binary_program("go-librespot", status)

        assert mocks["_run_deploy"].await_args.args[-1] == "-"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", ["go-librespot", "navidrome"])
    async def test_on_demand_program_leaves_inactive_service_stopped(self, update_service, program_key):
        """go-librespot's Spotify service is on-demand, and Navidrome runs only
        while the dock enables Music Library: updating must not start either —
        an update would otherwise run a catalog the dock switched off."""
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=False) as mocks:
            result = await update_service._update_binary_program(program_key, status)

        assert result["success"] is True
        mocks["_stop_service"].assert_not_awaited()
        mocks["_start_service"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_always_on_program_restarts_even_when_inactive(self, update_service):
        """CamillaDSP is always-on: the binary cannot be swapped while it is in
        use, and the service must be back up afterwards — whatever state it
        happened to be in when the update started.
        """
        status = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}

        with self._flow(update_service, service_active=False) as mocks:
            result = await update_service._update_binary_program("camilladsp", status)

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
    def _flow(service, *, multiroom, install=None, installed=None):
        """Stack the collaborators, leaving _update_multiroom itself real.

        `multiroom` is the stored `routing.multiroom_enabled`, which decides
        whether the units come back; `install` overrides the two
        _install_deb_package results; `installed` is what `snapserver
        --version` and `snapclient --version` read afterwards, both at the
        target unless given. No process holds apt's locks unless a test says
        so through `apt_holder` (what /proc/locks answers, look by look).
        """
        latest = TestUpdateMultiroom.STATUS["latest"]["version"]
        if installed is None:
            installed = {"snapserver": latest, "snapclient": latest}
        service.settings_service._storage["routing.multiroom_enabled"] = multiroom
        with ExitStack() as stack:
            mocks = {
                "_download_snapcast_component": stack.enter_context(patch.object(
                    service, "_download_snapcast_component",
                    return_value={"success": True, "deb_path": "/tmp/pkg.deb", "sha256": None,
                                  "temp_dir": "/tmp/dl"},
                )),
                "_install_deb_package": stack.enter_context(patch.object(
                    service, "_install_deb_package",
                    side_effect=install or [{"success": True}, {"success": True}],
                )),
                "get_installed_version": stack.enter_context(patch.object(
                    service, "get_installed_version",
                    return_value={"versions": installed},
                )),
                "apt_holder": stack.enter_context(patch.object(dpkg_lock, "holder", return_value=None)),
            }
            stack.enter_context(patch.object(dpkg_lock, "POLL_S", 0.001))
            for name, value in (("_stop_service", True), ("_start_service", True), ("_cleanup_temp_files", None)):
                mocks[name] = stack.enter_context(patch.object(service, name, return_value=value))
            yield mocks

    @staticmethod
    def _started(mocks):
        """The units _start_service was actually asked to start."""
        return [call.args[0] for call in mocks["_start_service"].await_args_list]

    @pytest.mark.asyncio
    async def test_units_stay_stopped_in_direct_mode(self, update_service):
        """The snapcast units have no WantedBy — nothing reconciles them after an
        update. Starting them in direct mode leaves snapclient holding
        hw:Loopback,0,0, so the next direct-mode source plays silence until reboot.
        """
        with self._flow(update_service, multiroom=False) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_units_come_back_in_multiroom_mode(self, update_service):
        """Multiroom mode: the update must not leave the appliance mute."""
        with self._flow(update_service, multiroom=True) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == list(self.SERVICES)

    @pytest.mark.asyncio
    async def test_the_restore_reads_the_setting_not_the_units(self, update_service):
        """Multiroom runs both units or neither, and the setting is what
        `_sync_snapcast_state` holds them to. A probe that timed out, or a unit
        caught `activating`, read as "was inactive" and left every room mute."""
        probe = AsyncMock(return_value=None)
        with self._flow(update_service, multiroom=True) as mocks, \
                patch.object(update_service._systemd, "probe_active", probe):
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert self._started(mocks) == list(self.SERVICES)
        probe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_install_failure_does_not_start_the_units_in_direct_mode(self, update_service):
        """The recovery branches restore the mode in force, they do not impose one."""
        with self._flow(update_service, multiroom=False, install=[{"success": False, "error": "dpkg"}]) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_failure_before_the_stop_starts_nothing(self, update_service):
        """The download phase runs before anything is stopped, so its except branch
        has no prior state to restore — and must not invent one.
        """
        with self._flow(update_service, multiroom=True) as mocks:
            mocks["_download_snapcast_component"].side_effect = RuntimeError("network down")
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == []

    @pytest.mark.asyncio
    async def test_a_unit_that_did_not_come_back_reads_differently(self, update_service):
        """A snapcast unit that stayed down after a failed install leaves the
        appliance mute — the opposite of the recovery the error claimed.
        """
        install = [{"success": False, "error": "dpkg"}]

        with self._flow(update_service, multiroom=True, install=install) as mocks:
            mocks["_start_service"].return_value = True
            restored = await update_service._update_multiroom(self.STATUS)

        with self._flow(update_service, multiroom=True, install=install) as mocks:
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
        with self._flow(update_service, multiroom=True) as mocks:
            mocks["_stop_service"].return_value = False
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self.SERVICES[0] in result["error"]
        assert mocks["_install_deb_package"].await_count == 0

    @pytest.mark.asyncio
    async def test_apt_held_past_the_wait_fails_the_update_with_nothing_stopped(self, update_service):
        """unattended-upgrades holds dpkg's lock for its whole run, and
        install-deb fails at once on it. Found after the stop, the room would
        sit mute through the wait and then through a restore."""
        with self._flow(update_service, multiroom=True) as mocks, \
                patch("backend.core.updates.update.APT_LOCK_WAIT_S", 0.003):
            mocks["apt_holder"].return_value = "unattended-upgr (pid 812)"
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert "unattended-upgr (pid 812)" in result["error"]
        assert mocks["_stop_service"].await_count == 0
        assert mocks["_install_deb_package"].await_count == 0
        assert self._started(mocks) == []
        cleaned = [c.args[0] for c in mocks["_cleanup_temp_files"].await_args_list]
        assert cleaned.count("/tmp/dl") == 2, "both downloads are cleaned up"

    @pytest.mark.asyncio
    async def test_apt_let_go_during_the_wait_lets_the_update_through(self, update_service):
        answers = ["apt-get (pid 77)", "apt-get (pid 77)"]
        with self._flow(update_service, multiroom=True) as mocks:
            mocks["apt_holder"].side_effect = lambda paths: answers.pop(0) if answers else None
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is True
        assert mocks["_install_deb_package"].await_count == 2
        looks = [set(c.args[0]) for c in mocks["apt_holder"].call_args_list]
        assert len(looks) == 3, "two looks at a held lock, a third at a free one"
        for looked_at in looks:
            assert dpkg_lock.APT_LISTS_LOCK in looked_at, "install-deb's `apt update` takes the lists' lock"
            assert set(dpkg_lock.DPKG_LOCKS) <= looked_at

    @pytest.mark.asyncio
    async def test_the_units_stopped_before_that_one_are_started_back(self, update_service):
        """Aborting mid-phase must leave the appliance as it was, not half stopped.

        The second unit refuses; the first was already stopped by then, and is
        the one the room needs back.
        """
        with self._flow(update_service, multiroom=True) as mocks:
            mocks["_stop_service"].side_effect = [True, False]
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self._started(mocks) == list(self.SERVICES)
        assert "restored" in result["error"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("installed", [
        pytest.param({}, id="both-removed"),
        pytest.param({"snapserver": "0.27.0", "snapclient": "0.27.0"}, id="old-version-stayed"),
        pytest.param({"snapserver": STATUS["latest"]["version"]}, id="snapclient-removed"),
    ])
    async def test_a_deb_that_did_not_end_up_installed_fails_the_update(self, update_service, installed):
        """The helper's OK is not the proof: what the binaries read afterwards is.

        `apt-get -f install -y` once removed a package whose Depends it could not
        satisfy and exited 0, so the update answered success over an uninstalled
        snapcast — every room mute, and `installed` None, which the
        reconciliation skips forever. The units still come back as the mode
        says, and the error says whether they did.
        """
        with self._flow(update_service, multiroom=True, installed=installed) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self.STATUS["latest"]["version"] in result["error"]
        assert self._started(mocks) == list(self.SERVICES)

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
                return {"success": True, "deb_path": "/tmp/s.deb", "sha256": None, "temp_dir": "/tmp/s"}
            return {"success": False, "error": "HTTP 404"}

        with patch.object(update_service, "_download_snapcast_component", side_effect=mock_download):
            with patch.object(update_service, "_cleanup_temp_files") as mock_cleanup:
                result = await update_service._update_multiroom(status)

        assert result["success"] is False
        mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("down", PROGRAMS["multiroom"]["services"])
    async def test_an_install_whose_units_do_not_start_is_not_a_success(self, update_service, down):
        """Installed is not done: a snapserver that does not start on the new
        package leaves every room mute, while the row read "up to date" and a
        Milō update closed "committed" with nothing about multiroom."""
        with self._flow(update_service, multiroom=True) as mocks:
            mocks["_start_service"].side_effect = lambda unit: unit != down
            result = await update_service._update_multiroom(self.STATUS)

        assert result["success"] is False
        assert self.STATUS["latest"]["version"] in result["error"]
        assert "manual intervention" in result["error"].lower()
        assert down in self._started(mocks), "the restart was not attempted"

    @pytest.mark.asyncio
    async def test_snapclient_install_failure_partial_success(self, update_service):
        install = [{"success": True}, {"success": False, "error": "install failed"}]

        with self._flow(update_service, multiroom=True, install=install) as mocks:
            result = await update_service._update_multiroom(self.STATUS)

        # The discriminator is which half failed: snapserver installed, snapclient
        # did not. "success is False" alone would also pass if snapserver had
        # failed, which is a different outcome (nothing was replaced).
        assert result["success"] is False
        assert "snapclient failed" in result["error"]
        assert mocks["_install_deb_package"].await_count == 2


class TestDependencyReconciliation:
    """The dependency set a Milō release declares, installed by that release.

    The release that arrived runs it once its trial committed, against the
    catalog it imported at start — so the set it compares against is the one
    it was validated with. One sequence, never one transaction: each flow keeps
    its own rollback, and a step that failed is reported rather than fatal.

    Phases are patched rather than collaborators, per this file's header: what
    is under test here is which program was reached, with which version, and
    which was not reached at all.
    """

    # A version string no manifest will ever hold, so a dispatch carrying one
    # can only have come from the pin the test set.
    BUMPED = "9.87.65"

    @staticmethod
    @contextmanager
    def _reconciling(service, *, installed=b"1.0.0", tag="v1.0.0", dispatch=None, unreadable=None,
                     through_flows=False):
        """Drive the real status chain, and record what was dispatched.

        `installed` answers every version command (or, callable, the one whose
        argv it is given) and `tag` every GitHub fetch, so each program's own
        regex decides which of them read as installed — the same filter
        production applies. `unreadable` maps a repo to the HTTP status GitHub
        answers its release with instead. No program's file is on disk unless
        a test patches `os.path.lexists` itself: a version nothing could read
        is then a program the unit does not have. `through_flows` records at
        each program's install flow instead of at `_dispatch_update`, so what
        the dispatch does before it is run.
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
            return _make_mock_proc(stdout=installed(args) if callable(installed) else installed)

        class _Response:
            def __init__(self, status=200):
                self.status = status

            async def json(self):
                if self.status == 403:
                    return {"message": "API rate limit exceeded"}
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

            def get(self, url, *a, **kw):
                for repo, status in (unreadable or {}).items():
                    if f"/repos/{repo}/" in url:
                        return _Response(status)
                return _Response()

        with ExitStack() as stack:
            stack.enter_context(patch("asyncio.create_subprocess_exec", side_effect=exec_))
            stack.enter_context(
                patch("backend.core.updates.update.aiohttp.ClientSession", return_value=_Session())
            )
            if through_flows:
                stack.enter_context(patch.object(service, "_update_binary_program", side_effect=record))
                for flow, key in (("_update_multiroom", "multiroom"),
                                  ("_update_shairport_sync", "shairport-sync"),
                                  ("_update_qobuz_proxy", "qobuz-proxy")):
                    async def one_flow(status, key=key):
                        return await record(key, status)
                    stack.enter_context(patch.object(service, flow, side_effect=one_flow))
            else:
                stack.enter_context(patch.object(service, "_dispatch_update", side_effect=record))
            stack.enter_context(patch("backend.core.updates.version.os.path.lexists", return_value=False))
            yield dispatched

    @staticmethod
    def _playing(service, source):
        """The programs router over a state machine whose active source is
        `source`, and the record of what happened in which order: a session
        ended, a program's install flow reached."""
        events = []
        machine = Mock()
        machine.broadcast = AsyncMock()
        machine.system_state.active_source = source
        machine.transition_to_source = AsyncMock(side_effect=lambda to: events.append(("end", to)))
        create_programs_router(service, Mock(), machine)
        return events, lambda key, _status: events.append(("install", key)) or {"success": True}

    @pytest.mark.asyncio
    async def test_the_session_a_dependency_serves_ends_before_its_install(self, update_service):
        """The reconciliation runs once the update screen has lifted, beside a
        unit the owner is listening to again: it stopped the daemon under the
        session — the sound cut, the source left selected over a restarted
        daemon, and for the library a lost-stream banner. It now ends the
        session first, as the route's own install does, and only the active
        source's."""
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED
        events, install = self._playing(update_service, AudioSource.MUSIC_LIBRARY)

        with self._reconciling(update_service, dispatch=install, through_flows=True):
            failed = await update_service._reconcile_dependencies()

        assert failed == []
        assert ("install", "navidrome") in events
        assert events.index(("end", AudioSource.NONE)) < events.index(("install", "navidrome"))
        assert events.count(("end", AudioSource.NONE)) == 1, events

    @pytest.mark.asyncio
    async def test_the_route_s_install_ends_the_same_session_the_same_way(self, update_service):
        """One implementation for the two callers: the route's install reaches
        the flow through the same dispatch, and ends the session there."""
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED
        events, install = self._playing(update_service, AudioSource.MUSIC_LIBRARY)

        with self._reconciling(update_service, dispatch=install, through_flows=True):
            result = await update_service.update_program("navidrome")

        assert result["success"] is True
        assert events == [("end", AudioSource.NONE), ("install", "navidrome")]

    @pytest.mark.asyncio
    async def test_a_source_that_is_not_playing_is_left_alone(self, update_service):
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED
        events, install = self._playing(update_service, AudioSource.RADIO)

        with self._reconciling(update_service, dispatch=install, through_flows=True):
            await update_service.update_program("navidrome")

        assert events == [("install", "navidrome")]

    @pytest.mark.asyncio
    async def test_a_dependency_off_its_pin_is_brought_to_it(self, update_service):
        """The pin the catalog resolved is the version installed."""
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED
        with self._reconciling(update_service) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == []
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
    async def test_the_app_itself_is_never_reconciled(self, update_service):
        """`milo` reaching the dispatcher re-enters `_update_milo_app`, from
        inside the post-commit phase of the update that just installed it."""
        # A tag ahead of the installed version, so `milo` is exactly what the
        # loop *would* pick up. A fixture where it is already current would
        # leave this guard untested while reading green.
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED
        with self._reconciling(update_service, tag="v2.0.0") as dispatched:
            await update_service._reconcile_dependencies()

        reached = [k for k, _ in dispatched]
        assert "milo" not in reached
        assert "navidrome" in reached, "the loop dispatched nothing at all"

    @pytest.mark.asyncio
    async def test_one_failed_dependency_does_not_stop_the_others(self, update_service):
        """Reported, not fatal — and never fatal to the programs behind it.

        Each flow restores itself on failure, so a failed step leaves the unit
        on the previous version of that one dependency: the state every unit is
        already in for anything nobody clicked. Aborting the sequence there
        would leave the rest of the set behind for no gain.
        """
        for key in ("go-librespot", "navidrome"):
            update_service.programs[key]["validated_version"] = self.BUMPED

        def dispatch(program_key, status):
            if program_key == "go-librespot":
                return {"success": False, "error": "download failed"}
            return {"success": True}

        with self._reconciling(update_service, dispatch=dispatch) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert failed == ["go-librespot"]
        # go-librespot precedes navidrome in the catalog, so navidrome having
        # been reached at all is the proof the loop did not stop on the failure.
        reached = [k for k, _ in dispatched]
        assert reached.index("go-librespot") < reached.index("navidrome")

    @pytest.mark.asyncio
    async def test_a_dependency_that_raises_is_caught(self, update_service):
        """The app has committed by the time this runs: an exception escaping
        would cut the post-commit phase before the satellites, and leave the
        journal open for the next start to run it all again."""
        update_service.programs["navidrome"]["validated_version"] = self.BUMPED

        with self._reconciling(update_service, dispatch=lambda *_a: None) as dispatched:
            failed = await update_service._reconcile_dependencies()

        # It returned at all — that is the assertion. Every program it reached
        # raised, and every one of them is reported rather than propagated.
        assert failed == [k for k, _ in dispatched]
        assert "navidrome" in failed

    @pytest.mark.asyncio
    async def test_qobuz_proxy_is_installed_before_the_rest_of_the_set(self, update_service):
        """The launcher the new release installed refuses a venv whose bindings
        moved, so Qobuz stays down until its venv has. Behind a shairport-sync
        compile, that is minutes."""
        for key in ("go-librespot", "shairport-sync", "qobuz-proxy"):
            update_service.programs[key]["validated_version"] = self.BUMPED

        with self._reconciling(update_service) as dispatched:
            await update_service._reconcile_dependencies()

        reached = [k for k, _ in dispatched]
        assert reached[0] == "qobuz-proxy", reached
        assert {"go-librespot", "shairport-sync"} <= set(reached)

    @pytest.mark.asyncio
    async def test_each_dependency_installed_is_announced_with_its_rank(self, update_service):
        """The banner says "<program> · k of n": n counts what will be
        installed, not the catalog, or a unit with one bump reads "1 of 8"."""
        for key in ("go-librespot", "navidrome"):
            update_service.programs[key]["validated_version"] = self.BUMPED
        announced = []

        async def on_progress(state):
            announced.append(state)

        update_service.set_milo_update_listener(on_progress, AsyncMock())
        with self._reconciling(update_service) as dispatched:
            await update_service._reconcile_dependencies()

        names = [update_service.programs[k]["name"] for k, _ in dispatched]
        assert [(s["phase"], s["item"], s["index"], s["total"]) for s in announced] == [
            ("dependencies", name, rank, len(names)) for rank, name in enumerate(names, 1)
        ]

    @pytest.mark.parametrize("http_status", [403, 502])
    @pytest.mark.asyncio
    async def test_a_dependency_whose_offer_cannot_be_read_is_reported_not_skipped(
            self, update_service, caplog, http_status):
        """No offer is no target, and no target is not "nothing to do".

        A unit without a GitHub token runs out of quota within the hour the
        settings screen was opened, and GitHub has outages. Skipping silently
        left the unit on the old dependency with nothing in the journal or the
        outcome to say the set was never reached.
        """
        with self._reconciling(
            update_service, unreadable={"navidrome/navidrome": http_status},
        ) as dispatched, caplog.at_level(logging.ERROR, logger="backend.core.updates.update.update"):
            failed = await update_service._reconcile_dependencies()

        assert "navidrome" in failed
        assert "navidrome" not in [k for k, _ in dispatched]
        assert "navidrome" in caplog.text

    @pytest.mark.asyncio
    async def test_a_dependency_not_installed_is_not_reported_for_its_offer(self, update_service):
        """A missing binary is "not installed", which the catalog means as no update.

        The qobuz-proxy venv and Navidrome are absent on many units by design;
        reporting them whenever GitHub fails would turn every outage into a
        list of failures that are not failures.
        """
        unreadable = {"navidrome/navidrome": 502, "devgianlu/go-librespot": 502}
        with self._reconciling(
            update_service, installed=lambda argv: b"" if "navidrome" in argv[0] else b"1.0.0",
            unreadable=unreadable,
        ) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert "go-librespot" in failed, "the outage reached no installed dependency"
        assert "navidrome" not in failed
        assert "navidrome" not in [k for k, _ in dispatched]

    @pytest.mark.asyncio
    async def test_a_dependency_whose_version_cannot_be_read_is_reinstalled_and_said(
            self, update_service, caplog):
        """Installed but unreadable — a version command that timed out on a cold
        card, a banner the regex no longer matches — is not "not installed".
        Skipped in silence, the unit kept the old daemon under code written for
        the new one, and the outcome said "committed" with no detail."""
        validated = update_service.programs["go-librespot"]["validated_version"]
        binary = update_service.programs["go-librespot"]["binary_path"]
        with self._reconciling(
            update_service, installed=lambda argv: b"" if "go-librespot" in " ".join(argv) else b"1.0.0",
            tag=f"v{validated}",
        ) as dispatched, patch(
            "backend.core.updates.version.os.path.lexists", side_effect=lambda path: path == binary,
        ), caplog.at_level(logging.ERROR, logger="backend.core.updates.update.update"):
            failed = await update_service._reconcile_dependencies()

        assert ("go-librespot", validated) in dispatched
        assert "go-librespot" not in failed
        assert "go-librespot: the installed version could not be read" in caplog.text

    @pytest.mark.asyncio
    async def test_a_dependency_absent_from_disk_is_still_left_alone(self, update_service, caplog):
        """The other half: no binary where the catalog names one is a program
        this unit does not have, and installing it is no update's business."""
        with self._reconciling(
            update_service, installed=lambda argv: b"" if "go-librespot" in " ".join(argv) else b"1.0.0",
        ) as dispatched, patch(
            "backend.core.updates.version.os.path.lexists", return_value=False,
        ), caplog.at_level(logging.ERROR, logger="backend.core.updates.update.update"):
            failed = await update_service._reconcile_dependencies()

        assert "go-librespot" not in [k for k, _ in dispatched]
        assert "go-librespot" not in failed
        assert "go-librespot" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_dependency_on_its_pin_needs_no_offer(self, update_service):
        """The target is the manifest's, so GitHub is needed only to install.

        On a healthy unit every dependency sits on its pin; reporting each one
        whenever the quota runs out would list the whole set as failed after an
        update that had nothing to do.
        """
        validated = update_service.programs["navidrome"]["validated_version"]
        with self._reconciling(
            update_service,
            installed=lambda argv: validated.encode() if "navidrome" in argv[0] else b"1.0.0",
            unreadable={"navidrome/navidrome": 403},
        ) as dispatched:
            failed = await update_service._reconcile_dependencies()

        assert "navidrome" not in failed
        assert "navidrome" not in [k for k, _ in dispatched]
        assert ("go-librespot", update_service.programs["go-librespot"]["validated_version"]) \
            in dispatched, "the loop dispatched nothing at all"


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
            with patch.object(update_service._systemd, "probe_active") as mock_active:
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
            with patch.object(update_service._systemd, "probe_active", return_value=False):
                result = await update_service._verify_shairport_sync_update(config, service_was_active=True)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_service_active(self, update_service):
        config = {"binary_path": "/usr/local/bin/shairport-sync", "service_name": "milo-airplay.service"}
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(update_service._systemd, "probe_active", return_value=True):
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

        with patch.object(update_service, "_start_service", return_value=True) as mock_start:
            result = await update_service._rollback_shairport_sync(config)
        assert result is False
        mock_start.assert_awaited_once_with("milo-airplay.service")

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

        with patch.object(update_service, "_stop_service", return_value=False), \
             patch.object(update_service, "_start_service", return_value=True):
            with patch.object(update_service, "_run_deploy") as mock_deploy:
                result = await update_service._rollback_shairport_sync(config, service_was_active=True)

        assert result is False
        mock_deploy.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["nqptp", "shairport-sync"])
    async def test_a_failed_rollback_install_still_starts_the_unit(self, update_service, tmp_path, failing):
        """Each unit the rollback stopped is started again once, whichever install
        failed: NQPTP is the AirPlay clock, and shairport-sync the receiver."""
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service",
            "nqptp": {"binary_path": "/usr/local/bin/nqptp", "service_name": "nqptp.service"},
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")
        (tmp_path / "backups" / "nqptp.backup").write_text("old")

        async def _deploy(*args, **kwargs):
            return (False, "install-binary refused") if args[2].endswith(failing) else (True, "")

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch.object(update_service, "_run_deploy", side_effect=_deploy), \
             patch.object(update_service, "_start_service", return_value=True) as mock_start:
            result = await update_service._rollback_shairport_sync(
                config, service_was_active=True, restore_nqptp=True
            )

        assert result is False
        assert sorted(c.args[0] for c in mock_start.await_args_list) == [
            "milo-airplay.service", "nqptp.service"
        ]


    @pytest.mark.asyncio
    async def test_an_nqptp_that_will_not_start_still_gets_its_shairport_back(self, update_service, tmp_path):
        """Once the old NQPTP is on disk, the new shairport-sync speaks the wrong
        interface: it goes back too, and the rollback is not claimed."""
        config = {
            "backup_path": str(tmp_path / "backups"),
            "binary_path": "/usr/local/bin/shairport-sync",
            "service_name": "milo-airplay.service",
            "nqptp": {"binary_path": "/usr/local/bin/nqptp", "service_name": "nqptp.service"},
        }
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "shairport-sync.backup").write_text("old")
        (tmp_path / "backups" / "nqptp.backup").write_text("old")

        with patch.object(update_service, "_stop_service", return_value=True), \
             patch.object(update_service, "_run_deploy", return_value=(True, "")) as mock_deploy, \
             patch.object(update_service, "_start_service",
                          side_effect=lambda name: name != "nqptp.service"):
            result = await update_service._rollback_shairport_sync(
                config, service_was_active=True, restore_nqptp=True
            )

        assert result is False
        assert [c.args[2] for c in mock_deploy.call_args_list] == [
            "/usr/local/bin/nqptp", "/usr/local/bin/shairport-sync"
        ]


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
            "_backup_shairport_sync": {"success": True},
            "_download_source": {
                "success": True, "temp_dir": "/tmp/sps", "source_dir": "/tmp/sps/src"
            },
            "_configure_source": {"success": True},
            "_compile_source": {"success": True},
            "_prepare_nqptp": {"success": True, "build": None, "expected": ("1.2.4", 10)},
            "_install_shairport_sync": install or {"success": True},
            "_verify_shairport_sync_update": {"success": True},
            "_stop_service": True,
            "_start_service": True,
            "_rollback_shairport_sync": rollback,
            "_cleanup_temp_files": None,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                service._systemd, "probe_active", AsyncMock(return_value=True)))
            yield {
                name: stack.enter_context(patch.object(service, name, return_value=value))
                for name, value in returns.items()
            }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["install", "start", "verify", "raise"])
    async def test_a_shairport_rollback_runs_after_its_build_tree_is_freed(self, update_service, failure):
        """The build tree sits in /tmp, which is RAM: a rollback run beside it is
        one that may find no room for its own copy."""
        with self._flow(update_service) as mocks:
            order = []
            mocks["_cleanup_temp_files"].side_effect = lambda *a: order.append("cleanup")
            mocks["_rollback_shairport_sync"].side_effect = lambda *a, **k: order.append("rollback") or True
            if failure == "install":
                mocks["_install_shairport_sync"].return_value = {"success": False, "error": "refused"}
            elif failure == "start":
                mocks["_start_service"].return_value = False
            elif failure == "verify":
                mocks["_verify_shairport_sync_update"].return_value = {"success": False, "error": "not running"}
            else:
                mocks["_install_shairport_sync"].side_effect = OSError(errno.ENOSPC, "No space left on device")

            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        assert order == ["cleanup", "rollback"]

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
            mocks["probe_active"] = stack.enter_context(patch.object(
                service._systemd, "probe_active", AsyncMock(return_value=True)))
            for name, value in (
                ("_download_release_asset", None),
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
        """A stand-in for `tar -tzf`: lists `produces`, or fails as tar does on
        an archive it cannot read."""

        async def _exec(*args, **_kwargs):
            assert args[:2] == ("tar", "-tzf"), args
            proc = AsyncMock()
            listing = f"{produces}\n".encode() if returncode == 0 and produces else b""
            stderr = b"" if returncode == 0 else b"gzip: stdin: not in gzip format"
            proc.communicate = AsyncMock(return_value=(listing, stderr))
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
    async def test_an_unreadable_archive_removes_its_temp_dir(self, update_service, tmp_path):
        with self._sandboxed_tmp(tmp_path) as created:
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session()))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    self._tar(returncode=2)))
                result = await update_service._download_binary_program(self.CONFIG, "0.7.0")

        assert result["success"] is False
        assert "not in gzip format" in result["error"]
        assert len(created) == 1
        assert not created[0].exists(), "the scratch directory outlived an unreadable archive"

    @pytest.mark.asyncio
    async def test_a_file_that_is_not_the_declared_one_is_refused_before_anything_stops(
            self, update_service, tmp_path):
        """The validated version's archive is checked against the manifest's
        digest as it streams in: bytes GitHub serves today that are not the file
        the digest was taken over never reach tar, nor the wrapper."""
        config = self.CONFIG
        spawned = []
        with self._sandboxed_tmp(tmp_path) as created:
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session(payload=b"not the release")))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    side_effect=lambda *a, **k: spawned.append(a)))
                result = await update_service._download_binary_program(
                    config, config["validated_version"]
                )

        assert result["success"] is False
        assert "checksum mismatch" in result["error"]
        assert config["validated_sha256"]["main"] in result["error"]
        assert spawned == []
        assert not created[0].exists(), "the scratch directory outlived a refused file"

    @pytest.mark.asyncio
    async def test_the_declared_file_is_handed_on_with_its_digest(self, update_service, tmp_path):
        """What the wrapper is told to check again is the digest the bytes were
        checked against here — the manifest's, for the validated version."""
        payload = b"the release archive"
        config = dict(self.CONFIG, validated_version="0.7.0",
                      validated_sha256={"main": hashlib.sha256(payload).hexdigest()})
        binary_name = Path(config["binary_path"]).name
        with self._sandboxed_tmp(tmp_path):
            with ExitStack() as stack:
                stack.enter_context(patch("backend.core.updates.update.aiohttp.ClientSession",
                                          self._session(payload=payload)))
                stack.enter_context(patch(
                    "backend.core.updates.update.asyncio.create_subprocess_exec",
                    self._tar(produces=binary_name)))
                result = await update_service._download_binary_program(config, "0.7.0")

        assert result["success"] is True, result
        assert result["sha256"] == hashlib.sha256(Path(result["archive_path"]).read_bytes()).hexdigest()
        await update_service._cleanup_temp_files(result["temp_dir"])

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
        assert Path(result["archive_path"]).read_bytes() == b"tarball-bytes"
        assert result["member"] == binary_name
        # A version the manifest does not declare carries no digest on.
        assert result["sha256"] is None
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
            result = await update_service._download_source("mikebrady/shairport-sync", "4.3.7", None)

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
            result = await update_service._download_source("mikebrady/shairport-sync", "9.9.9", None)

        assert result["success"] is False
        assert "404" in result["error"]
        assert not staging.exists()

    @pytest.mark.asyncio
    async def test_a_corrupt_tarball_is_reported_with_tars_own_reason(self, update_service, tmp_path):
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(), spawning(lambda argv: (2, b"", b"gzip: unexpected end of file")), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_source("mikebrady/shairport-sync", "4.3.7", None)

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
            result = await update_service._download_source("mikebrady/shairport-sync", "4.3.7", None)

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
            await update_service._download_source("mikebrady/shairport-sync", "4.3.7", None)

        archive = Path(spawn.argv_for("tar")[2])
        assert archive.read_bytes() == payload

    @pytest.mark.asyncio
    async def test_a_source_that_is_not_the_declared_archive_is_never_unpacked(
            self, update_service, tmp_path):
        """The tree is compiled and `make install`ed into /usr/local/bin through
        the root helper, so an archive that is not the one the manifest's digest
        was taken over is refused before tar opens it."""
        config = PROGRAMS["shairport-sync"]
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading(body=b"not the tag's archive"), spawning() as spawn, \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_source(
                config["repo"], config["validated_version"], config["validated_sha256"]["main"]
            )

        assert result["success"] is False
        assert "checksum mismatch" in result["error"]
        assert "tar" not in spawn.programs()
        assert not staging.exists()


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
            result = await update_service._configure_source("/src", self.FLAGS)

        assert result["success"] is True
        assert spawn.argv_for("./configure") == ("./configure", *self.FLAGS)

    @pytest.mark.asyncio
    async def test_both_steps_run_in_the_extracted_source_tree(self, update_service):
        """`autoreconf` and `./configure` are relative to the unpacked tree; run
        from the backend's own cwd they would regenerate the build system of
        this repository."""
        with spawning() as spawn:
            await update_service._configure_source("/src", self.FLAGS)

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
            result = await update_service._configure_source("/src", self.FLAGS)

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
            result = await update_service._configure_source("/src", self.FLAGS)

        assert result["success"] is False
        assert "Avahi library not found" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stalled", ["autoreconf", "./configure"])
    async def test_a_stalled_step_is_killed_and_reaped(self, update_service, stalled):
        """Both are bounded at five minutes. An unbounded one holds the update
        key in `active_updates` for ever and the UI shows a build in progress
        that will never end."""
        with spawning(hang=[stalled]) as spawn:
            result = await update_service._configure_source("/src", self.FLAGS)

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
            result = await update_service._compile_source("/src")

        assert result["success"] is True
        assert spawn.argv_for("make") == ("make", "-j4")
        assert spawn.call_for("make")["cwd"] == "/src"

    @pytest.mark.asyncio
    async def test_a_host_that_cannot_count_its_cores_still_builds(self, update_service):
        """`make -j` with an empty argument is `make -j`, which spawns an
        unbounded number of compilers and takes the Pi's memory with it."""
        with spawning(lambda argv: (0, b"", b"")) as spawn:
            await update_service._compile_source("/src")

        assert spawn.argv_for("make") == ("make", "-j2")

    @pytest.mark.asyncio
    async def test_a_build_error_is_reported_by_its_tail(self, update_service):
        """gcc's output is thousands of lines and the error is at the end; the
        head would be the banner. 500 characters is what reaches the UI banner."""
        noise = b"warning: unused variable\n" * 500
        with spawning(lambda argv: (2, b"", noise + b"error: ao.h: No such file")):
            result = await update_service._compile_source("/src")

        assert result["success"] is False
        assert "ao.h: No such file" in result["error"]

    @pytest.mark.asyncio
    async def test_a_build_that_never_finishes_is_killed_and_reaped(self, update_service):
        with spawning(hang=["make"]) as spawn:
            result = await update_service._compile_source("/src")

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
            "_backup_shairport_sync": {"success": True},
            "_download_source": {
                "success": True, "temp_dir": "/tmp/sps", "source_dir": "/tmp/sps/src"
            },
            "_configure_source": {"success": True},
            "_compile_source": {"success": True},
            "_prepare_nqptp": {"success": True, "build": None, "expected": ("1.2.4", 10)},
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
            mocks["probe_active"] = stack.enter_context(patch.object(
                service._systemd, "probe_active", AsyncMock(return_value=was_active)))
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
        flow["_download_source"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_download_that_failed_is_returned_verbatim(self, update_service):
        """It cleaned up its own temp dir already, and nothing was replaced, so
        neither a cleanup nor a rollback belongs here."""
        with self._flow(update_service, overrides={
            "_download_source": {"success": False, "error": "HTTP 404"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["error"] == "HTTP 404"
        flow["_cleanup_temp_files"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phase", ["_configure_source", "_compile_source"])
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
        """install-binary on a running unit replaces the image under a live
        process, which goes on running the old one."""
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
            PROGRAMS["shairport-sync"], True, restore_nqptp=False
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

    NEW_NQPTP = {"binary": "/tmp/sps/n/nqptp-1.2.9/nqptp", "version": "1.2.9"}

    @staticmethod
    def _record_order(flow, order, names):
        for name in names:
            mock, value = flow[name], flow[name].return_value
            mock.side_effect = lambda *a, _n=name, _v=value, **k: (order.append(_n), _v)[1]

    @pytest.mark.asyncio
    async def test_nqptp_goes_in_after_airplay_stops_and_before_shairport_sync(self, update_service):
        """Upstream's order for 5.6 (NQPTP first), and the only one that works:
        NQPTP's binary cannot be replaced while it runs, and the new
        shairport-sync cannot start against the old one."""
        order = []
        with self._flow(update_service, overrides={
            "_prepare_nqptp": {"success": True, "build": self.NEW_NQPTP, "expected": ("1.2.9", 11)},
            "_install_nqptp": {"success": True},
        }) as flow:
            self._record_order(flow, order, (
                "_stop_service", "_install_nqptp", "_install_shairport_sync", "_start_service",
            ))
            assert await update_service._update_shairport_sync(self.STATUS) == {"success": True}

        assert order == ["_stop_service", "_install_nqptp", "_install_shairport_sync", "_start_service"]
        flow["_install_nqptp"].assert_called_once_with(self.NEW_NQPTP)
        assert flow["_verify_shairport_sync_update"].call_args.kwargs == {"expected_nqptp": ("1.2.9", 11)}

    @pytest.mark.asyncio
    async def test_a_pair_that_cannot_be_formed_stops_nothing(self, update_service):
        """Refused while the running receiver is still untouched: stopping it
        to discover that the pair is impossible would cut a room's sound for
        an install that was never going to happen."""
        with self._flow(update_service, overrides={
            "_prepare_nqptp": {"success": False, "error": "needs NQPTP interface smi11"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result == {"success": False, "error": "needs NQPTP interface smi11"}
        flow["_stop_service"].assert_not_called()
        flow["_install_shairport_sync"].assert_not_called()
        flow["_rollback_shairport_sync"].assert_not_called()
        flow["_cleanup_temp_files"].assert_called_once_with("/tmp/sps")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["_install_nqptp", "_install_shairport_sync"])
    async def test_a_failure_once_nqptp_moved_puts_both_back(self, update_service, failing):
        """The old shairport-sync speaks the old interface only: restoring it
        over the new NQPTP is a receiver that cannot start either."""
        with self._flow(update_service, overrides={
            "_prepare_nqptp": {"success": True, "build": self.NEW_NQPTP, "expected": ("1.2.9", 11)},
            "_install_nqptp": {"success": True},
            failing: {"success": False, "error": "install-binary refused"},
        }) as flow:
            result = await update_service._update_shairport_sync(self.STATUS)

        assert result["success"] is False
        flow["_rollback_shairport_sync"].assert_called_once_with(
            PROGRAMS["shairport-sync"], True, restore_nqptp=True
        )


class TestShairportSyncNqptpPair:
    """What the shairport-sync update decides about NQPTP, and how it undoes it.

    The two share one ABI, the shared memory interface (SMI) each source tree
    declares in `nqptp-shm-structures.h`; shairport-sync 5.6 moved it from 10
    to 11 and refuses to start against any other.
    """

    NQPTP = PROGRAMS["shairport-sync"]["nqptp"]

    @staticmethod
    def _tree(root: Path, name: str, smi: Optional[int]) -> str:
        """A source tree carrying the header as both repositories ship it."""
        tree = root / name
        tree.mkdir()
        if smi is not None:
            (tree / "nqptp-shm-structures.h").write_text(
                '#define NQPTP_INTERFACE_NAME "/nqptp"\n\n'
                f"#define NQPTP_SHM_STRUCTURES_VERSION {smi}\n"
                "#define NQPTP_CONTROL_PORT 9000\n"
            )
        return str(tree)

    @contextmanager
    def _world(self, service, tmp_path, *, installed, nqptp_smi=11, capabilities=1 << 10):
        source = self._tree(tmp_path, "nqptp-src", nqptp_smi)
        (Path(source) / "nqptp").write_bytes(b"\x7fELF")
        with ExitStack() as stack:
            def patched(name, value):
                return stack.enter_context(patch.object(service, name, AsyncMock(return_value=value)))
            yield {
                "_installed_nqptp": patched("_installed_nqptp", installed),
                "_download_source": patched("_download_source", {
                    "success": True, "temp_dir": str(tmp_path), "source_dir": source,
                }),
                "_configure_source": patched("_configure_source", {"success": True}),
                "_compile_source": patched("_compile_source", {"success": True}),
                "ambient_capabilities": stack.enter_context(patch.object(
                    service._systemd, "ambient_capabilities", AsyncMock(return_value=capabilities),
                )),
                "source": source,
            }

    @pytest.mark.asyncio
    async def test_the_installed_pair_is_read_from_nqptp_itself(self, update_service):
        """The line `nqptp -V` prints, as measured on the unit (1.2.4)."""
        with patch.object(update_service, "_run_local", AsyncMock(
                return_value=(True, "Version: 1.2.4. Shared Memory Interface Version: smi10."))):
            assert await update_service._installed_nqptp(self.NQPTP["binary_path"]) == ("1.2.4", 10)
        with patch.object(update_service, "_run_local", AsyncMock(return_value=(False, "not found"))):
            assert await update_service._installed_nqptp(self.NQPTP["binary_path"]) is None

    @pytest.mark.asyncio
    async def test_an_nqptp_that_already_pairs_is_left_alone(self, update_service, tmp_path):
        """Restarting the AirPlay clock for nothing is a restart nobody needed."""
        shairport = self._tree(tmp_path, "sps", 11)
        pinned = (self.NQPTP["validated_version"], 11)
        with self._world(update_service, tmp_path, installed=pinned) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result == {"success": True, "build": None, "expected": pinned}
        world["_download_source"].assert_not_called()

    @pytest.mark.asyncio
    async def test_the_validated_nqptp_is_built_when_another_runs(self, update_service, tmp_path):
        shairport = self._tree(tmp_path, "sps", 11)
        with self._world(update_service, tmp_path, installed=("1.2.3", 10)) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        target = self.NQPTP["validated_version"]
        assert result == {
            "success": True,
            "build": {"binary": str(Path(world["source"]) / "nqptp"), "version": target},
            "expected": (target, 11),
        }
        # The manifest's NQPTP is the only one installed, so its source always
        # has a digest to be checked against.
        world["_download_source"].assert_called_once_with(
            self.NQPTP["repo"], target, self.NQPTP["validated_sha256"]["main"], parent_dir=str(tmp_path)
        )

    @pytest.mark.asyncio
    async def test_a_trial_that_moves_the_interface_is_refused_before_any_build(
            self, update_service, tmp_path):
        """A shairport-sync from upstream that needs an SMI the manifest's NQPTP
        does not speak: the pair is the manifest's to declare, both lines in
        one edit."""
        shairport = self._tree(tmp_path, "sps", 11)
        with self._world(update_service, tmp_path,
                         installed=(self.NQPTP["validated_version"], 10)) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result["success"] is False
        assert "smi11" in result["error"] and "NQPTP_VERSION" in result["error"]
        world["_download_source"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_validated_nqptp_on_another_interface_is_refused_before_compiling(
            self, update_service, tmp_path):
        shairport = self._tree(tmp_path, "sps", 11)
        with self._world(update_service, tmp_path, installed=("1.2.3", 10), nqptp_smi=10) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result["success"] is False
        assert "smi10" in result["error"]
        world["_compile_source"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_unit_that_grants_no_port_capability_is_refused_before_downloading(
            self, update_service, tmp_path):
        """NQPTP's right to ports 319/320 used to be a file capability on its
        binary. A replaced binary has none, nor does the backup a rollback puts
        back, so the swap would kill the clock and the rollback with it."""
        shairport = self._tree(tmp_path, "sps", 11)
        with self._world(update_service, tmp_path, installed=("1.2.3", 10),
                         capabilities=0) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result["success"] is False
        assert "CAP_NET_BIND_SERVICE" in result["error"]
        world["ambient_capabilities"].assert_called_once_with(self.NQPTP["service_name"])
        world["_download_source"].assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unreadable_capability_set_is_refused_without_blaming_the_unit(
            self, update_service, tmp_path):
        """A systemd read that failed (a slow bus, None) is not a unit that
        grants nothing. Reported as one, the Updates screen sends the operator
        off to reinstall an nqptp.service that already grants the capability,
        when retrying the shairport-sync update was all it took."""
        shairport = self._tree(tmp_path, "sps", 11)
        with self._world(update_service, tmp_path, installed=("1.2.3", 10),
                         capabilities=None) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result["success"] is False
        assert "does not grant" not in result["error"]
        assert "retry" in result["error"]
        assert self.NQPTP["service_name"] in result["error"]
        world["ambient_capabilities"].assert_called_once_with(self.NQPTP["service_name"])
        world["_download_source"].assert_not_called()

    @pytest.mark.asyncio
    async def test_a_shairport_tree_that_declares_no_interface_is_refused(self, update_service, tmp_path):
        """A header that moved or was renamed must not read as "no constraint"."""
        shairport = self._tree(tmp_path, "sps", None)
        with self._world(update_service, tmp_path, installed=("1.2.3", 10)) as world:
            result = await update_service._prepare_nqptp(shairport, "5.6", str(tmp_path))

        assert result["success"] is False
        world["_installed_nqptp"].assert_not_called()

    @pytest.mark.asyncio
    async def test_an_nqptp_that_exits_once_started_is_a_failed_install(self, update_service):
        """It binds its ports after it starts and exits when it cannot, so the
        start returning is not the clock running."""
        build = {"binary": "/tmp/sps/nqptp", "version": "1.2.9"}
        with patch.object(update_service, "_stop_service", AsyncMock(return_value=True)), \
                patch.object(update_service, "_start_service", AsyncMock(return_value=True)), \
                patch.object(update_service._systemd, "probe_active", AsyncMock(return_value=False)), \
                patch.object(update_service, "_run_deploy", AsyncMock(return_value=(True, ""))) as deploy, \
                patch("backend.core.updates.update.asyncio.sleep", AsyncMock()):
            result = await update_service._install_nqptp(build)

        assert result == {"success": False, "error": "NQPTP exited after update"}
        deploy.assert_called_once_with("install-binary", build["binary"], self.NQPTP["binary_path"])

    @pytest.mark.asyncio
    async def test_a_rollback_puts_nqptp_back_before_shairport_sync(self, update_service, tmp_path):
        (tmp_path / "shairport-sync.backup").write_bytes(b"old sps")
        (tmp_path / "nqptp.backup").write_bytes(b"old nqptp")
        config = {**PROGRAMS["shairport-sync"], "backup_path": str(tmp_path)}
        events = []

        async def deploy(*argv, **kwargs):
            events.append(("install", argv[2]))
            return True, ""

        def service(verb):
            async def act(name):
                events.append((verb, name))
                return True
            return act

        with patch.object(update_service, "_run_deploy", side_effect=deploy), \
                patch.object(update_service, "_stop_service", side_effect=service("stop")), \
                patch.object(update_service, "_start_service", side_effect=service("start")), \
                patch("backend.core.updates.update.asyncio.sleep", AsyncMock()):
            assert await update_service._rollback_shairport_sync(
                config, service_was_active=True, restore_nqptp=True
            ) is True

        assert events == [
            ("stop", config["service_name"]),
            ("stop", self.NQPTP["service_name"]),
            ("install", self.NQPTP["binary_path"]),
            ("start", self.NQPTP["service_name"]),
            ("install", config["binary_path"]),
            ("start", config["service_name"]),
        ]

    @pytest.mark.asyncio
    async def test_a_rollback_owed_an_nqptp_backup_it_lacks_still_restores_shairport_sync(
            self, update_service, tmp_path):
        """A unit that had no NQPTP before the update has none to go back to.
        Refusing outright left the new shairport-sync in place as well; the
        old one goes back, and the rollback is not claimed."""
        (tmp_path / "shairport-sync.backup").write_bytes(b"old sps")
        config = {**PROGRAMS["shairport-sync"], "backup_path": str(tmp_path)}
        with patch.object(update_service, "_run_deploy", AsyncMock(return_value=(True, ""))) as deploy, \
                patch.object(update_service, "_stop_service", AsyncMock(return_value=True)), \
                patch.object(update_service, "_start_service", AsyncMock(return_value=True)), \
                patch("backend.core.updates.update.asyncio.sleep", AsyncMock()):
            assert await update_service._rollback_shairport_sync(config, restore_nqptp=True) is False

        assert [c.args[2] for c in deploy.await_args_list] == [config["binary_path"]]

    async def test_an_older_runs_nqptp_backup_is_not_kept(self, update_service, tmp_path):
        """With no NQPTP installed now, a backup left by an earlier update would
        be restored by this one's rollback as if it were current."""
        (tmp_path / "nqptp.backup").write_bytes(b"from another update")
        config = {**PROGRAMS["shairport-sync"], "backup_path": str(tmp_path),
                  "binary_path": str(tmp_path / "shairport-sync"),
                  "nqptp": {**PROGRAMS["shairport-sync"]["nqptp"], "binary_path": str(tmp_path / "nqptp")}}

        assert (await update_service._backup_shairport_sync(config))["success"]
        assert not (tmp_path / "nqptp.backup").exists()


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
    def _flow(service, *, was_active=True, overrides=None, real=()):
        """Every step of the flow stubbed, but the ones `real` names."""
        returns = {
            "_download_release_asset": None,
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
                if name not in real
            }
            mocks["probe_active"] = stack.enter_context(patch.object(
                service._systemd, "probe_active", AsyncMock(return_value=was_active)))
            local = LocalCommands((overrides or {}).pop("_local_failures", None))
            mocks["_local"] = stack.enter_context(
                patch.object(service, "_run_local", local)) and local
            yield mocks

    @pytest.mark.asyncio
    async def test_pip_installs_the_checked_archive_of_the_upstream_tag_with_the_local_extra(
            self, update_service):
        """Three halves, all load-bearing. `[local]` pulls the extra that ships
        the local backend Milō streams through — without it the sidecar imports
        and answers nothing. The archive is the *tag's* (`v1.4.2`), not one
        rebuilt from the parsed version, which names no ref. And pip reads the
        file the download checked, never `git+…@tag`: a tag is a pointer its
        owner can move, and a clone is nothing a digest can be taken over."""
        with self._flow(update_service) as flow:
            assert await update_service._update_qobuz_proxy(self.STATUS) == {"success": True}

        url, archive, sha256 = flow["_download_release_asset"].await_args.args
        assert url == "https://github.com/leolobato/qobuz-proxy/archive/refs/tags/v1.4.2.tar.gz"
        # 1.4.2 is not the manifest's version, so nothing declares its digest.
        assert sha256 is None
        pip_argv = flow["_local"].calls[0]
        assert pip_argv[0] == f"{QOBUZ['venv_path']}/bin/pip"
        assert pip_argv[1:3] == ("install", "--upgrade")
        assert pip_argv[3] == f"qobuz-proxy[local] @ {Path(archive).as_uri()}"

    @pytest.mark.asyncio
    async def test_the_validated_release_is_checked_against_the_manifest_digest(self, update_service):
        status = {"latest": {"version": QOBUZ["validated_version"],
                             "tag_name": f"v{QOBUZ['validated_version']}"}}
        with self._flow(update_service) as flow:
            await update_service._update_qobuz_proxy(status)

        assert flow["_download_release_asset"].await_args.args[2] == QOBUZ["validated_sha256"]["main"]

    @pytest.mark.asyncio
    async def test_an_archive_that_is_not_the_declared_one_touches_nothing(self, update_service):
        """Refused before the venv is copied and before the sidecar stops: the
        download is the first thing the update does, and the bytes GitHub
        serves for the validated tag are not the ones its digest was taken over."""
        version = QOBUZ["validated_version"]
        status = {"latest": {"version": version, "tag_name": f"v{version}"}}
        with self._flow(update_service, real=("_download_release_asset",)) as flow, \
                downloading(body=b"not the release"):
            result = await update_service._update_qobuz_proxy(status)

        assert result["success"] is False
        assert "checksum mismatch" in result["error"]
        assert QOBUZ["validated_sha256"]["main"] in result["error"]
        flow["_backup_qobuz_venv"].assert_not_called()
        flow["_stop_service"].assert_not_called()
        assert flow["_local"].calls == []

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
            "_backup_binary_program": {"success": True},
            "_download_binary_program": {
                "success": True, "archive_path": "/tmp/dl/camilladsp.tar.gz", "member": "camilladsp",
                "sha256": None, "temp_dir": "/tmp/dl"},
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
    @pytest.mark.parametrize("component", ["snapserver", "snapclient"])
    async def test_each_package_of_the_validated_version_is_checked_against_its_own_digest(
            self, update_service, tmp_path, component):
        """The two .debs have one digest each: the server's snapserver must not
        pass under the snapclient's, and bytes that are neither are refused
        before the units stop — the manifest's digest named in the refusal."""
        version = PROGRAMS["multiroom"]["validated_version"]
        digest = PROGRAMS["multiroom"]["validated_sha256"][component]
        staging = tmp_path / "dl"
        staging.mkdir()
        with downloading() as session, \
                patch.object(update_service, "_get_debian_codename", return_value="trixie"), \
                patch("tempfile.mkdtemp", return_value=str(staging)):
            result = await update_service._download_snapcast_component(component, version)

        assert len(session.urls) == 1
        assert result["success"] is False
        assert "checksum mismatch" in result["error"] and digest in result["error"]
        assert not staging.exists(), "the refused package was left in /tmp"

    @pytest.mark.asyncio
    async def test_an_unknown_component_creates_nothing_at_all(self, update_service):
        """The temp dir is made only once the name is known, so this exit hands
        the caller no `temp_dir` to release — and must therefore not have made
        one."""
        made = []
        with patch.object(update_service, "_get_debian_codename", return_value="trixie"), \
                patch("tempfile.mkdtemp", side_effect=lambda **kw: made.append(1)):
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


class TestRunDeployVerdict:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exit_code, stream, expected", [
        (0, "stdout", (True, "installed")),
        (1, "stderr", (False, "E: dpkg was interrupted")),
    ])
    async def test_a_deploy_that_outlives_its_timeout_reports_its_real_exit(
            self, update_service, tmp_path, caplog, exit_code, stream, expected):
        """milo cannot signal the root helper and sudo does not relay SIGKILL,
        so a step that runs past its bound keeps running. Reporting "Timed out"
        there made `_update_multiroom` restore the services and announce a
        failure over a package `apt` had in fact installed."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sudo = bin_dir / "sudo"
        # Like the real sudo: the command is a child that inherits the pipes.
        sudo.write_text('#!/bin/sh\n[ "$1" = -n ] && shift\n"$@" &\nwait $!\nexit $?\n')
        sudo.chmod(0o755)
        helper = tmp_path / "milo-deploy-update"
        text = expected[1]
        redirect = "" if stream == "stdout" else " >&2"
        helper.write_text(f"#!/bin/sh\nsleep 0.5\necho '{text}'{redirect}\nexit {exit_code}\n")
        helper.chmod(0o755)

        async def through_the_fake_sudo(program, *args, **kwargs):
            assert program == "sudo"
            return await asyncio.subprocess.create_subprocess_exec(str(sudo), *args, **kwargs)

        with patch("asyncio.create_subprocess_exec", new=through_the_fake_sudo), \
                patch("backend.core.updates.update.DEPLOY_UPDATE_CMD", str(helper)), \
                caplog.at_level(logging.WARNING, logger="backend.core.updates.update.update"):
            result = await update_service._run_deploy("install-deb", "/tmp/x.deb", warn_after=0.05)

        assert result == expected
        assert "still running after" in caplog.text

    @pytest.mark.asyncio
    async def test_a_missing_wrapper_is_reported_not_raised(self, update_service):
        """`can_update_program` calls this to decide whether updates are offered
        at all; a raise there would take the whole programs panel down."""
        async def missing(*a, **k):
            raise FileNotFoundError("sudo")

        with patch("asyncio.create_subprocess_exec", new=missing):
            ok, out = await update_service._run_deploy("check", warn_after=5)

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


class TestTheInstallTarget:
    """Which release an update installs, for each of the three gestures.

    One flow serves all three — the ordinary update, the trial of what upstream
    published past the manifest, and the return that ends such a trial — and it
    reads its version from `latest`. Pointing `latest` at the wrong release is
    an install that succeeds, verifies, and lands the wrong version.
    """

    @staticmethod
    def _status(*, version, upstream_version, ahead, validated=None, update_available=False,
                trials=(), installed=None):
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
            "trials": [
                {
                    "version": trial,
                    "tag_name": f"v{trial}",
                    "html_url": f"https://example.invalid/{trial}",
                    "published_at": "2026-05-01T00:00:00Z",
                }
                for trial in trials
            ],
        }
        if validated:
            latest["validated"] = {
                "version": validated,
                "tag_name": f"v{validated}",
                "html_url": f"https://example.invalid/{validated}",
                "published_at": None,
            }
        status = {"latest": latest, "update_available": update_available}
        if installed:
            status["installed"] = {"status": "installed", "versions": {"main": installed}}
        return status

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

    def test_a_chosen_trial_installs_that_release(self):
        """Stepping down from the latest to an earlier trial: `ahead` is false, and irrelevant."""
        selected = UpdateService._select_target(
            self._status(
                version="0.9.0", upstream_version="0.9.0", ahead=False, validated="0.7.2",
                trials=("0.9.0", "0.8.1"), installed="0.9.0",
            ),
            "upstream", "0.8.1",
        )

        assert selected["latest"]["version"] == "0.8.1"
        assert selected["latest"]["tag_name"] == "v0.8.1"
        assert selected["latest"]["html_url"].endswith("0.8.1")

    def test_a_version_outside_the_window_is_refused(self):
        """Below the manifest, or anything the offer did not list, installs nothing."""
        assert UpdateService._select_target(
            self._status(
                version="0.7.2", upstream_version="0.9.0", ahead=True,
                trials=("0.9.0", "0.8.1"), installed="0.7.2",
            ),
            "upstream", "0.7.1",
        ) is None

    def test_the_running_version_is_not_reinstalled(self):
        assert UpdateService._select_target(
            self._status(
                version="0.8.1", upstream_version="0.9.0", ahead=True, validated="0.7.2",
                trials=("0.9.0", "0.8.1"), installed="0.8.1",
            ),
            "upstream", "0.8.1",
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
    async def test_a_chosen_trial_is_recorded_as_the_forced_version(
            self, update_service, mock_settings_service):
        """The record must name what was installed, not upstream's latest.

        Recording the latest would have the next Milō update's reconciliation
        move the unit up to it — undoing the choice behind the user's back.
        """
        update_service.programs["go-librespot"]["validated_version"] = "0.7.2"
        mock_settings_service._storage["updates.forced_versions"] = {"go-librespot": "0.9.0"}
        selected = UpdateService._select_target(
            TestTheInstallTarget._status(
                version="0.9.0", upstream_version="0.9.0", ahead=False, validated="0.7.2",
                trials=("0.9.0", "0.8.1"), installed="0.9.0",
            ),
            "upstream", "0.8.1",
        )

        await update_service._record_forced_version("go-librespot", "upstream", selected)

        assert await update_service.get_forced_versions() == {"go-librespot": "0.8.1"}

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


def _frontend_tarball(tmp_path, marker=b"<!doctype html>release build", extra=()):
    """A real gzipped tar carrying `dist/index.html` (and the `extra` paths under
    `dist/`), and its sha256.

    Built rather than faked: the install verifies a digest and then looks inside
    the archive for `dist/index.html`, so a stand-in that is not really a tar
    would pass a test the production path fails.
    """
    src = tmp_path / "src" / "dist"
    src.mkdir(parents=True)
    (src / "index.html").write_bytes(marker)
    for name in extra:
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(b"chunk")
    archive = tmp_path / "milo-frontend.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(str(src), arcname="dist")
    body = archive.read_bytes()
    return body, hashlib.sha256(body).hexdigest()


class TestTheReleaseFrontend:
    """The frontend a Milo update installs is the one its release was built with.

    It used to be rebuilt on the Pi with `npm install`, resolving the
    dependency tree against whatever the registry offered that day. These drive
    the stage and the swap directly, over a real tarball and a real extraction;
    the whole update around them is `TestTheMiloInstall`'s.
    """

    @pytest.mark.asyncio
    async def test_the_frontend_comes_out_of_the_release_asset(self, update_service, tmp_path):
        """What nginx ends up serving is the bytes the release published."""
        body, digest = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body, f"{url}.sha256": b"0" * 64})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets):
            await self._install(update_service, "v0.2.0", digest)

        assert (dist / "index.html").read_bytes() == b"<!doctype html>release build"
        # The asset alone: the digest it is checked against is the signed
        # manifest's, and the sidecar — whatever the channel served — is not read.
        assert assets.requested == [url]

    @pytest.mark.asyncio
    async def test_the_previous_frontend_is_kept_where_the_guard_finds_it(self, update_service, tmp_path):
        """`frontend/dist` is not in git, so no checkout can bring it back:
        moved aside rather than deleted, into the backup the guard restores
        from."""
        from backend.core.updates.milo_journal import KEPT_DIST_NAME

        body, digest = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets):
            await self._install(update_service, "v0.2.0", digest)

        kept = Path(update_service.programs["milo"]["backup_path"]) / KEPT_DIST_NAME
        assert (kept / "index.html").read_bytes() == b"the previous release"

    @pytest.mark.asyncio
    async def test_a_checksum_mismatch_installs_nothing(self, update_service, tmp_path):
        """A truncated download extracts into a tree that is *almost* a
        frontend, and nginx serves it without complaining. The digest is the
        only thing between that and the owner.
        """
        body, _ = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")

        with self._extracting(assets), pytest.raises(Exception, match="checksum mismatch"):
            await self._install(update_service, "v0.2.0", "0" * 64)

        assert (dist / "index.html").read_bytes() == b"the previous release"

    @pytest.mark.asyncio
    async def test_frontend_staging_shares_the_filesystem_of_dist(self, update_service, tmp_path):
        """The swap is a rename only if the release is staged on the filesystem
        `dist` lives on. Staged in /tmp — a tmpfs on the unit — the move into
        `dist` was a copy, and a copy that failed halfway left nginx serving a
        tree of two releases. The path is what is asserted: under pytest
        tmp_path sits on /tmp too, so a device comparison alone proves nothing.

        A staging directory left by a killed install is gone after the next
        one, and so is this one's: git does not see it, but the disk does.
        """
        body, digest = _frontend_tarball(tmp_path)
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({url: body})

        dist = self._point_at(update_service, tmp_path)
        dist.mkdir(parents=True)
        (dist / "index.html").write_bytes(b"the previous release")
        stale = dist.parent / ".dist-staging-stale"
        (stale / "dist").mkdir(parents=True)

        extracted_into = []
        with self._extracting(assets, extracted_into):
            await self._install(update_service, "v0.2.0", digest)

        assert len(extracted_into) == 1, extracted_into
        dest, device = extracted_into[0]
        assert dest.is_relative_to(dist.parent), dest
        assert device == os.stat(dist.parent).st_dev
        assert (dist / "index.html").read_bytes() == b"<!doctype html>release build"
        assert not stale.exists()
        assert not list(dist.parent.glob(".dist-staging-*"))

    @pytest.mark.asyncio
    async def test_a_release_publishing_no_frontend_stages_nothing(self, update_service, tmp_path):
        """A tag pushed without CI, or a build that did not finish."""
        url = update_service.programs["milo"]["frontend_asset_url"].format(tag="v0.2.0")
        assets = ReleaseAssets({})
        self._point_at(update_service, tmp_path).parent.mkdir(parents=True)

        with self._extracting(assets), pytest.raises(Exception, match="HTTP 404"):
            await self._install(update_service, "v0.2.0", "0" * 64)

        assert assets.requested == [url]

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _install(service, tag, digest):
        """Stage the release's frontend against the digest its manifest names,
        then swap it in, as the flow does."""
        await service._swap_release_frontend(await service._stage_release_frontend(tag, digest), tag)

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
    def _extracting(assets, extracted_into=None):
        """Serve the assets, and let `tar` extract for real, in process."""
        async def spawn(program, *args, **kwargs):
            assert program == "tar", f"unexpected process: {program} {args}"
            argv = list(args)
            archive = argv[argv.index("-xzf") + 1]
            dest = argv[argv.index("-C") + 1]
            if extracted_into is not None:
                extracted_into.append((Path(dest), os.stat(dest).st_dev))
            with tarfile.open(archive) as tar:
                tar.extractall(dest, filter="data")
            return _make_mock_proc()

        with ExitStack() as stack:
            stack.enter_context(patch("asyncio.create_subprocess_exec", new=spawn))
            stack.enter_context(patch(
                "backend.core.updates.update.aiohttp.ClientSession",
                side_effect=lambda *a, **k: assets.session(),
            ))
            yield


class MiloUnit:
    """The appliance as a Milō update sees it, built in tmp_path.

    A checkout on release `v0.1.0` (commit ORIGIN) with its venv, its built
    frontend and its data files; GitHub's release host serving `v0.2.0`'s
    frontend and its manifest, signed by the key the unit trusts (`key`);
    and one stand-in for every process the update spawns — git, pip, the
    venv's python, tar, `sudo milo-deploy-update`, `sudo systemctl` and
    `systemctl is-active` — that answers as the unit would and records the
    argv. ssh-keygen alone runs for real. `events` interleaves those argv with
    the two other things the install's durability rests on: each `os.sync()`
    and each journal write.

    `fail(argv)` may answer `(returncode, stdout, stderr)` for any argv to make
    that process fail; `other(argv)` answers what the update itself never
    spawns (the version reads of the post-commit phase) and refuses by default.
    No process holds apt's locks unless a test names one in `apt_held_by`
    (what /proc/locks answers at every look).
    """

    ORIGIN = "0" * 39 + "a"
    TARGET = "0" * 39 + "b"
    # requirements.lock as uv writes it: a header naming the command, then one
    # pin per package with its hashes and why it is there.
    REQUIREMENTS = (
        "# This file was autogenerated by uv via the following command:\n"
        "#    uv pip compile requirements.txt --universal --python-version 3.13 "
        "--generate-hashes -o requirements.lock\n"
        "aiohttp==3.14.5 \\\n"
        f"    --hash=sha256:{'a' * 64}\n"
        "    # via -r requirements.txt\n"
        "fastapi==0.165.0 \\\n"
        f"    --hash=sha256:{'b' * 64}\n"
        "    # via -r requirements.txt\n"
    )

    def __init__(self, service, tmp_path, *, target_requirements=REQUIREMENTS,
                 porcelain=b"", fail=None, other=None, free_bytes=1 << 40):
        from backend.core.updates import milo_journal

        self.journal_names = milo_journal
        self.target_requirements = target_requirements
        self.porcelain = porcelain
        self.fail = fail or (lambda argv: None)
        self.other = other
        self.free_bytes = free_bytes
        self.apt_held_by: Optional[str] = None
        self.events: list[tuple] = []
        self.units: dict[str, bool] = {}
        self.manifest_at_open: Optional[bool] = None

        self.repo = tmp_path / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.dist = self.repo / "frontend" / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_bytes(b"the previous release")
        (self.dist / "assets" / "index-OLD.js").write_bytes(b"chunk")
        self.venv = self.repo / "venv"
        (self.venv / "lib").mkdir(parents=True)
        (self.venv / "lib" / "installed.txt").write_text("the previous packages")

        data = tmp_path / "data"
        (data / "spotify").mkdir(parents=True)
        (data / "settings.json").write_text('{"schema_version": 4}')
        (data / "spotify" / "profiles.json").write_text('{"schema_version": 2}')
        os.chmod(data / "spotify" / "profiles.json", 0o600)
        self.data_files = (
            data / "settings.json", data / "podcast_data.json", data / "spotify" / "profiles.json",
        )

        self.backups = tmp_path / "backups"
        self.state = tmp_path / "state"
        milo = service.programs["milo"]
        milo["git_path"] = str(self.repo)
        milo["backup_path"] = str(self.backups)
        milo["state_path"] = str(self.state)
        milo["data_files"] = self.data_files

        # The sidecar is not served: a signed release's digest is its manifest's.
        body, self.frontend_digest = _frontend_tarball(tmp_path, extra=["assets/index-NEW.js"])
        self.key = make_key(tmp_path / "keys")
        milo["release_signing_key"] = str(self.key.allowed_signers)
        self.frontend_url = milo["frontend_asset_url"].format(tag="v0.2.0")
        self.manifest_url = milo["release_manifest_url"].format(tag="v0.2.0")
        self.assets = ReleaseAssets({self.frontend_url: body})
        self.sign(manifest_for("v0.2.0", self.TARGET, self.frontend_digest))

    def sign(self, manifest: bytes, key=None) -> None:
        """Publish `manifest` as v0.2.0's, signed by `key` (the trusted one)."""
        self.assets.files[self.manifest_url] = manifest
        self.assets.files[f"{self.manifest_url}.sig"] = (key or self.key).sign(manifest)

    # ── what the update did ─────────────────────────────────────────────────

    def spawned(self) -> list[tuple]:
        return [event[1] for event in self.events if event[0] == "spawn"]

    def journal_steps(self) -> list[str]:
        return [event[1] for event in self.events if event[0] == "journal"]

    def index_of(self, predicate) -> int:
        for index, event in enumerate(self.events):
            if event[0] == "spawn" and predicate(event[1]):
                return index
        raise AssertionError(f"never spawned; events were {self.events}")

    def ran(self, predicate) -> bool:
        return any(predicate(argv) for argv in self.spawned())

    def journal(self) -> Optional[dict]:
        path = self.state / self.journal_names.JOURNAL_NAME
        return json.loads(path.read_text()) if path.exists() else None

    # ── the unit's processes ─────────────────────────────────────────────────

    def _answer(self, argv) -> tuple[int, bytes, bytes]:
        program = str(argv[0])
        if program == "tar":
            args = list(argv)
            with tarfile.open(args[args.index("-xzf") + 1]) as tar:
                tar.extractall(args[args.index("-C") + 1], filter="data")
            return 0, b"", b""
        if program == "git":
            assert argv[1:3] == ("-C", str(self.repo)), argv
            command = argv[3:]
            if command == ("rev-parse", "HEAD"):
                return 0, f"{self.ORIGIN}\n".encode(), b""
            if command == ("rev-parse", "v0.2.0^{commit}"):
                return 0, f"{self.TARGET}\n".encode(), b""
            if command == ("show", f"{self.ORIGIN}:requirements.lock"):
                return 0, self.REQUIREMENTS.encode(), b""
            if command == ("show", f"{self.TARGET}:requirements.lock"):
                return 0, self.target_requirements.encode(), b""
            if command == ("status", "--porcelain"):
                return 0, self.porcelain, b""
            if command[0] in ("fetch", "checkout"):
                return 0, b"", b""
        if program == str(self.venv / "bin" / "pip3"):
            (self.venv / "lib" / "installed.txt").write_text("the new packages")
            return 0, b"", b""
        if program == str(self.venv / "bin" / "python") and argv[1:3] in (("-I", "-c"), ("-I", "-m")):
            return 0, b"", b""
        if argv[:3] == ("sudo", "-n", DEPLOY_UPDATE_CMD) and argv[3] in ("sync-system-files", "reboot"):
            return 0, b"", b""
        if argv[:4] in (("sudo", "-n", "systemctl", "start"), ("sudo", "-n", "systemctl", "stop")):
            self.units[argv[4]] = argv[3] == "start"
            return 0, b"", b""
        if argv[:5] == ("sudo", "-n", "systemctl", "restart", "--no-block"):
            return 0, b"", b""
        if argv[:2] == ("systemctl", "is-active"):
            return 0, b"active\n" if self.units.get(argv[2]) else b"inactive\n", b""
        if self.other is not None:
            return self.other(argv)
        raise AssertionError(f"a Milo update spawned something unexpected: {argv}")

    async def _spawn(self, program, *args, **kwargs):
        argv = (program, *args)
        self.events.append(("spawn", argv))
        if program == "ssh-keygen":
            return await _REAL_EXEC(program, *args, **kwargs)
        rc, out, err = self.fail(argv) or self._answer(argv)
        return _make_mock_proc(returncode=rc, stdout=out, stderr=err)

    @contextmanager
    def running(self):
        from backend.core.updates import update as update_module

        real_write = update_module.write_json_durably

        names = self.journal_names
        manifest = self.backups / names.DATA_SNAPSHOT_NAME / names.DATA_MANIFEST_NAME

        def write(path, data):
            if Path(path).name == names.JOURNAL_NAME and self.manifest_at_open is None:
                self.manifest_at_open = manifest.is_file()
            real_write(path, data)
            if Path(path).name == names.JOURNAL_NAME:
                self.events.append(("journal", data[names.KEY_STEP], dict(data)))

        class _Usage:
            free = self.free_bytes

        with ExitStack() as stack:
            stack.enter_context(patch("asyncio.create_subprocess_exec", new=self._spawn))
            stack.enter_context(patch(
                "backend.core.updates.update.aiohttp.ClientSession",
                side_effect=lambda *a, **k: self.assets.session(),
            ))
            stack.enter_context(patch("os.sync", new=lambda: self.events.append(("sync",))))
            stack.enter_context(patch.object(update_module, "write_json_durably", new=write))
            stack.enter_context(patch("shutil.disk_usage", return_value=_Usage()))
            stack.enter_context(patch.object(
                dpkg_lock, "holder", side_effect=lambda paths=None: self.apt_held_by,
            ))
            stack.enter_context(patch.object(dpkg_lock, "POLL_S", 0.001))
            yield self


def _git(*command):
    """Matches a git argv by its subcommand and arguments."""
    return lambda argv: argv[0] == "git" and argv[3:3 + len(command)] == command


def _deploy(subcommand):
    return lambda argv: argv[:4] == ("sudo", "-n", DEPLOY_UPDATE_CMD, subcommand)


def _systemctl(*command):
    return lambda argv: argv[:3 + len(command)] == ("sudo", "-n", "systemctl", *command)


_RESTART_SELF = _systemctl("restart", "--no-block", "milo-backend.service")
_INHIBITOR = PROGRAMS["milo"]["inhibit_service_name"]


class TestTheMiloInstall:
    """What the leaving release does to the unit, read off the processes it
    spawns and the files it leaves.

    One rollback exists and it is not here: `milo-update-guard` restores from
    the journal at the next start. So what this release owes is a journal
    that is true at every instant — each step named before the next one
    starts, and on the card before it is named — and a handover, never a
    repair, when a step fails.
    """

    @pytest.mark.asyncio
    async def test_the_journal_moves_through_every_step_in_the_contract_order(
            self, update_service, tmp_path):
        """The guard decides from the step alone: one recorded early restores a
        release that was never installed, one recorded late keeps a half-built
        one. Each step after the first is written once its work is synced."""
        from backend.core.updates.milo_journal import STEPS, TRIAL_STEP

        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result == {"success": True, "pending": True}
        assert unit.journal_steps() == list(STEPS[:STEPS.index(TRIAL_STEP) + 1])
        for index, event in enumerate(unit.events):
            if event[0] == "journal" and event[1] not in (STEPS[0], TRIAL_STEP):
                assert unit.events[index - 1] == ("sync",), (event, unit.events[index - 1])
        journal = unit.journal()
        assert journal["origin_commit"] == MiloUnit.ORIGIN
        assert journal["target_commit"] == MiloUnit.TARGET
        assert (journal["origin_tag"], journal["target_tag"]) == ("v0.1.0", "v0.2.0")
        assert journal["failure"] is None

    @pytest.mark.asyncio
    async def test_the_origin_is_read_by_hash_before_the_fetch(self, update_service, tmp_path):
        """Tags have been re-cut: after a fetch, `v0.1.0` may name another
        commit than the one this unit runs, and a rollback to the name would
        land somewhere nobody tested."""
        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert unit.index_of(_git("rev-parse", "HEAD")) < unit.index_of(_git("fetch"))
        assert unit.journal()["origin_commit"] == MiloUnit.ORIGIN

    @pytest.mark.asyncio
    async def test_the_install_checks_out_the_tag_the_offer_named(self, update_service, tmp_path):
        """The version is not enough: "0.2.0" is not a ref, `v0.2.0` is — and
        what is checked out is the commit that tag resolves to, by hash, once
        it is the one the signed manifest names. And no branch is ever pulled
        or named: that would install the tip of `main`, which is not what was
        offered and cannot be withheld."""
        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert unit.index_of(_git("rev-parse", "v0.2.0^{commit}")) < unit.index_of(_git("checkout"))
        assert ("git", "-C", str(unit.repo), "checkout", "--force", MiloUnit.TARGET) in unit.spawned()
        flat = [" ".join(map(str, argv)) for argv in unit.spawned()]
        assert not [c for c in flat if " pull" in c or c.endswith(" main") or " main " in c], flat
        assert any("fetch origin --tags --force" in c for c in flat), flat
        assert not [argv for argv in unit.spawned() if str(argv[0]).endswith("npm")]

    @pytest.mark.asyncio
    async def test_the_data_is_copied_before_the_journal_opens(self, update_service, tmp_path):
        """A release that bumps a schema rewrites the file on its first save,
        and the release a rollback brings back refuses it at every start. The
        copy is what the guard puts back — and a file that did not exist is
        recorded as such, so the restore removes what the new release created.
        """
        from backend.core.updates.milo_journal import (
            DATA_MANIFEST_NAME, DATA_SNAPSHOT_NAME, KEY_DATA_COPY, KEY_DATA_FILES, KEY_DATA_PATH,
        )

        unit = MiloUnit(update_service, tmp_path)
        snapshot = unit.backups / DATA_SNAPSHOT_NAME
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert unit.manifest_at_open is True
        entries = json.loads((snapshot / DATA_MANIFEST_NAME).read_text())[KEY_DATA_FILES]
        assert [e[KEY_DATA_PATH] for e in entries] == [str(p) for p in unit.data_files]
        for entry, original in zip(entries, unit.data_files):
            if original.exists():
                copy = snapshot / entry[KEY_DATA_COPY]
                assert copy.read_bytes() == original.read_bytes()
                assert copy.stat().st_mode & 0o777 == original.stat().st_mode & 0o777
            else:
                assert entry[KEY_DATA_COPY] is None

    @pytest.mark.parametrize("target, pip", [
        (MiloUnit.REQUIREMENTS + f"pyudev==0.24.5 \\\n    --hash=sha256:{'c' * 64}\n", True),
        (MiloUnit.REQUIREMENTS.replace("fastapi==0.165.0", "fastapi==0.166.0"), True),
        (MiloUnit.REQUIREMENTS.replace("fastapi==0.165.0", "fastapi==0.164.0"), True),
        (MiloUnit.REQUIREMENTS.split("fastapi==")[0], False),
        (MiloUnit.REQUIREMENTS.replace("--python-version 3.13", "--python-version 3.13 --quiet"), False),
    ], ids=["added", "raised", "lowered", "removed", "comment"])
    @pytest.mark.asyncio
    async def test_pip_runs_only_when_the_lock_pins_something_new(
            self, update_service, tmp_path, target, pip):
        """`pip install -r` never uninstalls, so a release whose lock only drops
        a package or edits a comment needs no pip — and no venv copy for the
        guard. A pin moved either way does: a lower one is a downgrade the venv
        has to make. When pip does run, it reads the lock with every file
        checked against its hash, and the venv it changes was kept first, whole."""
        from backend.core.updates.milo_journal import KEPT_VENV_NAME

        unit = MiloUnit(update_service, tmp_path, target_requirements=target)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        pip_runs = [argv for argv in unit.spawned() if str(argv[0]).endswith("pip3")]
        kept = unit.backups / KEPT_VENV_NAME
        assert unit.journal()["venv_kept"] is pip
        if pip:
            assert pip_runs == [(str(unit.venv / "bin" / "pip3"), "install", "--no-cache-dir",
                                 "--require-hashes", "-r", str(unit.repo / "requirements.lock"))]
            assert (kept / "lib" / "installed.txt").read_text() == "the previous packages"
            assert (unit.venv / "lib" / "installed.txt").read_text() == "the new packages"
        else:
            assert pip_runs == []
            assert not kept.exists()

    @pytest.mark.asyncio
    async def test_the_lock_pip_reads_is_one_the_repo_ships(self, update_service, tmp_path):
        """The path was `backend/requirements.txt`, which has never existed."""
        unit = MiloUnit(update_service, tmp_path, target_requirements=f"pyudev==1 \\\n    --hash=sha256:{'c' * 64}\n")
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        pip = next(argv for argv in unit.spawned() if str(argv[0]).endswith("pip3"))
        assert (REPO_ROOT / Path(pip[-1]).relative_to(unit.repo)).is_file()

    @pytest.mark.asyncio
    async def test_the_new_release_is_imported_and_checked_before_it_is_kept(
            self, update_service, tmp_path):
        """An import that fails at the first start is found by the trial two
        starts later; found here, it is one restart into the guard."""
        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        python = str(unit.venv / "bin" / "python")
        import_check = next(a for a in unit.spawned() if a[:3] == (python, "-I", "-c"))
        assert import_check[-1] == str(unit.repo)
        imports = unit.index_of(lambda a: a == import_check)
        pip_check = unit.index_of(lambda a: a == (python, "-I", "-m", "pip", "check"))
        assert unit.index_of(_git("checkout")) < imports < pip_check \
            < unit.index_of(_deploy("sync-system-files"))

    @pytest.mark.asyncio
    async def test_the_inhibitor_holds_from_the_journal_to_the_reboot(self, update_service, tmp_path):
        """A power key or a logind shutdown during the install is a restore at
        the next boot; the inhibitor refuses them for exactly that window, and
        is released before the reboot it would otherwise block."""
        from backend.core.updates.milo_journal import TRIAL_STEP

        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        start = unit.index_of(_systemctl("start", _INHIBITOR))
        stop = unit.index_of(_systemctl("stop", _INHIBITOR))
        reboot = unit.index_of(_deploy("reboot"))
        opened = unit.events.index(next(e for e in unit.events if e[0] == "journal"))
        trial = unit.events.index(next(e for e in unit.events if e[:2] == ("journal", TRIAL_STEP)))
        assert opened < start < unit.index_of(_git("checkout"))
        assert trial < stop < reboot
        assert unit.events[-1] == ("spawn", ("sudo", "-n", DEPLOY_UPDATE_CMD, "reboot"))

    @pytest.mark.asyncio
    async def test_a_refused_reboot_restarts_the_backend_into_the_trial(self, update_service, tmp_path):
        """The release is installed; its start is what decides now. A restart
        runs the guard's count in this boot rather than waiting for one."""
        unit = MiloUnit(update_service, tmp_path,
                        fail=lambda argv: (1, b"", b"sudo: a password is required")
                        if _deploy("reboot")(argv) else None)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result == {"success": True, "pending": True}
        assert unit.index_of(_deploy("reboot")) < unit.index_of(_RESTART_SELF)
        assert unit.journal()["step"] == "rebooting"

    @pytest.mark.asyncio
    async def test_the_previous_release_chunks_stay_served_until_the_reboot(
            self, update_service, tmp_path):
        """Chunk names are content hashes: a `dist` swapped before pip, the
        sync and the import check left every open UI unable to load a view it
        had not shown yet, for minutes, against the previous backend."""
        served = []

        def observe(argv):
            if str(argv[0]).endswith("pip3") or _deploy("sync-system-files")(argv) \
                    or _deploy("reboot")(argv):
                served.append((
                    (unit.dist / "assets" / "index-OLD.js").is_file(),
                    (unit.dist / "assets" / "index-NEW.js").is_file(),
                ))
            return None

        unit = MiloUnit(update_service, tmp_path, target_requirements="pyudev>=1\n", fail=observe)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert served == [(True, False), (True, False), (False, True)]
        assert not list(unit.dist.parent.glob(".dist-staging-*"))

    @pytest.mark.asyncio
    async def test_the_leaving_release_moves_nothing_outside_the_checkout(self, update_service, tmp_path):
        """The dependency set and the satellites belong to the release that
        arrives, once it has held: moved before the reboot, a rollback of the
        app would leave them ahead of the server that drives them."""
        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        # No dependency was read either: MiloUnit refuses any process a Milō
        # install does not spawn, and the install would have failed on it.
        assert result == {"success": True, "pending": True}
        update_service._satellites.push_client_app_to_fleet.assert_not_awaited()
        assert all(url.startswith("https://github.com/leodurandfr/Milo/") for url in unit.assets.requested)

    @pytest.mark.parametrize("refusal, error", [
        (_git("fetch"), "Git fetch failed"),
        (_git("status"), "Git status failed"),
        (_git("rev-parse", "v0.2.0^{commit}"), "rev-parse"),
    ], ids=["fetch", "status", "unknown-tag"])
    @pytest.mark.asyncio
    async def test_a_failure_before_the_journal_opens_is_a_refusal(
            self, update_service, tmp_path, refusal, error):
        """The fetch writes into `.git` and moves no ref, so the tree is still
        on the release it booted: nothing to restore, no restart, and no
        "pending" — the route reports the refusal like any failed update."""
        unit = MiloUnit(update_service, tmp_path,
                        fail=lambda argv: (128, b"", b"fatal: unable to access") if refusal(argv) else None)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False and "pending" not in result
        assert error in result["error"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_a_dirty_tree_is_a_refusal(self, update_service, tmp_path):
        """`git checkout --force <tag>` over uncommitted work destroys it."""
        unit = MiloUnit(update_service, tmp_path, porcelain=b" M backend/main.py\n")
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert "Local changes" in result["error"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_a_frontend_that_cannot_be_staged_is_refused_before_git_moves(
            self, update_service, tmp_path):
        """The stage reads nothing from the new tree, so it runs first: a 404
        or a bad digest refuses the update instead of rolling one back."""
        unit = MiloUnit(update_service, tmp_path)
        del unit.assets.files[unit.frontend_url]
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert "HTTP 404" in result["error"]
        assert not [argv for argv in unit.spawned() if argv[0] == "git"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_a_card_too_full_for_the_copies_is_refused_first(self, update_service, tmp_path):
        """A copy that fills the card halfway leaves the guard nothing whole to
        put back; refused before anything is downloaded."""
        unit = MiloUnit(update_service, tmp_path, free_bytes=1 << 20)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert "free space" in result["error"]
        assert unit.assets.requested == []
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_apt_held_past_the_wait_is_a_refusal(self, update_service, tmp_path):
        """unattended-upgrades holds dpkg's lock for its whole run. Met after
        the journal opens, a release that adds a package fails in the sync a
        minute later and rolls back through an extra reboot; and the trial's
        reboot would cut the run. So the update waits for it first, and past
        the wait refuses with nothing moved, naming the holder."""
        unit = MiloUnit(update_service, tmp_path)
        unit.apt_held_by = "unattended-upgr (pid 812)"
        with unit.running(), patch("backend.core.updates.update.APT_LOCK_WAIT_S", 0.003):
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False and "pending" not in result
        assert "unattended-upgr (pid 812)" in result["error"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_what_the_last_rollback_set_aside_counts_as_free(self, update_service, tmp_path):
        """A rollback leaves the venv and the frontend it replaced in the
        backup's discard until the next update empties it, which this update
        does before its first copy: counted as taken, the Retry the rollback
        offers would be refused on a card it fits on."""
        from backend.core.updates.milo_journal import DISCARD_NAME

        unit = MiloUnit(update_service, tmp_path, free_bytes=1 << 20)
        (unit.backups / DISCARD_NAME / "venv").mkdir(parents=True)
        with open(unit.backups / DISCARD_NAME / "venv" / "python", "wb") as sparse:
            sparse.truncate(1 << 32)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result == {"success": True, "pending": True}
        assert not (unit.backups / DISCARD_NAME).exists()

    @pytest.mark.parametrize("failing, last_step", [
        (_git("checkout"), "started"),
        (lambda a: str(a[0]).endswith("pip3"), "checked_out"),
        (lambda a: a[1:3] == ("-I", "-c"), "checked_out"),
        (lambda a: a[1:] == ("-I", "-m", "pip", "check"), "checked_out"),
        (_deploy("sync-system-files"), "installed"),
    ], ids=["checkout", "pip", "import", "pip-check", "sync"])
    @pytest.mark.asyncio
    async def test_a_failure_after_the_journal_opens_hands_over_to_the_guard(
            self, update_service, tmp_path, caplog, failing, last_step):
        """No repair in this process: the failure is recorded beside the step
        it stopped at, and the backend restarts into the guard — the same path
        as a crash or a power cut. Nothing past the failure runs."""
        unit = MiloUnit(update_service, tmp_path, target_requirements="pyudev>=1\n",
                        fail=lambda argv: (1, b"", b"Traceback\nModuleNotFoundError: No module named 'x'")
                        if failing(argv) else None)
        with unit.running(), caplog.at_level(logging.WARNING, logger="backend.core.updates.update"):
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False and result["pending"] is True
        journal = unit.journal()
        assert journal["step"] == last_step
        assert "No module named 'x'" in journal["failure"]
        assert "\n" not in journal["failure"]
        assert unit.ran(_RESTART_SELF)
        assert unit.index_of(failing) < unit.index_of(_RESTART_SELF)
        # The restart takes a BindsTo= unit with it and brings it back: left
        # up, logind would refuse every reboot after the guard's rollback.
        assert unit.index_of(_systemctl("stop", _INHIBITOR)) < unit.index_of(_RESTART_SELF)
        assert not unit.units[_INHIBITOR]
        assert not unit.ran(_deploy("reboot"))
        assert (unit.dist / "index.html").read_bytes() == b"the previous release"
        assert update_service.milo_update_state()["phase"] == "restart"
        # Held, the unit would refuse the very restart that ends it, should the
        # one asked for here never come.
        assert not update_service.milo_update_active
        # An error is the backend-error banner, behind the update screen and
        # first in line once it lifts, in place of the localized rollback.
        handed = [r for r in caplog.records if "the update guard puts v0.1.0 back" in r.getMessage()]
        assert [r.levelno for r in handed] == [logging.WARNING]

    @pytest.mark.asyncio
    async def test_a_swap_that_fails_leaves_the_previous_frontend_for_the_guard(
            self, update_service, tmp_path):
        """The disk refuses the new tree once the old one is aside. No restore
        here: the guard puts the kept frontend back whenever the live one is
        missing, and the journal says the install never reached the trial."""
        from backend.core.updates.milo_journal import KEPT_DIST_NAME

        real_rename = os.rename

        def rename(src, dst, *args, **kwargs):
            if Path(src).name == "dist" and Path(src).parent.name == "staged":
                raise OSError(errno.ENOSPC, "No space left on device", str(dst))
            return real_rename(src, dst, *args, **kwargs)

        unit = MiloUnit(update_service, tmp_path)
        with unit.running(), patch("os.rename", new=rename):
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["pending"] is True
        assert unit.journal()["step"] == "synced"
        assert "No space left on device" in unit.journal()["failure"]
        assert not unit.dist.exists()
        assert (unit.backups / KEPT_DIST_NAME / "index.html").read_bytes() == b"the previous release"
        assert not list(unit.dist.parent.glob(".dist-staging-*"))
        assert unit.ran(_RESTART_SELF) and not unit.ran(_deploy("reboot"))

    @pytest.mark.asyncio
    async def test_the_previous_outcome_goes_when_a_journal_opens(self, update_service, tmp_path):
        """It describes the last update until the next one starts; kept across
        it, the row would show a rollback beside an update in progress."""
        from backend.core.updates.milo_journal import ATTEMPTS_NAME, LAST_UPDATE_NAME

        unit = MiloUnit(update_service, tmp_path)
        unit.state.mkdir()
        (unit.state / LAST_UPDATE_NAME).write_text(json.dumps(
            {"from": "v0.0.9", "to": "v0.1.0", "outcome": "rolled_back", "detail": "x", "step": "rebooting"}))
        (unit.state / ATTEMPTS_NAME).write_text("2\n")
        assert update_service.last_update_record()["outcome"] == "rolled_back"

        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert not (unit.state / LAST_UPDATE_NAME).exists()
        assert not (unit.state / ATTEMPTS_NAME).exists()
        assert update_service.last_update_record() is None

    @pytest.mark.asyncio
    async def test_an_open_journal_refuses_every_update(self, update_service, tmp_path):
        """A trial in progress keeps its venv and its frontend in the backup;
        a second Milō update would clear them, and a dependency update would
        restart a unit the trial is counting."""
        from backend.core.updates.milo_journal import JOURNAL_NAME

        unit = MiloUnit(update_service, tmp_path)
        unit.state.mkdir()
        (unit.state / JOURNAL_NAME).write_text(json.dumps({"step": "rebooting"}))

        with unit.running():
            milo = await update_service.update_program("milo")
            navidrome = await update_service.update_program("navidrome")
            can = await update_service.can_update_program("navidrome")

        assert "in progress" in milo["error"] and "in progress" in navidrome["error"]
        assert can["can_update"] is False and "in progress" in can["reason"]
        assert unit.spawned() == []

    @pytest.mark.asyncio
    async def test_the_unit_is_held_while_the_install_runs(self, update_service, tmp_path):
        """A reboot pressed mid-install throws the install away: the process
        running it refuses the power routes, whatever step its journal says."""
        held = []
        unit = MiloUnit(update_service, tmp_path, fail=lambda argv: held.append(
            update_service.milo_update_active) if _git("checkout")(argv) else None)
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert held == [True]

    @pytest.mark.asyncio
    async def test_stale_git_locks_are_removed_before_the_fetch(self, update_service, tmp_path):
        """A fetch cut by a power loss leaves a ref lock, a cut `git status` the
        index lock: the first refused every later press before any journal
        opened, the second failed the checkout after one had, and the guard
        rolled back an update that had changed nothing."""
        unit = MiloUnit(update_service, tmp_path)
        git_dir = unit.repo / ".git"
        stale = [git_dir / "index.lock", git_dir / "packed-refs.lock", git_dir / "refs" / "tags" / "v0.2.0.lock"]
        for lock in stale:
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("")
        locked_at_fetch = []
        unit.fail = lambda argv: locked_at_fetch.append(any(lock.exists() for lock in stale)) if _git("fetch")(argv) else None
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result == {"success": True, "pending": True}
        assert locked_at_fetch == [False]
        assert unit.index_of(_git("status")) < unit.index_of(_git("fetch"))

    @pytest.mark.asyncio
    async def test_a_tree_someone_works_in_keeps_its_locks(self, update_service, tmp_path):
        """The locks are only removed once the tree is found clean: on a checkout
        with local work a git command may be running, and its lock is live."""
        unit = MiloUnit(update_service, tmp_path, porcelain=b" M backend/main.py\n")
        (unit.repo / ".git" / "index.lock").write_text("")
        with unit.running():
            await update_service._update_milo_app(MILO_STATUS)

        assert (unit.repo / ".git" / "index.lock").exists()

    @pytest.mark.asyncio
    async def test_a_journal_the_guard_did_not_close_refuses_a_new_milo_update(self, update_service, tmp_path):
        """It holds nothing, so a restart can still finish it; but what it kept
        aside is its only way back, and a new journal would clear it."""
        unit = MiloUnit(update_service, tmp_path)
        _write_journal(unit.state, **{**TestTheMiloPostCommit.COMMITTED,
                                      "step": "installed", "failure": "pip install failed"})

        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert not update_service.milo_update_active
        assert result["success"] is False and "restarting Milo" in result["error"]
        assert unit.spawned() == []

    # ── the signature ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("unsigned", ["another-key", "no-signature", "no-manifest", "other-tag"])
    @pytest.mark.asyncio
    async def test_a_release_that_does_not_verify_is_refused_before_anything_moves(
            self, update_service, tmp_path, unsigned):
        """The install is where a release reaches root, so it verifies again
        rather than trusting that the offer did: a status can be built by any
        caller, and the offer's verdict is an hour old. Each way a release can
        fail to be one the owner signed is a refusal — no download into the
        tree, no fetch, no journal."""
        unit = MiloUnit(update_service, tmp_path)
        if unsigned == "another-key":
            unit.sign(manifest_for("v0.2.0", MiloUnit.TARGET, unit.frontend_digest),
                      key=make_key(tmp_path / "intruder", name="intruder"))
        elif unsigned == "no-signature":
            del unit.assets.files[f"{unit.manifest_url}.sig"]
        elif unsigned == "no-manifest":
            del unit.assets.files[unit.manifest_url]
        else:
            # The owner's genuine signature of another release's manifest.
            unit.sign(manifest_for("v0.1.9", MiloUnit.TARGET, unit.frontend_digest))

        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert unit.frontend_url not in unit.assets.requested
        assert not [argv for argv in unit.spawned() if argv[0] == "git"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_the_signature_is_verified_before_the_frontend_or_the_tags_are_fetched(
            self, update_service, tmp_path):
        unit = MiloUnit(update_service, tmp_path)
        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result == {"success": True, "pending": True}
        verify = unit.index_of(lambda argv: argv[:3] == ("ssh-keygen", "-Y", "verify"))
        assert verify < unit.index_of(_git("fetch")) < unit.index_of(_git("checkout"))
        requested = unit.assets.requested
        assert requested.index(unit.manifest_url) < requested.index(unit.frontend_url), requested

    @pytest.mark.asyncio
    async def test_a_tag_that_moved_after_it_was_signed_is_refused(self, update_service, tmp_path):
        """The signature names a commit, the channel a tag. A tag re-pointed
        since — by whoever can push one — names a tree nobody signed: refused
        once the fetch shows it, before the journal opens."""
        unit = MiloUnit(update_service, tmp_path)
        unit.sign(manifest_for("v0.2.0", "f" * 40, unit.frontend_digest))

        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert result["success"] is False
        assert MiloUnit.TARGET in result["error"] and "f" * 40 in result["error"]
        self._nothing_moved(unit)

    @pytest.mark.asyncio
    async def test_the_frontend_is_held_to_the_signed_digest_not_to_the_sidecar(
            self, update_service, tmp_path):
        """The `.sha256` beside the asset is whatever the channel served: a
        replaced frontend comes with a matching sidecar. The digest it is held
        to is the one the owner signed."""
        unit = MiloUnit(update_service, tmp_path)
        unit.assets.files[f"{unit.frontend_url}.sha256"] = unit.frontend_digest.encode()
        unit.sign(manifest_for("v0.2.0", MiloUnit.TARGET, "0" * 64))

        with unit.running():
            result = await update_service._update_milo_app(MILO_STATUS)

        assert "checksum mismatch" in result["error"]
        assert f"{unit.frontend_url}.sha256" not in unit.assets.requested
        self._nothing_moved(unit)

    @staticmethod
    def _nothing_moved(unit):
        from backend.core.updates.milo_journal import JOURNAL_NAME

        assert not (unit.state / JOURNAL_NAME).exists()
        assert not unit.ran(_git("checkout"))
        assert not [argv for argv in unit.spawned() if argv[0] == "sudo"]
        assert (unit.dist / "index.html").read_bytes() == b"the previous release"
        assert not list(unit.dist.parent.glob(".dist-staging-*"))


class TestTheImportCheck:
    """The script the install runs in the new release's venv, run for real
    over a tree made for it."""

    @staticmethod
    def _tree(tmp_path, **modules):
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)
        (backend / "__init__.py").write_text("")
        (backend / "tests" / "__init__.py").write_text("")
        (backend / "tests" / "test_x.py").write_text("raise SystemExit('a test was imported')")
        (backend / "main.py").write_text("raise SystemExit('main was run')\n")
        for name, source in modules.items():
            (backend / f"{name}.py").write_text(source)
        return tmp_path

    @staticmethod
    def _run(root):
        import subprocess
        import sys

        from backend.core.updates.update import _IMPORT_CHECK

        return subprocess.run([sys.executable, "-I", "-c", _IMPORT_CHECK, str(root)],
                              capture_output=True, text=True, timeout=60)

    def test_a_sound_tree_passes_without_running_main_or_the_tests(self, tmp_path):
        root = self._tree(tmp_path, sound="import json\n")
        result = self._run(root)
        assert result.returncode == 0, result.stderr

    def test_a_module_that_does_not_import_fails_it(self, tmp_path):
        root = self._tree(tmp_path, broken="import a_module_no_venv_has\n")
        result = self._run(root)
        assert result.returncode != 0
        assert "a_module_no_venv_has" in result.stderr

    def test_a_main_that_does_not_compile_fails_it(self, tmp_path):
        root = self._tree(tmp_path)
        (root / "backend" / "main.py").write_text("def broken(:\n")
        result = self._run(root)
        assert result.returncode != 0
        assert "SyntaxError" in result.stderr


def _write_journal(state, **journal):
    from backend.core.updates.milo_journal import JOURNAL_NAME

    state.mkdir(parents=True, exist_ok=True)
    (state / JOURNAL_NAME).write_text(json.dumps(journal))


class TestTheMiloPostCommit:
    """What the release that arrived finishes once its trial committed.

    The dependency set and the satellites, then the outcome, then the journal
    closed — in that order, so a phase cut short is run again at the next start
    rather than lost. Driven through the real reconciliation: every dependency
    reads as not installed unless `installed` names it, and GitHub is
    unreachable, so an installed one off its pin is reported, not dispatched.
    """

    COMMITTED = {
        "origin_commit": MiloUnit.ORIGIN, "origin_tag": "v0.1.0",
        "target_tag": "v0.2.0", "target_commit": MiloUnit.TARGET,
        "step": "committed", "failure": None, "venv_kept": True,
    }

    @contextmanager
    def _arrived(self, service, tmp_path, *, journal, installed=(), pushed=(), rooms=()):
        """The arriving release's world; the fleet push announces `rooms` and
        answers `pushed` (the rooms left behind), or raises it."""
        def other(argv):
            if any(name in " ".join(map(str, argv)) for name in installed):
                return 0, b"0.0.1\n", b""
            # What a version command over a program the unit does not have
            # meets: no executable (multiroom's two), or a `sh -c` that prints
            # nothing over a file that is not there (the binary paths, below).
            raise FileNotFoundError(argv[0])

        unit = MiloUnit(service, tmp_path, other=other)
        _write_journal(unit.state, **journal)
        progress, complete = [], []

        async def on_progress(state):
            progress.append(state)

        async def on_complete(success):
            complete.append(success)

        async def push(on_push=None):
            unit.events.append(("push",))
            if isinstance(pushed, BaseException):
                raise pushed
            for index, room in enumerate(rooms, 1):
                await on_push(room, index, len(rooms))
            return list(pushed)

        service.set_milo_update_listener(on_progress, on_complete)
        service._satellites.push_client_app_to_fleet = AsyncMock(side_effect=push)
        with unit.running(), github_unreachable(), \
                patch("backend.core.updates.version.os.path.lexists", return_value=False):
            yield unit, progress, complete

    @pytest.mark.asyncio
    async def test_without_a_committed_journal_nothing_runs(self, update_service, tmp_path):
        """The trial has not decided yet (or there was no update): the set and
        the fleet move only behind a release that held."""
        with self._arrived(update_service, tmp_path,
                           journal={**self.COMMITTED, "step": "rebooting"}) as (unit, progress, complete):
            await update_service.run_post_commit()

        update_service._satellites.push_client_app_to_fleet.assert_not_awaited()
        assert unit.spawned() == [] and progress == [] and complete == []
        assert unit.journal()["step"] == "rebooting"

    @pytest.mark.asyncio
    async def test_the_set_then_the_satellites_then_the_outcome(self, update_service, tmp_path):
        from backend.core.updates.milo_journal import ATTEMPTS_NAME, LAST_UPDATE_NAME, OUTCOMES

        with self._arrived(update_service, tmp_path, journal=self.COMMITTED) as (unit, progress, complete):
            (unit.state / ATTEMPTS_NAME).write_text("1\n")
            await update_service.run_post_commit()

        pushed = unit.events.index(("push",))
        assert [e for e in unit.events[:pushed] if e[0] == "spawn"], "no dependency was read before the push"
        assert not [e for e in unit.events[pushed:] if e[0] == "spawn"]
        assert [s["phase"] for s in progress] == ["dependencies", "satellites"]
        assert all((s["from_version"], s["to_version"]) == ("v0.1.0", "v0.2.0") for s in progress)
        record = json.loads((unit.state / LAST_UPDATE_NAME).read_text())
        assert record == {"from": "v0.1.0", "to": "v0.2.0", "outcome": "committed",
                          "detail": None, "step": "committed"}
        assert record["outcome"] in OUTCOMES
        assert unit.journal() is None and not (unit.state / ATTEMPTS_NAME).exists()
        assert complete == [True]
        assert not update_service.milo_update_active
        assert update_service.milo_update_state() is None
        assert update_service.last_update_record() == {
            "from_version": "v0.1.0", "to_version": "v0.2.0", "outcome": "committed",
            "detail": None, "step": "committed", "dismissed": False}

    @pytest.mark.asyncio
    async def test_each_room_is_announced_as_its_push_begins(self, update_service, tmp_path):
        """The banner names the room whose sound is about to cut out; the
        push's own count is the one shown, not a count of the registry."""
        with self._arrived(update_service, tmp_path, journal=self.COMMITTED,
                           rooms=("Canapé", "Bureau")) as (unit, progress, complete):
            await update_service.run_post_commit()

        rooms = [(s["item"], s["index"], s["total"]) for s in progress if s["phase"] == "satellites"]
        assert rooms == [(None, None, None), ("Canapé", 1, 2), ("Bureau", 2, 2)]
        assert all((s["from_version"], s["to_version"]) == ("v0.1.0", "v0.2.0") for s in progress)

    @pytest.mark.asyncio
    async def test_what_stayed_behind_is_named_in_the_outcome(self, update_service, tmp_path):
        """The app committed, so the outcome says committed — with the
        dependency and the room that did not follow, for the row to show."""
        with self._arrived(update_service, tmp_path, journal=self.COMMITTED,
                           installed=("navidrome",), pushed=["Canapé"]) as (unit, progress, complete):
            await update_service.run_post_commit()

        record = update_service.last_update_record()
        assert record["outcome"] == "committed"
        assert "navidrome" in record["detail"] and "Canapé" in record["detail"]
        assert complete == [True]

    @pytest.mark.asyncio
    async def test_a_phase_cut_short_runs_again_at_the_next_start(self, update_service, tmp_path):
        """A restart in the middle of the push cancels it: the journal still
        says committed, and the next start finishes what was left."""
        with self._arrived(update_service, tmp_path, journal=self.COMMITTED,
                           pushed=asyncio.CancelledError()) as (unit, progress, complete):
            with pytest.raises(asyncio.CancelledError):
                await update_service.run_post_commit()
            assert unit.journal()["step"] == "committed"
            assert complete == []
            assert update_service.milo_update_state()["phase"] == "dependencies"

            update_service._satellites.push_client_app_to_fleet = AsyncMock(return_value=[])
            await update_service.run_post_commit()

        assert unit.journal() is None
        assert complete == [True]

    @pytest.mark.asyncio
    async def test_an_outcome_that_cannot_be_written_keeps_the_journal_and_lets_go(
            self, update_service, tmp_path):
        """Closed first, a journal whose outcome then failed to land would
        leave the unit with no record of the update at all, and nothing to run
        again. Kept, it must not hold the unit: nothing in this process retries
        it, and the restart that does would be refused, every client held
        behind the banner, and every update refused, until someone unplugged
        the unit."""
        from backend.core.updates.milo_journal import LAST_UPDATE_NAME

        with self._arrived(update_service, tmp_path, journal=self.COMMITTED) as (unit, progress, complete):
            (unit.state / LAST_UPDATE_NAME / "in-the-way").mkdir(parents=True)
            await update_service.run_post_commit()
            milo = await update_service._update_milo_app(MILO_STATUS)

        assert unit.journal()["step"] == "committed"
        assert complete == [False], "the clients drop the banner"
        assert not update_service.milo_update_active
        assert update_service.milo_update_state() is None
        assert "restarting Milo" in milo["error"]

    @pytest.mark.asyncio
    async def test_a_phase_that_never_finishes_is_given_up_naming_where_it_was_cut(
            self, update_service, tmp_path):
        """A compile that runs the backend out of memory at every start would
        hold the unit for good — the power routes refused, every update refused,
        the banner up — with nothing but SSH to end it. Each run is counted
        before it begins; the start after MAX_ATTEMPTS - 1 cut runs closes the
        journal instead, with where the last one was cut."""
        from backend.core.updates.milo_journal import MAX_ATTEMPTS

        with self._arrived(update_service, tmp_path, journal=self.COMMITTED,
                           rooms=("Canapé",), pushed=asyncio.CancelledError()) as (unit, progress, complete):
            for run in range(1, MAX_ATTEMPTS):
                with pytest.raises(asyncio.CancelledError):
                    await update_service.run_post_commit()
                assert unit.journal()["post_commit_runs"] == run
            update_service._satellites.push_client_app_to_fleet.reset_mock()

            await update_service.run_post_commit()

        update_service._satellites.push_client_app_to_fleet.assert_not_awaited()
        assert unit.journal() is None
        record = update_service.last_update_record()
        assert record["outcome"] == "committed"
        assert f"cut {MAX_ATTEMPTS - 1} times" in record["detail"]
        assert "satellites" in record["detail"]
        assert complete == [True]
        assert not update_service.milo_update_active

    @pytest.mark.asyncio
    async def test_the_last_item_reached_is_kept_for_a_phase_given_up(self, update_service, tmp_path):
        """The detail of a phase given up names the dependency or the room it
        was installing, which is the one that killed it."""
        async def killed_at_the_room(on_push=None):
            await on_push("Canapé", 1, 1)
            raise asyncio.CancelledError()

        with self._arrived(update_service, tmp_path, journal=self.COMMITTED) as (unit, progress, complete):
            update_service._satellites.push_client_app_to_fleet = AsyncMock(side_effect=killed_at_the_room)
            with pytest.raises(asyncio.CancelledError):
                await update_service.run_post_commit()

        assert unit.journal()["post_commit_at"] == "satellites (Canapé)"


class TestAStaleShutdownInhibitor:
    """The inhibitor a backend that died mid-install left behind, found by the
    next one once it listens: only the process running an install holds it, and
    one just started runs none."""

    @staticmethod
    def _service(settings_service, state):
        systemd = Mock()
        systemd.unit_state = AsyncMock(return_value=state)
        systemd.stop = AsyncMock(return_value=True)
        service = UpdateService(systemd_manager=systemd, satellite_update_service=Mock(),
                                settings_service=settings_service)
        return service, systemd

    @pytest.mark.parametrize("state", [("active", "success"), ("activating", "success"), None],
                             ids=["active", "activating", "unreadable"])
    @pytest.mark.asyncio
    async def test_one_left_up_is_stopped(self, mock_settings_service, state):
        """Left up, it keeps the power key dead, and logind refusing every
        reboot the 409s no longer refuse, until its RuntimeMaxSec."""
        service, systemd = self._service(mock_settings_service, state)

        await service.release_stale_inhibitor()

        systemd.stop.assert_awaited_once_with(_INHIBITOR)

    @pytest.mark.parametrize("state", [("inactive", "success"), ("failed", "signal")])
    @pytest.mark.asyncio
    async def test_one_already_down_costs_no_stop(self, mock_settings_service, state):
        service, systemd = self._service(mock_settings_service, state)

        await service.release_stale_inhibitor()

        systemd.stop.assert_not_awaited()


class TestTheMiloUpdateState:
    """What a process that did not start the update reads back from disk."""

    @pytest.mark.parametrize("step, phase, holds", [
        ("rebooting", "verify", True), ("committed", "dependencies", True), ("swapped", "restart", False),
    ])
    def test_the_phase_is_rebuilt_from_the_journal(self, update_service, tmp_path, step, phase, holds):
        """A journal the guard is to put back holds nothing: a restart is what
        finishes it, and held, it would refuse that restart."""
        state = tmp_path / "state"
        update_service.programs["milo"]["state_path"] = str(state)
        _write_journal(state, **{**TestTheMiloPostCommit.COMMITTED, "step": step})

        assert update_service.milo_update_active is holds
        assert update_service.milo_update_state() == {
            "phase": phase, "from_version": "v0.1.0", "to_version": "v0.2.0",
            "item": None, "index": None, "total": None,
        }

    def test_no_journal_is_no_update(self, update_service, tmp_path):
        update_service.programs["milo"]["state_path"] = str(tmp_path / "state")
        assert not update_service.milo_update_active
        assert update_service.milo_update_state() is None
        assert update_service.last_update_record() is None

    def test_a_rollback_is_read_back_with_the_rest_api_names(self, update_service, tmp_path):
        """The file keeps the guard's keys; the API names the versions."""
        from backend.core.updates.milo_journal import LAST_UPDATE_NAME

        state = tmp_path / "state"
        state.mkdir()
        update_service.programs["milo"]["state_path"] = str(state)
        (state / LAST_UPDATE_NAME).write_text(json.dumps({
            "from": "v0.1.0", "to": "v0.2.0", "outcome": "rolled_back",
            "detail": "ModuleNotFoundError: No module named 'x'", "step": "rebooting",
        }))

        assert update_service.last_update_record() == {
            "from_version": "v0.1.0", "to_version": "v0.2.0", "outcome": "rolled_back",
            "detail": "ModuleNotFoundError: No module named 'x'", "step": "rebooting",
            "dismissed": False,
        }

    @staticmethod
    def _rolled_back(state):
        from backend.core.updates.milo_journal import LAST_UPDATE_NAME

        state.mkdir(exist_ok=True)
        (state / LAST_UPDATE_NAME).write_text(json.dumps({
            "from": "v0.1.0", "to": "v0.2.0", "outcome": "rolled_back",
            "detail": "ModuleNotFoundError: No module named 'x'", "step": "rebooting",
        }))
        return state / LAST_UPDATE_NAME

    @pytest.mark.asyncio
    async def test_a_closed_banner_stays_closed_for_every_screen(
        self, update_service, tmp_path, mock_settings_service,
    ):
        """Kept in a browser, the dismissal was lost at every kiosk restart (a
        private profile), and the same rollback came back after each one. In
        the record, the next process reads it too; the outcome itself stays,
        since the Milō row still describes it."""
        state = tmp_path / "state"
        path = self._rolled_back(state)
        update_service.programs["milo"]["state_path"] = str(state)

        assert await update_service.dismiss_last_update() is True
        assert await update_service.dismiss_last_update() is False

        restarted = UpdateService(systemd_manager=Mock(), satellite_update_service=Mock(),
                                  settings_service=mock_settings_service)
        restarted.programs["milo"]["state_path"] = str(state)
        record = restarted.last_update_record()
        assert record["dismissed"] is True
        assert (record["outcome"], record["to_version"]) == ("rolled_back", "v0.2.0")
        assert json.loads(path.read_text())["outcome"] == "rolled_back"

    @pytest.mark.asyncio
    async def test_nothing_is_closed_while_an_update_holds_its_journal(self, update_service, tmp_path):
        """An update removes the previous outcome when its journal opens:
        written back meanwhile, that outcome would outlive the update that
        replaced it."""
        state = tmp_path / "state"
        path = self._rolled_back(state)
        _write_journal(state, **{**TestTheMiloPostCommit.COMMITTED, "step": "rebooting"})
        update_service.programs["milo"]["state_path"] = str(state)
        before = path.read_text()

        assert await update_service.dismiss_last_update() is False
        assert path.read_text() == before


class TestATrialThatCannotBeCommitted:
    """A guard that cannot write the journal (a card remounted read-only, a
    full card) fails every commit the same way. Held, the trial kept the
    update screen up and refused the reboot and the power-off — the first
    useful gestures on a sick card — until the unit was unplugged."""

    @staticmethod
    def _on_trial(update_service, tmp_path):
        state = tmp_path / "state"
        update_service.programs["milo"]["state_path"] = str(state)
        _write_journal(state, **{**TestTheMiloPostCommit.COMMITTED, "step": "rebooting"})
        completed = []

        async def on_complete(success):
            completed.append(success)

        update_service.set_milo_update_listener(AsyncMock(), on_complete)
        return state, completed

    @pytest.mark.asyncio
    async def test_letting_go_releases_the_unit_and_says_so_once(self, update_service, tmp_path, caplog):
        state, completed = self._on_trial(update_service, tmp_path)
        assert update_service.milo_update_active
        journal = (state / "journal.json").read_bytes()

        with caplog.at_level(logging.WARNING):
            await update_service.let_go_of_the_trial("5 commits failed, the last: OSError: Read-only file system")

        assert not update_service.milo_update_active, "the reboot and the power-off are still refused"
        assert update_service.milo_update_state() is None, "the update screen is still drawn"
        assert completed == [False], "no screen was told the update screen lifts"
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert update_service.milo_update_holds_installs, "an install may start over the open trial"
        assert (state / "journal.json").read_bytes() == journal, "the trial's journal is the next start's"

    @pytest.mark.asyncio
    async def test_the_next_start_takes_the_trial_up_again(self, update_service, tmp_path, mock_settings_service):
        """Let go of in memory only: the journal is the next process's, which
        holds the unit through the trial again and commits it once it holds."""
        state, _ = self._on_trial(update_service, tmp_path)
        await update_service.let_go_of_the_trial("the guard failed")

        restarted = UpdateService(systemd_manager=Mock(), satellite_update_service=Mock(),
                                  settings_service=mock_settings_service)
        restarted.programs["milo"]["state_path"] = str(state)
        assert restarted.milo_update_active
        assert restarted.milo_update_state()["phase"] == "verify"

    @pytest.mark.asyncio
    async def test_nothing_is_let_go_of_past_the_trial(self, update_service, tmp_path, caplog):
        """A commit that landed meanwhile (another attempt, the guard's own
        start) is a post-commit phase to run, not a trial to give up."""
        state = tmp_path / "state"
        update_service.programs["milo"]["state_path"] = str(state)
        _write_journal(state, **TestTheMiloPostCommit.COMMITTED)

        with caplog.at_level(logging.ERROR):
            let_go = await update_service.let_go_of_the_trial("the guard failed")

        assert not let_go
        assert update_service.milo_update_active
        assert not caplog.records

    @pytest.mark.asyncio
    async def test_a_commit_the_guard_wrote_meanwhile_is_read_off_the_card(self, update_service, tmp_path):
        """The guard writes the journal from another process: a trial read
        before its commit landed is not one to let go of."""
        state, completed = self._on_trial(update_service, tmp_path)
        assert update_service.milo_update_state()["phase"] == "verify"
        _write_journal(state, **TestTheMiloPostCommit.COMMITTED)

        assert not await update_service.let_go_of_the_trial("the guard failed")
        assert update_service.milo_update_state()["phase"] == "dependencies"
        assert completed == []


class TestAJournalTheGuardDidNotClose:
    """A backend that starts over a journal its guard should have put back —
    the guard failed, or could not read the tree — finds it once it listens.
    Left so, the unit shows the update screen for good and nothing acts on the
    journal again."""

    @staticmethod
    def _service(settings_service, tmp_path, monkeypatch, journal):
        from backend.core.updates import update as update_module

        monkeypatch.setattr(update_module, "BOOT_ID_PATH", str(tmp_path / "boot_id"))
        (tmp_path / "boot_id").write_text("boot-1\n")
        systemd = Mock()
        systemd.restart_self = AsyncMock()
        service = UpdateService(systemd_manager=systemd, satellite_update_service=Mock(),
                                settings_service=settings_service)
        service.programs["milo"]["state_path"] = str(tmp_path / "state")
        _write_journal(tmp_path / "state", **{**TestTheMiloPostCommit.COMMITTED, **journal})
        complete = []

        async def on_complete(success):
            complete.append(success)

        service.set_milo_update_listener(AsyncMock(), on_complete)
        return service, systemd, complete

    @pytest.mark.parametrize("journal", [
        {"step": "synced"}, {"step": "rebooting", "failure": "pip check failed"},
    ], ids=["before-the-trial", "failure-recorded"])
    @pytest.mark.asyncio
    async def test_it_restarts_once_so_the_guard_runs_again(
            self, mock_settings_service, tmp_path, monkeypatch, journal):
        service, systemd, complete = self._service(mock_settings_service, tmp_path, monkeypatch, journal)

        await service.hand_stranded_journal_to_the_guard()

        systemd.restart_self.assert_awaited_once_with(PROGRAMS["milo"]["service_name"])
        assert service.milo_update_state()["phase"] == "restart"
        assert complete == []

    @pytest.mark.asyncio
    async def test_found_again_in_the_same_boot_it_is_let_go_of(self, mock_settings_service, tmp_path, monkeypatch):
        """A guard that fails the same way at every start must not become a
        restart loop: the second backend of the boot leaves the unit usable —
        the screen lifted, the power routes answering — and says so."""
        service, systemd, _ = self._service(mock_settings_service, tmp_path, monkeypatch, {"step": "synced"})
        await service.hand_stranded_journal_to_the_guard()
        left = json.loads((tmp_path / "state" / "journal.json").read_text())
        again, systemd_again, complete = self._service(mock_settings_service, tmp_path, monkeypatch, left)

        await again.hand_stranded_journal_to_the_guard()

        systemd_again.restart_self.assert_not_awaited()
        assert again.milo_update_state() is None
        assert not again.milo_update_active
        assert complete == [False]
        assert (tmp_path / "state" / "journal.json").exists(), "the next boot's guard still has it"

    @pytest.mark.parametrize("step", ["rebooting", "committed"])
    @pytest.mark.asyncio
    async def test_a_trial_or_a_committed_update_is_left_to_its_phase(
            self, mock_settings_service, tmp_path, monkeypatch, step):
        service, systemd, complete = self._service(mock_settings_service, tmp_path, monkeypatch, {"step": step})

        await service.hand_stranded_journal_to_the_guard()

        systemd.restart_self.assert_not_awaited()
        assert complete == []


class _SystemdWorld:
    """systemd as the update flows drive it: units that hold a state, and the
    calls made on them, in order.

    Injected as `systemd_manager`, the collaborator every flow reaches systemd
    through. A flow that decides what to stop and restart from a reading taken
    minutes earlier is only visible against a world that keeps moving between
    the reading and the act: a receiver selected during a compile, a source
    started during a download. `requires` is the one unit relation the flows
    depend on — stopping nqptp stops milo-airplay (`Requires=`).
    """

    def __init__(self, active=None, *, unreadable=(),
                 requires=(("milo-airplay.service", "nqptp.service"),)):
        self.active = dict(active or {})
        self.unreadable = set(unreadable)
        self.requires = dict(requires)
        self.calls: list[tuple[str, ...]] = []

    async def probe_active(self, unit):
        self.calls.append(("probe_active", unit))
        if unit in self.unreadable:
            return None
        return self.active.get(unit, False)

    async def is_active(self, unit):
        return await self.probe_active(unit) is True

    async def stop(self, unit):
        self.calls.append(("stop", unit))
        self.active[unit] = False
        for dependent, required in self.requires.items():
            if required == unit:
                self.active[dependent] = False
        return True

    async def start(self, unit):
        self.calls.append(("start", unit))
        if unit in self.requires:
            self.active[self.requires[unit]] = True
        self.active[unit] = True
        return True

    def acts(self):
        """What was done to the units, without the readings."""
        return [call for call in self.calls if call[0] != "probe_active"]


def _deploy_into(world):
    """Stand in for the deploy wrapper, recording each subcommand beside the
    systemd calls so their order is one list."""
    async def run_deploy(*args, timeout=120):
        world.calls.append(("deploy", *args))
        return True, ""
    return run_deploy


class TestTheUnitStateIsReadWhenItIsActedOn:
    """Every install flow decides what to stop, restart and roll back from the
    unit's state at the moment it acts on it, never from a reading taken before
    minutes of download, compile or pip — during which the owner can select the
    very source being updated. A stale reading left AirPlay, Spotify or Navidrome
    stopped, and a probe that could not answer read as "inactive", often under
    a success.
    """

    BINARY_STATUS = {"installed": {"versions": {"main": "0.6.1"}}, "latest": {"version": "0.7.0"}}
    SHAIRPORT_STATUS = {
        "installed": {"versions": {"main": "5.6.1"}},
        "latest": {"version": "5.6.2", "tag_name": "5.6.2"},
    }
    MULTIROOM_STATUS = {"installed": {"versions": {"main": "0.27.0"}}, "latest": {"version": "0.28.0"}}

    @staticmethod
    def _service(world, settings):
        satellites = Mock()
        satellites.push_client_app_to_fleet = AsyncMock(return_value=[])
        settings._storage["updates.forced_versions"] = {}
        return UpdateService(systemd_manager=world, satellite_update_service=satellites,
                             settings_service=settings)

    @pytest.mark.asyncio
    async def test_a_receiver_selected_during_the_compile_is_running_after_the_update(
            self, mock_settings_service):
        """Spotify plays, AirPlay is off; during `make` the owner selects AirPlay
        and an iPhone plays to it. The NQPTP swap stops milo-airplay through
        `Requires=`, so it must have been stopped on purpose first and started
        again after — not left down under `success: True`."""
        airplay = PROGRAMS["shairport-sync"]["service_name"]
        nqptp = PROGRAMS["shairport-sync"]["nqptp"]
        world = _SystemdWorld()
        service = self._service(world, mock_settings_service)

        async def compile_while_airplay_is_selected(source_dir):
            world.active[airplay] = True
            world.active[nqptp["service_name"]] = True
            return {"success": True}

        build = {"binary": "/tmp/sps/nqptp/nqptp", "version": "1.2.9"}
        with patch.object(service, "_backup_shairport_sync", AsyncMock(return_value={"success": True})), \
                patch.object(service, "_download_source", AsyncMock(return_value={
                    "success": True, "temp_dir": "/tmp/sps", "source_dir": "/tmp/sps/src"})), \
                patch.object(service, "_prepare_nqptp", AsyncMock(return_value={
                    "success": True, "build": build, "expected": ("1.2.9", 11)})), \
                patch.object(service, "_configure_source", AsyncMock(return_value={"success": True})), \
                patch.object(service, "_compile_source", side_effect=compile_while_airplay_is_selected), \
                patch.object(service, "_run_deploy", side_effect=_deploy_into(world)), \
                patch.object(service, "_install_shairport_sync", AsyncMock(return_value={"success": True})), \
                patch.object(service, "_installed_nqptp", AsyncMock(return_value=("1.2.9", 11))), \
                patch.object(service, "_cleanup_temp_files", AsyncMock()), \
                patch("backend.core.updates.update.Path.exists", return_value=True), \
                patch("backend.core.updates.update.aiofiles.open", new=_recording_aiofiles_open({})), \
                patch("backend.core.updates.update.asyncio.sleep", AsyncMock()):
            result = await service._update_shairport_sync(self.SHAIRPORT_STATUS)

        assert result == {"success": True}
        assert world.active[airplay] is True
        acts = world.acts()
        swap = acts.index(("deploy", "install-binary", build["binary"], nqptp["binary_path"]))
        assert ("stop", airplay) in acts[:swap]
        assert ("start", airplay) in acts[swap:]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", ["navidrome", "go-librespot"])
    async def test_a_service_started_during_the_download_is_restarted_onto_the_new_binary(
            self, mock_settings_service, program_key):
        """The dock enables Music Library, or Spotify is selected, while the
        release downloads. The binary must not be swapped under the running
        process, and the unit must end up running the new one."""
        world = _SystemdWorld()
        service = self._service(world, mock_settings_service)
        config = service.programs[program_key]
        unit = config["service_name"]

        async def download_while_the_unit_starts(cfg, version):
            world.active[unit] = True
            return {"success": True, "archive_path": "/tmp/dl/bin.tar.gz", "member": "bin",
                    "sha256": None, "temp_dir": "/tmp/dl"}

        with patch.object(service, "_backup_binary_program", AsyncMock(return_value={"success": True})), \
                patch.object(service, "_download_binary_program", side_effect=download_while_the_unit_starts), \
                patch.object(service, "_run_deploy", side_effect=_deploy_into(world)), \
                patch.object(service, "_rollback_binary_program", AsyncMock(return_value=True)) as rollback, \
                patch.object(service, "_cleanup_temp_files", AsyncMock()), \
                patch("backend.core.updates.update.Path.exists", return_value=True):
            result = await service._update_binary_program(program_key, self.BINARY_STATUS)

        assert result == {"success": True}
        assert world.acts() == [
            ("stop", unit),
            ("deploy", "install-release-binary", "/tmp/dl/bin.tar.gz", "bin", config["binary_path"], "-"),
            ("start", unit),
        ]
        rollback.assert_not_awaited()
        assert world.active[unit] is True

    @pytest.mark.asyncio
    async def test_an_unreadable_service_state_aborts_before_touching_the_binary(
            self, mock_settings_service):
        """A probe that timed out is not "inactive": swapping on that belief
        writes under a process that may be running and skips its restart."""
        unit = PROGRAMS["go-librespot"]["service_name"]
        world = _SystemdWorld(unreadable={unit})
        service = self._service(world, mock_settings_service)

        with patch.object(service, "_backup_binary_program", AsyncMock(return_value={"success": True})), \
                patch.object(service, "_download_binary_program", AsyncMock(return_value={
                    "success": True, "archive_path": "/tmp/dl/bin.tar.gz", "member": "bin",
                    "sha256": None, "temp_dir": "/tmp/dl"})), \
                patch.object(service, "_run_deploy", side_effect=_deploy_into(world)), \
                patch.object(service, "_cleanup_temp_files", AsyncMock()) as cleanup:
            result = await service._update_binary_program("go-librespot", self.BINARY_STATUS)

        assert result["success"] is False
        assert unit in result["error"]
        assert world.acts() == []
        cleanup.assert_awaited_with("/tmp/dl")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("program_key", ["go-librespot", "shairport-sync", "qobuz-proxy"])
    async def test_a_rollback_restarts_a_unit_that_was_running_when_it_began(
            self, mock_settings_service, tmp_path, program_key):
        """The caller's flag says the unit was off when the update stopped
        nothing; someone selected it since. The rollback stops it to put the old
        version back, and must start it again rather than leave it down."""
        world = _SystemdWorld()
        service = self._service(world, mock_settings_service)
        config = {**service.programs[program_key], "backup_path": str(tmp_path)}
        unit = config["service_name"]
        world.active[unit] = True

        with patch.object(service, "_run_deploy", side_effect=_deploy_into(world)), \
                patch.object(service, "_run_local", LocalCommands()):
            if program_key == "qobuz-proxy":
                (tmp_path / "venv").mkdir()
                rolled_back = await service._rollback_qobuz_venv(config, service_was_active=False)
            elif program_key == "shairport-sync":
                (tmp_path / "shairport-sync.backup").write_bytes(b"old")
                rolled_back = await service._rollback_shairport_sync(config, service_was_active=False)
            else:
                (tmp_path / "go-librespot.backup").write_bytes(b"old")
                rolled_back = await service._rollback_binary_program(config, restart_service=False)

        assert rolled_back is True
        assert world.active[unit] is True
        assert world.acts()[-1] == ("start", unit)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multiroom_enabled", [True, False])
    @pytest.mark.parametrize("reading", [None, False], ids=["unreadable", "activating"])
    async def test_a_multiroom_update_restarts_the_units_whenever_multiroom_is_on(
            self, mock_settings_service, multiroom_enabled, reading):
        """The snapcast units run exactly while multiroom is on — the setting is
        what `_sync_snapcast_state` reconciles them to, and nothing reconciles
        them after an update. A probe that timed out, or a unit caught
        `activating`, must not leave every room mute; and in direct mode they
        stay stopped whatever they read, or snapclient holds hw:Loopback."""
        units = PROGRAMS["multiroom"]["services"]
        world = _SystemdWorld()
        if reading is None:
            world.unreadable.update(units)
        mock_settings_service._storage["routing.multiroom_enabled"] = multiroom_enabled
        service = self._service(world, mock_settings_service)

        with patch.object(service, "_download_snapcast_component", AsyncMock(return_value={
                    "success": True, "deb_path": "/tmp/pkg.deb", "sha256": None, "temp_dir": "/tmp/dl"})), \
                patch.object(service, "_install_deb_package", AsyncMock(return_value={"success": True})), \
                patch.object(service, "get_installed_version", AsyncMock(return_value={"versions": {
                    unit: self.MULTIROOM_STATUS["latest"]["version"] for unit in ("snapserver", "snapclient")}})), \
                patch.object(service, "_cleanup_temp_files", AsyncMock()):
            result = await service._update_multiroom(self.MULTIROOM_STATUS)

        assert result == {"success": True}
        started = [call[1] for call in world.acts() if call[0] == "start"]
        assert started == (list(units) if multiroom_enabled else [])
