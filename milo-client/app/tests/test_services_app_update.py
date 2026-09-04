"""
Unit tests for AppUpdateService's app/ tree swap.

The satellite has no console and no log surface in the UI: the API these files
serve is the only way to repair it remotely. A sync that fails after it has
already started overwriting the live tree therefore takes the repair path down
with it, and the unit needs a hand on the machine.
"""
import io
import logging
import os
import stat
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
    def identity_files(self, tmp_path):
        """The two facts a satellite reports: which version it shows, and which
        payload it runs.

        Separate because they answer different questions and move
        independently — most releases never touch `milo-client/` at all.
        """
        version = tmp_path / "app-version"
        payload = tmp_path / "app-payload"
        version.write_text("v0.1.0")
        payload.write_text("aaaa111")
        with patch.object(app_update_module, "VERSION_FILE", version), \
                patch.object(app_update_module, "PAYLOAD_FILE", payload):
            yield version, payload

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
        self, repo_dir, tmp_path, identity_files, failing_pip
    ):
        """A warning here is a satellite that reports success and crashloops."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        result = await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        assert result["success"] is False
        assert "aiohttp" in result["error"], "the pip failure must reach the server verbatim"

    @pytest.mark.asyncio
    async def test_no_identity_is_written_when_the_deps_are_missing(
        self, repo_dir, tmp_path, identity_files, failing_pip
    ):
        """The payload file is what the server polls to call the update done,
        and the version file is what the owner reads on the screen. Writing
        either one over a deployment that failed makes a crashlooping satellite
        report itself as the version it never reached."""
        version, payload = identity_files
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        assert version.read_text() == "v0.1.0"
        assert payload.read_text() == "aaaa111"

    @pytest.mark.asyncio
    async def test_the_previous_tree_is_put_back(
        self, repo_dir, tmp_path, identity_files, failing_pip
    ):
        """The window this closes: app.old was deleted inside the swap, so by the
        time pip ran there was nothing left to go back to."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        live = repo_dir / "app"
        assert (live / "main.py").read_text() == "live main\n"
        assert (live / "routes" / "health.py").read_text() == "live health\n"
        assert not (repo_dir / "app.old").exists(), "the restore must not leave a copy behind"
        assert not (repo_dir / "app.new").exists()


class TestDeployUpdateSuccess:
    """The committed path, and the floor the failure tests above stand on."""

    @pytest.fixture
    def identity_files(self, tmp_path):
        """The two facts a satellite reports: which version it shows, and which
        payload it runs.

        Separate because they answer different questions and move
        independently — most releases never touch `milo-client/` at all.
        """
        version = tmp_path / "app-version"
        payload = tmp_path / "app-payload"
        version.write_text("v0.1.0")
        payload.write_text("aaaa111")
        with patch.object(app_update_module, "VERSION_FILE", version), \
                patch.object(app_update_module, "PAYLOAD_FILE", payload):
            yield version, payload

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
        self, repo_dir, tmp_path, identity_files, working_pip
    ):
        """Both staging dirs are one app/ each; keeping either doubles the
        satellite's disk use per update, on a device with no one watching it."""
        tarball = _make_tarball(tmp_path, {
            "main.py": "new main\n",
            "requirements.txt": "aiohttp==3.9.0\n",
        })

        version, payload = identity_files
        result = await AppUpdateService().deploy_update(tarball, "bbbb222", "v0.2.0")

        assert result["success"] is True
        assert (repo_dir / "app" / "main.py").read_text() == "new main\n"
        assert version.read_text() == "v0.2.0"
        assert payload.read_text() == "bbbb222"
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
