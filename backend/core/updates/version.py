# backend/core/updates/version.py
"""
Version management service - Version with GitHub token support
"""
import asyncio
import copy
import time
import aiohttp
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.helpers import compare_versions, is_stable_release
from backend.core.updates.release_signature import (
    MAX_MANIFEST_BYTES,
    SIGNATURE_SUFFIX,
    ReleaseManifest,
    UnverifiableRelease,
    verified_manifest,
)

# How long a failed GitHub read (the latest release, the release list, a
# release's signature) is remembered before it is tried again.
RELEASES_RETRY_S = 300
# The offer's verdict on one release's signature, cached per tag.
SIGNATURE_CACHE_PREFIX = "signature_"


class VersionService:
    """Simplified service to manage Milo program versions"""

    def __init__(self, settings_service):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service

        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.info("GitHub token detected - using authenticated API (5000 req/hour)")
        else:
            self.logger.info("No GitHub token - using anonymous API (60 req/hour)")

        # One entry per program, shared with UpdateService. Copied per instance so
        # overriding a "validated_version" at runtime cannot mutate the module constant.
        self.programs = copy.deepcopy(PROGRAMS)

        # Cache to avoid repeated GitHub calls
        self._github_cache = {}
        self._cache_timeout = 3600  # 1 hour
        self._last_github_fetch = {}
        # The GitHub read in flight per cache key, which a second caller joins.
        self._inflight: Dict[str, asyncio.Task] = {}

    def _get_github_headers(self) -> Dict[str, str]:
        """Returns headers for GitHub requests (with token if available)"""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Milo-Audio-System"
        }

        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        return headers

    async def get_installed_version(self, program_key: str) -> Dict[str, Any]:
        """Gets the installed version of a program"""
        if program_key not in self.programs:
            return {"status": "error", "message": "Unknown program"}

        program_config = self.programs[program_key]
        result = {
            "name": program_config["name"],
            "description": program_config["description"],
            "status": "unknown",
            "versions": {},
            "errors": []
        }

        has_git_path = "git_path" in program_config
        for cmd_name, cmd_args in program_config["commands"].items():
            try:
                raw_output, version = await self._execute_version_command(cmd_args, program_config["version_regex"])
                if version:
                    result["versions"][cmd_name] = version
                    result["status"] = "installed"
                    # For git-based programs, include the raw command output (e.g. "v0.0.1-533-gc6d74a1")
                    if has_git_path and raw_output:
                        result["raw_version"] = raw_output
                else:
                    result["errors"].append(f"{cmd_name}: Version not detected")
            except Exception as e:
                result["errors"].append(f"{cmd_name}: {str(e)}")

        # If no version detected, mark as not installed
        if not result["versions"]:
            result["status"] = "not_installed"

        return result

    async def _execute_version_command(self, cmd_args: List[str], version_regex: str) -> tuple:
        """Executes a version command and extracts the version number.

        Returns (raw_output, extracted_version) tuple.
        """
        try:
            # Short timeout to avoid blocking
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

                # Search for version in stdout then stderr
                output_text = stdout.decode() + stderr.decode()
                raw_output = output_text.strip()
                match = re.search(version_regex, output_text)

                if match:
                    return raw_output, match.group(1)

                # Fallback: search for common version patterns
                fallback_patterns = [
                    r"(\d+\.\d+\.\d+)",
                    r"version (\d+\.\d+\.\d+)",
                    r"Version: (\d+\.\d+\.\d+)"
                ]

                for pattern in fallback_patterns:
                    match = re.search(pattern, output_text, re.IGNORECASE)
                    if match:
                        return raw_output, match.group(1)

                return raw_output, None

            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise Exception("Command timeout")

        except FileNotFoundError:
            raise Exception("Command not found")
        except Exception as e:
            raise Exception(f"Execution error: {str(e)}")

    async def get_forced_versions(self) -> Dict[str, str]:
        """The versions this unit was deliberately moved to, past the manifest.

        Only an entry still *ahead* of what `dependencies.env` declares counts:
        bumping the manifest to the version that was forced makes the override
        redundant, and keeping it would hold the unit below the *next* bump —
        the "landed behind" failure the manifest exists to prevent, one program
        at a time. `UpdateService._prune_forced_versions` writes that reading
        back to disk.
        """
        # No `or {}`: `_validate_and_merge` emits the section unconditionally,
        # so a missing key is a broken settings.json and must surface as one.
        stored = await self.settings_service.get_setting("updates.forced_versions")
        return {
            key: version
            for key, version in stored.items()
            if key in self.programs
            and compare_versions(self.programs[key].get("validated_version"), version)
        }

    def _release_at(self, program_key: str, fetched_tag: str, version: str) -> Dict[str, Any]:
        """Describe a release the fetch did not return: its tag, its URL, no date.

        The tag is rebuilt in the repo's own convention — some tag "v1.2.3"
        (go-librespot, snapcast), others "1.2.3" (shairport-sync). Guessing wrong
        points the source download at a tag that doesn't exist. Derived from the
        fetched tag rather than restated in the manifest, which carries bare
        versions only. The publication date belongs to the fetched release and
        not to this one, so it is dropped rather than carried over.
        """
        prefix = "v" if fetched_tag.startswith("v") else ""
        repo = self.programs[program_key]["repo"]
        return {
            "version": version,
            "tag_name": f"{prefix}{version}",
            "html_url": f"https://github.com/{repo}/releases/tag/{prefix}{version}",
            "published_at": None,
        }

    async def _apply_pin(self, program_key: str, fetched: Dict[str, Any]) -> Dict[str, Any]:
        """Point the offered release at the version this unit is pinned to.

        Pin, not ceiling: what the manifest declares is what is offered, whether
        upstream is ahead of it or (a yanked release, a manifest bumped early)
        behind. A clamp would let an upstream release below the manifest
        through, which would make dependencies.env something less than the
        source of truth it is declared to be.

        A forced version outranks the manifest for as long as it stays ahead of
        it — that *is* "this unit is deliberately off-pin", and the `validated`
        block it adds is what the return button installs. `upstream` keeps what
        upstream actually offers: the clamp used to overwrite it, so the
        maintainer surface — the one place the decision to bump the set is taken
        — could not show what there was to bump to.
        """
        validated = self.programs[program_key].get("validated_version")
        if not validated:
            # milo: the app, not a dependency. It has no manifest line, so the
            # latest release is what it offers.
            return fetched

        forced = (await self.get_forced_versions()).get(program_key)
        target = forced or validated

        result = dict(fetched)
        result["upstream"] = {
            "version": fetched["version"],
            "tag_name": fetched["tag_name"],
            "published_at": fetched["published_at"],
            "html_url": fetched["html_url"],
            "ahead": compare_versions(target, fetched["version"]),
        }
        result.update(self._release_at(program_key, fetched["tag_name"], target))
        if forced:
            result["validated"] = self._release_at(program_key, fetched["tag_name"], validated)
        # The versions a trial may land on: past the manifest, up to what
        # upstream calls latest. Never below the manifest — a forced version is
        # by definition ahead of it, and `get_forced_versions` drops anything else.
        result["trials"] = [
            release for release in await self._fetch_releases(program_key)
            if compare_versions(validated, release["version"])
            and not compare_versions(fetched["version"], release["version"])
        ]
        return result

    async def get_latest_github_version(self, program_key: str) -> Dict[str, Any]:
        """The release this unit is meant to run, and what upstream has.

        The fetch is cached raw and the pin applied on every call: the pin is
        not a property of the fetch. A forced version is written while the cache
        is still warm, and a Milo update replaces the manifest under a process
        that read it minutes earlier.
        """
        fetched = await self._fetch_latest_release(program_key)
        if fetched.get("status") != "success":
            return fetched
        return await self._apply_pin(program_key, fetched)

    async def _shared_read(self, cache_key: str, read) -> Any:
        """The cached answer for `cache_key`, else the one GitHub read in flight for it.

        The update screen asks `/api/programs` and `/api/programs/satellites` at
        once, and both read multiroom and camilladsp: checked and filled around
        an await, the cache let each caller send its own request. Shielded, so a
        caller that gives up neither cuts the read nor hands the callers waiting
        beside it a CancelledError.
        """
        if (cache_key in self._github_cache and
                time.time() - self._last_github_fetch.get(cache_key, 0) < self._cache_timeout):
            return self._github_cache[cache_key]

        task = self._inflight.get(cache_key)
        if task is None:
            task = asyncio.create_task(read())
            self._inflight[cache_key] = task
            task.add_done_callback(lambda done, key=cache_key: self._forget_read(key, done))
        return await asyncio.shield(task)

    def _forget_read(self, cache_key: str, done: asyncio.Task) -> None:
        if self._inflight.get(cache_key) is done:
            del self._inflight[cache_key]
        # Logged here: every caller may be gone, and the status read that
        # receives it turns it into an error row without a word.
        if not done.cancelled() and done.exception() is not None:
            self.logger.error(f"GitHub read {cache_key} failed: {done.exception()!r}")

    async def _fetch_latest_release(self, program_key: str) -> Dict[str, Any]:
        """The program's latest upstream release, cached for an hour.

        A failure is cached too, for `RELEASES_RETRY_S`: every status read
        reaches this, so an uncached one would cost each opening of the screen a
        10 s timeout offline, and under the anonymous rate limit a 403 asked
        again on every read. Never the last good release in its place — that
        would go on offering an update GitHub can no longer be asked about.
        """
        if program_key not in self.programs:
            return {"status": "error", "message": "Unknown program"}
        cache_key = f"github_{program_key}"
        return await self._shared_read(
            cache_key, lambda: self._read_latest_release(program_key, cache_key)
        )

    async def _read_latest_release(self, program_key: str, cache_key: str) -> Dict[str, Any]:
        repo = self.programs[program_key]["repo"]
        current_time = time.time()

        try:
            # Call GitHub API with headers (including token if available)
            url = f"https://api.github.com/repos/{repo}/releases/latest"
            headers = self._get_github_headers()

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        version_regex = self.programs[program_key]["version_regex"]
                        match = re.search(version_regex, tag_name)

                        # What the release actually carries. Milo's offer has to
                        # know: a tag whose build never attached the frontend is
                        # a release that cannot be installed, and offering it
                        # draws a button whose every press is refused.
                        asset_names = [
                            asset.get("name") for asset in data.get("assets") or []
                        ]

                        result = {
                            "status": "success",
                            "version": match.group(1) if match else tag_name,
                            "tag_name": tag_name,
                            "published_at": data.get("published_at"),
                            "html_url": data.get("html_url"),
                            "asset_names": asset_names,
                        }

                        self._github_cache[cache_key] = result
                        self._last_github_fetch[cache_key] = current_time

                        return result

                    elif response.status == 403:
                        error_data = await response.json()
                        error_message = error_data.get("message", "Rate limit exceeded")

                        if self.github_token:
                            self.logger.warning(f"GitHub API error despite token: {error_message}")
                        else:
                            self.logger.warning("GitHub API rate limit - consider adding GITHUB_TOKEN")

                        error = {"status": "error", "message": error_message}
                    else:
                        error = {"status": "error", "message": f"GitHub API error: {response.status}"}

        except asyncio.TimeoutError:
            error = {"status": "error", "message": "GitHub API timeout"}
        except Exception as e:
            error = {"status": "error", "message": f"GitHub API error: {str(e)}"}

        self._github_cache[cache_key] = error
        self._last_github_fetch[cache_key] = current_time - self._cache_timeout + RELEASES_RETRY_S
        return error

    def forget_failed_checks(self) -> None:
        """Drop every failed latest-release read, so the next status read asks again.

        What a press of "Retry" on a row that says the check failed means: the
        failure is otherwise remembered for `RELEASES_RETRY_S`, and a retry
        answered from that memory would repeat the failure it was pressed to
        get past. Successes stay cached — a retry is about the rows that failed,
        not a reason to spend the anonymous rate limit on the rows that did not.
        """
        candidates = [f"github_{program_key}" for program_key in self.programs]
        # A signature that could not be *read* is a check that failed too; one
        # that was read and refused is an answer, and a retry would repeat it.
        candidates += [key for key in self._github_cache if key.startswith(SIGNATURE_CACHE_PREFIX)]
        for cache_key in candidates:
            cached = self._github_cache.get(cache_key)
            if cached is not None and cached.get("status") not in ("success", "refused"):
                del self._github_cache[cache_key]
                self._last_github_fetch.pop(cache_key, None)

    async def _fetch_releases(self, program_key: str) -> List[Dict[str, Any]]:
        """The program's recent stable releases, newest first, cached for an hour.

        Only what a trial can pick from, so it never stands in the way of the
        offer: a failed read is a warning and the last good list (empty if there
        never was one), and it is kept for
        `RELEASES_RETRY_S` — this runs on every status read, so an uncached
        failure would cost each opening of the screen a 10 s timeout offline, and
        under the anonymous rate limit spend the requests the offer itself needs.
        A tag counts only
        when it is the bare version, optionally "v"-prefixed — `version_regex`
        searches, so "v0.10.2-rc1" would otherwise read as 0.10.2.
        """
        cache_key = f"github_releases_{program_key}"
        return await self._shared_read(
            cache_key, lambda: self._read_releases(program_key, cache_key)
        )

    async def _read_releases(self, program_key: str, cache_key: str) -> List[Dict[str, Any]]:
        now = time.time()
        repo = self.programs[program_key]["repo"]
        url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_github_headers(),
                                       timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        raise ValueError(f"GitHub API error: {response.status}")
                    data = await response.json()
            if not isinstance(data, list):
                raise ValueError("GitHub API answered no release list")
        except Exception as e:
            self.logger.warning(f"Release list for {program_key} unavailable: {e}")
            # The last good list outlives a failed refresh: the install checks the
            # chosen version against it, so emptying it would refuse the release
            # the menu showed a moment ago. Retried RELEASES_RETRY_S from now.
            stale = self._github_cache.get(cache_key, [])
            self._github_cache[cache_key] = stale
            self._last_github_fetch[cache_key] = now - self._cache_timeout + RELEASES_RETRY_S
            return stale

        version_regex = self.programs[program_key]["version_regex"]
        releases = []
        for release in data:
            tag_name = release.get("tag_name") or ""
            match = re.search(version_regex, tag_name)
            if (release.get("draft") or release.get("prerelease") or not match
                    or tag_name.removeprefix("v") != match.group(1)):
                continue
            releases.append({
                "version": match.group(1),
                "tag_name": tag_name,
                "html_url": release.get("html_url"),
                "published_at": release.get("published_at"),
            })
        releases.sort(key=lambda r: [int(p) for p in r["version"].split(".")], reverse=True)

        self._github_cache[cache_key] = releases
        self._last_github_fetch[cache_key] = now
        return releases

    async def get_all_program_status(self) -> Dict[str, Any]:
        """Gets the status of all programs"""
        results = {}

        # Get installed versions in parallel
        tasks = []
        for program_key in self.programs.keys():
            tasks.append(self.get_program_full_status(program_key))

        program_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, program_key in enumerate(self.programs.keys()):
            if isinstance(program_results[i], Exception):
                results[program_key] = {
                    "status": "error",
                    "message": str(program_results[i]),
                    "update_available": False,
                    "check_failed": True,
                }
            else:
                results[program_key] = program_results[i]

        return results

    def _reconcile_offer(
        self,
        program_key: str,
        latest: Dict[str, Any],
        installed: str,
        update_available: bool,
    ) -> None:
        """Make the offer readable on a unit sitting ABOVE the version it is pinned to.

        Two ways in, and neither records a trial: a manifest deliberately rolled
        back (a yanked release), and a `forced_versions` write that failed after
        the install went through. `_apply_pin` emits the `validated` block — the
        one the return button is built from — only while an override is active,
        so such a unit is offered nothing at all: `update_available` is false
        (the pin is older than what it runs) and the row reads "up to date" on a
        release nobody validated. That is the exact state this whole surface
        exists to make impossible, and `_reconcile_dependencies` only undoes it
        during a Milo app update — never from the screen.

        Two corrections, both of them about what the row may claim:

          * name the pin as `validated`, so the return button appears and
            `_select_target(status, "validated")` installs it;
          * drop `upstream.ahead` when the unit already runs that upstream
            release. `ahead` is measured against the pin on purpose — a unit
            *behind* the set still sees what there is to try — and this is the
            one case it gets wrong, drawing "1.6.0 > 1.6.0" beside an Update
            button that reinstalls what is already installed.

        A pinned program only: `milo` is the app, not a dependency, and the
        latest release is simply what it offers.
        """
        if not self.programs[program_key].get("validated_version"):
            return

        upstream = latest.get("upstream")
        if upstream and upstream.get("version") == installed:
            upstream["ahead"] = False

        if update_available or installed == latest.get("version") or "validated" in latest:
            return

        latest["validated"] = {
            key: latest.get(key)
            for key in ("version", "tag_name", "html_url", "published_at")
        }

    @staticmethod
    def installed_version(status: Dict[str, Any]) -> Optional[str]:
        """The one version a full status reports as installed.

        Multiroom's two components are normalised to a single entry below, so
        the first value is the only value for every program.
        """
        return next(iter(status.get("installed", {}).get("versions", {}).values()), None)

    async def _exact_release_tag(self) -> Optional[str]:
        """The tag this checkout sits exactly on, asked of git rather than inferred.

        `--exact-match` prints the tag or fails, which is the whole question. The
        alternative was to read the shape of `git describe --tags --always`, and
        that cannot tell "v0.2.0-rc1" — a tag — apart from "v0.1.0-2017-g36b9a0d7"
        — a tree 2017 commits past one — by anything but the convention of a
        suffix. Getting it wrong in the second direction offers an update to a
        development checkout; in the first, it tells a unit running a
        pre-release that it is running nothing at all.
        """
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", self.programs["milo"]["git_path"],
            "describe", "--tags", "--exact-match",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return stdout.decode().strip() or None

    async def _fetch_release_bytes(self, url: str, *, listed: bool) -> bytes:
        """One small release asset, byte for byte: a signature covers exact
        bytes, so nothing is decoded or stripped on the way. A 404 is a release
        that publishes no such file, refused as unverifiable — unless `listed`:
        the release's listing names the file, so a 404 is the download host
        failing, a read that failed like any other, which raises as one."""
        name = url.rsplit("/", 1)[-1]
        body = bytearray()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url) as response:
                if response.status == 404 and not listed:
                    raise UnverifiableRelease(f"the release publishes no {name}")
                if response.status != 200:
                    raise Exception(f"Download failed: HTTP {response.status} for {url}")
                async for chunk in response.content.iter_chunked(8192):
                    body += chunk
                    if len(body) > MAX_MANIFEST_BYTES:
                        raise UnverifiableRelease(f"{name} is larger than a release manifest")
        return bytes(body)

    async def verify_release(self, tag: str, *, listed: bool = False) -> ReleaseManifest:
        """The signed manifest of Milō release `tag`, verified against the keys
        of the release this unit runs. Raises UnverifiableRelease when the
        release cannot be verified, anything else when it could not be read
        (`listed`: the caller saw both files in the release's listing).

        Asked afresh on every call: the install calls it before it moves
        anything (`UpdateService._update_milo_app`), the offer through a cache
        (`_release_verdict`).
        """
        milo = self.programs["milo"]
        url = milo["release_manifest_url"].format(tag=tag)
        manifest = await self._fetch_release_bytes(url, listed=listed)
        signature = await self._fetch_release_bytes(f"{url}{SIGNATURE_SUFFIX}", listed=listed)
        return await verified_manifest(manifest, signature, Path(milo["release_signing_key"]), tag)

    async def _release_verdict(self, tag: str, asset_names: List[str]) -> Dict[str, Any]:
        """Whether release `tag` verifies: `success`, `refused` (no signed
        manifest, or one that does not verify), or `error` (it could not be
        read). Cached per tag like the release it belongs to — a published
        release's assets do not change — and a read that failed for
        `RELEASES_RETRY_S`, the screen's Retry dropping it at once."""
        cache_key = f"{SIGNATURE_CACHE_PREFIX}{tag}"
        return await self._shared_read(
            cache_key, lambda: self._read_release_verdict(tag, asset_names, cache_key)
        )

    async def _read_release_verdict(
        self, tag: str, asset_names: List[str], cache_key: str
    ) -> Dict[str, Any]:
        now = time.time()
        manifest = self.programs["milo"]["release_manifest_url"].format(tag=tag).rsplit("/", 1)[-1]
        try:
            # A release that lists neither file is refused without a download.
            missing = [name for name in (manifest, f"{manifest}{SIGNATURE_SUFFIX}")
                       if name not in asset_names]
            if missing:
                raise UnverifiableRelease(f"the release publishes no {' and no '.join(missing)}")
            await self.verify_release(tag, listed=True)
            verdict = {"status": "success"}
        except UnverifiableRelease as e:
            # Logged once per read, not per status read: this is cached. A
            # warning, as for a release that published no frontend — the row
            # says it, and an error would raise the banner on every opening.
            self.logger.warning(f"Release {tag} is refused, it is not offered: {e}")
            verdict = {"status": "refused", "message": str(e)}
        except Exception as e:
            self.logger.warning(f"The signature of release {tag} could not be read: {e}")
            verdict = {"status": "error", "message": str(e)}
            self._github_cache[cache_key] = verdict
            self._last_github_fetch[cache_key] = now - self._cache_timeout + RELEASES_RETRY_S
            return verdict
        self._github_cache[cache_key] = verdict
        self._last_github_fetch[cache_key] = now
        return verdict

    async def _apply_milo_offer(self, result: Dict[str, Any], latest: Dict[str, Any]) -> None:
        """Decide Milo's row on tag identity rather than version order.

        Milo is the app, not a dependency: it does not track an upstream project
        whose numbers only ever grow, it moves between the releases of this
        repo. So the question is not "is there a bigger number" but "is this the
        release the channel publishes", and it is asked of the tag — the thing
        the install actually checks out.

        Four behaviours fall out, and all four are the point:

          * **a pre-release is never offered.** It exists to be installed on a
            unit somebody is watching, by somebody who chose it. `releases/latest`
            already excludes one and the publish step marks it as such, but the
            refusal is stated here too, so it survives either of those being
            changed by accident.
          * a release that is withdrawn — unpublished, or marked pre-release
            because it turned out bad — stops being what `releases/latest`
            names, and every unit that took it is offered the return on its next
            check. Retracting is one gesture, not N units visited one by one.
            `withdrawn` says which of the two directions the row points, so the
            button can name itself.
          * a tree that is not at a tag — a development checkout, or a unit
            somebody pulled on — is offered nothing at all. It is not behind a
            release, it is outside the channel, and `git checkout <tag>` over
            uncommitted work is not an update. `development_build` is what the
            row says instead of a version comparison nobody can act on.
          * **a release no trusted key signed is refused, and says so.** The
            install would refuse it before its checkout (release_signature.py
            says why), so offering it would draw a button whose every press
            fails. `refused: "signature"` is what the row reads instead — not
            "up to date", which would hide that the channel served something
            this unit will not run. A signature that could not be *read* (no
            network) is a check that failed, like the release read itself.

        A unit running a pre-release is *not* a development build: it sits on a
        tag, so it is told plainly what it runs and offered the stable release
        the channel publishes — which is how a test unit gets back.
        """
        # Asked of git, not of GitHub: an offline development tree is still one.
        tag = await self._exact_release_tag()
        result["refused"] = None
        result["development_build"] = tag is None
        result["installed"]["release_tag"] = tag
        if tag is None:
            return
        if latest.get("status") != "success":
            # A unit in the channel whose question went unanswered. Not "up to
            # date": nobody asked GitHub what it publishes.
            result["check_failed"] = True
            return

        published = latest.get("tag_name")
        if not is_stable_release(published) or published == tag:
            return

        # The offer's contract is that what it names can be *installed*, and half
        # of that is an artefact, not a ref. A tag pushed without CI, a build
        # that died after publishing, a release predating this channel — each
        # gives a frontend download that 404s, an update refused at every
        # press. Measured on v0.0.1, the one release this repo carried before
        # the channel existed: offered, and carrying no asset at all.
        wanted = self.programs["milo"]["frontend_asset_url"].format(
            tag=published
        ).rsplit("/", 1)[-1]
        if wanted not in (latest.get("asset_names") or []):
            self.logger.warning(
                f"Release {published} publishes no {wanted}; it cannot be "
                "installed, so it is not offered."
            )
            return

        verdict = await self._release_verdict(published, latest.get("asset_names") or [])
        if verdict["status"] == "error":
            result["check_failed"] = True
            return
        if verdict["status"] != "success":
            result["refused"] = "signature"
            return

        result["update_available"] = True
        # Installed newer than published: the release moved backwards, which is
        # a withdrawal. Measured on the versions rather than the tags because
        # the "v" prefix is a repo convention, not part of the ordering.
        latest["withdrawn"] = compare_versions(
            latest.get("version"), self.installed_version(result)
        )

    async def get_program_full_status(self, program_key: str) -> Dict[str, Any]:
        """Gets complete status (installed + GitHub) for a program"""
        try:
            # Launch both requests in parallel
            installed_task = self.get_installed_version(program_key)
            github_task = self.get_latest_github_version(program_key)

            installed_result, github_result = await asyncio.gather(
                installed_task, github_task, return_exceptions=True
            )

            if isinstance(installed_result, Exception):
                installed_result = {"status": "error", "message": str(installed_result)}

            if isinstance(github_result, Exception):
                github_result = {"status": "error", "message": str(github_result)}

            # For multiroom, normalize to single canonical version (use snapserver)
            if program_key == "multiroom" and installed_result.get("status") == "installed":
                versions = installed_result.get("versions", {})
                canonical_version = versions.get("snapserver") or versions.get("snapclient")
                if canonical_version:
                    installed_result["versions"] = {"main": canonical_version}

            # `update_available` False answers two different things — "asked,
            # nothing newer" and "could not ask" — so the second carries
            # `check_failed`. A GitHub rate limit or a unit offline otherwise
            # reads "up to date" for as long as it lasts, which is the one
            # answer nobody checked.
            result = {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "installed": installed_result,
                "latest": github_result,
                "update_available": False,
                "check_failed": False,
            }

            # Determine if an update is available. Milo answers a different
            # question from the dependencies — which release, not which version
            # — so it takes the whole branch rather than a clause inside it.
            if program_key == "milo":
                await self._apply_milo_offer(result, github_result)
            elif github_result.get("status") != "success":
                result["check_failed"] = True
            elif installed_result.get("status") == "installed":

                installed_version = self.installed_version(result)
                latest_version = github_result.get("version")

                if installed_version and latest_version:
                    result["update_available"] = compare_versions(installed_version, latest_version)
                    self._reconcile_offer(
                        program_key, github_result, installed_version,
                        result["update_available"],
                    )

            return result

        except Exception as e:
            # A row that could not be decided is a check that failed, and
            # says so like one rather than leaving `update_available` absent —
            # which a screen reads as "up to date".
            self.logger.error(f"Status of {program_key} could not be decided: {e}")
            return {
                "name": self.programs[program_key]["name"],
                "description": self.programs[program_key]["description"],
                "status": "error",
                "message": str(e),
                "update_available": False,
                "check_failed": True,
            }
