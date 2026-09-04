# backend/core/updates/satellite.py
"""
Satellite update service — discovers satellites via client registry and manages updates.
"""
import asyncio
import contextlib
import aiohttp
import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.config.constants import CLIENT_API_PORT
from backend.shared.decorators import handle_errors

MILO_REPO_DIR = Path("/home/milo/milo")
MILO_CLIENT_DIR = MILO_REPO_DIR / "milo-client"

# Patterns to exclude from the client tarball. `venv` is the satellite's own,
# built at install time and symlinked into the repo dir — shipping the server's
# copy meant sending 67 MB the satellite extracted and threw away, over 99 % of
# every app update's payload.
TARBALL_EXCLUDE_PATTERNS = {"__pycache__", ".pyc", ".pytest_cache", "tests", ".git", "venv"}


class SatelliteUpdateService:
    """Service to manage satellites and their updates.

    Discovers satellites via ClientRegistryService (non-local clients)
    and identifies them by mac_id.
    """

    def __init__(self, snapcast_service, client_registry_service):
        self.snapcast_service = snapcast_service
        self.client_registry_service = client_registry_service
        self.logger = logging.getLogger(__name__)
        self.satellite_api_port = CLIENT_API_PORT

    @handle_errors(default=[])
    async def discover_satellites(self) -> List[Dict[str, Any]]:
        """Discovers active satellites on the network via the client registry.

        Only probes online, non-local clients to avoid timeout delays on
        unreachable devices. Probes run in parallel for speed.
        """
        all_clients = self.client_registry_service.get_all_clients()

        # Only probe clients that Snapcast reports as online
        candidates = [
            (mac_id, client) for mac_id, client in all_clients.items()
            if not client.is_local and client.ip and client.online
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
                "app_release": result.get("app_release"),
                "app_payload": result.get("app_payload"),
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
                        "app_release": data.get("app", {}).get("release"),
                        "app_payload": data.get("app", {}).get("payload"),
                        "app_started_at": data.get("app", {}).get("started_at"),
                        "camilladsp_version": data.get("camilladsp", {}).get("version")
                    }

        return {"online": False}

    async def update_satellite(
        self,
        mac_id: str,
        target_version: str
    ) -> Dict[str, Any]:
        """Installs `target_version` of snapclient on a satellite.

        The version is resolved on the server, against the same manifest — and
        the same trial — the server itself runs on. A satellite carries neither,
        so deciding it there is how a client lands on a release nobody validated.
        """
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/update"

            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json={"target_version": target_version}) as response:
                    if response.status == 200:
                        data = await response.json()

                        # `started` is the satellite's answer to "did I begin
                        # one" — false is the legitimate already-up-to-date
                        # branch, not a failure. What it would install is no
                        # longer in question: it is what this call named, so
                        # the satellite's own version is what separates the two
                        # readings of `false`.
                        if data.get("started"):
                            update_result = await self._wait_for_update_completion(
                                mac_id,
                                ip,
                                target_version
                            )

                            return update_result
                        if data.get("current_version") == target_version:
                            # Nothing to do, and reporting it as a failure was
                            # self-perpetuating: the UI refetches the inventory
                            # only on success, so the stale row that offered the
                            # button kept offering it, and every press failed
                            # the same way until the page was reloaded.
                            return {
                                "success": True,
                                "message": f"Satellite {mac_id} already runs {target_version}",
                                "new_version": target_version
                            }
                        return {
                            "success": False,
                            "error": data.get("message", "Update failed")
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}"
                        }

        except Exception as e:
            self.logger.error(f"Error updating satellite {mac_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_update_completion(
        self,
        mac_id: str,
        ip: str,
        target_version: str
    ) -> Dict[str, Any]:
        """Waits for update completion on the satellite, then checks it landed.

        `update_in_progress` going false says the attempt ended, not that it
        worked — a failed download and a successful install clear it alike. Only
        the version the satellite now reports separates the two.
        """
        max_wait_time = 180
        check_interval = 5
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            try:
                url = f"http://{ip}:{self.satellite_api_port}/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
                                status_url = f"http://{ip}:{self.satellite_api_port}/status"

                                async with session.get(status_url) as status_response:
                                    if status_response.status == 200:
                                        status_data = await status_response.json()
                                        new_version = status_data.get("snapclient", {}).get("version")

                                        if new_version != target_version:
                                            return {
                                                "success": False,
                                                "error": (
                                                    f"Satellite {mac_id} still runs snapclient "
                                                    f"{new_version}, expected {target_version}"
                                                )
                                            }

                                        return {
                                            "success": True,
                                            "message": f"Satellite {mac_id} updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for update on {mac_id}: {e}")
                continue

        return {
            "success": False,
            "error": f"Update timeout for {mac_id}"
        }

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
        """Fingerprint of what a satellite update actually ships: the commit
        that last touched `milo-client/`.

        Two different questions are asked of a satellite, and one string used to
        answer both — badly. *Which version does it run* is the release, and it
        is what the row displays (`get_release_version`). *Does its code differ
        from the server's* is this, and it is what decides whether to push.

        Deciding on the release lit the update button on the whole fleet every
        time the server moved — measured once at 100 commits, 0 of them
        touching `milo-client/` — and each press deployed a byte-identical
        payload while restarting a speaker in an occupied room. Deciding on the
        payload leaves the button lit for exactly one case: a satellite that was
        offline while the server updated, which is the case it is for.

        Uncommitted work in the directory is payload no commit names, hence the
        `-dirty` suffix: without it a satellite change under test can never be
        pushed from the UI.
        """
        commit = await self._git("log", "-1", "--format=%h", "--", "milo-client")
        if not commit:
            return None

        dirty = await self._git("status", "--porcelain", "--", "milo-client")
        return f"{commit}-dirty" if dirty else commit

    async def get_release_version(self) -> Optional[str]:
        """The release this server runs, which is the one a satellite displays.

        Both halves ship from one commit, so a satellite carrying this server's
        payload is running this release — there is no third thing it could be,
        and giving it a number of its own is how a fleet comes to show four
        version schemes on one screen.

        None on a development checkout: it is outside the release channel, and
        so is anything pushed from it. `--exact-match` is what asks that of git
        instead of inferring it from the shape of a describe suffix — which
        cannot tell a pre-release tag apart from a tree past a tag.
        """
        return await self._git("describe", "--tags", "--exact-match")

    async def _create_client_tarball(self) -> tuple:
        """Creates a tarball of the milo-client/ directory.

        Returns (tarball_path, version) tuple.
        """
        version = await self.get_client_payload_version()
        if not version:
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
        self.logger.info(f"Created client tarball: {tarball_path} (version: {version})")
        return tarball_path, version

    async def update_satellite_app(
        self,
        mac_id: str
    ) -> Dict[str, Any]:
        """Pushes a milo-client app update to a satellite."""
        tarball_path = None
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]
            # Read before the push: the satellite cannot move this without
            # restarting, which is the one step the version file cannot attest.
            started_at_before = satellite.get("app_started_at")

            tarball_path, version = await self._create_client_tarball()
            # Empty on a development checkout, and stored as such: the satellite
            # then reads as a development build too, which is what it is.
            release = await self.get_release_version() or ""

            url = f"http://{ip}:{self.satellite_api_port}/app/update"
            timeout = aiohttp.ClientTimeout(total=120)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                with open(tarball_path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("tarball", f, filename="milo-client.tar.gz",
                                   content_type="application/gzip")
                    form.add_field("version", version)
                    form.add_field("release", release)

                    async with session.post(url, data=form) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            return {
                                "success": False,
                                "error": f"Satellite rejected update: HTTP {response.status} - {error_text}"
                            }

            result = await self._wait_for_app_update_completion(
                mac_id, ip, version, started_at_before
            )
            return result

        except Exception as e:
            self.logger.error(f"Error updating satellite app {mac_id}: {e}")
            return {"success": False, "error": str(e)}

        finally:
            if tarball_path:
                with contextlib.suppress(OSError):
                    os.unlink(tarball_path)

    async def push_client_app_to_fleet(self) -> list[str]:
        """Bring every satellite to the `milo-client/` tree this server now has.

        A Milō update replaces that tree, so the whole fleet goes stale at once
        and deterministically. Pushing it here is the same sequence as the
        validated dependency set: one press updates the appliance, not the
        appliance and then N speakers one screen at a time. It costs no extra
        silence either — the update reboots the unit on the next step, so the
        satellites restart inside a window that is already quiet.

        Never raises. It runs past the point where the app can still be rolled
        back, and an unplugged satellite is a fact to report, not a failed Milō
        update: its own row offers the catch-up when it comes back.

        Returns what was left behind, named for the journal and the envelope —
        a satellite, or the reason nothing could be pushed at all.
        """
        left_behind: list[str] = []
        try:
            version = await self.get_client_payload_version()
            if not version:
                self.logger.error(
                    "Client app not pushed: the milo-client payload version is "
                    "unreadable. Every satellite keeps the app it runs."
                )
                return ["milo-client payload version unreadable"]

            remote = {
                mac: client
                for mac, client in self.client_registry_service.get_all_clients().items()
                if not client.is_local
            }
            if not remote:
                return left_behind

            reachable = {s["mac_id"]: s for s in await self.discover_satellites()}

            for mac, client in remote.items():
                name = client.name or mac
                satellite = reachable.get(mac)

                if not satellite:
                    left_behind.append(name)
                    self.logger.error(
                        f"{name}: not answering its API, client app not pushed. "
                        "Its own row offers the update once it is back."
                    )
                    continue

                # Already running this tree: pushing would restart a speaker in
                # an occupied room to deploy bytes it already has.
                if satellite.get("app_payload") == version:
                    continue

                self.logger.info(f"{name}: pushing client app {version} with the Milo update")
                result = await self.update_satellite_app(mac)
                if not result.get("success"):
                    left_behind.append(name)
                    self.logger.error(
                        f"{name}: client app push failed ({result.get('error', 'unknown error')}). "
                        "The Milo update continues; the satellite keeps the app it runs."
                    )

        except Exception as e:
            self.logger.error(f"Pushing the client app to the fleet failed: {e}")
            left_behind.append(f"fleet push failed: {e}")

        return left_behind

    async def update_satellite_camilladsp(
        self,
        mac_id: str,
        target_version: str
    ) -> Dict[str, Any]:
        """Installs `target_version` of CamillaDSP on a satellite.

        Server-resolved for the same reason as the snapclient update above.
        """
        try:
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["mac_id"] == mac_id), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {mac_id} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/camilladsp/update"

            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json={"target_version": target_version}) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Same reading of `started: false` as the snapclient
                        # update above.
                        if data.get("started"):
                            return await self._wait_for_camilladsp_update_completion(
                                mac_id, ip, target_version
                            )
                        if data.get("current_version") == target_version:
                            return {
                                "success": True,
                                "message": f"Satellite {mac_id} already runs CamillaDSP {target_version}",
                                "new_version": target_version
                            }
                        return {
                            "success": False,
                            "error": data.get("message", "Update failed")
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}"
                        }

        except Exception as e:
            self.logger.error(f"Error updating CamillaDSP on satellite {mac_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_camilladsp_update_completion(
        self,
        mac_id: str,
        ip: str,
        target_version: str
    ) -> Dict[str, Any]:
        """Polls the satellite until the CamillaDSP update completes, then
        checks the version actually moved — same reasoning as the snapclient
        waiter above."""
        max_wait_time = 180
        check_interval = 5
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            try:
                url = f"http://{ip}:{self.satellite_api_port}/camilladsp/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
                                # Fetch updated version from /status
                                status_url = f"http://{ip}:{self.satellite_api_port}/status"

                                async with session.get(status_url) as status_response:
                                    if status_response.status == 200:
                                        status_data = await status_response.json()
                                        new_version = status_data.get("camilladsp", {}).get("version")

                                        if new_version != target_version:
                                            return {
                                                "success": False,
                                                "error": (
                                                    f"Satellite {mac_id} still runs CamillaDSP "
                                                    f"{new_version}, expected {target_version}"
                                                )
                                            }

                                        return {
                                            "success": True,
                                            "message": f"Satellite {mac_id} CamillaDSP updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for CamillaDSP update on {mac_id}: {e}")
                continue

        return {
            "success": False,
            "error": f"CamillaDSP update timeout for {mac_id}"
        }

    async def _wait_for_app_update_completion(
        self,
        mac_id: str,
        ip: str,
        expected_version: str,
        started_at_before: Optional[int]
    ) -> Dict[str, Any]:
        """Polls satellite /status until the payload just sent is the one *running*.

        The payload it reports cannot say that on its own. The satellite writes
        it to a file at step 5 of its own deployment and only schedules the
        restart at step 6, so a restart that never lands — an invalid unit file
        the same update just deployed, a masked unit — leaves a satellite
        answering with the new fingerprint out of the old process, forever.
        `app.started_at` is the process itself, and only a real restart moves it.
        """
        max_wait_time = 90
        check_interval = 5
        elapsed = 0
        payload_seen = False

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            try:
                url = f"http://{ip}:{self.satellite_api_port}/status"
                timeout = aiohttp.ClientTimeout(total=5)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            app = data.get("app", {})
                            payload = app.get("payload")

                            if payload == expected_version:
                                payload_seen = True

                            if payload == expected_version and app.get("started_at") != started_at_before:
                                return {
                                    "success": True,
                                    "message": f"Satellite {mac_id} app updated successfully",
                                    "new_version": payload
                                }

            except Exception as e:
                self.logger.debug(f"Waiting for satellite {mac_id} restart: {e}")
                continue

        if payload_seen:
            self.logger.error(
                f"Satellite {mac_id} deployed {expected_version} but never restarted into it"
            )
            return {
                "success": False,
                "error": f"Satellite {mac_id} deployed the update but never restarted into it"
            }

        return {
            "success": False,
            "error": f"App update timeout for {mac_id}"
        }

