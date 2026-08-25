# backend/api/system.py
"""
System power management + status + telemetry routes
(restart, shutdown, hostname conflict, temperature, resources, network).
"""
from fastapi import APIRouter, BackgroundTasks
import asyncio
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.connectivity.service import ConnectivityService
    from backend.core.system.hostname_conflict import HostnameConflictService
    from backend.core.systemd import SystemdServiceManager


logger = logging.getLogger(__name__)


def create_system_router(
    systemd_manager: "SystemdServiceManager",
    hostname_conflict_service: Optional["HostnameConflictService"] = None,
    connectivity_service: Optional["ConnectivityService"] = None
):
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
        data = {"hostname_conflict": False, "connectivity": "unknown"}
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

    # System temperature
    @router.get("/temperature")
    async def get_system_temperature():
        """Retrieve the Raspberry Pi's SoC temperature.

        A `throttling` block used to ride along here, parsed out of a second
        `vcgencmd get_throttled`. Removed 2026-08-25: no consumer ever read it
        (the settings screen reads `temperature` alone, and the route is not in
        Milo-Mac's manifest), and it was wrong — it looked for the "has
        occurred" bits at 19-22 where the Pi sets them at 16-19, so this unit,
        measured at `throttled=0xe0000`, would have been reported as having had
        an under-voltage it never had while its three real events stayed hidden.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                "vcgencmd measure_temp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
            except asyncio.TimeoutError:
                proc.kill()
                logger.error("Timeout reading temperature (vcgencmd measure_temp)")
                return {"status": "success", "temperature": None}

            if proc.returncode == 0:
                output = stdout.decode().strip()
                if output.startswith("temp=") and output.endswith("'C"):
                    temp_str = output.replace("temp=", "").replace("'C", "")
                    return {
                        "status": "success",
                        "temperature": float(temp_str),
                        "unit": "°C",
                    }

            # debug, not warning: the settings screen polls this every 5 s, and a
            # dev host has no vcgencmd at all — the fail-open answer is the point.
            logger.debug("vcgencmd measure_temp gave nothing usable: %r", stdout)
            return {"status": "success", "temperature": None}

        except Exception as e:
            logger.error(f"Failed to read system temperature: {e}")
            return {"status": "error", "message": str(e), "temperature": None}

    # Network info (IP address)
    @router.get("/network-info")
    async def get_network_info():
        """Retrieve the primary local IP address of the Raspberry Pi"""
        try:
            process = await asyncio.create_subprocess_shell(
                "hostname -I",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                output = stdout.decode().strip()
                # hostname -I returns all IPs separated by spaces
                # Take the first one, generally the primary IPv4
                ips = output.split()
                if ips:
                    # Keep only IPv4 (format x.x.x.x)
                    ipv4_ips = [ip for ip in ips if ip.count('.') == 3]
                    if ipv4_ips:
                        return {
                            "status": "success",
                            "ip": ipv4_ips[0]
                        }

            return {
                "status": "error",
                "message": "Unable to retrieve IP address",
                "ip": None
            }

        except Exception as e:
            logger.warning(f"Failed to retrieve network info: {e}")
            return {
                "status": "error",
                "message": str(e),
                "ip": None
            }

    # System resources (CPU + RAM)
    @router.get("/resources")
    async def get_system_resources():
        """Retrieve CPU usage percentage and RAM usage"""
        result = {"status": "success", "cpu_percent": None, "ram": None}

        # CPU usage: read two snapshots of /proc/stat 100ms apart
        def read_cpu_stats():
            with open("/proc/stat", "r") as f:
                line = f.readline()  # First line: cpu total
            fields = line.split()[1:]  # Skip "cpu" label
            fields = [int(x) for x in fields]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
            total = sum(fields)
            return idle, total

        def read_meminfo():
            meminfo = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split()
                    key = parts[0].rstrip(":")
                    meminfo[key] = int(parts[1])  # Value in kB
            return meminfo

        loop = asyncio.get_running_loop()

        try:
            idle1, total1 = await loop.run_in_executor(None, read_cpu_stats)
            await asyncio.sleep(0.1)
            idle2, total2 = await loop.run_in_executor(None, read_cpu_stats)

            total_diff = total2 - total1
            idle_diff = idle2 - idle1
            if total_diff > 0:
                result["cpu_percent"] = round((1 - idle_diff / total_diff) * 100, 1)
        except Exception as e:
            logger.info(f"Failed to read CPU stats: {e}")

        try:
            meminfo = await loop.run_in_executor(None, read_meminfo)
            total_kb = meminfo.get("MemTotal", 0)
            available_kb = meminfo.get("MemAvailable", 0)
            used_kb = total_kb - available_kb

            result["ram"] = {
                "used_mb": round(used_kb / 1024),
                "total_mb": round(total_kb / 1024),
            }
        except Exception as e:
            logger.info(f"Failed to read memory stats: {e}")

        return result

    return router
