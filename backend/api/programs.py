# backend/api/programs.py
"""
API routes for program management — Full version with satellites
"""
import asyncio
import logging
from typing import TYPE_CHECKING, Optional
from fastapi import APIRouter, BackgroundTasks
from backend.core.models.audio_state import AudioSource
from backend.core.models.ws_events import (
    ProgramsCompleteEvent,
    ProgramsProgressEvent,
    ProgramUpdateComplete,
    ProgramUpdateProgress,
    SatelliteAppUpdateComplete,
    SatelliteAppUpdateProgress,
    SatelliteCamillaDspUpdateComplete,
    SatelliteCamillaDspUpdateProgress,
    SatelliteUpdateComplete,
    SatelliteUpdateProgress,
)
from backend.api.models import ProgramUpdateRequest

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine
    from backend.core.updates.satellite import SatelliteUpdateService
    from backend.core.updates.update import UpdateService


logger = logging.getLogger(__name__)

# Mapping from program key to AudioSource for pre-update deactivation
PROGRAM_TO_AUDIO_SOURCE = {
    'go-librespot': AudioSource.SPOTIFY,
    'shairport-sync': AudioSource.AIRPLAY,
    'qobuz-proxy': AudioSource.QOBUZ,
    # Stopping Navidrome mid-update kills the localhost stream mpv is playing.
    'navidrome': AudioSource.MUSIC_LIBRARY,
}

