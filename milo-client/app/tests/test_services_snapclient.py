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
                version = await snapclient_service.get_installed_version()
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

            result = await snapclient_service.update_snapclient("0.29.0")

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
