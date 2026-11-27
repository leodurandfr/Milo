# backend/core/updates/satellite.py
"""
Satellite update service — discovers satellites via client registry and manages updates.
"""
import asyncio
import contextlib
import aiohttp
import logging
import os
import hashlib
import json
import re
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.config.constants import CLIENT_API_PORT
from backend.core.models.ws_events import SatelliteUpdateVerdict
from backend.core.multiroom.satellite_http import SatelliteSecret, SatelliteSecretUnavailable
from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors

MILO_REPO_DIR = Path("/home/milo/milo")
MILO_CLIENT_DIR = MILO_REPO_DIR / "milo-client"

# Patterns to exclude from the client tarball. `venv` is the satellite's own,
# built at install time and symlinked into the repo dir — shipping the server's
# copy meant sending 67 MB the satellite extracted and threw away, over 99 % of
# every app update's payload.
TARBALL_EXCLUDE_PATTERNS = {"__pycache__", ".pyc", ".pytest_cache", "tests", ".git", "venv"}

# What the payload fingerprint covers: the trees a push installs (the app swaps
# in app/ and configs/, its deploy script system/ and rootfs/), less the tests
# the tarball leaves out. Spelled identically in
# pi-gen/stage-milo/02-install-milo/00-run.sh, which seeds a flashed card's.
PAYLOAD_PATHSPEC = (
    "milo-client/app",
    "milo-client/system",
    "milo-client/rootfs",
    "milo-client/configs",
    ":(exclude,glob)milo-client/**/tests/**",
)

# How long a pushed app may take to reach a final state once the satellite has
# answered the POST (or stopped deploying). The worst case is a version that
# crashes just before its hold ends on every try: MAX_ATTEMPTS starts, each a
# full TimeoutStartSec + TimeoutStopSec (a start that never gets READY is only
# killed once its stop times out) + HOLD_S + WatchdogSec (a hang just before the
# hold ends) + RestartSec — after a reboot, when a security fix waited for one
# and the room was silent. Held above that bound, read from the satellite's own
# source, by tests/architecture/test_satellite_app_ceiling.py.
APP_UPDATE_CEILING_S = 840
# How long the POST that carries a push may go unanswered. A refusal the
# satellite only reaches after it — dpkg busy past its wait — would be a silence
# the server waits out to the ceiling, so the satellite's wait stays well under
# this (the same test holds it).
APP_PUSH_TIMEOUT_S = 120
APP_UPDATE_POLL_S = 5
# A POST that got no answer may still be deploying (pip on a Pi takes minutes):
# while the satellite says so the ceiling restarts, up to this much more.
APP_DEPLOY_CEILING_S = 600
# Polls in a row a satellite whose POST got no answer must look untouched —
# never seen deploying, no trial, same process — before that reads as "it never
# started the update". At 5 s apart, longer than the 2 s restart delay.
APP_UNTOUCHED_POLLS = 3

APP_FAILURES_KEY = "updates.satellite_app_failures"

# A satellite answers a push 409 when dpkg stays held past its own wait
# (unattended-upgrades runs on every unit, and a speaker plugged back in after
# days runs it at once), with a refusal that starts with this — the satellite's
# `AppUpdateService._dpkg_busy`, read across releases, so held equal to it by
# test_satellite_updates.py. Nothing installed and nothing restarted, and
# nothing else pushes a satellite that stays online: its admission push is
# tried again this much later, at most this many times, each time through the
# same check of whether one is still owed.
DPKG_BUSY_REFUSAL = "dpkg is busy"
ADMISSION_DPKG_RETRY_S = 600
ADMISSION_DPKG_RETRIES = 3

# How long a snapclient or CamillaDSP install may run on a satellite once it
# answered `started`. The satellite's own steps are bounded: its download by
# DOWNLOAD_TIMEOUT_S (services/release_download.py), snapclient's wait for apt
# before it stops the unit by APT_LOCK_WAIT_S (services/snapclient.py),
# snapclient's `apt-get update` by APT_UPDATE_TIMEOUT_S
# (milo-client-install-snapclient), then dpkg and the service's restart. While the satellite says the install runs the
# server waits, up to this; held above the satellite's budget, read from its
# source, by tests/architecture/test_satellite_program_ceiling.py.
PROGRAM_UPDATE_CEILING_S = 900
PROGRAM_UPDATE_POLL_S = 5
# The satellite's app serves its API through a snapclient or CamillaDSP
# install, which restarts neither: this long without an answer is a unit that
# went away (unplugged, rebooted), not one still installing.
PROGRAM_SILENCE_S = 60
# The POST that starts one: the satellite reads its installed version (5 s at
# most) and answers before it begins.
PROGRAM_POST_TIMEOUT_S = 30

# The row draws one raw error line, as long as the satellite's guard keeps its
# own (DETAIL_MAX): a refusal can carry pages of pip resolver output.
DETAIL_MAX = 500
_FAILURE_LINE_RE = re.compile(r"^(?:ERROR\b|[A-Za-z_][\w.]*(?:Error|Exception)\b)")


def _describe_error(e: BaseException) -> str:
    """aiohttp's TimeoutError has an empty str(); the type is the message."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def _one_line(text: str) -> str:
    """The line that says why: the last one naming an error, else the last."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    chosen = next((line for line in reversed(lines) if _FAILURE_LINE_RE.search(line)), lines[-1])
    return chosen[:DETAIL_MAX]


def _refusal_text(text: str) -> str:
    """The satellite's own reason: FastAPI answers `{"detail": ...}`."""
    with contextlib.suppress(ValueError):
        body = json.loads(text)
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            return _one_line(body["detail"])
    return _one_line(text)


def _settled_on(payload: Optional[str], satellite: Dict[str, Any]) -> bool:
    """Whether an answering satellite runs `payload` with its trial over and
    committed — or with no record at all, the push the old updater applied.
    The same reading as the waiter's `committed`."""
    if not payload or not satellite.get("online") or satellite.get("app_payload") != payload:
        return False
    if satellite.get("app_trial"):
        return False
    last = satellite.get("app_last_update")
    return last is None or (
        isinstance(last, dict) and last.get("payload") == payload
        and last.get("outcome") == "committed"
    )


