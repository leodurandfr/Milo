# backend/tests/test_version_service.py
"""
Tests for VersionService — version detection, GitHub API, caching.
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock

from backend.core.updates.version import VersionService


@pytest.fixture
def version_service():
    """Fresh VersionService instance with no GitHub token"""
    with patch.dict("os.environ", {}, clear=True):
        return VersionService()


@pytest.fixture
def version_service_with_token():
    """VersionService with a GitHub token configured"""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_testtoken123"}):
        return VersionService()


class TestVersionServiceInit:
    """Tests for VersionService initialization"""

    def test_no_github_token(self, version_service):
        assert version_service.github_token is None

    def test_with_github_token(self, version_service_with_token):
        assert version_service_with_token.github_token == "ghp_testtoken123"

    def test_programs_configured(self, version_service):
        expected_keys = {"milo", "go-librespot", "shairport-sync", "multiroom", "bluez-alsa", "roc-toolkit"}
        assert set(version_service.programs.keys()) == expected_keys

    def test_cache_initialized_empty(self, version_service):
        assert version_service._github_cache == {}
        assert version_service._last_github_fetch == {}
        assert version_service._cache_timeout == 3600


class TestGetGithubHeaders:
    """Tests for _get_github_headers()"""

    def test_headers_without_token(self, version_service):
        headers = version_service._get_github_headers()
        assert headers["Accept"] == "application/vnd.github.v3+json"
        assert headers["User-Agent"] == "Milo-Audio-System"
        assert "Authorization" not in headers

    def test_headers_with_token(self, version_service_with_token):
        headers = version_service_with_token._get_github_headers()
        assert headers["Authorization"] == "token ghp_testtoken123"


class TestExecuteVersionCommand:
    """Tests for _execute_version_command()"""

    @pytest.mark.asyncio
    async def test_version_extracted_with_primary_regex(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"snapserver v0.28.0\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await version_service._execute_version_command(
                ["snapserver", "--version"],
                r"v(\d+\.\d+\.\d+)"
            )
        assert result == "0.28.0"

    @pytest.mark.asyncio
    async def test_version_from_stderr(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"version 1.2.3\n"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await version_service._execute_version_command(
                ["some-cmd"], r"(\d+\.\d+\.\d+)"
            )
        assert result == "1.2.3"

    @pytest.mark.asyncio
    async def test_fallback_pattern_used(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"something 4.5.6 output\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Primary regex won't match, but fallback \d+\.\d+\.\d+ will
            result = await version_service._execute_version_command(
                ["cmd"], r"NOMATCH_(\d+)"
            )
        assert result == "4.5.6"

    @pytest.mark.asyncio
    async def test_no_version_found(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"no version here\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await version_service._execute_version_command(
                ["cmd"], r"NOMATCH_(\d+)"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_raises(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                with pytest.raises(Exception, match="Command timeout"):
                    await version_service._execute_version_command(["cmd"], r"(\d+)")

    @pytest.mark.asyncio
    async def test_command_not_found(self, version_service):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            with pytest.raises(Exception, match="Command not found"):
                await version_service._execute_version_command(["nonexistent"], r"(\d+)")


class TestGetInstalledVersion:
    """Tests for get_installed_version()"""

    @pytest.mark.asyncio
    async def test_unknown_program(self, version_service):
        result = await version_service.get_installed_version("unknown_program")
        assert result["status"] == "error"
        assert result["message"] == "Unknown program"

    @pytest.mark.asyncio
    async def test_version_detected(self, version_service):
        with patch.object(version_service, "_execute_version_command", return_value="0.28.0"):
            result = await version_service.get_installed_version("multiroom")

        assert result["status"] == "installed"
        assert "snapserver" in result["versions"]
        assert result["errors"] == [] or len(result["errors"]) < len(result["versions"])

    @pytest.mark.asyncio
    async def test_no_version_detected(self, version_service):
        with patch.object(version_service, "_execute_version_command", return_value=None):
            result = await version_service.get_installed_version("bluez-alsa")

        assert result["status"] == "not_installed"
        assert result["versions"] == {}

    @pytest.mark.asyncio
    async def test_command_error_accumulated(self, version_service):
        with patch.object(
            version_service, "_execute_version_command",
            side_effect=Exception("Command not found")
        ):
            result = await version_service.get_installed_version("bluez-alsa")

        assert result["status"] == "not_installed"
        assert len(result["errors"]) > 0
        assert "Command not found" in result["errors"][0]


class TestGetLatestGithubVersion:
    """Tests for get_latest_github_version()"""

    @pytest.mark.asyncio
    async def test_unknown_program(self, version_service):
        result = await version_service.get_latest_github_version("unknown")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_successful_fetch(self, version_service):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "tag_name": "v0.28.0",
            "published_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/badaix/snapcast/releases/tag/v0.28.0"
        })

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False)
        )):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "success"
        assert result["version"] == "0.28.0"
        assert result["tag_name"] == "v0.28.0"

    @pytest.mark.asyncio
    async def test_cache_hit(self, version_service):
        cached_result = {
            "status": "success",
            "version": "0.28.0",
            "tag_name": "v0.28.0",
            "published_at": None,
            "html_url": None
        }
        version_service._github_cache["github_multiroom"] = cached_result
        version_service._last_github_fetch["github_multiroom"] = time.time()

        # Should return cached result without making HTTP request
        result = await version_service.get_latest_github_version("multiroom")
        assert result == cached_result

    @pytest.mark.asyncio
    async def test_cache_expired(self, version_service):
        cached_result = {"status": "success", "version": "0.27.0", "tag_name": "v0.27.0", "published_at": None, "html_url": None}
        version_service._github_cache["github_multiroom"] = cached_result
        version_service._last_github_fetch["github_multiroom"] = time.time() - 7200  # 2 hours ago

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "tag_name": "v0.28.0",
            "published_at": None,
            "html_url": None
        })

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False)
        )):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["version"] == "0.28.0"

    @pytest.mark.asyncio
    async def test_rate_limit_403(self, version_service):
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"message": "API rate limit exceeded"})

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False)
        )):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "error"
        assert "rate limit" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_http_error_status(self, version_service):
        mock_response = AsyncMock()
        mock_response.status = 500

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False)
        )):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "error"
        assert "500" in result["message"]

    @pytest.mark.asyncio
    async def test_timeout(self, version_service):
        with patch("aiohttp.ClientSession", side_effect=asyncio.TimeoutError()):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "error"
        assert "timeout" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_network_error(self, version_service):
        with patch("aiohttp.ClientSession", side_effect=Exception("Connection refused")):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_tag_without_matching_regex(self, version_service):
        """When tag_name doesn't match version_regex, raw tag is used"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "tag_name": "release-candidate",
            "published_at": None,
            "html_url": None
        })

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=False)
        )):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["status"] == "success"
        assert result["version"] == "release-candidate"


