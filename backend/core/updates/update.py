# backend/core/updates/update.py
"""
Update service - Phase 2A (go-librespot + snapcast)
"""
import asyncio
import aiohttp
import aiofiles
import shutil
import tempfile
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Awaitable
from backend.core.updates.version import VersionService
from backend.config.constants import DEPLOY_UPDATE_CMD


class UpdateService(VersionService):
    """Update service - Extends VersionService"""

    def __init__(self):
        super().__init__()
        self.update_logger = logging.getLogger(f"{__name__}.update")

        # Update-specific configuration
        self.update_config = {
            "milo": {
                "git_path": "/home/milo/milo",
                "git_branch": "main",
                "service_name": "milo-backend.service",
                "backup_path": "/var/lib/milo/backups/milo-app"
            },
            "go-librespot": {
                "binary_path": "/usr/local/bin/go-librespot",
                "config_path": "/var/lib/milo/go-librespot/config.yml",
                "service_name": "milo-spotify.service",
                "github_asset_pattern": "go-librespot_linux_arm64.tar.gz",
                "backup_path": "/var/lib/milo/backups/go-librespot"
            },
            "shairport-sync": {
                "binary_path": "/usr/local/bin/shairport-sync",
                "service_name": "milo-airplay.service",
                "backup_path": "/var/lib/milo/backups/shairport-sync",
                "configure_flags": [
                    "--sysconfdir=/etc", "--with-alsa", "--with-avahi",
                    "--with-ssl=openssl", "--with-soxr", "--with-metadata",
                    "--with-airplay-2", "--with-dbus-interface"
                ]
            },
            "multiroom": {
                "services": [
                    "milo-snapserver-multiroom.service",
                    "milo-snapclient-multiroom.service"
                ],
                "components": ["snapserver", "snapclient"],
                "backup_path": "/var/lib/milo/backups/multiroom"
            }
        }

    async def update_program(self, program_key: str, progress_callback: Optional[Callable[[str, int], Awaitable[None]]] = None) -> Dict[str, Any]:
        """Updates a specific program with progress callback"""
        if program_key not in self.update_config:
            return {"success": False, "error": f"Update not supported for {program_key}"}

        try:
            # Check that an update is available
            status = await self._get_program_full_status(program_key)
            if not status.get("update_available"):
                return {"success": False, "error": "No update available"}

            if progress_callback:
                await progress_callback("Initializing update...", 0)

            # Dispatch to specific method
            if program_key == "milo":
                return await self._update_milo_app(status, progress_callback)
            elif program_key == "go-librespot":
                return await self._update_go_librespot(status, progress_callback)
            elif program_key == "multiroom":
                return await self._update_multiroom(status, progress_callback)
            elif program_key == "shairport-sync":
                return await self._update_shairport_sync(status, progress_callback)
            else:
                return {"success": False, "error": f"Update handler not implemented for {program_key}"}

        except Exception as e:
            self.update_logger.error(f"Update failed for {program_key}: {e}")
            return {"success": False, "error": str(e)}

    async def _get_current_commit(self, git_path: str) -> str:
        """Get current HEAD commit hash"""
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", git_path, "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    async def _rollback_milo_to_commit(self, commit_hash: str, progress_callback: Optional[Callable] = None) -> bool:
        """Rollback Milo to a specific commit and rebuild"""
        config = self.update_config["milo"]
        try:
            self.update_logger.info(f"Rolling back Milo to commit {commit_hash[:8]}...")

            # Hard reset to the original commit
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "reset", "--hard", commit_hash,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.update_logger.error(f"Git reset failed: {stderr.decode()}")
                return False

            if progress_callback:
                await progress_callback("updates.progress.rollbackRebuilding", 92)

            # Rebuild frontend after rollback
            frontend_dir = Path(config["git_path"]) / "frontend"
            if frontend_dir.exists():
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

                proc = await asyncio.create_subprocess_exec(
                    "npm", "run", "build",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

            # Reinstall Python dependencies in venv
            requirements_file = Path(config["git_path"]) / "backend" / "requirements.txt"
            venv_pip = str(Path(config["git_path"]) / "venv" / "bin" / "pip3")
            if requirements_file.exists():
                proc = await asyncio.create_subprocess_exec(
                    venv_pip, "install", "-r", str(requirements_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

            # Sync system files from rolled-back version
            await self._sync_system_files()

            if progress_callback:
                await progress_callback("updates.progress.rollbackRestarting", 96)

            # Restart services
            await self._restart_service(config["service_name"])
            await self._restart_service("milo-kiosk.service")

            self.update_logger.info("Milo rollback completed successfully")
            return True

        except Exception as e:
            self.update_logger.error(f"Milo rollback failed: {e}")
            return False

    async def _update_milo_app(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Updates Milo application via git pull with automatic rollback on failure"""
        config = self.update_config["milo"]
        latest_version = status["latest"]["version"]
        original_commit = None

        try:
            if progress_callback:
                await progress_callback("updates.progress.checkingRepository", 5)

            # 1. Check that the directory is a git repository
            git_dir = Path(config["git_path"]) / ".git"
            if not git_dir.exists():
                return {"success": False, "error": "Not a git repository"}

            # 2. Save current commit for potential rollback
            original_commit = await self._get_current_commit(config["git_path"])
            self.update_logger.info(f"Current commit before update: {original_commit[:8]}")

            if progress_callback:
                await progress_callback("updates.progress.fetchingUpdates", 10)

            # 3. Do a git fetch to retrieve the latest information
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "fetch", "origin", config["git_branch"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("git fetch timed out (120s)")

            if proc.returncode != 0:
                return {"success": False, "error": f"Git fetch failed: {stderr.decode()}"}

            if progress_callback:
                await progress_callback("updates.progress.checkingLocalChanges", 15)

            # 4. Check if there are uncommitted local changes
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if stdout.decode().strip():
                return {"success": False, "error": "Local changes detected. Please commit or stash them first."}

            if progress_callback:
                await progress_callback("updates.progress.pullingChanges", 20)

            # 5. Do the git pull
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "pull", "origin", config["git_branch"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("git pull timed out (120s)")

            if proc.returncode != 0:
                error_msg = f"Git pull failed: {stderr.decode()}"
                raise Exception(error_msg)

            if progress_callback:
                await progress_callback("updates.progress.installingFrontendDeps", 30)

            # 6. Install frontend npm dependencies
            frontend_dir = Path(config["git_path"]) / "frontend"
            if frontend_dir.exists():
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise Exception("npm install timed out (600s)")

                if proc.returncode != 0:
                    error_msg = f"npm install failed: {stderr.decode()}"
                    raise Exception(error_msg)

            if progress_callback:
                await progress_callback("updates.progress.buildingFrontend", 45)

            # 7. Build the frontend
            if frontend_dir.exists():
                proc = await asyncio.create_subprocess_exec(
                    "npm", "run", "build",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise Exception("npm run build timed out (600s)")

                if proc.returncode != 0:
                    error_msg = f"npm run build failed: {stderr.decode()}"
                    raise Exception(error_msg)

            if progress_callback:
                await progress_callback("updates.progress.installingPythonDeps", 60)

            # 8. Install Python dependencies in venv if requirements.txt exists
            requirements_file = Path(config["git_path"]) / "backend" / "requirements.txt"
            venv_pip = str(Path(config["git_path"]) / "venv" / "bin" / "pip3")
            if requirements_file.exists():
                proc = await asyncio.create_subprocess_exec(
                    venv_pip, "install", "-r", str(requirements_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

            if progress_callback:
                await progress_callback("updates.progress.syncingSystemFiles", 75)

            # 9. Sync system files (services, rootfs)
            await self._sync_system_files()

            if progress_callback:
                await progress_callback("updates.progress.rebooting", 95)

            # 10. Reboot the system to reload all services and configs
            # Small delay to ensure the WebSocket message is sent
            await asyncio.sleep(1)

            await self._run_deploy("reboot")

            # The process will be killed by the reboot, but return success in case it somehow continues
            return {
                "success": True,
                "message": "Milo application updated, rebooting...",
                "old_version": status["installed"]["versions"].get("main", "unknown"),
                "new_version": latest_version
            }

        except Exception as e:
            self.update_logger.error(f"Milo app update failed: {e}")

            # Automatic rollback if we have an original commit
            if original_commit:
                self.update_logger.info("Initiating automatic rollback...")
                if progress_callback:
                    await progress_callback("updates.progress.rollingBack", 90)

                rollback_success = await self._rollback_milo_to_commit(original_commit, progress_callback)

                if rollback_success:
                    if progress_callback:
                        await progress_callback("updates.progress.rollbackComplete", 100)
                    return {
                        "success": False,
                        "error": f"Update failed: {str(e)}. Rolled back to previous version.",
                        "rolled_back": True
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Update failed: {str(e)}. Rollback also failed - manual intervention required.",
                        "rolled_back": False
                    }

            return {"success": False, "error": str(e)}

    async def _run_deploy(self, *args, timeout: int = 120) -> tuple[bool, str]:
        """Run a milo-deploy-update subcommand via sudo."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", DEPLOY_UPDATE_CMD, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                error = stderr.decode().strip() or stdout.decode().strip()
                return False, error
            return True, stdout.decode().strip()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"Timed out after {timeout}s"
        except Exception as e:
            return False, str(e)

    async def _sync_system_files(self) -> None:
        """Sync system/ and rootfs/ files to their system destinations.

        Delegates to milo-deploy-update sync-system-files which handles:
        - Copying system/*.service to /etc/systemd/system/
        - systemctl daemon-reload
        - Copying rootfs/** preserving directory structure
        - Setting executable/ownership permissions
        - Reloading udev rules if needed
        """
        success, output = await self._run_deploy("sync-system-files", timeout=60)
        if not success:
            self.update_logger.warning(f"System files sync had errors: {output}")
        else:
            self.update_logger.info("System files sync completed")

    async def _update_go_librespot(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Updates go-librespot with proper service state preservation"""
        config = self.update_config["go-librespot"]
        latest_version = status["latest"]["version"]

        # Track if service was active before update
        service_was_active = await self._is_service_active(config["service_name"])
        self.update_logger.info(f"Service {config['service_name']} was {'active' if service_was_active else 'inactive'} before update")

        try:
            if progress_callback:
                await progress_callback("updates.progress.creatingBackup", 10)

            # 1. Create a backup
            backup_result = await self._backup_go_librespot(config)
            if not backup_result["success"]:
                return backup_result

            if progress_callback:
                await progress_callback("updates.progress.downloadingGoLibrespot", 20)

            # 2. Download the new version
            download_result = await self._download_go_librespot(latest_version)
            if not download_result["success"]:
                return download_result

            # 3. Stop service only if it was active
            if service_was_active:
                if progress_callback:
                    await progress_callback("updates.progress.stoppingService", 60)

                stop_result = await self._stop_service(config["service_name"])
                if not stop_result:
                    return {"success": False, "error": "Failed to stop service"}

            if progress_callback:
                await progress_callback("updates.progress.installingVersion", 70)

            # 4. Replace the binary
            install_result = await self._install_go_librespot_binary(download_result["binary_path"])
            if not install_result["success"]:
                # Rollback
                await self._rollback_go_librespot(config, service_was_active)
                return install_result

            # 5. Restart service only if it was previously active
            if service_was_active:
                if progress_callback:
                    await progress_callback("updates.progress.startingService", 90)

                start_result = await self._start_service(config["service_name"])
                if not start_result:
                    # Rollback
                    await self._rollback_go_librespot(config, service_was_active)
                    return {"success": False, "error": "Failed to start service after update"}

                if progress_callback:
                    await progress_callback("updates.progress.verifyingUpdate", 95)

                # 6. Verify that the update worked (only if service is running)
                verify_result = await self._verify_go_librespot_update(latest_version)
                if not verify_result["success"]:
                    # Rollback
                    await self._rollback_go_librespot(config, service_was_active)
                    return verify_result

            if progress_callback:
                await progress_callback("updates.progress.completed", 100)

            # 7. Clean up legacy version file if it exists (version now embedded in binary)
            legacy_version_file = Path("/var/lib/milo/go-librespot-version")
            if legacy_version_file.exists():
                try:
                    legacy_version_file.unlink()
                    self.update_logger.info("Removed legacy go-librespot-version file")
                except Exception as e:
                    self.update_logger.info(f"Could not remove legacy version file: {e}")

            # 8. Clean up temporary files
            await self._cleanup_temp_files(download_result.get("temp_dir"))

            return {
                "success": True,
                "message": f"go-librespot updated to {latest_version}",
                "old_version": status["installed"]["versions"].get("main"),
                "new_version": latest_version,
                "service_restarted": service_was_active
            }

        except Exception as e:
            # Rollback in case of error
            await self._rollback_go_librespot(config, service_was_active)
            self.update_logger.error(f"go-librespot update failed: {e}")
            return {"success": False, "error": str(e)}

    async def _update_multiroom(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Updates both snapserver and snapclient atomically"""
        config = self.update_config["multiroom"]
        latest_version = status["latest"]["version"]
        old_version = status["installed"]["versions"].get("main", "unknown")

        server_download = None
        client_download = None

        try:
            # Phase 1: Download both packages (0-30%)
            if progress_callback:
                await progress_callback("updates.progress.downloadingSnapserver", 5)

            server_download = await self._download_snapcast_component("snapserver", latest_version)
            if not server_download["success"]:
                return {"success": False, "error": f"Failed to download snapserver: {server_download.get('error')}"}

            if progress_callback:
                await progress_callback("updates.progress.downloadingSnapclient", 20)

            client_download = await self._download_snapcast_component("snapclient", latest_version)
            if not client_download["success"]:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                return {"success": False, "error": f"Failed to download snapclient: {client_download.get('error')}"}

            # Phase 2: Stop all services (30-40%)
            if progress_callback:
                await progress_callback("updates.progress.stoppingMultiroom", 35)

            for service in config["services"]:
                await self._stop_service(service)

            # Phase 3: Install snapserver (40-60%)
            if progress_callback:
                await progress_callback("updates.progress.installingSnapserver", 45)

            server_install = await self._install_deb_package(server_download["deb_path"])
            if not server_install["success"]:
                for service in config["services"]:
                    await self._start_service(service)
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return {"success": False, "error": f"Failed to install snapserver: {server_install.get('error')}"}

            # Phase 4: Install snapclient (60-80%)
            if progress_callback:
                await progress_callback("updates.progress.installingSnapclient", 65)

            client_install = await self._install_deb_package(client_download["deb_path"])
            if not client_install["success"]:
                self.update_logger.warning(f"Snapclient installation failed after snapserver succeeded: {client_install.get('error')}")
                for service in config["services"]:
                    await self._start_service(service)
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return {
                    "success": False,
                    "error": f"Snapserver updated but snapclient failed: {client_install.get('error')}",
                    "partial_success": True
                }

            # Phase 5: Restart services (80-95%)
            if progress_callback:
                await progress_callback("updates.progress.startingMultiroom", 85)

            all_services_started = True
            for service in config["services"]:
                start_result = await self._start_service(service)
                if not start_result:
                    self.update_logger.error(f"Failed to start {service}")
                    all_services_started = False

            # Phase 6: Cleanup (95-100%)
            if progress_callback:
                await progress_callback("updates.progress.cleaningUp", 95)

            await self._cleanup_temp_files(server_download.get("temp_dir"))
            await self._cleanup_temp_files(client_download.get("temp_dir"))

            if progress_callback:
                await progress_callback("updates.progress.completed", 100)

            result = {
                "success": True,
                "message": f"Multiroom updated to {latest_version}",
                "old_version": old_version,
                "new_version": latest_version
            }

            if not all_services_started:
                result["warning"] = "Some services failed to start automatically"

            return result

        except Exception as e:
            self.update_logger.error(f"Multiroom update failed: {e}")
            for service in config["services"]:
                await self._start_service(service)
            if server_download:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
            if client_download:
                await self._cleanup_temp_files(client_download.get("temp_dir"))
            return {"success": False, "error": str(e)}

    # === SHAIRPORT-SYNC (compile from source) ===

    async def _update_shairport_sync(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Updates shairport-sync by compiling from source"""
        config = self.update_config["shairport-sync"]
        latest_version = status["latest"]["version"]
        tag_name = status["latest"]["tag_name"]

        service_was_active = await self._is_service_active(config["service_name"])
        self.update_logger.info(f"Service {config['service_name']} was {'active' if service_was_active else 'inactive'} before update")

        temp_dir = None

        try:
            # Phase 1: Backup (5%)
            if progress_callback:
                await progress_callback("updates.progress.creatingBackup", 5)

            backup_result = await self._backup_shairport_sync(config)
            if not backup_result["success"]:
                return backup_result

            # Phase 2: Download source (10%)
            if progress_callback:
                await progress_callback("updates.progress.downloadingSource", 10)

            download_result = await self._download_shairport_sync_source(tag_name)
            if not download_result["success"]:
                return download_result
            temp_dir = download_result["temp_dir"]
            source_dir = download_result["source_dir"]

            # Phase 3: Configure (20%)
            if progress_callback:
                await progress_callback("updates.progress.configuringSource", 20)

            configure_result = await self._configure_shairport_sync(source_dir, config["configure_flags"])
            if not configure_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return configure_result

            # Phase 4: Compile (30%)
            if progress_callback:
                await progress_callback("updates.progress.compilingSource", 30)

            compile_result = await self._compile_shairport_sync(source_dir)
            if not compile_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return compile_result

            # Phase 5: Stop service only if active (75%)
            if service_was_active:
                if progress_callback:
                    await progress_callback("updates.progress.stoppingService", 75)

                stop_result = await self._stop_service(config["service_name"])
                if not stop_result:
                    await self._cleanup_temp_files(temp_dir)
                    return {"success": False, "error": "Failed to stop service"}

            # Phase 6: Install (80%)
            if progress_callback:
                await progress_callback("updates.progress.installingVersion", 80)

            install_result = await self._install_shairport_sync(source_dir)
            if not install_result["success"]:
                await self._rollback_shairport_sync(config, service_was_active)
                await self._cleanup_temp_files(temp_dir)
                return install_result

            # Phase 7: Restart service if it was active (85%)
            if service_was_active:
                if progress_callback:
                    await progress_callback("updates.progress.startingService", 85)

                start_result = await self._start_service(config["service_name"])
                if not start_result:
                    await self._rollback_shairport_sync(config, service_was_active)
                    await self._cleanup_temp_files(temp_dir)
                    return {"success": False, "error": "Failed to start service after update"}

            # Phase 8: Verify (90%)
            if progress_callback:
                await progress_callback("updates.progress.verifyingUpdate", 90)

            verify_result = await self._verify_shairport_sync_update(config, service_was_active)
            if not verify_result["success"]:
                await self._rollback_shairport_sync(config, service_was_active)
                await self._cleanup_temp_files(temp_dir)
                return verify_result

            # Write version file for reliable version tracking
            try:
                async with aiofiles.open('/var/lib/milo/shairport-sync-version', 'w') as f:
                    await f.write(latest_version)
            except Exception as e:
                self.update_logger.warning(f"Failed to write version file: {e}")

            # Phase 9: Cleanup (95-100%)
            if progress_callback:
                await progress_callback("updates.progress.cleaningUp", 95)

            await self._cleanup_temp_files(temp_dir)

            if progress_callback:
                await progress_callback("updates.progress.completed", 100)

            return {
                "success": True,
                "message": f"shairport-sync updated to {latest_version}",
                "old_version": status["installed"]["versions"].get("main"),
                "new_version": latest_version,
                "service_restarted": service_was_active
            }

        except Exception as e:
            self.update_logger.error(f"shairport-sync update failed: {e}")
            await self._rollback_shairport_sync(config, service_was_active)
            if temp_dir:
                await self._cleanup_temp_files(temp_dir)
            return {"success": False, "error": str(e)}

    async def _backup_shairport_sync(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Backs up the shairport-sync binary"""
        try:
            backup_dir = Path(config["backup_path"])
            backup_dir.mkdir(parents=True, exist_ok=True)

            binary_path = Path(config["binary_path"])
            if binary_path.exists():
                binary_backup = backup_dir / "shairport-sync.backup"
                shutil.copy2(config["binary_path"], binary_backup)

            return {"success": True, "backup_dir": str(backup_dir)}

        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    async def _download_shairport_sync_source(self, tag_name: str) -> Dict[str, Any]:
        """Downloads and extracts shairport-sync source tarball from GitHub"""
        try:
            temp_dir = tempfile.mkdtemp(dir="/tmp")
            url = f"https://github.com/mikebrady/shairport-sync/archive/refs/tags/{tag_name}.tar.gz"

            self.update_logger.info(f"Downloading shairport-sync source from {url}...")

            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}

                    archive_path = Path(temp_dir) / "shairport-sync-source.tar.gz"
                    async with aiofiles.open(archive_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            # Extract the archive
            proc = await asyncio.create_subprocess_exec(
                "tar", "-xzf", str(archive_path), "-C", temp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {"success": False, "error": f"Failed to extract archive: {stderr.decode()}"}

            # Find the extracted directory (shairport-sync-{tag})
            extracted_dirs = [d for d in Path(temp_dir).iterdir() if d.is_dir()]
            if not extracted_dirs:
                return {"success": False, "error": "No directory found in archive"}

            source_dir = str(extracted_dirs[0])
            self.update_logger.info(f"Source extracted to {source_dir}")

            return {
                "success": True,
                "source_dir": source_dir,
                "temp_dir": temp_dir
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _configure_shairport_sync(self, source_dir: str, configure_flags: list) -> Dict[str, Any]:
        """Runs autoreconf and configure for shairport-sync"""
        try:
            # Step 1: autoreconf -fi
            self.update_logger.info("Running autoreconf -fi...")
            proc = await asyncio.create_subprocess_exec(
                "autoreconf", "-fi",
                cwd=source_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"success": False, "error": "autoreconf timed out (5 min)"}

            if proc.returncode != 0:
                return {"success": False, "error": f"autoreconf failed: {stderr.decode()}"}

            # Step 2: ./configure with flags
            self.update_logger.info(f"Running ./configure with flags: {configure_flags}")
            proc = await asyncio.create_subprocess_exec(
                "./configure", *configure_flags,
                cwd=source_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"success": False, "error": "configure timed out (5 min)"}

            if proc.returncode != 0:
                return {"success": False, "error": f"configure failed: {stderr.decode()}"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Configure failed: {e}"}

    async def _compile_shairport_sync(self, source_dir: str) -> Dict[str, Any]:
        """Compiles shairport-sync with make"""
        try:
            # Get number of CPU cores for parallel build
            nproc_proc = await asyncio.create_subprocess_exec(
                "nproc",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            nproc_stdout, _ = await nproc_proc.communicate()
            num_cores = nproc_stdout.decode().strip() or "2"

            self.update_logger.info(f"Compiling shairport-sync with make -j{num_cores}...")
            proc = await asyncio.create_subprocess_exec(
                "make", f"-j{num_cores}",
                cwd=source_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"success": False, "error": "Compilation timed out (15 min)"}

            if proc.returncode != 0:
                return {"success": False, "error": f"Compilation failed: {stderr.decode()[-500:]}"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Compilation failed: {e}"}

    async def _install_shairport_sync(self, source_dir: str) -> Dict[str, Any]:
        """Installs compiled shairport-sync via DESTDIR staging + install-binary.

        Stages to a temp directory as unprivileged user, then installs
        only the binary via the secure milo-deploy-update wrapper.
        """
        staging_dir = tempfile.mkdtemp(prefix="milo-shairport-", dir="/tmp")
        try:
            # Stage install as unprivileged user (no sudo needed)
            self.update_logger.info("Staging shairport-sync install...")
            proc = await asyncio.create_subprocess_exec(
                "make", "install", f"DESTDIR={staging_dir}",
                cwd=source_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"success": False, "error": "make install staging timed out (120s)"}

            if proc.returncode != 0:
                return {"success": False, "error": f"Staged install failed: {stderr.decode()}"}

            # Install only the binary via the secure wrapper
            staged_binary = os.path.join(staging_dir, "usr/local/bin/shairport-sync")
            if not os.path.isfile(staged_binary):
                return {"success": False, "error": "Binary not found in staging directory"}

            success, output = await self._run_deploy(
                "install-binary", staged_binary, "/usr/local/bin/shairport-sync"
            )
            if not success:
                return {"success": False, "error": f"Installation failed: {output}"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Installation failed: {e}"}
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    async def _verify_shairport_sync_update(self, config: Dict[str, Any], service_was_active: bool) -> Dict[str, Any]:
        """Verifies that shairport-sync was updated successfully"""
        try:
            # Check binary exists
            binary_path = Path(config["binary_path"])
            if not binary_path.exists():
                return {"success": False, "error": "shairport-sync binary not found after update"}

            # Check service is running (only if it was active before)
            if service_was_active:
                is_active = await self._is_service_active(config["service_name"])
                if not is_active:
                    return {"success": False, "error": "shairport-sync service not running after update"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}

    async def _rollback_shairport_sync(self, config: Dict[str, Any], service_was_active: bool = True) -> bool:
        """Rollback shairport-sync to the backed up version."""
        try:
            binary_backup = Path(config["backup_path"]) / "shairport-sync.backup"
            if not binary_backup.exists():
                self.update_logger.error("No backup found for rollback")
                return False

            await self._stop_service(config["service_name"])

            # Copy backup to /tmp for install-binary (requires temp path)
            tmp_backup = Path(tempfile.mktemp(prefix="milo-rollback-", dir="/tmp"))
            shutil.copy2(binary_backup, tmp_backup)
            try:
                success, output = await self._run_deploy(
                    "install-binary", str(tmp_backup), config["binary_path"]
                )
                if not success:
                    self.update_logger.error(f"Rollback install failed: {output}")
                    return False
            finally:
                tmp_backup.unlink(missing_ok=True)

            if service_was_active:
                await self._start_service(config["service_name"])

            self.update_logger.info(f"shairport-sync rollback completed (service {'restarted' if service_was_active else 'left stopped'})")
            return True

        except Exception as e:
            self.update_logger.error(f"shairport-sync rollback failed: {e}")
            return False

    # === UTILITY METHODS ===

    async def _backup_go_librespot(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Backs up go-librespot"""
        try:
            backup_dir = Path(config["backup_path"])
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Backup the binary
            binary_backup = backup_dir / "go-librespot.backup"
            shutil.copy2(config["binary_path"], binary_backup)

            # Backup config if it exists
            config_path = Path(config["config_path"])
            if config_path.exists():
                config_backup = backup_dir / "config.yml.backup"
                shutil.copy2(config_path, config_backup)

            return {"success": True, "backup_dir": str(backup_dir)}

        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    async def _download_go_librespot(self, version: str) -> Dict[str, Any]:
        """Downloads go-librespot from GitHub"""
        try:
            # Create a temporary directory
            temp_dir = tempfile.mkdtemp(dir="/tmp")

            # Download URL
            url = f"https://github.com/devgianlu/go-librespot/releases/download/v{version}/go-librespot_linux_arm64.tar.gz"

            # Download
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}

                    archive_path = Path(temp_dir) / "go-librespot.tar.gz"
                    async with aiofiles.open(archive_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            # Extract the archive
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()

            proc = await asyncio.create_subprocess_exec(
                "tar", "-xzf", str(archive_path), "-C", str(extract_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            if proc.returncode != 0:
                return {"success": False, "error": "Failed to extract archive"}

            # Find the binary
            binary_path = extract_dir / "go-librespot"
            if not binary_path.exists():
                return {"success": False, "error": "Binary not found in archive"}

            return {
                "success": True,
                "binary_path": str(binary_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            return {"success": False, "error": str(e)}


    async def _get_debian_codename(self) -> str:
        """Detects the Debian version of the system (bookworm, trixie, etc.)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", "source /etc/os-release && echo $VERSION_CODENAME",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await proc.communicate()
            codename = stdout.decode().strip()

            if codename:
                self.update_logger.info(f"Detected Debian codename: {codename}")
                return codename
            else:
                self.update_logger.warning("Could not detect Debian codename, using 'bookworm' as fallback")
                return "bookworm"

        except Exception as e:
            self.update_logger.error(f"Error detecting Debian codename: {e}, using 'bookworm' as fallback")
            return "bookworm"

    async def _download_snapcast_component(self, component_key: str, version: str) -> Dict[str, Any]:
        """Downloads a snapcast component (.deb) with auto Debian detection"""
        try:
            # Detect Debian version
            debian_codename = await self._get_debian_codename()

            temp_dir = tempfile.mkdtemp(dir="/tmp")

            # Determine package name according to component
            if component_key == "snapserver":
                package_name = f"snapserver_{version}-1_arm64_{debian_codename}.deb"
            elif component_key == "snapclient":
                package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
            else:
                return {"success": False, "error": f"Unknown component: {component_key}"}

            # Download URL
            url = f"https://github.com/badaix/snapcast/releases/download/v{version}/{package_name}"

            self.update_logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")

            # Download
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}

                    deb_path = Path(temp_dir) / package_name
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

    async def _install_go_librespot_binary(self, binary_path: str) -> Dict[str, Any]:
        """Installs the new go-librespot binary via milo-deploy-update."""
        success, output = await self._run_deploy(
            "install-binary", binary_path, "/usr/local/bin/go-librespot"
        )
        if not success:
            return {"success": False, "error": f"Failed to install binary: {output}"}
        return {"success": True}

    async def _install_deb_package(self, deb_path: str) -> Dict[str, Any]:
        """Installs a .deb package via milo-deploy-update (dpkg + apt-get -f)."""
        self.update_logger.info(f"Installing {Path(deb_path).name}...")
        success, output = await self._run_deploy("install-deb", deb_path, timeout=300)
        if not success:
            return {"success": False, "error": f"Package installation failed: {output}"}
        self.update_logger.info("Package installed successfully")
        return {"success": True}

    async def _is_service_active(self, service_name: str) -> bool:
        """Checks if a systemd service is currently active"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.update_logger.error(f"Failed to check service status for {service_name}: {e}")
            return False

    async def _stop_service(self, service_name: str) -> bool:
        """Stops a systemd service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            return proc.returncode == 0

        except Exception as e:
            self.update_logger.error(f"Failed to stop {service_name}: {e}")
            return False

    async def _start_service(self, service_name: str) -> bool:
        """Starts a systemd service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return False

            # Wait for the service to actually start
            await asyncio.sleep(2)

            # Check status
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            return stdout.decode().strip() == "active"

        except Exception as e:
            self.update_logger.error(f"Failed to start {service_name}: {e}")
            return False

    async def _restart_service(self, service_name: str) -> bool:
        """Restarts a systemd service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.update_logger.error(f"Failed to restart {service_name}: {stderr.decode()}")
                return False

            # Wait for the service to actually start
            await asyncio.sleep(2)

            # Check status
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            return stdout.decode().strip() == "active"

        except Exception as e:
            self.update_logger.error(f"Failed to restart {service_name}: {e}")
            return False

    async def _verify_go_librespot_update(self, expected_version: str) -> Dict[str, Any]:
        """Verifies that go-librespot was updated by checking binary exists and service runs"""
        try:
            # Check binary exists
            binary_path = Path("/usr/local/bin/go-librespot")
            if not binary_path.exists():
                return {"success": False, "error": "go-librespot binary not found after update"}

            # Check service is running
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "milo-spotify.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if stdout.decode().strip() != "active":
                return {"success": False, "error": "go-librespot service not running after update"}

            return {"success": True, "verified_version": expected_version}

        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}

    async def _rollback_go_librespot(self, config: Dict[str, Any], service_was_active: bool = True) -> bool:
        """Rollback go-librespot to the backed up version, respecting previous service state."""
        try:
            binary_backup = Path(config["backup_path"]) / "go-librespot.backup"
            if not binary_backup.exists():
                self.update_logger.error("No backup found for rollback")
                return False

            await self._stop_service(config["service_name"])

            # Copy backup to /tmp for install-binary (requires temp path)
            tmp_backup = Path(tempfile.mktemp(prefix="milo-rollback-", dir="/tmp"))
            shutil.copy2(binary_backup, tmp_backup)
            try:
                success, output = await self._run_deploy(
                    "install-binary", str(tmp_backup), config["binary_path"]
                )
                if not success:
                    self.update_logger.error(f"Rollback install failed: {output}")
                    return False
            finally:
                tmp_backup.unlink(missing_ok=True)

            if service_was_active:
                await self._start_service(config["service_name"])

            self.update_logger.info(f"go-librespot rollback completed (service {'restarted' if service_was_active else 'left stopped'})")
            return True

        except Exception as e:
            self.update_logger.error(f"Rollback failed: {e}")
            return False

    async def _cleanup_temp_files(self, temp_dir: Optional[str]) -> None:
        """Cleans up temporary files"""
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self.update_logger.warning(f"Failed to cleanup {temp_dir}: {e}")

    async def can_update_program(self, program_key: str) -> Dict[str, Any]:
        """Checks if a program can be updated"""
        if program_key not in self.update_config:
            return {"can_update": False, "reason": "Update not supported"}

        # Verify the deploy wrapper is reachable via sudo NOPASSWD
        success, _ = await self._run_deploy("check", timeout=5)
        if not success:
            return {"can_update": False, "reason": "Deploy wrapper not accessible"}

        # Check that an update is available
        status = await self._get_program_full_status(program_key)
        if not status.get("update_available"):
            return {"can_update": False, "reason": "No update available"}

        return {"can_update": True, "available_version": status["latest"]["version"]}
