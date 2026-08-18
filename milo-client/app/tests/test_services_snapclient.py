"""
Unit tests for SnapclientService.

A satellite has no console and no log surface in the UI. What it reports about
snapclient is a version string it parses out of a subprocess and a flag it
raises while an update runs — so both of those are the whole of what the server,
and the operator, get to see.
"""
import pytest
from unittest.mock import AsyncMock, patch


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._output = (stdout, stderr)

    async def communicate(self):
        return self._output


def _spawning(proc):
    """Patches the boundary so every spawn hands back one finished process."""
    return patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc))


def _github_serving(payload, status=200):
    """A ClientSession class standing in for api.github.com, serving one release document."""

    class _Response:
        def __init__(self):
            self.status = status

        async def json(self):
            return payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            return _Response()

    return _Session


class TestSnapclientServiceProperties:
    """Test SnapclientService property accessors."""

    def test_a_fresh_service_has_no_update_running(self, snapclient_service):
        """`GET /update/status` answers from this flag before anything has run."""
        assert snapclient_service.update_in_progress is False


class TestInstalledVersionParsing:
    """The string the server compares against the target to call an update done.

    `SatelliteUpdateService._wait_for_update_completion` polls this value and
    stops when it equals the target. A parse that returns None therefore reads as
    "the update never took" on a satellite that updated correctly, and the
    operator is sent after a fault that is not there.
    """

    @pytest.mark.asyncio
    async def test_the_version_comes_off_stdout(self, snapclient_service):
        with _spawning(_FakeProc(stdout=b"snapclient v0.28.0\n")):
            assert await snapclient_service.get_installed_version() == "0.28.0"

    @pytest.mark.asyncio
    async def test_the_version_is_read_off_stderr_too(self, snapclient_service):
        """Some snapclient builds print the banner on stderr, which is why the
        production code concatenates both streams. A test that only ever feeds
        stdout cannot tell that concatenation from a stdout-only read."""
        with _spawning(_FakeProc(stderr=b"snapclient v0.28.0\n")):
            assert await snapclient_service.get_installed_version() == "0.28.0"

    @pytest.mark.asyncio
    async def test_output_carrying_no_version_is_not_a_version(self, snapclient_service):
        """A partial string would compare unequal to every target for ever; None
        is what the caller knows to read as "unknown"."""
        with _spawning(_FakeProc(stdout=b"snapclient: error while loading shared libraries\n")):
            assert await snapclient_service.get_installed_version() is None

    @pytest.mark.asyncio
    async def test_a_missing_binary_is_not_a_version(self, snapclient_service):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            assert await snapclient_service.get_installed_version() is None


class TestLatestGithubVersion:
    """What the UI offers as the update target for every satellite in the house."""

    @pytest.mark.asyncio
    async def test_the_release_tag_becomes_the_version(self, snapclient_service):
        with patch("aiohttp.ClientSession", _github_serving({"tag_name": "v0.29.0"})):
            assert await snapclient_service.get_latest_github_version() == "0.29.0"

    @pytest.mark.asyncio
    async def test_a_tag_the_regex_misses_falls_back_to_stripping_the_v(self, snapclient_service):
        """The regex wants three parts. The fallback below it is deliberate: on a
        two-part tag the alternative is None, which reads in the UI as "no update
        available" rather than as "GitHub answered something unexpected"."""
        with patch("aiohttp.ClientSession", _github_serving({"tag_name": "v0.30"})):
            assert await snapclient_service.get_latest_github_version() == "0.30"

    @pytest.mark.asyncio
    async def test_a_non_200_offers_nothing(self, snapclient_service):
        """GitHub rate-limits unauthenticated callers and answers 403 with a JSON
        body. Parsing that body would push a version off an error message."""
        with patch("aiohttp.ClientSession",
                   _github_serving({"message": "API rate limit exceeded"}, status=403)):
            assert await snapclient_service.get_latest_github_version() is None

    @pytest.mark.asyncio
    async def test_an_offline_unit_offers_nothing(self, snapclient_service):
        with patch("aiohttp.ClientSession", side_effect=Exception("Network error")):
            assert await snapclient_service.get_latest_github_version() is None


class TestServiceRunning:
    """`_start_snapclient_service` decides from this whether the start took, and
    that verdict is what makes the rollback below fire or stay out."""

    @pytest.mark.asyncio
    async def test_active_is_running(self, snapclient_service):
        with _spawning(_FakeProc(stdout=b"active\n")):
            assert await snapclient_service.is_service_running() is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", [b"inactive\n", b"failed\n", b"activating\n"])
    async def test_nothing_else_is(self, snapclient_service, state):
        """`activating` included: a unit still coming up has not played a note yet,
        and reporting it running is how a start that never completed reads as done."""
        with _spawning(_FakeProc(stdout=state)):
            assert await snapclient_service.is_service_running() is False


class TestSnapclientServiceDebianDetection:
    """The codename picks the .deb variant; a wrong one downloads a 404."""

    @pytest.mark.asyncio
    async def test_get_debian_codename_bookworm(self, snapclient_service):
        with _spawning(_FakeProc(stdout=b"bookworm\n")):
            assert await snapclient_service._get_debian_codename() == "bookworm"

    @pytest.mark.asyncio
    async def test_get_debian_codename_fallback(self, snapclient_service):
        """Should fallback to bookworm on error."""
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("Error")):
            assert await snapclient_service._get_debian_codename() == "bookworm"


