# backend/tests/test_version_service.py
"""
Tests for VersionService — version detection, GitHub API, caching.
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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
        expected_keys = {"milo", "go-librespot", "shairport-sync", "multiroom", "camilladsp", "qobuz-proxy", "navidrome"}
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
            raw_output, version = await version_service._execute_version_command(
                ["snapserver", "--version"],
                r"v(\d+\.\d+\.\d+)"
            )
        assert version == "0.28.0"
        assert "snapserver" in raw_output

    @pytest.mark.asyncio
    async def test_version_from_stderr(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"version 1.2.3\n"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            raw_output, version = await version_service._execute_version_command(
                ["some-cmd"], r"(\d+\.\d+\.\d+)"
            )
        assert version == "1.2.3"

    @pytest.mark.asyncio
    async def test_fallback_pattern_used(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"something 4.5.6 output\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            # Primary regex won't match, but fallback \d+\.\d+\.\d+ will
            raw_output, version = await version_service._execute_version_command(
                ["cmd"], r"NOMATCH_(\d+)"
            )
        assert version == "4.5.6"

    @pytest.mark.asyncio
    async def test_no_version_found(self, version_service):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"no version here\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            raw_output, version = await version_service._execute_version_command(
                ["cmd"], r"NOMATCH_(\d+)"
            )
        assert version is None

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
        with patch.object(version_service, "_execute_version_command", return_value=("snapserver v0.28.0", "0.28.0")):
            result = await version_service.get_installed_version("multiroom")

        assert result["status"] == "installed"
        assert "snapserver" in result["versions"]
        assert result["errors"] == [] or len(result["errors"]) < len(result["versions"])

    @pytest.mark.asyncio
    async def test_no_version_detected(self, version_service):
        with patch.object(version_service, "_execute_version_command", return_value=None):
            result = await version_service.get_installed_version("go-librespot")

        assert result["status"] == "not_installed"
        assert result["versions"] == {}

    @pytest.mark.asyncio
    async def test_command_error_accumulated(self, version_service):
        with patch.object(
            version_service, "_execute_version_command",
            side_effect=Exception("Command not found")
        ):
            result = await version_service.get_installed_version("go-librespot")

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
        # Every dependency is pinned, so what is *offered* is the validated
        # version and the fetched release lands under "upstream". Both halves
        # are read from the service's own config rather than restated here.
        assert result["version"] == version_service.programs["multiroom"]["validated_version"]
        assert result["upstream"]["version"] == "0.28.0"
        assert result["upstream"]["tag_name"] == "v0.28.0"

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

        # The stale entry said 0.27.0 and carried no "upstream" at all, so this
        # can only have come from a refetch.
        assert result["upstream"]["version"] == "0.28.0"

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
        """When tag_name doesn't match version_regex, the raw tag is carried through.

        The pin decides what is *offered*; this is about what was *read*, so the
        raw tag now surfaces under "upstream" instead of at the top level.
        """
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
        assert result["upstream"]["version"] == "release-candidate"


def _patch_github_release(tag_name: str):
    """Returns a patch() for aiohttp.ClientSession that yields a release with tag_name."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={
        "tag_name": tag_name,
        "published_at": "2026-05-21T00:00:00Z",
        "html_url": f"https://github.com/devgianlu/go-librespot/releases/tag/{tag_name}"
    })

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_response),
        __aexit__=AsyncMock(return_value=False)
    ))

    return patch("aiohttp.ClientSession", return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=False)
    ))


