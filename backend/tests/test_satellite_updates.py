# backend/tests/test_satellite_updates.py
"""
Tests for the server half of the satellite update path: what gets shipped, what
counts as a finished update, and when the UI is allowed to offer one.

Every assertion here is about a machine CI can never reach — a second physical
Pi — so the mocks stand for the satellite's HTTP surface and for git, and the
assertion is always what the service concluded from what the satellite said.
"""
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.programs import create_programs_router
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import Client
from backend.core.multiroom.satellite_http import (
    SATELLITE_SECRET_FIELD,
    SATELLITE_SECRET_HEADER,
    SatelliteSecret,
)
from backend.core.updates.satellite import PAYLOAD_PATHSPEC, SatelliteUpdateService

# What the server names in the call. It resolves the version against its own
# manifest — including a trial of an unvalidated release — because a satellite
# carries neither a manifest nor a GitHub token.
SNAPCLIENT_TARGET = "0.28.0"
CAMILLADSP_TARGET = "3.1.0"


# The two things the server tells a satellite about itself, and they answer
# different questions. The VERSION is what the row displays — character for
# character what the server's own row shows, since both halves ship from one
# commit. The PAYLOAD fingerprints the `milo-client/` tree the tarball carries,
# and is what decides whether a satellite needs the push at all: most releases
# never touch that directory, so deciding on the displayed version would
# restart every speaker for nothing.
SERVER_VERSION = "v0.2.0"
SERVER_PAYLOAD = "deadbee"


def _mock_proc(stdout: bytes, returncode: int = 0):
    proc = Mock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


def _fake_git(payload: str = SERVER_PAYLOAD, described: str = SERVER_VERSION,
              dirty: bool = False, diff: str = "- old\n+ new", status: str = None):
    """Stands in for git across the calls the two identities issue.

    It answers each by its argv instead of returning one string to all of them:
    a single-answer double lets an identity built from the wrong command pass.
    Every argv it received is recorded on `.calls`.
    """
    calls = []

    def _run(program, *args, **kwargs):
        argv = [str(a) for a in args]
        calls.append(argv)
        if "log" in argv:
            out = payload
        elif "describe" in argv:
            # `--exact-match` fails rather than falling back, which is the whole
            # reason it is used: a tree past a tag has no release, and a double
            # that answered a string here would hide that.
            if described is None:
                return _mock_proc(b"", returncode=128)
            out = described
        elif "status" in argv:
            out = (status if status is not None
                   else (" M milo-client/app/main.py" if dirty else ""))
        elif "diff" in argv:
            out = diff if dirty else ""
        else:
            raise AssertionError(f"unexpected git call: {argv}")
        return _mock_proc(out.encode() + b"\n")

    mock = AsyncMock(side_effect=_run)
    mock.calls = calls
    return mock


class _FakeResponse:
    """One aiohttp response: an async context manager with a status and a body."""

    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        # aiohttp responses carry both; the app-update arm reads `text()` on a
        # refusal because a satellite that rejects a tarball answers a plain
        # message, not JSON. A double that only has `json()` is a shape the real
        # server cannot produce.
        return self._payload if isinstance(self._payload, str) else str(self._payload)


class _FakeSatellite:
    """A satellite answering the endpoints the update path polls.

    `/status` reports the versions it currently runs; `/<component>/update/status`
    reports an update that has already finished, which is the exact situation the
    waiters must not mistake for a successful one.
    """

    def __init__(self, status_payload, post_payload=None, status_after_post=None):
        self.status_payload = status_payload
        self.post_payload = post_payload
        self.status_after_post = status_after_post
        self.posts = []
        self.bodies = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if url.endswith("/update/status"):
            return _FakeResponse(200, {"update_in_progress": False})
        return _FakeResponse(200, self.status_payload)

    def post(self, url, **kwargs):
        self.posts.append(url)
        self.bodies.append(kwargs.get("json"))
        if self.status_after_post is not None:
            # What the push does to the unit: from here /status answers as the
            # satellite does once it has deployed the tarball.
            self.status_payload = self.status_after_post
        return _FakeResponse(200, self.post_payload)


class _FakeFleet:
    """The whole fleet at once: `/status` answers per IP, and an IP the fleet
    does not hold refuses the connection the way an unplugged satellite does.

    `_FakeSatellite` answers every host identically, which cannot express the
    only question the fleet push asks — which of them is behind.
    """

    def __init__(self, by_ip):
        self._by_ip = by_ip

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        ip = url.split("//", 1)[1].split(":", 1)[0]
        if ip not in self._by_ip:
            raise ConnectionError(f"nothing answering at {ip}")
        return _FakeResponse(200, self._by_ip[ip])


class _MemorySettings:
    """SettingsService's two calls the satellite service makes, over a dict.

    The round trip through the real service and its file is pinned once, in
    TestAppFailureRecord; everywhere else the record is only a place to look.
    """

    def __init__(self, failures=None):
        self.values = {"updates.satellite_app_failures": dict(failures or {})}

    async def get_setting(self, key):
        return self.values.get(key)

    async def set_setting(self, key, value):
        self.values[key] = value
        return True


def _patch_satellite(satellite):
    """Every ClientSession the service opens answers as this one satellite."""
    return patch("backend.core.updates.satellite.aiohttp.ClientSession", return_value=satellite)


@pytest.fixture
def satellite_service(satellite_secret):
    registry = Mock()
    # `name` cannot be passed to the Mock constructor: it names the mock itself
    # and leaves `client.name` a child Mock, which is how `display_name` ends up
    # as a repr string in the payload the UI labels its buttons with.
    canape = Mock(is_local=False, ip="192.168.1.153", online=True, host="milo-client")
    canape.name = "Canapé"
    registry.get_all_clients = Mock(return_value={"dc:a6:32:7e:d3:43": canape})
    return SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                  settings_service=_MemorySettings(), satellite_secret=satellite_secret)


async def _registry_loaded_from(clients):
    """A real registry booted from the persisted clients alone (`{mac: (ip, name)}`),
    with no snapserver to admit them — the registry of a unit in direct mode,
    which every boot reloads with each client offline."""
    persisted = {
        mac: Client(mac_id=mac, name=name, ip=ip).to_dict(include_runtime=False)
        for mac, (ip, name) in clients.items()
    }
    settings = AsyncMock()
    settings.get_setting = AsyncMock(
        side_effect=lambda key: persisted if key == "multiroom.clients" else None
    )
    registry = ClientRegistryService(settings_service=settings)
    await registry.initialize()
    return registry


class TestClientTarball:
    """What a satellite update actually ships.

    The satellite builds its own venv at install time and symlinks it into the
    repo dir; a venv inside the tarball is bytes it extracts and throws away.
    """

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app" / "routes").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        (tree / "app" / "routes" / "health.py").write_text("health\n")
        (tree / "app" / "__pycache__").mkdir()
        (tree / "app" / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00")
        (tree / "app" / "tests").mkdir()
        (tree / "app" / "tests" / "test_routes.py").write_text("tests\n")
        (tree / "system").mkdir()
        (tree / "system" / "milo-client.service").write_text("unit\n")
        (tree / "venv" / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
        (tree / "venv" / "bin").mkdir()
        (tree / "venv" / "bin" / "pip3").write_text("pip\n")
        (tree / "venv" / "lib" / "python3.13" / "site-packages" / "aiohttp.py").write_text("dep\n")
        return tree

    async def _members(self, service, client_tree):
        with patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
             patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git()):
            tarball_path, version = await service._create_client_tarball()

        assert version == SERVER_PAYLOAD
        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                return [m.name for m in tar.getmembers()]
        finally:
            Path(tarball_path).unlink()

    async def test_the_app_the_satellite_runs_is_shipped(self, satellite_service, client_tree):
        """Sanity floor: if the tarball were empty every exclusion below would pass."""
        members = await self._members(satellite_service, client_tree)

        assert "milo-client/app/main.py" in members
        assert "milo-client/app/routes/health.py" in members
        assert "milo-client/system/milo-client.service" in members

    async def test_the_venv_is_not_shipped(self, satellite_service, client_tree):
        """A shipped venv is ~67 MB of an ~67 MB transfer, extracted then discarded."""
        members = await self._members(satellite_service, client_tree)

        assert not [m for m in members if "venv" in Path(m).parts]

    async def test_pycache_and_tests_are_not_shipped(self, satellite_service, client_tree):
        """The other exclusions must survive the venv addition."""
        members = await self._members(satellite_service, client_tree)

        assert not [m for m in members if "__pycache__" in Path(m).parts]
        assert not [m for m in members if "tests" in Path(m).parts]


class TestTheTwoSatelliteIdentities:
    """A satellite answers two questions, and one string used to answer both.

    *Which version does it run* is the release, and it is what the row prints —
    in the server's own numbering, because both halves ship from one commit.
    *Does its code differ from the server's* is the payload: the commit that
    last touched the `milo-client/` trees a push installs.

    Collapsing them either way breaks something measured. Deciding on the
    release lit the update button on the whole fleet every time the server
    moved — 100 commits, 0 of them touching the directory — and each press
    deployed a byte-identical payload while restarting a speaker in an occupied
    room. Displaying the payload put a `git describe` hash on the screen beside
    the server's clean tag, in a numbering nothing else on the appliance uses.
    """

    async def test_the_payload_is_the_last_commit_touching_the_client_tree(
            self, satellite_service):
        git = _fake_git(payload="c6247d9e1a0b4f2d8c3e5a7b9d1f3e5c7a9b0d2f")

        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", git):
            payload = await satellite_service.get_client_payload_version()

        assert payload == "c6247d9e1a0b4f2d8c3e5a7b9d1f3e5c7a9b0d2f"
        log = next(argv for argv in git.calls if "log" in argv)
        assert log[log.index("--") + 1:] == list(PAYLOAD_PATHSPEC), (
            "unscoped, this fingerprints HEAD again"
        )

    async def test_the_dirty_suffix_reads_the_same_trees_as_the_commit(
            self, satellite_service):
        """An edit to a file no push installs must not mark the payload dirty,
        or the button lights for it exactly as a commit would."""
        git = _fake_git(dirty=True)

        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", git):
            await satellite_service.get_client_payload_version()

        for verb in ("status", "diff"):
            argv = next(a for a in git.calls if verb in a)
            assert argv[argv.index("--") + 1:] == list(PAYLOAD_PATHSPEC), verb

    async def test_a_tests_only_commit_does_not_change_the_payload(
            self, satellite_service, tmp_path):
        """The payload decides whether a speaker is restarted, so it must be a
        function of the trees a push installs and nothing else.

        Read from a real repository, because both ways it went wrong were git's
        doing, not Milō's: an abbreviated hash grows a character once the object
        store passes 65 535 objects, and a commit touching only `app/tests/`
        (which the tarball leaves out) named a new commit. Either one changed
        the string, and a string compare offered the whole fleet a push of
        byte-identical code.
        """
        # A GIT_DIR or GIT_INDEX_FILE inherited from a hook outranks `-C` and
        # would commit into the live checkout; the user's config could add hooks.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(tmp_path), "-c", "user.name=milo", "-c", "user.email=milo@localhost",
                 *args],
                check=True, capture_output=True, text=True, env=env,
            ).stdout.strip()

        def commit(relative, body):
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            git("add", relative)
            git("commit", "-q", "-m", relative)
            return git("rev-parse", "HEAD")

        async def spawn_in_tmp_repo(program, *args, **kwargs):
            # The file's spawn guard stands for the live checkout; this test
            # runs git for real, and only ever against its own repository.
            assert (program, args[:2]) == ("git", ("-C", str(tmp_path))), args
            return await asyncio.subprocess.create_subprocess_exec(
                program, *args, **{**kwargs, "env": env})

        git("init", "-q")
        shipped = commit("milo-client/app/main.py", "v1\n")

        with patch("backend.core.updates.satellite.MILO_REPO_DIR", tmp_path), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      new=spawn_in_tmp_repo):
            assert await satellite_service.get_client_payload_version() == shipped, (
                "the payload is not the full hash of the last commit touching the shipped trees"
            )

            commit("milo-client/app/tests/test_x.py", "def test_x(): pass\n")
            assert await satellite_service.get_client_payload_version() == shipped

            (tmp_path / "milo-client/app/tests/test_y.py").write_text("def test_y(): pass\n")
            assert await satellite_service.get_client_payload_version() == shipped, (
                "an untracked test file marks the payload dirty"
            )

            changed = commit("milo-client/app/main.py", "v2\n")
            assert await satellite_service.get_client_payload_version() == changed != shipped

    async def test_uncommitted_client_work_is_marked_dirty(self, satellite_service):
        """The tarball is built from the working tree, so an edit no commit
        names is still payload. Without the suffix, a satellite change under
        test reports the committed fingerprint and can never be pushed from the
        UI.
        """
        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git(dirty=True)):
            payload = await satellite_service.get_client_payload_version()

        assert payload.startswith(f"{SERVER_PAYLOAD}-dirty.")

    async def test_a_new_module_is_hashed_file_by_file(self, satellite_service):
        """git collapses an untracked directory into one `?? dir/` entry.

        Hashing that entry reads a directory, which raises, which the digest
        swallows -- so every edit inside a brand-new module produced the same
        fingerprint, the exact case this digest exists to catch. `-uall` is what
        makes git name the files instead, and `-z` stops it quoting a path that
        holds an accent.
        """
        git = _fake_git(dirty=True)

        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", git):
            await satellite_service.get_client_payload_version()

        status = next(argv for argv in git.calls if "status" in argv)
        assert "--untracked-files=all" in status, "an untracked directory would hash as one entry"
        assert "-z" in status, "a quoted path reads as a missing file"

    async def test_editing_uncommitted_work_again_changes_the_payload(
            self, satellite_service):
        """A bare `-dirty` is a flag, not a fingerprint.

        It reads identically before and after the working tree changes, so the
        first push of a change under test landed and every later one was
        reported as already applied. Measured on the fleet: both satellites
        answered `081c8316-dirty` and the UI said "up to date", while neither
        carried a route added to that directory half an hour earlier.
        """
        async def payload_for(diff):
            with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                       _fake_git(dirty=True, diff=diff)):
                return await satellite_service.get_client_payload_version()

        first = await payload_for("- old\n+ new")
        second = await payload_for("- old\n+ newer still")

        assert first != second

    async def test_a_git_that_fails_yields_no_payload(self, satellite_service):
        """None disarms every satellite's button; any stray string would arm
        them all at once and ship an unstamped tarball."""
        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=_mock_proc(b"", returncode=128))):
            assert await satellite_service.get_client_payload_version() is None

    async def test_the_displayed_version_is_the_one_the_server_shows_for_itself(
            self, satellite_service):
        git = _fake_git(described="v0.3.1")

        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", git):
            version = await satellite_service.get_server_version()

        assert version == "v0.3.1"
        described = next(argv for argv in git.calls if "describe" in argv)
        assert "--always" in described and "--exact-match" not in described, (
            "`--exact-match` answers 'is this a release', which is what the OFFER "
            "asks; asked here it blanks the row of every satellite pushed from a "
            "development tree"
        )

    async def test_a_development_checkout_still_names_a_version(self, satellite_service):
        """The defect this replaced, measured on the two units: both rows empty
        while both satellites were up and reachable.

        A satellite row asks "what does this speaker run", never "is that a
        release". Answering the second question leaves a blank cell that cannot
        be told apart from a satellite that did not answer at all.
        """
        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git(described="v0.2.0-rc1-9-gef269e15")):
            assert await satellite_service.get_server_version() == "v0.2.0-rc1-9-gef269e15"

    async def test_a_prerelease_is_a_version_a_satellite_may_carry(self, satellite_service):
        """The server refuses to *offer* a pre-release to the fleet; it does not
        refuse to run one, and a test unit pushed from an rc must say so.
        """
        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git(described="v0.2.0-rc1")):
            assert await satellite_service.get_server_version() == "v0.2.0-rc1"


