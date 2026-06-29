# backend/core/updates/satellite.py
"""
Satellite update service — discovers satellites via client registry and manages updates.
"""
import asyncio
import contextlib
import aiohttp
import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.config.constants import CLIENT_API_PORT
from backend.shared.decorators import handle_errors

MILO_REPO_DIR = Path("/home/milo/milo")
MILO_CLIENT_DIR = MILO_REPO_DIR / "milo-client"

# Patterns to exclude from the client tarball
TARBALL_EXCLUDE_PATTERNS = {"__pycache__", ".pyc", ".pytest_cache", "tests", ".git"}


class SatelliteUpdateService:
    """Service to manage satellites and their updates.

    Discovers satellites via ClientRegistryService (non-local clients)
    and identifies them by mac_id.
    """

    def __init__(self, snapcast_service, client_registry_service):
        self.snapcast_service = snapcast_service
        self.client_registry_service = client_registry_service
        self.logger = logging.getLogger(__name__)
        self.satellite_api_port = CLIENT_API_PORT

    @handle_errors(default=[])
    async def discover_satellites(self) -> List[Dict[str, Any]]:
        """Discovers active satellites on the network via the client registry.

        Only probes online, non-local clients to avoid timeout delays on
        unreachable devices. Probes run in parallel for speed.
        """
        all_clients = self.client_registry_service.get_all_clients()

        # Only probe clients that Snapcast reports as online
        candidates = [
            (mac_id, client) for mac_id, client in all_clients.items()
            if not client.is_local and client.ip and client.online
        ]

        if not candidates:
            return []

        tasks = [self._check_satellite_api(client.ip) for _mac_id, client in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        satellites = []
        for (mac_id, client), result in zip(candidates, results):
            if isinstance(result, Exception) or not result.get("online"):
                continue
            satellites.append({
                "mac_id": mac_id,
                "hostname": client.host,
                "display_name": client.name or mac_id,
                "ip": client.ip,
                "snapclient_version": result.get("version"),
                "app_version": result.get("app_version"),
                "camilladsp_version": result.get("camilladsp_version"),
                "online": True,
                "uptime": result.get("uptime"),
                "snapclient_running": result.get("running", False)
            })

        self.logger.info(f"Discovered {len(satellites)} satellites")
        return satellites

    @handle_errors(default={"online": False}, level='debug')
    async def _check_satellite_api(self, ip: str) -> Dict[str, Any]:
        """Checks if a satellite API responds and retrieves its info."""
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
                        "app_version": data.get("app", {}).get("version"),
                        "camilladsp_version": data.get("camilladsp", {}).get("version")
                    }

        return {"online": False}

    async def update_satellite(
        self,
        mac_id: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Launches a satellite snapclient update."""
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/update"

            if progress_callback:
                await progress_callback("updates.progress.startingUpdate", 0)

            timeout = aiohttp.ClientTimeout(total=300)
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

                            update_result = await self._wait_for_update_completion(
                                mac_id,
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
            self.logger.error(f"Error updating satellite {mac_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_update_completion(
        self,
        mac_id: str,
        ip: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Waits for update completion on the satellite."""
        max_wait_time = 180
        check_interval = 5
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

            try:
                url = f"http://{ip}:{self.satellite_api_port}/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
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
                                            "message": f"Satellite {mac_id} updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for update on {mac_id}: {e}")
                continue

        return {
            "success": False,
            "error": f"Update timeout for {mac_id}"
        }

    async def _get_server_version(self) -> Optional[str]:
        """Gets the current server version via git describe."""
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(MILO_REPO_DIR), "describe", "--tags", "--always",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
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

        tarball_path = await asyncio.get_running_loop().run_in_executor(None, _create)
        self.logger.info(f"Created client tarball: {tarball_path} (version: {version})")
        return tarball_path, version

    async def update_satellite_app(
        self,
        mac_id: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Pushes a milo-client app update to a satellite."""
        tarball_path = None
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]

            if progress_callback:
                await progress_callback("updates.progress.startingUpdate", 5)

            tarball_path, version = await self._create_client_tarball()

            if progress_callback:
                await progress_callback("updates.progress.sendingUpdate", 20)

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

            result = await self._wait_for_app_update_completion(
                mac_id, ip, version, progress_callback
            )
            return result

        except Exception as e:
            self.logger.error(f"Error updating satellite app {mac_id}: {e}")
            return {"success": False, "error": str(e)}

        finally:
            if tarball_path:
                with contextlib.suppress(OSError):
                    os.unlink(tarball_path)

    async def update_satellite_camilladsp(
        self,
        mac_id: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Triggers a CamillaDSP binary update on a satellite."""
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/camilladsp/update"

            if progress_callback:
                await progress_callback("updates.progress.startingUpdate", 0)

            timeout = aiohttp.ClientTimeout(total=300)
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

                            return await self._wait_for_camilladsp_update_completion(
                                mac_id, ip, progress_callback
                            )
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
            self.logger.error(f"Error updating CamillaDSP on satellite {mac_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_camilladsp_update_completion(
        self,
        mac_id: str,
        ip: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Polls satellite until CamillaDSP update completes."""
        max_wait_time = 180
        check_interval = 5
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

            try:
                url = f"http://{ip}:{self.satellite_api_port}/camilladsp/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
                                # Fetch updated version from /status
                                status_url = f"http://{ip}:{self.satellite_api_port}/status"

                                async with session.get(status_url) as status_response:
                                    if status_response.status == 200:
                                        status_data = await status_response.json()
                                        new_version = status_data.get("camilladsp", {}).get("version")

                                        if progress_callback:
                                            await progress_callback(
                                                "updates.progress.completed",
                                                100
                                            )

                                        return {
                                            "success": True,
                                            "message": f"Satellite {mac_id} CamillaDSP updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for CamillaDSP update on {mac_id}: {e}")
                continue

        return {
            "success": False,
            "error": f"CamillaDSP update timeout for {mac_id}"
        }

    async def _wait_for_app_update_completion(
        self,
        mac_id: str,
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
                                    "message": f"Satellite {mac_id} app updated successfully",
                                    "new_version": app_version
                                }

            except Exception as e:
                self.logger.debug(f"Waiting for satellite {mac_id} restart: {e}")
                continue

        return {
            "success": False,
            "error": f"App update timeout for {mac_id}"
        }