class TestValidatedVersionPin:
    """Tests for the pin every dependency carries (`validated_version`).

    The version each dependency's entry declares is read from the repo's
    `dependencies.env`, the one place any of these numbers exists. It is a pin,
    not a ceiling: what the manifest says is what the update flow offers, and
    the release GitHub actually published is kept alongside rather than
    overwritten — the maintainer surface needs it to decide whether to bump.

    Most tests here arm the pin in-fixture on a chosen value so the assertion
    does not move every time the real set is bumped;
    `test_the_pin_is_armed_on_the_real_catalog` is the one that reads production.
    """

    @pytest.mark.asyncio
    async def test_upstream_above_the_validated_version_is_not_offered(self, version_service):
        """The whole point: a newer upstream release must not reach a unit.

        shairport-sync 5.1 shipped exactly this way — no error, no log line,
        AirPlay metadata simply gone. The offered version stays the validated
        one and the fetched release survives under "upstream".
        """
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"
        with _patch_github_release("v0.9.9"):
            result = await version_service.get_latest_github_version("go-librespot")

        assert result["version"] == "0.7.2"
        assert result["tag_name"] == "v0.7.2"
        assert result["html_url"].endswith("/releases/tag/v0.7.2")
        # The validated release's own publication date is not what this fetch
        # returned, so it is cleared rather than mislabelled.
        assert result["published_at"] is None

        assert result["upstream"]["version"] == "0.9.9"
        assert result["upstream"]["tag_name"] == "v0.9.9"
        assert result["upstream"]["published_at"] == "2026-05-21T00:00:00Z"
        assert result["upstream"]["ahead"] is True

    @pytest.mark.asyncio
    async def test_upstream_below_the_validated_version_is_not_offered_either(self, version_service):
        """A pin, not a clamp — this is what separates the two.

        A yanked release, or a set bumped ahead of the tag being published,
        makes `releases/latest` answer *below* the manifest. A clamp would let
        that through and install it, which would quietly make GitHub, not
        `dependencies.env`, the thing that decides what the appliance runs.
        """
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"
        with _patch_github_release("v0.7.1"):
            result = await version_service.get_latest_github_version("go-librespot")

        assert result["version"] == "0.7.2"
        assert result["tag_name"] == "v0.7.2"
        assert result["upstream"]["version"] == "0.7.1"
        assert result["upstream"]["ahead"] is False

    @pytest.mark.asyncio
    async def test_the_pinned_tag_keeps_the_repo_tag_convention(self, version_service):
        """Some repos tag "v1.2.3", others "1.2.3", and the manifest holds neither.

        It carries bare versions, so the prefix is derived from the tag actually
        fetched. Guessing it wrong points the source download at a tag that does
        not exist — a failed build rather than a wrong install, but only after
        several minutes of compiling.
        """
        version_service.programs["shairport-sync"]["validated_version"] = "4.3.7"
        with _patch_github_release("5.1"):
            result = await version_service.get_latest_github_version("shairport-sync")

        assert result["version"] == "4.3.7"
        assert result["tag_name"] == "4.3.7"
        assert result["html_url"].endswith("/releases/tag/4.3.7")

    @pytest.mark.asyncio
    async def test_the_pin_is_armed_on_the_real_catalog(self, version_service):
        """End to end: `dependencies.env` -> catalog -> the version offered.

        The structural half (every dependency declares one, read by lookup) is
        proven by `tests/architecture/test_dependency_manifest.py`. What is
        proven here is that the declaration is actually *wired* — an entry read
        into a key nothing consults would pass every structural rule and still
        hand the unit whatever upstream released.
        """
        validated = version_service.programs["navidrome"]["validated_version"]
        with _patch_github_release("v99.0.0"):
            result = await version_service.get_latest_github_version("navidrome")

        assert result["version"] == validated
        assert result["upstream"]["version"] == "99.0.0"

    @pytest.mark.asyncio
    async def test_the_app_itself_is_not_pinned(self, version_service):
        """`milo` is the app, not a dependency: it updates to whatever main is.

        Pinning it would mean the appliance could never update itself, and the
        absent "upstream" key is what tells a consumer this row has no set to
        compare against.
        """
        assert "validated_version" not in version_service.programs["milo"]

        with _patch_github_release("v9.9.9"):
            result = await version_service.get_latest_github_version("milo")

        assert result["version"] == "9.9.9"
        assert "upstream" not in result


class TestGetProgramFullStatus:
    """Tests for get_program_full_status()"""

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
                result = await version_service.get_program_full_status("multiroom")

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
                result = await version_service.get_program_full_status("multiroom")

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
                result = await version_service.get_program_full_status("multiroom")

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
                result = await version_service.get_program_full_status("multiroom")

        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_installed_error_handled(self, version_service):
        with patch.object(version_service, "get_installed_version",
                          side_effect=Exception("subprocess error")):
            with patch.object(version_service, "get_latest_github_version", return_value={
                "status": "success", "version": "1.0.0", "tag_name": "v1.0.0",
                "published_at": None, "html_url": None
            }):
                result = await version_service.get_program_full_status("multiroom")

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

        with patch.object(version_service, "get_program_full_status", return_value=mock_status):
            result = await version_service.get_all_program_status()

        assert set(result.keys()) == set(version_service.programs.keys())

    @pytest.mark.asyncio
    async def test_exception_captured(self, version_service):
        async def raise_for_milo(key):
            if key == "milo":
                raise Exception("milo error")
            return {"name": "Test", "update_available": False}

        with patch.object(version_service, "get_program_full_status", side_effect=raise_for_milo):
            result = await version_service.get_all_program_status()

        assert result["milo"]["status"] == "error"
        assert "milo error" in result["milo"]["message"]