class TestSnapclientUpdateOutcome:
    """`update_in_progress` going false says the attempt ended, not that it worked.

    Reporting success there tells the UI a satellite was updated when it still
    runs the old binary — the fleet then looks current and nobody retries.
    """

    async def test_a_version_that_did_not_move_is_a_failed_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": "0.27.0", "running": True}},
            post_payload={"status": "success", "started": True, "target_version": SNAPCLIENT_TARGET},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert satellite.posts == ["http://192.168.1.153:8001/update"]
        assert result["success"] is False
        assert "0.27.0" in result["error"] and "0.28.0" in result["error"]

    async def test_the_version_the_satellite_targeted_is_the_one_required(self, satellite_service):
        """The update is done when the satellite reports the version asked for.

        `update_in_progress` going false only says the attempt ended; the
        version is the only thing that separates an install from a rollback."""
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": "0.28.0", "running": True}},
            post_payload={"status": "success", "started": True, "target_version": SNAPCLIENT_TARGET},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is True
        assert result["new_version"] == "0.28.0"


    async def test_a_satellite_already_on_the_target_is_not_a_failure(self, satellite_service):
        """`started: false` on a matching version is a no-op, and the row must heal.

        The satellite answers that when the version this call named is the one
        it already runs — a stale row, a second device that just ran it. Calling
        that a failure was self-perpetuating: the UI refetches the inventory only
        on success, so the row kept offering the button and every press failed
        the same way until the page was reloaded.
        """
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": SNAPCLIENT_TARGET, "running": True}},
            post_payload={
                "status": "success", "started": False, "message": "Already up to date",
                "current_version": SNAPCLIENT_TARGET, "target_version": SNAPCLIENT_TARGET,
            },
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is True
        assert result["new_version"] == SNAPCLIENT_TARGET

    async def test_a_refusal_on_another_version_stays_a_failure(self, satellite_service):
        """Only the matching version reads as a no-op — anything else is a refusal.

        A satellite that declined for its own reason has not installed what the
        row named, and saying otherwise would leave the fleet looking current.
        """
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": "0.27.0", "running": True}},
            post_payload={
                "status": "success", "started": False, "message": "Busy",
                "current_version": "0.27.0", "target_version": SNAPCLIENT_TARGET,
            },
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False
        assert result["error"] == "Busy"


class TestCamillaDspUpdateOutcome:
    """Same gate on the DSP binary: a satellite left on the old CamillaDSP is a
    speaker whose EQ pipeline silently differs from every other."""

    async def test_a_version_that_did_not_move_is_a_failed_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": "3.0.0"}, "snapclient": {"version": "0.28.0"}},
            post_payload={"status": "success", "started": True, "target_version": CAMILLADSP_TARGET},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert result["success"] is False
        assert "3.0.0" in result["error"] and "3.1.0" in result["error"]

    async def test_a_version_that_moved_is_a_successful_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": "3.1.0"}, "snapclient": {"version": "0.28.0"}},
            post_payload={"status": "success", "started": True, "target_version": CAMILLADSP_TARGET},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert result["success"] is True
        assert result["new_version"] == "3.1.0"


    async def test_a_satellite_already_on_the_target_is_not_a_failure(self, satellite_service):
        """Same reading of `started: false` as the snapclient update."""
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": CAMILLADSP_TARGET}, "snapclient": {"version": "0.28.0"}},
            post_payload={
                "status": "success", "started": False, "message": "Already up to date",
                "current_version": CAMILLADSP_TARGET, "target_version": CAMILLADSP_TARGET,
            },
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp(
                "dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None
            )

        assert result["success"] is True
        assert result["new_version"] == CAMILLADSP_TARGET


