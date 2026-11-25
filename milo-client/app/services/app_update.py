"""
App update service for Milo Client.

Handles deploying app updates pushed from the main Milo server, as a trial the
new version has to survive before what it replaced is dropped:
- Wait for dpkg's lock, finish a dpkg run a power cut interrupted
- Extract the tarball, open the trial (the identity it replaces is kept)
- Keep the venv when requirements.lock changed, then swap in app/ and configs/
- Install Python dependencies (only when they changed), import the new app
- Deploy system/rootfs files, write the identity files, schedule the restart —
  or the reboot a security fix waits for, when the room is silent

The trial is closed by `milo-client-update-guard`: `commit` from the new
process once it has held HOLD_S, a rollback from the unit's own start when the
new version keeps failing, or `restore` from here when a step fails after the
swap — one implementation of the rollback, run from either side.
"""
import asyncio
import contextlib
import json
import os
import shutil
import tarfile
import tempfile
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

import dpkg_lock
from atomic_file import write_atomic

REPO_DIR = Path("/home/milo-client/repo/milo-client")
# A symlink on the image (milo-first-boot); the copy kept for a rollback sits
# beside the directory it resolves to.
VENV_DIR = Path("/home/milo-client/venv")
STATE_DIR = Path("/var/lib/milo-client")
DEPLOY_SCRIPT = "/usr/local/bin/milo-client-deploy-update"
GUARD = "/usr/local/bin/milo-client-update-guard"
FINISH_DPKG = "/usr/local/bin/milo-client-finish-dpkg"
# What the helper answers when another process took dpkg's lock first.
FINISH_DPKG_BUSY = 75

# unattended-upgrades installs the security fixes of Debian-Security on this
# unit, and never reboots it (/etc/apt/apt.conf.d/52milo-unattended-upgrades).
# This file is left by a package's hook, when what it installed takes effect
# only at the next boot. Rare here: the kernel hook unattended-upgrades ships
# fires for a kernel, and Raspberry Pi OS's kernels come from its own archive,
# which that rule leaves out.
REBOOT_REQUIRED = Path("/var/run/reboot-required")
# How long a push waits for dpkg's lock before it is refused. Well under the
# server's budget for the POST (satellite.py, APP_PUSH_TIMEOUT_S), so that a
# refusal reaches it as an answer rather than a silence it would have to wait
# out: held by backend/tests/architecture/test_satellite_app_ceiling.py.
DPKG_LOCK_WAIT_S = 60

# Two files because a satellite answers two different questions. VERSION_NAME
# is the version string the server showed for itself when it pushed — what the
# update screen displays, in the same numbering as the server's own row.
# PAYLOAD_NAME fingerprints the `milo-client/` tree that actually landed here,
# and is what the server compares to decide whether this unit needs a push at
# all: the two move independently, since most releases never touch this
# directory.
VERSION_NAME = "app-version"
PAYLOAD_NAME = "app-payload"
# The trial's state, opened here and closed by the guard — an updater of one
# release hands over to the guard of the next, so these never move. Held equal
# to the guard by backend/tests/architecture/test_satellite_update_trial.py.
TRIAL_NAME = "update-trial"
ATTEMPTS_NAME = "update-trial.attempts"
LAST_UPDATE_NAME = "last-update.json"
OLD_SUFFIX = ".old"
STAGING_SUFFIX = ".new"
DISCARD_SUFFIX = ".discard"
# The trees this service swaps in itself; system/ and rootfs/ are the deploy
# script's. Together they are what the server's payload fingerprint covers —
# held equal by backend/tests/architecture/test_rootfs_deployment.py, since a
# fingerprinted tree nothing installs is a push that restarts a speaker for
# nothing.
SYNCED_TREES = ("app", "configs")
# What pip installs into the venv, under app/: compiled from requirements.txt,
# every package pinned and every file named by its sha256.
PYTHON_LOCK = "requirements.lock"

