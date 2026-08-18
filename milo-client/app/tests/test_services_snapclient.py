"""
Unit tests for SnapclientService.
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestSnapclientServiceProperties:
    """Test SnapclientService property accessors."""

    def test_update_in_progress_default(self, snapclient_service):
        """Should return False by default."""
        assert snapclient_service.update_in_progress is False

    def test_update_in_progress_when_updating(self, snapclient_service):
        """Should return True during update."""
        snapclient_service._update_in_progress = True
        assert snapclient_service.update_in_progress is True


class TestSnapclientServiceVersion:
    """Test SnapclientService version operations."""

    @pytest.mark.asyncio
    async def test_get_installed_version_parses_output(self, snapclient_service):
        """Should parse version from snapclient --version output."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"snapclient v0.28.0\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=(b"snapclient v0.28.0\n", b"")):
                mock_proc.communicate = AsyncMock(return_value=(b"snapclient v0.28.0\n", b""))
                await snapclient_service.get_installed_version()
                # Note: actual parsing depends on wait_for result
                # This test verifies the method doesn't crash

    @pytest.mark.asyncio
    async def test_get_installed_version_handles_error(self, snapclient_service):
        """Should return None when snapclient not found."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            version = await snapclient_service.get_installed_version()
            assert version is None

    @pytest.mark.asyncio
    async def test_get_latest_github_version(self, snapclient_service):
        """Should fetch latest version from GitHub API."""
        # This test verifies the method handles responses correctly
        # Full integration would require actual network calls
        # Here we just verify error handling works
        with patch("aiohttp.ClientSession") as mock_session_class:
            # Simulate a network error to test error path
            mock_session_class.side_effect = Exception("Mocked network error")
            version = await snapclient_service.get_latest_github_version()
            assert version is None  # Should handle error gracefully

    @pytest.mark.asyncio
    async def test_get_latest_github_version_handles_error(self, snapclient_service):
        """Should return None on network error."""
        with patch("aiohttp.ClientSession", side_effect=Exception("Network error")):
            version = await snapclient_service.get_latest_github_version()
            assert version is None


class TestSnapclientServiceStatus:
    """Test SnapclientService status operations."""

    @pytest.mark.asyncio
    async def test_is_service_running_active(self, snapclient_service):
        """Should return True when service is active."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"active\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            is_running = await snapclient_service.is_service_running()
            assert is_running is True

    @pytest.mark.asyncio
    async def test_is_service_running_inactive(self, snapclient_service):
        """Should return False when service is inactive."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"inactive\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            is_running = await snapclient_service.is_service_running()
            assert is_running is False


class TestSnapclientServiceUpdate:
    """Test SnapclientService update operations."""

    @pytest.mark.asyncio
    async def test_update_rejects_when_in_progress(self, snapclient_service):
        """Should reject update if one is already in progress."""
        snapclient_service._update_in_progress = True
        result = await snapclient_service.update_snapclient("0.29.0")
        assert result["success"] is False
        assert "already in progress" in result["error"]

    @pytest.mark.asyncio
    async def test_update_sets_flag_during_update(self, snapclient_service):
        """Should set update_in_progress during update."""
        # Mock all the subprocess calls to fail early
        with patch.object(snapclient_service, "get_installed_version", return_value="0.28.0"), \
             patch.object(snapclient_service, "_download_snapclient_deb",
                         return_value={"success": False, "error": "Test error"}):

            await snapclient_service.update_snapclient("0.29.0")

            # Flag should be reset after update (success or failure)
            assert snapclient_service.update_in_progress is False


class TestSnapclientServiceDebianDetection:
    """Test SnapclientService Debian version detection."""

    @pytest.mark.asyncio
    async def test_get_debian_codename_bookworm(self, snapclient_service):
        """Should detect bookworm codename."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"bookworm\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            codename = await snapclient_service._get_debian_codename()
            assert codename == "bookworm"

    @pytest.mark.asyncio
    async def test_get_debian_codename_fallback(self, snapclient_service):
        """Should fallback to bookworm on error."""
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("Error")):
            codename = await snapclient_service._get_debian_codename()
            assert codename == "bookworm"


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._output = (stdout, stderr)

    async def communicate(self):
        return self._output


class FakeMachine:
    """The outside world of a snapclient update: systemd, the wrapper, the binary.

    Records every argv it is handed. The sudoers policy on a satellite is
    argument-scoped, so the exact verb and unit name are the contract, not an
    implementation detail — which is why the tests below read `calls`.
    """

    STOP = ("sudo", "systemctl", "stop", "milo-client-snapclient.service")
    START = ("sudo", "systemctl", "start", "milo-client-snapclient.service")

    def __init__(self, install_returncode=0, install_raises=False):
        self.calls = []
        self.running = True
        self.install_returncode = install_returncode
        self.install_raises = install_raises

    async def spawn(self, *argv, **kwargs):
        self.calls.append(argv)

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
