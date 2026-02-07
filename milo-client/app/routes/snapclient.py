"""
Snapclient management routes for Milo Client.
"""
import asyncio
import time
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks

from services.snapclient import SnapclientService
from models import SnapclientConfigUpdate

logger = logging.getLogger(__name__)

ENV_FILE = Path("/var/lib/milo-client/env")


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

    @router.put("/snapclient/config")
    async def update_snapclient_config(payload: SnapclientConfigUpdate):
        """Update snapclient ALSA buffer configuration and restart the service.

        Idempotent: skips restart if values are already current.
        """
        buffer_time = max(20, min(200, payload.buffer_time))
        fragments = max(2, min(8, payload.fragments))

        try:
            if not ENV_FILE.exists():
                raise HTTPException(status_code=500, detail="Environment file not found")

            content = ENV_FILE.read_text()
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
                return {"success": True, "buffer_time": buffer_time, "fragments": fragments, "changed": False}

            new_content = '\n'.join(updated_lines)
            if not new_content.endswith('\n'):
                new_content += '\n'

            ENV_FILE.write_text(new_content)

            # Restart snapclient service (sudoers allows stop/start, not restart)
            for action in ["stop", "start"]:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", action, "milo-client-snapclient.service",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise HTTPException(status_code=500, detail=f"Failed to {action} snapclient: {stderr.decode()}")

            logger.info(f"Snapclient config updated: buffer_time={buffer_time}ms, fragments={fragments}")
            return {"success": True, "buffer_time": buffer_time, "fragments": fragments, "changed": True}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating snapclient config: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
