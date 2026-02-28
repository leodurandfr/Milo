"""
App update service for Milo Client.

Handles deploying app updates pushed from the main Milo server:
- Extract tarball, sync app/ files, deploy system/rootfs files
- Install Python dependencies
- Write version file and schedule service restart
"""
import asyncio
import os
import shutil
import tarfile
import tempfile
import logging
from pathlib import Path
from typing import Optional

REPO_DIR = Path("/home/milo-client/repo/milo-client")
VENV_PIP = Path("/home/milo-client/venv/bin/pip3")
VERSION_FILE = Path("/var/lib/milo-client/app-version")
DEPLOY_SCRIPT = "/usr/local/bin/milo-client-deploy-update"


class AppUpdateService:
    """Service for deploying app updates pushed from the main server."""

    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.AppUpdateService")
        self._update_in_progress = False

    @property
    def update_in_progress(self) -> bool:
        return self._update_in_progress

    def get_app_version(self) -> Optional[str]:
        """Reads the current app version from the version file."""
        try:
            if VERSION_FILE.exists():
                version = VERSION_FILE.read_text().strip()
                return version if version else None
            return None
        except Exception as e:
            self.logger.error(f"Error reading app version: {e}")
            return None

    async def deploy_update(self, tarball_path: str, version: str) -> dict:
        """Deploys an app update from a tarball.

        Steps:
        1. Extract tarball to temp dir (with security validation)
        2. Sync app/ files (clear old, copy new, skip __pycache__)
        3. Run sudo deploy script for system/rootfs files
        4. Install Python dependencies via pip
        5. Write version file
        6. Schedule service restart with delay
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        temp_dir = None
        try:
            self._update_in_progress = True
            self.logger.info(f"Starting app update deployment (version: {version})")

            # 1. Extract tarball to temp dir
            temp_dir = tempfile.mkdtemp(prefix="milo-client-update-")
            self.logger.info(f"Extracting tarball to {temp_dir}")

            await self._extract_tarball(tarball_path, temp_dir)

            # Validate extracted content
            extracted_root = Path(temp_dir) / "milo-client"
            if not extracted_root.is_dir():
                return {"success": False, "error": "Invalid tarball: missing milo-client/ directory"}

            # 2. Sync app/ files
            extracted_app = extracted_root / "app"
            if extracted_app.is_dir():
                await self._sync_app_files(extracted_app)
            else:
                self.logger.warning("No app/ directory in tarball, skipping app sync")

            # 3. Run deploy script for system/rootfs files
            if (extracted_root / "system").is_dir() or (extracted_root / "rootfs").is_dir():
                await self._deploy_system_files(temp_dir)

            # 4. Install Python dependencies
            requirements_file = REPO_DIR / "app" / "requirements.txt"
            if requirements_file.exists():
                await self._install_dependencies(requirements_file)

            # 5. Write version file
            VERSION_FILE.write_text(version)
            self.logger.info(f"Version file updated: {version}")

            # 6. Schedule restart (2s delay so HTTP response is sent first)
            asyncio.get_event_loop().call_later(2, lambda: asyncio.ensure_future(self._restart_service()))

            return {"success": True, "version": version}

        except Exception as e:
            self.logger.error(f"App update deployment failed: {e}")
            return {"success": False, "error": str(e)}

        finally:
            self._update_in_progress = False
            # Clean up tarball
            try:
                if os.path.exists(tarball_path):
                    os.unlink(tarball_path)
            except Exception:
                pass
            # Clean up temp extraction dir
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def _extract_tarball(self, tarball_path: str, dest_dir: str):
        """Extracts tarball with security validation (no path traversal)."""
        def _do_extract():
            with tarfile.open(tarball_path, "r:gz") as tar:
                # Security: check for path traversal
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        raise ValueError(f"Unsafe path in tarball: {member.name}")
                tar.extractall(path=dest_dir)

        await asyncio.get_event_loop().run_in_executor(None, _do_extract)

    async def _sync_app_files(self, source_app: Path):
        """Syncs app/ files: clear old content and copy new."""
        target_app = REPO_DIR / "app"

        def _do_sync():
            # Remove old app files (keep directory itself)
            if target_app.exists():
                for item in target_app.iterdir():
                    if item.name == "__pycache__":
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # Copy new files
            for item in source_app.iterdir():
                if item.name == "__pycache__":
                    continue
                dest = target_app / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(item, dest)

        await asyncio.get_event_loop().run_in_executor(None, _do_sync)
        self.logger.info("App files synced successfully")

    async def _deploy_system_files(self, temp_dir: str):
        """Runs the sudo deploy script for system and rootfs files."""
        self.logger.info("Deploying system files via sudo wrapper...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", DEPLOY_SCRIPT, temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or stdout.decode().strip()
            raise RuntimeError(f"Deploy script failed: {error_msg}")

        self.logger.info(f"System files deployed: {stdout.decode().strip()}")

    async def _install_dependencies(self, requirements_file: Path):
        """Installs Python dependencies from requirements.txt."""
        self.logger.info("Installing Python dependencies...")

        proc = await asyncio.create_subprocess_exec(
            str(VENV_PIP), "install", "-r", str(requirements_file),
            "--quiet", "--disable-pip-version-check",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            self.logger.warning(f"pip install had issues: {error_msg}")
        else:
            self.logger.info("Python dependencies installed successfully")

    async def _restart_service(self):
        """Restarts milo-client.service."""
        self.logger.info("Restarting milo-client.service...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", "milo-client.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )

        await proc.communicate()
