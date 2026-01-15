"""
Snapclient management routes for Milo Client.
"""
import time
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks

from services.snapclient import SnapclientService

logger = logging.getLogger(__name__)


def create_snapclient_router(snapclient_service: SnapclientService) -> APIRouter:
    """Creates snapclient router with injected dependencies."""
    router = APIRouter(tags=["snapclient"])

    @router.get("/version")
    async def get_version():
        """Gets only the snapclient version."""
        try:
            version = await snapclient_service.get_installed_version()

            if version:
                return {
                    "version": version,
                    "timestamp": int(time.time())
                }
            else:
                return {
                    "version": None,
                    "error": "Could not determine snapclient version",
                    "timestamp": int(time.time())
                }

        except Exception as e:
            logger.error(f"Error getting version: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/update")
    async def update_snapclient(background_tasks: BackgroundTasks):
        """Starts the snapclient update from GitHub."""
        if snapclient_service.update_in_progress:
            raise HTTPException(status_code=409, detail="Update already in progress")

        try:
            # Get the latest available version on GitHub
            latest_version = await snapclient_service.get_latest_github_version()
            if not latest_version:
                raise HTTPException(status_code=500, detail="Could not determine latest version")

            # Check if an update is needed
            current_version = await snapclient_service.get_installed_version()
            if current_version == latest_version:
                return {
                    "success": False,
                    "message": "Already up to date",
                    "current_version": current_version,
                    "latest_version": latest_version
                }

            # Start the update in background
            async def do_update():
                result = await snapclient_service.update_snapclient(latest_version)
                logger.info(f"Update completed: {result}")

            background_tasks.add_task(do_update)

            return {
                "success": True,
                "message": f"Update started: {current_version} -> {latest_version}",
                "current_version": current_version,
                "target_version": latest_version
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error starting update: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/update/status")
    async def get_update_status():
        """Gets the status of the ongoing update."""
        return {
            "update_in_progress": snapclient_service.update_in_progress,
            "timestamp": int(time.time())
        }

    return router