# How long a new version must stay up after READY=1 before it is declared
# healthy and what it replaced is deleted. The server's wait for a verdict is
# bounded above this plus the guard's worst case.
HOLD_S = 120
IMPORT_TIMEOUT_S = 20
GUARD_TIMEOUT_S = 60
# A commit that failed (a full card, an SD stall past GUARD_TIMEOUT_S) is tried
# again while the trial is open: left open over a version that held, it would
# refuse every push and roll that version back two restarts later.
COMMIT_RETRY_S = 60


def state_path(name: str) -> Path:
    return STATE_DIR / name


def read_identity(name: str) -> Optional[str]:
    """One of the two identity files, or None when it is missing or empty.

    None on a satellite that has never been pushed is the truth: it runs
    whatever the image gave it.
    """
    path = state_path(name)
    try:
        if path.exists():
            value = path.read_text().strip()
            return value if value else None
        return None
    except Exception as e:
        logging.getLogger(__name__).error(f"Error reading {path}: {e}")
        return None


def trial_payload() -> Optional[str]:
    """The payload on trial, or None when no trial is open."""
    try:
        lines = state_path(TRIAL_NAME).read_text().splitlines()
    except FileNotFoundError:
        return None
    return lines[0].strip() if lines else ""


def read_last_update() -> Optional[dict]:
    """The guard's record of how the last trial ended, or None before any."""
    path = state_path(LAST_UPDATE_NAME)
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).error(f"Error reading {path}: {e}")
        return None
    return record if isinstance(record, dict) else None


def _venv_previous() -> Path:
    real = Path(os.path.realpath(VENV_DIR))
    return real.with_name(real.name + OLD_SUFFIX)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except FileNotFoundError:
        return False


def _clear_leftovers() -> None:
    """What a cut commit, or a rollback run from the unit's start, left behind.

    Only ever called with no trial open, so nothing here is needed; cleared
    before a new trial opens, so its rollback cannot put back a stale tree.
    One that survives the clear refuses the update: the trial would otherwise
    keep it as the tree to go back to, over the live one it moves aside.
    """
    leftovers = [
        REPO_DIR / f"{name}{suffix}"
        for name in SYNCED_TREES
        for suffix in (OLD_SUFFIX, STAGING_SUFFIX, DISCARD_SUFFIX)
    ]
    real = Path(os.path.realpath(VENV_DIR))
    previous = _venv_previous()
    leftovers += [
        previous,
        previous.with_name(previous.name + ".tmp"),
        real.with_name(real.name + DISCARD_SUFFIX),
    ]
    for path in leftovers:
        shutil.rmtree(path, ignore_errors=True)
    remaining = [str(path) for path in leftovers if os.path.lexists(path)]
    if remaining:
        raise RuntimeError(f"An earlier update left what cannot be removed: {', '.join(remaining)}")


def _runs_trials(extracted_root: Path) -> bool:
    """Whether the pushed tree closes the trial this updater opens: it ships
    the guard and its unit runs it. A release from before the guard (a
    retracted one, pushed back) would leave the trial open for good."""
    name = Path(GUARD).name
    unit = extracted_root / "system" / "milo-client.service"
    try:
        wired = f"{name} start" in unit.read_text()
    except OSError:
        return False
    return wired and (extracted_root / "rootfs" / "usr" / "local" / "bin" / name).is_file()


def _guard_env() -> dict:
    """The guard's paths, from this module's: the rollback it runs is this update's."""
    return {
        **os.environ,
        "MILO_CLIENT_REPO_DIR": str(REPO_DIR),
        "MILO_CLIENT_STATE_DIR": str(STATE_DIR),
        "MILO_CLIENT_VENV": str(VENV_DIR),
    }


def _last_line(output: bytes) -> str:
    lines = [line.strip() for line in output.decode(errors="replace").splitlines() if line.strip()]
    return lines[-1] if lines else ""


