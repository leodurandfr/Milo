# backend/core/updates/update.py
"""
Update service - installs a new version of each program Milo ships.
"""
import asyncio
import aiohttp
import aiofiles
import shutil
import tempfile
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from backend.core.updates.version import VersionService
from backend.core.updates.dependency_versions import apply_validated_versions
from backend.config.constants import DEPLOY_UPDATE_CMD

# qobuz-proxy is a pip package installed from a git tag; the in-app update pins
# the same URL the installer uses (install/qobuz-proxy.sh) and re-applies our
# vendored patches via the shared script (single source of truth for the anchors).
QOBUZ_PROXY_REPO_URL = "https://github.com/leolobato/qobuz-proxy"
QOBUZ_PROXY_PATCHES_SCRIPT = "/home/milo/milo/install/qobuz_proxy_patches.py"


class UpdateService(VersionService):
    """Update service - Extends VersionService"""

    def __init__(self, systemd_manager, satellite_update_service):
        super().__init__()
        self._systemd = systemd_manager
        self._satellites = satellite_update_service
        self.update_logger = logging.getLogger(f"{__name__}.update")

    async def update_program(self, program_key: str) -> Dict[str, Any]:
        """Dispatches a program key to the flow that knows how to update it."""
        if program_key not in self.programs:
            return {"success": False, "error": f"Update not supported for {program_key}"}

        try:
            # Check that an update is available
            status = await self.get_program_full_status(program_key)
            if not status.get("update_available"):
                return {"success": False, "error": "No update available"}

            return await self._dispatch_update(program_key, status)

        except Exception as e:
            self.update_logger.error(f"Update failed for {program_key}: {e}")
            return {"success": False, "error": str(e)}

    async def _dispatch_update(self, program_key: str, status: Dict[str, Any]) -> Dict[str, Any]:
        """Route one program to its install flow. Two callers, one implementation.

        `update_program` is the route's entry point; `_reconcile_dependencies`
        is the Milō update bringing the set to the manifest. Both have already
        decided there is something to install, so neither re-checks here.
        """
        if program_key == "milo":
            return await self._update_milo_app(status)
        elif program_key == "multiroom":
            return await self._update_multiroom(status)
        elif program_key == "shairport-sync":
            return await self._update_shairport_sync(status)
        elif program_key == "qobuz-proxy":
            return await self._update_qobuz_proxy(status)
        elif "asset_url" in self.programs[program_key]:
            return await self._update_binary_program(program_key, status)
        else:
            return {"success": False, "error": f"Update handler not implemented for {program_key}"}

    async def _reconcile_dependencies(self) -> list[str]:
        """Bring every dependency to the version the *pulled* manifest declares.

        A Milō update installs the app and the dependency set validated with it,
        as one sequence — never as one transaction, and never deferred to the
        next boot. Deferring would put a multi-minute source compile behind a
        dark screen with the backend down and nothing anywhere to say why; a
        transaction would mean composing seven independent rollbacks into one,
        whose half-applied states are worse than either end.

        So each step keeps its own backup and its own rollback, and a step that
        failed *and restored itself* is reported rather than fatal: the app still
        reboots into the new code with the old dependency, which is the state
        every unit is already in for any dependency nobody clicked. The caller
        relies on this never raising — it runs past the point where the app can
        still be rolled back.

        Returns the keys that failed, for the journal and for the one caller path
        that survives to return an envelope.
        """
        # The pulled tree carries a new dependencies.env that this process was
        # started before, and the cached GitHub results carry the *old* pin.
        # Without both of these the reconciliation compares against the set the
        # unit already had, and does nothing at all.
        apply_validated_versions(self.programs)
        self._github_cache.clear()
        self._last_github_fetch.clear()

        failed: list[str] = []
        for program_key in self.programs:
            if program_key == "milo":
                continue
            try:
                status = await self.get_program_full_status(program_key)
                if not status.get("update_available"):
                    continue

                self.update_logger.info(
                    f"{program_key}: installing the validated "
                    f"{status['latest']['version']} as part of the Milo update"
                )
                result = await self._dispatch_update(program_key, status)
                if not result.get("success"):
                    failed.append(program_key)
                    self.update_logger.error(
                        f"{program_key}: could not reach the validated version "
                        f"({result.get('error', 'unknown error')}). The app update "
                        "continues; the unit will run the previous version of it."
                    )
            except Exception as e:
                failed.append(program_key)
                self.update_logger.error(f"{program_key}: reconciliation failed: {e}")

        return failed

    async def _get_current_commit(self, git_path: str) -> str:
        """Get current HEAD commit hash, or "" when it cannot be read.

        The empty string is what `_update_milo_app`'s `if original_commit:`
        guard reads as "nothing to roll back to", and it then skips the
        automatic rollback entirely — rightly, since `git reset --hard ""`
        would be worse. So the failure is logged at error here: an update with
        no rollback point must say so before it starts, not disappear into an
        update that fails quietly at the end.
        """
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", git_path, "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            self.update_logger.error(
                f"Cannot read HEAD in {git_path}: {stderr.decode().strip()} - "
                "this update will have no automatic rollback"
            )
            return ""

        return stdout.decode().strip()

    @staticmethod
    def _failure_after_rollback(error: str, rolled_back: bool) -> Dict[str, Any]:
        """The failure envelope of a path that restored the previous version.

        `success` is False either way — what the two outcomes distinguish is
        whether the program is running again, which is the only half the owner
        can act on. The route logs `error` at error level, so both reach the
        UI's banner.
        """
        outcome = (
            "Rolled back to previous version."
            if rolled_back
            else "Rollback also failed - manual intervention required."
        )
        return {"success": False, "error": f"{error} {outcome}"}

    @staticmethod
    def _failure_after_restore(error: str, restored: bool) -> Dict[str, Any]:
        """Same, for multiroom: nothing is reinstalled, only the units go back.

        A snapcast unit that did not come back leaves the appliance mute, so it
        is worth a different sentence than a binary that was not restored.
        """
        outcome = (
            "Multiroom services restored."
            if restored
            else "Some multiroom services did not come back - manual intervention required."
        )
        return {"success": False, "error": f"{error} {outcome}"}

    async def _rollback_milo_to_commit(self, commit_hash: str) -> bool:
        """Rollback Milo to a specific commit and rebuild"""
        config = self.programs["milo"]
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

            # Rebuild frontend after rollback. Bounded and checked exactly like
            # the forward path: an npm that hangs here would freeze the rollback
            # with no ceiling, leaving the backend un-restarted and the update
            # key in active_updates — the UI would show an update running forever.
            frontend_dir = Path(config["git_path"]) / "frontend"
            if frontend_dir.exists():
                await self._run_npm(["install"], frontend_dir)
                await self._run_npm(["run", "build"], frontend_dir)

            # Reinstall Python dependencies in venv
            requirements_file = Path(config["git_path"]) / "requirements.txt"
            venv_pip = str(Path(config["git_path"]) / "venv" / "bin" / "pip3")
            proc = await asyncio.create_subprocess_exec(
                venv_pip, "install", "-r", str(requirements_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("pip install timed out (600s)")

            if proc.returncode != 0:
                raise Exception(f"pip install failed: {stderr.decode()}")

            # Sync system files from rolled-back version
            await self._sync_system_files()

            # Restart services. Kiosk first (observable); milo-backend LAST and
            # fire-and-forget — restarting our own unit tears this process down
            # mid-call, so anything after it would not run.
            if not await self._restart_service("milo-kiosk.service"):
                # Logged, not fatal: the tree is back on the previous commit and
                # the backend restart below is what puts it back in service. A
                # kiosk that stayed down is a black screen the owner has to be
                # told about, not a reason to call the rollback failed.
                self.update_logger.error("Kiosk failed to restart after the rollback")
            await self._systemd.restart_self(config["service_name"])

            self.update_logger.info("Milo rollback completed successfully")
            return True

        except Exception as e:
            self.update_logger.error(f"Milo rollback failed: {e}")
            return False

    async def _update_milo_app(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates Milo application via git pull with automatic rollback on failure"""
        config = self.programs["milo"]
        original_commit = None

        try:
            git_dir = Path(config["git_path"]) / ".git"
            if not git_dir.exists():
                return {"success": False, "error": "Not a git repository"}

            # 2. Save current commit for potential rollback
            original_commit = await self._get_current_commit(config["git_path"])
            if original_commit:
                self.update_logger.info(f"Current commit before update: {original_commit[:8]}")

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

            # 4. Check if there are uncommitted local changes
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            # An unread exit code makes a failed status indistinguishable from a
            # clean tree: both give empty stdout, and the pull would proceed.
            if proc.returncode != 0:
                return {"success": False, "error": f"Git status failed: {stderr.decode()}"}

            if stdout.decode().strip():
                return {"success": False, "error": "Local changes detected. Please commit or stash them first."}

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

            frontend_dir = Path(config["git_path"]) / "frontend"
            if frontend_dir.exists():
                await self._run_npm(["install"], frontend_dir)

            if frontend_dir.exists():
                await self._run_npm(["run", "build"], frontend_dir)

            # 8. Install Python dependencies in venv
            requirements_file = Path(config["git_path"]) / "requirements.txt"
            venv_pip = str(Path(config["git_path"]) / "venv" / "bin" / "pip3")
            proc = await asyncio.create_subprocess_exec(
                venv_pip, "install", "-r", str(requirements_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("pip install timed out (600s)")

            if proc.returncode != 0:
                raise Exception(f"pip install failed: {stderr.decode()}")

            # 9. Sync system files (services, rootfs)
            await self._sync_system_files()

            # 10. Install the dependency set the pulled commit validated.
            # Deliberately *after* the app is built and synced: everything above
            # this line can still roll the app back, and rolling back with the
            # dependencies already moved would leave the two out of step. Below
            # it, the only remaining step is the reboot — which is why
            # _reconcile_dependencies never raises.
            dependency_failures = await self._reconcile_dependencies()

            # 10b. Push the same commit's client app to the satellites. The
            # tarball is built from the tree the pull just replaced, so the fleet
            # is stale from this line on — leaving it there means the appliance
            # updates and the speakers do not, until someone finds their rows.
            # Below step 10 for the same reason as the set above (a rollback
            # with the satellites already moved leaves them ahead of the server
            # that drives them), and after it rather than before so the local
            # snapserver has finished whatever the reconciliation did to it —
            # a satellite is discovered through it. Never raises, same rule.
            satellite_failures = await self._satellites.push_client_app_to_fleet()

            # 11. Reboot the system to reload all services and configs
            # Small delay to ensure the WebSocket message is sent
            await asyncio.sleep(1)

            rebooting, reboot_output = await self._run_deploy("reboot")
            if not rebooting:
                # No rollback: the new code is pulled, built and synced — it is
                # the *restart* that did not happen, and undoing a good update
                # over that would be worse. Report it so the owner reboots.
                self.update_logger.error(f"Reboot refused after a successful update: {reboot_output}")
                return {
                    "success": False,
                    "error": f"Update applied but the reboot failed ({reboot_output}). Reboot to complete it.",
                    "dependency_failures": dependency_failures,
                    "satellite_failures": satellite_failures,
                }

            # The process will be killed by the reboot, but return success in case it somehow continues.
            # A dependency that failed is reported here and in the journal; after the reboot it is
            # visible for as long as it lasts, as installed != validated on the dependency rows.
            return {
                "success": True,
                "dependency_failures": dependency_failures,
                "satellite_failures": satellite_failures,
            }

        except Exception as e:
            self.update_logger.error(f"Milo app update failed: {e}")

            # Automatic rollback if we have an original commit
            if original_commit:
                self.update_logger.info("Initiating automatic rollback...")
                rollback_success = await self._rollback_milo_to_commit(original_commit)

                return self._failure_after_rollback(f"Update failed: {e}.", rollback_success)

            return {"success": False, "error": str(e)}

    async def _run_npm(self, args: list, cwd: Path, timeout: int = 600) -> None:
        """Run one npm step, bounded and checked. Raises on timeout or non-zero.

        The forward path and the rollback share this: both rebuild the same
        frontend, and an unbounded or unchecked npm on either side produces the
        same silent outcome — a broken `frontend/dist/` reported as a success.
        """
        step = "npm " + " ".join(args)
        proc = await asyncio.create_subprocess_exec(
            "npm", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise Exception(f"{step} timed out ({timeout}s)")

        if proc.returncode != 0:
            raise Exception(f"{step} failed: {stderr.decode()}")

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

        Raises on failure rather than warning: the units and the rootfs helpers
        this copies are what the new code expects to find on the next boot, so a
        sync that did not happen is an update that did not happen. The caller
        turns it into a failed update and a rollback.
        """
        success, output = await self._run_deploy("sync-system-files", timeout=60)
        if not success:
            raise Exception(f"System files sync failed: {output}")
        self.update_logger.info("System files sync completed")

    async def _update_binary_program(self, program_key: str, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates a program shipped as a single binary inside a release tarball.

        go-librespot, CamillaDSP and Navidrome all follow this flow: back the
        binary up, download and unpack the release asset, stop the service, swap
        the binary through the deploy wrapper, start it again, verify, and roll
        back to the backup if anything after the stop fails.

        The one behavioural difference is service policy. CamillaDSP and
        Navidrome are always-on, so they are stopped and started
        unconditionally; go-librespot's Spotify service is on-demand, so its
        previous state is preserved and an inactive service is left inactive.
        """
        config = self.programs[program_key]
        display_name = config["log_name"]
        latest_version = status["latest"]["version"]

        service_was_active = await self._is_service_active(config["service_name"])
        self.update_logger.info(f"Service {config['service_name']} was {'active' if service_was_active else 'inactive'} before update")
        run_service = config.get("always_on", False) or service_was_active

        download_result = None
        service_stopped = False

        try:
            backup_result = await self._backup_binary_program(config)
            if not backup_result["success"]:
                return backup_result

            download_result = await self._download_binary_program(config, latest_version)
            if not download_result["success"]:
                return download_result

            if run_service:
                if not await self._stop_service(config["service_name"]):
                    await self._cleanup_temp_files(download_result.get("temp_dir"))
                    return {"success": False, "error": "Failed to stop service"}

                service_stopped = True
                # Let the kernel release the running image before it is
                # overwritten, otherwise install-binary hits "Text file busy".
                await asyncio.sleep(0.5)

            success, output = await self._run_deploy(
                "install-binary", download_result["binary_path"], config["binary_path"]
            )
            if not success:
                await self._cleanup_temp_files(download_result.get("temp_dir"))
                rolled_back = await self._rollback_binary_program(config, run_service)
                return self._failure_after_rollback(f"Failed to install binary: {output}.", rolled_back)

            if run_service:
                if not await self._start_service(config["service_name"]):
                    await self._cleanup_temp_files(download_result.get("temp_dir"))
                    rolled_back = await self._rollback_binary_program(config, run_service)
                    return self._failure_after_rollback(
                        f"Failed to start {display_name} after update.", rolled_back
                    )

            verify_result = await self._verify_binary_program(config, run_service)
            if not verify_result["success"]:
                await self._cleanup_temp_files(download_result.get("temp_dir"))
                rolled_back = await self._rollback_binary_program(config, run_service)
                return self._failure_after_rollback(f"{verify_result['error']}.", rolled_back)

            await self._cleanup_temp_files(download_result.get("temp_dir"))

            return {"success": True}

        except Exception as e:
            self.update_logger.error(f"{display_name} update failed: {e}")
            if download_result:
                await self._cleanup_temp_files(download_result.get("temp_dir"))
            if not service_stopped:
                # Nothing was replaced yet, so there is nothing to restore and no
                # rollback outcome to report.
                return {"success": False, "error": str(e)}
            rolled_back = await self._rollback_binary_program(config, run_service)
            return self._failure_after_rollback(f"{e}.", rolled_back)

    async def _backup_binary_program(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Backs up the program's binary, plus its config file when it has one.

        A missing binary fails the backup on purpose: without one there is
        nothing to roll back to, so the update must not start.
        """
        try:
            backup_dir = Path(config["backup_path"])
            backup_dir.mkdir(parents=True, exist_ok=True)

            binary_path = Path(config["binary_path"])
            shutil.copy2(binary_path, backup_dir / f"{binary_path.name}.backup")

            config_path = config.get("config_path")
            if config_path and Path(config_path).exists():
                shutil.copy2(config_path, backup_dir / f"{Path(config_path).name}.backup")

            return {"success": True, "backup_dir": str(backup_dir)}

        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    async def _download_binary_program(self, config: Dict[str, Any], version: str) -> Dict[str, Any]:
        """Downloads the release tarball and extracts the program's binary from it."""
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        binary_name = Path(config["binary_path"]).name
        try:
            url = config["asset_url"].format(version=version)

            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}

                    archive_path = Path(temp_dir) / f"{binary_name}.tar.gz"
                    async with aiofiles.open(archive_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()

            # Naming a member keeps the docs some tarballs ship out of the way.
            tar_args = ["tar", "-xzf", str(archive_path), "-C", str(extract_dir)]
            if config.get("tar_member"):
                tar_args.append(config["tar_member"])

            proc = await asyncio.create_subprocess_exec(
                *tar_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            if proc.returncode != 0:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": "Failed to extract archive"}

            binary_path = extract_dir / binary_name
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

    async def _verify_binary_program(self, config: Dict[str, Any], expect_service_active: bool) -> Dict[str, Any]:
        """Verifies the new binary is in place and the service came back up."""
        try:
            if not Path(config["binary_path"]).exists():
                return {"success": False, "error": f"{config['log_name']} binary not found after update"}

            if expect_service_active and not await self._is_service_active(config["service_name"]):
                return {"success": False, "error": f"{config['log_name']} service not running after update"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}

    async def _rollback_binary_program(self, config: Dict[str, Any], restart_service: bool = True) -> bool:
        """Restores the backed-up binary, respecting the service's previous state."""
        display_name = config["log_name"]
        try:
            binary_path = Path(config["binary_path"])
            binary_backup = Path(config["backup_path"]) / f"{binary_path.name}.backup"
            if not binary_backup.exists():
                self.update_logger.error("No backup found for rollback")
                return False

            # A unit that refuses to stop keeps its image loaded: install-binary
            # either hits "Text file busy" or writes a file nothing is running,
            # and the start below would report the *new* binary still up. Neither
            # is a rollback, so it does not claim one.
            if not await self._stop_service(config["service_name"]):
                self.update_logger.error(f"{display_name} did not stop, cannot restore its binary")
                return False
            await asyncio.sleep(0.5)

            # Copy backup to /tmp for install-binary (requires temp path)
            tmp_backup = Path(tempfile.mktemp(prefix="milo-rollback-", dir="/tmp"))
            shutil.copy2(binary_backup, tmp_backup)
            try:
                success, output = await self._run_deploy(
                    "install-binary", str(tmp_backup), str(binary_path)
                )
                if not success:
                    self.update_logger.error(f"Rollback install failed: {output}")
                    return False
            finally:
                tmp_backup.unlink(missing_ok=True)

            if restart_service and not await self._start_service(config["service_name"]):
                self.update_logger.error(f"{display_name} binary restored but the service did not start")
                return False

            self.update_logger.info(f"{display_name} rollback completed (service {'restarted' if restart_service else 'left stopped'})")
            return True

        except Exception as e:
            self.update_logger.error(f"{display_name} rollback failed: {e}")
            return False

    async def _restore_multiroom_services(self, services_were_active: Dict[str, bool]) -> bool:
        """Starts back only the multiroom units that were running before the update.

        The two snapcast units have no `WantedBy`: their lifecycle is owned solely
        by `AudioRoutingService._sync_snapcast_state`, which runs at init and inside
        `set_multiroom_enabled` and never reconciles afterwards. Starting a unit that
        was inactive leaves snapclient holding hw:Loopback,0,0, so the next
        direct-mode source opens a busy device and plays silence until a reboot or a
        manual multiroom toggle.

        Returns whether every unit that was running before the update is running
        again. The recovery branches report it; the success path does not, on
        purpose — there the caller has nothing better to say than the per-unit
        error already logged here.
        """
        restored = True
        for service, was_active in services_were_active.items():
            if not was_active:
                self.update_logger.info(f"Leaving {service} stopped (it was inactive before the update)")
                continue
            if not await self._start_service(service):
                self.update_logger.error(f"Failed to start {service}")
                restored = False
        return restored

    async def _update_multiroom(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates both snapserver and snapclient atomically"""
        config = self.programs["multiroom"]
        latest_version = status["latest"]["version"]

        server_download = None
        client_download = None
        # Empty until phase 2 stops anything, so a failure before that restores nothing.
        services_were_active: Dict[str, bool] = {}

        try:
            # Phase 1: Download both packages (0-30%)
            server_download = await self._download_snapcast_component("snapserver", latest_version)
            if not server_download["success"]:
                return {"success": False, "error": f"Failed to download snapserver: {server_download.get('error')}"}

            client_download = await self._download_snapcast_component("snapclient", latest_version)
            if not client_download["success"]:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                return {"success": False, "error": f"Failed to download snapclient: {client_download.get('error')}"}

            # Phase 2: Stop all services (30-40%)
            for service in config["services"]:
                services_were_active[service] = await self._is_service_active(service)
                self.update_logger.info(f"Service {service} was {'active' if services_were_active[service] else 'inactive'} before update")
                await self._stop_service(service)

            # Phase 3: Install snapserver (40-60%)
            server_install = await self._install_deb_package(server_download["deb_path"])
            if not server_install["success"]:
                restored = await self._restore_multiroom_services(services_were_active)
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return self._failure_after_restore(
                    f"Failed to install snapserver: {server_install.get('error')}.", restored
                )

            # Phase 4: Install snapclient (60-80%)
            client_install = await self._install_deb_package(client_download["deb_path"])
            if not client_install["success"]:
                self.update_logger.warning(f"Snapclient installation failed after snapserver succeeded: {client_install.get('error')}")
                restored = await self._restore_multiroom_services(services_were_active)
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return self._failure_after_restore(
                    f"Snapserver updated but snapclient failed: {client_install.get('error')}.", restored
                )

            # Phase 5: Restart services (80-95%)
            await self._restore_multiroom_services(services_were_active)

            # Phase 6: Cleanup (95-100%)
            await self._cleanup_temp_files(server_download.get("temp_dir"))
            await self._cleanup_temp_files(client_download.get("temp_dir"))

            # A service that failed to come back up is reported by the per-service
            # error log above; the caller only distinguishes success from failure.
            return {"success": True}

        except Exception as e:
            self.update_logger.error(f"Multiroom update failed: {e}")
            restored = await self._restore_multiroom_services(services_were_active)
            if server_download:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
            if client_download:
                await self._cleanup_temp_files(client_download.get("temp_dir"))
            # Nothing was stopped before phase 2, so there is nothing to restore
            # and no outcome to report — services_were_active is still empty.
            if not services_were_active:
                return {"success": False, "error": str(e)}
            return self._failure_after_restore(f"{e}.", restored)

    # === SHAIRPORT-SYNC (compile from source) ===

    async def _update_shairport_sync(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates shairport-sync by compiling from source"""
        config = self.programs["shairport-sync"]
        latest_version = status["latest"]["version"]
        tag_name = status["latest"]["tag_name"]

        service_was_active = await self._is_service_active(config["service_name"])
        self.update_logger.info(f"Service {config['service_name']} was {'active' if service_was_active else 'inactive'} before update")

        temp_dir = None

        try:
            # Phase 1: Backup (5%)
            backup_result = await self._backup_shairport_sync(config)
            if not backup_result["success"]:
                return backup_result

            # Phase 2: Download source (10%)
            download_result = await self._download_shairport_sync_source(tag_name)
            if not download_result["success"]:
                return download_result
            temp_dir = download_result["temp_dir"]
            source_dir = download_result["source_dir"]

            # Phase 3: Configure (20%)
            configure_result = await self._configure_shairport_sync(source_dir, config["configure_flags"])
            if not configure_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return configure_result

            # Phase 4: Compile (30%)
            compile_result = await self._compile_shairport_sync(source_dir)
            if not compile_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return compile_result

            # Phase 5: Stop service only if active (75%)
            if service_was_active:
                stop_result = await self._stop_service(config["service_name"])
                if not stop_result:
                    await self._cleanup_temp_files(temp_dir)
                    return {"success": False, "error": "Failed to stop service"}

            # Phase 6: Install (80%)
            install_result = await self._install_shairport_sync(source_dir)
            if not install_result["success"]:
                rolled_back = await self._rollback_shairport_sync(config, service_was_active)
                await self._cleanup_temp_files(temp_dir)
                return self._failure_after_rollback(f"{install_result['error']}.", rolled_back)

            # Phase 7: Restart service if it was active (85%)
            if service_was_active:
                start_result = await self._start_service(config["service_name"])
                if not start_result:
                    rolled_back = await self._rollback_shairport_sync(config, service_was_active)
                    await self._cleanup_temp_files(temp_dir)
                    return self._failure_after_rollback(
                        "Failed to start service after update.", rolled_back
                    )

            # Phase 8: Verify (90%)
            verify_result = await self._verify_shairport_sync_update(config, service_was_active)
            if not verify_result["success"]:
                rolled_back = await self._rollback_shairport_sync(config, service_was_active)
                await self._cleanup_temp_files(temp_dir)
                return self._failure_after_rollback(f"{verify_result['error']}.", rolled_back)

            # Write version file for reliable version tracking
            try:
                async with aiofiles.open('/var/lib/milo/shairport-sync-version', 'w') as f:
                    await f.write(latest_version)
            except Exception as e:
                self.update_logger.warning(f"Failed to write version file: {e}")

            # Phase 9: Cleanup (95-100%)
            await self._cleanup_temp_files(temp_dir)

            return {"success": True}

        except Exception as e:
            self.update_logger.error(f"shairport-sync update failed: {e}")
            rolled_back = await self._rollback_shairport_sync(config, service_was_active)
            if temp_dir:
                await self._cleanup_temp_files(temp_dir)
            return self._failure_after_rollback(f"{e}.", rolled_back)

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
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        try:
            url = f"https://github.com/mikebrady/shairport-sync/archive/refs/tags/{tag_name}.tar.gz"

            self.update_logger.info(f"Downloading shairport-sync source from {url}...")

            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        shutil.rmtree(temp_dir, ignore_errors=True)
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
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": f"Failed to extract archive: {stderr.decode()}"}

            # Find the extracted directory (shairport-sync-{tag})
            extracted_dirs = [d for d in Path(temp_dir).iterdir() if d.is_dir()]
            if not extracted_dirs:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": "No directory found in archive"}

            source_dir = str(extracted_dirs[0])
            self.update_logger.info(f"Source extracted to {source_dir}")

            return {
                "success": True,
                "source_dir": source_dir,
                "temp_dir": temp_dir
            }

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
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

            # Same as _rollback_binary_program: a unit that will not stop makes
            # the restore unverifiable, so it is not claimed.
            if not await self._stop_service(config["service_name"]):
                self.update_logger.error("shairport-sync did not stop, cannot restore its binary")
                return False

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

            if service_was_active and not await self._start_service(config["service_name"]):
                self.update_logger.error("shairport-sync binary restored but the service did not start")
                return False

            self.update_logger.info(f"shairport-sync rollback completed (service {'restarted' if service_was_active else 'left stopped'})")
            return True

        except Exception as e:
            self.update_logger.error(f"shairport-sync rollback failed: {e}")
            return False

    # === QOBUZ-PROXY (pip venv upgrade + source patch) ===

    async def _run_local(self, *args, timeout: int = 120) -> tuple[bool, str]:
        """Run an unprivileged command (as the milo user) and capture output.

        Used for venv operations (pip, the volume patch, cp/mv/rm) that write
        only inside the milo-owned /var/lib/milo/qobuz tree — no sudo needed,
        unlike _run_deploy.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                return False, stderr.decode().strip() or stdout.decode().strip()
            return True, stdout.decode().strip()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"Timed out after {timeout}s"
        except Exception as e:
            return False, str(e)

    async def _update_qobuz_proxy(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates the qobuz-proxy sidecar (a pip package installed from a git tag).

        Unlike the binary programs, the "install" is a pip upgrade inside the
        milo-owned venv plus re-applying our vendored source patches (volume
        policy + status progress) — both unprivileged (the backend runs as
        milo). The whole venv is backed up first so any failure rolls back to
        the working version; the fragile part is the patching, which fails loud
        if an upstream release moved its anchors. config.yaml and
        credentials.json are left untouched.
        """
        config = self.programs["qobuz-proxy"]
        latest_version = status["latest"]["version"]
        # Use the exact upstream tag for the pip ref (not a reconstructed
        # "v{version}") so a tag that isn't simply "v" + semver still resolves.
        tag_name = status["latest"]["tag_name"]
        venv = config["venv_path"]
        service = config["service_name"]

        service_was_active = await self._is_service_active(service)
        self.update_logger.info(f"Service {service} was {'active' if service_was_active else 'inactive'} before update")

        # Nothing to roll back to before phase 1 completes, so a failure above
        # that line reports no rollback outcome at all.
        backed_up = False

        try:
            # Phase 1: Back up the venv (10%)
            backup_result = await self._backup_qobuz_venv(config)
            if not backup_result["success"]:
                return backup_result
            backed_up = True

            # Phase 2: Stop the sidecar before touching the venv (50%).
            # Normally already stopped (the route deactivates Qobuz pre-update),
            # so this is defensive against a file lock (Restart=always).
            if service_was_active:
                if not await self._stop_service(service):
                    rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                    return self._failure_after_rollback("Failed to stop service.", rolled_back)

            # Phase 3: pip upgrade to the pinned tag (70%) — unprivileged
            pip_ok, pip_out = await self._run_local(
                f"{venv}/bin/pip", "install", "--upgrade",
                f"qobuz-proxy[local] @ git+{QOBUZ_PROXY_REPO_URL}@{tag_name}",
                timeout=600
            )
            if not pip_ok:
                self.update_logger.error(f"qobuz-proxy pip upgrade failed: {pip_out}")
                rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                return self._failure_after_rollback(f"pip install failed: {pip_out}.", rolled_back)

            # Phase 4: re-apply our vendored patches (85%) — the fragile step
            patch_ok, patch_out = await self._run_local(
                f"{venv}/bin/python", QOBUZ_PROXY_PATCHES_SCRIPT, timeout=60
            )
            if not patch_ok:
                self.update_logger.error(f"qobuz-proxy patches failed: {patch_out}")
                rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                return self._failure_after_rollback(
                    f"Patching failed (upstream sources may have changed): {patch_out}.", rolled_back
                )

            # Phase 5: verify import + version (95%)
            verify_result = await self._verify_qobuz_update(config, latest_version)
            if not verify_result["success"]:
                rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                return self._failure_after_rollback(f"{verify_result['error']}.", rolled_back)

            # Restart only if it was active; otherwise the sidecar stays stopped
            # and starts on demand when the user next selects Qobuz.
            if service_was_active and not await self._start_service(service):
                # No rollback: the venv is upgraded and verified — it is the
                # restart that did not happen, and undoing a good update over
                # that would be worse. Same call as the refused reboot in
                # _update_milo_app, and the backup is kept for the same reason.
                self.update_logger.error("qobuz-proxy updated but its service did not restart")
                return {
                    "success": False,
                    "error": "qobuz-proxy updated but its service did not restart. Restart it manually.",
                }

            await self._cleanup_qobuz_backup(config)

            return {"success": True}

        except Exception as e:
            self.update_logger.error(f"qobuz-proxy update failed: {e}")
            if not backed_up:
                return {"success": False, "error": str(e)}
            rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
            return self._failure_after_rollback(f"{e}.", rolled_back)

    async def _backup_qobuz_venv(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Copies the whole venv to backup_path/venv so a failed upgrade can roll back."""
        try:
            backup_venv = Path(config["backup_path"]) / "venv"
            if backup_venv.exists():
                ok, out = await self._run_local("rm", "-rf", str(backup_venv), timeout=120)
                if not ok:
                    return {"success": False, "error": f"Backup cleanup failed: {out}"}

            Path(config["backup_path"]).mkdir(parents=True, exist_ok=True)
            # cp -a preserves the venv's symlinks/permissions; restoring to the
            # same absolute path keeps the interpreter shebangs valid.
            ok, out = await self._run_local("cp", "-a", config["venv_path"], str(backup_venv), timeout=300)
            if not ok:
                return {"success": False, "error": f"venv backup failed: {out}"}

            return {"success": True, "backup_dir": config["backup_path"]}

        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    async def _rollback_qobuz_venv(self, config: Dict[str, Any], service_was_active: bool = False) -> bool:
        """Restores the backed-up venv (same path → shebangs stay valid)."""
        try:
            backup_venv = Path(config["backup_path"]) / "venv"
            if not backup_venv.exists():
                self.update_logger.error("No qobuz-proxy venv backup found for rollback")
                return False

            # Refusing to touch the venv of a unit that would not stop is the
            # point: the rm below would pull it out from under a running
            # process, and the start further down would report the *new* code
            # still up as a successful restore.
            if not await self._stop_service(config["service_name"]):
                self.update_logger.error("qobuz-proxy did not stop, cannot restore its venv")
                return False

            ok, out = await self._run_local("rm", "-rf", config["venv_path"], timeout=120)
            if not ok:
                self.update_logger.error(f"qobuz-proxy rollback (rm) failed: {out}")
                return False

            ok, out = await self._run_local("mv", str(backup_venv), config["venv_path"], timeout=120)
            if not ok:
                self.update_logger.error(f"qobuz-proxy rollback (mv) failed: {out}")
                return False

            if service_was_active and not await self._start_service(config["service_name"]):
                self.update_logger.error("qobuz-proxy venv restored but the service did not start")
                return False

            self.update_logger.info(f"qobuz-proxy rollback completed (service {'restarted' if service_was_active else 'left stopped'})")
            return True

        except Exception as e:
            self.update_logger.error(f"qobuz-proxy rollback failed: {e}")
            return False

    async def _cleanup_qobuz_backup(self, config: Dict[str, Any]) -> None:
        """Removes the venv backup after a successful update (best-effort)."""
        try:
            backup_venv = Path(config["backup_path"]) / "venv"
            if backup_venv.exists():
                await self._run_local("rm", "-rf", str(backup_venv), timeout=120)
        except Exception as e:
            self.update_logger.warning(f"Failed to clean qobuz-proxy backup: {e}")

    async def _verify_qobuz_update(self, config: Dict[str, Any], expected_version: str) -> Dict[str, Any]:
        """Verifies the upgraded venv imports the patched stream and reports the expected version."""
        ok, out = await self._run_local(
            f"{config['venv_path']}/bin/python", "-c",
            "import qobuz_proxy.backends.local.stream, importlib.metadata as m; "
            "print(m.version('qobuz-proxy'))",
            timeout=60
        )
        if not ok:
            return {"success": False, "error": f"Verification import failed: {out}"}

        installed = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if installed != expected_version:
            return {"success": False, "error": f"Version mismatch after update: {installed!r} != {expected_version!r}"}

        return {"success": True}


    # === UTILITY METHODS ===


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
        debian_codename = await self._get_debian_codename()

        # Determine package name according to component
        if component_key == "snapserver":
            package_name = f"snapserver_{version}-1_arm64_{debian_codename}.deb"
        elif component_key == "snapclient":
            package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
        else:
            return {"success": False, "error": f"Unknown component: {component_key}"}

        url = f"https://github.com/badaix/snapcast/releases/download/v{version}/{package_name}"

        self.update_logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")

        # Created only once the component is known and only the caller that gets
        # the success dict is left to clean it up — the temp dir is handed back in
        # `temp_dir` and released by _cleanup_temp_files. Every failure exit below
        # removes it here, as _download_binary_program already does.
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        try:
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        shutil.rmtree(temp_dir, ignore_errors=True)
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
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}


    async def _install_deb_package(self, deb_path: str) -> Dict[str, Any]:
        """Installs a .deb package via milo-deploy-update (dpkg + apt-get -f)."""
        self.update_logger.info(f"Installing {Path(deb_path).name}...")
        success, output = await self._run_deploy("install-deb", deb_path, timeout=300)
        if not success:
            return {"success": False, "error": f"Package installation failed: {output}"}
        self.update_logger.info("Package installed successfully")
        return {"success": True}

    # Service control delegates to the central SystemdServiceManager — one
    # implementation of the privileged `sudo systemctl` gesture (see invariant #1).
    async def _is_service_active(self, service_name: str) -> bool:
        """Checks if a systemd service is currently active."""
        return await self._systemd.is_active(service_name)

    async def _stop_service(self, service_name: str) -> bool:
        """Stops a systemd service."""
        return await self._systemd.stop(service_name)

    async def _start_service(self, service_name: str) -> bool:
        """Starts a systemd service."""
        return await self._systemd.start(service_name)

    async def _restart_service(self, service_name: str) -> bool:
        """Restarts a systemd service."""
        return await self._systemd.restart(service_name)


    async def _cleanup_temp_files(self, temp_dir: Optional[str]) -> None:
        """Cleans up temporary files"""
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self.update_logger.warning(f"Failed to cleanup {temp_dir}: {e}")

    async def can_update_program(self, program_key: str) -> Dict[str, Any]:
        """Checks if a program can be updated"""
        if program_key not in self.programs:
            return {"can_update": False, "reason": "Update not supported"}

        # Verify the deploy wrapper is reachable via sudo NOPASSWD
        success, _ = await self._run_deploy("check", timeout=5)
        if not success:
            return {"can_update": False, "reason": "Deploy wrapper not accessible"}

        # Check that an update is available
        status = await self.get_program_full_status(program_key)
        if not status.get("update_available"):
            return {"can_update": False, "reason": "No update available"}

        return {"can_update": True, "available_version": status["latest"]["version"]}
