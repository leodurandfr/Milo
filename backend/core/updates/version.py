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
from typing import Dict, Any, List

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.helpers import compare_versions

class VersionService:
    """Simplified service to manage Milo program versions"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.debug("GitHub token detected - using authenticated API (5000 req/hour)")
        else:
            self.logger.debug("No GitHub token - using anonymous API (60 req/hour)")

        # One entry per program, shared with UpdateService. Copied per instance so
        # arming a "max_version" ceiling at runtime cannot mutate the module constant.
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

    async def get_latest_github_version(self, program_key: str) -> Dict[str, Any]:
        """Gets the latest version from GitHub with cache and token"""
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

                        # Apply optional version ceiling: never offer an upstream
                        # release newer than the program's pinned known-good version
                        # (compare_versions(max, fetched) is True when fetched > max).
                        max_version = self.programs[program_key].get("max_version")
                        if max_version and compare_versions(max_version, result["version"]):
                            self.logger.info(
                                f"{program_key}: upstream {result['version']} exceeds pinned "
                                f"ceiling {max_version}; offering {max_version} instead"
                            )
                            # Rebuild the tag in the repo's own convention — some tag
                            # "v1.2.3" (go-librespot, snapcast), others "1.2.3"
                            # (shairport-sync). Guessing wrong makes the ceiling point
                            # the source download at a tag that doesn't exist.
                            prefix = "v" if result["tag_name"].startswith("v") else ""
                            result["version"] = max_version
                            result["tag_name"] = f"{prefix}{max_version}"
                            result["html_url"] = (
                                f"https://github.com/{repo}/releases/tag/{prefix}{max_version}"
                            )
                            result["published_at"] = None

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

                # Take the first installed version for comparison
                installed_versions = installed_result.get("versions", {})
                if installed_versions:
                    installed_version = list(installed_versions.values())[0]
                    latest_version = github_result.get("version")

                    if installed_version and latest_version:
                        result["update_available"] = compare_versions(installed_version, latest_version)

            return result

        except Exception as e:
            return {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "status": "error",
                "message": str(e)
            }