class FakeMachine:
    """The outside world of a snapclient update: systemd, the wrapper, the binary.

    Records every argv it is handed. The sudoers policy on a satellite is
    argument-scoped, so the exact verb and unit name are the contract, not an
    implementation detail — which is why the tests below read `calls`.

    `on_spawn` is an async hook fired before each command is served, so a test can
    observe the service *while the update is in flight* rather than after it has
    returned and put everything back.
    """

    STOP = ("sudo", "systemctl", "stop", "milo-client-snapclient.service")
    START = ("sudo", "systemctl", "start", "milo-client-snapclient.service")

    def __init__(self, install_returncode=0, install_raises=False):
        self.calls = []
        self.running = True
        self.install_returncode = install_returncode
        self.install_raises = install_raises
        self.on_spawn = None

    async def spawn(self, *argv, **kwargs):
        self.calls.append(argv)
        if self.on_spawn:
            await self.on_spawn(argv)

        if argv[:2] == ("snapclient", "--version"):
            return _FakeProc(stdout=b"snapclient v0.29.0\n")
        if argv[0] == "bash":
            return _FakeProc(stdout=b"bookworm\n")
        if argv[:2] == ("systemctl", "is-active"):
            return _FakeProc(stdout=b"active\n" if self.running else b"inactive\n")
        if argv == self.STOP:
            self.running = False
            return _FakeProc()
        if argv == self.START:
            self.running = True
            return _FakeProc()
        if argv[1].endswith("milo-client-install-snapclient"):
            if self.install_raises:
                raise OSError(13, "Permission denied")
            return _FakeProc(returncode=self.install_returncode, stderr=b"dpkg: dependency problems")

        raise AssertionError(f"unexpected command: {argv}")


class _FakeContent:
    async def iter_chunked(self, size):
        yield b"a .deb that apt will refuse"


class _FakeResponse:
    status = 200
    content = _FakeContent()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """GitHub, serving the release asset."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _FakeResponse()


@pytest.fixture
def machine():
    return FakeMachine()


@pytest.fixture
def patched_world(machine):
    """Patches only the boundary: subprocesses, GitHub, and the settling sleeps."""
    import services.snapclient as snapclient_module

    with patch.object(snapclient_module.asyncio, "create_subprocess_exec", machine.spawn), \
         patch.object(snapclient_module.asyncio, "sleep", AsyncMock()), \
         patch.object(snapclient_module.aiohttp, "ClientSession", _FakeSession):
        yield machine


class TestOnlyOneUpdateRunsAtATime:
    """`update_in_progress` is the whole of the mutual exclusion.

    Two `update_snapclient` calls overlapping means two runs of the install
    wrapper over the same dpkg lock, one of them after the other has already
    stopped the unit — which is how a satellite ends up with no snapclient
    installed at all. The flag is also what `GET /update/status` reports, so the
    server's own poll depends on it being up for the whole run and down after.
    """

    @pytest.mark.asyncio
    async def test_a_second_update_is_refused_while_the_first_is_in_flight(
        self, snapclient_service, patched_world
    ):
        """Observed mid-update, not after: a flag read once everything has been
        put back cannot distinguish one that was raised from one that never was."""
        machine = patched_world
        refused = []

        async def _call_again(argv):
            if argv == FakeMachine.STOP:
                refused.append(await snapclient_service.update_snapclient("0.29.0"))

        machine.on_spawn = _call_again

        result = await snapclient_service.update_snapclient("0.29.0")

        assert result["success"] is True
        assert len(refused) == 1, "the hook must have fired while the first update ran"
        assert refused[0]["success"] is False
        assert "already in progress" in refused[0]["error"]
        assert machine.calls.count(FakeMachine.STOP) == 1, "the refused call stopped nothing"

    @pytest.mark.asyncio
    async def test_the_flag_is_down_again_after_a_failed_update(
        self, snapclient_service, patched_world
    ):
        """Left up, one failure locks the satellite out of every later update and
        the only way back is a restart of the app on the unit itself."""
        machine = patched_world
        machine.install_returncode = 1

        result = await snapclient_service.update_snapclient("0.29.0")

        assert result["success"] is False
        assert snapclient_service.update_in_progress is False


class TestSnapclientUpdateLeavesTheServiceRunning:
    """A failed update must not leave the room silent.

    Step 2 of the update stops snapclient. `Restart=on-failure` does not undo an
    explicit `systemctl stop`, so every failure path after that point used to
    return an error dict with the speaker muted and nothing in the UI saying so —
    on a satellite whose only local sign of life is the sound itself. The
    neighbouring CamillaDSP updater has always tracked this; this one did not.
    """

    @pytest.mark.asyncio
    async def test_a_failed_install_starts_snapclient_back(self, snapclient_service, patched_world):
        machine = patched_world
        machine.install_returncode = 1

        result = await snapclient_service.update_snapclient("0.29.0")

        assert result["success"] is False
        assert machine.running is True, "an apt failure must not leave the speaker stopped"
        assert machine.calls.count(FakeMachine.START) == 1, "the rollback issued the start"

    @pytest.mark.asyncio
    async def test_an_install_that_raises_starts_snapclient_back(self, snapclient_service, patched_world):
        """The wrapper is a sudo call: a denial raises rather than exiting non-zero."""
        machine = patched_world
        machine.install_raises = True

        result = await snapclient_service.update_snapclient("0.29.0")

        assert result["success"] is False
        assert machine.running is True

    @pytest.mark.asyncio
    async def test_an_applied_update_does_not_start_it_twice(self, snapclient_service, patched_world):
        """The floor under the two above: on success the rollback must stay out."""
        machine = patched_world

        result = await snapclient_service.update_snapclient("0.29.0")

        assert result["success"] is True
        assert machine.running is True
        assert machine.calls.count(FakeMachine.START) == 1