@pytest.mark.parametrize("push, status_key, target", [
    pytest.param("update_satellite", "snapclient", SNAPCLIENT_TARGET, id="snapclient"),
    pytest.param("update_satellite_camilladsp", "camilladsp", CAMILLADSP_TARGET, id="camilladsp"),
])
class TestProgramPushCarriesTheDigest:
    """The satellite has no manifest: the digest its root installer checks the
    snapclient .deb or the CamillaDSP archive against is the one this push puts
    in the body, beside the version. A push that dropped it would have every
    speaker install as root, unchecked, with nothing on the server to say so.
    The route's choice of digest is pinned in test_programs_routes.py; this is
    the leg from the service to the wire.
    """

    async def test_the_digest_named_is_the_digest_sent(self, satellite_service, push, status_key, target):
        digest = hashlib.sha256(f"{status_key} {target}".encode()).hexdigest()
        satellite = _FakeSatellite(
            status_payload={status_key: {"version": target}},
            post_payload={"status": "success", "started": True, "target_version": target},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            await getattr(satellite_service, push)("dc:a6:32:7e:d3:43", target, digest)

        assert [body["sha256"] for body in satellite.bodies] == [digest], satellite.bodies

    async def test_a_version_past_the_manifest_is_sent_with_a_null_digest(
            self, satellite_service, push, status_key, target):
        """The key travels even with no digest: the satellite requires it, so a
        push without it would be a 422, and null is what says "past the manifest"."""
        satellite = _FakeSatellite(
            status_payload={status_key: {"version": target}},
            post_payload={"status": "success", "started": True, "target_version": target},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            await getattr(satellite_service, push)("dc:a6:32:7e:d3:43", target, None)

        assert len(satellite.bodies) == 1 and "sha256" in satellite.bodies[0], satellite.bodies
        assert satellite.bodies[0]["sha256"] is None


class TestAppUpdateAvailableFlag:
    """`app_update_available` is the only thing that puts the update button in
    UpdateManager.vue, and it is decided on the payload — never on the version
    string the row displays beside it.

    Both halves ship from one commit, so the two agree whenever a satellite took
    the server's push. They part on the case that matters: a release that did
    not touch `milo-client/` leaves every satellite's payload correct while its
    release is behind, and a button lit there restarts a speaker to deploy bytes
    the unit already has.
    """

    @pytest.fixture
    def satellites(self):
        return [{
            "mac_id": "dc:a6:32:7e:d3:43",
            "hostname": "milo-client",
            "display_name": "Canapé",
            "ip": "192.168.1.153",
            "snapclient_version": "0.28.0",
            "app_version": SERVER_VERSION,
            "app_payload": SERVER_PAYLOAD,
            "camilladsp_version": "3.0.0",
            "online": True,
            "uptime": 1000,
            "snapclient_running": True,
        }]

    @pytest.fixture
    def client(self, satellites):
        def _build(server_payload, server_version=SERVER_VERSION):
            update_service = Mock()
            update_service.get_latest_github_version = AsyncMock(
                return_value={"status": "success", "version": "0.28.0"}
            )
            satellite_service = Mock()
            satellite_service.list_satellites = AsyncMock(return_value=satellites)
            satellite_service.get_client_payload_version = AsyncMock(return_value=server_payload)
            satellite_service.get_server_version = AsyncMock(return_value=server_version)

            app = FastAPI()
            app.include_router(create_programs_router(update_service, satellite_service, Mock()))
            return TestClient(app)

        return _build

    def _row(self, client, server_payload, server_version=SERVER_VERSION):
        response = client(server_payload, server_version).get("/api/programs/satellites")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1, "the fixture satellite must survive the route"
        return body["satellites"][0]

    def _flag(self, client, server_payload, server_version=SERVER_VERSION):
        return self._row(client, server_payload, server_version)["app_update_available"]

    def test_the_same_payload_on_both_sides_offers_nothing(self, client):
        assert self._flag(client, SERVER_PAYLOAD) is False

    def test_a_satellite_behind_the_server_is_offered_the_update(self, client, satellites):
        """Offline while the server updated — the one case the button is for."""
        satellites[0]["app_payload"] = "0ae3872"

        assert self._flag(client, SERVER_PAYLOAD) is True

    def test_a_satellite_that_never_updated_is_offered_the_update(self, client, satellites):
        """No payload file yet: it runs whatever it was installed with."""
        satellites[0]["app_payload"] = None

        assert self._flag(client, SERVER_PAYLOAD) is True

    def test_a_server_that_cannot_fingerprint_itself_offers_nothing(self, client, satellites):
        """`git log` failing must not light up every satellite at once."""
        satellites[0]["app_payload"] = "0ae3872"

        assert self._flag(client, None) is False

    def test_a_release_that_did_not_touch_the_client_tree_offers_nothing(
            self, client, satellites):
        """The whole reason the two values are separate. The satellite shows an
        older version and its code is identical — pushing would restart a
        speaker in an occupied room to deploy bytes it already has.
        """
        satellites[0]["app_version"] = "v0.1.0"

        assert self._flag(client, SERVER_PAYLOAD) is False

    def test_the_row_carries_the_version_to_display_and_not_the_payload(self, client):
        """What the screen prints is the server's own version string. A payload
        hash there is a fourth version scheme on one panel.
        """
        row = self._row(client, SERVER_PAYLOAD)

        assert row["app_version"] == SERVER_VERSION
        assert row["server_version"] == SERVER_VERSION

    def _snapclient_flag(self, client):
        body = client(SERVER_PAYLOAD).get("/api/programs/satellites").json()
        return body["satellites"][0]["update_available"]

    def test_a_satellite_above_the_target_is_offered_the_way_back(self, client, satellites):
        """The server sends versions in both directions, so the row must too.

        Ending a trial of an unvalidated snapclient lowers the fleet's target;
        asking only "is the target newer" leaves the satellite that took the
        trial reading "up to date" on a version the server no longer runs, with
        its button disabled and nothing able to bring it back.
        """
        satellites[0]["snapclient_version"] = "0.29.0"   # the target is 0.28.0

        assert self._snapclient_flag(client) is True

    def test_a_satellite_already_at_the_target_is_offered_nothing(self, client, satellites):
        """Restarting snapclient to install what is already there cuts a room."""
        satellites[0]["snapclient_version"] = "0.28.0"

        assert self._snapclient_flag(client) is False


class TestSatelliteCheckFailed:
    """A satellite's snapclient and CamillaDSP rows say when GitHub did not answer.

    `update_available: False` is what the read of a failed GitHub check left
    too, so without `check_failed` UpdateManager.vue drew both rows "up to date"
    while the local Multiroom row, fed by the same read, said it could not
    check — and an update for the speaker stayed invisible until the next read.
    """

    @pytest.fixture
    def satellites(self):
        return [{
            "mac_id": "dc:a6:32:7e:d3:43",
            "snapclient_version": "0.28.0",
            "app_version": SERVER_VERSION,
            "app_payload": SERVER_PAYLOAD,
            "camilladsp_version": "3.0.0",
            "online": True,
        }]

    def _row(self, satellites, answers):
        """The satellite's row, GitHub answering `answers[program_key]`."""
        update_service = Mock()
        update_service.get_latest_github_version = AsyncMock(side_effect=lambda key: answers[key])
        satellite_service = Mock()
        satellite_service.list_satellites = AsyncMock(return_value=satellites)
        satellite_service.get_client_payload_version = AsyncMock(return_value=SERVER_PAYLOAD)
        satellite_service.get_server_version = AsyncMock(return_value=SERVER_VERSION)
        app = FastAPI()
        app.include_router(create_programs_router(update_service, satellite_service, Mock()))
        body = TestClient(app).get("/api/programs/satellites").json()
        assert body["status"] == "success" and body["count"] == 1, body
        return body["satellites"][0]

    def test_a_snapclient_read_that_failed_is_said(self, satellites):
        row = self._row(satellites, {
            "multiroom": {"status": "error", "message": "rate limited"},
            "camilladsp": {"status": "success", "version": "3.0.0"},
        })

        assert row["update_available"] is False
        assert row["check_failed"] is True
        assert row["camilladsp_check_failed"] is False

    def test_a_camilladsp_read_that_failed_is_said(self, satellites):
        row = self._row(satellites, {
            "multiroom": {"status": "success", "version": "0.28.0"},
            "camilladsp": {"status": "error", "message": "offline"},
        })

        assert row["camilladsp_update_available"] is False
        assert row["camilladsp_check_failed"] is True
        assert row["check_failed"] is False

    def test_an_answer_with_nothing_newer_is_not_a_failure(self, satellites):
        """Up to date stays an answer GitHub gave, never the failure's flag."""
        row = self._row(satellites, {
            "multiroom": {"status": "success", "version": "0.28.0"},
            "camilladsp": {"status": "success", "version": "3.0.0"},
        })

        assert (row["update_available"], row["check_failed"]) == (False, False)
        assert (row["camilladsp_update_available"], row["camilladsp_check_failed"]) == (False, False)


class TestAppUpdateOutcome:
    """What counts as "the satellite is running the new app".

    Its payload: a satellite reports the one its process read when it started,
    so the pushed payload answers only once the restart has landed. A restart
    that never lands — a unit file the same update just made invalid, a masked
    unit — leaves the old payload in place, which the fleet then pushes again.
    """

    @pytest.fixture
    def client_tree(self, tmp_path):
        """Enough of milo-client/ for the real tarball step to run."""
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        return tree

    async def _push(self, service, satellite, client_tree):
        with _patch_satellite(satellite), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
             patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git()):
            return await service.update_satellite_app("dc:a6:32:7e:d3:43")

    async def test_a_satellite_that_restarted_into_it_is_a_successful_update(
        self, satellite_service, client_tree
    ):
        """Sanity floor: without this the failure test below passes on any bug."""
        satellite = _FakeSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
            status_after_post={"app": {"payload": SERVER_PAYLOAD, "version": SERVER_VERSION, "started_at": 1200},
                               "snapclient": {"version": "0.28.0"}},
        )

        result = await self._push(satellite_service, satellite, client_tree)

        assert satellite.posts == ["http://192.168.1.153:8001/app/update"]
        assert result["success"] is True
        assert result["new_version"] == SERVER_PAYLOAD

    async def test_a_satellite_that_keeps_its_old_payload_never_restarted(
        self, satellite_service, client_tree
    ):
        """The 200 to the POST is the deploy succeeding, so a payload that never
        moves is a restart that never landed. Read as a timeout, the UI offered
        a retry for a unit that needs somebody to look at why it stays put."""
        satellite = _FakeSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
        )

        result = await self._push(satellite_service, satellite, client_tree)

        assert satellite.posts == ["http://192.168.1.153:8001/app/update"]
        assert result["success"] is False
        assert "never restarted" in result["error"]

    class _PinningSatellite(_FakeSatellite):
        """A satellite that pins the secret its admission hands over, and
        refuses a push whose form carries any other (milo-client/app/server_secret.py)."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pinned = None

        def put(self, url, headers=None, **kwargs):
            assert url.endswith("/api/system/server-secret"), url
            self.pinned = self.pinned or headers[SATELLITE_SECRET_HEADER]
            return _FakeResponse(200, {"status": "success", "data": {"changed": True}})

        def post(self, url, data=None, **kwargs):
            if data.fields.get(SATELLITE_SECRET_FIELD) != self.pinned:
                self.posts.append(url)
                return _FakeResponse(403, {"detail": "Refused: it carries another server's secret"})
            return super().post(url, **kwargs)

    @pytest.fixture
    def recorded_forms(self, monkeypatch):
        """aiohttp's form, keeping what each text field carried."""
        class _Form(aiohttp.FormData):
            def add_field(self, name, value, **kwargs):
                self.fields = getattr(self, "fields", {})
                self.fields[name] = value if isinstance(value, str) else None
                super().add_field(name, value, **kwargs)

        monkeypatch.setattr(aiohttp, "FormData", _Form)

    def _pinning(self):
        return self._PinningSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
            status_after_post={"app": {"payload": SERVER_PAYLOAD, "version": SERVER_VERSION, "started_at": 1200},
                               "snapclient": {"version": "0.28.0"}},
        )

    async def test_the_push_carries_the_secret_its_admission_handed_over(
        self, satellite_service, satellite_secret, client_tree, recorded_forms,
    ):
        """The tarball is installed as root: a satellite takes it from the server it pinned only."""
        satellite = self._pinning()
        with _patch_satellite(satellite):
            await satellite_secret.hand_over("dc:a6:32:7e:d3:43", "192.168.1.153")

        result = await self._push(satellite_service, satellite, client_tree)

        assert result["verdict"] == "committed", result

    async def test_a_satellite_that_pinned_another_server_refuses_the_push(
        self, satellite_service, client_tree, recorded_forms, tmp_path,
    ):
        """And says so on the row: a 403 is a refusal, nothing was installed."""
        satellite = self._pinning()
        with _patch_satellite(satellite):
            await SatelliteSecret(key_file=tmp_path / "another-key").hand_over("dc:a6:32:7e:d3:43", "192.168.1.153")

        result = await self._push(satellite_service, satellite, client_tree)

        assert result["verdict"] == "rejected"
        assert "HTTP 403" in result["error"]

    async def test_an_app_push_reaches_a_satellite_the_registry_holds_offline(self, client_tree, satellite_secret):
        """The update-app route and the fleet push both land here, and in direct
        mode nothing admits a client: the push answered "not found or offline"
        to a satellite whose API was up, without ever asking it."""
        registry = await _registry_loaded_from({"dc:a6:32:7e:d3:43": ("192.168.1.153", "Canapé")})
        service = SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                         settings_service=_MemorySettings(), satellite_secret=satellite_secret)
        satellite = _FakeSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
            status_after_post={"app": {"payload": SERVER_PAYLOAD, "version": SERVER_VERSION, "started_at": 1200},
                               "snapclient": {"version": "0.28.0"}},
        )

        result = await self._push(service, satellite, client_tree)

        assert satellite.posts == ["http://192.168.1.153:8001/app/update"]
        assert result["success"] is True


# --------------------------------------------------------------------------- #
# No test in this file may reach a real satellite or spawn a real process
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def never_a_real_satellite():
    """Two doubles, both of which must FAIL rather than be spied on.

    The conftest's network guard refuses a connect off this host, and both
    satellites are off this host — but a refused connect leaves aiohttp raising
    inside a `try` this service catches, so a leak stays green. Here the session
    itself raises, which names the test that tried. The spawn double covers
    `git -C <repo> describe`, whose repo is the live checkout.
    """
    def _refuse_session(*a, **k):
        raise AssertionError("a real aiohttp session was opened towards a satellite")

    async def _refuse_spawn(program, *args, **kwargs):
        raise AssertionError(
            f"a real process was spawned: {program} {' '.join(map(str, args))}"
        )

    with patch("backend.core.updates.satellite.aiohttp.ClientSession",
               side_effect=_refuse_session), \
            patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                  new=_refuse_spawn):
        yield


class TestDiscoverSatellites:
    """The list every satellite action starts from — an update is dispatched by
    `mac_id` against what this returned."""

    @staticmethod
    def _client(**kw):
        # `name` is set after construction on purpose — see the fixture above.
        defaults = dict(is_local=False, ip="192.168.1.153", online=True,
                        host="milo-client")
        display = kw.pop("name", "Canapé")
        defaults.update(kw)
        client = Mock(**defaults)
        client.name = display
        return client

    @staticmethod
    def _service(clients):
        registry = Mock()
        registry.get_all_clients = Mock(return_value=clients)
        return SatelliteUpdateService(snapcast_service=Mock(),
                                      client_registry_service=registry,
                                      settings_service=_MemorySettings())

    STATUS = {
        "snapclient": {"version": "0.31.0", "running": True},
        "uptime": 4242,
        "app": {"version": "v0.2.0", "payload": "deadbee", "started_at": 1787000000},
        "camilladsp": {"version": "3.0.1"},
    }

    async def test_an_online_satellite_is_described_from_the_registry_and_its_api(self):
        service = self._service({"dc:a6:32:7e:d3:43": self._client()})
        with _patch_satellite(_FakeSatellite(self.STATUS)):
            found = await service.discover_satellites()

        assert found == [{
            "mac_id": "dc:a6:32:7e:d3:43",
            "hostname": "milo-client",
            "display_name": "Canapé",
            "ip": "192.168.1.153",
            "snapclient_version": "0.31.0",
            "app_version": "v0.2.0",
            "app_payload": "deadbee",
            "app_trial": False,
            "app_last_update": None,
            "app_started_at": 1787000000,
            "camilladsp_version": "3.0.1",
            "online": True,
            "uptime": 4242,
            "snapclient_running": True,
        }]

    async def test_the_local_client_is_never_probed(self):
        """The server's own snapclient has no `:8001` listener — the satellite
        app is installed on satellites only. Probing it costs a 3 s timeout on
        every refresh of the programs panel."""
        service = self._service({"aa:bb:cc:dd:ee:ff": self._client(is_local=True)})
        with _patch_satellite(_FakeSatellite(self.STATUS)):
            assert await service.discover_satellites() == []

    async def test_an_offline_client_is_never_probed(self):
        """A speaker that is unplugged answers nothing; the timeout is what made
        the panel take seconds to open with one satellite off."""
        service = self._service({"dc:a6:32:7e:d3:43": self._client(online=False)})
        with _patch_satellite(_FakeSatellite(self.STATUS)):
            assert await service.discover_satellites() == []

    async def test_a_client_the_registry_has_no_address_for_is_never_probed(self):
        """`http://None:8001/status` is a DNS lookup for the string "None"."""
        service = self._service({"dc:a6:32:7e:d3:43": self._client(ip=None)})
        with _patch_satellite(_FakeSatellite(self.STATUS)):
            assert await service.discover_satellites() == []

    async def test_a_registry_with_nothing_in_it_opens_no_session(self):
        """The early return is what keeps the autouse refusal above quiet: with
        no candidate there is nothing to probe, and `asyncio.gather` of an empty
        list would still have opened one."""
        assert await self._service({}).discover_satellites() == []

    async def test_a_satellite_that_refuses_the_probe_is_left_out(self):
        """It is in the registry because snapcast sees its audio client, but the
        satellite *app* may be down or mid-update. Listing it anyway offers an
        update button that answers "not found or offline"."""
        service = self._service({"dc:a6:32:7e:d3:43": self._client()})

        class _Down(_FakeSatellite):
            def get(self, url, **kwargs):
                return _FakeResponse(503, {})

        with _patch_satellite(_Down(self.STATUS)):
            assert await service.discover_satellites() == []

    async def test_one_satellite_timing_out_does_not_hide_the_other(self):
        """One unreachable speaker must not empty the list for the ones that are
        up — the panel would then offer no update at all.

        Note for whoever mutates this: the `return_exceptions=True` on the
        gather and the `isinstance(result, Exception)` arm below it are both
        inert, because `_check_satellite_api` is `@handle_errors(default=…)` and
        cannot raise. What actually carries this case is the `online` check."""
        service = self._service({
            "dc:a6:32:7e:d3:43": self._client(ip="192.168.1.153", name="Canapé"),
            "d8:3a:dd:68:e7:e4": self._client(ip="192.168.1.60", name="Bureau"),
        })

        class _OneDead(_FakeSatellite):
            def get(self, url, **kwargs):
                if "192.168.1.60" in url:
                    raise OSError("No route to host")
                return _FakeResponse(200, TestDiscoverSatellites.STATUS)

        with _patch_satellite(_OneDead(self.STATUS)):
            found = await service.discover_satellites()

        assert [s["display_name"] for s in found] == ["Canapé"]

    async def test_a_satellite_with_no_name_falls_back_to_its_mac(self):
        """The name is what the update dialog shows; an empty one would offer a
        button labelled with nothing."""
        service = self._service({"dc:a6:32:7e:d3:43": self._client(name=None)})
        with _patch_satellite(_FakeSatellite(self.STATUS)):
            found = await service.discover_satellites()

        assert found[0]["display_name"] == "dc:a6:32:7e:d3:43"


