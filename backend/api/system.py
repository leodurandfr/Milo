# backend/api/system.py
"""
System power management + status + telemetry routes
(restart, shutdown, hostname conflict, temperature, resources, network).
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException
import asyncio
import functools
import logging
import os
import zoneinfo
from typing import Optional, TYPE_CHECKING

from backend.api.models import DevicePasswordRequest, SshRequest, TimezoneRequest
from backend.api.route_helpers import api_error_handler
from backend.config.constants import (
    PASSWORD_CHANGED_MARKER,
    RESET_SETUP_MARKER,
    SET_PASSWORD_CMD,
    SET_TIMEZONE_CMD,
)

if TYPE_CHECKING:
    from backend.core.connectivity.service import ConnectivityService
    from backend.core.system.diagnostic import DiagnosticService
    from backend.core.system.hostname_conflict import HostnameConflictService
    from backend.core.systemd import SystemdServiceManager
    from backend.hardware.service import HardwareService


logger = logging.getLogger(__name__)

SSH_UNIT = "ssh.service"
LOCALTIME_LINK = "/etc/localtime"
ZONEINFO_PREFIX = "/usr/share/zoneinfo/"

# The zone the image ships. Not a location — it is the value that means "nobody
# has told us yet", which is what lets the first browser to open the UI supply
# the real one without ever overwriting a deliberate choice.
DEFAULT_TIMEZONE = "Etc/UTC"


@functools.lru_cache(maxsize=1)
def _available_timezones() -> list:
    """Every IANA zone this system ships, as `Area/Location`.

    Two things are dropped, both of them artefacts of the directory scan rather
    than zones: `localtime` (the symlink itself), and the bare legacy aliases
    (`UTC`, `CET`, `Zulu`…) which have no area to sort under. What remains is
    total for an Area → Location pair of dropdowns, and `Etc/UTC` still carries
    plain UTC. Cached: the scan is ~11 ms and the answer changes with a package
    upgrade, never within a process.
    """
    return sorted(tz for tz in zoneinfo.available_timezones() if "/" in tz)


def _current_timezone() -> Optional[str]:
    """The zone in force, read from what the C library actually follows.

    `/etc/localtime` is the file every timestamp on this box resolves through,
    so it cannot disagree with reality the way a second copy in /etc/timezone
    can. Returns None when it is not a symlink into the zoneinfo tree — an
    answer, not a guess.
    """
    try:
        target = os.readlink(LOCALTIME_LINK)
    except OSError:
        return None
    if not target.startswith(ZONEINFO_PREFIX):
        return None
    return target[len(ZONEINFO_PREFIX):]


def create_system_router(
    systemd_manager: "SystemdServiceManager",
    hostname_conflict_service: Optional["HostnameConflictService"] = None,
    connectivity_service: Optional["ConnectivityService"] = None,
    hardware_service: Optional["HardwareService"] = None,
    diagnostic_service: Optional["DiagnosticService"] = None
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
        """System-level status: hostname conflict, connectivity, audio card.

        `audio_card_missing` carries the *label* of the configured card rather
        than a boolean, because the banner has to name it — "HiFiBerry Amp2 is
        configured but not detected" is actionable where "no audio card" is
        just the symptom the user already has. None when all is well.

        A state and not an event, deliberately: a HAT is not hot-pluggable, so
        the answer is settled at boot, and the UI needs it on every load rather
        than once, at the moment it happened to be connected.
        """
        data = {
            "hostname_conflict": False,
            "connectivity": "unknown",
            "audio_card_missing": None,
        }
        if hostname_conflict_service is not None:
            data.update(hostname_conflict_service.get_state())
        if connectivity_service is not None:
            data.update(connectivity_service.get_state())
        if hardware_service is not None:
            data["audio_card_missing"] = hardware_service.get_missing_audio_card()
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

    @router.post("/diagnostic")
    async def generate_diagnostic():
        """Build the diagnostic report the user sends to the maintainer.

        A single readable text file, on a button, and nothing automatic: an
        export sent by itself would need a destination, therefore consent,
        therefore a server — a different project. What comes back is the file
        plus the list of sections that could not be collected, so the UI can
        name them under the buttons rather than let them pass as silence.

        POST because it is an action: it runs subprocesses and fans out to every
        satellite. It carries no body — there is nothing to choose.
        """
        async with api_error_handler("Failed to generate the diagnostic report", logger):
            if diagnostic_service is None:
                raise HTTPException(status_code=503, detail="Diagnostic service unavailable")
            result = await diagnostic_service.generate()
            logger.info(
                "Diagnostic report generated (%d bytes, %d section(s) not collected)",
                len(result["report"].encode("utf-8")), len(result["unavailable"]),
            )
            return {"status": "success", "data": result}

    @router.post("/reset-setup")
    async def reset_setup(background_tasks: BackgroundTasks):
        """Re-run first-boot: role detection, then the setup wizard.

        The way back from the one mistake a multiroom product invites — powering
        a speaker on before the server it should join. With no server answering
        on the LAN, `milo-first-boot` leaves it a server and the wizard appears
        on its own screen; finishing that wizard locked the role for good, since
        `become-client` refuses an already-configured device and nothing else
        could clear the flag. The only way out was a reflash.

        Drops a marker and reboots rather than deleting settings.json here: this
        process owns that file, so a write landing in the second before the
        reboot would recreate it with `setup_completed` still true.
        `milo-first-boot` does the deletion with the backend down.

        Durable user data — radio favourites, podcast subscriptions, shares,
        hardware.json — is untouched. This resets the *setup*, and says so.
        """
        async with api_error_handler("Error scheduling the setup reset", logger):
            RESET_SETUP_MARKER.parent.mkdir(parents=True, exist_ok=True)
            RESET_SETUP_MARKER.touch()
            logger.info("Setup reset requested — rebooting into first-boot detection")
            background_tasks.add_task(systemd_manager.power, "reboot", 2.0)
            return {"status": "success"}

    # =========================================================================
    # Remote access + account password
    # =========================================================================

    @router.get("/ssh")
    async def get_ssh_state():
        """SSH server state, and whether the factory password is still in place.

        The three travel together because they are one panel on screen: the
        factory password is worth a word only while SSH is actually open, so the
        UI needs both facts in one read to decide whether to say anything.
        """
        return {
            "status": "success",
            "data": {
                "enabled": await systemd_manager.is_enabled(SSH_UNIT),
                "active": await systemd_manager.is_active(SSH_UNIT),
                "password_is_default": not PASSWORD_CHANGED_MARKER.exists(),
            },
        }

    @router.put("/ssh")
    async def set_ssh_state(payload: SshRequest):
        """Open or close SSH.

        Not gated on the factory password having been replaced. Nothing on this
        API authenticates — network position is the authority, see
        `api/middleware.py` — so a refusal here stops nobody already on the LAN,
        who can set the password and open the door in two calls, while standing
        in the way of the owner. What the factory password costs is that it is
        *published*: the same value on every unit, in a public repository. That
        is a fact to report beside an open door, which `GET /ssh` carries, not a
        permission to withhold.
        """
        if not await systemd_manager.set_enabled(SSH_UNIT, payload.enabled):
            raise HTTPException(
                status_code=500,
                detail=f"Failed to {'enable' if payload.enabled else 'disable'} SSH",
            )

        logger.info("SSH %s", "enabled" if payload.enabled else "disabled")
        return {
            "status": "success",
            "data": {
                "enabled": await systemd_manager.is_enabled(SSH_UNIT),
                "active": await systemd_manager.is_active(SSH_UNIT),
                "password_is_default": not PASSWORD_CHANGED_MARKER.exists(),
            },
        }

    @router.post("/password")
    async def set_device_password(payload: DevicePasswordRequest):
        """Set the `milo` account password (SSH login + sudo).

        The password goes to the helper on **stdin**: /proc/<pid>/cmdline is
        world-readable, so an argv would publish it to every process on the box
        for the lifetime of the call. The helper is also what creates the
        marker `GET /ssh` reads — one writer for the fact and the flag, so they
        cannot disagree.
        """
        async with api_error_handler("Error setting the device password", logger):
            proc = await asyncio.create_subprocess_exec(
                "sudo", SET_PASSWORD_CMD,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(payload.password.encode()), 15.0
            )
            if proc.returncode != 0:
                detail = stderr.decode().strip() if stderr else "unknown error"
                logger.error("milo-set-password failed (rc=%s): %s", proc.returncode, detail)
                raise HTTPException(status_code=500, detail=f"Failed to set password: {detail}")

            logger.info("Device password updated")
            return {"status": "success", "data": {"password_is_default": False}}

    # =========================================================================
    # Timezone
    # =========================================================================

    @router.get("/timezone")
    async def get_timezone():
        """Current zone, whether it is still the shipped default, and the choices.

        `is_default` is what the frontend gates its one-shot adoption on: a
        browser reports the zone it lives in, and it is taken only while nobody
        has chosen one. The list rides along so the settings dropdowns are built
        from the zones this system actually has rather than a table restated in
        the frontend.
        """
        current = _current_timezone()
        return {
            "status": "success",
            "data": {
                "timezone": current,
                "is_default": current == DEFAULT_TIMEZONE,
                "available": _available_timezones(),
            },
        }

    @router.put("/timezone")
    async def set_timezone(payload: TimezoneRequest):
        """Apply an IANA timezone."""
        if payload.timezone not in _available_timezones():
            logger.error("Rejected unknown timezone %r", payload.timezone)
            raise HTTPException(status_code=400, detail=f"Unknown timezone: {payload.timezone}")

        async with api_error_handler("Error setting the timezone", logger):
            proc = await asyncio.create_subprocess_exec(
                "sudo", SET_TIMEZONE_CMD, payload.timezone,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
            if proc.returncode != 0:
                detail = stderr.decode().strip() if stderr else "unknown error"
                logger.error("milo-set-timezone failed (rc=%s): %s", proc.returncode, detail)
                raise HTTPException(status_code=500, detail=f"Failed to set timezone: {detail}")

            logger.info("Timezone set to %s", payload.timezone)
            return {"status": "success", "data": {"timezone": _current_timezone()}}

    return router
