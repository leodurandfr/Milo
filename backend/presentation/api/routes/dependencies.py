# backend/presentation/api/routes/dependencies.py
"""
Routes API pour la gestion des dépendances - Version complète avec satellites
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any
from backend.infrastructure.services.dependency_version_service import DependencyVersionService
from backend.infrastructure.services.dependency_update_service import DependencyUpdateService
from backend.infrastructure.services.satellite_dependency_update_service import SatelliteDependencyUpdateService

def create_dependencies_router(ws_manager, snapcast_service):
    """Router pour les dépendances locales et satellites"""
    router = APIRouter(prefix="/api/dependencies", tags=["dependencies"])
    
    dependency_service = DependencyVersionService()
    update_service = DependencyUpdateService()
    satellite_service = SatelliteDependencyUpdateService(snapcast_service)
    
    # Store pour suivre les mises à jour en cours
    active_updates = {}
    
    # === ROUTES DÉPENDANCES LOCALES ===
    
    @router.get("")
    async def get_all_dependencies():
        """Récupère le statut de toutes les dépendances locales (installées + GitHub)"""
        try:
            results = await dependency_service.get_all_dependency_status()
            return {
                "status": "success",
                "dependencies": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependencies": {},
                "count": 0
            }
    
    @router.get("/list")
    async def get_dependency_list():
        """Récupère la liste des dépendances configurées"""
        try:
            dependencies = dependency_service.get_dependency_list()
            return {
                "status": "success",
                "dependencies": dependencies
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependencies": []
            }
    
    @router.get("/{dependency_key}")
    async def get_dependency_details(dependency_key: str):
        """Récupère les détails d'une dépendance spécifique"""
        try:
            result = await dependency_service._get_dependency_full_status(dependency_key)
            return {
                "status": "success",
                "dependency": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependency": None
            }
    
    @router.get("/{dependency_key}/installed")
    async def get_dependency_installed_version(dependency_key: str):
        """Récupère uniquement la version installée d'une dépendance"""
        try:
            result = await dependency_service.get_installed_version(dependency_key)
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
    
    @router.get("/{dependency_key}/latest")
    async def get_dependency_latest_version(dependency_key: str):
        """Récupère uniquement la dernière version depuis GitHub"""
        try:
            result = await dependency_service.get_latest_github_version(dependency_key)
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
    
    @router.get("/{dependency_key}/can-update")
    async def check_can_update_dependency(dependency_key: str):
        """Vérifie si une dépendance peut être mise à jour"""
        try:
            result = await update_service.can_update_dependency(dependency_key)
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
    
    @router.post("/{dependency_key}/update")
    async def update_dependency(dependency_key: str, background_tasks: BackgroundTasks):
        """Lance la mise à jour d'une dépendance locale en arrière-plan"""
        
        if dependency_key in active_updates:
            return {
                "status": "error",
                "message": "Update already in progress for this dependency"
            }
        
        can_update = await update_service.can_update_dependency(dependency_key)
        if not can_update.get("can_update"):
            return {
                "status": "error", 
                "message": can_update.get("reason", "Cannot update")
            }
        
        active_updates[dependency_key] = {
            "status": "starting",
            "progress": 0,
            "message": "Initializing update..."
        }
        
        async def progress_callback(message: str, progress: int):
            active_updates[dependency_key] = {
                "status": "updating",
                "progress": progress,
                "message": message
            }
            
            await ws_manager.broadcast_dict({
                "category": "dependencies",
                "type": "dependency_update_progress",
                "source": "dependency_update",
                "data": {
                    "dependency": dependency_key,
                    "progress": progress,
                    "message": message,
                    "status": "updating"
                }
            })
        
        async def do_update():
            try:
                result = await update_service.update_dependency(dependency_key, progress_callback)
                
                if result["success"]:
                    del active_updates[dependency_key]
                    
                    await ws_manager.broadcast_dict({
                        "category": "dependencies",
                        "type": "dependency_update_complete",
                        "source": "dependency_update",
                        "data": {
                            "dependency": dependency_key,
                            "success": True,
                            "message": result.get("message", "Update completed"),
                            "old_version": result.get("old_version"),
                            "new_version": result.get("new_version")
                        }
                    })
                else:
                    del active_updates[dependency_key]
                    
                    await ws_manager.broadcast_dict({
                        "category": "dependencies",
                        "type": "dependency_update_complete",
                        "source": "dependency_update",
                        "data": {
                            "dependency": dependency_key,
                            "success": False,
                            "error": result.get("error", "Update failed")
                        }
                    })
                    
            except Exception as e:
                if dependency_key in active_updates:
                    del active_updates[dependency_key]
                
                await ws_manager.broadcast_dict({
                    "category": "dependencies",
                    "type": "dependency_update_complete",
                    "source": "dependency_update",
                    "data": {
                        "dependency": dependency_key,
                        "success": False,
                        "error": str(e)
                    }
                })
        
        background_tasks.add_task(do_update)
        
        return {
            "status": "success",
            "message": f"Update started for {dependency_key}",
            "available_version": can_update.get("available_version")
        }
    
    @router.get("/{dependency_key}/update-status")
    async def get_update_status(dependency_key: str):
        """Récupère le statut de mise à jour d'une dépendance"""
        if dependency_key in active_updates:
            return {
                "status": "success",
                "updating": True,
                **active_updates[dependency_key]
            }
        else:
            return {
                "status": "success",
                "updating": False,
                "message": "No update in progress"
            }
    
    # === ROUTES SATELLITES ===
    
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
                "category": "dependencies",
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
                        "category": "dependencies",
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
                        "category": "dependencies",
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
                    "category": "dependencies",
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
    
    return router