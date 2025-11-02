# backend/presentation/api/routes/programs.py
"""
Routes API pour la gestion des programmes - Version complète avec satellites
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any
from backend.infrastructure.services.program_version_service import ProgramVersionService
from backend.infrastructure.services.program_update_service import ProgramUpdateService
from backend.infrastructure.services.satellite_program_update_service import SatelliteProgramUpdateService

def create_programs_router(ws_manager, snapcast_service):
    """Router pour les programmes locaux et satellites"""
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    program_service = ProgramVersionService()
    update_service = ProgramUpdateService()
    satellite_service = SatelliteProgramUpdateService(snapcast_service)

    # Store pour suivre les mises à jour en cours
    active_updates = {}

    # === ROUTES SPÉCIFIQUES (doivent être AVANT les routes génériques) ===

    @router.get("")
    async def get_all_programs():
        """Récupère le statut de tous les programmes locaux (installés + GitHub)"""
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
        """Récupère la liste des programmes configurés"""
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

    # === ROUTES SATELLITES (spécifiques, avant les routes génériques) ===

    @router.get("/satellites")
    async def get_satellites():
        """Récupère la liste des satellites détectés avec leurs versions"""
        try:
            satellites = await satellite_service.discover_satellites()

            # Enrichir avec version disponible et update_available
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
        """Récupère le statut d'un satellite spécifique"""
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
        """Lance la mise à jour d'un satellite en arrière-plan"""

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
        """Récupère le statut de mise à jour d'un satellite"""
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

    # === ROUTES GÉNÉRIQUES (doivent être APRÈS les routes spécifiques) ===

    @router.get("/{program_key}")
    async def get_program_details(program_key: str):
        """Récupère les détails d'un programme spécifique"""
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
        """Récupère uniquement la version installée d'un programme"""
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
        """Récupère uniquement la dernière version depuis GitHub"""
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
        """Vérifie si un programme peut être mis à jour"""
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
        """Lance la mise à jour d'un programme local en arrière-plan"""

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
        """Récupère le statut de mise à jour d'un programme"""
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