def _program_result(
    verdict: SatelliteUpdateVerdict,
    detail: Optional[str] = None,
    *,
    error: Optional[str] = None,
    version: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """One snapclient or CamillaDSP install's result: the app push's `verdict`
    and `detail`, `new_version` when it landed, `error` when it did not.

    `error` is the failure's one ERROR line, logged by the route that asked
    for the install, so the flow logs none of its own."""
    result: Dict[str, Any] = {
        "success": verdict == "committed",
        "verdict": verdict,
        "detail": _one_line(detail) if detail else None,
    }
    if verdict == "committed":
        result["new_version"] = version
        result["message"] = message
    else:
        result["error"] = error or detail or verdict
    return result


# The callable the programs router hands in, through which the admission's
# catch-up runs: it takes the mac and the check that decides whether a push is
# owed, and runs the push under the router's update claim, announced as the
# row's button announces one.
AdmissionPushRunner = Callable[[str, Callable[[], Awaitable[bool]]], Awaitable[None]]


class SatelliteUpdateService:
    """Service to manage satellites and their updates.

    Discovers satellites via ClientRegistryService (non-local clients)
    and identifies them by mac_id.
    """

    def __init__(self, snapcast_service, client_registry_service, settings_service,
                 satellite_secret: Optional["SatelliteSecret"] = None):
        self.snapcast_service = snapcast_service
        self.client_registry_service = client_registry_service
        self.settings_service = settings_service
        # What the app push carries for a satellite to take it: without it,
        # every push is refused (`_push_app`), never sent bare.
        self._satellite_secret = satellite_secret
        self.logger = logging.getLogger(__name__)
        self.satellite_api_port = CLIENT_API_PORT
        self._failures_lock = asyncio.Lock()
        self._admission_push_runner: Optional[AdmissionPushRunner] = None
        # The admission pushes (`catch_up_app`): owned here, not by the
        # multiroom connection that admits the satellite, whose task set a
        # multiroom toggle cancels — a push takes minutes, and one cut there
        # left nothing to say how it ended.
        self._bg = BackgroundTaskSet(self.logger, "satellite_updates")
        # The satellites whose admission push waits to be tried again because
        # dpkg was busy on them (`_catch_up`).
        self._dpkg_waits: set[str] = set()

    async def cleanup(self) -> None:
        """Cancel the admission pushes still waiting or running; a push cut
        here is followed again from the satellite's status at the next start."""
        await self._bg.cancel_all()

    def set_admission_push_runner(self, runner: AdmissionPushRunner) -> None:
        """Who runs the push a satellite's admission owes it (`catch_up_app`):
        the programs router, which owns the update claim every app push takes."""
        self._admission_push_runner = runner

    @handle_errors(default=[])
    async def discover_satellites(self, *, include_offline: bool = False) -> List[Dict[str, Any]]:
        """Discovers active satellites on the network via the client registry.

        By default only probes online, non-local clients, so the programs panel
        does not wait out a timeout per unplugged speaker. Probes run in parallel.

        `online` means Snapcast admitted the client, not that its API answers:
        every boot reloads the registry offline and direct mode admits nobody.
        A push of the client app passes `include_offline=True`, or after the
        reboot that ends a Milō update it would skip satellites that answer.
        """
        all_clients = self.client_registry_service.get_all_clients()

        candidates = [
            (mac_id, client) for mac_id, client in all_clients.items()
            if not client.is_local and client.ip and (include_offline or client.online)
        ]

        if not candidates:
            return []

        tasks = [self._check_satellite_api(client.ip) for _mac_id, client in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        satellites = []
        for (mac_id, client), result in zip(candidates, results):
            if isinstance(result, Exception) or not result.get("online"):
                continue
            satellites.append({
                "mac_id": mac_id,
                "hostname": client.host,
                "display_name": client.name or mac_id,
                "ip": client.ip,
                "snapclient_version": result.get("version"),
                "app_version": result.get("app_version"),
                "app_payload": result.get("app_payload"),
                "app_trial": result.get("app_trial", False),
                "app_last_update": result.get("app_last_update"),
                "app_started_at": result.get("app_started_at"),
                "camilladsp_version": result.get("camilladsp_version"),
                "online": True,
                "uptime": result.get("uptime"),
                "snapclient_running": result.get("running", False)
            })

        self.logger.info(f"Discovered {len(satellites)} satellites")
        return satellites

    @handle_errors(default={"online": False}, level='debug')
    async def _check_satellite_api(self, ip: str) -> Dict[str, Any]:
        """Checks if a satellite API responds and retrieves its info."""
        url = f"http://{ip}:{self.satellite_api_port}/status"

        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()

                    return {
                        "online": True,
                        "version": data.get("snapclient", {}).get("version"),
                        "running": data.get("snapclient", {}).get("running", False),
                        "uptime": data.get("uptime"),
                        "app_version": data.get("app", {}).get("version"),
                        "app_payload": data.get("app", {}).get("payload"),
                        "app_trial": bool(data.get("app", {}).get("trial")),
                        "app_last_update": data.get("app", {}).get("last_update"),
                        "app_started_at": data.get("app", {}).get("started_at"),
                        "camilladsp_version": data.get("camilladsp", {}).get("version")
                    }

        return {"online": False}

    async def update_satellite(
        self,
        mac_id: str,
        target_version: str,
        sha256: Optional[str],
    ) -> Dict[str, Any]:
        """Installs `target_version` of snapclient on a satellite.

        The version is resolved on the server, against the same manifest — and
        the same trial — the server itself runs on. A satellite carries neither,
        so deciding it there is how a client lands on a release nobody validated.
        `sha256` is the manifest's digest of the .deb, which the satellite's
        installer checks before dpkg runs; None for a version past the manifest.

        The result carries `verdict` and `detail`, as an app push's does.
        """
        return await self._install_program(
            mac_id, target_version, sha256,
            label="snapclient", route="update", status_key="snapclient",
        )

    async def update_satellite_camilladsp(
        self,
        mac_id: str,
        target_version: str,
        sha256: Optional[str],
    ) -> Dict[str, Any]:
        """Installs `target_version` of CamillaDSP on a satellite.

        Server-resolved for the same reason as the snapclient update above, and
        `sha256` with it: the manifest's digest of the release archive.
        """
        return await self._install_program(
            mac_id, target_version, sha256,
            label="CamillaDSP", route="camilladsp/update", status_key="camilladsp",
        )

    async def _install_program(
        self,
        mac_id: str,
        target_version: str,
        sha256: Optional[str],
        *,
        label: str,
        route: str,
        status_key: str,
    ) -> Dict[str, Any]:
        """One install of a program besides the app: `route` is where the
        satellite takes it (its status under `/<route>/status`), and
        `status_key` the block of its `/status` that names the version."""
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return _program_result("rejected", error=f"Satellite {mac_id} not found or offline")

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/{route}"

            timeout = aiohttp.ClientTimeout(total=PROGRAM_POST_TIMEOUT_S)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, json={"target_version": target_version, "sha256": sha256}
                ) as response:
                    if response.status != 200:
                        detail = f"HTTP {response.status} - {_refusal_text(await response.text())}"
                        return _program_result(
                            "rejected", detail, error=f"Satellite rejected the {label} update: {detail}",
                        )
                    data = await response.json()

        except Exception as e:
            detail = _describe_error(e)
            return _program_result(
                "rejected", detail, error=f"Error updating {label} on satellite {mac_id}: {detail}",
            )

        # `started` is the satellite's answer to "did I begin one" — false is
        # the legitimate already-up-to-date branch, not a failure. What it would
        # install is no longer in question: it is what this call named, so the
        # satellite's own version is what separates the two readings of `false`.
        if data.get("started"):
            try:
                return await self._wait_for_program_update(
                    mac_id, ip, target_version,
                    label=label, route=route, status_key=status_key,
                )
            except Exception as e:
                detail = _describe_error(e)
                return _program_result(
                    "did_not_return", detail,
                    error=f"Following satellite {mac_id}'s {label} update failed: {detail}",
                )
        if data.get("current_version") == target_version:
            # Nothing to do, and reporting it as a failure was self-perpetuating:
            # the UI refetches the inventory only on success, so the stale row
            # that offered the button kept offering it, and every press failed
            # the same way until the page was reloaded.
            return _program_result(
                "committed", version=target_version,
                message=f"Satellite {mac_id} already runs {label} {target_version}",
            )
        return _program_result("rejected", data.get("message") or "Update failed")

    @staticmethod
    async def _json_of(request) -> Dict[str, Any]:
        """The JSON object one satellite request answers, or an error naming why not."""
        async with request as response:
            if response.status != 200:
                raise RuntimeError(f"answered HTTP {response.status}")
            data = await response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"answered {data!r}")
            return data

    async def _wait_for_program_update(
        self,
        mac_id: str,
        ip: str,
        target_version: str,
        *,
        label: str,
        route: str,
        status_key: str,
    ) -> Dict[str, Any]:
        """Follows the install on the satellite until it ends, then checks it landed.

        `update_in_progress` going false says the attempt ended, not that it
        worked — a failed download and a successful install clear it alike. Only
        the version the satellite now reports separates the two, and its own
        error line (`last_result`) says why one did not.

        The satellite's word is what ends the wait: while it says it installs,
        the server waits, up to PROGRAM_UPDATE_CEILING_S. A fixed 180 s used to
        report "timeout" for an install the satellite was allowed to run past
        300 s, which then landed under a row that said it had failed.
        `elapsed` counts the sleeps, so the real wait is never shorter.
        """
        elapsed = 0
        silent = 0
        last_seen = "no answer yet"

        while elapsed < PROGRAM_UPDATE_CEILING_S:
            await asyncio.sleep(PROGRAM_UPDATE_POLL_S)
            elapsed += PROGRAM_UPDATE_POLL_S
            ip = self._registry_ip(mac_id) or ip
            state_url = f"http://{ip}:{self.satellite_api_port}/{route}/status"
            status_url = f"http://{ip}:{self.satellite_api_port}/status"

            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    state = await self._json_of(session.get(state_url))
                    if state.get("update_in_progress"):
                        silent = 0
                        last_seen = f"still installing {label} {target_version}"
                        continue
                    status = await self._json_of(session.get(status_url))
            except Exception as e:
                silent += PROGRAM_UPDATE_POLL_S
                last_seen = _describe_error(e)
                self.logger.debug(f"Waiting for the {label} update on {mac_id}: {last_seen}")
                if silent >= PROGRAM_SILENCE_S:
                    break
                continue

            reported = status.get(status_key)
            version = reported.get("version") if isinstance(reported, dict) else None
            if version == target_version:
                return _program_result(
                    "committed", version=version,
                    message=f"Satellite {mac_id} {label} updated successfully",
                )
            last = state.get("last_result")
            detail = (
                last.get("error")
                if isinstance(last, dict) and last.get("target_version") == target_version
                else None
            ) or f"still runs {version or 'no readable version'}"
            return _program_result(
                "rolled_back", detail,
                error=f"Satellite {mac_id} still runs {label} {version}, expected {target_version}: {detail}",
            )

        return _program_result(
            "did_not_return", last_seen,
            error=f"{label} update timeout for {mac_id}: no final state for {target_version} "
                  f"after {elapsed} s ({last_seen})",
        )

    async def _git(self, *args: str) -> Optional[str]:
        """Runs one git command in the repo; None when git itself failed."""
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(MILO_REPO_DIR), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return stdout.decode().strip()

    async def get_client_payload_version(self) -> Optional[str]:
        """Fingerprint of what a satellite update actually installs: the full
        hash of the commit that last touched `PAYLOAD_PATHSPEC`.

        Full, because it is compared as a string and `%h` is not a function of
        the commit: its length follows `core.abbrev=auto`, so the same commit
        reads a character longer once the object store passes 65 535 objects,
        and the fetch that crosses it would offer every satellite a push of the
        code it already runs.

        Two different questions are asked of a satellite, and one string used to
        answer both — badly. *Which version does it run* is displayed, and comes
        from `get_server_version`. *Does its code differ from the server's* is
        this, and it is what decides whether to push.

        Deciding on the release lit the update button on the whole fleet every
        time the server moved — measured once at 100 commits, 0 of them
        touching `milo-client/` — and each press deployed a byte-identical
        payload while restarting a speaker in an occupied room. Deciding on the
        payload leaves the button lit for exactly one case: a satellite that was
        offline while the server updated, which is the case it is for.

        Uncommitted work in the directory is payload no commit names, hence the
        `-dirty` suffix: without it a satellite change under test can never be
        pushed from the UI.

        That suffix carries a digest of the uncommitted content, and it has to.
        A bare `-dirty` is a flag, not a fingerprint: it reads the same before
        and after the working tree changes, so the *first* push of a change
        under test worked and every later one was reported as already applied.
        Measured on the fleet -- both satellites answered `081c8316-dirty`,
        "up to date" in the UI, while neither carried the route added to that
        directory half an hour earlier.
        """
        commit = await self._git("log", "-1", "--format=%H", "--", *PAYLOAD_PATHSPEC)
        if not commit:
            return None

        # `-uall` and `-z` are both load-bearing. Without `-uall`, git collapses a
        # brand-new directory into a single `?? dir/` entry, `read_bytes()` on it
        # raises IsADirectoryError, the suppress below eats it, and the digest is
        # identical across every edit inside that module -- which is the exact
        # case this fingerprint exists to catch. Without `-z`, git quotes paths
        # holding accents or spaces, and the quoted name reads as a missing file.
        dirty = await self._git(
            "status", "--porcelain", "--untracked-files=all", "-z", "--", *PAYLOAD_PATHSPEC
        )
        if not dirty:
            return commit

        digest = hashlib.sha256()
        digest.update((await self._git("diff", "HEAD", "--", *PAYLOAD_PATHSPEC) or "").encode())
        for entry in dirty.split("\0"):
            # Untracked files are absent from the diff, and a brand new module is
            # the most common shape of satellite work under test.
            if entry.startswith("?? "):
                path = MILO_REPO_DIR / entry[3:]
                with contextlib.suppress(OSError):
                    digest.update(await asyncio.to_thread(path.read_bytes))
        return f"{commit}-dirty.{digest.hexdigest()[:8]}"

    async def get_server_version(self) -> Optional[str]:
        """The version string the server shows for itself, which is the one a
        satellite carries.

        Both halves ship from one commit, so a satellite that took this server's
        payload runs this version — there is no third thing it could be, and
        giving it a numbering of its own is how a fleet comes to show four
        version schemes on one screen.

        Exactly `git describe --tags --always`: the tag when the server sits on
        one, the descriptive `<tag>-<n>-g<sha>` when it does not, and never
        empty on a checkout. Deliberately NOT `--exact-match`, which answers
        "is this a release" — a question the *offer* asks, and a satellite row
        never does. Asked here it turned every satellite pushed from a
        development tree into a blank cell, indistinguishable from one that had
        not answered at all. Measured on the two units: both rows empty while
        both satellites were up and reachable.
        """
        return await self._git("describe", "--tags", "--always")

    async def running_build(self) -> Dict[str, Optional[str]]:
        """What this server runs, for the logs page's "Copy for correction":
        `version`, the one string `get_server_version` gives (so it reads
        exactly as Programs and every satellite row show it), and `commit`,
        HEAD's full hash. Each None when git could not say."""
        async def read(what: str, ask: Callable[[], Awaitable[Optional[str]]]) -> Optional[str]:
            try:
                return await ask() or None
            except OSError as e:
                self.logger.warning(f"{what} could not be read: {e}")
                return None

        version, commit = await asyncio.gather(
            read("the server's version", self.get_server_version),
            read("HEAD", lambda: self._git("rev-parse", "HEAD")),
        )
        return {"version": version, "commit": commit}

    async def _create_client_tarball(self) -> tuple:
        """Creates a tarball of the milo-client/ directory.

        Returns (tarball_path, payload) tuple.
        """
        payload = await self.get_client_payload_version()
        if not payload:
            raise RuntimeError("Could not determine the milo-client payload version")

        if not MILO_CLIENT_DIR.is_dir():
            raise RuntimeError(f"milo-client directory not found: {MILO_CLIENT_DIR}")

        def _should_exclude(tarinfo):
            name = tarinfo.name
            for pattern in TARBALL_EXCLUDE_PATTERNS:
                if pattern.startswith("."):
                    # Extension match (e.g. .pyc)
                    if name.endswith(pattern):
                        return True
                else:
                    # Directory/file name match
                    parts = Path(name).parts
                    if pattern in parts:
                        return True
            return False

        def _filter(tarinfo):
            if _should_exclude(tarinfo):
                return None
            return tarinfo

        def _create():
            fd, tarball_path = tempfile.mkstemp(suffix=".tar.gz", prefix="milo-client-")
            os.close(fd)
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(str(MILO_CLIENT_DIR), arcname="milo-client", filter=_filter)
            return tarball_path

        tarball_path = await asyncio.get_running_loop().run_in_executor(None, _create)
        self.logger.info(f"Created client tarball: {tarball_path} (payload: {payload})")
        return tarball_path, payload

    async def update_satellite_app(
        self,
        mac_id: str
    ) -> Dict[str, Any]:
        """Pushes the client app to one satellite and follows it to a verdict.

        The result carries `verdict` (`SatelliteUpdateVerdict`) and `detail`, the
        satellite's own error line when it has one. Every verdict is recorded:
        anything but `committed` stays in `updates.satellite_app_failures`
        until this satellite's next committed push, so a satellite that failed
        and stopped answering still shows why on its row.
        """
        result = await self._push_app(mac_id)
        if result.get("payload") is None:
            # Nothing was built or sent: the record already there, the
            # satellite's own error line included, stays what the row shows.
            return result
        try:
            await self._record_app_outcome(mac_id, result)
        except Exception as e:
            self.logger.error(
                f"Satellite {mac_id}: the app push's {result['verdict']} could not be "
                f"recorded ({_describe_error(e)}); its row will not show it"
            )
        return result

    async def catch_up_app(self, mac_id: str) -> None:
        """Bring a satellite that was just admitted to this server's client app.

        A Milō update pushes the app once, to the satellites that answer then
        (`push_client_app_to_fleet`). One unplugged during it keeps the old app,
        and from then on the server sends it request keys its handlers do not
        read: its EQ or its level stop answering, with no error anywhere. Its
        admission is the moment it is back, so it is pushed then — through the
        programs router's runner, under the same claim as its row's button and
        announced the same way, so a Milō update or another app push in flight
        holds it back until it ends, and the row shows it running and how it
        ended. One refused because dpkg was busy on the satellite is tried
        again a few times, minutes apart (`_catch_up`).

        Called by the admission once the client counts as online, after the
        hand-over of the secret the push carries. Returns at once: the push
        runs in this service's own task set, which only the backend's teardown
        cancels. Never raises.
        """
        self._spawn_catch_up(mac_id)

    def _spawn_catch_up(self, mac_id: str) -> None:
        """`catch_up_app`'s spawn, for a caller that cannot await."""
        if self._admission_push_runner is None or mac_id in self._dpkg_waits:
            # A re-admission during a dpkg retry's wait adds nothing: the
            # retry asks again whether a push is owed when it ends.
            return
        self._bg.spawn(self._catch_up(mac_id), label=f"catch_up_app_{mac_id}")

    async def _catch_up(self, mac_id: str) -> None:
        """The admission's push, and its retries while the satellite answers
        that dpkg is busy: a refusal that installed nothing, with nothing else
        to push a satellite that stays online. Every try asks `_catch_up_owed`
        again, through the runner, so a satellite that went away, took the
        payload meanwhile or opened a trial is left alone."""
        for attempt in range(ADMISSION_DPKG_RETRIES + 1):
            decided: List[bool] = []

            async def owed() -> bool:
                decided.append(await self._catch_up_owed(mac_id))
                return decided[-1]

            try:
                await self._admission_push_runner(mac_id, owed)
            except Exception as e:
                self.logger.error(
                    f"Satellite {mac_id}: the client app catch-up at its admission failed: "
                    f"{_describe_error(e)}"
                )
                return
            # The runner pushed iff the last check it made said so.
            if not decided or not decided[-1] or not await self._refused_for_dpkg(mac_id):
                return
            if attempt == ADMISSION_DPKG_RETRIES:
                self.logger.warning(
                    f"Satellite {mac_id}: dpkg was still busy after {attempt + 1} client app "
                    "pushes at its admission; its own row offers the push"
                )
                return
            self.logger.info(
                f"Satellite {mac_id}: dpkg is busy on it, its client app push is tried "
                f"again in {ADMISSION_DPKG_RETRY_S // 60} min"
            )
            self._dpkg_waits.add(mac_id)
            try:
                await asyncio.sleep(ADMISSION_DPKG_RETRY_S)
            finally:
                self._dpkg_waits.discard(mac_id)

    async def _refused_for_dpkg(self, mac_id: str) -> bool:
        """Whether the push just recorded for this satellite was refused because
        dpkg was busy on it — a 409 that installed nothing, unlike the 409 of a
        trial still open, which a retry would only meet again."""
        record = (await self.get_app_update_failures()).get(mac_id)
        return (
            isinstance(record, dict) and record.get("verdict") == "rejected"
            and str(record.get("detail") or "").startswith(f"HTTP 409 - {DPKG_BUSY_REFUSAL}")
        )

    def _pushable(self, mac_id: str, client: Any, when: str) -> bool:
        """Whether this registry client is one this server pushes its client
        app to, by the fleet push and by the admission alike: a satellite, not
        the server's own client, and one that holds this server's secret. A
        satellite that pinned another server's refuses every push (403) until
        it is unplugged and plugged back in, and Settings › Multiroom already
        names it, so it is no failure to record or report."""
        if client is None or client.is_local:
            return False
        if client.other_server is True:
            self.logger.info(
                f"{client.name or mac_id}: answers another server, so no client app push {when}"
            )
            return False
        return True

    @staticmethod
    def _follows_this_server(client: Any) -> bool:
        """`_pushable`'s answer, with nothing logged."""
        return client is not None and not client.is_local and client.other_server is not True

    @staticmethod
    def _holds_back_fleet(record: Any, payload: str) -> bool:
        """Whether one satellite's recorded failure holds every other satellite
        back from `payload`: it took that payload and went back from it, or did
        not come back from it. A release that breaks one room breaks them all,
        and a unit that does not come back is the worse sign — its network or
        its boot broken, which its guard does not always undo. A `rejected`
        push restarted nothing and holds nothing.

        The hold lifts by itself: the record goes once that satellite settles
        on the payload (`_reconcile_app_failures`, or a push of it that
        commits), and the satellites it held back are pushed then
        (`_release_held`); the next payload starts clean. Read by the fleet
        push and the admission's catch-up alike."""
        return (
            isinstance(record, dict) and record.get("payload") == payload
            and record.get("verdict") in ("rolled_back", "did_not_return")
        )

    def _fleet_holder(
        self, failures: Dict[str, Any], payload: str, mac_id: str,
    ) -> Optional[tuple]:
        """`(mac, record)` of another satellite whose failure holds `mac_id`
        back from `payload`, or None.

        Only a satellite this server pushes holds the others: one removed from
        the registry, or one that answers another server, can never come back
        on this payload here, so its record would hold the fleet for good."""
        clients = self.client_registry_service.get_all_clients()
        return next(
            ((mac, record) for mac, record in failures.items()
             if mac != mac_id and self._holds_back_fleet(record, payload)
             and self._follows_this_server(clients.get(mac))),
            None,
        )

    def _release_held(self, lifted: List[str]) -> None:
        """Push the satellites a lifted hold kept back. A satellite that stays
        online is admitted once, so nothing else would push it the app it was
        held back from: each other online one is handed to the admission's
        catch-up, which asks again whether a push is owed and waits for any
        claim in flight."""
        for mac, client in self.client_registry_service.get_all_clients().items():
            if mac not in lifted and client.online and self._follows_this_server(client):
                self._spawn_catch_up(mac)

    async def _catch_up_owed(self, mac_id: str) -> bool:
        """Whether an admitted satellite is owed a push of the client app: it
        answers, its payload differs from this server's, no trial is open on it,
        it holds this server's secret, it refused this payload neither to a
        push the server recorded nor by a rollback of its own, and no other
        satellite's failure holds the fleet back from it (`_holds_back_fleet`,
        the fleet push's halt)."""
        client = self.client_registry_service.get_all_clients().get(mac_id)
        if not self._pushable(mac_id, client, "at its admission") or not client.ip:
            return False
        name = client.name or mac_id
        payload = await self.get_client_payload_version()
        if not payload:
            self.logger.error(
                f"{name}: client app not pushed at its admission, the milo-client "
                "payload version is unreadable"
            )
            return False
        probe = await self._check_satellite_api(client.ip)
        if not probe.get("online"):
            return False
        satellite = {
            "mac_id": mac_id,
            "online": True,
            "app_payload": probe.get("app_payload"),
            "app_trial": bool(probe.get("app_trial")),
            "app_last_update": probe.get("app_last_update"),
        }
        # Read before the answer below: a satellite back on the payload it did
        # not come back from drops its own failure here, and with it the hold
        # that failure puts on the others.
        failures = await self._reconcile_app_failures([satellite], payload)
        if probe.get("app_payload") == payload:
            return False
        if probe.get("app_trial"):
            self.logger.info(f"{name}: on trial of a client app, not pushed {payload} at its admission")
            return False
        failure = failures.get(mac_id)
        refused = (
            isinstance(failure, dict) and failure.get("payload") == payload and failure.get("refused")
        )
        if refused or self._rolled_back_before(payload, failure, probe.get("app_last_update")):
            self.logger.info(
                f"{name}: did not take client app {payload} before, not pushed it again at its "
                "admission. Its own row offers the retry."
            )
            return False
        # The fleet push's halt, by the same rule: another satellite that went
        # back from this payload, or did not come back from it, holds this one
        # back with its own app.
        holder = self._fleet_holder(failures, payload, mac_id)
        if holder is not None:
            other = self.client_registry_service.get_all_clients().get(holder[0])
            how = ("went back from it" if holder[1].get("verdict") == "rolled_back"
                   else "did not come back from it")
            self.logger.info(
                f"{name}: client app {payload} held back at its admission, "
                f"{(other.name if other else None) or holder[0]} {how}. "
                "Its own row offers the push."
            )
            return False
        self.logger.info(
            f"{name}: runs client app {probe.get('app_payload')}, this server's is {payload}; "
            "pushing it at its admission"
        )
        return True

    def _app_result(
        self,
        verdict: SatelliteUpdateVerdict,
        payload: Optional[str],
        detail: Optional[str] = None,
        error: Optional[str] = None,
        refused: bool = False,
    ) -> Dict[str, Any]:
        """`refused` is whether the satellite itself said no to this payload — a
        refusal it answered, or a rollback — as opposed to a push that never
        reached it or a wait that saw no end. Recorded with the failure, it is
        what holds back the push the satellite's next admission would make
        (`catch_up_app`): the same payload pushed at every reconnect would cut
        the room each time for an answer already given."""
        result: Dict[str, Any] = {
            "success": verdict == "committed",
            "verdict": verdict,
            "detail": _one_line(detail) if detail else None,
            "payload": payload,
            "refused": refused or verdict == "rolled_back",
        }
        if verdict == "committed":
            result["new_version"] = payload
        else:
            result["error"] = error or detail or verdict
        return result

    async def _push_app(self, mac_id: str) -> Dict[str, Any]:
        tarball_path = None
        payload = None
        try:
            satellites = await self.discover_satellites(include_offline=True)
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                # Nothing was sent, so nothing restarted.
                return self._app_result(
                    "rejected", None,
                    error=f"Satellite {mac_id} not found or offline",
                )

            ip = satellite["ip"]
            if self._satellite_secret is None:
                raise SatelliteSecretUnavailable("no satellite key is wired into this server")

            tarball_path, payload = await self._create_client_tarball()
            version = await self.get_server_version() or ""

            url = f"http://{ip}:{self.satellite_api_port}/app/update"
            timeout = aiohttp.ClientTimeout(total=APP_PUSH_TIMEOUT_S)
            unanswered = None

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    with open(tarball_path, "rb") as f:
                        form = aiohttp.FormData()
                        form.add_field("tarball", f, filename="milo-client.tar.gz",
                                       content_type="application/gzip")
                        form.add_field("payload", payload)
                        form.add_field("version", version)
                        # The satellite installs this as root: it takes it from
                        # the server it pinned only (satellite_http.py).
                        await self._satellite_secret.sign(form, mac_id)

                        async with session.post(url, data=form) as response:
                            if response.status != 200:
                                detail = f"HTTP {response.status} - {_refusal_text(await response.text())}"
                                return self._app_result(
                                    "rejected", payload, detail,
                                    error=f"Satellite rejected update: {detail}",
                                    # A 409 is the satellite busy — a trial open,
                                    # a push already deploying —, and a 403 the
                                    # secret of a server it does not follow (it
                                    # takes this one's again once unplugged and
                                    # plugged back in): neither is an answer
                                    # about this payload.
                                    refused=response.status not in (403, 409),
                                )
            except aiohttp.ClientConnectorError as e:
                # Never connected, so nothing was sent and nothing restarted.
                detail = _describe_error(e)
                return self._app_result(
                    "rejected", payload, detail,
                    error=f"Satellite {mac_id} could not be reached: {detail}",
                )
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
                # The upload may have landed and the deploy still be running
                # (pip on a Pi outlasts the POST's budget): only the satellite's
                # /status can say what became of it.
                unanswered = _describe_error(e)
                self.logger.warning(
                    f"Satellite {mac_id}: no answer to the app push ({unanswered}); "
                    "following its status"
                )

        except Exception as e:
            detail = _describe_error(e)
            # Raised before the POST, or by it before an answer: nothing reached
            # the satellite.
            return self._app_result(
                "rejected", payload, detail, error=f"Error updating satellite app {mac_id}: {detail}",
            )

        else:
            # Past the POST the satellite may be restarting into the push: an
            # error here is a wait that saw no final state, never a refusal.
            try:
                return await self._wait_for_app_update_completion(
                    mac_id, ip, payload,
                    baseline=satellite,
                    unanswered=unanswered,
                )
            except Exception as e:
                detail = _describe_error(e)
                return self._app_result(
                    "did_not_return", payload, detail,
                    error=f"Following satellite {mac_id}'s app update failed: {detail}",
                )

        finally:
            if tarball_path:
                with contextlib.suppress(OSError):
                    os.unlink(tarball_path)

    def _registry_ip(self, mac_id: str) -> Optional[str]:
        """Where the registry last saw this satellite: a unit that restarted may
        come back over its WiFi link, on another address."""
        client = self.client_registry_service.get_all_clients().get(mac_id)
        return getattr(client, "ip", None) if client is not None else None

    async def _read_app_status(self, ip: str) -> Dict[str, Any]:
        url = f"http://{ip}:{self.satellite_api_port}/status"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            try:
                data = await self._json_of(session.get(url))
            except RuntimeError as e:
                raise RuntimeError(f"/status {e}") from None
        app = data.get("app")
        if not isinstance(app, dict):
            raise RuntimeError(f"/status carries no app object: {app!r}")
        return app

    async def _wait_for_app_update_completion(
        self,
        mac_id: str,
        ip: str,
        payload: str,
        baseline: Dict[str, Any],
        unanswered: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Polls the satellite's /status until the push reaches a final state.

        A satellite on trial reports `trial` until its new process has held
        HOLD_S, then commits; its guard, after MAX_ATTEMPTS failed starts, puts
        the previous version back and records `rolled_back`. Both land in
        `last_update`, which is compared to the one the satellite reported
        before the push (`baseline`): a retry of a payload it once rolled back
        must not read that old record as this push's answer.

        `committed` also covers a satellite whose updater predates the trial
        (`last_update` null): the push that installs the guard is applied by
        the updater it replaces.

        A POST that got no answer is not a verdict. The satellite may still be
        deploying — while it says so the ceiling restarts — or may never have
        received it: untouched for APP_UNTOUCHED_POLLS polls in a row, never
        seen deploying, it is `rejected`, since nothing restarted.

        `elapsed` counts the sleeps, so the real wait is never shorter than the
        ceiling.
        """
        before = baseline.get("app_last_update")
        started_at = baseline.get("app_started_at")
        elapsed = 0
        deadline = APP_UPDATE_CEILING_S
        hard_deadline = APP_UPDATE_CEILING_S + APP_DEPLOY_CEILING_S
        seen_moving = unanswered is None
        untouched = 0
        last_seen = None
        seen_payload = baseline.get("app_payload")

        while elapsed < deadline:
            await asyncio.sleep(APP_UPDATE_POLL_S)
            elapsed += APP_UPDATE_POLL_S
            ip = self._registry_ip(mac_id) or ip

            try:
                app = await self._read_app_status(ip)
            except Exception as e:
                # Expected for a few seconds on every restart.
                last_seen = _describe_error(e)
                self.logger.debug(f"Waiting for satellite {mac_id}: {last_seen}")
                untouched = 0
                continue

            last = app.get("last_update")
            fresh = last if isinstance(last, dict) and last != before else None
            if fresh and fresh.get("payload") == payload and fresh.get("outcome") == "rolled_back":
                detail = fresh.get("detail")
                return self._app_result(
                    "rolled_back", payload, detail,
                    error=f"Satellite {mac_id} went back to its previous app"
                          + (f": {detail}" if detail else ""),
                )

            trial = bool(app.get("trial"))
            deploying = bool(app.get("update_in_progress"))
            seen_payload = app.get("payload")

            if seen_payload == payload and not trial and not deploying and (
                last is None
                or (fresh and fresh.get("payload") == payload
                    and fresh.get("outcome") == "committed")
            ):
                return self._app_result("committed", payload)

            if deploying:
                deadline = min(max(deadline, elapsed + APP_UPDATE_CEILING_S), hard_deadline)

            if not seen_moving:
                if deploying or trial or fresh or app.get("started_at") != started_at:
                    seen_moving = True
                else:
                    untouched += 1
                    if untouched >= APP_UNTOUCHED_POLLS:
                        detail = f"the satellite never started the update ({unanswered})"
                        return self._app_result(
                            "rejected", payload, detail, error=f"Satellite {mac_id}: {detail}",
                        )

            last_seen = (
                f"still on trial of {payload}" if trial and seen_payload == payload
                else f"still deploying {payload}" if deploying
                else f"still running {seen_payload}"
            )

        if seen_payload != payload:
            error = f"Satellite {mac_id} deployed the update but never restarted into it"
        else:
            error = f"Satellite {mac_id} did not settle on the update"
        return self._app_result(
            "did_not_return", payload, last_seen, error=f"{error} ({last_seen}, after {elapsed} s)",
        )

    async def get_app_update_failures(self) -> Dict[str, Dict[str, Any]]:
        """{mac: {payload, verdict, detail, at}}: the last app push each
        satellite did not take, kept until its next committed one."""
        stored = await self.settings_service.get_setting(APP_FAILURES_KEY)
        return dict(stored) if isinstance(stored, dict) else {}

    async def _record_app_outcome(self, mac_id: str, result: Dict[str, Any]) -> None:
        async with self._failures_lock:
            failures = await self.get_app_update_failures()
            lifted = False
            if result["verdict"] == "committed":
                if mac_id not in failures:
                    return
                lifted = self._holds_back_fleet(failures.pop(mac_id), result.get("payload"))
            else:
                failures[mac_id] = {
                    "payload": result.get("payload"),
                    "verdict": result["verdict"],
                    "detail": _one_line(result.get("detail") or result.get("error") or "") or None,
                    "at": int(time.time()),
                    "refused": bool(result.get("refused")),
                }
            if not await self.settings_service.set_setting(APP_FAILURES_KEY, failures):
                self.logger.error(
                    f"Satellite {mac_id}: the app push's {result['verdict']} could not be "
                    "recorded; its row will not show it"
                )
        if lifted:
            self._release_held([mac_id])

    async def _reconcile_app_failures(
        self, satellites: List[Dict[str, Any]], server_payload: Optional[str]
    ) -> Dict[str, Dict[str, Any]]:
        """The record, read against what the answering satellites report now.

        A failure is dropped once its satellite settled on that payload on its
        own: a wait that ended `did_not_return` while the unit held and
        committed, a 409 recorded while an earlier trial ran. And a rollback a
        satellite recorded itself, of the payload this server offers, is taken
        in where the server holds nothing about that payload — the wait that
        would have recorded it was cut, by a restart of this backend — or holds
        only that it did not come back from it: it came back, the old app.
        """
        async with self._failures_lock:
            failures = await self.get_app_update_failures()
            changed = False
            lifted: List[str] = []
            for satellite in satellites:
                mac = satellite["mac_id"]
                failure = failures.get(mac)
                if failure and _settled_on(failure.get("payload"), satellite):
                    del failures[mac]
                    changed = True
                    if server_payload and self._holds_back_fleet(failure, server_payload):
                        lifted.append(mac)
                    continue
                last = satellite.get("app_last_update")
                if (
                    server_payload and satellite.get("online") and isinstance(last, dict)
                    and last.get("payload") == server_payload
                    and last.get("outcome") == "rolled_back"
                    and satellite.get("app_payload") != server_payload
                    and (failure is None or failure.get("payload") != server_payload
                         or failure.get("verdict") == "did_not_return")
                ):
                    at = last.get("at")
                    failures[mac] = {
                        "payload": server_payload,
                        "verdict": "rolled_back",
                        "detail": _one_line(str(last.get("detail") or "")) or None,
                        "at": at if isinstance(at, int) else int(time.time()),
                        "refused": True,
                    }
                    changed = True
            if changed and not await self.settings_service.set_setting(APP_FAILURES_KEY, failures):
                self.logger.error("The satellites' app push failures could not be updated")
        if lifted:
            self._release_held(lifted)
        return failures

    @staticmethod
    def _rolled_back_before(payload: str, failure: Optional[dict], last_update: Optional[dict]) -> bool:
        """Whether this satellite already went back from `payload`, by the
        server's record or by its own."""
        return any(
            isinstance(record, dict) and record.get("payload") == payload
            and (record.get("verdict") or record.get("outcome")) == "rolled_back"
            for record in (failure, last_update)
        )

    async def list_satellites(self) -> List[Dict[str, Any]]:
        """The rows of the programs panel: every satellite that answers, and
        every one that does not but holds an app push it did not take — the
        failure would otherwise vanish with the unit it is about.

        Each row carries `app_update_failure`, the recorded failure or None,
        read against what the satellite reports now.
        """
        satellites, server_payload = await asyncio.gather(
            self.discover_satellites(), self.get_client_payload_version()
        )
        failures = await self._reconcile_app_failures(satellites, server_payload)
        listed = {s["mac_id"] for s in satellites}
        clients = self.client_registry_service.get_all_clients()
        for mac in failures:
            client = clients.get(mac)
            if mac in listed or client is None or client.is_local:
                continue
            satellites.append({
                "mac_id": mac,
                "hostname": client.host,
                "display_name": client.name or mac,
                "ip": client.ip,
                "snapclient_version": None,
                "app_version": None,
                "app_payload": None,
                "app_trial": False,
                "app_last_update": None,
                "app_started_at": None,
                "camilladsp_version": None,
                "online": False,
                "uptime": None,
                "snapclient_running": False,
            })
        for satellite in satellites:
            satellite["app_update_failure"] = failures.get(satellite["mac_id"])
        return satellites

    async def push_client_app_to_fleet(
        self, on_push: Optional[Callable[[str, int, int], Awaitable[None]]] = None,
    ) -> list[str]:
        """Bring every satellite to the `milo-client/` tree this server now has.

        A Milō update replaces that tree, so the whole fleet goes stale at once
        and deterministically. Pushing it here is the same sequence as the
        validated dependency set: one press updates the appliance, not the
        appliance and then N speakers one screen at a time. It runs once the
        new release has held (`UpdateService.run_post_commit`), so a release
        the server puts back has never reached a speaker.

        `on_push(name, index, total)` is awaited before each push, `total`
        counting the satellites this call means to push; a satellite held back
        by an earlier one's failure is never announced.

        One satellite at a time, and a satellite whose failure holds the fleet
        back (`_holds_back_fleet`: it went back from this payload or did not
        come back from it, in this push or recorded before) stops the push: the
        rest are held back with their own app, as the admission's catch-up
        holds them. A `rejected` push restarted nothing and stops nothing. A
        satellite that already went back from this payload is not pushed
        again — its own row offers the retry —, and one that answers another
        server is not pushed at all (`_pushable`).

        Never raises. It runs past the point where the app can still be rolled
        back, and an unplugged satellite is a fact to report, not a failed Milō
        update: its admission pushes it the app once it is back (`catch_up_app`),
        and its own row offers the push meanwhile.

        Returns what was left behind, named for the journal and the envelope —
        a satellite, or the reason nothing could be pushed at all.
        """
        left_behind: list[str] = []
        try:
            payload = await self.get_client_payload_version()
            if not payload:
                self.logger.error(
                    "Client app not pushed: the milo-client payload version is "
                    "unreadable. Every satellite keeps the app it runs."
                )
                return ["milo-client payload version unreadable"]

            remote = {
                mac: client
                for mac, client in self.client_registry_service.get_all_clients().items()
                if self._pushable(mac, client, "with the Milo update")
            }
            if not remote:
                return left_behind

            reachable = {
                s["mac_id"]: s for s in await self.discover_satellites(include_offline=True)
            }
            # The record as this push moves it: each verdict below replaces
            # its satellite's entry, so a failure here holds the rest back by
            # the same rule as one recorded before.
            failures = dict(await self._reconcile_app_failures(list(reachable.values()), payload))
            total = sum(
                1 for mac in remote
                if mac in reachable
                and reachable[mac].get("app_payload") != payload
                and not self._rolled_back_before(payload, failures.get(mac),
                                                 reachable[mac].get("app_last_update"))
            )
            index = 0

            for mac, client in remote.items():
                name = client.name or mac
                satellite = reachable.get(mac)

                if not satellite:
                    left_behind.append(name)
                    self.logger.error(
                        f"{name}: not answering its API, client app not pushed. "
                        "It is pushed the app at its next admission."
                    )
                    continue

                # Already running this tree: pushing would restart a speaker in
                # an occupied room to deploy bytes it already has.
                if satellite.get("app_payload") == payload:
                    continue

                if self._rolled_back_before(payload, failures.get(mac),
                                            satellite.get("app_last_update")):
                    left_behind.append(f"{name}: went back from {payload} before, not pushed again")
                    self.logger.error(
                        f"{name}: already went back from client app {payload}; not pushed "
                        "again. Its own row offers the retry."
                    )
                    continue

                holder = self._fleet_holder(failures, payload, mac)
                if holder is not None:
                    other = self.client_registry_service.get_all_clients().get(holder[0])
                    held_by = (other.name if other else None) or holder[0]
                    left_behind.append(f"{name}: held back, {held_by} did not take {payload}")
                    self.logger.error(
                        f"{name}: client app {payload} held back, {held_by} did not take it"
                    )
                    continue

                self.logger.info(f"{name}: pushing client app {payload} with the Milo update")
                index += 1
                if on_push is not None:
                    try:
                        await on_push(name, index, total)
                    except Exception as e:
                        self.logger.error(f"{name}: client app push not announced: {e}")
                result = await self.update_satellite_app(mac)
                if result.get("verdict") == "committed":
                    failures.pop(mac, None)
                else:
                    failures[mac] = {"payload": payload, "verdict": result.get("verdict")}
                if not result.get("success"):
                    left_behind.append(name)
                    self.logger.error(
                        f"{name}: client app push failed ({result.get('verdict')}: "
                        f"{result.get('error', 'unknown error')}). "
                        "The Milo update continues; the satellite keeps the app it runs."
                    )

        except Exception as e:
            self.logger.error(f"Pushing the client app to the fleet failed: {e}")
            left_behind.append(f"fleet push failed: {e}")

        return left_behind