class TestFleetPush:
    """What a Milō update does to the satellites.

    The push itself is `update_satellite_app`, tested above. What is new here is
    the decision: which satellites it is called for, and what becomes of what it
    answers. Both matter on hardware — a satellite pushed for nothing restarts a
    speaker in an occupied room, and a satellite skipped in silence runs client
    code older than the server driving it, which is the one thing the contract
    test cannot catch because it holds per commit, not per unit.
    """

    @staticmethod
    def _client(ip, name, is_local=False, other_server=False):
        # `name` is set after construction: passing it to Mock() names the mock.
        client = Mock(is_local=is_local, ip=ip, online=True, host="milo-client",
                      other_server=other_server)
        client.name = name
        return client

    @staticmethod
    def _status(app_payload, app_version=SERVER_VERSION):
        return {
            "snapclient": {"version": "0.35.0", "running": True},
            "uptime": 4242,
            "app": {"payload": app_payload, "version": app_version,
                    "started_at": 1787000000},
            "camilladsp": {"version": "4.1.3"},
        }

    @pytest.fixture
    def fleet(self):
        """Two satellites: Canapé behind the server, Bureau already on it."""
        registry = Mock()
        registry.get_all_clients = Mock(return_value={
            "dc:a6:32:7e:d3:43": self._client("192.168.1.153", "Canapé"),
            "d8:3a:dd:68:e7:e4": self._client("192.168.1.60", "Bureau"),
        })
        service = SatelliteUpdateService(snapcast_service=Mock(),
                                         client_registry_service=registry,
                                         settings_service=_MemorySettings())
        service.pushed = []

        async def _push(mac_id):
            service.pushed.append(mac_id)
            return service.push_results.get(mac_id, service.push_result)

        service.update_satellite_app = _push
        service.push_result = {"success": True, "verdict": "committed"}
        service.push_results = {}
        return service

    async def _push_to(self, service, by_ip):
        with patch("backend.core.updates.satellite.aiohttp.ClientSession",
                   return_value=_FakeFleet(by_ip)), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()):
            return await service.push_client_app_to_fleet()

    async def test_only_the_satellite_behind_the_server_is_pushed(self, fleet):
        """The other one would be restarted to deploy bytes it already has."""
        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": self._status(SERVER_PAYLOAD),
        })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43"]
        assert left_behind == []

    async def test_a_satellite_that_does_not_answer_is_reported_and_the_rest_still_get_it(self, fleet):
        """Unplugged during the update: naming it is all that is left here, since
        its admission is what pushes it the app once it is back."""
        left_behind = await self._push_to(fleet, {
            "192.168.1.60": self._status("0ae3872", "v0.1.0"),
        })

        assert fleet.pushed == ["d8:3a:dd:68:e7:e4"]
        assert left_behind == ["Canapé"]

    async def test_the_fleet_push_probes_a_satellite_the_registry_holds_offline(self, fleet, caplog):
        """`online` is Snapcast's admission, not the satellite's API answering.
        In direct mode nothing admits a client, so after the reboot that ends
        every Milō update the next one skipped the whole fleet: each speaker
        logged as "not answering its API" while its :8001 answered, and left on
        client code a release behind the server driving it."""
        fleet.client_registry_service = await _registry_loaded_from({
            "dc:a6:32:7e:d3:43": ("192.168.1.153", "Canapé"),
            "d8:3a:dd:68:e7:e4": ("192.168.1.60", "Bureau"),
        })
        assert not any(c.online for c in fleet.client_registry_service.get_all_clients().values())

        with caplog.at_level(logging.ERROR, logger="backend.core.updates.satellite"):
            left_behind = await self._push_to(fleet, {
                "192.168.1.153": self._status("0ae3872", "v0.1.0"),
                "192.168.1.60": self._status("0ae3872", "v0.1.0"),
            })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43", "d8:3a:dd:68:e7:e4"]
        assert left_behind == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_a_push_that_failed_is_reported_and_not_raised(self, fleet):
        """It runs past the point where the app can still be rolled back: a
        raise here would abort a Milō update that has already succeeded. And a
        refusal restarted nothing, so it holds nobody back."""
        fleet.push_result = {"success": False, "verdict": "rejected",
                             "error": "satellite rejected update"}

        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": self._status("0ae3872", "v0.1.0"),
        })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43", "d8:3a:dd:68:e7:e4"]
        assert left_behind == ["Canapé", "Bureau"]

    @pytest.mark.parametrize("verdict", ["rolled_back", "did_not_return"])
    async def test_a_satellite_that_did_not_take_the_release_holds_back_the_rest(
            self, fleet, verdict):
        """A release that breaks one room breaks them all: the first satellite
        is the canary, and the rest keep the app they run."""
        fleet.push_results["dc:a6:32:7e:d3:43"] = {
            "success": False, "verdict": verdict, "error": "x"}

        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": self._status("0ae3872", "v0.1.0"),
        })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43"]
        assert left_behind == [
            "Canapé", f"Bureau: held back, Canapé did not take {SERVER_PAYLOAD}"]

    async def test_each_push_is_announced_with_the_count_of_the_pushes_meant(self, fleet):
        """The banner names the room about to go quiet, and "k of n" counts the
        speakers this push means to restart — not the one already on the tree,
        and not one held back by an earlier failure, which restarts nothing."""
        announced = []

        async def on_push(name, index, total):
            announced.append((name, index, total))

        async def push_with(statuses, **results):
            fleet.pushed.clear()
            announced.clear()
            fleet.push_results = results
            with patch("backend.core.updates.satellite.aiohttp.ClientSession",
                       return_value=_FakeFleet(statuses)), \
                    patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                          _fake_git()):
                await fleet.push_client_app_to_fleet(on_push=on_push)

        behind = self._status("0ae3872", "v0.1.0")
        await push_with({"192.168.1.153": behind, "192.168.1.60": self._status(SERVER_PAYLOAD)})
        assert announced == [("Canapé", 1, 1)]

        await push_with({"192.168.1.153": behind, "192.168.1.60": behind})
        assert announced == [("Canapé", 1, 2), ("Bureau", 2, 2)]

        await push_with({"192.168.1.153": behind, "192.168.1.60": behind}, **{
            "dc:a6:32:7e:d3:43": {"success": False, "verdict": "rolled_back", "error": "x"}})
        assert announced == [("Canapé", 1, 2)]

    async def test_a_satellite_that_already_went_back_from_this_payload_is_not_pushed_it(
            self, fleet):
        """Pushed again, it would cut its room for the same crash; the server's
        record and the satellite's own both say so."""
        fleet.settings_service = _MemorySettings({"dc:a6:32:7e:d3:43": {
            "payload": SERVER_PAYLOAD, "verdict": "rolled_back", "detail": "x", "at": 1}})
        bureau = self._status("0ae3872", "v0.1.0")
        bureau["app"]["last_update"] = {"payload": SERVER_PAYLOAD, "outcome": "rolled_back",
                                        "detail": "x", "at": 1}

        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": bureau,
        })

        assert fleet.pushed == []
        assert len(left_behind) == 2
        assert all(SERVER_PAYLOAD in entry for entry in left_behind)

    async def test_a_rollback_of_an_earlier_payload_does_not_block_this_one(self, fleet):
        fleet.settings_service = _MemorySettings({"dc:a6:32:7e:d3:43": {
            "payload": "1111111", "verdict": "rolled_back", "detail": "x", "at": 1}})

        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": self._status(SERVER_PAYLOAD),
        })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43"]
        assert left_behind == []

    async def test_a_satellite_that_answers_another_server_is_not_pushed_nor_left_behind(
            self, fleet, caplog):
        """It refuses every push with a 403 until it is unplugged and plugged
        back in, and Settings › Multiroom already names it. Pushed anyway, each
        Milō update built and sent it a tarball for nothing and wrote an ERROR,
        a failure on its row and "left behind" in the update's journal, while
        its admission skipped it with an info line — two rules for one unit."""
        fleet.client_registry_service.get_all_clients.return_value = {
            "dc:a6:32:7e:d3:43": self._client("192.168.1.153", "Canapé", other_server=True),
            "d8:3a:dd:68:e7:e4": self._client("192.168.1.60", "Bureau"),
        }

        with caplog.at_level(logging.INFO, logger="backend.core.updates.satellite"):
            left_behind = await self._push_to(fleet, {
                "192.168.1.153": self._status("0ae3872", "v0.1.0"),
                "192.168.1.60": self._status("0ae3872", "v0.1.0"),
            })

        assert fleet.pushed == ["d8:3a:dd:68:e7:e4"]
        assert left_behind == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "dc:a6:32:7e:d3:43" not in await fleet.get_app_update_failures()
        assert any("Canapé: answers another server" in r.getMessage() for r in caplog.records)

    async def test_the_local_client_is_not_a_satellite(self, fleet):
        """The server is a client of its own snapserver. Pushing the tarball to
        itself would deploy the satellite app over the machine that built it."""
        fleet.client_registry_service.get_all_clients.return_value = {
            "aa:bb:cc:dd:ee:ff": self._client("192.168.1.10", "Milō", is_local=True),
        }

        left_behind = await self._push_to(fleet, {"192.168.1.10": self._status("0ae3872", "v0.1.0")})

        assert fleet.pushed == []
        assert left_behind == []

    async def test_nothing_is_pushed_when_the_payload_version_cannot_be_read(self, fleet):
        """Without it there is nothing to compare against and nothing to stamp
        the tarball with — pushing anyway writes a version file no later
        comparison can use."""
        with patch("backend.core.updates.satellite.aiohttp.ClientSession",
                   return_value=_FakeFleet({"192.168.1.153": self._status("0ae3872", "v0.1.0")})), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      AsyncMock(return_value=_mock_proc(b"", returncode=128))):
            left_behind = await fleet.push_client_app_to_fleet()

        assert fleet.pushed == []
        assert left_behind and "payload version" in left_behind[0]


class TestUpdateSatelliteDispatch:
    """`POST /api/programs/satellites/{mac}/update` lands here. Every arm below
    is a different sentence in the UI's banner, and the satellite is the only
    thing that knows which one is true."""

    STATUS = TestDiscoverSatellites.STATUS

    async def test_an_unknown_or_offline_satellite_is_refused_before_any_post(
            self, satellite_service):
        """Posting to the wrong address would start an update on a speaker the
        user did not pick."""
        fake = _FakeSatellite(self.STATUS)
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite("00:00:00:00:00:00", SNAPCLIENT_TARGET, None)

        assert result["success"] is False
        assert "not found or offline" in result["error"]
        assert fake.posts == []

    async def test_a_satellite_that_declines_reports_its_own_message(self, satellite_service):
        """`started: false` is the legitimate already-up-to-date answer, not a
        transport failure — the satellite's own message is the only thing that
        distinguishes them."""
        fake = _FakeSatellite(self.STATUS, post_payload={
            "started": False, "message": "Already at the latest version"})
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False and result["verdict"] == "rejected"
        assert result["error"] == result["detail"] == "Already at the latest version"

    async def test_a_declined_update_with_no_message_still_says_something(self, satellite_service):
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False and result["verdict"] == "rejected"
        assert result["error"] == "Update failed"

    async def test_a_refused_post_is_reported_with_its_status(self, satellite_service):
        """A satellite mid-reboot answers 502 through its own nginx."""
        class _Refusing(_FakeSatellite):
            def post(self, url, **kwargs):
                self.posts.append(url)
                return _FakeResponse(502, {})

        with _patch_satellite(_Refusing(self.STATUS)):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False and result["verdict"] == "rejected"
        assert result["detail"].startswith("HTTP 502")

    async def test_a_transport_that_dies_mid_update_is_reported_not_raised(
            self, satellite_service, caplog):
        """The route turns this dict into a banner; an exception would reach the
        client as a 500 with no indication of which satellite failed. The error
        names the satellite, and is the failure's one ERROR line: the route
        logs it, so the service logs none of its own (two lines per failure
        read as two failures in errors.log)."""
        class _Dropping(_FakeSatellite):
            def post(self, url, **kwargs):
                raise OSError("Connection reset by peer")

        with _patch_satellite(_Dropping(self.STATUS)), caplog.at_level(logging.ERROR):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False
        assert "Connection reset" in result["error"]
        assert "dc:a6:32:7e:d3:43" in result["error"]
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_the_update_is_posted_to_the_satellites_own_api_port(self, satellite_service):
        """`CLIENT_API_PORT` is the satellite app's only listener; posting to 80
        reaches its nginx and 404s, which reads as "satellite refused"."""
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert fake.posts == [
            f"http://192.168.1.153:{satellite_service.satellite_api_port}/update"
        ]