class TestVersionDetectionResidue:
    """The three arms the programs panel reads when detection half-works."""

    @pytest.mark.asyncio
    async def test_a_git_program_carries_the_raw_describe_alongside_the_number(
            self, version_service):
        """Milō's own version is `v0.1.0-1673-gdeadbee`; the regex reduces it to
        `0.1.0`, which is the same for every commit since the tag. The satellite
        update path compares the *raw* describe on both sides, so dropping it
        makes every satellite look current."""
        with patch.object(version_service, "_execute_version_command",
                          return_value=("v0.1.0-1673-gdeadbee", "0.1.0")):
            result = await version_service.get_installed_version("milo")

        assert result["status"] == "installed"
        assert result["versions"]["main"] == "0.1.0"
        assert result["raw_version"] == "v0.1.0-1673-gdeadbee"

    @pytest.mark.asyncio
    async def test_a_program_without_a_checkout_carries_no_raw_version(self, version_service):
        """`raw_version` only means something for a git tree; on a binary it
        would be the `--version` banner, and the satellite comparison would then
        be against a string that never matches."""
        with patch.object(version_service, "_execute_version_command",
                          return_value=("snapserver v0.28.0", "0.28.0")):
            result = await version_service.get_installed_version("multiroom")

        assert "raw_version" not in result

    @pytest.mark.asyncio
    async def test_output_the_regex_cannot_read_is_an_error_not_a_version(
            self, version_service):
        """A binary that prints an unexpected banner after an upstream change
        must read as not-installed, so the panel offers a reinstall — rather
        than as installed at some version nobody parsed."""
        with patch.object(version_service, "_execute_version_command",
                          return_value=("shairport-sync unknown build", None)):
            result = await version_service.get_installed_version("shairport-sync")

        assert result["status"] == "not_installed"
        assert result["errors"] == ["main: Version not detected"]

    @pytest.mark.asyncio
    async def test_a_command_that_raises_is_collected_not_propagated(self, version_service):
        """`get_all_program_status` gathers seven of these; one raising would
        empty the whole panel."""
        with patch.object(version_service, "_execute_version_command",
                          side_effect=FileNotFoundError("shairport-sync")):
            result = await version_service.get_installed_version("shairport-sync")

        assert result["status"] == "not_installed"
        assert "shairport-sync" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_a_rate_limit_hit_with_a_token_configured_is_reported_differently(
            self, version_service_with_token):
        """Without a token, 403 means "wait an hour" and the log says to add
        one. With a token it means the token is wrong or exhausted, and telling
        the operator to add the token they already added is the wrong advice."""
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"message": "Bad credentials"})
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False)))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False))), \
                patch.object(version_service_with_token.logger, "warning") as warned:
            result = await version_service_with_token.get_latest_github_version("multiroom")

        assert result == {"status": "error", "message": "Bad credentials"}
        assert "despite token" in warned.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_github_read_that_raises_is_turned_into_an_error_entry(
            self, version_service):
        """`asyncio.gather(..., return_exceptions=True)` hands the raw exception
        back; without the isinstance arm the next line calls `.get` on it."""
        with patch.object(version_service, "get_installed_version", return_value={
                "status": "installed", "versions": {"main": "0.28.0"}, "errors": []}), \
                patch.object(version_service, "get_latest_github_version",
                             side_effect=OSError("Name or service not known")):
            result = await version_service.get_program_full_status("multiroom")

        assert result["latest"]["status"] == "error"
        assert "Name or service not known" in result["latest"]["message"]
        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_a_comparison_that_blows_up_becomes_an_error_row(self, version_service):
        """`get_all_program_status` gathers seven of these; one raising past the
        handler would empty the whole programs panel instead of greying out one
        row. Note the limit of that handler, measured and left as it is: it
        builds its answer from `self.programs[program_key]`, so it can only
        catch failures for a key that exists — which every one of its four call
        sites already guarantees."""
        with patch.object(version_service, "get_installed_version", return_value={
                "status": "installed", "versions": {"main": "0.28.0"}, "errors": []}), \
                patch.object(version_service, "get_latest_github_version", return_value={
                    "status": "success", "version": "0.29.0"}), \
                patch("backend.core.updates.version.compare_versions",
                      side_effect=ValueError("invalid literal for int()")):
            result = await version_service.get_program_full_status("multiroom")

        assert result["status"] == "error"
        assert "invalid literal" in result["message"]
        assert result["name"] == version_service.programs["multiroom"]["name"]