class TestGetProgramFullStatus:
    """Tests for _get_program_full_status()"""

    @pytest.mark.asyncio
    async def test_update_available(self, version_service):
        with patch.object(version_service, "get_installed_version", return_value={
            "status": "installed",
            "versions": {"main": "0.27.0"},
            "errors": [],
            "name": "Multiroom",
            "description": "updates.multiroom"
        }):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "success",
                "version": "0.28.0",
                "tag_name": "v0.28.0",
                "published_at": None,
                "html_url": None
            }):
                result = await version_service._get_program_full_status("multiroom")

        assert result["update_available"] is True

    @pytest.mark.asyncio
    async def test_no_update_available(self, version_service):
        with patch.object(version_service, "get_installed_version", return_value={
            "status": "installed",
            "versions": {"main": "0.28.0"},
            "errors": [],
            "name": "Multiroom",
            "description": "updates.multiroom"
        }):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "success",
                "version": "0.28.0",
                "tag_name": "v0.28.0",
                "published_at": None,
                "html_url": None
            }):
                result = await version_service._get_program_full_status("multiroom")

        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_multiroom_normalizes_snapserver_version(self, version_service):
        with patch.object(version_service, "get_installed_version", return_value={
            "status": "installed",
            "versions": {"snapserver": "0.28.0", "snapclient": "0.28.0"},
            "errors": [],
            "name": "Multiroom",
            "description": "updates.multiroom"
        }):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "success",
                "version": "0.28.0",
                "tag_name": "v0.28.0",
                "published_at": None,
                "html_url": None
            }):
                result = await version_service._get_program_full_status("multiroom")

        # Should normalize to {"main": "0.28.0"}
        assert result["installed"]["versions"] == {"main": "0.28.0"}

    @pytest.mark.asyncio
    async def test_github_error_handled(self, version_service):
        with patch.object(version_service, "get_installed_version", return_value={
            "status": "installed",
            "versions": {"main": "0.28.0"},
            "errors": [],
            "name": "Multiroom",
            "description": "updates.multiroom"
        }):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "error",
                "message": "timeout"
            }):
                result = await version_service._get_program_full_status("multiroom")

        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_installed_error_handled(self, version_service):
        with patch.object(version_service, "get_installed_version",
                          side_effect=Exception("subprocess error")):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "success", "version": "1.0.0", "tag_name": "v1.0.0",
                "published_at": None, "html_url": None
            }):
                result = await version_service._get_program_full_status("multiroom")

        # Exception is caught and converted to error dict
        assert result["update_available"] is False


class TestGetAllProgramStatus:
    """Tests for get_all_program_status()"""

    @pytest.mark.asyncio
    async def test_returns_all_programs(self, version_service):
        mock_status = {
            "name": "Test",
            "description": "test",
            "installed": {"status": "installed", "versions": {"main": "1.0.0"}},
            "latest": {"status": "success", "version": "1.0.0"},
            "update_available": False
        }

        with patch.object(version_service, "_get_program_full_status", return_value=mock_status):
            result = await version_service.get_all_program_status()

        assert set(result.keys()) == set(version_service.programs.keys())

    @pytest.mark.asyncio
    async def test_exception_captured(self, version_service):
        async def raise_for_milo(key):
            if key == "milo":
                raise Exception("milo error")
            return {"name": "Test", "update_available": False}

        with patch.object(version_service, "_get_program_full_status", side_effect=raise_for_milo):
            result = await version_service.get_all_program_status()

        assert result["milo"]["status"] == "error"
        assert "milo error" in result["milo"]["message"]


class TestGetProgramList:
    """Tests for get_program_list()"""

    def test_returns_all_programs(self, version_service):
        program_list = version_service.get_program_list()
        assert len(program_list) == 6

    def test_each_entry_has_required_fields(self, version_service):
        program_list = version_service.get_program_list()
        for entry in program_list:
            assert "key" in entry
            assert "name" in entry
            assert "description" in entry