class AppUpdateService:
    """Service for deploying app updates pushed from the main server."""

    def __init__(self, room_is_silent: Optional[Callable[[], Awaitable[Optional[bool]]]] = None):
        """`room_is_silent` answers whether nothing plays in this room (True,
        False, or None when it cannot tell); without it a push never reboots."""
        self.logger = logging.getLogger(f"{__name__}.AppUpdateService")
        self._update_in_progress = False
        self._room_is_silent = room_is_silent

    @property
    def update_in_progress(self) -> bool:
        return self._update_in_progress

    async def deploy_update(self, tarball_path: str, payload: str, version: str = "") -> dict:
        """Deploys an app update from a tarball, as a trial.

        `payload` fingerprints the tree being deployed; `version` is what the
        server shows for itself. Both are decided on the server, which is the
        only side that holds a git checkout — a satellite computing either of
        them itself is how a fleet comes to disagree about what it runs.

        Steps:
        1. Wait for dpkg's lock, then finish an interrupted dpkg run; a lock
           still held refuses the update before anything is touched
        2. Extract tarball to temp dir (with security validation)
        3. Open the trial: keep the identity, then write update-trial
        4. Keep the venv if requirements.lock changed; swap in app/ and configs/
           (staged, skip __pycache__), keeping the trees they replace
        5. pip install the lock, only if it changed
        6. Import the new app with the venv's python
        7. Run sudo deploy script for system/rootfs files
        8. Write the identity files (and, for a tree that runs no trial,
           close this one at once)
        9. Schedule service restart with delay — a reboot instead when a
           security fix waits for one and the room is silent

        Any failure past step 3 is put back by the guard before this answers,
        so a refused update never cuts the room. Nothing here deletes what was
        kept: the new process does, through the guard, once it has held.
        """
        refusal = None
        on_trial = trial_payload()
        if self._update_in_progress:
            refusal = {"success": False, "conflict": True, "error": "Update already in progress"}
        elif on_trial is not None:
            refusal = {"success": False, "conflict": True,
                       "error": f"An update to {on_trial} is still on trial"}
        elif not payload.strip() or any(c.isspace() for c in payload.strip()):
            # One line of update-trial, read by the guard.
            refusal = {"success": False, "error": "Invalid payload"}
        if refusal:
            with contextlib.suppress(OSError):
                os.unlink(tarball_path)
            return refusal
        payload = payload.strip()

        temp_dir = None
        trial_open = False
        try:
            self._update_in_progress = True
            self.logger.info(
                f"Starting app update deployment (payload: {payload}, version: {version or 'unknown'})"
            )

            # 1. dpkg first: nothing is extracted or installed under a dpkg run.
            busy = await self._settle_dpkg()
            if busy:
                return {"success": False, "conflict": True, "error": busy}

            # 2. Extract tarball to temp dir
            temp_dir = tempfile.mkdtemp(prefix="milo-client-update-")
            self.logger.info(f"Extracting tarball to {temp_dir}")

            await self._extract_tarball(tarball_path, temp_dir)

            # Validate extracted content
            extracted_root = Path(temp_dir) / "milo-client"
            if not extracted_root.is_dir():
                return {"success": False, "error": "Invalid tarball: missing milo-client/ directory"}

            runs_trials = _runs_trials(extracted_root)
            new_requirements = extracted_root / "app" / PYTHON_LOCK
            requirements_changed = new_requirements.is_file() and not _same_file(
                new_requirements, REPO_DIR / "app" / PYTHON_LOCK
            )

            # 3. Open the trial
            await self._run_blocking(_clear_leftovers)
            await self._run_blocking(self._open_trial, payload)
            trial_open = True

            # 4. Keep the venv, then swap in app/ and configs/
            if requirements_changed:
                await self._keep_venv()
            swapped = []
            for name in SYNCED_TREES:
                extracted_tree = extracted_root / name
                if extracted_tree.is_dir():
                    await self._sync_tree(extracted_tree, name)
                    swapped.append(name)
                else:
                    self.logger.warning(f"No {name}/ directory in tarball, skipping its sync")
            await self._run_blocking(os.sync)

            # 5. Install Python dependencies
            if requirements_changed:
                await self._install_dependencies(REPO_DIR / "app" / PYTHON_LOCK)

            # 6. The cheapest failure to catch is the one that never restarts:
            # an import that fails here would crash the new process on start.
            if "app" in swapped:
                await self._import_new_app()

            # 7. Run deploy script for system/rootfs files, after pip and the
            # import: the rollback puts back app/, configs/ and the venv, never
            # the units, sudoers and helpers this installs as root, so only the
            # identity write may still fail behind it.
            if (extracted_root / "system").is_dir() or (extracted_root / "rootfs").is_dir():
                await self._deploy_system_files(temp_dir)

            # 8. Write the identity files. They name the code the *next* process
            # runs: this one keeps reporting what it read when it started, so
            # nothing written here is announced unless the restart lands.
            def _write_identity():
                write_atomic(state_path(VERSION_NAME), version)
                write_atomic(state_path(PAYLOAD_NAME), payload)
                os.sync()
            await self._run_blocking(_write_identity)
            self.logger.info(f"Identity updated: payload {payload}, version {version or 'unknown'}")

            # A tree that never commits gets the update the way it was applied
            # before trials: what it replaced is dropped now, before the restart.
            if not runs_trials:
                self.logger.warning(f"{payload} runs no update trial: applied without one")
                if not await self._guard("commit"):
                    raise RuntimeError("The trial of a version that runs none could not be closed")

            # 9. Schedule the restart, or the reboot (2s delay so HTTP response
            # is sent first)
            asyncio.get_event_loop().call_later(2, lambda: asyncio.ensure_future(self._restart_or_reboot()))

            return {"success": True, "version": version}

        except Exception as e:
            error = str(e) or type(e).__name__
            self.logger.error(f"App update deployment failed: {error}")
            if trial_open:
                # One line: it is what the satellite's row shows, and a pip
                # failure is pages of resolver output.
                await self._restore(_last_line(error.encode()))
            return {"success": False, "error": error}

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

    async def commit_trial(self, running_payload: Optional[str]) -> None:
        """Ends the trial of the version this process runs, once it has held.

        Only that trial: a process that took an update while its own hold was
        still counting must not commit a payload that has not run yet. Tried
        again every COMMIT_RETRY_S until it lands.
        """
        while True:
            on_trial = trial_payload()
            if on_trial is None:
                return
            if on_trial != running_payload or self._update_in_progress:
                self.logger.info(f"Not committing {on_trial}: this process runs {running_payload}")
                return
            if await self._guard("commit"):
                return
            await asyncio.sleep(COMMIT_RETRY_S)

    async def _settle_dpkg(self) -> Optional[str]:
        """Waits for dpkg's lock, then finishes a dpkg run a power cut cut short.

        unattended-upgrades holds the lock for its whole run, at a time apt's
        timers pick. A push that lands meanwhile waits for it, DPKG_LOCK_WAIT_S
        at most, and is refused past that before anything is touched: the
        refusal names the holder, and a later push finds dpkg free. Then
        `dpkg --configure -a`, a no-op on a healthy unit.

        Returns the refusal, or None to go on. The helper failing for any other
        reason is logged and the update goes on: the push is this satellite's
        only repair path, and the app it installs needs nothing from dpkg.
        """
        held_by = await dpkg_lock.wait_until_free(DPKG_LOCK_WAIT_S)
        if held_by:
            return self._dpkg_busy(f"held by {held_by} after {DPKG_LOCK_WAIT_S} s")
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", FINISH_DPKG,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as e:
            self.logger.error(f"dpkg could not be finished, updating anyway: {type(e).__name__}: {e}")
            return None
        if proc.returncode == FINISH_DPKG_BUSY:
            return self._dpkg_busy(_last_line(stdout) or "its lock was taken")
        if proc.returncode != 0:
            self.logger.error(
                "dpkg could not finish what was interrupted, updating anyway: "
                f"{_last_line(stderr) or f'exit code {proc.returncode}'}"
            )
            return None
        for line in stdout.decode(errors="replace").splitlines():
            if line.strip():
                self.logger.info(f"dpkg: {line.strip()}")
        return None

    def _dpkg_busy(self, detail: str) -> str:
        refusal = f"dpkg is busy ({detail}): nothing was installed; push again once it is done"
        self.logger.warning(f"Update refused: {refusal}")
        return refusal

    async def _restart_or_reboot(self) -> None:
        """Ends a push: the reboot a security fix waits for when the room is
        silent, the app's restart otherwise.

        unattended-upgrades never reboots, so a fix whose package left
        REBOOT_REQUIRED takes effect here: at a push, which restarts the room's
        audio anyway — only when nothing plays, since a reboot takes it away
        for a minute rather than a few seconds. The new version then starts its
        trial at boot, as it would at a restart. A refused reboot restarts the
        app instead, and the fix waits for the next push.

        The restart is never skipped: this runs detached once the push has
        answered, and without it the old process would keep running with the
        trial open, every later push refused as "still on trial".
        """
        try:
            due = await self._reboot_due()
        except Exception as e:
            self.logger.error(
                f"Could not tell whether a reboot is due, restarting the app: {type(e).__name__}: {e}"
            )
            due = False
        if due and await self._reboot():
            return
        await self._restart_service()

    async def _reboot_due(self) -> bool:
        """Whether this push ends in a reboot: a fix waits for one, no dpkg
        runs (a reboot under it is the interrupted dpkg the next push would
        have to finish), and the room is known to be silent."""
        if not await self._run_blocking(REBOOT_REQUIRED.exists):
            return False
        held_by = await self._run_blocking(dpkg_lock.holder)
        if held_by:
            reason = f"{held_by} holds dpkg"
        else:
            silent = None
            if self._room_is_silent is not None:
                try:
                    silent = await self._room_is_silent()
                except Exception as e:
                    self.logger.warning(f"Could not tell whether the room plays: {type(e).__name__}: {e}")
            if silent is True:
                self.logger.info(f"{REBOOT_REQUIRED} is there and the room is silent: rebooting")
                return True
            reason = "the room is playing" if silent is False else "whether the room plays is unknown"
        self.logger.info(f"{REBOOT_REQUIRED} is there, but {reason}: restarting the app only")
        return False

    async def _reboot(self) -> bool:
        """`systemctl reboot`; False when it was refused.

        It answers 0 once the reboot is queued, or dies with this process when
        the shutdown reaches the cgroup first: both are a reboot under way.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "reboot",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as e:
            error = f"{type(e).__name__}: {e}"
        else:
            if proc.returncode is None or proc.returncode <= 0:
                return True
            error = stderr.decode().strip() or f"Exit code {proc.returncode}"
        self.logger.error(f"Reboot refused ({error}): restarting the app instead")
        return False

    @staticmethod
    async def _run_blocking(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    @staticmethod
    def _open_trial(payload: str) -> None:
        # The identity is kept before the trial file exists, so a trial on disk
        # always has one to go back to. A missing file is kept as an empty one,
        # which reads as "never pushed" once put back.
        for name in (VERSION_NAME, PAYLOAD_NAME):
            live = state_path(name)
            write_atomic(state_path(name + OLD_SUFFIX), live.read_text() if live.exists() else "")
        write_atomic(state_path(ATTEMPTS_NAME), "0\n")
        write_atomic(state_path(TRIAL_NAME), payload + "\n")

    async def _keep_venv(self) -> None:
        """Copies the venv aside before pip changes it.

        A copy, not a move: pip installs into the live path, which every script
        in the venv names in its shebang. The copy is renamed into its final
        name only once complete, so the guard never puts back half a venv.
        """
        previous = _venv_previous()
        partial = previous.with_name(previous.name + ".tmp")

        def _copy():
            shutil.rmtree(partial, ignore_errors=True)
            shutil.rmtree(previous, ignore_errors=True)
            shutil.copytree(os.path.realpath(VENV_DIR), partial, symlinks=True)
            os.rename(partial, previous)

        try:
            await self._run_blocking(_copy)
        except OSError:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        self.logger.info(f"Previous venv kept as {previous}")

    async def _import_new_app(self) -> None:
        """Imports the new app/ with the venv's python, as its start will."""
        app_dir = REPO_DIR / "app"
        proc = await asyncio.create_subprocess_exec(
            str(VENV_DIR / "bin" / "python3"), "-c", "import main",
            cwd=str(app_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), IMPORT_TIMEOUT_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise RuntimeError(f"The new app did not import within {IMPORT_TIMEOUT_S} s")
        if proc.returncode != 0:
            raise RuntimeError(
                f"The new app does not import: {_last_line(stderr) or f'exit code {proc.returncode}'}"
            )
        self.logger.info("New app imports cleanly")

    async def _guard(self, command: str, *args: str) -> bool:
        """Runs the update guard (no sudo: it is the app account's own state)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                GUARD, command, *args,
                env=_guard_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), GUARD_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError) as e:
            self.logger.error(f"Update guard {command} failed: {type(e).__name__}: {e}")
            return False
        if proc.returncode != 0:
            self.logger.error(
                f"Update guard {command} failed: {_last_line(stderr) or f'exit code {proc.returncode}'}"
            )
            return False
        return True

    async def _restore(self, detail: str) -> None:
        """Puts back the version the trial replaced, before this process answers."""
        if trial_payload() is None:
            # Closed already: a version that runs no trial was applied without one.
            return
        restored = await self._guard("restore", detail)
        # The unit's WorkingDirectory is app/, which the guard just renamed
        # back: re-anchor on the tree now behind that path.
        with contextlib.suppress(OSError):
            os.chdir(REPO_DIR / "app")
        if restored:
            self.logger.warning("Update failed after the swap: previous version restored")

    async def _extract_tarball(self, tarball_path: str, dest_dir: str):
        """Extracts tarball with security validation (no path traversal).

        The upload this unpacks comes from this unit's server only: `POST
        /app/update` refuses, before anything is saved, a request that does not
        carry the secret the satellite pinned (`server_secret.py`). It is still
        extracted by the account that owns the app and the deploy sudo wrapper,
        so a tarball is never trusted for its sender.

        Two layers, and both are needed. The name pass runs over every member
        before a single one is written, so a refused tarball leaves no partial
        tree behind — and it reads `linkname` as well as `name`, because a member
        whose own name is clean can still be a symlink resolving outside the
        destination, which a later member then writes *through*. The `data`
        filter is the backstop for what a path cannot express: setuid bits,
        device nodes, and links Python resolves against members it has already
        extracted. Without an explicit filter Python 3.13 extracts fully trusted.
        """
        def _do_extract():
            with tarfile.open(tarball_path, "r:gz") as tar:
                for member in tar.getmembers():
                    for path in (member.name, member.linkname):
                        if path.startswith("/") or ".." in path:
                            raise ValueError(f"Unsafe path in tarball: {member.name}")
                tar.extractall(path=dest_dir, filter="data")

        await asyncio.get_event_loop().run_in_executor(None, _do_extract)

    async def _sync_tree(self, source: Path, name: str):
        """Stages one new tree (app/, configs/) next to the live one, then swaps it in.

        Copying straight over the live tree means a failure mid-copy leaves a
        half-written app on a machine whose only repair path is the API that app
        serves. Staging first makes the copy — the part that can fail — happen
        while the live tree is untouched, and reduces the window to two renames.
        Both staging dirs sit inside REPO_DIR so the renames never cross a
        filesystem and stay atomic.

        The tree that was live stays behind as <name>.old for the whole
        trial: the steps after this one can fail, and so can the new version's
        first starts. The guard owns it from here — `commit` drops it, a
        rollback puts it back.
        """
        target = REPO_DIR / name
        staging = REPO_DIR / f"{name}{STAGING_SUFFIX}"
        previous = REPO_DIR / f"{name}{OLD_SUFFIX}"

        def _do_sync():
            # Anything left by a killed run is stale, never a fallback. Not
            # `previous`: cleared before the trial opened, it can only be a kept
            # tree now, and the rename below refuses to replace it.
            shutil.rmtree(staging, ignore_errors=True)

            shutil.copytree(
                source, staging,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )

            if target.exists():
                os.rename(target, previous)
            os.rename(staging, target)

            # The unit's WorkingDirectory is app/, so the running process is
            # now holding the renamed-away inode as its cwd. pip runs in the
            # next step and calls getcwd(); re-anchor now.
            if name == "app":
                os.chdir(target)

        await asyncio.get_event_loop().run_in_executor(None, _do_sync)
        self.logger.info(f"{name}/ synced successfully")

    async def _deploy_system_files(self, temp_dir: str):
        """Runs the sudo deploy script for system and rootfs files."""
        self.logger.info("Deploying system files via sudo wrapper...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", DEPLOY_SCRIPT, temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or stdout.decode().strip()
            raise RuntimeError(f"Deploy script failed: {error_msg}")

        output = stdout.decode().strip()
        self.logger.info(f"System files deployed: {output}")

        # The script compares every file before overwriting it and ends on one
        # line saying what actually moved. Worth its own journal entry: the rest
        # of that output is the same forty "Installed /path" lines every time,
        # and `sat logs` is the only place a satellite says anything at all.
        for line in output.splitlines():
            if line.startswith("Changed"):
                self.logger.info(f"Deploy: {line[0].lower()}{line[1:]}")

    async def _install_dependencies(self, lock_file: Path):
        """Installs the Python dependencies requirements.lock pins.

        The lock, with `--require-hashes`: pip refuses any file the lock does
        not name by digest and any package it does not pin, so a satellite runs
        the versions the release was tested with, not what PyPI served the day
        of the push. requirements.txt holds only the floors the lock is
        compiled from.
        """
        self.logger.info("Installing Python dependencies...")

        proc = await asyncio.create_subprocess_exec(
            str(VENV_DIR / "bin" / "pip3"), "install", "--require-hashes", "-r", str(lock_file),
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
        """Restarts milo-client.service, or puts the previous version back.

        A successful restart kills this process, and systemd kills the whole
        cgroup — this `systemctl` child included — so the *expected* outcome is
        a negative return code, signalled death. Measured on canapé: -15, with
        an empty stderr, 150 ms in. Only a positive code is a restart that did
        not happen: a unit file the deploy step made invalid, a masked unit, a
        policy that no longer grants the verb. Left as it was, the trial would
        stay open over a process still running the previous code, and the next
        start of the unit — a reboot, weeks later — would land in a version
        nothing ever tried. So the trial is rolled back at once: the guard
        records why in last-update.json, which /status reports, and the server
        reads the push as rolled back. Same distinction the reboot route draws.
        """
        self.logger.info("Restarting milo-client.service...")

        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "systemctl", "restart", "milo-client.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
        except OSError as e:
            error_msg = f"{type(e).__name__}: {e}"
        else:
            if proc.returncode is None or proc.returncode <= 0:
                return
            error_msg = stderr.decode().strip() or f"Exit code {proc.returncode}"

        self.logger.error(f"Restart failed, still running the previous code: {error_msg}")
        await self._restore(f"restart refused: {error_msg}")
