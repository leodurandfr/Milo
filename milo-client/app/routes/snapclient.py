"""
Snapclient management routes for Milo Client.
"""
import asyncio
import time
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks

from atomic_file import write_atomic
from services.server_address_watch import RestartFailed, restart_snapclient
from services.snapclient import SnapclientService
from models import ProgramUpdateRequest, SnapclientConfigUpdate

logger = logging.getLogger(__name__)

# The units' EnvironmentFile, and the one file an operator may edit by hand
# (MILO_PRINCIPAL_IP, ENVIRONMENT.md): replaced whole, so a power cut during a
# push never truncates it and sends the speaker looking for its server anew.
ENV_FILE = Path("/var/lib/milo-client/env")


def create_snapclient_router(snapclient_service: SnapclientService) -> APIRouter:
    """Creates snapclient router with injected dependencies."""
    router = APIRouter(tags=["snapclient"])

    @router.post("/update")
    async def update_snapclient(payload: ProgramUpdateRequest, background_tasks: BackgroundTasks):
        """Installs the snapclient version the server names."""
        if snapclient_service.update_in_progress:
            raise HTTPException(status_code=409, detail="Update already in progress")

        target_version = payload.target_version

        # Not "is the target newer": the server also sends a version *below*
        # the installed one, to end a trial or to follow a manifest rollback.
        current_version = await snapclient_service.get_installed_version()
        if current_version == target_version:
            return {
                "status": "success",
                "started": False,
                "message": "Already up to date",
                "current_version": current_version,
                "target_version": target_version
            }

        # Start the update in background
        async def do_update():
            result = await snapclient_service.update_snapclient(target_version, payload.sha256)
            logger.info(f"Update completed: {result}")

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "started": True,
            "message": f"Update started: {current_version} -> {target_version}",
            "current_version": current_version,
            "target_version": target_version
        }

    @router.get("/update/status")
    async def get_update_status():
        """Gets the status of the ongoing update."""
        return {
            "update_in_progress": snapclient_service.update_in_progress,
            "last_result": snapclient_service.last_result,
            "timestamp": int(time.time())
        }

    @router.put("/snapclient/config")
    async def update_snapclient_config(payload: SnapclientConfigUpdate):
        """Update snapclient ALSA buffer configuration and restart the service.

        Idempotent: skips restart if values are already current.
        """
        buffer_time = payload.buffer_time
        fragments = payload.fragments

        try:
            content = await asyncio.to_thread(ENV_FILE.read_text)
        except FileNotFoundError:
            logger.error(f"Snapclient config not applied: {ENV_FILE} is missing")
            raise HTTPException(status_code=500, detail="Environment file not found") from None
        lines = content.split('\n')
        updated_lines = []
        found_buffer_time = False
        found_fragments = False
        current_buffer_time = None
        current_fragments = None

        for line in lines:
            if line.startswith('MILO_SNAPCLIENT_BUFFER_TIME='):
                current_buffer_time = line.split('=', 1)[1].strip()
                updated_lines.append(f'MILO_SNAPCLIENT_BUFFER_TIME={buffer_time}')
                found_buffer_time = True
            elif line.startswith('MILO_SNAPCLIENT_FRAGMENTS='):
                current_fragments = line.split('=', 1)[1].strip()
                updated_lines.append(f'MILO_SNAPCLIENT_FRAGMENTS={fragments}')
                found_fragments = True
            else:
                updated_lines.append(line)

        if not found_buffer_time:
            updated_lines.append(f'MILO_SNAPCLIENT_BUFFER_TIME={buffer_time}')
        if not found_fragments:
            updated_lines.append(f'MILO_SNAPCLIENT_FRAGMENTS={fragments}')

        # Skip write and restart if values are already current
        if current_buffer_time == str(buffer_time) and current_fragments == str(fragments):
            return {"status": "success", "buffer_time": buffer_time, "fragments": fragments, "changed": False}

        new_content = '\n'.join(updated_lines)
        if not new_content.endswith('\n'):
            new_content += '\n'

        try:
            await asyncio.to_thread(write_atomic, ENV_FILE, new_content)
        except OSError as e:
            logger.error(f"Snapclient config not applied: {ENV_FILE} not written: {e}")
            raise HTTPException(status_code=500, detail=f"Environment file not written: {e}") from e

        try:
            await restart_snapclient()
        except (RestartFailed, OSError) as e:
            # The old values go back, so the next push of the same pair reads
            # as a change and restarts again; a unit left stopped is started
            # by the address watcher, which now owes it a start.
            await asyncio.to_thread(write_atomic, ENV_FILE, content)
            logger.error(f"Snapclient config not applied: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

        logger.info(f"Snapclient config updated: buffer_time={buffer_time}ms, fragments={fragments}")
        return {"status": "success", "buffer_time": buffer_time, "fragments": fragments, "changed": True}

    return router
