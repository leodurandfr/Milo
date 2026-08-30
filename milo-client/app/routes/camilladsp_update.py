"""
CamillaDSP update routes for Milo Client.
"""
import time
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks

from models import ProgramUpdateRequest
from services.camilladsp_update import CamillaDSPUpdateService

logger = logging.getLogger(__name__)


def create_camilladsp_update_router(camilladsp_update_service: CamillaDSPUpdateService) -> APIRouter:
    """Creates CamillaDSP update router with injected dependencies."""
    router = APIRouter(tags=["camilladsp-update"])

    @router.post("/camilladsp/update")
    async def update_camilladsp(payload: ProgramUpdateRequest, background_tasks: BackgroundTasks):
        """Installs the CamillaDSP version the server names."""
        if camilladsp_update_service.update_in_progress:
            raise HTTPException(status_code=409, detail="Update already in progress")

        try:
            target_version = payload.target_version

            current_version = await camilladsp_update_service.get_installed_version()
            if current_version == target_version:
                return {
                    "status": "success",
                    "started": False,
                    "message": "Already up to date",
                    "current_version": current_version,
                    "target_version": target_version
                }

            async def do_update():
                result = await camilladsp_update_service.update_camilladsp(target_version)
                logger.info(f"CamillaDSP update completed: {result}")

            background_tasks.add_task(do_update)

            return {
                "status": "success",
                "started": True,
                "message": f"Update started: {current_version} -> {target_version}",
                "current_version": current_version,
                "target_version": target_version
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error starting CamillaDSP update: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/camilladsp/update/status")
    async def get_update_status():
        """Gets the status of the ongoing CamillaDSP update."""
        return {
            "update_in_progress": camilladsp_update_service.update_in_progress,
            "timestamp": int(time.time())
        }

    return router
