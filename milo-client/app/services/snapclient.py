"""
Snapclient service for managing snapclient binary updates and version detection.
"""
import asyncio
import aiohttp
import aiofiles
import re
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Constants
SNAPCLIENT_VERSION_REGEX = r"v(\d+\.\d+\.\d+)"
GITHUB_REPO = "badaix/snapcast"
SNAPCLIENT_SERVICE = "milo-client-snapclient.service"


class SnapclientService:
    """
    Service for snapclient operations.

    Handles:
    - Version detection (installed and latest from GitHub)
    - Binary updates from GitHub releases
    - Service lifecycle (start/stop)
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.SnapclientService")
        self._update_in_progress = False

    @property
    def update_in_progress(self) -> bool:
        """Returns whether an update is currently in progress."""
        return self._update_in_progress

    async def get_installed_version(self) -> Optional[str]:
        """Gets the installed version of snapclient."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "snapclient", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            output_text = stdout.decode() + stderr.decode()

            match = re.search(SNAPCLIENT_VERSION_REGEX, output_text)
            if match:
                return match.group(1)

            return None

        except (FileNotFoundError, asyncio.TimeoutError, Exception) as e:
            self.logger.error(f"Error getting snapclient version: {e}")
            return None

    async def get_latest_github_version(self) -> Optional[str]:
        """Gets the latest version from GitHub."""
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        match = re.search(SNAPCLIENT_VERSION_REGEX, tag_name)
                        if match:
                            return match.group(1)

                        # Fallback: return tag_name without the 'v'
                        return tag_name.lstrip('v')

                    return None

        except Exception as e:
            self.logger.error(f"Error getting latest version from GitHub: {e}")
            return None

    async def _get_debian_codename(self) -> str:
        """Detects the system's Debian version (bookworm, trixie, etc.)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", "source /etc/os-release && echo $VERSION_CODENAME",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await proc.communicate()
            codename = stdout.decode().strip()

            if codename:
                self.logger.info(f"Detected Debian codename: {codename}")
                return codename
            else:
                self.logger.warning("Could not detect Debian codename, using 'bookworm' as fallback")
                return "bookworm"

        except Exception as e:
            self.logger.error(f"Error detecting Debian codename: {e}, using 'bookworm' as fallback")
            return "bookworm"

    async def is_service_running(self) -> bool:
        """Checks if the snapclient service is running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", SNAPCLIENT_SERVICE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"

        except Exception as e:
            self.logger.error(f"Error checking service status: {e}")
            return False

    async def update_snapclient(self, target_version: str) -> Dict[str, Any]:
        """Updates snapclient from GitHub with APT dependency resolution."""
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        service_stopped = False

        try:
            self._update_in_progress = True
            self.logger.info(f"Starting snapclient update to version {target_version}")

            # Get current version before update
            old_version = await self.get_installed_version()

            # 1. Download the .deb from GitHub
            download_result = await self._download_snapclient_deb(target_version)
            if not download_result["success"]:
                return download_result

            # 2. Stop the service
            stop_result = await self._stop_snapclient_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop snapclient service"}
            service_stopped = True

            # 3. Install the .deb with APT (which resolves dependencies automatically)
            install_result = await self._install_deb_with_apt(download_result["deb_path"])
            if not install_result["success"]:
                return install_result

            # 4. Restart the service
            start_result = await self._start_snapclient_service()
            if not start_result:
                return {"success": False, "error": "Failed to start snapclient service"}
            service_stopped = False

            # 5. Verify the update
            await asyncio.sleep(3)  # Wait for the service to stabilize
            new_version = await self.get_installed_version()

            if new_version == target_version:
                self.logger.info(f"Snapclient successfully updated from {old_version} to {new_version}")
                return {
                    "success": True,
                    "message": "Snapclient updated successfully",
                    "old_version": old_version,
                    "new_version": new_version
                }
            else:
                return {
                    "success": False,
                    "error": f"Version mismatch after update: expected {target_version}, got {new_version}"
                }

        except Exception as e:
            self.logger.error(f"Update failed: {e}")
            return {"success": False, "error": str(e)}

        finally:
            self._update_in_progress = False
            if service_stopped:
                await self._restore_stopped_service()
            # Clean up temporary files
            if 'download_result' in locals() and download_result.get("temp_dir"):
                shutil.rmtree(download_result["temp_dir"], ignore_errors=True)

    async def _restore_stopped_service(self):
        """Starts snapclient back after an update that failed with it stopped.

        `Restart=on-failure` does not undo an explicit `systemctl stop`, so
        without this the room stays silent until someone walks up to the
        speaker — and nothing in the UI says why. Same shape as
        CamillaDSPUpdateService._rollback, minus the binary: apt either
        installed the package or left the old one, there is nothing to restore
        but the unit itself. `start` is what the policy grants for this unit,
        `restart` is not.
        """
        self.logger.warning("Snapclient update failed with the service stopped, starting it back")
        if not await self._start_snapclient_service():
            self.logger.error("Could not start snapclient back after the failed update")

    async def _download_snapclient_deb(self, version: str) -> Dict[str, Any]:
        """Downloads the snapclient .deb package from GitHub with auto Debian detection."""
        try:
            # Detect Debian version
            debian_codename = await self._get_debian_codename()

            temp_dir = tempfile.mkdtemp()
            package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
            url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{package_name}"

            deb_path = Path(temp_dir) / package_name

            self.logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "error": f"Download failed: HTTP {response.status}"
                        }

                    async with aiofiles.open(deb_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            return {
                "success": True,
                "deb_path": str(deb_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _install_deb_with_apt(self, deb_path: str) -> Dict[str, Any]:
        """Installs a .deb package using the secure wrapper script."""
        try:
            self.logger.info(f"Installing {Path(deb_path).name} via secure wrapper...")

            proc = await asyncio.create_subprocess_exec(
                "sudo", "/usr/local/bin/milo-client-install-snapclient", deb_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                self.logger.info("Package installed successfully")
                return {"success": True}
            else:
                error_msg = stderr.decode().strip() or stdout.decode().strip()
                return {
                    "success": False,
                    "error": f"Installation failed: {error_msg}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _stop_snapclient_service(self) -> bool:
        """Stops the snapclient service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", SNAPCLIENT_SERVICE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await proc.communicate()
            return proc.returncode == 0

        except Exception as e:
            self.logger.error(f"Failed to stop snapclient service: {e}")
            return False

    async def _start_snapclient_service(self) -> bool:
        """Starts the snapclient service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", SNAPCLIENT_SERVICE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return False

            # Wait for the service to be actually started
            await asyncio.sleep(2)

            # Check the status
            is_running = await self.is_service_running()
            return is_running

        except Exception as e:
            self.logger.error(f"Failed to start snapclient service: {e}")
            return False
