# backend/core/updates/update.py
"""
Update service - installs a new version of each program Milo ships.
"""
import asyncio
import aiohttp
import aiofiles
import contextlib
import hashlib
import json
import shutil
import tempfile
import logging
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from backend.core.updates.version import VersionService
from backend.core.updates.data_snapshot import take_snapshot
from backend.core.updates import dpkg_lock
from backend.core.updates.helpers import (
    fsync_directory,
    requirements_moved_up,
    write_json_durably,
)
from backend.core.updates.milo_journal import (
    ATTEMPTS_NAME,
    COMMITTED_STEP,
    DATA_SNAPSHOT_NAME,
    DISCARD_NAME,
    GIT_LOCKS,
    JOURNAL_NAME,
    KEPT_DIST_NAME,
    KEPT_VENV_NAME,
    KEPT_VENV_PARTIAL_NAME,
    KEY_DETAIL,
    KEY_DISMISSED,
    KEY_FAILURE,
    KEY_FROM,
    KEY_GUARD_RETRY_BOOT,
    KEY_ORIGIN_COMMIT,
    KEY_ORIGIN_TAG,
    KEY_OUTCOME,
    KEY_POST_COMMIT_AT,
    KEY_POST_COMMIT_RUNS,
    KEY_STEP,
    KEY_TARGET_COMMIT,
    KEY_TARGET_TAG,
    KEY_TO,
    KEY_VENV_KEPT,
    LAST_UPDATE_NAME,
    MAX_ATTEMPTS,
    STEPS,
    TRIAL_STEP,
)
from backend.config.constants import DEPLOY_UPDATE_CMD

# qobuz-proxy is a pip package installed from its tag's source archive, checked
# against the manifest's digest first — the same file the image build installs
# (provisioning/qobuz-proxy.sh). Milō's adaptations live in the launcher the
# service runs, which also answers whether a release still offers what they bind
# to.
QOBUZ_ADAPTER = "/usr/local/bin/milo-qobuz"

# What the root helpers read, in a digest's place, as "dependencies.env declares
# none for this file": an install of a version past the manifest.
NO_DIGEST = "-"

def source_archive_url(repo: str, tag: str) -> str:
    """The source archive GitHub serves for a tag — what the manifest's digest
    of a program built or pip-installed from source is taken over."""
    return f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"


def digest_for(entry: Dict[str, Any], version: str, asset: str = "main") -> Optional[str]:
    """The sha256 `dependencies.env` declares for one file `version` of `entry`
    downloads, or None when it declares none.

    Only the validated version has one. Any other is what upstream published
    past the manifest — a `target: upstream` install — and nothing says what
    its files should hash to; the return to the manifest is the validated
    version again, and checked.
    """
    if version != entry.get("validated_version"):
        return None
    return entry["validated_sha256"][asset]

# The shared memory interface shairport-sync and NQPTP must agree on: declared
# by both source trees, and reported by `nqptp -V` as
# "Version: 1.2.4. Shared Memory Interface Version: smi10."
_SMI_DEFINE_RE = re.compile(r"^#define\s+NQPTP_SHM_STRUCTURES_VERSION\s+(\d+)", re.MULTILINE)
_NQPTP_VERSION_RE = re.compile(r"Version: (\S+?)\. Shared Memory Interface Version: smi(\d+)")
CAP_NET_BIND_SERVICE = 10
# NQPTP opens its ports within milliseconds of starting, and exits if it cannot.
NQPTP_SETTLE_S = 1.0

# How long an update that installs through apt waits for apt and dpkg to let go
# — unattended-upgrades' run, apt-daily's refresh of the lists, both at a time
# apt's timers pick — before it gives up with nothing stopped or installed. What
# `milo-deploy-update install-deb` and `sync-system-files` take: dpkg's locks
# for `dpkg --configure -a` and the install, the lists' for their `apt update`.
APT_LOCK_WAIT_S = 300
APT_LOCKS = dpkg_lock.DPKG_LOCKS + (dpkg_lock.APT_LISTS_LOCK,)

# What a Milō update needs on the card beyond the copies it measures: pip's
# downloads into the venv, the fetched packs, the journal.
MILO_UPDATE_MARGIN_BYTES = 256 * 1024 * 1024
MILO_PIP_TIMEOUT_S = 600
# What pip installs into the app venv, relative to the checkout.
PYTHON_LOCK = "requirements.lock"
MILO_IMPORT_CHECK_TIMEOUT_S = 120

# Run by the new release's venv python, -I, with the checkout as argv[1]: every
# backend module the new release will import at start, imported now, while the
# release that runs can still refuse it. main.py itself builds the app, so it
# is only compiled.
_IMPORT_CHECK = """
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
for path in sorted((root / "backend").rglob("*.py")):
    parts = path.relative_to(root).with_suffix("").parts
    if parts[1] == "tests" or parts == ("backend", "main"):
        continue
    importlib.import_module(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
main = root / "backend" / "main.py"
compile(main.read_text(), str(main), "exec")
"""

# Not read from disk yet: the journal and the last outcome are read once, on
# first use, then kept in step with every write this process makes.
_UNREAD = object()

# What a previous update left in the backup directory, emptied when a journal
# opens: a rollback's discard holds a whole venv until then.
_CLEARED_BACKUPS = (
    KEPT_VENV_NAME, KEPT_VENV_PARTIAL_NAME, KEPT_DIST_NAME, DATA_SNAPSHOT_NAME, DISCARD_NAME,
)

# Which boot this is, for what a backend may do once per boot.
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

# The phases the arriving release runs once its trial committed.
_POST_COMMIT_PHASES = ("dependencies", "satellites")

MiloProgressListener = Callable[[Dict[str, Any]], Awaitable[None]]
MiloCompleteListener = Callable[[bool], Awaitable[None]]
SessionEnder = Callable[[str], Awaitable[None]]


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).error(f"Cannot read {path}: {e}")
        return None
    return data if isinstance(data, dict) else None


def _tree_size(root: Path) -> int:
    total = 0
    for directory, _subdirs, files in os.walk(root):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(directory, name)).st_size
    return total


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _boot_id() -> Optional[str]:
    try:
        return Path(BOOT_ID_PATH).read_text().strip() or None
    except OSError:
        return None


def _before_the_trial(journal: dict) -> bool:
    """A journal the guard puts the previous release back from at the next
    start: the install never reached its trial, or it recorded a failure."""
    step = journal.get(KEY_STEP)
    return bool(journal.get(KEY_FAILURE)) or (
        step in STEPS and STEPS.index(step) < STEPS.index(TRIAL_STEP)
    )


