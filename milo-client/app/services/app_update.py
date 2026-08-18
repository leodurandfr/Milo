"""
App update service for Milo Client.

Handles deploying app updates pushed from the main Milo server:
- Extract tarball, sync app/ files, deploy system/rootfs files
- Install Python dependencies
- Write version file and schedule service restart
"""
import asyncio
import contextlib
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
        app_swapped = False
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
                app_swapped = True
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

            # Everything that can still fail has now succeeded, so the tree kept
            # for the rollback is dead weight — one app/ of disk on a device
            # nobody watches. Past this point the restart is the only step left.
            if app_swapped:
                await self._drop_previous_app()
                app_swapped = False

            # 6. Schedule restart (2s delay so HTTP response is sent first)
            asyncio.get_event_loop().call_later(2, lambda: asyncio.ensure_future(self._restart_service()))

            return {"success": True, "version": version}

        except Exception as e:
            self.logger.error(f"App update deployment failed: {e}")
            if app_swapped:
                await self._restore_previous_app()
            return {"success": False, "error": str(e)}

        finally:
            self._update_in_progress = False
            # Clean up tarball — best-effort, like ignore_errors on the temp dir
            # below: the tarball is disposable and a cleanup failure must not
            # mask the update's own outcome. FileNotFoundError is an OSError, so
            # an already-removed tarball needs no separate existence check.
            with contextlib.suppress(OSError):
                os.unlink(tarball_path)
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
        """Stages the new app/ tree next to the live one, then swaps it in.

        Copying straight over the live tree means a failure mid-copy leaves a
        half-written app on a machine whose only repair path is the API that app
        serves. Staging first makes the copy — the part that can fail — happen
        while the live tree is untouched, and reduces the window to two renames.
        Both staging dirs sit inside REPO_DIR so the renames never cross a
        filesystem and stay atomic.

        The tree that was live stays behind as app.old: two steps of the update
        can still fail after this one, and a satellite that restarts into a tree
        pip never finished installing crashloops with its own repair API down.
        deploy_update owns that copy — _drop_previous_app once the update is
        committed, _restore_previous_app if it is not.
        """
        target_app = REPO_DIR / "app"
        staging = REPO_DIR / "app.new"
        previous = REPO_DIR / "app.old"

        def _do_sync():
            # Anything left by a killed run is stale, never a fallback.
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(previous, ignore_errors=True)

            shutil.copytree(
                source_app, staging,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )

            if target_app.exists():
                os.rename(target_app, previous)
            os.rename(staging, target_app)

            # The unit's WorkingDirectory is this very path, so the running
            # process is now holding the renamed-away inode as its cwd. pip runs
            # two steps later and calls getcwd(); re-anchor now.
            os.chdir(target_app)

        await asyncio.get_event_loop().run_in_executor(None, _do_sync)
        self.logger.info("App files synced successfully")

    async def _drop_previous_app(self):
        """Deletes the tree kept by _sync_app_files, once it can no longer be needed."""
        previous = REPO_DIR / "app.old"
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: shutil.rmtree(previous, ignore_errors=True)
        )

    async def _restore_previous_app(self):
        """Puts the tree kept by _sync_app_files back as the live app/.

        Three renames inside REPO_DIR, so nothing crosses a filesystem and no
        step copies: the new tree steps aside into the staging name (which the
        next sync clears anyway), the previous one takes its place, and the cwd
        is re-anchored exactly as the swap did.
        """
        target_app = REPO_DIR / "app"
        staging = REPO_DIR / "app.new"
        previous = REPO_DIR / "app.old"

        def _do_restore():
            if not previous.is_dir():
                return False
            shutil.rmtree(staging, ignore_errors=True)
            if target_app.exists():
                os.rename(target_app, staging)
            os.rename(previous, target_app)
            os.chdir(target_app)
            shutil.rmtree(staging, ignore_errors=True)
            return True

        try:
            restored = await asyncio.get_event_loop().run_in_executor(None, _do_restore)
        except OSError as e:
            self.logger.error(f"Rollback failed, app/ is left as the update wrote it: {e}")
            return

        if restored:
            self.logger.warning("Update failed after the swap: previous app/ tree restored")
        else:
            self.logger.error("Update failed after the swap and no previous tree was kept")

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
            error_msg = stderr.decode().strip() or stdout.decode().strip()
            raise RuntimeError(f"pip install failed: {error_msg}")

        self.logger.info("Python dependencies installed successfully")

    async def _restart_service(self):
        """Restarts milo-client.service.

        A successful restart kills this process before systemctl returns, so a
        return code that arrives at all is a restart that did not happen — a
        unit file the deploy step made invalid, a masked unit, a policy that no
        longer grants the verb. There is nobody left to tell: the HTTP response
        went out two seconds ago. Logging it is what a `sat logs` can find, and
        the started_at the server compares stays put, so the update is reported
        failed rather than succeeded on a satellite still running the old code.
        """
        self.logger.info("Restarting milo-client.service...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", "milo-client.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            self.logger.error(
                f"Restart failed, still running the previous code: {stderr.decode().strip()}"
            )
