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
def version_service(mock_settings_service):
    """Fresh VersionService instance with no GitHub token"""
    # A healthy settings.json always carries the section: `_validate_and_merge`
    # emits it unconditionally, and the update service reads it without a
    # fallback so a degraded read surfaces instead of passing as "none forced".
    mock_settings_service._storage["updates.forced_versions"] = {}
    with patch.dict("os.environ", {}, clear=True):
        return VersionService(mock_settings_service)


@pytest.fixture
def version_service_with_token(mock_settings_service):
    """VersionService with a GitHub token configured"""
    # A healthy settings.json always carries the section: `_validate_and_merge`
    # emits it unconditionally, and the update service reads it without a
    # fallback so a degraded read surfaces instead of passing as "none forced".
    mock_settings_service._storage["updates.forced_versions"] = {}
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_testtoken123"}):
        return VersionService(mock_settings_service)


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
        """A warm cache answers without a fetch — and the pin still applies.

        What is cached is the release GitHub returned; which version the unit is
        meant to run is resolved on every call, because a forced version is
        written while the cache is still warm and would otherwise stay invisible
        for an hour.
        """
        version_service._github_cache["github_multiroom"] = {
            "status": "success",
            "version": "0.28.0",
            "tag_name": "v0.28.0",
            "published_at": None,
            "html_url": None
        }
        version_service._last_github_fetch["github_multiroom"] = time.time()

        with patch("aiohttp.ClientSession", side_effect=AssertionError("refetched a warm cache")):
            result = await version_service.get_latest_github_version("multiroom")

        assert result["version"] == version_service.programs["multiroom"]["validated_version"]
        assert result["upstream"]["version"] == "0.28.0"

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


class TestForcedVersions:
    """Tests for a version deliberately installed past the manifest.

    The maintainer surface can install what upstream published beyond
    `dependencies.env`, to try it before the set is bumped. Everything the UI
    draws from that state — which version is offered, which one the return
    button installs, whether upstream still has something newer — is decided
    here, and nothing downstream compares versions again.
    """

    @pytest.mark.asyncio
    async def test_a_forced_version_is_the_one_the_unit_is_offered(self, version_service, mock_settings_service):
        """The override outranks the manifest, and says so in the payload.

        Without `validated` alongside it there is no way back: the manifest's
        version appears nowhere else in the answer, so the return button would
        have nothing to install and the unit would stay off-pin for good.
        """
        version_service.programs["shairport-sync"]["validated_version"] = "5.2.2"
        mock_settings_service._storage["updates.forced_versions"] = {"shairport-sync": "5.2.3"}

        with _patch_github_release("5.2.3"):
            result = await version_service.get_latest_github_version("shairport-sync")

        assert result["version"] == "5.2.3"
        assert result["tag_name"] == "5.2.3"
        assert result["validated"]["version"] == "5.2.2"
        assert result["validated"]["tag_name"] == "5.2.2"
        # Nothing left to install: the trial already runs what upstream has.
        assert result["upstream"]["ahead"] is False

    @pytest.mark.asyncio
    async def test_upstream_moving_past_a_forced_version_is_still_offered(self, version_service, mock_settings_service):
        """A trial does not freeze the row — `ahead` measures against what runs.

        Comparing upstream to the manifest instead would leave `ahead` true for
        as long as the override lived, offering an update to the version the
        unit already runs.
        """
        version_service.programs["shairport-sync"]["validated_version"] = "5.2.2"
        mock_settings_service._storage["updates.forced_versions"] = {"shairport-sync": "5.2.3"}

        with _patch_github_release("5.2.4"):
            result = await version_service.get_latest_github_version("shairport-sync")

        assert result["version"] == "5.2.3"
        assert result["upstream"]["version"] == "5.2.4"
        assert result["upstream"]["ahead"] is True
        assert result["validated"]["version"] == "5.2.2"

    @pytest.mark.asyncio
    async def test_an_override_the_manifest_caught_up_with_is_ignored(self, version_service, mock_settings_service):
        """Bumping the set to the version that was forced ends the trial.

        Left standing, the entry would hold the unit at 5.2.3 through every
        later bump — the manifest landing *behind* the fleet, one program at a
        time, which is the failure it exists to prevent.
        """
        version_service.programs["shairport-sync"]["validated_version"] = "5.2.4"
        mock_settings_service._storage["updates.forced_versions"] = {"shairport-sync": "5.2.3"}

        with _patch_github_release("5.2.4"):
            result = await version_service.get_latest_github_version("shairport-sync")

        assert result["version"] == "5.2.4"
        assert "validated" not in result

    @pytest.mark.asyncio
    async def test_an_entry_naming_no_program_is_dropped(self, version_service, mock_settings_service):
        """A key nothing in the catalog answers to must not reach the flows.

        `_apply_pin` would hand it to a download that builds its URL from the
        catalog entry — so the read filters on the catalog rather than trusting
        what is stored.
        """
        mock_settings_service._storage["updates.forced_versions"] = {"not-a-program": "9.9.9"}

        assert await version_service.get_forced_versions() == {}