class UpdateService(VersionService):
    """Update service - Extends VersionService"""

    def __init__(self, systemd_manager, satellite_update_service, settings_service):
        super().__init__(settings_service)
        self._systemd = systemd_manager
        self._satellites = satellite_update_service
        self.update_logger = logging.getLogger(f"{__name__}.update")

        # The Milō update this process runs (the install, or the post-commit
        # phase), and what it is doing now.
        self._milo_running = False
        self._milo_phase: Optional[Dict[str, Any]] = None
        self._milo_versions: tuple[Optional[str], Optional[str]] = (None, None)
        self._journal = _UNREAD
        self._last_update = _UNREAD
        # A committed journal whose post-commit phase ended in this process
        # without closing it, and an open journal this process let go of — one
        # it gave up handing to the guard, or a trial it could not commit: both
        # stay on disk for the next start, and hold nothing until then.
        self._post_commit_stalled = False
        self._journal_abandoned = False
        self._on_milo_progress: Optional[MiloProgressListener] = None
        self._on_milo_complete: Optional[MiloCompleteListener] = None
        self._end_session_of: Optional[SessionEnder] = None

    # === The Milō update, as the rest of the backend sees it ===

    def set_milo_update_listener(
        self, on_progress: MiloProgressListener, on_complete: MiloCompleteListener,
    ) -> None:
        """Who hears a Milō update move.

        `on_progress(state)` is awaited at every phase change and per
        dependency or satellite, `state` being what `milo_update_state()`
        answers then. `on_complete(success)` is awaited once, when the release
        that arrived has finished its post-commit phase. A listener that raises
        is logged and never stops the update.
        """
        self._on_milo_progress = on_progress
        self._on_milo_complete = on_complete

    def set_session_ender(self, end_session_of: SessionEnder) -> None:
        """What ends the listening session a program's daemon serves.

        `end_session_of(program_key)` is awaited before every program's install
        flow, whoever asked for it — the route, or a Milō update bringing the
        dependency set to its manifest —, and ends the session of the source
        that daemon plays for when it is the active one, as a press on another
        source would. An install stops the daemon: under a session, the sound
        was cut with the source left selected over a restarted daemon, and for
        Navidrome mpv's localhost stream ended as a lost stream, with its
        banner, for an operation the owner asked for.
        """
        self._end_session_of = end_session_of

    @property
    def milo_update_active(self) -> bool:
        """Whether a Milō update holds the unit: this process runs one (the
        install, or the post-commit phase), the new release is on trial, or its
        post-commit phase is still to run.

        Anything that would restart or power off the unit asks this first: a
        reboot in the middle of an install is a restore at the next start, and
        one during the trial counts as a failed start of the new release. A
        journal the guard is to put back holds nothing — a restart is what
        finishes it — and neither does a post-commit phase that ended in this
        process without closing its journal, which the next start runs again,
        nor a trial this process could not commit (`let_go_of_the_trial`).
        Held instead, any of them would refuse the very restart that ends it.
        """
        if self._milo_running:
            return True
        journal = self._milo_journal()
        if journal is None or _before_the_trial(journal):
            return False
        if journal.get(KEY_STEP) == COMMITTED_STEP:
            return not self._post_commit_stalled
        return journal.get(KEY_STEP) == TRIAL_STEP and not self._journal_abandoned

    @property
    def milo_update_holds_installs(self) -> bool:
        """Whether no other install may start now: a Milō update holds the unit
        (`milo_update_active`), or the trial this process let go of is still
        open (`let_go_of_the_trial`).

        Letting go of a trial frees the restart and lifts the update screen,
        nothing else: the dependency set and the satellites move only once a
        trial has committed (`run_post_commit`), and the guard may still put
        the previous release back. Freed here as well, a satellite owed the
        client app at its admission was pushed the uncommitted release's — a
        speaker restarted in every room for a release that may not stay — and
        a local install took the uncommitted manifest's pin.
        """
        if self.milo_update_active:
            return True
        journal = self._milo_journal()
        return journal is not None and journal.get(KEY_STEP) == TRIAL_STEP

    def milo_update_state(self) -> Optional[Dict[str, Any]]:
        """`{"phase", "from_version", "to_version", "item", "index", "total"}`,
        or None when no Milō update is under way.

        Rebuilt from the journal by a process that did not start the update:
        the release on trial says "verify", a committed one "dependencies"
        until its post-commit phase reports more. A journal that never reached
        the trial is one the next start restores, so it reads as "restart".
        None for a journal this process let go of (a post-commit phase that
        could not close it, a guard that did not put it back, a trial that
        could not be committed): the next start takes it up again. Versions
        are the release tags, verbatim.
        """
        if self._milo_phase is not None:
            return dict(self._milo_phase)
        journal = self._milo_journal()
        if journal is None:
            return None
        if _before_the_trial(journal):
            phase = None if self._journal_abandoned else "restart"
        elif journal.get(KEY_STEP) == TRIAL_STEP:
            phase = None if self._journal_abandoned else "verify"
        elif journal.get(KEY_STEP) == COMMITTED_STEP:
            phase = None if self._post_commit_stalled else "dependencies"
        else:
            phase = None
        if phase is None:
            return None
        return self._phase_record(phase, journal.get(KEY_ORIGIN_TAG), journal.get(KEY_TARGET_TAG))

    def last_update_record(self) -> Optional[Dict[str, Any]]:
        """How the last Milō update ended, kept until the next one opens a
        journal: `{"from_version", "to_version", "outcome", "detail", "step",
        "dismissed"}`, or None. `dismissed` is whether its banner was closed,
        on any screen."""
        record = self._last_update_on_disk()
        if record is None:
            return None
        return {
            "from_version": record.get(KEY_FROM),
            "to_version": record.get(KEY_TO),
            "outcome": record.get(KEY_OUTCOME),
            "detail": record.get(KEY_DETAIL),
            "step": record.get(KEY_STEP),
            "dismissed": record.get(KEY_DISMISSED) is True,
        }

    async def dismiss_last_update(self) -> bool:
        """Close the banner of the last Milō update for every screen; True when
        this call closed it.

        Kept in the record rather than in each browser: the kiosk's private
        profile forgets a dismissal at every restart, and showed the same
        rollback again after each one. Nothing is written while an update runs
        or holds its journal open: it removes the record itself, and a write
        racing that removal would bring the previous outcome back.
        """
        if self._milo_running or self._milo_journal() is not None:
            return False
        await self._reload_milo_state()
        record = self._last_update
        if not isinstance(record, dict) or record.get(KEY_DISMISSED) is True:
            return False
        if self._milo_running or self._milo_journal() is not None:
            return False
        dismissed = {**record, KEY_DISMISSED: True}
        await asyncio.to_thread(write_json_durably, self._milo_state(LAST_UPDATE_NAME), dismissed)
        self._last_update = dismissed
        return True

    async def run_post_commit(self) -> None:
        """Finish a Milō update whose new release held: the dependency set this
        release declares, then the satellites, then close the journal.

        Run by the release that arrived, never by the one that left: before the
        reboot the leaving release installs the app only, so nothing outside
        the checkout has moved if the guard has to put it back. Called once the
        trial committed, and again at every start while the journal still says
        so — both steps leave alone what is already where it should be, so a
        phase cut by a restart resumes. Without a committed journal it does
        nothing.

        Each run is counted in the journal before it begins: a phase that kills
        the backend at the same item every time (an install that runs the
        unit out of memory) is given up after MAX_ATTEMPTS - 1 runs, the journal
        closed as committed with a detail naming where it was cut, instead of
        holding the unit at every start for good.

        A dependency or a satellite that did not move is recorded in the
        outcome's detail, not failed on: the app itself has committed. A phase
        that cannot close its journal (a full card) lets go of the unit and is
        run again at the next start.
        """
        if self._milo_running:
            return
        await self._reload_milo_state()
        journal = self._milo_journal()
        if journal is None or journal.get(KEY_STEP) != COMMITTED_STEP:
            return

        self._milo_running = True
        self._post_commit_stalled = False
        self._milo_versions = (journal.get(KEY_ORIGIN_TAG), journal.get(KEY_TARGET_TAG))
        finished = False
        try:
            runs = journal.get(KEY_POST_COMMIT_RUNS)
            runs = runs if isinstance(runs, int) and runs > 0 else 0
            if runs >= MAX_ATTEMPTS - 1:
                cut = (
                    f"the dependency set and the satellites were cut {runs} times, last during "
                    f"{journal.get(KEY_POST_COMMIT_AT) or 'their start'}: not run again"
                )
                self.update_logger.error(
                    f"Milo update {journal.get(KEY_ORIGIN_TAG)} -> {journal.get(KEY_TARGET_TAG)}: {cut}"
                )
                await self._close_post_commit(journal, cut)
            else:
                await self._write_journal(**{KEY_POST_COMMIT_RUNS: runs + 1})
                await self._close_post_commit(journal, await self._run_post_commit_phase())
            finished = True
        except Exception as e:
            self._post_commit_stalled = True
            self.update_logger.error(
                f"Milo update {journal.get(KEY_ORIGIN_TAG)} -> {journal.get(KEY_TARGET_TAG)}: "
                f"the post-commit phase could not finish ({e}); it runs again at the next start"
            )
        finally:
            self._milo_running = False
            self._milo_phase = None
        await self._notify_milo_complete(finished)

    async def _run_post_commit_phase(self) -> Optional[str]:
        """The dependency set, then the satellites; what stayed behind, for the
        outcome's detail."""
        await self._milo_progress("dependencies")
        dependency_failures = await self._reconcile_dependencies()
        await self._milo_progress("satellites")
        satellite_failures = await self._satellites.push_client_app_to_fleet(
            on_push=lambda name, index, total: self._milo_progress(
                "satellites", item=name, index=index, total=total,
            ),
        )
        left = []
        if dependency_failures:
            left.append(f"dependencies not reconciled: {', '.join(dependency_failures)}")
        if satellite_failures:
            left.append(f"satellites left behind: {', '.join(satellite_failures)}")
        return "; ".join(left) or None

    async def _close_post_commit(self, journal: dict, detail: Optional[str]) -> None:
        record = {
            KEY_FROM: journal.get(KEY_ORIGIN_TAG),
            KEY_TO: journal.get(KEY_TARGET_TAG),
            KEY_OUTCOME: "committed",
            KEY_DETAIL: detail,
            KEY_STEP: COMMITTED_STEP,
        }
        # The outcome first: a phase cut between the two writes is run again at
        # the next start, and only rewrites it.
        await asyncio.to_thread(write_json_durably, self._milo_state(LAST_UPDATE_NAME), record)
        self._last_update = record
        await asyncio.to_thread(self._close_journal)
        self._journal = None
        self.update_logger.info(
            f"Milo update {record[KEY_FROM]} -> {record[KEY_TO]} complete"
            + (f" ({detail})" if detail else "")
        )

    async def hand_stranded_journal_to_the_guard(self) -> None:
        """Restart into the guard when a journal it should have closed is open.

        Called once the backend listens. An install that never reached its
        trial, or one that recorded a failure, is put back by the guard before
        the backend starts; a backend that finds one anyway started over a
        guard that could not finish, on a tree that may be half installed, and
        nothing else would ever act on it. It restarts once per boot so the
        guard runs again. Found a second time in the same boot, the journal is
        let go of instead: the unit runs what started, the update screen lifts
        and the power routes answer, so a restart can still be asked for.
        """
        if self._milo_running:
            return
        await self._reload_milo_state()
        journal = self._milo_journal()
        if journal is None or not _before_the_trial(journal):
            return
        names = f"{journal.get(KEY_ORIGIN_TAG)} -> {journal.get(KEY_TARGET_TAG)}"
        boot = await asyncio.to_thread(_boot_id)
        if boot is not None and journal.get(KEY_GUARD_RETRY_BOOT) != boot:
            try:
                # Recorded first: a restart that cannot be bounded is a loop.
                await self._write_journal(**{KEY_GUARD_RETRY_BOOT: boot})
            except Exception as e:
                self.update_logger.error(f"The Milo update {names} could not be marked for its guard: {e}")
            else:
                # Warning, not error: the guard's own line says how it ended,
                # and an error would raise the backend-error banner over the
                # update screen.
                self.update_logger.warning(
                    f"The Milo update {names} was not put back before this start; "
                    "restarting so the update guard runs again"
                )
                self._milo_versions = (journal.get(KEY_ORIGIN_TAG), journal.get(KEY_TARGET_TAG))
                await self._milo_progress("restart")
                await self._systemd.restart_self(self.programs["milo"]["service_name"])
                return
        self._journal_abandoned = True
        self.update_logger.error(
            f"The Milo update {names} is still open: its guard did not put the previous "
            "release back, a restart into it included. Milo runs what started; restart "
            "Milo to try the guard again."
        )
        await self._notify_milo_complete(False)

    async def let_go_of_the_trial(self, cause: str) -> bool:
        """Stop holding the unit for a release on trial that could not be
        committed (`cause`), and say so, once, at error.

        A guard that cannot write the journal (a card remounted read-only, a
        full one) fails the same way at every attempt, and held, the trial kept
        the update screen up and refused the reboot and the power-off — the
        first useful gestures on a sick card — until the unit was unplugged.
        The journal stays open on disk: the next start is counted as one of the
        trial's, commits it again once it holds, and the guard puts the
        previous release back when the trial's starts run out. Every other
        install stays refused until then (`milo_update_holds_installs`).

        Read from the card first: a commit that landed meanwhile is not a
        trial to give up, and its post-commit phase is the caller's to run.
        Returns whether the trial was let go of.
        """
        await self._reload_milo_state()
        journal = self._milo_journal()
        if journal is None or journal.get(KEY_STEP) != TRIAL_STEP:
            return False
        self._journal_abandoned = True
        self.update_logger.error(
            f"The Milo update {journal.get(KEY_ORIGIN_TAG)} -> {journal.get(KEY_TARGET_TAG)} "
            f"could not be committed ({cause}). The update is let go of and the unit can be "
            "restarted again; at the next start the update guard decides whether "
            f"{journal.get(KEY_TARGET_TAG)} stays or {journal.get(KEY_ORIGIN_TAG)} is put back."
        )
        await self._notify_milo_complete(False)
        return True

    # === Selection and dispatch ===

    @classmethod
    def _select_target(
        cls, status: Dict[str, Any], target: str, version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Point `latest` at the version this update must install.

        Four gestures share one flow and differ only in which release `latest`
        names: the ordinary update (the manifest's version), the trial of what
        upstream published past it — its latest, or a chosen `version` between
        the two — and the return to the manifest that ends such a trial. Every
        install flow below reads its version from the same place, so none of
        them knows which gesture it is serving.

        `None` means there is nothing to install for that target.
        """
        latest = status.get("latest", {})
        release_keys = ("version", "tag_name", "html_url", "published_at")

        if version is not None:
            # Only a release the offer listed, and never the one that runs. No
            # `ahead` check: stepping down from the latest to an earlier trial
            # is the point.
            chosen = next(
                (r for r in latest.get("trials") or [] if r["version"] == version), None
            )
            if chosen is None or version == cls.installed_version(status):
                return None
            return {**status, "latest": {**latest, **{k: chosen[k] for k in release_keys}}}

        if target == "upstream":
            upstream = latest.get("upstream") or {}
            if not upstream.get("ahead"):
                return None
            return {**status, "latest": {**latest, **{k: upstream[k] for k in release_keys}}}

        # The manifest's version. Present as its own block only while an override
        # is active, and then it is the return: `latest` already names the forced
        # version, so the ordinary update path cannot reach it.
        validated = latest.get("validated")
        if validated:
            return {**status, "latest": {**latest, **validated}}
        if not status.get("update_available"):
            return None
        return status

    async def _record_forced_version(self, program_key: str, target: str, status: Dict[str, Any]) -> None:
        """Remember, or forget, that this unit runs a version past the manifest.

        Written only once the install succeeded: a trial that rolled back left
        the previous version in place, so recording it beforehand would claim an
        override the unit does not have. Returning to the manifest drops the
        entry the same way — the two gestures are one write of the whole map.

        Strict, so a write that failed is reported rather than swallowed: the
        unit would be running an unvalidated release with nothing recording it,
        which reads on the row as "up to date" — the exact state this whole
        surface exists to make impossible.
        """
        forced = await self.get_forced_versions()
        updated = dict(forced)
        if target == "upstream":
            updated[program_key] = status["latest"]["version"]
        else:
            updated.pop(program_key, None)
        if updated != forced:
            await self.settings_service.set_setting_strict("updates.forced_versions", updated)

    async def _prune_forced_versions(self) -> None:
        """Drop the overrides the manifest has caught up with.

        Called by the release that arrived, whose `dependencies.env` may now
        declare a version an override was holding: left on disk, the entry
        would hold the unit below the next bump. `get_forced_versions` already
        reads it that way — this is what makes the reading durable.
        """
        stored = await self.settings_service.get_setting("updates.forced_versions")
        live = await self.get_forced_versions()
        if live != stored:
            self.update_logger.info(
                f"Forced versions pruned against the manifest: {stored} -> {live}"
            )
            await self.settings_service.set_setting("updates.forced_versions", live)

    async def update_program(
        self, program_key: str, target: str = "validated", version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatches a program key to the flow that knows how to update it.

        `target` picks the release: "validated" is the version `dependencies.env`
        declares (and, when the unit is off-pin, the return to it), "upstream"
        the one GitHub published past it — its latest, or `version` when given.

        A Milō update answers `pending` when it handed the unit over to a
        restart: its outcome is decided by the release that starts next, and
        read from `last_update_record()`.
        """
        if program_key not in self.programs:
            return {"success": False, "error": f"Update not supported for {program_key}"}
        await self._reload_milo_state()
        if self.milo_update_holds_installs:
            return {"success": False, "error": "A Milo update is in progress; it must finish first"}

        try:
            status = await self.get_program_full_status(program_key)
            selected = self._select_target(status, target, version)
            if selected is None:
                return {"success": False, "error": "No update available"}

            result = await self._dispatch_update(program_key, selected)
            if result.get("success"):
                await self._record_forced_version(program_key, target, selected)
            return result

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dispatch_update(self, program_key: str, status: Dict[str, Any]) -> Dict[str, Any]:
        """Route one program to its install flow. Two callers, one implementation.

        `update_program` is the route's entry point; `_reconcile_dependencies`
        is the Milō update bringing the set to the manifest. Both have already
        decided there is something to install, so neither re-checks here. Both
        end the session the program's daemon serves first, here, so that no
        install stops a daemon under one (`set_session_ender`).
        """
        if program_key == "milo":
            return await self._update_milo_app(status)
        if self._end_session_of is not None:
            await self._end_session_of(program_key)
        if program_key == "multiroom":
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
        """Bring every dependency to the version this release's manifest declares.

        Run by the release that arrived, once its trial committed: the catalog
        it imported at start resolved its own `dependencies.env`, so what it
        compares against is the set it was validated with. Each step keeps its
        own backup and its own rollback, and a step that failed *and restored
        itself* is reported rather than fatal: the unit then runs the new app
        over the old dependency, which is the state every unit is already in
        for any dependency nobody clicked. Never raises — the app has committed,
        and nothing here can undo it.

        A dependency whose target cannot be determined is that dependency's
        failure, never a skip: an offer GitHub would not give (quota, outage)
        leaves nothing to compare against, and skipping it said nothing.

        Returns the keys that failed, for the outcome's detail.
        """
        failed: list[str] = []
        dependencies = [key for key in self.programs if key != "milo"]

        try:
            await self._prune_forced_versions()
        except Exception as e:
            self.update_logger.error(f"updates.forced_versions could not be pruned: {e}")

        to_install: list[tuple[str, Dict[str, Any]]] = []
        for program_key in dependencies:
            try:
                status = await self.get_program_full_status(program_key)
                if status.get("status") == "error":
                    raise RuntimeError(status.get("message"))
                installed = self.installed_version(status)
                # Not installed is the catalog's "no update offered" (a venv or
                # a binary this unit never had), whatever GitHub answers. A
                # version that is there but could not be read (a command that
                # timed out on a cold card, a banner the regex no longer
                # matches) is not that: it reads as off the pin, so it is
                # reinstalled, and said — skipping it in silence left the old
                # daemon under code written for the new one.
                if installed is None:
                    if status["installed"].get("status") != "unreadable":
                        continue
                    self.update_logger.error(
                        f"{program_key}: the installed version could not be read "
                        f"({'; '.join(status['installed'].get('errors') or []) or 'no error recorded'}); "
                        "reinstalling the validated version."
                    )
                # Not `update_available`: the manifest is authoritative in both
                # directions, so a unit sitting *above* what it declares — a
                # yanked release, a manifest deliberately rolled back — is
                # brought down as readily as one sitting below. A version that
                # is off-pin on purpose is the target itself, exactly as
                # `_apply_pin` offers it. Known without GitHub, so a unit
                # already on it is not reported for an offer it does not need.
                forced = (await self.get_forced_versions()).get(program_key)
                if installed == (forced or self.programs[program_key]["validated_version"]):
                    continue
                latest = status["latest"]
                if latest.get("status") != "success":
                    failed.append(program_key)
                    self.update_logger.error(
                        f"{program_key}: the validated version could not be read "
                        f"({latest.get('message')}); not reconciled."
                    )
                    continue
                to_install.append((program_key, status))
            except Exception as e:
                failed.append(program_key)
                self.update_logger.error(f"{program_key}: reconciliation failed: {e}")

        # qobuz-proxy first: the launcher this release installed checks what it
        # binds to in the venv at every start, so Qobuz cannot start until its
        # venv has moved — and a shairport-sync compile ahead of it is minutes.
        to_install.sort(key=lambda entry: entry[0] != "qobuz-proxy")

        for index, (program_key, status) in enumerate(to_install, 1):
            await self._milo_progress(
                "dependencies", item=self.programs[program_key]["name"],
                index=index, total=len(to_install),
            )
            self.update_logger.info(
                f"{program_key}: installing the validated "
                f"{status['latest']['version']} as part of the Milo update"
            )
            try:
                result = await self._dispatch_update(program_key, status)
            except Exception as e:
                result = {"success": False, "error": str(e)}
            if not result.get("success"):
                failed.append(program_key)
                self.update_logger.error(
                    f"{program_key}: could not reach the validated version "
                    f"({result.get('error', 'unknown error')}). The unit keeps "
                    "the version it runs."
                )

        return failed

    # === The Milō update: the leaving release's half ===

    def _milo_state(self, name: str) -> Path:
        return Path(self.programs["milo"]["state_path"]) / name

    def _milo_backup(self, name: str) -> Path:
        return Path(self.programs["milo"]["backup_path"]) / name

    def _milo_journal(self) -> Optional[dict]:
        if self._journal is _UNREAD:
            self._journal = _read_json(self._milo_state(JOURNAL_NAME))
        return self._journal

    def _last_update_on_disk(self) -> Optional[dict]:
        if self._last_update is _UNREAD:
            self._last_update = _read_json(self._milo_state(LAST_UPDATE_NAME))
        return self._last_update

    async def _reload_milo_state(self) -> None:
        """Read the journal and the last outcome again: the guard writes both
        from another process."""
        self._journal = await asyncio.to_thread(_read_json, self._milo_state(JOURNAL_NAME))
        self._last_update = await asyncio.to_thread(_read_json, self._milo_state(LAST_UPDATE_NAME))

    async def _write_journal(self, **changes: Any) -> None:
        """Move the journal forward, on the card before this returns."""
        journal = {**(self._milo_journal() or {}), **changes}
        await asyncio.to_thread(write_json_durably, self._milo_state(JOURNAL_NAME), journal)
        self._journal = journal

    def _close_journal(self) -> None:
        for name in (JOURNAL_NAME, ATTEMPTS_NAME):
            self._milo_state(name).unlink(missing_ok=True)
        fsync_directory(self._milo_state(JOURNAL_NAME).parent)

    def _phase_record(
        self, phase: str, from_version: Optional[str], to_version: Optional[str],
        item: Optional[str] = None, index: Optional[int] = None, total: Optional[int] = None,
    ) -> Dict[str, Any]:
        return {
            "phase": phase, "from_version": from_version, "to_version": to_version,
            "item": item, "index": index, "total": total,
        }

    async def _milo_progress(
        self, phase: str, item: Optional[str] = None,
        index: Optional[int] = None, total: Optional[int] = None,
    ) -> None:
        self._milo_phase = self._phase_record(phase, *self._milo_versions, item, index, total)
        if phase in _POST_COMMIT_PHASES:
            await self._record_post_commit_at(phase if item is None else f"{phase} ({item})")
        if self._on_milo_progress is not None:
            try:
                await self._on_milo_progress(dict(self._milo_phase))
            except Exception as e:
                self.update_logger.error(f"Milo update progress not announced: {e}")

    async def _record_post_commit_at(self, at: str) -> None:
        """Where the post-commit phase is, for the detail of one given up."""
        journal = self._milo_journal()
        if journal is None or journal.get(KEY_STEP) != COMMITTED_STEP:
            return
        try:
            await self._write_journal(**{KEY_POST_COMMIT_AT: at})
        except Exception as e:
            self.update_logger.warning(f"Where the Milo update's post-commit phase stands was not recorded: {e}")

    async def _notify_milo_complete(self, success: bool) -> None:
        if self._on_milo_complete is not None:
            try:
                await self._on_milo_complete(success)
            except Exception as e:
                self.update_logger.error(f"Milo update completion not announced: {e}")

    @staticmethod
    def _failure_after_rollback(error: str, rolled_back: bool) -> Dict[str, Any]:
        """The failure envelope of a path that restored the previous version.

        `success` is False either way — what the two outcomes distinguish is
        whether the program is running again, which is the only half the owner
        can act on. The flow's caller — the route, or a Milō update's
        reconciliation — logs `error` once, at error level; the flow itself
        logs only a failure the envelope does not carry (a rollback's own).
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

    async def _run_git(self, *args: str, timeout: int = 60) -> tuple[bool, str]:
        """Run one git command in the Milo checkout, bounded and checked."""
        return await self._run_local(
            "git", "-C", self.programs["milo"]["git_path"], *args, timeout=timeout
        )

    async def _git_output(self, *args: str) -> str:
        """What one git command prints, or an exception naming why it did not."""
        ok, output = await self._run_git(*args)
        if not ok:
            raise Exception(f"git {' '.join(args)} failed: {output}")
        return output

    def _frontend_paths(self) -> tuple[Path, Path]:
        """Where the running frontend lives, and where the previous one is kept."""
        return (
            Path(self.programs["milo"]["git_path"]) / "frontend" / "dist",
            self._milo_backup(KEPT_DIST_NAME),
        )

    def declared_sha256(self, program_key: str, version: str, asset: str = "main") -> Optional[str]:
        """The digest `dependencies.env` declares for one file `version` of
        `program_key` downloads, or None for a version it does not declare.

        Public for the routes that send a satellite the version to install: a
        satellite carries no manifest, so the digest travels with the version.
        """
        return digest_for(self.programs[program_key], version, asset)

    async def _download_release_asset(self, url: str, dest: Path, sha256: Optional[str]) -> None:
        """Stream one release asset to disk and check it against `sha256`.
        Raises on anything but a 200, and on a file that is not the one declared.

        Every download an update makes goes through here, so the check is in one
        place: four copies of this loop lived in this class, and a fifth flow
        would have had to remember it. `sha256` is None only for a version the
        manifest does not declare, said so in the log; the file is then what
        GitHub served.
        """
        name = url.rsplit("/", 1)[-1]
        digest = hashlib.sha256()
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Download failed: HTTP {response.status} for {url}")
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        digest.update(chunk)
                        await f.write(chunk)
        actual = digest.hexdigest()
        if sha256 is None:
            self.update_logger.warning(
                f"{name}: no declared digest (a version past dependencies.env), sha256 {actual}"
            )
        elif actual != sha256:
            raise Exception(f"{name}: checksum mismatch, declared {sha256}, downloaded {actual}")

    async def _stage_release_frontend(self, tag: str, sha256: str) -> Path:
        """Download the frontend built for `tag`, check it against `sha256` —
        the digest its signed manifest names — and extract it beside the
        running one; return the staging directory `_swap_release_frontend`
        takes.

        The unit does not build it any more. Two units updated a month apart
        resolved `npm install` against whatever the registry offered that day
        and compiled it with whatever `n stable` had left them, so what a
        release was tested with and what a unit ran were two different trees —
        and a bad hour at the npm registry blocked the whole fleet from updating
        at all. CI builds it once, publishes it beside the image, and this
        installs that exact artefact.

        The digest is the signed manifest's, never the `.sha256` sidecar the
        release also publishes (for the releases that predate signing): the
        sidecar is whatever the channel served, and a frontend is code the
        kiosk runs. It also catches a truncated download, which extracts into a
        tree that is *almost* a frontend, and nginx serves it. Raises on every
        failure, staging removed. It reads nothing from the new release's
        tree, so the update runs it before anything moves and a failure here is
        a refusal.

        The release is staged in `frontend/`, beside `dist`, because only on the
        same filesystem is the swap two renames. Staged in /tmp — a tmpfs on the
        unit — the second move became a copy, and a copy that failed halfway
        left a `dist` mixing both releases while the old one sat in the backup.
        A staging directory left by a process killed mid-install is removed
        first; git ignores the pattern, so a leftover never reads as a local
        change to the next update.
        """
        config = self.programs["milo"]
        url = config["frontend_asset_url"].format(tag=tag)
        dist, _ = self._frontend_paths()

        for leftover in dist.parent.glob(".dist-staging-*"):
            await self._cleanup_temp_files(str(leftover))
        staging = Path(tempfile.mkdtemp(prefix=".dist-staging-", dir=dist.parent))
        try:
            archive = staging / "frontend.tar.gz"
            await self._download_release_asset(url, archive, sha256)

            # Extracted beside the running frontend and swapped in, never over
            # it: a tar that fails halfway through an in-place extraction leaves
            # a tree that is neither release, and nginx serves it either way.
            staged = staging / "staged"
            staged.mkdir()
            success, output = await self._run_local(
                "tar", "-xzf", str(archive), "-C", str(staged), timeout=120
            )
            if not success:
                raise Exception(f"Frontend extraction failed: {output}")
            archive.unlink()

            if not (staged / "dist" / "index.html").is_file():
                raise Exception(f"The {tag} frontend asset carries no dist/index.html")
        except BaseException:
            await self._cleanup_temp_files(str(staging))
            raise
        return staging

    async def _swap_release_frontend(self, staging: Path, tag: str) -> None:
        """Put the frontend `_stage_release_frontend` staged in place of the
        running one, and remove the staging.

        The previous one is moved to the backup rather than deleted:
        `frontend/dist` is not in git, so it is the one part of the app a
        checkout back cannot restore. A rename that fails raises with the
        previous one possibly still aside: the guard puts it back whenever the
        live one is missing.
        """
        dist, kept = self._frontend_paths()
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            if dist.exists():
                os.rename(dist, kept)
            os.rename(staging / "staged" / "dist", dist)
            self.update_logger.info(f"Frontend {tag} installed from the release asset")
        finally:
            await self._cleanup_temp_files(str(staging))

    async def _install_python_dependencies(self) -> None:
        """Install the release's requirements.lock into the app venv. Raises on failure.

        The lock, never requirements.txt: the floors there resolve to whatever
        PyPI serves the day pip runs, so two units updated a week apart ran two
        different sets, and neither was the one the release was tested with.
        `--require-hashes` makes pip refuse any file the lock does not name by
        digest, and any package it does not pin — but not the build backend it
        fetches to compile a package that has no wheel for the Pi (evdev, lgpio:
        `BUILT_ON_INSTALL` in test_python_lock.py), which pip installs unpinned
        and never hash-checks. `--no-cache-dir`: pip's cache
        under the milo HOME is SD card space no later update reads back, and
        the update is the one moment that space is counted.
        """
        git_path = Path(self.programs["milo"]["git_path"])
        success, output = await self._run_local(
            str(git_path / "venv" / "bin" / "pip3"),
            "install", "--no-cache-dir", "--require-hashes",
            "-r", str(git_path / PYTHON_LOCK),
            timeout=MILO_PIP_TIMEOUT_S,
        )
        if not success:
            raise Exception(f"pip install failed: {output}")

    async def _check_the_new_release_imports(self) -> None:
        """Import the new release with the venv it will run in. Raises on failure.

        A module the venv lacks, a syntax error, an import cycle: each is a
        backend that exits at its first start, found here while the release
        that runs can still hand the unit back, rather than by the trial two
        starts later. `pip check` then catches what an import does not reach,
        a dependency installed at a version its dependent refuses.
        """
        git_path = self.programs["milo"]["git_path"]
        python = str(Path(git_path) / "venv" / "bin" / "python")
        ok, output = await self._run_local(
            python, "-I", "-c", _IMPORT_CHECK, git_path, timeout=MILO_IMPORT_CHECK_TIMEOUT_S,
        )
        if not ok:
            raise Exception(f"The new release does not import: {_last_line(output) or output}")
        ok, output = await self._run_local(python, "-I", "-m", "pip", "check", timeout=60)
        if not ok:
            raise Exception(f"pip check failed: {output}")

    def _measure_free_space(self) -> tuple[int, int]:
        """(bytes the update needs, bytes free or freed before the first copy).
        Blocking."""
        config = self.programs["milo"]
        git_path = Path(config["git_path"])
        dist, _ = self._frontend_paths()
        # The venv is counted whether or not the lock moves: that is
        # only known once the release is fetched, after this check.
        needed = (
            _tree_size(git_path / "venv")
            + 3 * _tree_size(dist)
            + sum(Path(p).stat().st_size for p in config["data_files"] if Path(p).is_file())
            + MILO_UPDATE_MARGIN_BYTES
        )
        reclaimed = sum(_tree_size(self._milo_backup(name)) for name in _CLEARED_BACKUPS)
        return needed, shutil.disk_usage(git_path).free + reclaimed

    def _clear_milo_backups(self) -> None:
        """Empty what the previous update kept aside, so a copy found there
        means "this update replaced it". Blocking; raises when one stays."""
        for name in _CLEARED_BACKUPS:
            path = self._milo_backup(name)
            if path.exists() or path.is_symlink():
                shutil.rmtree(path)
        self._milo_backup("").mkdir(parents=True, exist_ok=True)

    def _keep_venv(self) -> None:
        """Copy the venv aside before pip changes it. Blocking.

        A copy, not a move: pip installs into the live path, which every script
        in the venv names in its shebang. Renamed into its kept name only once
        complete and on the card, so the guard never puts back half a venv.
        """
        venv = Path(self.programs["milo"]["git_path"]) / "venv"
        partial = self._milo_backup(KEPT_VENV_PARTIAL_NAME)
        shutil.rmtree(partial, ignore_errors=True)
        shutil.copytree(venv, partial, symlinks=True)
        os.sync()
        os.rename(partial, self._milo_backup(KEPT_VENV_NAME))
        fsync_directory(partial.parent)

    async def _update_milo_app(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Install the release `status` names, then hand the unit to a reboot
        that tries it.

        A release, not a branch: the offer is built from the repo's
        `releases/latest` and the install checks out *that release's tag*, so
        what was offered is what lands. `git pull origin main` installed
        whatever main was at the moment of the click — a tree nobody tagged,
        tested or published, and impossible to withhold.

        And a signed release, not whatever the channel serves: the release's
        manifest is verified against the keys of the release that runs before
        anything is downloaded into the tree (`verify_release`), the frontend
        is checked against the digest it names, and the tag must resolve to the
        commit it names — which is what is checked out, by hash, so nothing
        between the check and the checkout can move it. A release that does not
        verify is a refusal: nothing has moved. The guard's way back, to the
        commit this unit ran before, verifies nothing: that commit is the one
        the unit was flashed with or installed — verified then — recorded by
        hash in the journal before the fetch.

        This process only installs; it never judges. Everything it changes is
        named in the journal before it changes it, and the guard that runs
        before every start of the backend (`milo-update-guard`) restores the
        previous release from it when the install did not finish, when this
        process recorded a failure, or when the new release does not hold
        through its trial. So there is one rollback, and it works the same
        after a failure here, a crash, a power cut or a release that does not
        start. The dependency set and the satellites move only once the new
        release has held (`run_post_commit`), so a rollback has nothing outside
        the checkout to undo.

        Until the journal opens nothing has moved, and a failure is a plain
        refusal. After it, a failure is recorded and the backend restarts into
        the guard. Either way that hands over, the answer is `pending`: the
        outcome belongs to the release that starts next.

        The release's frontend is swapped in last, just before the reboot. Its
        assets are named by content hash and nginx answers 404 for a name it
        does not hold, so a `dist` swapped earlier left every open UI unable to
        load a view it had not shown yet, against a backend still running the
        previous release.
        """
        config = self.programs["milo"]
        git_path = Path(config["git_path"])
        tag = status["latest"]["tag_name"]
        origin_tag = status["installed"].get("release_tag")

        if self.milo_update_active:
            return {"success": False, "error": "A Milo update is already in progress"}
        if self._milo_journal() is not None:
            # Let go of but still open: what it kept aside is the only way
            # back, and a new journal would clear it.
            return {
                "success": False,
                "error": "The previous Milo update is not closed yet; restarting Milo finishes it",
            }
        if not (git_path / ".git").exists():
            return {"success": False, "error": "Not a git repository"}
        if not origin_tag:
            return {"success": False, "error": "This checkout is not on a release tag"}

        self._milo_running = True
        self._milo_versions = (origin_tag, tag)
        staging = None
        journal_open = False
        handed_over = False
        try:
            try:
                await self._milo_progress("download")
                needed, free = await asyncio.to_thread(self._measure_free_space)
                if needed > free:
                    raise Exception(
                        f"Not enough free space: the update needs {needed >> 20} MB, "
                        f"{free >> 20} MB are free"
                    )

                # The signature before anything else is downloaded: what it
                # vouches for is what every later check compares against.
                manifest = await self.verify_release(tag)
                staging = await self._stage_release_frontend(tag, manifest.frontend_sha256)

                # Before the fetch, and by hash: tags have been re-cut, so the
                # name of the release this unit runs is no way back to it.
                origin_commit = await self._git_output("rev-parse", "HEAD")
                # An unread exit code makes a failed status indistinguishable
                # from a clean tree: both give empty stdout. Before the fetch,
                # which writes no working file, so a tree someone works in is
                # refused before its locks are looked at.
                success, output = await self._run_git("status", "--porcelain")
                if not success:
                    raise Exception(f"Git status failed: {output}")
                if output:
                    raise Exception("Local changes detected. Please commit or stash them first.")
                await asyncio.to_thread(self._remove_stale_git_locks)
                # `--tags` adds `refs/tags/*:refs/tags/*` to the fetch, and that
                # is what brings the new release's tag object in at all: this
                # checkout's configured refspec is
                # `+refs/heads/main:refs/remotes/origin/main` — measured.
                success, output = await self._run_git(
                    "fetch", "origin", "--tags", "--force", timeout=120
                )
                if not success:
                    raise Exception(f"Git fetch failed: {output}")
                target_commit = await self._git_output("rev-parse", f"{tag}^{{commit}}")
                # A tag re-pointed after the owner signed — on GitHub, by
                # whoever can push one — names a commit nobody signed for.
                if target_commit != manifest.commit:
                    raise Exception(
                        f"{tag} names commit {target_commit}, its signed manifest {manifest.commit}"
                    )
                requirements_moved = requirements_moved_up(
                    await self._git_output("show", f"{origin_commit}:{PYTHON_LOCK}"),
                    await self._git_output("show", f"{target_commit}:{PYTHON_LOCK}"),
                )
                # The last refusal: nothing installs under a dpkg run. The sync
                # would wait only a minute to install a package the release
                # adds, then fail into a rollback, and the reboot that ends the
                # install would cut the run.
                held_by = await self._wait_for_apt()
                if held_by:
                    raise Exception(f"apt is busy ({held_by} after {APT_LOCK_WAIT_S} s); nothing was installed")

                await self._milo_progress("install")
                await asyncio.to_thread(self._clear_milo_backups)
                await asyncio.to_thread(
                    take_snapshot, config["data_files"], self._milo_backup(DATA_SNAPSHOT_NAME)
                )
                await self._write_journal(**{
                    KEY_ORIGIN_COMMIT: origin_commit,
                    KEY_ORIGIN_TAG: origin_tag,
                    KEY_TARGET_TAG: tag,
                    KEY_TARGET_COMMIT: target_commit,
                    KEY_STEP: "started",
                    KEY_FAILURE: None,
                    KEY_VENV_KEPT: False,
                })
                journal_open = True
                self._last_update = None
                await asyncio.to_thread(self._forget_last_update)

                if not await self._systemd.start(config["inhibit_service_name"]):
                    self.update_logger.error(
                        "The shutdown inhibitor did not start; a power key or a "
                        "shutdown from outside Milo can now cut the install"
                    )

                # By hash: the commit the signature names, not a ref that names it.
                success, output = await self._run_git("checkout", "--force", target_commit, timeout=120)
                if not success:
                    raise Exception(f"Git checkout of {tag} failed: {output}")
                await self._record_step("checked_out")

                if requirements_moved:
                    await asyncio.to_thread(self._keep_venv)
                    await self._write_journal(**{KEY_VENV_KEPT: True})
                    await self._install_python_dependencies()
                await self._check_the_new_release_imports()
                await self._record_step("installed")

                await self._sync_system_files()
                await self._record_step("synced")

                swapping, staging = staging, None
                await self._swap_release_frontend(swapping, tag)
                await self._record_step("swapped")

                await self._milo_progress("restart")
                await self._write_journal(**{KEY_STEP: TRIAL_STEP})
            except Exception as e:
                if not journal_open:
                    self.update_logger.info(f"Milo update to {tag} refused: {e}")
                    return {"success": False, "error": str(e)}
                handed_over = True
                return await self._hand_over_to_the_guard(tag, origin_tag, str(e))

            handed_over = True
            await self._reboot_into_the_trial()
            return {"success": True, "pending": True}

        finally:
            self._milo_running = False
            if not handed_over:
                self._milo_phase = None
            # The swap removes its own staging; this catches a step that
            # failed, or was cancelled, before it.
            if staging is not None:
                await self._cleanup_temp_files(str(staging))

    def _remove_stale_git_locks(self) -> None:
        """Remove the lock files a git command cut by a power loss left behind.
        Blocking.

        Before the journal opens nothing else does: the guard only cleans a
        tree it is putting back. A fetch cut while it wrote a ref left
        `refs/tags/<tag>.lock`, and every later fetch refused on it; a cut
        `git status` left `index.lock`, and the checkout refused after the
        journal had opened, rolling back an update that had changed nothing.
        """
        git_dir = Path(self.programs["milo"]["git_path"]) / ".git"
        locks = [git_dir / name for name in GIT_LOCKS]
        refs = git_dir / "refs"
        if refs.is_dir():
            locks += sorted(refs.rglob("*.lock"))
        for lock in locks:
            if lock.is_file():
                lock.unlink()
                self.update_logger.warning(f"Removed a stale git lock: {lock.relative_to(git_dir)}")

    async def _record_step(self, step: str) -> None:
        """Record a finished install step, once what it wrote is on the card."""
        await asyncio.to_thread(os.sync)
        await self._write_journal(**{KEY_STEP: step})

    def _forget_last_update(self) -> None:
        """The previous outcome goes when a journal opens; so does a start count
        a crashed guard may have left."""
        for name in (LAST_UPDATE_NAME, ATTEMPTS_NAME):
            self._milo_state(name).unlink(missing_ok=True)
        fsync_directory(self._milo_state(LAST_UPDATE_NAME).parent)

    async def _hand_over_to_the_guard(self, tag: str, origin_tag: str, error: str) -> Dict[str, Any]:
        """Record why the install stopped, and restart into the guard that puts
        the previous release back. The restart is the same path as a crash or a
        power cut: there is one rollback, and it is the guard's."""
        detail = " ".join(error.split())[:500]
        # Warning, not error: the guard records how it ended, and the row and
        # the rollback banner say it in the reader's language, where an error
        # would raise the backend-error banner, behind the update screen and
        # first in line once it lifts.
        self.update_logger.warning(
            f"Milo update to {tag} failed: {detail}. Restarting the backend: "
            f"the update guard puts {origin_tag} back."
        )
        try:
            await self._write_journal(**{KEY_FAILURE: detail})
        except Exception as e:
            # The step it stopped at, short of the trial, makes the guard
            # restore all the same; only the reason is lost.
            self.update_logger.error(f"The failure could not be recorded in the journal: {e}")
        await self._milo_progress("restart")
        # Stopped first: it is BindsTo= the backend, which restarts it along
        # with the backend, and left up it would keep the power key dead, and
        # logind refusing every reboot, long after the guard has closed the
        # journal the 409s read.
        await self._stop_inhibitor()
        await self._systemd.restart_self(self.programs["milo"]["service_name"])
        return {"success": False, "pending": True, "error": f"Update failed: {detail}"}

    async def _stop_inhibitor(self) -> None:
        if not await self._systemd.stop(self.programs["milo"]["inhibit_service_name"]):
            self.update_logger.error("The shutdown inhibitor did not stop")

    async def release_stale_inhibitor(self) -> None:
        """Stop a shutdown inhibitor no install holds any more.

        Only the process running an install holds it, and a backend that has
        just started runs none: one still up was left by a backend that died
        mid-install, and would keep the power key dead, and logind refusing
        every reboot, until its RuntimeMaxSec. Called once the backend listens.
        """
        if self._milo_running:
            return
        unit = self.programs["milo"]["inhibit_service_name"]
        state = await self._systemd.unit_state(unit)
        if state is not None and state[0] in ("inactive", "failed"):
            return
        self.update_logger.warning(f"{unit} was left up by a backend that is gone; stopping it")
        await self._stop_inhibitor()

    async def _reboot_into_the_trial(self) -> None:
        """Reboot into the installed release; the guard counts its starts from here.

        A refused reboot restarts the backend instead, so the trial runs in this
        boot: the release is installed and its start is what decides now.
        """
        config = self.programs["milo"]
        # logind refuses a reboot while it holds, root's included (systemd 257).
        await self._stop_inhibitor()
        rebooting, output = await self._run_deploy("reboot")
        if not rebooting:
            self.update_logger.error(
                f"Reboot refused after the install ({output}); restarting the "
                "backend instead, so the new release is tried in this boot"
            )
            await self._systemd.restart_self(config["service_name"])

    async def _run_deploy(self, *args, warn_after: float = 120) -> tuple[bool, str]:
        """Run a milo-deploy-update subcommand via sudo and return its real exit.

        ``warn_after`` is the threshold for a warning and bounds nothing: milo
        cannot signal the root helper, and sudo does not relay SIGKILL, so a
        step past it is still running and its verdict is the one it exits with.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", DEPLOY_UPDATE_CMD, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            comm = asyncio.create_task(proc.communicate())
            try:
                done, _ = await asyncio.wait({comm}, timeout=warn_after)
                if not done:
                    self.update_logger.warning(
                        f"milo-deploy-update {args[0]} still running after {warn_after}s; waiting for its exit"
                    )
                stdout, stderr = await comm
            finally:
                comm.cancel()
            if proc.returncode != 0:
                error = stderr.decode().strip() or stdout.decode().strip()
                return False, error
            return True, stdout.decode().strip()
        except Exception as e:
            return False, str(e)

    async def _sync_system_files(self) -> None:
        """Sync system/ and rootfs/ files to their system destinations, then
        the release's system manifest.

        Delegates to `milo-deploy-update sync-system-files`, which installs
        system/*.service and rootfs/** in place, each through a rename, then
        applies `system-manifest.env` — the packages it names that are
        missing, config.txt's managed block, cmdline.txt's managed tokens, the
        units it enables and masks — and reloads what reads them.

        Raises on failure rather than warning: the units, the rootfs helpers
        and the packages this installs are what the new code expects to find on
        the next boot, so a sync that did not happen is an update that did not
        happen. A package apt cannot install fails it here, before the reboot,
        on a line naming the package and apt's reason. The caller hands the unit
        to the guard, which syncs the previous release's back.
        """
        success, output = await self._run_deploy("sync-system-files", warn_after=60)
        if not success:
            raise Exception(f"System files sync failed: {output}")
        self.update_logger.info("System files sync completed")

    async def _update_binary_program(self, program_key: str, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates a program shipped as a single binary inside a release tarball.

        go-librespot, CamillaDSP and Navidrome all follow this flow: back the
        binary up, download and check the release asset, stop the service, have
        the deploy wrapper check it again and swap the binary out of it, start it
        again, verify, and roll back to the backup if anything after the stop
        fails.

        The one behavioural difference is service policy. CamillaDSP is
        always-on, so it is stopped and started unconditionally; go-librespot's
        Spotify service is on-demand and Navidrome runs only while the dock
        enables Music Library, so they are restarted only if they run when the
        download is done — the owner may select either while it downloads, and
        a reading taken before it would leave the one they chose stopped. A
        state systemd cannot report aborts the update before anything changes.
        """
        config = self.programs[program_key]
        display_name = config["log_name"]
        latest_version = status["latest"]["version"]
        service_name = config["service_name"]

        download_result = None
        service_stopped = False
        run_service = False

        try:
            backup_result = await self._backup_binary_program(config)
            if not backup_result["success"]:
                return backup_result

            download_result = await self._download_binary_program(config, latest_version)
            if not download_result["success"]:
                return download_result

            if config.get("always_on", False):
                run_service = True
            else:
                state = await self._systemd.probe_active(service_name)
                if state is None:
                    await self._cleanup_temp_files(download_result.get("temp_dir"))
                    return self._unreadable_state(service_name)
                run_service = state
                self.update_logger.info(f"Service {service_name} is {'active' if state else 'inactive'} before the swap")

            if run_service:
                if not await self._stop_service(config["service_name"]):
                    await self._cleanup_temp_files(download_result.get("temp_dir"))
                    return {"success": False, "error": "Failed to stop service"}

                service_stopped = True

            # The archive, not a binary unpacked from it: the wrapper checks its
            # own copy of the file against the digest and unpacks that, so what
            # lands in /usr/local/bin as root is what the digest names — not a
            # path a milo process could have rewritten since the check above.
            success, output = await self._run_deploy(
                "install-release-binary", download_result["archive_path"],
                download_result["member"], config["binary_path"],
                download_result["sha256"] or NO_DIGEST,
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
        """Downloads the release tarball, checks it against the manifest's digest,
        and makes sure it carries the program's binary — before anything stops.

        Nothing is unpacked here: the deploy wrapper unpacks the binary from its
        own checked copy. Listing the member is what keeps an archive that lost
        it (an upstream release that moved its layout) a refusal rather than a
        stopped service and a rollback.
        """
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        member = Path(config["binary_path"]).name
        try:
            url = config["asset_url"].format(version=version)
            sha256 = digest_for(config, version)
            archive_path = Path(temp_dir) / url.rsplit("/", 1)[-1]
            await self._download_release_asset(url, archive_path, sha256)

            proc = await asyncio.create_subprocess_exec(
                "tar", "-tzf", str(archive_path), member,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0 or member not in stdout.decode().split():
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {"success": False, "error": (
                    f"Binary not found in archive: no {member} in {archive_path.name}"
                    f" ({stderr.decode().strip() or 'tar listed nothing'})"
                )}

            return {
                "success": True,
                "archive_path": str(archive_path),
                "member": member,
                "sha256": sha256,
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

            if expect_service_active and await self._systemd.probe_active(config["service_name"]) is not True:
                return {"success": False, "error": f"{config['log_name']} service not running after update"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}

    async def _rollback_binary_program(self, config: Dict[str, Any], restart_service: bool = True) -> bool:
        """Restores the backed-up binary, and restarts the service if the caller
        ran it or it runs now.

        The start is made whatever the restore gave, once: the update stopped the
        unit, and `Restart=on-failure` does not undo an explicit stop, so a
        rollback that left before it kept every room silent until a reboot.
        """
        display_name = config["log_name"]
        service_name = config["service_name"]
        restored = False
        try:
            binary_path = Path(config["binary_path"])
            binary_backup = Path(config["backup_path"]) / f"{binary_path.name}.backup"
            restart_service = await self._restart_after_rollback(service_name, restart_service)
            if not binary_backup.exists():
                self.update_logger.error("No backup found for rollback")
            # A unit that refuses to stop keeps its image loaded: install-binary
            # writes a file nothing is running, and the start below would report
            # the *new* binary still up. That is not a rollback, so it does not
            # claim one.
            elif not await self._stop_service(service_name):
                self.update_logger.error(f"{display_name} did not stop, cannot restore its binary")
            else:
                restored = await self._install_backup(binary_backup, str(binary_path))
        except Exception as e:
            self.update_logger.error(f"{display_name} rollback failed: {e}")
        finally:
            started = not restart_service or await self._start_service(service_name)

        if not started:
            self.update_logger.error(f"{display_name} did not start after the rollback")
            return False
        if restored:
            self.update_logger.info(f"{display_name} rollback completed (service {'restarted' if restart_service else 'left stopped'})")
        return restored

    async def _restore_multiroom_services(self) -> bool:
        """Starts the multiroom units back exactly when multiroom is on.

        The two snapcast units have no `WantedBy`: their lifecycle is
        AudioRoutingService's — `_sync_snapcast_state`, which runs at init and
        inside `set_multiroom_enabled`, never reconciles afterwards, and runs both
        of them exactly while `routing.multiroom_enabled` is set; this install is
        the one other hand on them. So that setting decides
        here too, not a probe: one that timed out, or a unit caught `activating`,
        would leave every room mute; and starting them in direct mode leaves
        snapclient holding hw:Loopback,0,0, so the next direct-mode source opens a
        busy device and plays silence until a reboot or a manual multiroom toggle.

        Returns whether every unit multiroom needs is running again: the
        recovery branches word their failure on it, and the install itself is
        a success only when it is true — a snapserver that does not start on
        the new package is every room mute, however well the package went in.
        """
        if not await self.settings_service.get_setting("routing.multiroom_enabled"):
            self.update_logger.info("Leaving the multiroom units stopped (multiroom is off)")
            return True
        restored = True
        for service in self.programs["multiroom"]["services"]:
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
        # False until phase 2 stops anything, so a failure before that restores nothing.
        stopped = False

        try:
            # Phase 1: Download both packages (0-30%)
            server_download = await self._download_snapcast_component("snapserver", latest_version)
            if not server_download["success"]:
                return {"success": False, "error": f"Failed to download snapserver: {server_download.get('error')}"}

            client_download = await self._download_snapcast_component("snapclient", latest_version)
            if not client_download["success"]:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                return {"success": False, "error": f"Failed to download snapclient: {client_download.get('error')}"}

            # Before the stop: install-deb's dpkg run, its `apt update` and its
            # install each fail at once on a lock another apt holds, and
            # waiting for it with the units down is a house without music.
            held_by = await self._wait_for_apt()
            if held_by:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return {"success": False, "error": f"apt is busy ({held_by} after {APT_LOCK_WAIT_S} s); nothing was stopped"}

            # Phase 2: Stop all services (30-40%)
            #
            # The verdict is read here as it is in every other install flow: a
            # unit that would not stop keeps its image loaded, so the .deb lands
            # under the running process, `_restore_multiroom_services` "starts"
            # something that never stopped and reports it restored, and the row
            # then reads the new version off the new binary on disk while the
            # process serving audio is still the old one.
            stopped = True
            for service in config["services"]:
                if not await self._stop_service(service):
                    restored = await self._restore_multiroom_services()
                    await self._cleanup_temp_files(server_download.get("temp_dir"))
                    await self._cleanup_temp_files(client_download.get("temp_dir"))
                    return self._failure_after_restore(
                        f"Failed to stop {service}.", restored
                    )

            # Phase 3: Install snapserver (40-60%)
            server_install = await self._install_deb_package(
                server_download["deb_path"], server_download["sha256"]
            )
            if not server_install["success"]:
                restored = await self._restore_multiroom_services()
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return self._failure_after_restore(
                    f"Failed to install snapserver: {server_install.get('error')}.", restored
                )

            # Phase 4: Install snapclient (60-80%)
            client_install = await self._install_deb_package(
                client_download["deb_path"], client_download["sha256"]
            )
            if not client_install["success"]:
                self.update_logger.warning(f"Snapclient installation failed after snapserver succeeded: {client_install.get('error')}")
                restored = await self._restore_multiroom_services()
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return self._failure_after_restore(
                    f"Snapserver updated but snapclient failed: {client_install.get('error')}.", restored
                )

            # The helper's OK says apt exited 0, which is also what it answers
            # after removing a package it could not satisfy: what the binaries
            # read is the proof, as the NQPTP and qobuz flows read theirs.
            installed = (await self.get_installed_version("multiroom"))["versions"]
            off_target = [
                f"{binary} reads {installed.get(binary) or 'nothing'}"
                for binary in config["commands"]
                if installed.get(binary) != latest_version
            ]
            if off_target:
                restored = await self._restore_multiroom_services()
                await self._cleanup_temp_files(server_download.get("temp_dir"))
                await self._cleanup_temp_files(client_download.get("temp_dir"))
                return self._failure_after_restore(
                    f"{', '.join(off_target)} after install, expected {latest_version}.", restored
                )

            # Phase 5: Restart services (80-95%)
            restored = await self._restore_multiroom_services()

            # Phase 6: Cleanup (95-100%)
            await self._cleanup_temp_files(server_download.get("temp_dir"))
            await self._cleanup_temp_files(client_download.get("temp_dir"))

            # Installed is not done: a unit that does not start on the new
            # package leaves every room mute, and the row, the reconciliation
            # of a Milō update and its outcome must say so.
            if not restored:
                return {
                    "success": False,
                    "error": (
                        f"Snapcast {latest_version} installed, but some multiroom services did "
                        "not start on it - manual intervention required."
                    ),
                }
            return {"success": True}

        except Exception as e:
            if server_download:
                await self._cleanup_temp_files(server_download.get("temp_dir"))
            if client_download:
                await self._cleanup_temp_files(client_download.get("temp_dir"))
            # Nothing was stopped before phase 2, so there is nothing to restore
            # and no outcome to report.
            if not stopped:
                return {"success": False, "error": str(e)}
            restored = await self._restore_multiroom_services()
            return self._failure_after_restore(f"{e}.", restored)

    # === SHAIRPORT-SYNC (compile from source) ===

    async def _update_shairport_sync(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """Updates shairport-sync by compiling from source, with the NQPTP it pairs with.

        NQPTP is the AirPlay 2 clock, and the two builds share one ABI: the
        shared memory interface (SMI) each declares in `nqptp-shm-structures.h`.
        shairport-sync refuses to start against any other ("The NQPTP service
        (SMI Version 10) on this system is too old for this version of Shairport
        Sync"), and 5.6 moved it from 10 to 11. Installed alone it would compile,
        install and verify — an inactive milo-airplay is not started — and leave
        a receiver that dies the next time AirPlay is selected. So the pair is
        settled before anything is stopped (`_prepare_nqptp`), installed in
        upstream's order (NQPTP first), and rolled back together.

        Whether milo-airplay runs is read once the build is done, not before:
        the owner may select AirPlay during the minutes it compiles, and the
        NQPTP swap stops it through `Requires=` — a reading taken before would
        leave the receiver they chose stopped under a success.
        """
        config = self.programs["shairport-sync"]
        latest_version = status["latest"]["version"]
        tag_name = status["latest"]["tag_name"]
        service_name = config["service_name"]

        service_was_active = False
        temp_dir = None
        nqptp_swapped = False

        try:
            # Phase 1: Backup (5%)
            backup_result = await self._backup_shairport_sync(config)
            if not backup_result["success"]:
                return backup_result

            # Phase 2: Download source (10%)
            download_result = await self._download_source(
                config["repo"], tag_name, digest_for(config, latest_version)
            )
            if not download_result["success"]:
                return download_result
            temp_dir = download_result["temp_dir"]
            source_dir = download_result["source_dir"]

            # Phase 2b: the NQPTP this source runs against, built under temp_dir.
            # The tree declares its interface already, so an impossible pair is
            # refused before minutes of compiling.
            pair = await self._prepare_nqptp(source_dir, latest_version, temp_dir)
            if not pair["success"]:
                await self._cleanup_temp_files(temp_dir)
                return pair

            # Phase 3: Configure (20%)
            configure_result = await self._configure_source(source_dir, config["configure_flags"])
            if not configure_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return configure_result

            # Phase 4: Compile (30%)
            compile_result = await self._compile_source(source_dir)
            if not compile_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                return compile_result

            state = await self._systemd.probe_active(service_name)
            if state is None:
                await self._cleanup_temp_files(temp_dir)
                return self._unreadable_state(service_name)
            service_was_active = state
            self.update_logger.info(f"Service {service_name} is {'active' if state else 'inactive'} before the install")

            # Phase 5: Stop service only if active (75%)
            if service_was_active:
                stop_result = await self._stop_service(config["service_name"])
                if not stop_result:
                    await self._cleanup_temp_files(temp_dir)
                    return {"success": False, "error": "Failed to stop service"}

            # Phase 5b: NQPTP, when it has to move
            if pair["build"] is not None:
                nqptp_swapped = True
                nqptp_result = await self._install_nqptp(pair["build"])
                if not nqptp_result["success"]:
                    await self._cleanup_temp_files(temp_dir)
                    rolled_back = await self._rollback_shairport_sync(
                        config, service_was_active, restore_nqptp=True
                    )
                    return self._failure_after_rollback(f"{nqptp_result['error']}.", rolled_back)

            # Phase 6: Install (80%)
            install_result = await self._install_shairport_sync(source_dir)
            if not install_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                rolled_back = await self._rollback_shairport_sync(
                    config, service_was_active, restore_nqptp=nqptp_swapped
                )
                return self._failure_after_rollback(f"{install_result['error']}.", rolled_back)

            # Phase 7: Restart service if it was active (85%)
            if service_was_active:
                start_result = await self._start_service(config["service_name"])
                if not start_result:
                    await self._cleanup_temp_files(temp_dir)
                    rolled_back = await self._rollback_shairport_sync(
                        config, service_was_active, restore_nqptp=nqptp_swapped
                    )
                    return self._failure_after_rollback(
                        "Failed to start service after update.", rolled_back
                    )

            # Phase 8: Verify (90%)
            verify_result = await self._verify_shairport_sync_update(
                config, service_was_active, expected_nqptp=pair["expected"]
            )
            if not verify_result["success"]:
                await self._cleanup_temp_files(temp_dir)
                rolled_back = await self._rollback_shairport_sync(
                    config, service_was_active, restore_nqptp=nqptp_swapped
                )
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
            await self._cleanup_temp_files(temp_dir)
            rolled_back = await self._rollback_shairport_sync(
                config, service_was_active, restore_nqptp=nqptp_swapped
            )
            return self._failure_after_rollback(f"{e}.", rolled_back)

    async def _backup_shairport_sync(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Backs up the shairport-sync binary, and the NQPTP binary it runs against"""
        try:
            backup_dir = Path(config["backup_path"])
            backup_dir.mkdir(parents=True, exist_ok=True)

            binary_path = Path(config["binary_path"])
            if binary_path.exists():
                binary_backup = backup_dir / "shairport-sync.backup"
                shutil.copy2(config["binary_path"], binary_backup)

            nqptp_path = Path(config["nqptp"]["binary_path"]) if "nqptp" in config else None
            if nqptp_path is not None:
                # One taken now or none: an older run's would be restored as current.
                (backup_dir / "nqptp.backup").unlink(missing_ok=True)
                if nqptp_path.exists():
                    shutil.copy2(nqptp_path, backup_dir / "nqptp.backup")

            return {"success": True, "backup_dir": str(backup_dir)}

        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}

    # === NQPTP (built and installed by the shairport-sync update only) ===

    @staticmethod
    async def _declared_smi(source_dir: str) -> Optional[int]:
        """The shared memory interface a source tree was written against, or None.

        Both repositories carry the same header at their root, so the one
        reader serves both sides of the pair.
        """
        header = Path(source_dir) / "nqptp-shm-structures.h"
        try:
            async with aiofiles.open(header, errors="replace") as f:
                text = await f.read()
        except OSError:
            return None
        match = _SMI_DEFINE_RE.search(text)
        return int(match.group(1)) if match else None

    async def _installed_nqptp(self, binary_path: str) -> Optional[tuple[str, int]]:
        """(version, SMI) of the installed NQPTP, from `nqptp -V`, or None."""
        ok, output = await self._run_local(binary_path, "-V", timeout=10)
        match = _NQPTP_VERSION_RE.search(output) if ok else None
        return (match.group(1), int(match.group(2))) if match else None

    async def _prepare_nqptp(
        self, shairport_source: str, shairport_version: str, parent_dir: str,
    ) -> Dict[str, Any]:
        """Settle the NQPTP the new shairport-sync will run against, stopping nothing.

        NQPTP follows the manifest: `NQPTP_VERSION` is the only one Milō installs,
        whichever shairport-sync release is going in, so a version above or
        below it is brought back the same way. Returns `build` — the compiled
        binary to install, or None when the installed one is already the
        manifest's and speaks the interface — and `expected`, the
        (version, SMI) the verification reads back.

        Refused before any service is stopped:
        - a pair the manifest cannot form: the shairport-sync going in needs an
          SMI the validated NQPTP does not provide, which is what a trial of an
          upstream release that moved the interface looks like. NQPTP_VERSION
          has to move in the same edit as SHAIRPORT_SYNC_VERSION.
        - a unit whose nqptp.service does not grant CAP_NET_BIND_SERVICE. The
          right used to be a file capability on the binary (upstream's install
          hook ran `setcap`), and a replaced binary carries none — nor does the
          backup a rollback would put back — so NQPTP would exit on port 319.
          An unreadable set is refused too, as a retry, not as a unit fault.
        """
        nqptp = self.programs["shairport-sync"]["nqptp"]
        target = nqptp["validated_version"]
        required = await self._declared_smi(shairport_source)
        if required is None:
            return {"success": False, "error": (
                f"shairport-sync {shairport_version} declares no NQPTP interface version"
            )}

        def unpairable(provided: Optional[int]) -> Dict[str, Any]:
            return {"success": False, "error": (
                f"shairport-sync {shairport_version} needs NQPTP interface smi{required}, "
                f"and the validated NQPTP {target} provides smi{provided}: "
                "NQPTP_VERSION has to move with SHAIRPORT_SYNC_VERSION"
            )}

        installed = await self._installed_nqptp(nqptp["binary_path"])
        if installed == (target, required):
            return {"success": True, "build": None, "expected": installed}
        if installed is not None and installed[0] == target:
            return unpairable(installed[1])

        capabilities = await self._systemd.ambient_capabilities(nqptp["service_name"])
        if capabilities is None:
            return {"success": False, "error": (
                f"could not read the capabilities of {nqptp['service_name']} "
                "from systemd; retry the update"
            )}
        if not capabilities & (1 << CAP_NET_BIND_SERVICE):
            return {"success": False, "error": (
                f"{nqptp['service_name']} does not grant CAP_NET_BIND_SERVICE, so a "
                "replaced NQPTP could not open its ports; install Milo's own unit "
                "(system/nqptp.service, synced by a Milo update) first"
            )}

        download = await self._download_source(
            nqptp["repo"], target, digest_for(nqptp, target), parent_dir=parent_dir
        )
        if not download["success"]:
            return download
        source_dir = download["source_dir"]

        provided = await self._declared_smi(source_dir)
        if provided != required:
            return unpairable(provided)

        configured = await self._configure_source(source_dir, [])
        if not configured["success"]:
            return configured
        compiled = await self._compile_source(source_dir)
        if not compiled["success"]:
            return compiled

        binary = Path(source_dir) / "nqptp"
        if not binary.is_file():
            return {"success": False, "error": "nqptp binary not found after the build"}
        return {
            "success": True,
            "build": {"binary": str(binary), "version": target},
            "expected": (target, required),
        }

    async def _install_nqptp(self, build: Dict[str, Any]) -> Dict[str, Any]:
        """Swap the NQPTP binary and bring its unit back up.

        Stopping nqptp.service also stops milo-airplay (`Requires=`), which the
        caller has already stopped when it was running and starts again after.
        """
        nqptp = self.programs["shairport-sync"]["nqptp"]
        service = nqptp["service_name"]
        if not await self._stop_service(service):
            return {"success": False, "error": "NQPTP did not stop"}

        success, output = await self._run_deploy("install-binary", build["binary"], nqptp["binary_path"])
        if not success:
            return {"success": False, "error": f"NQPTP installation failed: {output}"}

        if not await self._start_service(service):
            return {"success": False, "error": "NQPTP did not start after update"}
        # NQPTP opens ports 319/320 right after it starts and exits when it
        # cannot: a start that returned is not yet a running clock.
        await asyncio.sleep(NQPTP_SETTLE_S)
        if await self._systemd.probe_active(service) is not True:
            return {"success": False, "error": "NQPTP exited after update"}

        self.update_logger.info(f"NQPTP {build['version']} installed")
        return {"success": True}

    # === Source builds (shairport-sync and its NQPTP) ===

    async def _download_source(
        self, repo: str, tag_name: str, sha256: Optional[str], parent_dir: str = "/tmp",
    ) -> Dict[str, Any]:
        """Downloads a GitHub tag's source tarball, checks it against the
        manifest's digest, and extracts it.

        Checked before anything is built from it: the build runs as milo, but
        `make install` lands its result in /usr/local/bin through the root
        helper, and a binary compiled from a tree nobody checked is the same
        root install as an unchecked download.
        """
        temp_dir = tempfile.mkdtemp(dir=parent_dir)
        try:
            url = source_archive_url(repo, tag_name)

            self.update_logger.info(f"Downloading source from {url}...")

            archive_path = Path(temp_dir) / "source.tar.gz"
            await self._download_release_asset(url, archive_path, sha256)

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

            # Find the extracted directory ({repo name}-{tag})
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

    async def _configure_source(self, source_dir: str, configure_flags: list) -> Dict[str, Any]:
        """Runs autoreconf and configure in a source tree"""
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
            self.update_logger.info(f"Running ./configure in {Path(source_dir).name} with flags: {configure_flags}")
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

    async def _compile_source(self, source_dir: str) -> Dict[str, Any]:
        """Compiles a configured source tree with make"""
        try:
            # Get number of CPU cores for parallel build
            nproc_proc = await asyncio.create_subprocess_exec(
                "nproc",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            nproc_stdout, _ = await nproc_proc.communicate()
            num_cores = nproc_stdout.decode().strip() or "2"

            self.update_logger.info(f"Compiling {Path(source_dir).name} with make -j{num_cores}...")
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

    # === shairport-sync's install, verification and rollback ===

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

    async def _verify_shairport_sync_update(
        self, config: Dict[str, Any], service_was_active: bool,
        expected_nqptp: Optional[tuple[str, int]] = None,
    ) -> Dict[str, Any]:
        """Verifies that shairport-sync was updated successfully, and that the
        NQPTP beside it is the (version, SMI) the update settled on"""
        try:
            binary_path = Path(config["binary_path"])
            if not binary_path.exists():
                return {"success": False, "error": "shairport-sync binary not found after update"}

            if expected_nqptp is not None:
                installed = await self._installed_nqptp(config["nqptp"]["binary_path"])
                if installed != expected_nqptp:
                    return {"success": False, "error": (
                        f"NQPTP reads {installed} after update, expected {expected_nqptp}"
                    )}

            # Check service is running (only if it was active before)
            if service_was_active:
                if await self._systemd.probe_active(config["service_name"]) is not True:
                    return {"success": False, "error": "shairport-sync service not running after update"}

            return {"success": True}

        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}

    async def _rollback_shairport_sync(
        self, config: Dict[str, Any], service_was_active: bool = True,
        restore_nqptp: bool = False,
    ) -> bool:
        """Rollback shairport-sync to the backed up version — and NQPTP with it
        when the update had already replaced it, since the old shairport-sync
        speaks the old interface only.

        As in `_rollback_binary_program`, each unit this flow stopped is started
        again whatever the restore gave, once.
        """
        service_name = config["service_name"]
        restored = False
        try:
            service_was_active = await self._restart_after_rollback(service_name, service_was_active)
            restored = await self._restore_shairport_sync_backups(config, restore_nqptp)
        except Exception as e:
            self.update_logger.error(f"shairport-sync rollback failed: {e}")
        finally:
            started = not service_was_active or await self._start_service(service_name)

        if not started:
            self.update_logger.error("shairport-sync did not start after the rollback")
            return False
        if restored:
            self.update_logger.info(f"shairport-sync rollback completed (service {'restarted' if service_was_active else 'left stopped'})")
        return restored

    async def _restore_shairport_sync_backups(self, config: Dict[str, Any], restore_nqptp: bool) -> bool:
        """Put the backed-up binaries back; the caller starts shairport-sync."""
        backup_dir = Path(config["backup_path"])
        binary_backup = backup_dir / "shairport-sync.backup"
        nqptp_backup = backup_dir / "nqptp.backup"
        if not binary_backup.exists():
            self.update_logger.error("No backup found for rollback")
            return False
        # A unit that had no NQPTP before has none to go back to: the
        # shairport-sync binary is still put back, and the rollback is
        # not claimed.
        nqptp_restorable = not restore_nqptp or nqptp_backup.exists()
        if not nqptp_restorable:
            self.update_logger.error("No NQPTP backup to roll back to")

        # Same as _rollback_binary_program: a unit that will not stop makes
        # the restore unverifiable, so it is not claimed.
        if not await self._stop_service(config["service_name"]):
            self.update_logger.error("shairport-sync did not stop, cannot restore its binary")
            return False

        nqptp_started = True
        if restore_nqptp and nqptp_restorable:
            nqptp = config["nqptp"]
            if not await self._stop_service(nqptp["service_name"]):
                self.update_logger.error("NQPTP did not stop, cannot restore its binary")
                return False
            nqptp_restored = False
            try:
                nqptp_restored = await self._install_backup(nqptp_backup, nqptp["binary_path"])
            finally:
                nqptp_started = await self._start_service(nqptp["service_name"])
            if not nqptp_restored:
                return False
            # The old NQPTP is on disk now, so the old shairport-sync still goes
            # back with it: the new one would be started against the wrong clock.
            if not nqptp_started:
                self.update_logger.error("NQPTP binary restored but the service did not start")

        if not await self._install_backup(binary_backup, config["binary_path"]):
            return False
        return nqptp_restorable and nqptp_started

    async def _install_backup(self, backup: Path, binary_path: str) -> bool:
        """Put a backed-up binary back, through a copy install-binary accepts.

        The copy goes to /var/tmp, on disk: /tmp is RAM, and a rollback runs
        exactly when an update may just have filled it.
        """
        fd, tmp_backup_str = tempfile.mkstemp(prefix="milo-rollback-", dir="/var/tmp")
        tmp_backup = Path(tmp_backup_str)
        try:
            os.close(fd)
            shutil.copy2(backup, tmp_backup)
            success, output = await self._run_deploy("install-binary", str(tmp_backup), binary_path)
            if not success:
                self.update_logger.error(f"Rollback install failed: {output}")
            return success
        finally:
            tmp_backup.unlink(missing_ok=True)

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
        """Updates the qobuz-proxy sidecar (a pip package installed from its tag).

        Unlike the binary programs, the "install" is a pip upgrade inside the
        milo-owned venv — unprivileged, since the backend runs as milo. What pip
        installs is the tag's source archive, downloaded and checked against the
        manifest's digest first: a `git+…@tag` reference is whatever the tag
        points at the moment pip clones it, which nothing can check. Nothing
        is written into site-packages: Milō's volume policy and status progress
        are applied at runtime by the launcher the service runs, so the venv
        matches the released package exactly. What is asked here instead is
        whether the new release still offers what those adaptations bind to.
        The whole venv is backed up first so any failure rolls back to the
        working version; config.yaml and credentials.json are left untouched.
        """
        config = self.programs["qobuz-proxy"]
        latest_version = status["latest"]["version"]
        # Use the exact upstream tag for the archive (not a reconstructed
        # "v{version}") so a tag that isn't simply "v" + semver still resolves.
        tag_name = status["latest"]["tag_name"]
        temp_dir = None
        venv = config["venv_path"]
        service = config["service_name"]

        # Read once the backup is taken, right before the stop it decides: the
        # owner may select Qobuz while the venv is copied.
        service_was_active = False
        # Nothing to roll back to before phase 1 completes, so a failure above
        # that line reports no rollback outcome at all.
        backed_up = False

        try:
            # Phase 0: the release's source, checked before anything moves (5%)
            temp_dir = tempfile.mkdtemp(dir="/tmp")
            archive = Path(temp_dir) / f"qobuz-proxy-{tag_name}.tar.gz"
            await self._download_release_asset(
                source_archive_url(config["repo"], tag_name), archive,
                digest_for(config, latest_version),
            )

            # Phase 1: Back up the venv (10%)
            backup_result = await self._backup_qobuz_venv(config)
            if not backup_result["success"]:
                return backup_result
            backed_up = True

            state = await self._systemd.probe_active(service)
            if state is None:
                await self._cleanup_qobuz_backup(config)
                return self._unreadable_state(service)
            service_was_active = state
            self.update_logger.info(f"Service {service} is {'active' if state else 'inactive'} before the upgrade")

            # Phase 2: Stop the sidecar before touching the venv (50%).
            # Normally already stopped (its session ended before the install),
            # so this is defensive against a file lock (Restart=always).
            if service_was_active:
                if not await self._stop_service(service):
                    rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                    return self._failure_after_rollback("Failed to stop service.", rolled_back)

            # Phase 3: pip upgrade to the checked archive (70%) — unprivileged
            pip_ok, pip_out = await self._run_local(
                f"{venv}/bin/pip", "install", "--upgrade",
                f"qobuz-proxy[local] @ {archive.as_uri()}",
                timeout=600
            )
            if not pip_ok:
                rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                return self._failure_after_rollback(f"pip install failed: {pip_out}.", rolled_back)

            # Phase 4: does this release still offer what Milō adapts? (85%)
            # Asked before the service is restarted onto it, so a refactor that
            # moved one of them is a rolled-back update rather than a sidecar
            # that starts and reports no progress.
            check_ok, check_out = await self._run_local(
                f"{venv}/bin/python", QOBUZ_ADAPTER, "--check", timeout=60
            )
            if not check_ok:
                rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
                return self._failure_after_rollback(
                    f"This release no longer offers what Milo adapts: {check_out}.", rolled_back
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
                # that would be worse; the backup is kept for the same reason.
                return {
                    "success": False,
                    "error": "qobuz-proxy updated but its service did not restart. Restart it manually.",
                }

            await self._cleanup_qobuz_backup(config)

            return {"success": True}

        except Exception as e:
            if not backed_up:
                return {"success": False, "error": str(e)}
            rolled_back = await self._rollback_qobuz_venv(config, service_was_active)
            return self._failure_after_rollback(f"{e}.", rolled_back)

        finally:
            await self._cleanup_temp_files(temp_dir)

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

            service_was_active = await self._restart_after_rollback(
                config["service_name"], service_was_active
            )
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
        """The Debian release this unit runs (bookworm, trixie, ...), read from /etc/os-release.

        Raises when it cannot be read: snapcast publishes one .deb per release,
        and another release's package is not the pinned dependency for this one.
        `_update_multiroom` reports the exception as the update's failure.
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
        self.update_logger.info(f"Detected Debian codename: {codename}")
        return codename

    async def _download_snapcast_component(self, component_key: str, version: str) -> Dict[str, Any]:
        """Downloads a snapcast component (.deb) built for this unit's Debian
        release, checked against the manifest's digest of it.

        The digest is the trixie package's, the release every image is built
        on: a unit on another release downloads another file and is refused,
        which is the truth — that file is not the validated dependency.
        """
        debian_codename = await self._get_debian_codename()

        # Determine package name according to component
        if component_key == "snapserver":
            package_name = f"snapserver_{version}-1_arm64_{debian_codename}.deb"
        elif component_key == "snapclient":
            package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
        else:
            return {"success": False, "error": f"Unknown component: {component_key}"}

        url = f"https://github.com/badaix/snapcast/releases/download/v{version}/{package_name}"
        sha256 = digest_for(self.programs["multiroom"], version, component_key)

        self.update_logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")

        # Created only once the component is known and only the caller that gets
        # the success dict is left to clean it up — the temp dir is handed back in
        # `temp_dir` and released by _cleanup_temp_files. Every failure exit below
        # removes it here, as _download_binary_program already does.
        temp_dir = tempfile.mkdtemp(dir="/tmp")
        try:
            deb_path = Path(temp_dir) / package_name
            await self._download_release_asset(url, deb_path, sha256)
            return {
                "success": True,
                "deb_path": str(deb_path),
                "sha256": sha256,
                "temp_dir": temp_dir
            }

        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}


    async def _wait_for_apt(self) -> Optional[str]:
        """Waits for APT_LOCKS to be free, APT_LOCK_WAIT_S at most: None once
        they are, the process still holding one otherwise.

        unattended-upgrades holds dpkg's for its whole run, which takes minutes
        when it installs fixes, and apt-daily the lists' while it refreshes
        them. What `milo-deploy-update` runs waits a minute at most: apt-get for
        dpkg's lock (52milo-unattended-upgrades), sync-system-files' `dpkg
        --configure -a` as long, install-deb's not at all. So both callers
        wait here first, while a refusal still leaves everything as it was.
        """
        held_by = await asyncio.to_thread(dpkg_lock.holder, APT_LOCKS)
        if held_by is None:
            return None
        self.update_logger.info(f"Waiting for {held_by} to let go of apt before installing")
        held_by = await dpkg_lock.wait_until_free(APT_LOCK_WAIT_S, APT_LOCKS)
        if held_by:
            # Warning: the holder rides in the caller's returned error, which
            # whoever asked for the install logs once, at error.
            self.update_logger.warning(f"apt is still held by {held_by} after {APT_LOCK_WAIT_S} s")
        return held_by

    async def _install_deb_package(self, deb_path: str, sha256: Optional[str]) -> Dict[str, Any]:
        """Installs a .deb package via milo-deploy-update, which checks its own
        copy of the file against `sha256` before apt sees it (one `apt-get
        install` that refuses removals, then a dpkg-query of the .deb's own
        version). A .deb runs its maintainer scripts as root, so the digest is
        checked where the trust changes, not only where the file arrived."""
        self.update_logger.info(f"Installing {Path(deb_path).name}...")
        success, output = await self._run_deploy(
            "install-deb", deb_path, sha256 or NO_DIGEST, warn_after=300
        )
        if not success:
            return {"success": False, "error": f"Package installation failed: {output}"}
        self.update_logger.info("Package installed successfully")
        return {"success": True}

    # Service control delegates to the central SystemdServiceManager — one
    # implementation of the privileged `sudo systemctl` gesture (see invariant #1).
    # Every reading here decides an act, so it is `probe_active`'s three answers,
    # never `is_active`, which reads a probe that timed out as "inactive".
    @staticmethod
    def _unreadable_state(service_name: str) -> Dict[str, Any]:
        """The answer of a flow that could not read the unit it was about to stop."""
        return {"success": False, "error": f"state of {service_name} unreadable, nothing changed"}

    async def _restart_after_rollback(self, service_name: str, requested: bool) -> bool:
        """Whether a rollback starts the unit again once the old version is back.

        The caller's flag is the state its own flow acted on, and the unit may
        have been started since — a source selected mid-update — which the
        rollback's own stop would leave down. An unreadable probe leaves the
        caller's flag to decide: a receiver nobody chose is never started on a
        guess.
        """
        return requested or await self._systemd.probe_active(service_name) is True

    async def _stop_service(self, service_name: str) -> bool:
        """Stops a systemd service."""
        return await self._systemd.stop(service_name)

    async def _start_service(self, service_name: str) -> bool:
        """Starts a systemd service."""
        return await self._systemd.start(service_name)


    async def _cleanup_temp_files(self, temp_dir: Optional[str]) -> None:
        """Cleans up temporary files"""
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self.update_logger.warning(f"Failed to cleanup {temp_dir}: {e}")

    async def can_update_program(
        self, program_key: str, target: str = "validated", version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Checks if a program can be installed at the requested target"""
        if program_key not in self.programs:
            return {"can_update": False, "reason": "Update not supported"}
        await self._reload_milo_state()
        if self.milo_update_holds_installs:
            return {"can_update": False, "reason": "A Milo update is in progress; it must finish first"}

        # Verify the deploy wrapper is reachable via sudo NOPASSWD. Not
        # reachable is a broken sudoers rule or a missing helper: said here, in
        # the journal and errors.log, since the route's refusal is a warning.
        success, output = await self._run_deploy("check", warn_after=5)
        if not success:
            self.update_logger.error(
                f"milo-deploy-update is not accessible through sudo: {_last_line(output) or 'no output'}"
            )
            return {"can_update": False, "reason": "Deploy wrapper not accessible"}

        status = await self.get_program_full_status(program_key)
        selected = self._select_target(status, target, version)
        if selected is None:
            return {"can_update": False, "reason": "No update available"}

        return {"can_update": True, "available_version": selected["latest"]["version"]}
