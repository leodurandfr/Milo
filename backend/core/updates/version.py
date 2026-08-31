# backend/core/updates/version.py
"""
Version management service - Version with GitHub token support
"""
import asyncio
import copy
import time
import aiohttp
import logging
import os
import re
from typing import Any, Dict, List, Optional

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.helpers import compare_versions

class VersionService:
    """Simplified service to manage Milo program versions"""

    def __init__(self, settings_service):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service

        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.debug("GitHub token detected - using authenticated API (5000 req/hour)")
        else:
            self.logger.debug("No GitHub token - using anonymous API (60 req/hour)")

        # One entry per program, shared with UpdateService. Copied per instance so
        # overriding a "validated_version" at runtime cannot mutate the module constant.
        self.programs = copy.deepcopy(PROGRAMS)

        # Cache to avoid repeated GitHub calls
        self._github_cache = {}
        self._cache_timeout = 3600  # 1 hour
        self._last_github_fetch = {}

    def _get_github_headers(self) -> Dict[str, str]:
        """Returns headers for GitHub requests (with token if available)"""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Milo-Audio-System"
        }

        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        return headers

    async def get_installed_version(self, program_key: str) -> Dict[str, Any]:
        """Gets the installed version of a program"""
        if program_key not in self.programs:
            return {"status": "error", "message": "Unknown program"}

        program_config = self.programs[program_key]
        result = {
            "name": program_config["name"],
            "description": program_config["description"],
            "status": "unknown",
            "versions": {},
            "errors": []
        }

        has_git_path = "git_path" in program_config
        for cmd_name, cmd_args in program_config["commands"].items():
            try:
                raw_output, version = await self._execute_version_command(cmd_args, program_config["version_regex"])
                if version:
                    result["versions"][cmd_name] = version
                    result["status"] = "installed"
                    # For git-based programs, include the raw command output (e.g. "v0.0.1-533-gc6d74a1")
                    if has_git_path and raw_output:
                        result["raw_version"] = raw_output
                else:
                    result["errors"].append(f"{cmd_name}: Version not detected")
            except Exception as e:
                result["errors"].append(f"{cmd_name}: {str(e)}")

        # If no version detected, mark as not installed
        if not result["versions"]:
            result["status"] = "not_installed"

        return result

    async def _execute_version_command(self, cmd_args: List[str], version_regex: str) -> tuple:
        """Executes a version command and extracts the version number.

        Returns (raw_output, extracted_version) tuple.
        """
        try:
            # Short timeout to avoid blocking
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

                # Search for version in stdout then stderr
                output_text = stdout.decode() + stderr.decode()
                raw_output = output_text.strip()
                match = re.search(version_regex, output_text)

                if match:
                    return raw_output, match.group(1)

                # Fallback: search for common version patterns
                fallback_patterns = [
                    r"(\d+\.\d+\.\d+)",
                    r"version (\d+\.\d+\.\d+)",
                    r"Version: (\d+\.\d+\.\d+)"
                ]

                for pattern in fallback_patterns:
                    match = re.search(pattern, output_text, re.IGNORECASE)
                    if match:
                        return raw_output, match.group(1)

                return raw_output, None

            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("Command timeout")

        except FileNotFoundError:
            raise Exception("Command not found")
        except Exception as e:
            raise Exception(f"Execution error: {str(e)}")

    async def get_forced_versions(self) -> Dict[str, str]:
        """The versions this unit was deliberately moved to, past the manifest.

        Only an entry still *ahead* of what `dependencies.env` declares counts:
        bumping the manifest to the version that was forced makes the override
        redundant, and keeping it would hold the unit below the *next* bump —
        the "landed behind" failure the manifest exists to prevent, one program
        at a time. `UpdateService._prune_forced_versions` writes that reading
        back to disk.
        """
        # No `or {}`: `_validate_and_merge` emits the section unconditionally,
        # so a missing key is a broken settings.json and must surface as one.
        stored = await self.settings_service.get_setting("updates.forced_versions")
        return {
            key: version
            for key, version in stored.items()
            if key in self.programs
            and compare_versions(self.programs[key].get("validated_version"), version)
        }

    def _release_at(self, program_key: str, fetched_tag: str, version: str) -> Dict[str, Any]:
        """Describe a release the fetch did not return: its tag, its URL, no date.

        The tag is rebuilt in the repo's own convention — some tag "v1.2.3"
        (go-librespot, snapcast), others "1.2.3" (shairport-sync). Guessing wrong
        points the source download at a tag that doesn't exist. Derived from the
        fetched tag rather than restated in the manifest, which carries bare
        versions only. The publication date belongs to the fetched release and
        not to this one, so it is dropped rather than carried over.
        """
        prefix = "v" if fetched_tag.startswith("v") else ""
        repo = self.programs[program_key]["repo"]
        return {
            "version": version,
            "tag_name": f"{prefix}{version}",
            "html_url": f"https://github.com/{repo}/releases/tag/{prefix}{version}",
            "published_at": None,
        }

    async def _apply_pin(self, program_key: str, fetched: Dict[str, Any]) -> Dict[str, Any]:
        """Point the offered release at the version this unit is pinned to.

        Pin, not ceiling: what the manifest declares is what is offered, whether
        upstream is ahead of it or (a yanked release, a manifest bumped early)
        behind. A clamp would let an upstream release below the manifest
        through, which would make dependencies.env something less than the
        source of truth it is declared to be.

        A forced version outranks the manifest for as long as it stays ahead of
        it — that *is* "this unit is deliberately off-pin", and the `validated`
        block it adds is what the return button installs. `upstream` keeps what
        upstream actually offers: the clamp used to overwrite it, so the
        maintainer surface — the one place the decision to bump the set is taken
        — could not show what there was to bump to.
        """
        validated = self.programs[program_key].get("validated_version")
        if not validated:
            # milo: the app, not a dependency. It has no manifest line, so the
            # latest release is what it offers.
            return fetched

        forced = (await self.get_forced_versions()).get(program_key)
        target = forced or validated

        result = dict(fetched)
        result["upstream"] = {
            "version": fetched["version"],
            "tag_name": fetched["tag_name"],
            "published_at": fetched["published_at"],
            "html_url": fetched["html_url"],
            "ahead": compare_versions(target, fetched["version"]),
        }
        result.update(self._release_at(program_key, fetched["tag_name"], target))
        if forced:
            result["validated"] = self._release_at(program_key, fetched["tag_name"], validated)
        return result

    async def get_latest_github_version(self, program_key: str) -> Dict[str, Any]:
        """The release this unit is meant to run, and what upstream has.

        The fetch is cached raw and the pin applied on every call: the pin is
        not a property of the fetch. A forced version is written while the cache
        is still warm, and a Milo update replaces the manifest under a process
        that read it minutes earlier.
        """
        fetched = await self._fetch_latest_release(program_key)
        if fetched.get("status") != "success":
            return fetched
        return await self._apply_pin(program_key, fetched)

    async def _fetch_latest_release(self, program_key: str) -> Dict[str, Any]:
        """The program's latest upstream release, cached for an hour."""
        if program_key not in self.programs:
            return {"status": "error", "message": "Unknown program"}

        repo = self.programs[program_key]["repo"]

        current_time = time.time()
        cache_key = f"github_{program_key}"

        if (cache_key in self._github_cache and
            cache_key in self._last_github_fetch and
            current_time - self._last_github_fetch[cache_key] < self._cache_timeout):
            return self._github_cache[cache_key]

        try:
            # Call GitHub API with headers (including token if available)
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            headers = self._get_github_headers()

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        version_regex = self.programs[program_key]["version_regex"]
                        match = re.search(version_regex, tag_name)

                        if match:
                            result = {
                                "status": "success",
                                "version": match.group(1),
                                "tag_name": tag_name,
                                "published_at": data.get("published_at"),
                                "html_url": data.get("html_url")
                            }
                        else:
                            result = {
                                "status": "success",
                                "version": tag_name,
                                "tag_name": tag_name,
                                "published_at": data.get("published_at"),
                                "html_url": data.get("html_url")
                            }

                        self._github_cache[cache_key] = result
                        self._last_github_fetch[cache_key] = current_time

                        return result

                    elif response.status == 403:
                        error_data = await response.json()
                        error_message = error_data.get("message", "Rate limit exceeded")

                        if self.github_token:
                            self.logger.warning(f"GitHub API error despite token: {error_message}")
                        else:
                            self.logger.warning("GitHub API rate limit - consider adding GITHUB_TOKEN")

                        return {"status": "error", "message": error_message}
                    else:
                        return {"status": "error", "message": f"GitHub API error: {response.status}"}

        except asyncio.TimeoutError:
            return {"status": "error", "message": "GitHub API timeout"}
        except Exception as e:
            return {"status": "error", "message": f"GitHub API error: {str(e)}"}

    async def get_all_program_status(self) -> Dict[str, Any]:
        """Gets the status of all programs"""
        results = {}

        # Get installed versions in parallel
        tasks = []
        for program_key in self.programs.keys():
            tasks.append(self.get_program_full_status(program_key))

        program_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, program_key in enumerate(self.programs.keys()):
            if isinstance(program_results[i], Exception):
                results[program_key] = {
                    "status": "error",
                    "message": str(program_results[i])
                }
            else:
                results[program_key] = program_results[i]

        return results

    def _reconcile_offer(
        self,
        program_key: str,
        latest: Dict[str, Any],
        installed: str,
        update_available: bool,
    ) -> None:
        """Make the offer readable on a unit sitting ABOVE the version it is pinned to.

        Two ways in, and neither records a trial: a manifest deliberately rolled
        back (a yanked release), and a `forced_versions` write that failed after
        the install went through. `_apply_pin` emits the `validated` block — the
        one the return button is built from — only while an override is active,
        so such a unit is offered nothing at all: `update_available` is false
        (the pin is older than what it runs) and the row reads "up to date" on a
        release nobody validated. That is the exact state this whole surface
        exists to make impossible, and `_reconcile_dependencies` only undoes it
        during a Milo app update — never from the screen.

        Two corrections, both of them about what the row may claim:

          * name the pin as `validated`, so the return button appears and
            `_select_target(status, "validated")` installs it;
          * drop `upstream.ahead` when the unit already runs that upstream
            release. `ahead` is measured against the pin on purpose — a unit
            *behind* the set still sees what there is to try — and this is the
            one case it gets wrong, drawing "1.6.0 > 1.6.0" beside an Update
            button that reinstalls what is already installed.

        A pinned program only: `milo` is the app, not a dependency, and the
        latest release is simply what it offers.
        """
        if not self.programs[program_key].get("validated_version"):
            return

        upstream = latest.get("upstream")
        if upstream and upstream.get("version") == installed:
            upstream["ahead"] = False

        if update_available or installed == latest.get("version") or "validated" in latest:
            return

        latest["validated"] = {
            key: latest.get(key)
            for key in ("version", "tag_name", "html_url", "published_at")
        }

    @staticmethod
    def installed_version(status: Dict[str, Any]) -> Optional[str]:
        """The one version a full status reports as installed.

        Multiroom's two components are normalised to a single entry below, so
        the first value is the only value for every program.
        """
        return next(iter(status.get("installed", {}).get("versions", {}).values()), None)

    async def get_program_full_status(self, program_key: str) -> Dict[str, Any]:
        """Gets complete status (installed + GitHub) for a program"""
        try:
            # Launch both requests in parallel
            installed_task = self.get_installed_version(program_key)
            github_task = self.get_latest_github_version(program_key)

            installed_result, github_result = await asyncio.gather(
                installed_task, github_task, return_exceptions=True
            )

            if isinstance(installed_result, Exception):
                installed_result = {"status": "error", "message": str(installed_result)}

            if isinstance(github_result, Exception):
                github_result = {"status": "error", "message": str(github_result)}

            # For multiroom, normalize to single canonical version (use snapserver)
            if program_key == "multiroom" and installed_result.get("status") == "installed":
                versions = installed_result.get("versions", {})
                canonical_version = versions.get("snapserver") or versions.get("snapclient")
                if canonical_version:
                    installed_result["versions"] = {"main": canonical_version}

            result = {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "installed": installed_result,
                "latest": github_result,
                "update_available": False
            }

            # Determine if an update is available
            if (installed_result.get("status") == "installed" and
                github_result.get("status") == "success"):

                installed_version = self.installed_version(result)
                latest_version = github_result.get("version")

                if installed_version and latest_version:
                    result["update_available"] = compare_versions(installed_version, latest_version)
                    self._reconcile_offer(
                        program_key, github_result, installed_version,
                        result["update_available"],
                    )

            return result

        except Exception as e:
            return {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "status": "error",
                "message": str(e)
            }