class TestSatelliteUpdateRecoveryArms:
    """The arms every waiter and every push shares: a satellite that never
    answers, one that answers something unparsable, and one that refuses."""

    STATUS = TestDiscoverSatellites.STATUS

    async def test_a_satellite_that_stops_answering_mid_update_is_a_timeout(
            self, satellite_service):
        """The poll loop swallows every transport error and keeps going — a
        satellite rebooting into its new snapclient is *expected* to refuse
        connections for a few seconds. What must not happen is the loop dying on
        the first refusal and reporting a failure for an update that worked."""
        class _DiesAfterPost(_FakeSatellite):
            def get(self, url, **kwargs):
                if url.endswith("/update/status") and self.posts:
                    raise OSError("Connection refused")
                return super().get(url, **kwargs)

        fake = _DiesAfterPost(self.STATUS, post_payload={
            "started": True, "target_version": SNAPCLIENT_TARGET})
        with _patch_satellite(fake), patch("asyncio.sleep", new=AsyncMock()):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET, None)

        assert result["success"] is False
        assert result["verdict"] == "did_not_return"
        assert "timeout" in result["error"]

    async def test_a_camilladsp_update_is_dispatched_to_its_own_endpoint(
            self, satellite_service):
        """The satellite serves snapclient, CamillaDSP and app updates on three
        separate paths; the wrong one 404s and reads as "satellite refused"."""
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert fake.posts == [
            f"http://192.168.1.153:{satellite_service.satellite_api_port}/camilladsp/update"
        ]

    async def test_an_unknown_satellite_gets_no_camilladsp_push(self, satellite_service):
        fake = _FakeSatellite(self.STATUS)
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite_camilladsp("00:00:00:00:00:00", CAMILLADSP_TARGET, None)

        assert "not found or offline" in result["error"]
        assert fake.posts == []

    async def test_a_camilladsp_update_the_satellite_declined_carries_its_message(
            self, satellite_service):
        fake = _FakeSatellite(self.STATUS, post_payload={
            "started": False, "message": "CamillaDSP is already at 3.0.1"})
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert result["success"] is False and result["verdict"] == "rejected"
        assert result["error"] == "CamillaDSP is already at 3.0.1"

    async def test_a_refused_camilladsp_post_reports_its_status(self, satellite_service):
        class _Refusing(_FakeSatellite):
            def post(self, url, **kwargs):
                return _FakeResponse(500, {})

        with _patch_satellite(_Refusing(self.STATUS)):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert result["success"] is False and result["verdict"] == "rejected"
        assert result["detail"].startswith("HTTP 500")

    async def test_a_camilladsp_transport_failure_is_reported_not_raised(
            self, satellite_service, caplog):
        class _Dropping(_FakeSatellite):
            def post(self, url, **kwargs):
                raise OSError("No route to host")

        with _patch_satellite(_Dropping(self.STATUS)), caplog.at_level(logging.ERROR):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert "No route to host" in result["error"]
        assert "dc:a6:32:7e:d3:43" in result["error"]
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_a_camilladsp_satellite_that_stops_answering_is_a_timeout(
            self, satellite_service):
        class _DiesAfterPost(_FakeSatellite):
            def get(self, url, **kwargs):
                if self.posts:
                    raise OSError("Connection refused")
                return super().get(url, **kwargs)

        fake = _DiesAfterPost(self.STATUS, post_payload={
            "started": True, "target_version": CAMILLADSP_TARGET})
        with _patch_satellite(fake), patch("asyncio.sleep", new=AsyncMock()):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET, None)

        assert result["verdict"] == "did_not_return"
        assert "CamillaDSP update timeout" in result["error"]

    async def test_an_unknown_satellite_is_never_sent_a_tarball(self, satellite_service):
        """Building the tarball is a `git describe` plus tens of megabytes of
        compression; the lookup comes first so an offline satellite costs
        nothing."""
        fake = _FakeSatellite(self.STATUS)
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite_app("00:00:00:00:00:00")

        assert "not found or offline" in result["error"]
        assert fake.posts == []

    async def test_a_server_that_cannot_describe_itself_ships_nothing(
            self, satellite_service, caplog):
        """`_create_client_tarball` refuses without a version, and the version
        it stamps is what the satellite writes to its own version file — an
        unstamped push would make every later comparison meaningless."""
        with _patch_satellite(_FakeSatellite(self.STATUS)), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      AsyncMock(return_value=_mock_proc(b"", returncode=128))), \
                caplog.at_level(logging.ERROR):
            result = await satellite_service.update_satellite_app("dc:a6:32:7e:d3:43")

        assert result["success"] is False
        assert "Could not determine the milo-client payload version" in result["error"]

    async def test_a_missing_client_tree_is_refused_before_any_transfer(
            self, satellite_service, tmp_path):
        """A server whose checkout has no `milo-client/` would otherwise ship an
        empty archive and the satellite would extract nothing over its app."""
        with _patch_satellite(_FakeSatellite(self.STATUS)), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", tmp_path / "absent"), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()):
            result = await satellite_service.update_satellite_app("dc:a6:32:7e:d3:43")

        assert "milo-client directory not found" in result["error"]

    async def test_the_tarball_is_deleted_whatever_happened(
            self, satellite_service, tmp_path):
        """It is written under /tmp, which is tmpfs on this appliance. A failed
        push per satellite per attempt eats the RAM the audio path needs."""
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        made = []

        class _Refusing(_FakeSatellite):
            def post(self, url, **kwargs):
                return _FakeResponse(507, {})

        real_tarball = SatelliteUpdateService._create_client_tarball

        async def recording(self):
            path, version = await real_tarball(self)
            made.append(path)
            return path, version

        with _patch_satellite(_Refusing(self.STATUS)), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()), \
                patch.object(SatelliteUpdateService, "_create_client_tarball", recording):
            result = await satellite_service.update_satellite_app("dc:a6:32:7e:d3:43")

        assert result["success"] is False
        assert "507" in result["error"]
        assert made and not Path(made[0]).exists()

    async def test_a_satellite_refusing_connections_while_it_restarts_is_not_a_failure(
            self, satellite_service, tmp_path):
        """The waiter polls across the satellite's own restart, so a refused
        connection is the *expected* answer for a few seconds. A loop that gave
        up on the first one would report every successful app update as failed."""
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")

        class _RebootingThenBack(_FakeSatellite):
            def __init__(self):
                super().__init__(
                    status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                                    "snapclient": {"version": "0.28.0"}},
                    post_payload={},
                )
                self.gets = 0

            def get(self, url, **kwargs):
                self.gets += 1
                if self.posts and self.gets <= 3:
                    raise OSError("Connection refused")
                if self.posts:
                    self.status_payload = {
                        "app": {"payload": SERVER_PAYLOAD, "version": SERVER_VERSION, "started_at": 2000},
                        "snapclient": {"version": "0.28.0"},
                    }
                return super().get(url, **kwargs)

        fake = _RebootingThenBack()
        with _patch_satellite(fake), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()), \
                patch("asyncio.sleep", new=AsyncMock()):
            result = await satellite_service.update_satellite_app("dc:a6:32:7e:d3:43")

        assert result["success"] is True
        assert result["new_version"] == SERVER_PAYLOAD

    async def test_a_stray_bytecode_file_outside_pycache_is_not_shipped(
            self, satellite_service, tmp_path):
        """`__pycache__` and `.pyc` are two separate rules, and
        `TARBALL_EXCLUDE_PATTERNS` is a *set* — so which one catches a file
        inside `__pycache__` depends on iteration order. A `.pyc` left beside
        its source (an interrupted install, an old layout) is only caught by the
        extension rule, and the satellite would extract it over a module it no
        longer matches."""
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        (tree / "app" / "stale.pyc").write_bytes(b"\x00")

        with patch("backend.core.updates.satellite.MILO_CLIENT_DIR", tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()):
            tarball_path, _ = await satellite_service._create_client_tarball()

        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                members = [m.name for m in tar.getmembers()]
        finally:
            Path(tarball_path).unlink()

        assert "milo-client/app/main.py" in members
        assert not [m for m in members if m.endswith(".pyc")]


# --------------------------------------------------------------------------- #
# The verdict of one app push, read from the satellite's trial
# --------------------------------------------------------------------------- #

OLD_PAYLOAD = "0ae3872"
CANAPE = "dc:a6:32:7e:d3:43"
SALON = "dc:a6:32:7e:d3:44"


def _app(payload, started_at, *, trial=False, last=None, deploying=False):
    """The `app` block of a satellite's /status, as its new updater answers it."""
    return {"payload": payload, "version": "v", "started_at": started_at,
            "update_in_progress": deploying, "trial": trial, "last_update": last}


def _record(payload, outcome, at, detail=None):
    return {"payload": payload, "outcome": outcome, "detail": detail, "at": at}


class _TrialSatellite:
    """A satellite whose /status moves through `timeline`, one entry per poll
    after the POST (the last one repeats), an exception standing for a poll the
    unit did not answer — the restart, a crashloop.

    `before` is what it answers until the POST: the discovery the push starts
    from, and the record the waiter must not mistake for this push's answer.
    """

    def __init__(self, before, timeline, *, post_status=200, post_body=None,
                 post_raises=None, on_post=None):
        self.before = before
        self.timeline = list(timeline)
        self.post_status = post_status
        self.post_body = post_body
        self.post_raises = post_raises
        self.on_post = on_post
        self.posts = []
        self.polls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _answer(self, app):
        return _FakeResponse(200, {"app": app, "snapclient": {"version": "0.35.0"}})

    def get(self, url, **kwargs):
        if not self.posts:
            return self._answer(self.before)
        step = self.timeline[min(self.polls, len(self.timeline) - 1)]
        self.polls += 1
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(url)
        return self._answer(step)

    def post(self, url, **kwargs):
        self.posts.append(url)
        if self.on_post:
            self.on_post()
        if self.post_raises is not None:
            raise self.post_raises
        return _FakeResponse(self.post_status, self.post_body)


