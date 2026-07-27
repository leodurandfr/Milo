# backend/api/system.py
"""
System power management + status + telemetry routes
(restart, shutdown, hostname conflict, temperature, resources, network).
"""
from fastapi import APIRouter, BackgroundTasks
import asyncio
import contextlib
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

    # System temperature
    @router.get("/temperature")
    async def get_system_temperature():
        """Retrieve Raspberry Pi temperature and throttling status"""
        try:
            temp_process = asyncio.create_subprocess_shell(
                "vcgencmd measure_temp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            throttle_process = asyncio.create_subprocess_shell(
                "vcgencmd get_throttled",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            temp_proc, throttle_proc = await asyncio.gather(temp_process, throttle_process)
            try:
                temp_stdout, _ = await asyncio.wait_for(temp_proc.communicate(), 5.0)
            except asyncio.TimeoutError:
                temp_proc.kill()
                logger.error("Timeout reading temperature (vcgencmd measure_temp)")
                temp_stdout, _ = b"", b""
            try:
                throttle_stdout, _ = await asyncio.wait_for(throttle_proc.communicate(), 5.0)
            except asyncio.TimeoutError:
                throttle_proc.kill()
                logger.error("Timeout reading throttle status (vcgencmd get_throttled)")
                throttle_stdout, _ = b"", b""

            result = {"status": "success"}

            # Parse temperature
            if temp_proc.returncode == 0:
                temp_output = temp_stdout.decode().strip()
                if temp_output.startswith("temp=") and temp_output.endswith("'C"):
                    temp_str = temp_output.replace("temp=", "").replace("'C", "")
                    result["temperature"] = float(temp_str)
                    result["unit"] = "°C"
                else:
                    result["temperature"] = None
            else:
                result["temperature"] = None

            # Parse throttling
            throttle_status = {"code": "0x0", "current": [], "past": [], "severity": "ok"}

            if throttle_proc.returncode == 0:
                throttle_output = throttle_stdout.decode().strip()
                if throttle_output.startswith("throttled="):
                    throttle_code = throttle_output.replace("throttled=", "").strip()
                    throttle_status["code"] = throttle_code

                    # Stable snake_case codes (current bits 0-3, past bits 19-22);
                    # display/translation is the consumer's concern.
                    with contextlib.suppress(ValueError):
                        throttle_value = int(throttle_code, 16)

                        for bit, code in ((0x1, "under_voltage"), (0x2, "overheating"),
                                          (0x4, "freq_capped_power"), (0x8, "freq_capped_temp")):
                            if throttle_value & bit:
                                throttle_status["current"].append(code)
                            if throttle_value & (bit << 19):
                                throttle_status["past"].append(code)

                        if throttle_status["current"]:
                            throttle_status["severity"] = "critical"
                        elif throttle_status["past"]:
                            throttle_status["severity"] = "warning"
                        else:
                            throttle_status["severity"] = "ok"

            result["throttling"] = throttle_status
            return result

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "temperature": None,
                "throttling": {"code": "error", "current": [], "past": [], "severity": "error"}
            }

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
