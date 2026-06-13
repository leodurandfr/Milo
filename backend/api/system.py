# backend/api/system.py
"""
System power management + status routes (restart, shutdown, hostname conflict).
"""
from fastapi import APIRouter, BackgroundTasks
import logging

logger = logging.getLogger(__name__)


def create_system_router(systemd_manager, hostname_conflict_service=None, connectivity_service=None):
    router = APIRouter()

    @router.post("/restart")
    async def restart_system(background_tasks: BackgroundTasks):
        """Reboot the Raspberry Pi."""
        logger.info("System restart requested")
        # 2s delay lets the HTTP response flush before the box goes down.
        background_tasks.add_task(systemd_manager.power, "reboot", 2.0)
        return {"status": "success"}

    @router.post("/shutdown")
    async def shutdown_system(background_tasks: BackgroundTasks):
        """Shut down the Raspberry Pi."""
        logger.info("System shutdown requested")
        background_tasks.add_task(systemd_manager.power, "poweroff", 2.0)
        return {"status": "success"}

    @router.get("/status")
    async def get_system_status():
        """Return system-level status (hostname conflict + internet connectivity)."""
        data = {"hostname_conflict": False, "online": True}
        if hostname_conflict_service is not None:
            data.update(hostname_conflict_service.get_state())
        if connectivity_service is not None:
            data.update(connectivity_service.get_state())
        return {"status": "success", "data": data}

    @router.post("/recheck-hostname")
    async def recheck_hostname():
        """Trigger an immediate hostname conflict re-check (manual button)."""
        if hostname_conflict_service is None:
            return {"status": "success", "data": {"hostname_conflict": False}}
        await hostname_conflict_service.check()
        return {"status": "success", "data": hostname_conflict_service.get_state()}

    return router
