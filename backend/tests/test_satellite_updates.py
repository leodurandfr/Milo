# backend/tests/test_satellite_updates.py
"""
Tests for the server half of the satellite update path: what gets shipped, what
counts as a finished update, and when the UI is allowed to offer one.

Every assertion here is about a machine CI can never reach — a second physical
Pi — so the mocks stand for the satellite's HTTP surface and for git, and the
assertion is always what the service concluded from what the satellite said.
"""
import logging
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.programs import create_programs_router
from backend.core.updates.satellite import SatelliteUpdateService

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
              dirty: bool = False):
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
            out = " M milo-client/app/main.py" if dirty else ""
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


def _patch_satellite(satellite):
    """Every ClientSession the service opens answers as this one satellite."""
    return patch("backend.core.updates.satellite.aiohttp.ClientSession", return_value=satellite)


@pytest.fixture
def satellite_service():
    registry = Mock()
    # `name` cannot be passed to the Mock constructor: it names the mock itself
    # and leaves `client.name` a child Mock, which is how `display_name` ends up
    # as a repr string in the payload the UI labels its buttons with.
    canape = Mock(is_local=False, ip="192.168.1.153", online=True, host="milo-client")
    canape.name = "Canapé"
    registry.get_all_clients = Mock(return_value={"dc:a6:32:7e:d3:43": canape})
    return SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry)


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
    last touched `milo-client/`, the tarball's only content.

    Collapsing them either way breaks something measured. Deciding on the
    release lit the update button on the whole fleet every time the server
    moved — 100 commits, 0 of them touching the directory — and each press
    deployed a byte-identical payload while restarting a speaker in an occupied
    room. Displaying the payload put a `git describe` hash on the screen beside
    the server's clean tag, in a numbering nothing else on the appliance uses.
    """

    async def test_the_payload_is_the_last_commit_touching_the_client_tree(
            self, satellite_service):
        git = _fake_git(payload="c6247d9")

        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec", git):
            payload = await satellite_service.get_client_payload_version()

        assert payload == "c6247d9"
        log = next(argv for argv in git.calls if "log" in argv)
        assert log[-2:] == ["--", "milo-client"], "unscoped, this fingerprints HEAD again"

    async def test_uncommitted_client_work_is_marked_dirty(self, satellite_service):
        """The tarball is built from the working tree, so an edit no commit
        names is still payload. Without the suffix, a satellite change under
        test reports the committed fingerprint and can never be pushed from the
        UI.
        """
        with patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   _fake_git(dirty=True)):
            payload = await satellite_service.get_client_payload_version()

        assert payload == f"{SERVER_PAYLOAD}-dirty"

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

        assert result["success"] is False
        assert "3.0.0" in result["error"] and "3.1.0" in result["error"]

    async def test_a_version_that_moved_is_a_successful_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": "3.1.0"}, "snapclient": {"version": "0.28.0"}},
            post_payload={"status": "success", "started": True, "target_version": CAMILLADSP_TARGET},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

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
                "dc:a6:32:7e:d3:43", CAMILLADSP_TARGET
            )

        assert result["success"] is True
        assert result["new_version"] == CAMILLADSP_TARGET


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
            satellite_service.discover_satellites = AsyncMock(return_value=satellites)
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


class TestAppUpdateOutcome:
    """What counts as "the satellite is running the new app".

    Not the version it reports: the satellite writes that file at step 5 of its
    own deployment and only schedules the restart at step 6. A restart that
    never lands — a unit file the same update just made invalid, a masked unit —
    leaves it answering the new version out of the old process. `started_at` is
    the process, so it is the half the satellite cannot write ahead of itself.
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

    async def test_a_version_file_written_by_a_process_that_never_restarted_is_not(
        self, satellite_service, client_tree
    ):
        """The defect: the deploy landed, the restart did not, and the satellite
        keeps serving the old code while the fleet view calls it current."""
        satellite = _FakeSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
            status_after_post={"app": {"payload": SERVER_PAYLOAD, "version": SERVER_VERSION, "started_at": 1000},
                               "snapclient": {"version": "0.28.0"}},
        )

        result = await self._push(satellite_service, satellite, client_tree)

        assert result["success"] is False
        assert "never restarted" in result["error"]

    async def test_a_satellite_that_answers_nothing_is_a_timeout(
        self, satellite_service, client_tree
    ):
        """The two failures read differently in the UI: one needs a retry, the
        other needs somebody to look at why the unit will not come back."""
        satellite = _FakeSatellite(
            status_payload={"app": {"payload": "0ae3872", "version": "v0.1.0", "started_at": 1000},
                            "snapclient": {"version": "0.28.0"}},
        )

        result = await self._push(satellite_service, satellite, client_tree)

        assert result["success"] is False
        assert "timeout" in result["error"]


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
                                      client_registry_service=registry)

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
    def _client(ip, name, is_local=False):
        # `name` is set after construction: passing it to Mock() names the mock.
        client = Mock(is_local=is_local, ip=ip, online=True, host="milo-client")
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
                                         client_registry_service=registry)
        service.pushed = []

        async def _push(mac_id):
            service.pushed.append(mac_id)
            return service.push_result

        service.update_satellite_app = _push
        service.push_result = {"success": True}
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
        """Unplugged during the update: naming it is all that is left, since its
        own row is what offers the catch-up once it is back."""
        left_behind = await self._push_to(fleet, {
            "192.168.1.60": self._status("0ae3872", "v0.1.0"),
        })

        assert fleet.pushed == ["d8:3a:dd:68:e7:e4"]
        assert left_behind == ["Canapé"]

    async def test_a_push_that_failed_is_reported_and_not_raised(self, fleet):
        """It runs past the point where the app can still be rolled back: a
        raise here would abort a Milō update that has already succeeded."""
        fleet.push_result = {"success": False, "error": "satellite rejected update"}

        left_behind = await self._push_to(fleet, {
            "192.168.1.153": self._status("0ae3872", "v0.1.0"),
            "192.168.1.60": self._status("0ae3872", "v0.1.0"),
        })

        assert fleet.pushed == ["dc:a6:32:7e:d3:43", "d8:3a:dd:68:e7:e4"]
        assert left_behind == ["Canapé", "Bureau"]

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
            result = await satellite_service.update_satellite("00:00:00:00:00:00", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

        assert result == {"success": False, "error": "Already at the latest version"}

    async def test_a_declined_update_with_no_message_still_says_something(self, satellite_service):
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

        assert result == {"success": False, "error": "Update failed"}

    async def test_a_refused_post_is_reported_with_its_status(self, satellite_service):
        """A satellite mid-reboot answers 502 through its own nginx."""
        class _Refusing(_FakeSatellite):
            def post(self, url, **kwargs):
                self.posts.append(url)
                return _FakeResponse(502, {})

        with _patch_satellite(_Refusing(self.STATUS)):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

        assert result == {"success": False, "error": "HTTP 502"}

    async def test_a_transport_that_dies_mid_update_is_reported_not_raised(
            self, satellite_service, caplog):
        """The route turns this dict into a banner; an exception would reach the
        client as a 500 with no indication of which satellite failed."""
        class _Dropping(_FakeSatellite):
            def post(self, url, **kwargs):
                raise OSError("Connection reset by peer")

        with _patch_satellite(_Dropping(self.STATUS)), caplog.at_level(logging.ERROR):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

        assert result["success"] is False
        assert "Connection reset" in result["error"]
        assert "dc:a6:32:7e:d3:43" in caplog.text

    async def test_the_update_is_posted_to_the_satellites_own_api_port(self, satellite_service):
        """`CLIENT_API_PORT` is the satellite app's only listener; posting to 80
        reaches its nginx and 404s, which reads as "satellite refused"."""
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

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
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43", SNAPCLIENT_TARGET)

        assert result["success"] is False
        assert "timeout" in result["error"]

    async def test_a_camilladsp_update_is_dispatched_to_its_own_endpoint(
            self, satellite_service):
        """The satellite serves snapclient, CamillaDSP and app updates on three
        separate paths; the wrong one 404s and reads as "satellite refused"."""
        fake = _FakeSatellite(self.STATUS, post_payload={"started": False})
        with _patch_satellite(fake):
            await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

        assert fake.posts == [
            f"http://192.168.1.153:{satellite_service.satellite_api_port}/camilladsp/update"
        ]

    async def test_an_unknown_satellite_gets_no_camilladsp_push(self, satellite_service):
        fake = _FakeSatellite(self.STATUS)
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite_camilladsp("00:00:00:00:00:00", CAMILLADSP_TARGET)

        assert "not found or offline" in result["error"]
        assert fake.posts == []

    async def test_a_camilladsp_update_the_satellite_declined_carries_its_message(
            self, satellite_service):
        fake = _FakeSatellite(self.STATUS, post_payload={
            "started": False, "message": "CamillaDSP is already at 3.0.1"})
        with _patch_satellite(fake):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

        assert result == {"success": False, "error": "CamillaDSP is already at 3.0.1"}

    async def test_a_refused_camilladsp_post_reports_its_status(self, satellite_service):
        class _Refusing(_FakeSatellite):
            def post(self, url, **kwargs):
                return _FakeResponse(500, {})

        with _patch_satellite(_Refusing(self.STATUS)):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

        assert result == {"success": False, "error": "HTTP 500"}

    async def test_a_camilladsp_transport_failure_is_reported_not_raised(
            self, satellite_service, caplog):
        class _Dropping(_FakeSatellite):
            def post(self, url, **kwargs):
                raise OSError("No route to host")

        with _patch_satellite(_Dropping(self.STATUS)), caplog.at_level(logging.ERROR):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

        assert "No route to host" in result["error"]
        assert "dc:a6:32:7e:d3:43" in caplog.text

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
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43", CAMILLADSP_TARGET)

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