class TestAppPushVerdict:
    """What one push of the client app concludes from the satellite's trial.

    The satellite keeps the previous version until the new one has held
    HOLD_S, and its guard puts it back after the new one failed to start. The
    server only sees /status — `payload`, `trial`, `last_update` — and each
    conclusion it draws decides a row, a retry and whether the rest of the
    fleet gets the same release.
    """

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        return tree

    @pytest.fixture
    def push(self, satellite_service, client_tree):
        async def _push(satellite, service=None):
            with _patch_satellite(satellite), \
                    patch("asyncio.sleep", new_callable=AsyncMock), \
                    patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
                    patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                          _fake_git()):
                return await (service or satellite_service).update_satellite_app(CANAPE)
        return _push

    async def test_a_version_that_held_its_trial_is_committed(self, push):
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            OSError("Connection refused"),
            _app(SERVER_PAYLOAD, 2000, trial=True),
            _app(SERVER_PAYLOAD, 2000, trial=True),
            _app(SERVER_PAYLOAD, 2000, last=_record(SERVER_PAYLOAD, "committed", 2125)),
        ])

        result = await push(satellite)

        assert result["verdict"] == "committed"
        assert result["success"] is True
        assert satellite.polls == 4, "the trial was read as a final state"

    async def test_a_version_still_on_trial_is_not_a_success(self, push):
        """The new process answers with the new payload from its first second;
        reading that as success is how a crashloop two minutes later went
        unseen."""
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            _app(SERVER_PAYLOAD, 2000, trial=True),
        ])

        result = await push(satellite)

        assert result["verdict"] == "did_not_return"
        assert result["success"] is False

    async def test_a_version_the_guard_put_back_is_rolled_back_with_its_cause(self, push):
        cause = "ModuleNotFoundError: No module named 'zeroconf'"
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            OSError("Connection refused"),
            _app(SERVER_PAYLOAD, 2000, trial=True),
            OSError("Connection refused"),
            _app(OLD_PAYLOAD, 2100, last=_record(SERVER_PAYLOAD, "rolled_back", 2100, cause)),
        ])

        result = await push(satellite)

        assert result["verdict"] == "rolled_back"
        assert result["detail"] == cause
        assert result["success"] is False

    async def test_a_retry_is_not_answered_by_the_rollback_it_retries(self, push):
        """The satellite keeps its last record until the trial ends: a retry of
        a payload it went back from reads `rolled_back` for that payload while
        the new trial runs."""
        earlier = _record(SERVER_PAYLOAD, "rolled_back", 900, "ImportError: x")
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000, last=earlier), [
            _app(OLD_PAYLOAD, 1000, trial=True, last=earlier),
            _app(SERVER_PAYLOAD, 2000, trial=True, last=earlier),
            _app(SERVER_PAYLOAD, 2000, last=_record(SERVER_PAYLOAD, "committed", 2125)),
        ])

        result = await push(satellite)

        assert result["verdict"] == "committed"

    async def test_the_push_that_installs_the_guard_is_committed_without_a_trial(self, push):
        """That push is applied by the updater it replaces, which opens no trial
        and writes no record: the payload is all there is to read."""
        satellite = _TrialSatellite(
            {"payload": OLD_PAYLOAD, "version": "v", "started_at": 1000},
            [{"payload": SERVER_PAYLOAD, "version": "v", "started_at": 2000}],
        )

        result = await push(satellite)

        assert result["verdict"] == "committed"

    async def test_a_refusal_is_rejected_and_not_waited_on(self, push):
        satellite = _TrialSatellite(
            _app(OLD_PAYLOAD, 1000), [_app(OLD_PAYLOAD, 1000)],
            post_status=409,
            post_body='{"detail": "An update to 1234567 is still on trial"}',
        )

        result = await push(satellite)

        assert result["verdict"] == "rejected"
        assert "still on trial" in result["detail"]
        assert satellite.polls == 0

    async def test_a_satellite_that_never_answers_again_did_not_return(self, push):
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            ConnectionRefusedError("Connection refused"),
        ])

        result = await push(satellite)

        assert result["verdict"] == "did_not_return"
        assert result["detail"].startswith("ConnectionRefusedError")

    async def test_a_post_that_timed_out_is_followed_to_its_verdict(self, push):
        """pip on a Pi outlasts the POST's 120 s: the deploy is still running,
        and the restart that follows is the push's real answer."""
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            _app(OLD_PAYLOAD, 1000, deploying=True),
            _app(OLD_PAYLOAD, 1000, deploying=True),
            OSError("Connection refused"),
            _app(SERVER_PAYLOAD, 2000, trial=True),
            _app(SERVER_PAYLOAD, 2000, last=_record(SERVER_PAYLOAD, "committed", 2125)),
        ], post_raises=asyncio.TimeoutError())

        result = await push(satellite)

        assert result["verdict"] == "committed"

    async def test_a_deploy_longer_than_the_ceiling_is_still_waited_on(self, push):
        from backend.core.updates.satellite import APP_UPDATE_CEILING_S, APP_UPDATE_POLL_S

        polls = APP_UPDATE_CEILING_S // APP_UPDATE_POLL_S + 10
        satellite = _TrialSatellite(
            _app(OLD_PAYLOAD, 1000),
            [_app(OLD_PAYLOAD, 1000, deploying=True)] * polls
            + [_app(SERVER_PAYLOAD, 2000, last=_record(SERVER_PAYLOAD, "committed", 2125))],
            post_raises=asyncio.TimeoutError(),
        )

        result = await push(satellite)

        assert result["verdict"] == "committed"

    async def test_a_post_that_never_arrived_is_rejected_without_waiting_out_the_ceiling(
            self, push):
        """No answer, and a satellite that goes on exactly as before — never
        deploying, no trial, the same process: nothing restarted, so the fleet
        may go on, and waiting ten minutes on it would hold the Milō update."""
        import aiohttp

        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [_app(OLD_PAYLOAD, 1000)],
                                    post_raises=aiohttp.ServerDisconnectedError())

        result = await push(satellite)

        assert result["verdict"] == "rejected"
        assert "ServerDisconnectedError" in result["detail"]
        assert satellite.polls < 10

    async def test_a_refusal_is_one_line_however_long_the_satellite_answered(self, push):
        """A pip failure answers pages of resolver output; the row draws one raw
        line, and the record lands in settings.json and every diagnostic."""
        noise = "\n".join(f"  Collecting package-{i}" for i in range(400))
        cause = "ERROR: No matching distribution found for aiohttp>=99.0"
        body = json.dumps({"detail": f"pip install failed: {noise}\n{cause}\n"})
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [], post_status=500, post_body=body)

        result = await push(satellite)

        assert result["verdict"] == "rejected"
        assert result["detail"] == f"HTTP 500 - {cause}"

    async def test_an_error_while_following_the_push_is_not_a_refusal(
            self, satellite_service, push):
        """The satellite took the push and is restarting into it: an error on
        the server's side of the wait does not mean nothing restarted, and a
        `rejected` would let the fleet push go on to the next room."""
        registry = satellite_service.client_registry_service

        def _registry_breaks():
            registry.get_all_clients.side_effect = RuntimeError("registry reloading")

        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [_app(OLD_PAYLOAD, 1000)],
                                    on_post=_registry_breaks)

        result = await push(satellite)

        assert result["verdict"] == "did_not_return"
        assert "registry reloading" in result["detail"]

    async def test_a_satellite_that_comes_back_on_another_address_is_still_followed(
            self, satellite_service, push):
        """A restarted satellite may come back over its WiFi link: the address
        the push started from then answers nothing, for good."""
        client = satellite_service.client_registry_service.get_all_clients()[CANAPE]

        def _answer_by_address(url):
            if "192.168.1.153" in url:
                raise OSError("No route to host")
            return _FakeResponse(200, {"app": _app(
                SERVER_PAYLOAD, 2000, last=_record(SERVER_PAYLOAD, "committed", 2125))})

        def _moves():
            client.ip = "192.168.1.154"

        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [_answer_by_address],
                                    on_post=_moves)

        result = await push(satellite)

        assert result["verdict"] == "committed"


class TestAppFailureRecord:
    """A failure is remembered by the server until that satellite's next
    committed push: the satellite that failed may be the one that no longer
    answers, and its row is the only place that says why."""

    @pytest.fixture
    def settings(self, tmp_path, monkeypatch):
        from backend.core.settings import SettingsService

        service = SettingsService()
        monkeypatch.setattr(service, "settings_file", str(tmp_path / "settings.json"))
        return service

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        return tree

    async def _push(self, service, satellite, client_tree):
        with _patch_satellite(satellite), \
                patch("asyncio.sleep", new_callable=AsyncMock), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()):
            return await service.update_satellite_app(CANAPE)

    async def test_a_rollback_is_kept_across_a_restart_and_cleared_by_the_next_success(
            self, satellite_service, settings, client_tree, tmp_path, monkeypatch):
        satellite_service.settings_service = settings
        cause = "ModuleNotFoundError: No module named 'zeroconf'"
        await self._push(satellite_service, _TrialSatellite(_app(OLD_PAYLOAD, 1000), [
            _app(OLD_PAYLOAD, 2100, last=_record(SERVER_PAYLOAD, "rolled_back", 2100, cause)),
        ]), client_tree)

        from backend.core.settings import SettingsService
        reloaded = SettingsService()
        monkeypatch.setattr(reloaded, "settings_file", str(tmp_path / "settings.json"))
        satellite_service.settings_service = reloaded
        kept = await satellite_service.get_app_update_failures()
        assert set(kept) == {CANAPE}
        assert kept[CANAPE]["verdict"] == "rolled_back"
        assert kept[CANAPE]["payload"] == SERVER_PAYLOAD
        assert kept[CANAPE]["detail"] == cause
        assert kept[CANAPE]["refused"] is True, "a rollback is the satellite's own answer"

        await self._push(satellite_service, _TrialSatellite(
            _app(OLD_PAYLOAD, 2100, last=_record(SERVER_PAYLOAD, "rolled_back", 2100, cause)),
            [_app(SERVER_PAYLOAD, 3000, last=_record(SERVER_PAYLOAD, "committed", 3125))],
        ), client_tree)

        assert await satellite_service.get_app_update_failures() == {}

    async def test_a_push_that_reached_no_satellite_keeps_the_record_it_found(self):
        """A retry pressed on a satellite that stopped answering sent nothing:
        overwriting its record drops the traceback line its row exists for,
        and calling it did_not_return held back the rest of a fleet push."""
        cause = "ModuleNotFoundError: No module named 'zeroconf'"
        kept = {"payload": SERVER_PAYLOAD, "verdict": "rolled_back", "detail": cause, "at": 1}
        registry = Mock()
        registry.get_all_clients = Mock(return_value={})
        service = SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                         settings_service=_MemorySettings({CANAPE: kept}))

        result = await service.update_satellite_app(CANAPE)

        assert result["verdict"] == "rejected"
        assert await service.get_app_update_failures() == {CANAPE: kept}


class TestSatelliteRows:
    """GET /api/programs/satellites draws one row per satellite; a satellite
    holding a failed app push keeps its row while it does not answer."""

    def _service(self, failures):
        registry = Mock()
        canape = Mock(is_local=False, ip="192.168.1.153", online=True, host="milo-client")
        canape.name = "Canapé"
        bureau = Mock(is_local=False, ip="192.168.1.60", online=False, host="milo-client")
        bureau.name = "Bureau"
        registry.get_all_clients = Mock(return_value={
            CANAPE: canape, "d8:3a:dd:68:e7:e4": bureau,
        })
        return SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                      settings_service=_MemorySettings(failures))

    def _rows(self, service, answers=None):
        update_service = Mock()
        update_service.get_latest_github_version = AsyncMock(
            return_value={"status": "success", "version": "0.35.0"})
        app = FastAPI()
        app.include_router(create_programs_router(update_service, service, Mock()))
        with _patch_satellite(_TrialSatellite(answers or _app(SERVER_PAYLOAD, 1000), [])), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                      _fake_git()):
            body = TestClient(app).get("/api/programs/satellites").json()
        assert body["status"] == "success"
        return {row["mac_id"]: row for row in body["satellites"]}

    def test_an_offline_satellite_with_a_failed_push_keeps_its_row(self):
        failure = {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                   "detail": "ConnectionRefusedError: Connection refused", "at": 1787000000}
        rows = self._rows(self._service({"d8:3a:dd:68:e7:e4": failure}))

        assert set(rows) == {CANAPE, "d8:3a:dd:68:e7:e4"}
        bureau = rows["d8:3a:dd:68:e7:e4"]
        assert bureau["online"] is False
        assert bureau["display_name"] == "Bureau"
        assert bureau["app_update_failure"] == failure
        assert bureau["app_update_available"] is True, "the row's retry is its update button"
        assert bureau["update_available"] is False
        assert bureau["camilladsp_update_available"] is False
        assert rows[CANAPE]["app_update_failure"] is None

    def test_an_offline_satellite_with_nothing_failed_has_no_row(self):
        rows = self._rows(self._service({}))

        assert set(rows) == {CANAPE}

    def test_a_failure_the_satellite_got_over_on_its_own_is_dropped(self):
        """The wait ended did_not_return — a LAN split past the ceiling — while
        the unit held its trial and committed. Kept, the row says "Not
        responding" for good, and its Retry restarts an occupied room to push
        the bytes it already runs."""
        failure = {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                   "detail": "still on trial of deadbee", "at": 1787000000}
        service = self._service({CANAPE: failure})

        rows = self._rows(service, _app(
            SERVER_PAYLOAD, 1000, last=_record(SERVER_PAYLOAD, "committed", 1125)))

        assert rows[CANAPE]["app_update_failure"] is None
        assert service.settings_service.values["updates.satellite_app_failures"] == {}

    def test_a_failure_on_trial_still_is_kept(self):
        """Running the payload is not having taken it: the trial may still end
        in a rollback."""
        failure = {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                   "detail": "still on trial of deadbee", "at": 1787000000}

        rows = self._rows(self._service({CANAPE: failure}), _app(SERVER_PAYLOAD, 1000, trial=True))

        assert rows[CANAPE]["app_update_failure"] == failure

    def test_a_rollback_no_wait_recorded_is_read_from_the_satellite(self):
        """A backend restart cut the wait, and the guard rolled back after it:
        the satellite's own record is then the only one, and without it the row
        offers a plain update with no cause while the fleet push skips it as a
        satellite that went back."""
        cause = "ModuleNotFoundError: No module named 'zeroconf'"
        service = self._service({})

        rows = self._rows(service, _app(
            OLD_PAYLOAD, 1000, last=_record(SERVER_PAYLOAD, "rolled_back", 1100, cause)))

        failure = rows[CANAPE]["app_update_failure"]
        assert (failure["payload"], failure["verdict"], failure["detail"]) == (
            SERVER_PAYLOAD, "rolled_back", cause)
        assert CANAPE in service.settings_service.values["updates.satellite_app_failures"]