def create_programs_router(
    update_service: "UpdateService",
    satellite_update_service: "SatelliteUpdateService",
    state_machine: "AudioStateMachine"
):
    """Router for local and satellite programs

    Args:
        update_service: Singleton service for version checks and updates
        satellite_update_service: Singleton service for satellite updates
        state_machine: AudioStateMachine for broadcasting events and deactivating active sources
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    satellite_service = satellite_update_service

    # The in-flight update keys. A set, not a map: the only questions asked of
    # it are membership and which keys are local, and a per-key status/progress
    # payload lived here unread for as long as it existed.
    active_updates: set[str] = set()

    # An update key belongs to the server ("local") or to one satellite. The
    # longest prefix first: a MAC never contains "app_" or "camilladsp_", but
    # the bare "satellite_" would swallow both.
    satellite_key_prefixes = ("satellite_app_", "satellite_camilladsp_", "satellite_")

    def _scope(update_key: str) -> str:
        """"local", or the satellite this key drives."""
        for prefix in satellite_key_prefixes:
            if update_key.startswith(prefix):
                return update_key[len(prefix):]
        return "local"

    def _claim_update(update_key: str) -> Optional[str]:
        """Reserve an update key, or name the in-flight update that forbids it.

        Synchronous on purpose, and called by the route rather than by
        _create_background_update: `update_program` awaits GitHub
        (`can_update_program`, up to ~20 s) between deciding and starting, and
        a check separated from its write by an await lets two clients both
        pass. The frontend store blocks a same-client double click; it cannot
        block a second device. Every path out of a route between the claim and
        `background_tasks.add_task` must release the key.

        The policy is the one the update screen draws, enforced here because
        that is the side a second device cannot walk around. `milo` blocks
        everything and is blocked by everything: it reconciles the whole
        dependency set — so a program update running beside it is installed
        twice, each run stopping the service the other started — pushes the
        client app to every satellite, colliding with that satellite's own 409,
        and then reboots the unit out from under the task polling it. Two local
        programs block each other for the deploy wrapper they share. A
        satellite is a separate machine, so it blocks only its own three keys.

        Returns None once the key is claimed.
        """
        scope = _scope(update_key)
        for running in active_updates:
            if "milo" in (running, update_key) or _scope(running) == scope:
                return running
        active_updates.add(update_key)
        return None

    def _refuse(blocker: str, update_key: str) -> dict:
        """The refusal envelope, naming what is in the way when it is not this key."""
        if blocker == update_key:
            return {
                "status": "error",
                "message": f"Update already in progress for {update_key}",
            }
        return {
            "status": "error",
            "message": f"An update is already in progress for {blocker}; it must finish first",
        }

    def _create_background_update(
        update_key: str,
        update_fn,
        progress_event_cls: type[ProgramsProgressEvent],
        complete_event_cls: type[ProgramsCompleteEvent],
        identifier: dict,
        pre_update_fn=None,
    ):
        """Create the background task that runs one update and broadcasts it.

        The key is already claimed by the route through _claim_update().
        Returns an async do_update coroutine to pass to background_tasks.add_task().
        """

        async def do_update():
            try:
                # Announced once, at the start: the event's whole payload is the
                # identifier plus a constant status, so firing it again at each
                # phase said nothing a client did not already know.
                await state_machine.broadcast(progress_event_cls(**identifier))

                if pre_update_fn:
                    await pre_update_fn()

                result = await update_fn()
                active_updates.discard(update_key)

                if not result["success"]:
                    logger.error(f"Update {update_key} failed: {result.get('error', 'Update failed')}")

                await state_machine.broadcast(
                    complete_event_cls(**identifier, success=result["success"])
                )

            except Exception as e:
                logger.error(f"Update {update_key} failed: {e}")
                active_updates.discard(update_key)
                await state_machine.broadcast(
                    complete_event_cls(**identifier, success=False)
                )

        return do_update

    # === SPECIFIC ROUTES (must come BEFORE generic routes) ===

    @router.get("")
    async def get_all_programs():
        """Retrieve the status of all local programs (installed + GitHub)"""
        # In-flight local update keys (satellite keys are prefixed, excluded here).
        # Lets a freshly loaded client reconstruct "updating" state it never saw the
        # WS progress deltas for — after a reload, on a second device, or when the
        # backend restarted mid-update (e.g. a milo self-update).
        active = [k for k in active_updates if not k.startswith("satellite_")]
        try:
            results = await update_service.get_all_program_status()
            return {
                "status": "success",
                "programs": results,
                "count": len(results),
                "active_updates": active
            }
        except Exception as e:
            # HTTP 200 + status:error is the documented resilience pattern for a
            # /status-style read — the settings screen must render with whatever
            # it has rather than collapsing on a GitHub timeout. It is not a
            # reason to keep the failure out of the journal and the banner.
            logger.error(f"Error listing program status: {e}")
            return {
                "status": "error",
                "message": str(e),
                "programs": {},
                "count": 0,
                "active_updates": active
            }

    # === SATELLITE ROUTES (specific, before generic routes) ===

    @router.get("/satellites")
    async def get_satellites():
        """Retrieve the list of detected satellites with their versions"""
        try:
            # Fetch all data in parallel: satellite discovery + cached version lookups
            satellites_task = satellite_service.discover_satellites()
            snapclient_task = update_service.get_latest_github_version("multiroom")
            payload_task = satellite_service.get_client_payload_version()
            release_task = satellite_service.get_release_version()

            camilladsp_task = update_service.get_latest_github_version("camilladsp")

            satellites, snapclient_github, server_payload, server_release, camilladsp_github = (
                await asyncio.gather(
                    satellites_task, snapclient_task, payload_task, release_task, camilladsp_task
                )
            )

            latest_version = snapclient_github.get("version") if snapclient_github.get("status") == "success" else None
            camilladsp_latest = camilladsp_github.get("version") if camilladsp_github.get("status") == "success" else None

            for satellite in satellites:
                mac = satellite.get("mac_id")
                # In-flight update flags, keyed to the same active_updates entries
                # the POST routes register — so a freshly loaded client can show
                # "updating" without having seen the WS progress deltas.
                satellite["updating"] = f"satellite_{mac}" in active_updates
                satellite["app_updating"] = f"satellite_app_{mac}" in active_updates
                satellite["camilladsp_updating"] = f"satellite_camilladsp_{mac}" in active_updates

                # Not "is the target newer": the server now sends a version
                # below the installed one too — ending a trial of an unvalidated
                # release, or following a manifest rolled back. Asking only
                # about newer leaves such a satellite reading "up to date" with
                # a disabled button and no way back.
                satellite["latest_version"] = latest_version
                satellite["update_available"] = bool(latest_version) and (
                    satellite.get("snapclient_version") != latest_version
                )
                # Displayed and decided are two different values. The row
                # shows the release — the same numbering as the server's own row,
                # because both halves ship from one commit — while the button is
                # lit by the payload, the fingerprint of the `milo-client/` tree
                # the tarball actually carries. Most releases do not touch that
                # directory, and deciding on the release lit the button across
                # the whole fleet for a byte-identical push every time.
                satellite["server_release"] = server_release
                satellite["app_update_available"] = (
                    bool(server_payload) and satellite.get("app_payload") != server_payload
                )
                # CamillaDSP update
                satellite["camilladsp_latest_version"] = camilladsp_latest
                satellite["camilladsp_update_available"] = bool(camilladsp_latest) and (
                    satellite.get("camilladsp_version") != camilladsp_latest
                )

            return {
                "status": "success",
                "satellites": satellites,
                "count": len(satellites)
            }
        except Exception as e:
            # Same resilience pattern as GET "" above, same duty to log: the
            # satellite list is built from four awaits, and a discovery that
            # throws used to leave the screen showing "no satellite" with
            # nothing anywhere to say why.
            logger.error(f"Error listing satellites: {e}")
            return {
                "status": "error",
                "message": str(e),
                "satellites": [],
                "count": 0
            }

    async def _fleet_target(program_key: str):
        """The version the fleet must run — the server's, resolved once here.

        A satellite has no manifest and no GitHub token; letting it read
        `releases/latest` for itself is what put a client on a version the row
        that started it never named. Unresolvable means the update does not
        start: an unpinned install is worse than a refusal.
        """
        latest = await update_service.get_latest_github_version(program_key)
        if latest.get("status") != "success":
            logger.error(
                f"Cannot resolve the {program_key} version for the fleet: "
                f"{latest.get('message', 'unknown error')}"
            )
            return None
        return latest.get("version")

    @router.post("/satellites/{mac_id}/update")
    async def update_satellite(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite update in the background"""

        satellite_key = f"satellite_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            return _refuse(blocker, satellite_key)

        target_version = await _fleet_target("multiroom")
        if not target_version:
            active_updates.discard(satellite_key)
            return {
                "status": "error",
                "message": "Could not resolve the snapclient version to install"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite(mac_id, target_version),
            progress_event_cls=SatelliteUpdateProgress,
            complete_event_cls=SatelliteUpdateComplete,
            identifier={"mac_id": mac_id},
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for satellite {mac_id}"
        }

    @router.post("/satellites/{mac_id}/update-app")
    async def update_satellite_app(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite app update in the background"""

        satellite_key = f"satellite_app_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            return _refuse(blocker, satellite_key)

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite_app(mac_id),
            progress_event_cls=SatelliteAppUpdateProgress,
            complete_event_cls=SatelliteAppUpdateComplete,
            identifier={"mac_id": mac_id},
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"App update started for satellite {mac_id}"
        }

    @router.post("/satellites/{mac_id}/update-camilladsp")
    async def update_satellite_camilladsp(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite CamillaDSP update in the background"""

        satellite_key = f"satellite_camilladsp_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            return _refuse(blocker, satellite_key)

        target_version = await _fleet_target("camilladsp")
        if not target_version:
            active_updates.discard(satellite_key)
            return {
                "status": "error",
                "message": "Could not resolve the CamillaDSP version to install"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite_camilladsp(mac_id, target_version),
            progress_event_cls=SatelliteCamillaDspUpdateProgress,
            complete_event_cls=SatelliteCamillaDspUpdateComplete,
            identifier={"mac_id": mac_id},
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"CamillaDSP update started for satellite {mac_id}"
        }

    # === GENERIC ROUTES (must come AFTER specific routes) ===

    @router.get("/{program_key}/installed")
    async def get_program_installed_version(program_key: str):
        """Retrieve only the installed version of a program"""
        try:
            result = await update_service.get_installed_version(program_key)
            return {
                "status": "success",
                "installed": result
            }
        except Exception as e:
            # Same resilience pattern as the two reads above, same duty to log.
            logger.error(f"Error reading installed version of {program_key}: {e}")
            return {
                "status": "error",
                "message": str(e),
                "installed": None
            }

    @router.post("/{program_key}/update")
    async def update_program(
        program_key: str,
        payload: ProgramUpdateRequest,
        background_tasks: BackgroundTasks
    ):
        """Launch a local program update in the background"""

        blocker = _claim_update(program_key)
        if blocker:
            return _refuse(blocker, program_key)

        try:
            can_update = await update_service.can_update_program(program_key, payload.target)
        except Exception:
            active_updates.discard(program_key)
            raise

        if not can_update.get("can_update"):
            active_updates.discard(program_key)
            return {
                "status": "error",
                "message": can_update.get("reason", "Cannot update")
            }

        async def _deactivate_if_needed():
            audio_source = PROGRAM_TO_AUDIO_SOURCE.get(program_key)
            if audio_source and state_machine.system_state.active_source == audio_source:
                await state_machine.transition_to_source(AudioSource.NONE)

        do_update = _create_background_update(
            update_key=program_key,
            update_fn=lambda: update_service.update_program(program_key, payload.target),
            progress_event_cls=ProgramUpdateProgress,
            complete_event_cls=ProgramUpdateComplete,
            identifier={"program": program_key},
            pre_update_fn=_deactivate_if_needed,
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for {program_key}",
            "available_version": can_update.get("available_version")
        }

    return router
