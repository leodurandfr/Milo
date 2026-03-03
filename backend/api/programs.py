# backend/api/programs.py
"""
API routes for program management — Full version with satellites
"""
from fastapi import APIRouter, BackgroundTasks
from backend.core.models.audio_state import AudioSource
from backend.core.updates.helpers import compare_versions, extract_base_tag

# Mapping from program key to AudioSource for pre-update deactivation
PROGRAM_TO_AUDIO_SOURCE = {
    'go-librespot': AudioSource.SPOTIFY,
    'shairport-sync': AudioSource.AIRPLAY,
}

def create_programs_router(ws_manager, update_service, satellite_update_service, state_machine):
    """Router for local and satellite programs

    Args:
        ws_manager: WebSocket manager for broadcasting updates
        update_service: Singleton service for version checks and updates
        satellite_update_service: Singleton service for satellite updates
        state_machine: AudioStateMachine for deactivating active sources before update
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    satellite_service = satellite_update_service

    # Store to track ongoing updates
    active_updates = {}

    def _create_background_update(
        update_key: str,
        update_fn,
        progress_event_type: str,
        complete_event_type: str,
        ws_source: str,
        identifier_data: dict,
        default_success_msg: str = "Update completed",
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
            await ws_manager.broadcast_dict({
                "category": "programs",
                "type": progress_event_type,
                "source": ws_source,
                "data": {**identifier_data, "progress": progress, "message": message, "status": "updating"}
            })

        async def do_update():
            try:
                if pre_update_fn:
                    await pre_update_fn(progress_callback)

                result = await update_fn(progress_callback)
                del active_updates[update_key]

                if result["success"]:
                    success_data = {**identifier_data, "success": True, "message": result.get("message", default_success_msg)}
                    for key in ("new_version", "old_version"):
                        if key in result:
                            success_data[key] = result[key]
                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": complete_event_type,
                        "source": ws_source,
                        "data": success_data
                    })
                else:
                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": complete_event_type,
                        "source": ws_source,
                        "data": {**identifier_data, "success": False, "error": result.get("error", "Update failed")}
                    })

            except Exception as e:
                if update_key in active_updates:
                    del active_updates[update_key]
                await ws_manager.broadcast_dict({
                    "category": "programs",
                    "type": complete_event_type,
                    "source": ws_source,
                    "data": {**identifier_data, "success": False, "error": str(e)}
                })

        return do_update

    # === SPECIFIC ROUTES (must come BEFORE generic routes) ===

    @router.get("")
    async def get_all_programs():
        """Retrieve the status of all local programs (installed + GitHub)"""
        try:
            results = await update_service.get_all_program_status()
            return {
                "status": "success",
                "programs": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "programs": {},
                "count": 0
            }

    @router.get("/list")
    async def get_program_list():
        """Retrieve the list of configured programs"""
        try:
            programs = update_service.get_program_list()
            return {
                "status": "success",
                "programs": programs
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "programs": []
            }

    # === SATELLITE ROUTES (specific, before generic routes) ===

    @router.get("/satellites")
    async def get_satellites():
        """Retrieve the list of detected satellites with their versions"""
        try:
            satellites = await satellite_service.discover_satellites()

            # Enrich with available snapclient version and update_available
            latest_version = await satellite_service._get_latest_snapclient_version()
            server_version = await satellite_service._get_server_version()

            for satellite in satellites:
                satellite["latest_version"] = latest_version
                satellite["update_available"] = compare_versions(
                    satellite.get("snapclient_version"),
                    latest_version
                )
                # App update: compare base tags (v0.0.1-347-g14ee633 -> v0.0.1)
                satellite["server_version"] = server_version
                sat_app_version = satellite.get("app_version")
                satellite["app_update_available"] = compare_versions(
                    extract_base_tag(sat_app_version),
                    extract_base_tag(server_version)
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

    @router.get("/satellites/{hostname}")
    async def get_satellite_status(hostname: str):
        """Retrieve the status of a specific satellite"""
        try:
            result = await satellite_service.get_satellite_status(hostname)
            return result
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    @router.post("/satellites/{hostname}/update")
    async def update_satellite(hostname: str, background_tasks: BackgroundTasks):
        """Launch a satellite update in the background"""

        satellite_key = f"satellite_{hostname}"

        if satellite_key in active_updates:
            return {
                "status": "error",
                "message": f"Update already in progress for {hostname}"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda cb: satellite_service.update_satellite(hostname, cb),
            progress_event_type="satellite_update_progress",
            complete_event_type="satellite_update_complete",
            ws_source="satellite_update",
            identifier_data={"hostname": hostname},
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for satellite {hostname}"
        }

    @router.post("/satellites/{hostname}/update-app")
    async def update_satellite_app(hostname: str, background_tasks: BackgroundTasks):
        """Launch a satellite app update in the background"""

        satellite_key = f"satellite_app_{hostname}"

        if satellite_key in active_updates:
            return {
                "status": "error",
                "message": f"App update already in progress for {hostname}"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda cb: satellite_service.update_satellite_app(hostname, cb),
            progress_event_type="satellite_app_update_progress",
            complete_event_type="satellite_app_update_complete",
            ws_source="satellite_update",
            identifier_data={"hostname": hostname},
            default_success_msg="App update completed",
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"App update started for satellite {hostname}"
        }

    @router.get("/satellites/{hostname}/update-status")
    async def get_satellite_update_status(hostname: str):
        """Retrieve the update status of a satellite"""
        satellite_key = f"satellite_{hostname}"

        if satellite_key in active_updates:
            return {
                "status": "success",
                "updating": True,
                **active_updates[satellite_key]
            }
        else:
            return {
                "status": "success",
                "updating": False,
                "message": "No update in progress"
            }

    # === GENERIC ROUTES (must come AFTER specific routes) ===

    @router.get("/{program_key}")
    async def get_program_details(program_key: str):
        """Retrieve the details of a specific program"""
        try:
            result = await update_service._get_program_full_status(program_key)
            return {
                "status": "success",
                "program": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "program": None
            }

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

    @router.get("/{program_key}/latest")
    async def get_program_latest_version(program_key: str):
        """Retrieve only the latest version from GitHub"""
        try:
            result = await update_service.get_latest_github_version(program_key)
            return {
                "status": "success",
                "latest": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "latest": None
            }

    @router.get("/{program_key}/can-update")
    async def check_can_update_program(program_key: str):
        """Check if a program can be updated"""
        try:
            result = await update_service.can_update_program(program_key)
            return {
                "status": "success",
                **result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "can_update": False
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
            progress_event_type="program_update_progress",
            complete_event_type="program_update_complete",
            ws_source="program_update",
            identifier_data={"program": program_key},
            pre_update_fn=_deactivate_if_needed,
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for {program_key}",
            "available_version": can_update.get("available_version")
        }

    @router.get("/{program_key}/update-status")
    async def get_update_status(program_key: str):
        """Retrieve the update status of a program"""
        if program_key in active_updates:
            return {
                "status": "success",
                "updating": True,
                **active_updates[program_key]
            }
        else:
            return {
                "status": "success",
                "updating": False,
                "message": "No update in progress"
            }

    return router