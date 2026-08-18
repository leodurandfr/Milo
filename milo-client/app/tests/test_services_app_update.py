"""
Unit tests for AppUpdateService's app/ tree swap.

The satellite has no console and no log surface in the UI: the API these files
serve is the only way to repair it remotely. A sync that fails after it has
already started overwriting the live tree therefore takes the repair path down
with it, and the unit needs a hand on the machine.
"""
import logging
import os
import tarfile
import shutil

import pytest
from unittest.mock import AsyncMock, patch

import services.app_update as app_update_module
from services.app_update import AppUpdateService


@pytest.fixture(autouse=True)
def restore_cwd():
    """_sync_app_files re-anchors the process cwd (the unit's WorkingDirectory
    is the directory it renames), so the test process must be put back."""
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


@pytest.fixture
def repo_dir(tmp_path):
    """A repo dir holding the live app/, as it is on a satellite."""
    live = tmp_path / "app"
    (live / "routes").mkdir(parents=True)
    (live / "main.py").write_text("live main\n")
    (live / "routes" / "health.py").write_text("live health\n")
    (live / "gone.py").write_text("dropped in the new release\n")

    with patch.object(app_update_module, "REPO_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def incoming(tmp_path):
    """The app/ tree extracted from the update tarball."""
    new = tmp_path / "extracted" / "app"
    (new / "routes").mkdir(parents=True)
    (new / "main.py").write_text("new main\n")
    (new / "routes" / "health.py").write_text("new health\n")
    (new / "added.py").write_text("new in this release\n")
    (new / "__pycache__").mkdir()
    (new / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00")
    return new


class TestSyncAppFiles:
    """What the live app/ tree looks like after a sync, and after a failed one."""

    @pytest.mark.asyncio
    async def test_the_new_release_replaces_the_old_one(self, repo_dir, incoming):
        """Sanity floor for the failure test below: the swap must really swap."""
        await AppUpdateService()._sync_app_files(incoming)

        live = repo_dir / "app"
        assert (live / "main.py").read_text() == "new main\n"
        assert (live / "routes" / "health.py").read_text() == "new health\n"
        assert (live / "added.py").is_file()
        assert not (live / "gone.py").exists(), "a file dropped upstream must not survive"
        assert not (live / "__pycache__").exists(), "stale bytecode is not part of the release"

    @pytest.mark.asyncio
    async def test_the_tree_it_replaced_is_kept_for_the_rollback(self, repo_dir, incoming):
        """pip and the version write still come after this, and both can fail."""
        await AppUpdateService()._sync_app_files(incoming)

        assert not (repo_dir / "app.new").exists(), "the staging copy is consumed by the swap"
        assert (repo_dir / "app.old" / "main.py").read_text() == "live main\n"

    @pytest.mark.asyncio
    async def test_a_failed_copy_leaves_the_running_app_untouched(self, repo_dir, incoming):
        """The defect this replaces: the old sync deleted the live tree first, so a
        copy that died halfway left a satellite serving a half-written app."""
        real_copytree = shutil.copytree
        calls = []

        def _die_midway(*args, **kwargs):
            # Fail on the recursion into routes/, so the copy dies with a
            # partially written staging tree rather than before it started.
            calls.append(args)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return real_copytree(*args, **kwargs)

        with patch.object(app_update_module.shutil, "copytree", _die_midway):
            with pytest.raises(OSError):
                await AppUpdateService()._sync_app_files(incoming)

        live = repo_dir / "app"
        assert (live / "main.py").read_text() == "live main\n"
        assert (live / "routes" / "health.py").read_text() == "live health\n"
        assert (live / "gone.py").is_file()

    @pytest.mark.asyncio
    async def test_a_staging_dir_from_a_killed_run_is_not_reused(self, repo_dir, incoming):
        """Leftovers are stale, never a partial release to build on."""
        stale = repo_dir / "app.new"
        stale.mkdir()
        (stale / "junk.py").write_text("from a run that died\n")

        await AppUpdateService()._sync_app_files(incoming)

        assert not (repo_dir / "app" / "junk.py").exists()


def _make_tarball(tmp_path, app_files: dict) -> str:
    """Builds an update tarball holding only milo-client/app/, as the server sends it.

    No system/ or rootfs/ entries: the deploy-script leg needs sudo, and the
    failure these tests cover is upstream of it.
    """
    staged = tmp_path / "tarball-src" / "milo-client" / "app"
    staged.mkdir(parents=True)
    for name, body in app_files.items():
        (staged / name).write_text(body)

    tarball = tmp_path / "update.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(str(staged.parent), arcname="milo-client")
    return str(tarball)


class TestDeployUpdateDependencyFailure:
    """What a satellite is left running when `pip install` fails.

    The satellite restarts into whatever `app/` holds two seconds after it
    answers, and `Restart=always` then retries it forever. A tree whose new
    dependencies were never installed therefore crashloops on an appliance whose
    only repair path is port 8001 — the port that tree serves. So the update has
    to fail *before* the version is written and the restart is scheduled, and the
    tree it replaced has to still be there to go back to.
    """

    @pytest.fixture
    def version_file(self, tmp_path):
        f = tmp_path / "app-version"
        f.write_text("v0.1.0-old")
        with patch.object(app_update_module, "VERSION_FILE", f):
            yield f

    @pytest.fixture
    def failing_pip(self):
        """Stands in for pip itself — the outside world, not a method of ours."""
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(
            return_value=(b"", b"ERROR: No matching distribution found for aiohttp")
        )
        with patch.object(app_update_module.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=proc)) as spawn:
            yield spawn

    @pytest.mark.asyncio
    async def test_a_failed_pip_install_fails_the_update(
        self, repo_dir, tmp_path, version_file, failing_pip
    ):
        """A warning here is a satellite that reports success and crashloops."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        result = await AppUpdateService().deploy_update(tarball, "v0.1.0-new")

        assert result["success"] is False
        assert "aiohttp" in result["error"], "the pip failure must reach the server verbatim"

    @pytest.mark.asyncio
    async def test_the_version_is_not_written_when_the_deps_are_missing(
        self, repo_dir, tmp_path, version_file, failing_pip
    ):
        """The version file is what the server polls to call the update done."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        await AppUpdateService().deploy_update(tarball, "v0.1.0-new")

        assert version_file.read_text() == "v0.1.0-old"

    @pytest.mark.asyncio
    async def test_the_previous_tree_is_put_back(
        self, repo_dir, tmp_path, version_file, failing_pip
    ):
        """The window this closes: app.old was deleted inside the swap, so by the
        time pip ran there was nothing left to go back to."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        await AppUpdateService().deploy_update(tarball, "v0.1.0-new")

        live = repo_dir / "app"
        assert (live / "main.py").read_text() == "live main\n"
        assert (live / "routes" / "health.py").read_text() == "live health\n"
        assert not (repo_dir / "app.old").exists(), "the restore must not leave a copy behind"
        assert not (repo_dir / "app.new").exists()


class TestDeployUpdateSuccess:
    """The committed path, and the floor the failure tests above stand on."""

    @pytest.fixture
    def version_file(self, tmp_path):
        f = tmp_path / "app-version"
        f.write_text("v0.1.0-old")
        with patch.object(app_update_module, "VERSION_FILE", f):
            yield f

    @pytest.fixture
    def working_pip(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch.object(app_update_module.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=proc)):
            yield

    @pytest.mark.asyncio
    async def test_an_applied_update_leaves_no_copy_behind(
        self, repo_dir, tmp_path, version_file, working_pip
    ):
        """Both staging dirs are one app/ each; keeping either doubles the
        satellite's disk use per update, on a device with no one watching it."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        result = await AppUpdateService().deploy_update(tarball, "v0.1.0-new")

        assert result["success"] is True
        assert (repo_dir / "app" / "main.py").read_text() == "new main\n"
        assert version_file.read_text() == "v0.1.0-new"
        assert not (repo_dir / "app.old").exists()
        assert not (repo_dir / "app.new").exists()


class TestRestartOutcome:
    """Which systemctl exits mean the restart did not happen.

    A restart that works kills this process, and systemd kills the whole cgroup
    — the `systemctl` child included — so signalled death is the success case.
    Measured on canapé: -15 with an empty stderr, 150 ms in. Treating any
    non-zero code as a failure logged an ERROR on every applied update, which on
    a satellite means the one log surface it has cries wolf.
    """

    async def _restart_with(self, returncode, caplog):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch.object(app_update_module.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=proc)):
            with caplog.at_level(logging.ERROR):
                await AppUpdateService()._restart_service()
        return caplog.records

    @pytest.mark.asyncio
    async def test_a_restart_that_killed_us_is_not_a_failure(self, caplog):
        assert await self._restart_with(-15, caplog) == []

    @pytest.mark.asyncio
    async def test_a_systemctl_that_refused_is(self, caplog):
        """A masked unit, or one the deploy step just made invalid."""
        records = await self._restart_with(1, caplog)

        assert len(records) == 1
        assert "Exit code 1" in records[0].message
