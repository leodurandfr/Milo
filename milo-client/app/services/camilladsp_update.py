"""
CamillaDSP update service for Milo Client.

Handles binary updates from GitHub releases:
- Version detection (installed and latest from GitHub)
- Binary download, backup, install via secure wrapper
- Service lifecycle (stop/start) and rollback on failure
"""
import asyncio
import aiohttp
import aiofiles
import os
import re
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Dict, Any, Optional

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

    @property
    def update_in_progress(self) -> bool:
        """Returns whether an update is currently in progress."""
        return self._update_in_progress

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

    async def get_latest_github_version(self) -> Optional[str]:
        """Gets the latest version from GitHub."""
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        match = re.search(CAMILLADSP_VERSION_REGEX, tag_name)
                        if match:
                            return match.group(1)

                        return tag_name.lstrip('v')

                    return None

        except Exception as e:
            self.logger.error(f"Error getting latest version from GitHub: {e}")
            return None

    async def update_camilladsp(self, target_version: str) -> Dict[str, Any]:
        """Updates CamillaDSP binary from GitHub release.

        Steps: backup → download → stop service → install via wrapper → start → verify.
        Rolls back on any failure after the service is stopped.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        temp_dir = None
        service_stopped = False

        try:
            self._update_in_progress = True
            self.logger.info(f"Starting CamillaDSP update to version {target_version}")

            old_version = await self.get_installed_version()

            # 1. Backup current binary
            backup_result = await self._backup_binary()
            if not backup_result["success"]:
                return backup_result

            # 2. Download new version
            download_result = await self._download_binary(target_version)
            if not download_result["success"]:
                return download_result
            temp_dir = download_result["temp_dir"]

            # 3. Stop service (binary cannot be replaced while in use)
            stop_result = await self._stop_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop CamillaDSP service"}
            service_stopped = True
            await asyncio.sleep(0.5)

            # 4. Install new binary via secure wrapper
            install_result = await self._install_binary(download_result["binary_path"])
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
            self._update_in_progress = False
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

    async def _download_binary(self, version: str) -> Dict[str, Any]:
        """Downloads CamillaDSP binary from GitHub."""
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        try:
            url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/camilladsp-linux-aarch64.tar.gz"

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}

                    archive_path = Path(temp_dir) / "camilladsp.tar.gz"
                    async with aiofiles.open(archive_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            # Extract archive
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()

            proc = await asyncio.create_subprocess_exec(
                "tar", "-xzf", str(archive_path), "-C", str(extract_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            if proc.returncode != 0:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": "Failed to extract archive"}

            binary_path = extract_dir / "camilladsp"
            if not binary_path.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": "Binary not found in archive"}

            return {
                "success": True,
                "binary_path": str(binary_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}

    async def _install_binary(self, binary_path: str) -> Dict[str, Any]:
        """Installs the binary using the secure wrapper script."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", INSTALL_WRAPPER, binary_path,
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
        """Restores the backed-up binary and restarts the service."""
        try:
            backup_file = BACKUP_DIR / "camilladsp.backup"
            if not backup_file.exists():
                self.logger.error("No backup found for rollback")
                return False

            await self._stop_service()
            await asyncio.sleep(0.5)

            # Copy backup to /tmp for the install wrapper
            fd, tmp_backup_str = tempfile.mkstemp(prefix="milo-rollback-", dir="/tmp")
            os.close(fd)
            tmp_backup = Path(tmp_backup_str)
            shutil.copy2(backup_file, tmp_backup)
            try:
                result = await self._install_binary(str(tmp_backup))
                if not result["success"]:
                    self.logger.error(f"Rollback install failed: {result['error']}")
                    return False
            finally:
                tmp_backup.unlink(missing_ok=True)

            await self._start_service()
            self.logger.info("CamillaDSP rollback completed, service restarted")
            return True

        except Exception as e:
            self.logger.error(f"CamillaDSP rollback failed: {e}")
            return False

    async def _stop_service(self) -> bool:
        """Stops the CamillaDSP service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", CAMILLADSP_SERVICE,
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
                "sudo", "systemctl", "start", CAMILLADSP_SERVICE,
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
