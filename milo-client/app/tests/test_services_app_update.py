"""
Unit tests for AppUpdateService's app/ tree swap.

The satellite has no console and no log surface in the UI: the API these files
serve is the only way to repair it remotely. A sync that fails after it has
already started overwriting the live tree therefore takes the repair path down
with it, and the unit needs a hand on the machine.
"""
import asyncio
import io
import json
import logging
import os
import stat
import subprocess
import tarfile
import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

import services.app_update as app_update_module
from services.app_update import AppUpdateService


@pytest.fixture(autouse=True)
def restore_cwd():
    """_sync_tree re-anchors the process cwd (the unit's WorkingDirectory
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
        await AppUpdateService()._sync_tree(incoming, "app")

        live = repo_dir / "app"
        assert (live / "main.py").read_text() == "new main\n"
        assert (live / "routes" / "health.py").read_text() == "new health\n"
        assert (live / "added.py").is_file()
        assert not (live / "gone.py").exists(), "a file dropped upstream must not survive"
        assert not (live / "__pycache__").exists(), "stale bytecode is not part of the release"

    @pytest.mark.asyncio
    async def test_the_tree_it_replaced_is_kept_for_the_rollback(self, repo_dir, incoming):
        """pip, the root deploy and the version write still come after this,
        and each can fail."""
        await AppUpdateService()._sync_tree(incoming, "app")

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
                await AppUpdateService()._sync_tree(incoming, "app")

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

        await AppUpdateService()._sync_tree(incoming, "app")

        assert not (repo_dir / "app" / "junk.py").exists()


def _make_tarball(tmp_path, app_files: dict, config_files: dict = None,
                  root_files: dict = None, runs_trials: bool = True) -> str:
    """Builds an update tarball holding milo-client/app/, plus configs/ and the
    root deploy's system/ and rootfs/ trees.

    `root_files` maps a path under milo-client/ (``system/…``, ``rootfs/…``) to
    its body, over the guard and the unit running it that every release since
    the trial ships (`runs_trials`); the update runs the sudo deploy script.
    """
    root_files = {**(TRIAL_FILES if runs_trials else {}), **(root_files or {})}
    staged = tmp_path / "tarball-src" / "milo-client" / "app"
    staged.mkdir(parents=True)
    for name, body in app_files.items():
        (staged / name).write_text(body)
    for rel, body in (config_files or {}).items():
        path = staged.parent / "configs" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    for rel, body in (root_files or {}).items():
        path = staged.parent / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    tarball = tmp_path / "update.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(str(staged.parent), arcname="milo-client")
    return str(tarball)


# What every release since the trial ships: the guard, run by the app's unit.
APP_UNIT = (
    "[Service]\nExecStartPre=-/usr/local/bin/milo-client-update-guard start\n"
    "ExecStart=/bin/true\n"
)
TRIAL_FILES = {
    "system/milo-client.service": APP_UNIT,
    "rootfs/usr/local/bin/milo-client-update-guard": "#!/usr/bin/python3 -I\n",
}
# What a release that changes the satellite's units and sudo policy carries.
ROOT_FILES = {
    "system/milo-client.service": APP_UNIT,
    "rootfs/etc/sudoers.d/milo-client": "milo-client ALL=(root) NOPASSWD: /bin/true\n",
}
# A release whose lock moved: what decides whether pip runs is requirements.lock,
# byte for byte, never the floors in requirements.txt it is compiled from.
NEW_LOCK = f"aiohttp==3.9.0 \\\n    --hash=sha256:{'a' * 64}\n"
NEW_REQUIREMENTS = {"main.py": "new main\n", "requirements.lock": NEW_LOCK}
IMPORT_ERROR = b"Traceback (most recent call last):\nModuleNotFoundError: No module named 'zeroconf'\n"

_real_exec = asyncio.create_subprocess_exec


def _spawner(pip_returncode: int = 0, import_returncode: int = 0, restart_returncode: int = -15):
    """Stands in for sudo, pip and the venv's python — the outside world —
    answering by argv. The update guard is not stood in for: it is this tree's
    own script and runs for real, on the paths `update_world` redirected.

    Every argv spawned is kept, in order. pip writes into the live venv before
    it answers, as a half-finished install does.
    """
    spawned = []

    async def _spawn(*argv, **kwargs):
        spawned.append(argv)
        if argv[0] == app_update_module.GUARD:
            return await _real_exec(*argv, **kwargs)
        proc = AsyncMock()
        stderr = b""
        if _ran_pip(argv):
            (Path(os.path.realpath(app_update_module.VENV_DIR)) / "marker").write_text("HALF")
            proc.returncode = pip_returncode
            if pip_returncode:
                stderr = b"ERROR: No matching distribution found for aiohttp>=99.0"
        elif _ran_import(argv):
            proc.returncode = import_returncode
            if import_returncode:
                stderr = IMPORT_ERROR
        elif "restart" in argv:
            proc.returncode = restart_returncode
            if restart_returncode > 0:
                stderr = b"Failed to restart milo-client.service: Unit milo-client.service is masked."
        else:
            proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        return proc

    return spawned, _spawn


def _ran_deploy_script(argv) -> bool:
    return app_update_module.DEPLOY_SCRIPT in argv


def _ran_pip(argv) -> bool:
    return argv[0] == str(app_update_module.VENV_DIR / "bin" / "pip3")


def _ran_import(argv) -> bool:
    return argv[0] == str(app_update_module.VENV_DIR / "bin" / "python3")


def _ran_restart(argv) -> bool:
    return "restart" in argv


@pytest.fixture
def identity_files(update_world):
    """The two facts a satellite reports: which version it shows, and which
    payload it runs.

    Separate because they answer different questions and move
    independently — most releases never touch `milo-client/` at all.
    """
    state, _ = update_world
    version = state / app_update_module.VERSION_NAME
    payload = state / app_update_module.PAYLOAD_NAME
    version.write_text("v0.1.0")
    payload.write_text("aaaa111")
    return version, payload


@pytest_asyncio.fixture
async def no_restart_timer():
    """Catches the restart deploy_update schedules two seconds out, so no timer
    outlives the patched subprocess; a test that wants it runs it itself."""
    scheduled = []
    loop = asyncio.get_running_loop()
    with patch.object(loop, "call_later", lambda delay, callback: scheduled.append(callback)):
        yield scheduled


async def _deploy(tmp_path, spawn, app_files=NEW_REQUIREMENTS, config_files=None, root_files=None,
                  runs_trials=True):
    tarball = _make_tarball(tmp_path, app_files, config_files, root_files, runs_trials)
    with patch.object(app_update_module.asyncio, "create_subprocess_exec", spawn):
        return await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")


def _last_update(state):
    path = state / app_update_module.LAST_UPDATE_NAME
    return json.loads(path.read_text()) if path.exists() else None


def _trial_open(state) -> bool:
    return (state / app_update_module.TRIAL_NAME).exists()


class TestDeployUpdateDependencyFailure:
    """What a satellite is left running when `pip install` fails.

    The satellite restarts into whatever `app/` holds two seconds after it
    answers, and `Restart=always` then retries it. A tree whose new
    dependencies were never installed therefore crashloops on an appliance whose
    only repair path is port 8001 — the port that tree serves. So the update has
    to fail *before* the identity is written and the restart is scheduled, and
    what it replaced has to be put back before it answers.
    """

    @pytest.fixture
    def failing_pip(self, no_restart_timer):
        spawned, spawn = _spawner(pip_returncode=1)
        with patch.object(app_update_module.asyncio, "create_subprocess_exec", spawn):
            yield spawned

    @pytest.mark.asyncio
    async def test_a_failed_pip_install_fails_the_update(
        self, repo_dir, tmp_path, identity_files, failing_pip
    ):
        """A warning here is a satellite that reports success and crashloops."""
        tarball = _make_tarball(tmp_path, NEW_REQUIREMENTS)

        result = await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        assert result["success"] is False
        assert "aiohttp" in result["error"], "the pip failure must reach the server verbatim"

    @pytest.mark.asyncio
    async def test_no_identity_is_left_when_the_deps_are_missing(
        self, repo_dir, tmp_path, identity_files, failing_pip
    ):
        """The payload file is what the server compares to call the update
        done, and the version file is what the owner reads on the screen."""
        version, payload = identity_files

        await AppUpdateService().deploy_update(_make_tarball(tmp_path, NEW_REQUIREMENTS), "bbbb222", "v0.2.0")

        assert version.read_text() == "v0.1.0"
        assert payload.read_text() == "aaaa111"

    @pytest.mark.asyncio
    async def test_the_previous_app_configs_and_venv_are_put_back(
        self, repo_dir, tmp_path, identity_files, update_world, failing_pip
    ):
        """configs/ is swapped beside app/, and pip had already written into the
        venv when it failed: the old code must find its own of all three."""
        state, real_venv = update_world
        live = repo_dir / "configs" / "camilladsp"
        live.mkdir(parents=True)
        (live / "config.yml").write_text("live devices\n")
        tarball = _make_tarball(tmp_path, NEW_REQUIREMENTS, {"camilladsp/config.yml": "new devices\n"})

        await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        assert (repo_dir / "app" / "main.py").read_text() == "live main\n"
        assert (repo_dir / "app" / "routes" / "health.py").read_text() == "live health\n"
        assert (live / "config.yml").read_text() == "live devices\n"
        assert (real_venv / "marker").read_text() == "OLD"
        leftovers = [p.name for p in tmp_path.rglob("*") if p.name.endswith((".old", ".new", ".discard"))]
        assert leftovers == [], "the restore must not leave a copy behind"

    @pytest.mark.asyncio
    async def test_the_failure_is_recorded_and_the_trial_closed(
        self, repo_dir, tmp_path, identity_files, update_world, failing_pip
    ):
        """What the server reads from /status to say why the push failed, and
        the next push is not refused as a trial still running."""
        state, _ = update_world

        await AppUpdateService().deploy_update(_make_tarball(tmp_path, NEW_REQUIREMENTS), "bbbb222", "v0.2.0")

        record = _last_update(state)
        assert (record["payload"], record["outcome"]) == ("bbbb222", "rolled_back")
        assert "aiohttp" in record["detail"]
        assert not _trial_open(state)

    @pytest.mark.asyncio
    async def test_a_failed_pip_install_never_runs_the_root_deploy(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """The rollback puts back app/, configs/ and the venv, never what the
        root deploy installed — units, sudoers, helpers. Run before pip, a pip
        failure left the old code under the new release's argument-scoped sudo
        policy, and every retry replayed the root deploy into the same failure."""
        spawned, spawn = _spawner(pip_returncode=1)

        result = await _deploy(tmp_path, spawn, root_files=ROOT_FILES)

        assert any(_ran_pip(argv) for argv in spawned), "pip must have been reached"
        assert not any(_ran_deploy_script(argv) for argv in spawned), (
            "the root deploy ran although pip failed"
        )
        assert result["success"] is False
        assert no_restart_timer == []


class TestDeployUpdateImportFailure:
    """The new app is imported before anything restarts.

    The most frequent way a pushed version dies on start is an import — a
    module the venv lacks, a syntax error. Caught here, the room is never cut:
    the update answers an error and the process that answers keeps playing.
    """

    @pytest.mark.asyncio
    async def test_an_app_that_does_not_import_is_never_restarted_into(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        state, _ = update_world
        version, payload = identity_files
        spawned, spawn = _spawner(import_returncode=1)

        result = await _deploy(tmp_path, spawn, root_files=ROOT_FILES)

        assert any(_ran_import(argv) for argv in spawned), "the import must have been tried"
        assert result["success"] is False
        assert "No module named 'zeroconf'" in result["error"]
        assert no_restart_timer == [], "no restart may be scheduled over an app that does not import"
        assert not any(_ran_deploy_script(argv) for argv in spawned)
        assert (repo_dir / "app" / "main.py").read_text() == "live main\n"
        assert payload.read_text() == "aaaa111"
        assert _last_update(state)["outcome"] == "rolled_back"

    @pytest.mark.asyncio
    async def test_the_import_runs_the_new_tree_with_the_venv_python(
        self, repo_dir, tmp_path, identity_files, no_restart_timer
    ):
        """Run from anywhere else, the import tests the code that is leaving."""
        seen = {}
        spawned, spawn = _spawner()

        async def _recording(*argv, **kwargs):
            if _ran_import(argv):
                seen["cwd"] = kwargs.get("cwd")
                seen["main"] = (Path(kwargs["cwd"]) / "main.py").read_text()
            return await spawn(*argv, **kwargs)

        await _deploy(tmp_path, _recording)

        assert seen == {"cwd": str(repo_dir / "app"), "main": "new main\n"}


class TestDeployUpdateTrial:
    """An applied update is a trial: what it replaced stays until it has held."""

    @pytest.mark.asyncio
    async def test_an_applied_update_keeps_what_it_replaced(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """The inverse of the rule this replaced (no copy left behind): the old
        tree was deleted before the restart, so a version that died on start
        had nothing to go back to and crashlooped with its room mute."""
        state, real_venv = update_world
        version, payload = identity_files
        _, spawn = _spawner()

        result = await _deploy(tmp_path, spawn)

        assert result["success"] is True
        assert len(no_restart_timer) == 1
        assert (repo_dir / "app" / "main.py").read_text() == "new main\n"
        assert (repo_dir / "app.old" / "main.py").read_text() == "live main\n"
        assert (real_venv.with_name("venv.old") / "marker").read_text() == "OLD"
        assert (version.read_text(), payload.read_text()) == ("v0.2.0", "bbbb222")
        assert (state / "app-payload.old").read_text() == "aaaa111"
        assert (state / app_update_module.TRIAL_NAME).read_text().strip() == "bbbb222"
        assert not (repo_dir / "app.new").exists()

    @pytest.mark.asyncio
    async def test_the_trial_is_open_before_the_swap(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """A power cut right after the swap must find a trial to roll back:
        opened after it, the unit would boot the new code with nothing to say
        it is on trial, and keep it however it fails."""
        state, _ = update_world
        real_rename = os.rename
        open_at_swap = []

        def _rename(src, dst):
            if Path(dst) == repo_dir / "app.old":
                open_at_swap.append(_trial_open(state))
            return real_rename(src, dst)

        _, spawn = _spawner()
        with patch.object(app_update_module.os, "rename", _rename):
            await _deploy(tmp_path, spawn)

        assert open_at_swap == [True]

    @pytest.mark.asyncio
    async def test_the_venv_is_kept_and_pip_run_only_when_requirements_change(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """A venv is the largest thing on the card; copying it for a release
        whose lock did not move doubles its disk use for nothing."""
        _, real_venv = update_world
        (repo_dir / "app" / "requirements.lock").write_text(NEW_LOCK)
        spawned, spawn = _spawner()

        result = await _deploy(tmp_path, spawn)

        assert result["success"] is True
        assert not any(_ran_pip(argv) for argv in spawned)
        assert not real_venv.with_name("venv.old").exists()
        assert any(_ran_import(argv) for argv in spawned), "the import runs either way"

    @pytest.mark.asyncio
    async def test_pip_installs_the_lock_with_every_file_checked(
        self, repo_dir, tmp_path, identity_files, no_restart_timer
    ):
        """The lock with `--require-hashes`, never requirements.txt: its floors
        resolve to whatever PyPI serves the day of the push, so two satellites
        pushed a week apart ran two sets, neither the one CI tested."""
        spawned, spawn = _spawner()

        result = await _deploy(tmp_path, spawn)

        assert result["success"] is True
        pip = next(argv for argv in spawned if _ran_pip(argv))
        assert pip[1:5] == ("install", "--require-hashes", "-r",
                            str(app_update_module.REPO_DIR / "app" / "requirements.lock"))

    @pytest.mark.asyncio
    async def test_dependencies_are_installed_before_the_root_deploy(
        self, repo_dir, tmp_path, identity_files, no_restart_timer
    ):
        """The root deploy is the one step the rollback cannot undo, so it must
        be the last one that can fail before the identity is written."""
        spawned, spawn = _spawner()

        result = await _deploy(tmp_path, spawn, root_files=ROOT_FILES)

        assert result["success"] is True
        pip_at = [i for i, argv in enumerate(spawned) if _ran_pip(argv)]
        import_at = [i for i, argv in enumerate(spawned) if _ran_import(argv)]
        deploy_at = [i for i, argv in enumerate(spawned) if _ran_deploy_script(argv)]
        assert len(pip_at) == len(import_at) == len(deploy_at) == 1, spawned
        assert pip_at[0] < import_at[0] < deploy_at[0]

    @pytest.mark.asyncio
    async def test_the_shipped_configs_land_beside_the_app(
        self, repo_dir, tmp_path, identity_files, no_restart_timer
    ):
        """configs/ is part of the payload fingerprint. Left out of the sync, a
        commit touching only the CamillaDSP config offered every satellite a
        push, restarted it, and marked it up to date with nothing installed."""
        live = repo_dir / "configs" / "camilladsp"
        live.mkdir(parents=True)
        (live / "config.yml").write_text("live devices\n")
        (live / "dropped.yml").write_text("dropped in the new release\n")
        _, spawn = _spawner()

        result = await _deploy(tmp_path, spawn, config_files={"camilladsp/config.yml": "new devices\n"})

        assert result["success"] is True
        assert (live / "config.yml").read_text() == "new devices\n"
        assert not (live / "dropped.yml").exists()

    @pytest.mark.asyncio
    async def test_a_push_during_a_trial_is_refused(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """A second push would overwrite app.old, the tree the first one's
        rollback needs, with the version on trial."""
        for push in ("first", "second"):
            (tmp_path / push).mkdir()
        _, spawn = _spawner()
        await _deploy(tmp_path / "first", spawn)
        spawned, spawn = _spawner()

        result = await _deploy(tmp_path / "second", spawn)

        assert result["success"] is False
        assert result["conflict"] is True
        assert spawned == []
        assert (repo_dir / "app.old" / "main.py").read_text() == "live main\n"

    @pytest.mark.asyncio
    async def test_the_layout_left_for_the_restart_is_one_the_guard_rolls_back(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """The two halves meet at the unit's start: three starts of a version
        that keeps dying, and the third runs the code that was there before."""
        state, real_venv = update_world
        version, payload = identity_files
        _, spawn = _spawner()
        await _deploy(tmp_path, spawn)
        env = {**app_update_module._guard_env(), "MILO_CLIENT_JOURNALCTL": "/bin/true"}

        for _ in range(3):
            subprocess.run([app_update_module.GUARD, "start"], env=env, check=True, timeout=30)

        assert (repo_dir / "app" / "main.py").read_text() == "live main\n"
        assert (real_venv / "marker").read_text() == "OLD"
        assert (version.read_text(), payload.read_text()) == ("v0.1.0", "aaaa111")
        assert _last_update(state)["outcome"] == "rolled_back"


class TestTrialBoundaries:
    """What a trial may not inherit from the update before it, and what a
    pushed tree with no trial of its own leaves behind."""

    @pytest.mark.asyncio
    async def test_a_tree_that_runs_no_trial_is_applied_without_one(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """A retracted release from before the guard, pushed back: nothing in
        it would ever commit, so a trial left open would refuse every push and
        be rolled back over the next updater's deploy."""
        state, real_venv = update_world
        _, spawn = _spawner()

        result = await _deploy(tmp_path, spawn, runs_trials=False)

        assert result["success"] is True
        assert len(no_restart_timer) == 1
        assert (repo_dir / "app" / "main.py").read_text() == "new main\n"
        assert not _trial_open(state)
        assert not (repo_dir / "app.old").exists()
        assert not real_venv.with_name("venv.old").exists()
        assert _last_update(state)["outcome"] == "committed"

    @pytest.fixture
    def stuck_previous(self, repo_dir):
        """An app.old milo-client cannot delete — a root-owned __pycache__ —
        stood in for by a directory it may not write."""
        stuck = repo_dir / "app.old" / "__pycache__"
        stuck.mkdir(parents=True)
        (stuck / "main.cpython-313.pyc").write_bytes(b"\x00")
        stuck.chmod(0o500)
        yield
        stuck.chmod(0o700)

    @pytest.mark.asyncio
    async def test_a_kept_tree_that_cannot_be_cleared_refuses_the_update(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer, stuck_previous
    ):
        """Kept as the trial's way back, the rollback would move the live app/
        aside and put this partial tree in its place."""
        state, _ = update_world
        _, spawn = _spawner()

        result = await _deploy(tmp_path, spawn)

        assert result["success"] is False
        assert "app.old" in result["error"]
        assert not _trial_open(state)
        assert _last_update(state) is None
        assert (repo_dir / "app" / "main.py").read_text() == "live main\n"
        assert no_restart_timer == []

    @pytest.mark.asyncio
    async def test_what_a_rollback_set_aside_is_deleted_by_the_next_update(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """The guard only renames inside TimeoutStartSec; a venv left aside is
        the largest thing on the card."""
        _, real_venv = update_world
        for aside in (repo_dir / "app.discard", real_venv.with_name("venv.discard")):
            aside.mkdir()
        _, spawn = _spawner()

        assert (await _deploy(tmp_path, spawn))["success"] is True

        assert not (repo_dir / "app.discard").exists()
        assert not real_venv.with_name("venv.discard").exists()


class TestCommitTrial:
    """The new process ends its own trial once it has held."""

    @pytest.mark.asyncio
    async def test_a_commit_that_failed_is_tried_again(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """Left open over a version that held, the trial refuses every push and
        rolls that version back two restarts later."""
        state, _ = update_world
        _, spawn = _spawner()
        await _deploy(tmp_path, spawn)
        commits = []

        async def _first_commit_times_out(*argv, **kwargs):
            if argv[0] == app_update_module.GUARD and argv[1] == "commit":
                commits.append(argv)
                if len(commits) == 1:
                    raise asyncio.TimeoutError()
            return await spawn(*argv, **kwargs)

        with patch.object(app_update_module.asyncio, "create_subprocess_exec", _first_commit_times_out), \
                patch.object(app_update_module, "COMMIT_RETRY_S", 0):
            await AppUpdateService().commit_trial("bbbb222")

        assert len(commits) == 2
        assert not _trial_open(state)
        assert _last_update(state)["outcome"] == "committed"

    @pytest.mark.asyncio
    async def test_the_version_on_trial_commits_itself(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        state, real_venv = update_world
        _, spawn = _spawner()
        await _deploy(tmp_path, spawn)

        with patch.object(app_update_module.asyncio, "create_subprocess_exec", spawn):
            await AppUpdateService().commit_trial("bbbb222")

        assert not _trial_open(state)
        assert _last_update(state)["outcome"] == "committed"
        assert not (repo_dir / "app.old").exists()
        assert not real_venv.with_name("venv.old").exists()

    @pytest.mark.asyncio
    async def test_a_process_running_other_code_never_commits_the_trial(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer
    ):
        """A process whose own hold was still counting when it took the push:
        committing then would bless a version that has not run once."""
        state, _ = update_world
        _, spawn = _spawner()
        await _deploy(tmp_path, spawn)
        spawned, spawn = _spawner()

        with patch.object(app_update_module.asyncio, "create_subprocess_exec", spawn):
            await AppUpdateService().commit_trial("aaaa111")

        assert spawned == []
        assert _trial_open(state)
        assert (repo_dir / "app.old").exists()


class TestRestartOutcome:
    """Which systemctl exits mean the restart did not happen.

    A restart that works kills this process, and systemd kills the whole cgroup
    — the `systemctl` child included — so signalled death is the success case.
    Measured on canapé: -15 with an empty stderr, 150 ms in. Treating any
    non-zero code as a failure logged an ERROR on every applied update, which on
    a satellite means the one log surface it has cries wolf.
    """

    async def _restart_after_deploy(self, tmp_path, returncode, caplog):
        _, spawn = _spawner(restart_returncode=returncode)
        assert (await _deploy(tmp_path, spawn))["success"] is True
        with patch.object(app_update_module.asyncio, "create_subprocess_exec", spawn):
            with caplog.at_level(logging.ERROR):
                await AppUpdateService()._restart_service()
        return [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.asyncio
    async def test_a_restart_that_killed_us_is_not_a_failure(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer, caplog
    ):
        state, _ = update_world

        assert await self._restart_after_deploy(tmp_path, -15, caplog) == []
        assert _trial_open(state), "the new process ends the trial, not this one"

    @pytest.mark.asyncio
    async def test_a_systemctl_that_refused_puts_the_previous_version_back(
        self, repo_dir, tmp_path, identity_files, update_world, no_restart_timer, caplog
    ):
        """A masked unit, or one the deploy step just made invalid. Left open,
        the trial would sit over a process still running the old code until
        the next start — a reboot weeks later — landed in a version nothing
        ever tried."""
        state, _ = update_world
        version, payload = identity_files

        records = await self._restart_after_deploy(tmp_path, 1, caplog)

        assert any("masked" in r.getMessage() for r in records)
        assert (repo_dir / "app" / "main.py").read_text() == "live main\n"
        assert payload.read_text() == "aaaa111"
        record = _last_update(state)
        assert record["outcome"] == "rolled_back"
        assert record["detail"].startswith("restart refused:")
        assert not _trial_open(state)


def _tarball_of(tmp_path, members) -> str:
    """Writes a tarball holding exactly the members given, as (TarInfo, payload) pairs.

    Hand-built rather than produced by `tar.add()`: an arcname is derived from a
    path that exists, so a traversing name or a link escaping the destination
    cannot be produced from a real tree — only by an attacker writing the archive
    directly, which is precisely the case the guard exists for.
    """
    tarball = tmp_path / "unsafe.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for info, payload in members:
            if payload is None:
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return str(tarball)


def _dir(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


class TestExtractTarballRefusesUnsafeMembers:
    """The satellite's only validation of an unauthenticated upload.

    `POST /app/update` takes a tarball from anything that can reach port 8001 and
    hands it straight to tarfile — there is no token, no signature and no peer
    check, only the LAN. The extraction runs as the account that owns the app and
    the `milo-client-deploy-update` sudo wrapper, so a member landing outside the
    temp dir is a write into a home directory, a unit file or a crontab on a
    machine nobody looks at.

    The rejection has to happen before the first member is written: a guard that
    fires halfway leaves a partial tree behind on the very disk it was defending.
    """

    @pytest.mark.asyncio
    async def test_an_absolute_member_name_is_refused(self, tmp_path):
        tarball = _tarball_of(tmp_path, [
            (tarfile.TarInfo("/etc/cron.d/milo-client"), b"* * * * * root id\n"),
        ])
        dest = tmp_path / "dest"
        dest.mkdir()

        with pytest.raises(ValueError, match="Unsafe path"):
            await AppUpdateService()._extract_tarball(tarball, str(dest))

        assert list(dest.iterdir()) == [], "a refused tarball must leave nothing behind"

    @pytest.mark.asyncio
    async def test_a_traversing_member_name_is_refused(self, tmp_path):
        tarball = _tarball_of(tmp_path, [
            (_dir("milo-client"), None),
            (tarfile.TarInfo("milo-client/../../../.ssh/authorized_keys"), b"ssh-rsa AAAA\n"),
        ])
        dest = tmp_path / "dest"
        dest.mkdir()

        with pytest.raises(ValueError, match="Unsafe path"):
            await AppUpdateService()._extract_tarball(tarball, str(dest))

        assert list(dest.iterdir()) == []

    @pytest.mark.asyncio
    async def test_a_symlink_pointing_out_of_the_destination_is_refused(self, tmp_path):
        """The hole a name check alone leaves open, reproduced before the fix:
        every member name here is clean and the escape lives in a link target the
        guard never read. The third member then writes *through* the second."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "authorized_keys").write_text("the real one\n")

        link = tarfile.TarInfo("milo-client/app")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"

        tarball = _tarball_of(tmp_path, [
            (_dir("milo-client"), None),
            (link, None),
            (tarfile.TarInfo("milo-client/app/authorized_keys"), b"pwned\n"),
        ])
        dest = tmp_path / "dest"
        dest.mkdir()

        with pytest.raises(ValueError, match="Unsafe path"):
            await AppUpdateService()._extract_tarball(tarball, str(dest))

        assert (outside / "authorized_keys").read_text() == "the real one\n"

    @pytest.mark.asyncio
    async def test_a_setuid_member_lands_without_its_setuid_bit(self, tmp_path):
        """`_deploy_system_files` copies this tree to /usr/local/bin as root, so a
        mode the extraction honoured is a mode the fleet installs. Names cannot
        express this one — it is what the `data` extraction filter is there for."""
        payload = b"#!/bin/bash\nexec bash\n"
        info = tarfile.TarInfo("milo-client/rootfs/usr/local/bin/milo-client-shell")
        info.mode = 0o4755

        tarball = _tarball_of(tmp_path, [(_dir("milo-client"), None), (info, payload)])
        dest = tmp_path / "dest"
        dest.mkdir()

        await AppUpdateService()._extract_tarball(tarball, str(dest))

        mode = (dest / info.name).stat().st_mode
        assert not mode & stat.S_ISUID, "a setuid bit must not survive the extraction"

    @pytest.mark.asyncio
    async def test_the_release_tarball_still_extracts(self, tmp_path):
        """The floor under the four above: a guard that refuses everything blocks
        the fleet instead of an attacker. The executable bit is part of the
        contract — the deploy wrapper installs the milo-client-* helpers from
        this tree, and a non-executable one denies the *next* update."""
        src = tmp_path / "src" / "milo-client" / "rootfs" / "usr" / "local" / "bin"
        src.mkdir(parents=True)
        helper = src / "milo-client-deploy-update"
        helper.write_text("#!/bin/bash\n")
        helper.chmod(0o755)

        tarball = tmp_path / "release.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(str(tmp_path / "src" / "milo-client"), arcname="milo-client")

        dest = tmp_path / "dest"
        dest.mkdir()
        await AppUpdateService()._extract_tarball(str(tarball), str(dest))

        deployed = dest / "milo-client" / "rootfs" / "usr" / "local" / "bin" / "milo-client-deploy-update"
        assert deployed.is_file()
        assert os.access(deployed, os.X_OK), "the sudoers helpers ship executable"
