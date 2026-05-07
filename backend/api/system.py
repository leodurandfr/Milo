# backend/api/system.py
"""
System power management + status routes (restart, shutdown, hostname conflict).
"""
from fastapi import APIRouter
import asyncio
import logging

logger = logging.getLogger(__name__)


def create_system_router(hostname_conflict_service=None):
    router = APIRouter()

    async def _delayed_exec(command: str, label: str):
        """Execute a system command after a short delay to allow HTTP response to be sent."""
        await asyncio.sleep(2)
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
        except Exception as e:
            logger.error(f"System {label} failed: {e}")

    @router.post("/restart")
    async def restart_system():
        """Reboot the Raspberry Pi."""
        logger.info("System restart requested")
        asyncio.create_task(_delayed_exec("reboot", "restart"))
        return {"status": "success"}

    @router.post("/shutdown")
    async def shutdown_system():
        """Shut down the Raspberry Pi."""
        logger.info("System shutdown requested")
        asyncio.create_task(_delayed_exec("poweroff", "shutdown"))
        return {"status": "success"}

    @router.get("/status")
    async def get_system_status():
        """Return system-level status (currently: hostname conflict detection)."""
        if hostname_conflict_service is None:
            return {"status": "success", "data": {"hostname_conflict": False}}
        return {"status": "success", "data": hostname_conflict_service.get_state()}

    @router.post("/recheck-hostname")
    async def recheck_hostname():
        """Trigger an immediate hostname conflict re-check (manual button)."""
        if hostname_conflict_service is None:
            return {"status": "success", "data": {"hostname_conflict": False}}
        await hostname_conflict_service.check()
        return {"status": "success", "data": hostname_conflict_service.get_state()}

    return router
