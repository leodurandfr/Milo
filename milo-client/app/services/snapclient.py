"""
Snapclient service for managing snapclient binary updates and version detection.
"""
import asyncio
import re
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import dpkg_lock
from services.release_download import NO_DIGEST, download_release_asset

# Constants
SNAPCLIENT_VERSION_REGEX = r"v(\d+\.\d+\.\d+)"
GITHUB_REPO = "badaix/snapcast"
SNAPCLIENT_SERVICE = "milo-client-snapclient.service"
# How long an update waits for apt before it stops the room's snapclient.
# unattended-upgrades runs on every satellite and holds dpkg's lock for its
# whole run; the install wrapper's `dpkg --configure -a` and `apt-get update`
# never wait for a lock, so an install that met one failed after the room had
# gone silent. Counted in the server's wait on this install
# (PROGRAM_UPDATE_CEILING_S): held by
# backend/tests/architecture/test_satellite_program_ceiling.py.
APT_LOCK_WAIT_S = 60


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
        self._last_result: Optional[Dict[str, Any]] = None

    @property
    def update_in_progress(self) -> bool:
        """Returns whether an update is currently in progress."""
        return self._update_in_progress

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        """How the last update this process ran ended: `{target_version,
        success, error}`, or None before the first. The server reads `error`
        once `update_in_progress` drops, as the reason its row shows."""
        return self._last_result

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

    async def _get_debian_codename(self) -> str:
        """The Debian release this unit runs (bookworm, trixie, ...), read from /etc/os-release.

        Raises when it cannot be read: snapcast publishes one .deb per release,
        and another release's package is not the pinned dependency for this one.
        `_download_snapclient_deb` reports the exception as the update's failure.
        """
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", "source /etc/os-release && echo $VERSION_CODENAME",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        codename = stdout.decode().strip()
        if proc.returncode != 0 or not codename:
            raise RuntimeError(
                f"Debian codename unreadable from /etc/os-release: {stderr.decode().strip() or 'empty'}"
            )
        self.logger.info(f"Detected Debian codename: {codename}")
        return codename

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

    async def update_snapclient(self, target_version: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Updates snapclient from GitHub with APT dependency resolution.

        `sha256` is the server's manifest digest of the .deb: checked as it
        downloads, and again by the root installer on its own copy before dpkg
        runs. None when the server names a version its manifest does not declare.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        # Up for the whole run, the restart of a stopped unit included, and its
        # outcome recorded before it drops: the server reads the version, and
        # `last_result`, the moment it reads the flag down.
        self._update_in_progress = True
        result: Dict[str, Any] = {"success": False, "error": "The update ended without a result"}
        try:
            result = await self._update(target_version, sha256)
            return result
        finally:
            self._last_result = {
                "target_version": target_version,
                "success": bool(result.get("success")),
                "error": result.get("error"),
            }
            self._update_in_progress = False

    async def _update(self, target_version: str, sha256: Optional[str]) -> Dict[str, Any]:
        service_stopped = False

        try:
            self.logger.info(f"Starting snapclient update to version {target_version}")

            # Get current version before update
            old_version = await self.get_installed_version()

            # 1. Download the .deb from GitHub
            download_result = await self._download_snapclient_deb(target_version, sha256)
            if not download_result["success"]:
                return download_result

            # 2. Wait for apt, and refuse with the room still playing when it
            # stays held: dpkg's locks and the lists' one, which the wrapper's
            # `apt-get update` takes.
            held_by = await dpkg_lock.wait_until_free(
                APT_LOCK_WAIT_S, dpkg_lock.DPKG_LOCKS + (dpkg_lock.APT_LISTS_LOCK,)
            )
            if held_by:
                error = f"apt is held by {held_by} after {APT_LOCK_WAIT_S} s: nothing was stopped"
                self.logger.warning(f"Snapclient update refused: {error}")
                return {"success": False, "error": error}

            # 3. Stop the service
            stop_result = await self._stop_snapclient_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop snapclient service"}
            service_stopped = True

            # 4. Install the .deb with APT (which resolves dependencies automatically)
            install_result = await self._install_deb_with_apt(download_result["deb_path"], sha256)
            if not install_result["success"]:
                return install_result

            # 5. Restart the service
            start_result = await self._start_snapclient_service()
            if not start_result:
                return {"success": False, "error": "Failed to start snapclient service"}
            service_stopped = False

            # 6. Verify the update
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

    async def _download_snapclient_deb(self, version: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Downloads the snapclient .deb built for this unit's Debian release
        from GitHub, checked against `sha256`."""
        temp_dir = None
        try:
            # Detect Debian version
            debian_codename = await self._get_debian_codename()

            temp_dir = tempfile.mkdtemp()
            package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
            url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{package_name}"

            deb_path = Path(temp_dir) / package_name

            self.logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")
            await download_release_asset(url, deb_path, sha256)

            return {
                "success": True,
                "deb_path": str(deb_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}

    async def _install_deb_with_apt(self, deb_path: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Installs a .deb package using the secure wrapper script, which checks
        its own copy against `sha256` before dpkg sees it."""
        try:
            self.logger.info(f"Installing {Path(deb_path).name} via secure wrapper...")

            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/usr/local/bin/milo-client-install-snapclient", deb_path, sha256 or NO_DIGEST,
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
                "sudo", "-n", "systemctl", "stop", SNAPCLIENT_SERVICE,
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
                "sudo", "-n", "systemctl", "start", SNAPCLIENT_SERVICE,
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
