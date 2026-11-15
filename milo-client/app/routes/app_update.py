"""
App update routes for Milo Client.
"""
import os
import time
import tempfile
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from services.app_update import AppUpdateService, trial_payload

logger = logging.getLogger(__name__)


def create_app_update_router(app_update_service: AppUpdateService) -> APIRouter:
    """Creates app update router with injected dependencies."""
    router = APIRouter(prefix="/app", tags=["app-update"])

    @router.post("/update")
    async def update_app(
        tarball: UploadFile = File(...),
        payload: str = Form(...),
        version: str = Form(...),
    ):
        """Receives and deploys an app update tarball from the main server.

        409 while an update runs or a pushed version is still on trial: a second
        push would replace the trees the first one's rollback needs.
        """
        if app_update_service.update_in_progress:
            raise HTTPException(status_code=409, detail="Update already in progress")
        on_trial = trial_payload()
        if on_trial is not None:
            raise HTTPException(status_code=409, detail=f"An update to {on_trial} is still on trial")

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
        result = await app_update_service.deploy_update(tarball_path, payload, version)

        if not result["success"]:
            status = 409 if result.get("conflict") else 500
            raise HTTPException(status_code=status, detail=result.get("error", "Update failed"))

        return {
            "status": "success",
            "payload": payload,
            "version": version,
            "message": "Update deployed, service restarting...",
            "timestamp": int(time.time())
        }

    return router
