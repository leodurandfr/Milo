# backend/core/updates/satellite.py
"""
Satellite update service - Version with GitHub token support
"""
import asyncio
import aiohttp
import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.config.constants import CLIENT_API_PORT
from backend.core.updates.helpers import compare_versions, extract_base_tag

MILO_REPO_DIR = Path("/home/milo/milo")
MILO_CLIENT_DIR = MILO_REPO_DIR / "milo-client"

# Patterns to exclude from the client tarball
TARBALL_EXCLUDE_PATTERNS = {"__pycache__", ".pyc", ".pytest_cache", "tests", ".git"}


class SatelliteUpdateService:
    """Service to manage satellites and their updates"""

    def __init__(self, snapcast_service, client_registry_service=None):
        self.snapcast_service = snapcast_service
        self.client_registry_service = client_registry_service
        self.logger = logging.getLogger(__name__)
        self.satellite_api_port = CLIENT_API_PORT

        # GitHub token (optional)
        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.debug("GitHub token detected for satellite updates")

        # Cache for detected satellites
        self._satellites_cache = {}
        self._cache_timeout = 30  # 30 seconds
        self._last_cache_time = 0

    def _get_github_headers(self) -> Dict[str, str]:
        """Returns headers for GitHub requests (with token if available)"""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Milo-Audio-System"
        }

        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        return headers

    async def discover_satellites(self) -> List[Dict[str, Any]]:
        """Discovers active satellites on the network"""
        try:
            # Get Snapcast clients
            clients = await self.snapcast_service.get_clients()

            satellites = []

            for client in clients:
                # Filter only clients with hostname milo-client-*
                hostname = client.get("host", "")
                if not hostname.startswith("milo-client-"):
                    continue

                ip = client.get("ip", "")
                if not ip:
                    continue

                # Check if satellite API responds
                satellite_info = await self._check_satellite_api(hostname, ip)

                if satellite_info["online"]:
                    # Display name: registry > hostname
                    display_name = hostname
                    if self.client_registry_service:
                        mac_id = client.get("mac_id")
                        if mac_id:
                            registry_client = self.client_registry_service.get_client(mac_id)
                            if registry_client and registry_client.name:
                                display_name = registry_client.name

                    satellites.append({
                        "hostname": hostname,
                        "display_name": display_name,
                        "ip": ip,
                        "snapclient_version": satellite_info.get("version"),
                        "app_version": satellite_info.get("app_version"),
                        "online": True,
                        "uptime": satellite_info.get("uptime"),
                        "snapclient_running": satellite_info.get("running", False)
                    })

            self.logger.info(f"Discovered {len(satellites)} satellites")
            return satellites

        except Exception as e:
            self.logger.error(f"Error discovering satellites: {e}")
            return []

    async def _check_satellite_api(self, hostname: str, ip: str) -> Dict[str, Any]:
        """Checks if a satellite API responds and retrieves its info"""
        try:
            url = f"http://{ip}:{self.satellite_api_port}/status"

            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()

                        return {
                            "online": True,
                            "version": data.get("snapclient", {}).get("version"),
                            "running": data.get("snapclient", {}).get("running", False),
                            "uptime": data.get("uptime"),
                            "app_version": data.get("app", {}).get("version")
                        }

            return {"online": False}

        except Exception as e:
            self.logger.debug(f"Satellite {hostname} ({ip}) not reachable: {e}")
            return {"online": False}

    async def get_satellite_status(self, hostname: str) -> Dict[str, Any]:
        """Gets complete status of a specific satellite"""
        try:
            satellites = await self.discover_satellites()

            for satellite in satellites:
                if satellite["hostname"] == hostname:
                    # Enrichir avec version disponible
                    latest_version = await self._get_latest_snapclient_version()
                    satellite["latest_version"] = latest_version
                    satellite["update_available"] = compare_versions(
                        satellite.get("snapclient_version"),
                        latest_version
                    )

                    return {
                        "status": "success",
                        "satellite": satellite
                    }

            return {
                "status": "error",
                "message": f"Satellite {hostname} not found or offline"
            }

        except Exception as e:
            self.logger.error(f"Error getting satellite status: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def update_satellite(
        self,
        hostname: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Launches a satellite update"""
        try:
            # Get satellite IP
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["hostname"] == hostname), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {hostname} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/update"

            if progress_callback:
                await progress_callback("updates.progress.startingUpdate", 0)

            # Launch update via satellite API
            timeout = aiohttp.ClientTimeout(total=300)  # 5 minutes timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get("success"):
                            if progress_callback:
                                await progress_callback(
                                    "updates.progress.updateInitiated",
                                    10
                                )

                            # Wait for update to complete
                            update_result = await self._wait_for_update_completion(
                                hostname,
                                ip,
                                progress_callback
                            )

                            return update_result
                        else:
                            return {
                                "success": False,
                                "error": data.get("message", "Update failed")
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}"
                        }

        except Exception as e:
            self.logger.error(f"Error updating satellite {hostname}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_update_completion(
        self,
        hostname: str,
        ip: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Waits for update completion on the satellite"""
        max_wait_time = 180  # 3 minutes max
        check_interval = 5   # Check every 5 seconds
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            progress = min(10 + (elapsed / max_wait_time * 80), 90)

            if progress_callback:
                await progress_callback(
                    "updates.progress.updateInProgress",
                    int(progress)
                )

            # Check update status
            try:
                url = f"http://{ip}:{self.satellite_api_port}/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
                                # Update complete, check new version
                                status_url = f"http://{ip}:{self.satellite_api_port}/status"

                                async with session.get(status_url) as status_response:
                                    if status_response.status == 200:
                                        status_data = await status_response.json()
                                        new_version = status_data.get("snapclient", {}).get("version")

                                        if progress_callback:
                                            await progress_callback(
                                                "updates.progress.completed",
                                                100
                                            )

                                        return {
                                            "success": True,
                                            "message": f"Satellite {hostname} updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for update on {hostname}: {e}")
                continue

        # Timeout
        return {
            "success": False,
            "error": f"Update timeout for {hostname}"
        }

    async def _get_latest_snapclient_version(self) -> Optional[str]:
        """Gets latest snapclient version from GitHub with token"""
        try:
            url = "https://api.github.com/repos/badaix/snapcast/releases/latest"
            headers = self._get_github_headers()

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        # Extract version number (v0.31.0 -> 0.31.0)
                        return tag_name.lstrip('v')
                    elif response.status == 403:
                        self.logger.warning("GitHub API rate limit - snapclient version unavailable")

            return None

        except Exception as e:
            self.logger.error(f"Error getting latest snapclient version: {e}")
            return None

    async def _get_server_version(self) -> Optional[str]:
        """Gets the current server version via git describe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(MILO_REPO_DIR), "describe", "--tags", "--always",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode().strip()
            return None
        except Exception as e:
            self.logger.error(f"Error getting server version: {e}")
            return None

    async def _create_client_tarball(self) -> tuple:
        """Creates a tarball of the milo-client/ directory.

        Returns (tarball_path, version) tuple.
        """
        version = await self._get_server_version()
        if not version:
            raise RuntimeError("Could not determine server version")

        if not MILO_CLIENT_DIR.is_dir():
            raise RuntimeError(f"milo-client directory not found: {MILO_CLIENT_DIR}")

        def _should_exclude(tarinfo):
            name = tarinfo.name
            for pattern in TARBALL_EXCLUDE_PATTERNS:
                if pattern.startswith("."):
                    # Extension match (e.g. .pyc)
                    if name.endswith(pattern):
                        return True
                else:
                    # Directory/file name match
                    parts = Path(name).parts
                    if pattern in parts:
                        return True
            return False

        def _filter(tarinfo):
            if _should_exclude(tarinfo):
                return None
            return tarinfo

        def _create():
            fd, tarball_path = tempfile.mkstemp(suffix=".tar.gz", prefix="milo-client-")
            os.close(fd)
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(str(MILO_CLIENT_DIR), arcname="milo-client", filter=_filter)
            return tarball_path

        tarball_path = await asyncio.get_event_loop().run_in_executor(None, _create)
        self.logger.info(f"Created client tarball: {tarball_path} (version: {version})")
        return tarball_path, version

    async def update_satellite_app(
        self,
        hostname: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Pushes a milo-client app update to a satellite."""
        tarball_path = None
        try:
            # Discover satellite
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["hostname"] == hostname), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {hostname} not found or offline"
                }

            ip = satellite["ip"]

            if progress_callback:
                await progress_callback("updates.progress.startingUpdate", 5)

            # Create tarball
            tarball_path, version = await self._create_client_tarball()

            if progress_callback:
                await progress_callback("updates.progress.sendingUpdate", 20)

            # POST tarball to satellite
            url = f"http://{ip}:{self.satellite_api_port}/app/update"
            timeout = aiohttp.ClientTimeout(total=120)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                with open(tarball_path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("tarball", f, filename="milo-client.tar.gz",
                                   content_type="application/gzip")
                    form.add_field("version", version)

                    async with session.post(url, data=form) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            return {
                                "success": False,
                                "error": f"Satellite rejected update: HTTP {response.status} - {error_text}"
                            }

            if progress_callback:
                await progress_callback("updates.progress.waitingForRestart", 50)

            # Poll /status until satellite is back online with matching version
            result = await self._wait_for_app_update_completion(
                hostname, ip, version, progress_callback
            )
            return result

        except Exception as e:
            self.logger.error(f"Error updating satellite app {hostname}: {e}")
            return {"success": False, "error": str(e)}

        finally:
            # Clean up tarball
            if tarball_path:
                try:
                    os.unlink(tarball_path)
                except Exception:
                    pass

    async def _wait_for_app_update_completion(
        self,
        hostname: str,
        ip: str,
        expected_version: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Polls satellite /status until it's back online with the expected app version."""
        max_wait_time = 90
        check_interval = 5
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            progress = min(50 + (elapsed / max_wait_time * 45), 95)

            if progress_callback:
                await progress_callback(
                    "updates.progress.waitingForRestart",
                    int(progress)
                )

            try:
                url = f"http://{ip}:{self.satellite_api_port}/status"
                timeout = aiohttp.ClientTimeout(total=5)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            app_version = data.get("app", {}).get("version")

                            if app_version == expected_version:
                                if progress_callback:
                                    await progress_callback(
                                        "updates.progress.completed",
                                        100
                                    )

                                return {
                                    "success": True,
                                    "message": f"Satellite {hostname} app updated successfully",
                                    "new_version": app_version
                                }

            except Exception as e:
                # Connection errors expected during restart
                self.logger.debug(f"Waiting for satellite {hostname} restart: {e}")
                continue

        return {
            "success": False,
            "error": f"App update timeout for {hostname}"
        }