class TestOffPinByAccident:
    """A unit running a version ABOVE the pin, with nothing recording a trial.

    Two ways in and neither writes `updates.forced_versions`: a manifest
    deliberately rolled back (a yanked release), and a strict record write that
    failed after the install went through — the branch `f31048a1` added so that
    state would at least be reported.

    What broke: `_apply_pin` emits the `validated` block only while an override
    is active, so the row had no return button, `update_available` was false
    (the pin is older than what runs), and the screen read "up to date" on a
    release nobody validated. `_reconcile_dependencies` brings such a unit back,
    but only during a Milo app update — never from this screen.
    """

    @staticmethod
    def _installed(version: str):
        return patch.object(VersionService, "get_installed_version", return_value={
            "status": "installed",
            "versions": {"main": version},
            "errors": [],
            "name": "go-librespot",
            "description": "updates.spotifyConnect",
        })

    @pytest.mark.asyncio
    async def test_a_unit_above_the_pin_is_offered_the_way_back(self, version_service):
        """The pin is named as `validated`, which is what the return button installs."""
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"

        with _patch_github_release("v0.9.9"), self._installed("0.9.9"):
            result = await version_service.get_program_full_status("go-librespot")

        latest = result["latest"]
        assert result["update_available"] is False
        assert latest["version"] == "0.7.2"
        assert latest["validated"]["version"] == "0.7.2"
        assert latest["validated"]["tag_name"] == "v0.7.2"
        # And the row stops offering an update to the release it already runs:
        # `ahead` is measured against the pin, which is right for a unit behind
        # the set and wrong for this one — it drew "0.9.9 > 0.9.9".
        assert latest["upstream"]["version"] == "0.9.9"
        assert latest["upstream"]["ahead"] is False

    @pytest.mark.asyncio
    async def test_a_unit_above_a_rolled_back_pin_is_offered_the_way_back(self, version_service):
        """The worst variant: upstream came back down too, so nothing was offered at all.

        A yanked release disappears from `releases/latest`, so the fetch answers
        the manifest's own version and `upstream.ahead` is false. Without a
        `validated` block the row had no button of any kind and said "up to
        date" while the unit ran the yanked release.
        """
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"

        with _patch_github_release("v0.7.2"), self._installed("0.9.9"):
            result = await version_service.get_program_full_status("go-librespot")

        assert result["update_available"] is False
        assert result["latest"]["validated"]["version"] == "0.7.2"
        assert result["latest"]["upstream"]["ahead"] is False

    @pytest.mark.asyncio
    async def test_a_recorded_trial_is_untouched(self, version_service, mock_settings_service):
        """The deliberate off-pin unit already had all of this, and must keep it.

        `_apply_pin` names the forced version as the offer and the manifest as
        `validated`; the correction above must not overwrite either, or the
        return button would install the trial it is meant to end.
        """
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"
        mock_settings_service._storage["updates.forced_versions"] = {"go-librespot": "0.9.9"}

        with _patch_github_release("v0.9.9"), self._installed("0.9.9"):
            result = await version_service.get_program_full_status("go-librespot")

        latest = result["latest"]
        assert latest["version"] == "0.9.9"
        assert latest["validated"]["version"] == "0.7.2"
        assert latest["upstream"]["ahead"] is False

    @pytest.mark.asyncio
    async def test_a_unit_behind_the_pin_keeps_the_ordinary_update(self, version_service):
        """Catching up is not a return: no `validated` block, and the trial stays on offer."""
        version_service.programs["go-librespot"]["validated_version"] = "0.7.2"

        with _patch_github_release("v0.9.9"), self._installed("0.5.0"):
            result = await version_service.get_program_full_status("go-librespot")

        assert result["update_available"] is True
        assert "validated" not in result["latest"]
        assert result["latest"]["upstream"]["ahead"] is True

    @pytest.mark.asyncio
    async def test_the_app_itself_is_never_given_a_return_button(self, version_service):
        """`milo` has no pin, so "above the offered release" means nothing for it.

        A dev unit sits on a tag GitHub has not released, which would otherwise
        grow a return button offering to install the app's own past.
        """
        with _patch_github_release("v0.1.0"), patch.object(
            VersionService, "get_installed_version", return_value={
                "status": "installed",
                "versions": {"main": "0.2.0"},
                "errors": [],
                "name": "Milō",
                "description": "updates.miloApp",
            }
        ):
            result = await version_service.get_program_full_status("milo")

        assert "validated" not in result["latest"]
        assert "upstream" not in result["latest"]


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
