# backend/infrastructure/services/program_version_service.py
"""
Program version management service - Version with GitHub token support
"""
import asyncio
import aiohttp
import logging
import re
import os
from typing import Dict, Any, Optional, List
from pathlib import Path

class ProgramVersionService:
    """Simplified service to manage Milo program versions"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Get GitHub token from environment (optional)
        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.info("GitHub token detected - using authenticated API (5000 req/hour)")
        else:
            self.logger.info("No GitHub token - using anonymous API (60 req/hour)")

        # Program configuration (snapserver and snapclient separated)
        self.programs = {
            "milo": {
                "name": "Milō",
                "description": "updates.miloApp",
                "commands": {
                    "main": ["git", "-C", "/home/milo/milo", "describe", "--tags", "--always"]
                },
                "repo": "Leshauts/Milo",
                "version_regex": r"v?(\d+\.\d+\.\d+)",
                "git_path": "/home/milo/milo"
            },
            "go-librespot": {
                "name": "go-librespot",
                "description": "updates.spotifyConnect",
                "commands": {
                    "main": ["sh", "-c", "strings /usr/local/bin/go-librespot | grep 'Bv[0-9]' | sed 's/^.*Bv//' | head -1"]
                },
                "repo": "devgianlu/go-librespot",
                "version_regex": r"(\d+\.\d+\.\d+)"
            },
            "multiroom": {
                "name": "Multiroom",
                "description": "updates.multiroom",
                "commands": {
                    "snapserver": ["snapserver", "--version"],
                    "snapclient": ["snapclient", "--version"]
                },
                "repo": "badaix/snapcast",
                "version_regex": r"v(\d+\.\d+\.\d+)"
            },
            "bluez-alsa": {
                "name": "BlueZ ALSA",
                "description": "updates.bluetoothAudio",
                "commands": {
                    "main": ["bluealsa", "--version"]
                },
                "repo": "arkq/bluez-alsa",
                "version_regex": r"v(\d+\.\d+\.\d+)"
            },
            "roc-toolkit": {
                "name": "ROC Streaming",
                "description": "updates.macStreaming",
                "commands": {
                    "recv": ["roc-recv", "--version"]
                },
                "repo": "roc-streaming/roc-toolkit",
                "version_regex": r"roc-recv (\d+\.\d+\.\d+)"
            }
        }

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

        # Try to retrieve versions for each command
        for cmd_name, cmd_args in program_config["commands"].items():
            try:
                version = await self._execute_version_command(cmd_args, program_config["version_regex"])
                if version:
                    result["versions"][cmd_name] = version
                    result["status"] = "installed"
                else:
                    result["errors"].append(f"{cmd_name}: Version not detected")
            except Exception as e:
                result["errors"].append(f"{cmd_name}: {str(e)}")

        # If no version detected, mark as not installed
        if not result["versions"]:
            result["status"] = "not_installed"

        return result

    async def _execute_version_command(self, cmd_args: List[str], version_regex: str) -> Optional[str]:
        """Executes a version command and extracts the number"""
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
                match = re.search(version_regex, output_text)

                if match:
                    return match.group(1)

                # Fallback: search for common version patterns
                fallback_patterns = [
                    r"(\d+\.\d+\.\d+)",
                    r"version (\d+\.\d+\.\d+)",
                    r"Version: (\d+\.\d+\.\d+)"
                ]

                for pattern in fallback_patterns:
                    match = re.search(pattern, output_text, re.IGNORECASE)
                    if match:
                        return match.group(1)

                return None

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

        # Check cache
        import time
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

                        # Extract version number
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

                        # Cache result
                        self._github_cache[cache_key] = result
                        self._last_github_fetch[cache_key] = current_time

                        return result

                    elif response.status == 403:
                        error_data = await response.json()
                        error_message = error_data.get("message", "Rate limit exceeded")

                        if self.github_token:
                            self.logger.warning(f"GitHub API error despite token: {error_message}")
                        else:
                            self.logger.warning(f"GitHub API rate limit - consider adding GITHUB_TOKEN")

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
            tasks.append(self._get_program_full_status(program_key))

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

    async def _get_program_full_status(self, program_key: str) -> Dict[str, Any]:
        """Gets complete status (installed + GitHub) for a program"""
        try:
            # Launch both requests in parallel
            installed_task = self.get_installed_version(program_key)
            github_task = self.get_latest_github_version(program_key)

            installed_result, github_result = await asyncio.gather(
                installed_task, github_task, return_exceptions=True
            )

            # Handle exceptions
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

            # Combine results
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
                        result["update_available"] = self._compare_versions(installed_version, latest_version)

            return result

        except Exception as e:
            return {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "status": "error",
                "message": str(e)
            }

    def _compare_versions(self, installed: str, latest: str) -> bool:
        """Compares two semver versions (returns True if update available)"""
        try:
            def parse_version(version_str):
                # Clean and parse version
                clean_version = re.sub(r'[^\d.]', '', version_str)
                parts = clean_version.split('.')
                # Ensure we have at least 3 parts
                while len(parts) < 3:
                    parts.append('0')
                return [int(p) for p in parts[:3]]

            installed_parts = parse_version(installed)
            latest_parts = parse_version(latest)

            # Semver comparison
            for i in range(3):
                if latest_parts[i] > installed_parts[i]:
                    return True
                elif latest_parts[i] < installed_parts[i]:
                    return False

            return False  # Identical versions

        except Exception:
            # In case of parsing error, assume no update available
            return False

    def get_program_list(self) -> List[Dict[str, Any]]:
        """Gets the list of configured programs"""
        return [
            {
                "key": key,
                "name": config["name"],
                "description": config["description"]
            }
            for key, config in self.programs.items()
        ]
