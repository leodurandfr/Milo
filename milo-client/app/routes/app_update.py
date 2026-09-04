"""
App update routes for Milo Client.
"""
import os
import time
import tempfile
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from services.app_update import AppUpdateService

logger = logging.getLogger(__name__)


def create_app_update_router(app_update_service: AppUpdateService) -> APIRouter:
    """Creates app update router with injected dependencies."""
    router = APIRouter(prefix="/app", tags=["app-update"])

    @router.post("/update")
    async def update_app(
        tarball: UploadFile = File(...),
        version: str = Form(...),
        release: str = Form(...),
    ):
        """Receives and deploys an app update tarball from the main server."""
        if app_update_service.update_in_progress:
            raise HTTPException(status_code=409, detail="Update already in progress")

        # Save uploaded tarball to temp file
        try:
            fd, tarball_path = tempfile.mkstemp(suffix=".tar.gz", prefix="milo-client-app-")
            with os.fdopen(fd, "wb") as f:
                content = await tarball.read()
                f.write(content)
        except Exception as e:
            logger.error(f"Error saving tarball: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save tarball: {e}")

        # Deploy the update
        result = await app_update_service.deploy_update(tarball_path, version, release)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "Update failed"))

        return {
            "status": "success",
            "version": version,
            "release": release,
            "message": "Update deployed, service restarting...",
            "timestamp": int(time.time())
        }

    return router
