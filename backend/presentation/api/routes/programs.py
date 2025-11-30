# backend/presentation/api/routes/programs.py
"""
API routes for program management — Full version with satellites
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any

def create_programs_router(ws_manager, program_version_service, program_update_service, satellite_program_update_service):
    """Router for local and satellite programs

    Args:
        ws_manager: WebSocket manager for broadcasting updates
        program_version_service: Singleton service for version checks
        program_update_service: Singleton service for updates
        satellite_program_update_service: Singleton service for satellite updates
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    # Use injected services (Singletons from container)
    program_service = program_version_service
    update_service = program_update_service
    satellite_service = satellite_program_update_service

    # Store to track ongoing updates
    active_updates = {}

    # === SPECIFIC ROUTES (must come BEFORE generic routes) ===

    @router.get("")
    async def get_all_programs():
        """Retrieve the status of all local programs (installed + GitHub)"""
        try:
            results = await program_service.get_all_program_status()
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
            programs = program_service.get_program_list()
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

            # Enrich with available version and update_available
            latest_version = await satellite_service._get_latest_snapclient_version()

            for satellite in satellites:
                satellite["latest_version"] = latest_version
                satellite["update_available"] = satellite_service._compare_versions(
                    satellite.get("snapclient_version"),
                    latest_version
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

        active_updates[satellite_key] = {
            "status": "starting",
            "progress": 0,
            "message": "Initializing satellite update..."
        }

        async def progress_callback(message: str, progress: int):
            active_updates[satellite_key] = {
                "status": "updating",
                "progress": progress,
                "message": message
            }

            await ws_manager.broadcast_dict({
                "category": "programs",
                "type": "satellite_update_progress",
                "source": "satellite_update",
                "data": {
                    "hostname": hostname,
                    "progress": progress,
                    "message": message,
                    "status": "updating"
                }
            })

        async def do_update():
            try:
                result = await satellite_service.update_satellite(hostname, progress_callback)

                if result["success"]:
                    del active_updates[satellite_key]

                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": "satellite_update_complete",
                        "source": "satellite_update",
                        "data": {
                            "hostname": hostname,
                            "success": True,
                            "message": result.get("message", "Update completed"),
                            "new_version": result.get("new_version")
                        }
                    })
                else:
                    del active_updates[satellite_key]

                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": "satellite_update_complete",
                        "source": "satellite_update",
                        "data": {
                            "hostname": hostname,
                            "success": False,
                            "error": result.get("error", "Update failed")
                        }
                    })

            except Exception as e:
                if satellite_key in active_updates:
                    del active_updates[satellite_key]

                await ws_manager.broadcast_dict({
                    "category": "programs",
                    "type": "satellite_update_complete",
                    "source": "satellite_update",
                    "data": {
                        "hostname": hostname,
                        "success": False,
                        "error": str(e)
                    }
                })

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for satellite {hostname}"
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
            result = await program_service._get_program_full_status(program_key)
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
            result = await program_service.get_installed_version(program_key)
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
            result = await program_service.get_latest_github_version(program_key)
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

        active_updates[program_key] = {
            "status": "starting",
            "progress": 0,
            "message": "Initializing update..."
        }

        async def progress_callback(message: str, progress: int):
            active_updates[program_key] = {
                "status": "updating",
                "progress": progress,
                "message": message
            }

            await ws_manager.broadcast_dict({
                "category": "programs",
                "type": "program_update_progress",
                "source": "program_update",
                "data": {
                    "program": program_key,
                    "progress": progress,
                    "message": message,
                    "status": "updating"
                }
            })

        async def do_update():
            try:
                result = await update_service.update_program(program_key, progress_callback)

                if result["success"]:
                    del active_updates[program_key]

                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": "program_update_complete",
                        "source": "program_update",
                        "data": {
                            "program": program_key,
                            "success": True,
                            "message": result.get("message", "Update completed"),
                            "old_version": result.get("old_version"),
                            "new_version": result.get("new_version")
                        }
                    })
                else:
                    del active_updates[program_key]

                    await ws_manager.broadcast_dict({
                        "category": "programs",
                        "type": "program_update_complete",
                        "source": "program_update",
                        "data": {
                            "program": program_key,
                            "success": False,
                            "error": result.get("error", "Update failed")
                        }
                    })

            except Exception as e:
                if program_key in active_updates:
                    del active_updates[program_key]

                await ws_manager.broadcast_dict({
                    "category": "programs",
                    "type": "program_update_complete",
                    "source": "program_update",
                    "data": {
                        "program": program_key,
                        "success": False,
                        "error": str(e)
                    }
                })

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