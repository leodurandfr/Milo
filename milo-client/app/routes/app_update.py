"""
App update routes for Milo Client.
"""
import os
import time
import tempfile
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from server_secret import guard
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
        # Optional on the form, like any field added to the frozen set
        # (backend/tests/contracts/test_milo_client_contract.py), and refused
        # below when absent: a 403 that says why, not a 422.
        server_secret: Optional[str] = Form(None),
    ):
        """Receives and deploys an app update tarball from the main server.

        Only from this unit's server (`server_secret.py`): the tarball is
        installed as root, sudoers included. 409 while an update runs or a
        pushed version is still on trial: a second push would replace the
        trees the first one's rollback needs.
        """
        await guard(server_secret, "POST /app/update")
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
        except OSError as e:
            logger.error(f"Error saving tarball: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save tarball: {e}") from e

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
