# backend/api/programs.py
"""
API routes for program management — Full version with satellites
"""
import asyncio
import logging
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
from backend.core.updates.helpers import compare_versions, extract_base_tag

logger = logging.getLogger(__name__)

# Mapping from program key to AudioSource for pre-update deactivation
PROGRAM_TO_AUDIO_SOURCE = {
    'go-librespot': AudioSource.SPOTIFY,
    'shairport-sync': AudioSource.AIRPLAY,
}

def create_programs_router(update_service, satellite_update_service, state_machine):
    """Router for local and satellite programs

    Args:
        update_service: Singleton service for version checks and updates
        satellite_update_service: Singleton service for satellite updates
        state_machine: AudioStateMachine for broadcasting events and deactivating active sources
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    satellite_service = satellite_update_service

    active_updates = {}

    def _create_background_update(
        update_key: str,
        update_fn,
        progress_event_cls: type[ProgramsProgressEvent],
        complete_event_cls: type[ProgramsCompleteEvent],
        identifier: dict,
        pre_update_fn=None,
    ):
        """Create a background update task with progress tracking and WS broadcasting.

        Returns an async do_update coroutine to pass to background_tasks.add_task().
        """
        active_updates[update_key] = {
            "status": "starting",
            "progress": 0,
            "message": "Initializing update..."
        }

        async def progress_callback(message: str, progress: int):
            active_updates[update_key] = {
                "status": "updating",
                "progress": progress,
                "message": message
            }
            # Broadcast carries status only; progress/message live in
            # active_updates for the REST reconstruction path (GET /programs).
            await state_machine.broadcast(progress_event_cls(**identifier))

        async def do_update():
            try:
                if pre_update_fn:
                    await pre_update_fn(progress_callback)

                result = await update_fn(progress_callback)
                del active_updates[update_key]

                if not result["success"]:
                    logger.error(f"Update {update_key} failed: {result.get('error', 'Update failed')}")

                await state_machine.broadcast(
                    complete_event_cls(**identifier, success=result["success"])
                )

            except Exception as e:
                logger.error(f"Update {update_key} failed: {e}")
                if update_key in active_updates:
                    del active_updates[update_key]
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
            milo_task = update_service.get_installed_version("milo")

            camilladsp_task = update_service.get_latest_github_version("camilladsp")

            satellites, snapclient_github, milo_installed, camilladsp_github = await asyncio.gather(
                satellites_task, snapclient_task, milo_task, camilladsp_task
            )

            latest_version = snapclient_github.get("version") if snapclient_github.get("status") == "success" else None
            server_version = milo_installed.get("raw_version")
            camilladsp_latest = camilladsp_github.get("version") if camilladsp_github.get("status") == "success" else None

            for satellite in satellites:
                mac = satellite.get("mac_id")
                # In-flight update flags, keyed to the same active_updates entries
                # the POST routes register — so a freshly loaded client can show
                # "updating" without having seen the WS progress deltas.
                satellite["updating"] = f"satellite_{mac}" in active_updates
                satellite["app_updating"] = f"satellite_app_{mac}" in active_updates
                satellite["camilladsp_updating"] = f"satellite_camilladsp_{mac}" in active_updates

                satellite["latest_version"] = latest_version
                satellite["update_available"] = compare_versions(
                    satellite.get("snapclient_version"),
                    latest_version
                )
                # App update: compare base tags (v0.0.1-347-g14ee633 -> v0.0.1)
                satellite["server_version"] = server_version
                satellite["app_update_available"] = compare_versions(
                    extract_base_tag(satellite.get("app_version")),
                    extract_base_tag(server_version)
                )
                # CamillaDSP update
                satellite["camilladsp_latest_version"] = camilladsp_latest
                satellite["camilladsp_update_available"] = compare_versions(
                    satellite.get("camilladsp_version"),
                    camilladsp_latest
                )

            return {
                "status": "success",
                "satellites": satellites,
                "count": len(satellites)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "satellites": [],
                "count": 0
            }

    @router.post("/satellites/{mac_id}/update")
    async def update_satellite(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite update in the background"""

        satellite_key = f"satellite_{mac_id}"

        if satellite_key in active_updates:
            return {
                "status": "error",
                "message": f"Update already in progress for {mac_id}"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda cb: satellite_service.update_satellite(mac_id, cb),
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

        if satellite_key in active_updates:
            return {
                "status": "error",
                "message": f"App update already in progress for {mac_id}"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda cb: satellite_service.update_satellite_app(mac_id, cb),
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

        if satellite_key in active_updates:
            return {
                "status": "error",
                "message": f"CamillaDSP update already in progress for {mac_id}"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda cb: satellite_service.update_satellite_camilladsp(mac_id, cb),
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
            return {
                "status": "error",
                "message": str(e),
                "installed": None
            }

    @router.post("/{program_key}/update")
    async def update_program(program_key: str, background_tasks: BackgroundTasks):
        """Launch a local program update in the background"""

        if program_key in active_updates:
            return {
                "status": "error",
                "message": "Update already in progress for this program"
            }

        can_update = await update_service.can_update_program(program_key)
        if not can_update.get("can_update"):
            return {
                "status": "error",
                "message": can_update.get("reason", "Cannot update")
            }

        async def _deactivate_if_needed(progress_callback):
            audio_source = PROGRAM_TO_AUDIO_SOURCE.get(program_key)
            if audio_source and state_machine.system_state.active_source == audio_source:
                await progress_callback("updates.progress.stoppingActiveSource", 2)
                await state_machine.transition_to_source(AudioSource.NONE)

        do_update = _create_background_update(
            update_key=program_key,
            update_fn=lambda cb: update_service.update_program(program_key, cb),
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
