"""
CamillaDSP update service for Milo Client.

Handles binary updates from GitHub releases:
- Version detection (installed and latest from GitHub)
- Archive download (checked against the server's digest), backup, install via
  the secure wrapper, which checks it again and unpacks the binary itself
- Service lifecycle (stop/start) and rollback on failure
"""
import asyncio
import os
import re
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from services.release_download import NO_DIGEST, download_release_asset

# Constants
CAMILLADSP_BINARY = "/usr/local/bin/camilladsp"
CAMILLADSP_VERSION_REGEX = r"(\d+\.\d+\.\d+)"
CAMILLADSP_SERVICE = "milo-client-camilladsp.service"
GITHUB_REPO = "HEnquist/camilladsp"
BACKUP_DIR = Path("/var/lib/milo-client/backups/camilladsp")
INSTALL_WRAPPER = "/usr/local/bin/milo-client-install-camilladsp"


class CamillaDSPUpdateService:
    """
    Service for CamillaDSP binary updates.

    Handles:
    - Version detection (installed and latest from GitHub)
    - Binary updates from GitHub releases
    - Backup and rollback on failure
    - Service lifecycle (stop/start)
    """

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.CamillaDSPUpdateService")
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
        """Gets the installed version of CamillaDSP."""
        try:
            proc = await asyncio.create_subprocess_exec(
                CAMILLADSP_BINARY, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            output_text = stdout.decode() + stderr.decode()

            match = re.search(CAMILLADSP_VERSION_REGEX, output_text)
            if match:
                return match.group(1)

            return None

        except (FileNotFoundError, asyncio.TimeoutError, Exception) as e:
            self.logger.error(f"Error getting CamillaDSP version: {e}")
            return None

    async def update_camilladsp(self, target_version: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Updates CamillaDSP binary from GitHub release.

        Steps: backup → download → stop service → install via wrapper → start → verify.
        Rolls back on any failure after the service is stopped. `sha256` is the
        server's manifest digest of the release archive, None for a version the
        manifest does not declare.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        # Up for the whole run, the rollback included, and its outcome recorded
        # before it drops: the server reads the version, and `last_result`, the
        # moment it reads the flag down.
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
        temp_dir = None
        service_stopped = False

        try:
            self.logger.info(f"Starting CamillaDSP update to version {target_version}")

            old_version = await self.get_installed_version()

            # 1. Backup current binary
            backup_result = await self._backup_binary()
            if not backup_result["success"]:
                return backup_result

            # 2. Download new version
            download_result = await self._download_archive(target_version, sha256)
            if not download_result["success"]:
                return download_result
            temp_dir = download_result["temp_dir"]

            # 3. Stop service (binary cannot be replaced while in use)
            stop_result = await self._stop_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop CamillaDSP service"}
            service_stopped = True
            await asyncio.sleep(0.5)

            # 4. Install the new binary via the secure wrapper, from the archive:
            # it checks its own copy against the digest and unpacks that one.
            install_result = await self._run_wrapper(
                "--release", download_result["archive_path"], sha256 or NO_DIGEST
            )
            if not install_result["success"]:
                await self._rollback()
                return install_result

            # 5. Start service
            start_result = await self._start_service()
            if not start_result:
                await self._rollback()
                return {"success": False, "error": "Failed to start CamillaDSP after update"}

            # 6. Verify
            await asyncio.sleep(2)
            new_version = await self.get_installed_version()
            is_running = await self._is_service_running()

            if not is_running:
                await self._rollback()
                return {"success": False, "error": "CamillaDSP service not running after update"}

            self.logger.info(f"CamillaDSP updated from {old_version} to {new_version}")
            return {
                "success": True,
                "message": "CamillaDSP updated successfully",
                "old_version": old_version,
                "new_version": new_version
            }

        except Exception as e:
            self.logger.error(f"CamillaDSP update failed: {e}")
            if service_stopped:
                await self._rollback()
            return {"success": False, "error": str(e)}

        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _backup_binary(self) -> Dict[str, Any]:
        """Backs up the current CamillaDSP binary."""
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(CAMILLADSP_BINARY, BACKUP_DIR / "camilladsp.backup")
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    async def _download_archive(self, version: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Downloads the CamillaDSP release archive, checked against `sha256`,
        and makes sure it carries the binary — before the service is stopped."""
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        try:
            url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/camilladsp-linux-aarch64.tar.gz"
            archive_path = Path(temp_dir) / "camilladsp.tar.gz"
            await download_release_asset(url, archive_path, sha256)

            proc = await asyncio.create_subprocess_exec(
                "tar", "-tzf", str(archive_path), "camilladsp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or "camilladsp" not in stdout.decode().split():
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": "Binary not found in archive"}

            return {
                "success": True,
                "archive_path": str(archive_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}

    async def _run_wrapper(self, *args: str) -> Dict[str, Any]:
        """Runs the secure install wrapper: `--release <archive> <sha256|->` for
        an update, `<binary>` for the rollback's kept copy."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", INSTALL_WRAPPER, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return {"success": True}
            else:
                error_msg = stderr.decode().strip() or stdout.decode().strip()
                return {"success": False, "error": f"Installation failed: {error_msg}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _rollback(self) -> bool:
        """Restores the backed-up binary, then starts the service whatever came of it.

        Every caller has stopped the unit, and `Restart=on-failure` does not undo
        an explicit stop: a rollback that left before the start would keep the
        room silent until a reboot, so the start runs even when the reinstall
        failed — on whichever binary is in place.
        """
        restored = False
        try:
            backup_file = BACKUP_DIR / "camilladsp.backup"
            if not backup_file.exists():
                self.logger.error("No backup found for rollback")
            else:
                await self._stop_service()
                await asyncio.sleep(0.5)

                # Copy backup to /tmp, the only source the install wrapper accepts
                fd, tmp_backup_str = tempfile.mkstemp(prefix="milo-rollback-", dir="/tmp")
                tmp_backup = Path(tmp_backup_str)
                try:
                    os.close(fd)
                    shutil.copy2(backup_file, tmp_backup)
                    result = await self._run_wrapper(str(tmp_backup))
                finally:
                    tmp_backup.unlink(missing_ok=True)
                restored = result["success"]
                if not restored:
                    self.logger.error(f"Rollback install failed: {result['error']}")
        except Exception as e:
            self.logger.error(f"CamillaDSP rollback failed: {e}")
        finally:
            started = await self._start_service()

        if not started:
            self.logger.error("CamillaDSP did not start after the rollback")
            return False
        if restored:
            self.logger.info("CamillaDSP rollback completed, service restarted")
        return restored

    async def _stop_service(self) -> bool:
        """Stops the CamillaDSP service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "stop", CAMILLADSP_SERVICE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Failed to stop CamillaDSP service: {e}")
            return False

    async def _start_service(self) -> bool:
        """Starts the CamillaDSP service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "start", CAMILLADSP_SERVICE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return False

            await asyncio.sleep(2)
            return await self._is_service_running()

        except Exception as e:
            self.logger.error(f"Failed to start CamillaDSP service: {e}")
            return False

    async def _is_service_running(self) -> bool:
        """Checks if the CamillaDSP service is running."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", CAMILLADSP_SERVICE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.logger.error(f"Error checking service status: {e}")
            return False