class _InstallingSatellite:
    """A satellite running a snapclient or CamillaDSP install: its
    `/<program>/update/status` answers `update_in_progress` for `polls` polls,
    then down with `last_result`; its `/status` then reports `version`. An
    exception in `silence_after` polls stands for a unit that went away."""

    def __init__(self, status_key, update_path, *, polls, version, last_result=None, silence_after=None):
        self.status_key = status_key
        self.update_path = update_path
        self.polls = polls
        self.version = version
        self.last_result = last_result
        self.silence_after = silence_after
        self.seen = 0
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if url.endswith(f"{self.update_path}/status"):
            self.seen += 1
            if self.silence_after is not None and self.seen > self.silence_after:
                raise ConnectionRefusedError("Connection refused")
            busy = self.seen <= self.polls
            return _FakeResponse(200, {"update_in_progress": busy,
                                       "last_result": None if busy else self.last_result})
        if not self.posts:
            return _FakeResponse(200, {"snapclient": {"version": "0.27.0", "running": True},
                                       "camilladsp": {"version": "3.0.0"}})
        return _FakeResponse(200, {self.status_key: {"version": self.version}})

    def post(self, url, **kwargs):
        self.posts.append(url)
        return _FakeResponse(200, {"status": "success", "started": True})


@pytest.mark.parametrize("push, status_key, update_path, target", [
    pytest.param("update_satellite", "snapclient", "/update", SNAPCLIENT_TARGET, id="snapclient"),
    pytest.param("update_satellite_camilladsp", "camilladsp", "/camilladsp/update", CAMILLADSP_TARGET,
                 id="camilladsp"),
])
class TestProgramInstallFollowsTheSatellite:
    """The satellite's word ends the wait on a snapclient or CamillaDSP install.

    The server used to give up at a fixed 180 s on an install its satellite
    allowed past 300 s (the download alone), and the row said "Update timeout"
    over an install that then landed. And a failed install reached the row as
    "still runs 0.27.0", never with the satellite's reason.
    """

    async def _run(self, satellite_service, satellite, push, target):
        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            return await getattr(satellite_service, push)(CANAPE, target, None)

    async def test_an_install_longer_than_the_old_budget_is_waited_on_to_its_end(
            self, satellite_service, push, status_key, update_path, target):
        from backend.core.updates.satellite import PROGRAM_UPDATE_POLL_S

        busy_polls = 400 // PROGRAM_UPDATE_POLL_S
        satellite = _InstallingSatellite(status_key, update_path, polls=busy_polls, version=target)

        result = await self._run(satellite_service, satellite, push, target)

        assert satellite.seen > busy_polls, "the wait ended while the satellite still installed"
        assert result["verdict"] == "committed" and result["success"] is True
        assert result["new_version"] == target

    async def test_an_install_that_left_the_old_version_says_why_in_the_satellites_words(
            self, satellite_service, push, status_key, update_path, target):
        cause = "Installation failed: dpkg: dependency problems prevent configuration"
        satellite = _InstallingSatellite(
            status_key, update_path, polls=2, version="0.0.1",
            last_result={"target_version": target, "success": False, "error": cause},
        )

        result = await self._run(satellite_service, satellite, push, target)

        assert result["verdict"] == "rolled_back" and result["success"] is False
        assert result["detail"] == cause

    async def test_another_targets_result_is_not_this_installs_reason(
            self, satellite_service, push, status_key, update_path, target):
        """A `last_result` naming another version is an earlier attempt's."""
        satellite = _InstallingSatellite(
            status_key, update_path, polls=1, version="0.0.1",
            last_result={"target_version": "0.0.2", "success": False, "error": "an older failure"},
        )

        result = await self._run(satellite_service, satellite, push, target)

        assert result["verdict"] == "rolled_back"
        assert "an older failure" not in result["detail"] and "0.0.1" in result["detail"]

    async def test_a_satellite_gone_silent_is_given_up_on_well_before_the_ceiling(
            self, satellite_service, push, status_key, update_path, target):
        from backend.core.updates.satellite import (
            PROGRAM_SILENCE_S, PROGRAM_UPDATE_CEILING_S, PROGRAM_UPDATE_POLL_S,
        )

        satellite = _InstallingSatellite(status_key, update_path, polls=10**6, version=target,
                                         silence_after=3)

        result = await self._run(satellite_service, satellite, push, target)

        assert result["verdict"] == "did_not_return"
        polls_after_silence = satellite.seen - 3
        assert polls_after_silence * PROGRAM_UPDATE_POLL_S >= PROGRAM_SILENCE_S
        assert satellite.seen * PROGRAM_UPDATE_POLL_S < PROGRAM_UPDATE_CEILING_S


class TestAppPushRefusal:
    """`refused` is whether the satellite itself said no to a payload. Recorded
    with the failure, it holds back the push the satellite's next admission
    would make: the same payload at every reconnect would cut the room each
    time for an answer already given — while a push that never reached the
    satellite, or a busy one, is owed again once it is back."""

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        return tree

    async def _push(self, satellite_service, satellite, client_tree):
        with _patch_satellite(satellite), \
                patch("asyncio.sleep", new_callable=AsyncMock), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            await satellite_service.update_satellite_app(CANAPE)
        return (await satellite_service.get_app_update_failures())[CANAPE]

    @pytest.mark.parametrize("status, refused", [
        pytest.param(500, True, id="deploy-failed"),
        pytest.param(409, False, id="busy"),
        # The secret of a server it does not follow: once unplugged and plugged
        # back in it takes this one's again, and must be caught up then.
        pytest.param(403, False, id="another-server"),
    ])
    async def test_an_answered_refusal_is_refused_unless_it_was_not_about_the_payload(
            self, satellite_service, client_tree, status, refused):
        satellite = _TrialSatellite(_app(OLD_PAYLOAD, 1000), [_app(OLD_PAYLOAD, 1000)],
                                    post_status=status, post_body='{"detail": "no"}')

        record = await self._push(satellite_service, satellite, client_tree)

        assert record["verdict"] == "rejected"
        assert record["refused"] is refused

    async def test_a_push_that_never_connected_is_not_a_refusal(self, satellite_service, client_tree):
        """Unplugged between the fleet's discovery and its turn: it is the
        satellite the admission catch-up exists for."""
        from aiohttp.client_reqrep import ConnectionKey

        key = ConnectionKey("192.168.1.153", 8001, False, True, None, None, None)
        satellite = _TrialSatellite(
            _app(OLD_PAYLOAD, 1000), [_app(OLD_PAYLOAD, 1000)],
            post_raises=aiohttp.ClientConnectorError(key, OSError(113, "No route to host")),
        )

        record = await self._push(satellite_service, satellite, client_tree)

        assert record["verdict"] == "rejected"
        assert record["refused"] is False


