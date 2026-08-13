"""
Unit tests for AppUpdateService's app/ tree swap.

The satellite has no console and no log surface in the UI: the API these files
serve is the only way to repair it remotely. A sync that fails after it has
already started overwriting the live tree therefore takes the repair path down
with it, and the unit needs a hand on the machine.
"""
import os
import shutil

import pytest
from unittest.mock import patch

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
    async def test_nothing_is_staged_next_to_the_live_tree(self, repo_dir, incoming):
        """Both staging dirs are one app/ each; leaving them doubles the satellite's
        disk use per update, on a device with no one watching it."""
        await AppUpdateService()._sync_app_files(incoming)

        assert not (repo_dir / "app.new").exists()
        assert not (repo_dir / "app.old").exists()

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