class TestAdmissionCatchUp:
    """What a satellite's admission decides about its client app.

    A Milō update pushes the fleet once; a satellite unplugged during it keeps
    the old app and is sent request keys it ignores — its EQ or level stop
    answering, with no error. Its admission pushes it, exactly when its payload
    differs from the server's, no trial is open on it, and it refused that
    payload neither to a recorded push nor by a rollback of its own.
    """

    def _service(self, failures=None, other_server=False):
        registry = Mock()
        canape = Mock(is_local=False, ip="192.168.1.153", online=True, host="milo-client",
                      other_server=other_server)
        canape.name = "Canapé"
        # Salon is the other satellite a failure on record may belong to.
        salon = Mock(is_local=False, ip="192.168.1.154", online=True, host="milo-client",
                     other_server=False)
        salon.name = "Salon"
        registry.get_all_clients = Mock(return_value={CANAPE: canape, SALON: salon})
        service = SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                         settings_service=_MemorySettings(failures))
        service.owed = []
        service.decided = asyncio.Event()

        async def runner(mac_id, owed):
            service.owed.append((mac_id, await owed()))
            service.decided.set()

        service.set_admission_push_runner(runner)
        return service

    async def _admit(self, service, app):
        status = {"snapclient": {"version": "0.35.0", "running": True}, "app": app}
        with _patch_satellite(_FakeSatellite(status)), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            await service.catch_up_app(CANAPE)
            # The push runs in the service's own task set, not the admission's.
            await asyncio.wait_for(service.decided.wait(), timeout=5)
        assert len(service.owed) == 1, "the runner was not handed the decision"
        return service.owed[0][1]

    async def test_a_satellite_behind_the_server_is_owed_the_push(self):
        assert await self._admit(self._service(), _app(OLD_PAYLOAD, 1000)) is True

    async def test_the_admission_does_not_wait_for_the_push_and_teardown_cuts_it(self):
        """A push takes minutes, and the admission runs in the multiroom
        connection's task set, which a multiroom toggle cancels: run inline, a
        push would be cut there with nothing to say how it ended. It is the
        service's own, so the admission returns at once and only the
        backend's teardown (`cleanup`) cancels it."""
        service = self._service()
        started, cut = asyncio.Event(), asyncio.Event()

        async def runner(mac_id, owed):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cut.set()
                raise

        service.set_admission_push_runner(runner)
        await asyncio.wait_for(service.catch_up_app(CANAPE), timeout=5)
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not cut.is_set()

        await service.cleanup()
        assert cut.is_set(), "the teardown left the admission push running"

    async def test_a_satellite_on_the_servers_payload_is_owed_nothing(self):
        assert await self._admit(self._service(), _app(SERVER_PAYLOAD, 1000)) is False

    async def test_never_during_an_open_trial(self):
        assert await self._admit(self._service(), _app(OLD_PAYLOAD, 1000, trial=True)) is False

    async def test_a_payload_it_refused_is_not_pushed_again(self):
        failures = {CANAPE: {"payload": SERVER_PAYLOAD, "verdict": "rejected", "detail": "HTTP 500",
                             "at": 1, "refused": True}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is False

    @pytest.mark.parametrize("verdict", ["did_not_return", "rejected"])
    async def test_a_push_it_never_answered_is_owed_again(self, verdict):
        failures = {CANAPE: {"payload": SERVER_PAYLOAD, "verdict": verdict, "detail": "gone",
                             "at": 1, "refused": False}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is True

    async def test_a_refusal_of_another_payload_holds_nothing_back(self):
        failures = {CANAPE: {"payload": "1111111", "verdict": "rolled_back", "detail": None,
                             "at": 1, "refused": True}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is True

    async def test_a_rollback_the_satellite_recorded_itself_holds_it_back(self):
        """The wait that would have recorded it was cut by a restart of this
        backend: the satellite's own record is the refusal."""
        app = _app(OLD_PAYLOAD, 2100, last=_record(SERVER_PAYLOAD, "rolled_back", 2100))
        assert await self._admit(self._service(), app) is False

    async def test_a_satellite_that_answers_another_server_is_owed_nothing(self):
        assert await self._admit(self._service(other_server=True), _app(OLD_PAYLOAD, 1000)) is False

    async def test_another_satellites_rollback_of_the_payload_holds_it_back(self):
        """The fleet push's halt: a payload one room went back from is not
        pushed to the next room that returns."""
        failures = {SALON: {"payload": SERVER_PAYLOAD, "verdict": "rolled_back", "detail": None,
                            "at": 1, "refused": True}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is False

    async def test_another_satellite_that_did_not_come_back_from_the_payload_holds_it_back(self):
        """The fleet push holds the rest back on it, and so does the admission:
        a unit that does not come back is the worse sign (its network or its
        boot broken), and pushed at its reconnect the next room is cut for
        it."""
        failures = {SALON: {"payload": SERVER_PAYLOAD, "verdict": "did_not_return", "detail": "gone",
                            "at": 1, "refused": False}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is False

    async def test_a_rejected_push_to_another_satellite_holds_nothing_back(self):
        """It restarted nothing, so it says nothing about the payload."""
        failures = {SALON: {"payload": SERVER_PAYLOAD, "verdict": "rejected", "detail": "HTTP 409",
                            "at": 1, "refused": False}}
        assert await self._admit(self._service(failures), _app(OLD_PAYLOAD, 1000)) is True


CANAPE_IP, SALON_IP = "192.168.1.153", "192.168.1.154"


def _two_satellites(failures, *, salon=True, salon_other_server=False):
    """Canapé and Salon in the registry (Salon left out with `salon=False`),
    the record holding `failures`, the admission's runner recording what it
    was told and the fleet's push recording who it reached (each push
    committing)."""
    registry = Mock()
    clients = {}
    for mac, ip, name in ((CANAPE, CANAPE_IP, "Canapé"), (SALON, SALON_IP, "Salon")):
        if mac == SALON and not salon:
            continue
        client = Mock(is_local=False, ip=ip, online=True, host="milo-client",
                      other_server=mac == SALON and salon_other_server)
        client.name = name
        clients[mac] = client
    registry.get_all_clients = Mock(return_value=clients)
    service = SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry,
                                     settings_service=_MemorySettings(failures))
    service.owed = []
    service.pushed = []

    async def runner(mac_id, owed):
        service.owed.append((mac_id, await owed()))

    async def push(mac_id):
        service.pushed.append(mac_id)
        return {"success": True, "verdict": "committed"}

    service.set_admission_push_runner(runner)
    service.update_satellite_app = push
    return service


def _status_of(app):
    return {"snapclient": {"version": "0.35.0", "running": True}, "app": app}


async def _admit_one(service, mac_id, statuses):
    """`statuses`: {ip: app block}; an IP left out does not answer."""
    with patch("backend.core.updates.satellite.aiohttp.ClientSession",
               return_value=_FakeFleet({ip: _status_of(app) for ip, app in statuses.items()})), \
            patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
        before = len(service.owed)
        await service.catch_up_app(mac_id)
        for task in [t for t in asyncio.all_tasks() if "catch_up_app" in t.get_name()]:
            await asyncio.wait_for(task, timeout=5)
    assert len(service.owed) == before + 1, "the runner was not handed the decision"
    return service.owed[-1][1]


async def _admit_and_drain(service, mac_id, statuses):
    """`_admit_one`, waiting as well for every admission push it set off; the
    runner's decisions, in order, are left in `service.owed`."""
    with patch("backend.core.updates.satellite.aiohttp.ClientSession",
               return_value=_FakeFleet({ip: _status_of(app) for ip, app in statuses.items()})), \
            patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
        await service.catch_up_app(mac_id)
        for _ in range(10):
            pending = [t for t in asyncio.all_tasks()
                       if "catch_up_app" in t.get_name() and not t.done()]
            if not pending:
                break
            await asyncio.wait_for(asyncio.gather(*pending), timeout=5)


class TestOneHoldForTheFleetAndTheAdmission:
    """The fleet push and a satellite's admission decide whether one
    satellite's failure holds the others back from a payload by one rule
    (`_holds_back_fleet`), read on one record. They disagreed: the fleet held
    the rest back on a satellite that did not come back, and the admission
    pushed the next one to reconnect anyway, restarting its room."""

    @pytest.mark.parametrize("verdict, holds", [
        ("rolled_back", True), ("did_not_return", True), ("rejected", False),
    ])
    async def test_the_fleet_and_the_admission_decide_alike_on_the_same_record(
            self, verdict, holds):
        failures = {SALON: {"payload": SERVER_PAYLOAD, "verdict": verdict, "detail": "x",
                            "at": 1, "refused": verdict == "rolled_back"}}
        behind = _app(OLD_PAYLOAD, 1000)

        fleet = _two_satellites(failures)
        with patch("backend.core.updates.satellite.aiohttp.ClientSession",
                   return_value=_FakeFleet({CANAPE_IP: _status_of(behind)})), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            await fleet.push_client_app_to_fleet()
        admission = _two_satellites(failures)
        owed = await _admit_one(admission, CANAPE, {CANAPE_IP: behind})

        assert (CANAPE in fleet.pushed) is (not holds)
        assert owed is (not holds)

    async def test_the_hold_lifts_once_the_satellite_comes_back_on_the_payload(self):
        """Salon's push saw no end; Canapé is held back. Salon's own admission,
        settled on the payload, drops its failure — and with it the hold, and
        Canapé is pushed then. It stays online, so it is never admitted again:
        left to its next reconnect, it kept the old app for weeks, silently
        ignoring keys this server sends."""
        service = _two_satellites({SALON: {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                                           "detail": "gone", "at": 1, "refused": False}})
        behind = _app(OLD_PAYLOAD, 1000)
        settled = _app(SERVER_PAYLOAD, 3000, last=_record(SERVER_PAYLOAD, "committed", 3125))

        assert await _admit_one(service, CANAPE, {CANAPE_IP: behind}) is False
        service.owed.clear()
        await _admit_and_drain(service, SALON, {SALON_IP: settled, CANAPE_IP: behind})

        assert SALON not in await service.get_app_update_failures()
        assert service.owed == [(SALON, False), (CANAPE, True)]

    @pytest.mark.parametrize("salon, salon_other_server", [(False, False), (True, True)],
                             ids=["removed from the registry", "answers another server"])
    async def test_a_satellite_that_cannot_come_back_here_holds_nothing_back(
            self, salon, salon_other_server):
        """Its record would never go: a speaker deleted from the registry, or
        one that now follows another server, never settles on this payload
        here, and a unit unplugged under the fleet push ends did_not_return —
        so every other speaker was held back at every admission and every
        Milō update, for good, by a row nothing shows."""
        failures = {SALON: {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                            "detail": "gone", "at": 1, "refused": False}}
        behind = _app(OLD_PAYLOAD, 1000)

        fleet = _two_satellites(failures, salon=salon, salon_other_server=salon_other_server)
        with patch("backend.core.updates.satellite.aiohttp.ClientSession",
                   return_value=_FakeFleet({CANAPE_IP: _status_of(behind)})), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            left_behind = await fleet.push_client_app_to_fleet()
        admission = _two_satellites(failures, salon=salon, salon_other_server=salon_other_server)

        assert fleet.pushed == [CANAPE]
        assert not [line for line in left_behind if "held back" in line]
        assert await _admit_one(admission, CANAPE, {CANAPE_IP: behind}) is True

    async def test_a_satellite_that_did_not_come_back_and_went_back_is_recorded_rolled_back(self):
        """What it says itself once it answers again: it came back, on the old
        app, from a payload whose push the server saw no end of."""
        service = _two_satellites({SALON: {"payload": SERVER_PAYLOAD, "verdict": "did_not_return",
                                           "detail": "gone", "at": 1, "refused": False}})
        went_back = _app(OLD_PAYLOAD, 2100, last=_record(SERVER_PAYLOAD, "rolled_back", 2100, "boom"))

        assert await _admit_one(service, SALON, {SALON_IP: went_back}) is False
        record = (await service.get_app_update_failures())[SALON]
        assert record["verdict"] == "rolled_back"
        assert record["refused"] is True


class TestAdmissionDpkgRetry:
    """A satellite plugged back in after days runs unattended-upgrades at once,
    and its admission push meets dpkg held past the satellite's wait: a 409
    that installed nothing. Nothing else pushes a satellite that stays online,
    so it kept the old app — its EQ or its level silently ignoring the keys
    this server sends — until the owner pressed its row. The admission tries
    again, minutes later, a bounded number of times; a 409 for a trial still
    open is not retried, since a retry would only meet it again."""

    DPKG_BUSY = ("dpkg is busy (held by unattended-upgr after 60 s): nothing was "
                 "installed; push again once it is done")

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        return tree

    class _Satellite:
        """Answers each POST with the next refusal (409, its `detail`), then
        takes the push: from there /status reports the payload committed."""

        def __init__(self, refusals, log):
            self.refusals = list(refusals)
            self.log = log
            self.app = _app(OLD_PAYLOAD, 1000)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            return _FakeResponse(200, {"app": self.app, "snapclient": {"version": "0.35.0"}})

        def post(self, url, **kwargs):
            self.log.append("post")
            if self.refusals:
                return _FakeResponse(409, json.dumps({"detail": self.refusals.pop(0)}))
            self.app = _app(SERVER_PAYLOAD, 3000, last=_record(SERVER_PAYLOAD, "committed", 3125))
            return _FakeResponse(200, {"status": "success"})

    async def _admit(self, satellite_service, client_tree, refusals):
        """The admission's push run to its end, every sleep recorded in the
        order it came: the retry's delay among the pushes, the waiter's polls
        left out."""
        from backend.core.updates.satellite import ADMISSION_DPKG_RETRY_S

        log = []
        service = satellite_service

        async def runner(mac_id, owed):
            # The programs router's runner, less its claim: ask, then push.
            if await owed():
                await service.update_satellite_app(mac_id)

        async def sleep(delay, *args, **kwargs):
            if delay == ADMISSION_DPKG_RETRY_S:
                log.append("retry delay")

        service.set_admission_push_runner(runner)
        canape = service.client_registry_service.get_all_clients()[CANAPE]
        canape.other_server = False
        with _patch_satellite(self._Satellite(refusals, log)), \
                patch("asyncio.sleep", new=sleep), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            await service.catch_up_app(CANAPE)
            for task in [t for t in asyncio.all_tasks() if "catch_up_app" in t.get_name()]:
                await asyncio.wait_for(task, timeout=5)
        return log

    async def test_a_dpkg_busy_refusal_is_pushed_again_after_the_delay(
            self, satellite_service, client_tree):
        log = await self._admit(satellite_service, client_tree, [self.DPKG_BUSY])

        assert log == ["post", "retry delay", "post"]
        assert CANAPE not in await satellite_service.get_app_update_failures()

    async def test_the_retries_are_bounded(self, satellite_service, client_tree):
        from backend.core.updates.satellite import ADMISSION_DPKG_RETRIES

        log = await self._admit(satellite_service, client_tree, [self.DPKG_BUSY] * 10)

        assert log.count("post") == ADMISSION_DPKG_RETRIES + 1
        assert log.count("retry delay") == ADMISSION_DPKG_RETRIES
        assert log[-1] == "post"

    @pytest.mark.parametrize("refusal", [
        f"An update to {OLD_PAYLOAD} is still on trial",
        "Update already in progress",
    ])
    async def test_a_busy_satellite_for_another_reason_is_not_pushed_again(
            self, satellite_service, client_tree, refusal):
        log = await self._admit(satellite_service, client_tree, [refusal])

        assert log == ["post"]

    async def test_a_readmission_during_the_wait_starts_no_second_retry(
            self, satellite_service, client_tree):
        """A speaker that reconnects while its retry waits was admitted again,
        and its own catch-up ran a second loop of pushes and retries beside the
        first: twice the refusals for one dpkg lock."""
        started = []
        service = satellite_service
        original = service.catch_up_app

        async def readmit_during_wait():
            await original(CANAPE)
            started.append(len([t for t in asyncio.all_tasks()
                                if "catch_up_app" in t.get_name() and not t.done()]))

        from backend.core.updates.satellite import ADMISSION_DPKG_RETRY_S
        real_sleep = asyncio.sleep

        async def sleep(delay, *args, **kwargs):
            if delay == ADMISSION_DPKG_RETRY_S:
                await readmit_during_wait()
            await real_sleep(0)

        async def runner(mac_id, owed):
            if await owed():
                await service.update_satellite_app(mac_id)

        service.set_admission_push_runner(runner)
        service.client_registry_service.get_all_clients()[CANAPE].other_server = False
        log = []
        with _patch_satellite(self._Satellite([self.DPKG_BUSY], log)), \
                patch("asyncio.sleep", new=sleep), \
                patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
                patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", _fake_git()):
            await original(CANAPE)
            for _ in range(10):
                pending = [t for t in asyncio.all_tasks()
                           if "catch_up_app" in t.get_name() and not t.done()]
                if not pending:
                    break
                await asyncio.wait_for(asyncio.gather(*pending), timeout=5)

        assert started == [1]
        assert log == ["post", "post"]

    # What a satellite on the previous app writes, frozen: this server reads it
    # off a 409 that release sent.
    FROZEN_DPKG_BUSY_REFUSAL = "dpkg is busy"

    def test_the_refusal_it_reads_is_frozen(self):
        """The refusal crosses releases: this server reads the one a satellite
        on the previous app writes. Reworded on both sides in one commit, the
        two still agree and every satellite on the previous app has its dpkg
        refusal read as one not worth a retry, with no error anywhere — so the
        words are pinned here, not only compared across the tree."""
        from backend.core.updates.satellite import DPKG_BUSY_REFUSAL

        assert DPKG_BUSY_REFUSAL == self.FROZEN_DPKG_BUSY_REFUSAL
        source = (Path(__file__).resolve().parents[2]
                  / "milo-client" / "app" / "services" / "app_update.py").read_text()
        assert "def _dpkg_busy(" in source, "the satellite's refusal moved; read it again"
        assert f'refusal = f"{self.FROZEN_DPKG_BUSY_REFUSAL} (' in source
