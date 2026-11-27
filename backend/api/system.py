# backend/api/system.py
"""
System power management + status + telemetry routes
(restart, shutdown, hostname conflict, temperature, resources, network).
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
import asyncio
import functools
import logging
import os
import zoneinfo
from datetime import datetime, timedelta
from time import monotonic
from typing import Literal, Optional, TYPE_CHECKING

from backend.api.models import DevicePasswordRequest, SshRequest, TimezoneRequest
from backend.api.programs import refuse_while_milo_updates
from backend.api.route_helpers import api_error_handler
from backend.core.models.ws_events import SystemTimezoneChanged
from backend.core.system.localtime import current_timezone
from backend.core.system.zone_coordinates import (
    read_country_zone,
    read_link_target,
    read_zone_coordinates,
)
from backend.config import constants
from backend.config.constants import (
    PASSWORD_CHANGED_MARKER,
    RESET_SETUP_MARKER,
    SET_PASSWORD_CMD,
    SET_TIMEZONE_CMD,
)

if TYPE_CHECKING:
    from backend.core.multiroom.satellite_timezone import SatelliteTimezoneService
    from backend.core.state import AudioStateMachine
    from backend.core.system.diagnostic import DiagnosticService
    from backend.core.system.hostname_conflict import HostnameConflictService
    from backend.core.system.logs import LogService
    from backend.core.system.notices import SetAsideNotices
    from backend.core.settings import SettingsService
    from backend.core.systemd import SystemdServiceManager
    from backend.core.updates.update import UpdateService
    from backend.hardware.service import HardwareService


logger = logging.getLogger(__name__)

SSH_UNIT = "ssh.service"

# The zone the image ships. Not a location, and never offered (it is outside
# PLACE_AREAS), so no choice can produce it: it means "nobody has told us yet",
# which is what lets the first browser to open the UI supply the real one
# without ever overwriting a deliberate choice.
DEFAULT_TIMEZONE = "Etc/UTC"

# The tz database's areas that hold places — continents and oceans, the islands
# filed under the latter (`Indian/Reunion`, `Pacific/Tahiti`). Named, not
# derived: `Etc` and `Factory` hold none, and `tzdata-legacy` would add `US/`,
# `Canada/`… as second names for cities already offered. The frontend labels
# each one (`timezone.areas.*`), held equal to this tuple by its i18n tests.
PLACE_AREAS = (
    "Africa", "America", "Antarctica", "Arctic", "Asia",
    "Atlantic", "Australia", "Europe", "Indian", "Pacific",
)


@functools.lru_cache(maxsize=1)
def _available_timezones() -> list:
    """Every IANA zone this system ships that names a place, as `Area/Location`.

    The list is what the owner picks from, so only PLACE_AREAS are kept. Left
    out with `Etc` are UTC under eight names and fixed offsets with no daylight
    saving whose sign is inverted (`Etc/GMT+1` is UTC−1) — the one a French
    owner would reach for puts the clock two hours off. Every country, UTC+0
    ones included, is reachable through a city. Cached: the scan is ~11 ms and
    the answer changes with a package upgrade, never within a process.
    """
    return sorted(
        tz for tz in zoneinfo.available_timezones()
        if tz.split("/", 1)[0] in PLACE_AREAS and "/" in tz
    )


async def _daylight_location() -> dict:
    """The zone in force and the tz database's point for it (nulls without one)."""
    zone = current_timezone()
    coordinates = await read_zone_coordinates(zone)
    latitude, longitude = coordinates if coordinates else (None, None)
    return {"latitude": latitude, "longitude": longitude, "timezone": zone}


# The logs page's window, back from now: a count and m/h/d. Thirty days is
# past anything a unit keeps (errors.log's rotations reach furthest).
LOG_WINDOW_PATTERN = r"^\d{1,4}[mhd]$"
LOG_WINDOW_MAX = timedelta(days=30)
_LOG_WINDOW_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def _log_window(since: str) -> timedelta:
    window = timedelta(**{_LOG_WINDOW_UNITS[since[-1]]: int(since[:-1])})
    if not timedelta(0) < window <= LOG_WINDOW_MAX:
        logger.info("Logs refused: a window of %s", since)
        raise HTTPException(status_code=400, detail=f"since must be between 1m and {LOG_WINDOW_MAX.days}d")
    return window


def create_system_router(
    systemd_manager: "SystemdServiceManager",
    hostname_conflict_service: Optional["HostnameConflictService"] = None,
    hardware_service: Optional["HardwareService"] = None,
    diagnostic_service: Optional["DiagnosticService"] = None,
    state_machine: Optional["AudioStateMachine"] = None,
    satellite_timezone_service: Optional["SatelliteTimezoneService"] = None,
    update_service: Optional["UpdateService"] = None,
    settings_service: Optional["SettingsService"] = None,
    notice_service: Optional["SetAsideNotices"] = None,
    log_service: Optional["LogService"] = None,
):
    router = APIRouter()

    @router.post("/restart")
    async def restart_system(background_tasks: BackgroundTasks):
        """Reboot the Raspberry Pi."""
        refuse_while_milo_updates(update_service, "restart")
        logger.info("System restart requested")
        # 2s delay lets the HTTP response flush before the box goes down.
        background_tasks.add_task(systemd_manager.power, "reboot", 2.0)
        return {"status": "success"}

    @router.post("/shutdown")
    async def shutdown_system(background_tasks: BackgroundTasks):
        """Shut down the Raspberry Pi."""
        refuse_while_milo_updates(update_service, "shutdown")
        logger.info("System shutdown requested")
        background_tasks.add_task(systemd_manager.power, "poweroff", 2.0)
        return {"status": "success"}

    @router.get("/status")
    async def get_system_status():
        """System-level status: hostname conflict, audio card, setup waiting for a link.

        `audio_card_missing` carries the *label* of the configured card rather
        than a boolean, because the banner has to name it — "HiFiBerry Amp2 is
        configured but not detected" is actionable where "no audio card" is
        just the symptom the user already has. None when all is well.

        `setup_awaiting_network`: the wizard's choices are applied and the unit
        has not joined a network since (`api/setup.py`), so the wizard opens on
        its Network step — the key typed over the AP is the one thing to fix.
        It moves while the unit runs: the first link clears it, written before
        any linked `network/status_changed` is broadcast
        (`NetworkService.add_link_listener`), so a client that reads the setup
        state on that event reads it cleared.

        A state and not an event, deliberately: a HAT is not hot-pluggable, so
        the card's answer is settled at boot; the UI reads it, and the setup
        state above, on every load rather than once, at the moment it happened
        to be connected.
        """
        data = {
            "hostname_conflict": False,
            "audio_card_missing": None,
            "setup_awaiting_network": False,
        }
        if hostname_conflict_service is not None:
            data.update(hostname_conflict_service.get_state())
        if hardware_service is not None:
            data["audio_card_missing"] = hardware_service.get_missing_audio_card()
        if settings_service is not None:
            awaiting = await settings_service.get_setting("setup_awaiting_network")
            completed = await settings_service.get_setting("setup_completed")
            data["setup_awaiting_network"] = bool(awaiting) and not completed
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

    # System resources (CPU, RAM, disk, network)
    @router.get("/resources")
    async def get_system_resources():
        """Retrieve CPU, RAM, root-disk and network usage.

        Network throughput counts physical interfaces only (those with a
        `device` link in sysfs): a tunnel's traffic already crosses eth0/wlan0,
        and counting both would double it.
        """
        result = {
            "status": "success",
            "cpu_percent": None,
            "ram": None,
            "disk": None,
            "network": None,
        }

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

        def read_net_bytes():
            rx = tx = 0
            with open("/proc/net/dev", "r") as f:
                lines = f.readlines()[2:]  # Two header lines
            for line in lines:
                name, _, counters = line.partition(":")
                name = name.strip()
                if not os.path.exists(os.path.join(constants.SYSFS_NET, name, "device")):
                    continue
                fields = counters.split()
                rx += int(fields[0])
                tx += int(fields[8])
            return rx, tx, monotonic()

        def read_disk():
            # Same split as `df`: the blocks reserved for root are neither used
            # nor available, so they are left out of the total.
            st = os.statvfs("/")
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            total = used + st.f_bavail * st.f_frsize
            return {
                "used_gb": round(used / 1024 ** 3, 1),
                "total_gb": round(total / 1024 ** 3, 1),
            }

        loop = asyncio.get_running_loop()

        cpu1 = net1 = None
        try:
            cpu1 = await loop.run_in_executor(None, read_cpu_stats)
        except Exception as e:
            logger.info(f"Failed to read CPU stats: {e}")
        try:
            net1 = await loop.run_in_executor(None, read_net_bytes)
        except Exception as e:
            logger.info(f"Failed to read network stats: {e}")

        await asyncio.sleep(0.1)

        if cpu1 is not None:
            try:
                idle1, total1 = cpu1
                idle2, total2 = await loop.run_in_executor(None, read_cpu_stats)
                total_diff = total2 - total1
                idle_diff = idle2 - idle1
                if total_diff > 0:
                    result["cpu_percent"] = round((1 - idle_diff / total_diff) * 100, 1)
            except Exception as e:
                logger.info(f"Failed to read CPU stats: {e}")

        if net1 is not None:
            try:
                rx1, tx1, t1 = net1
                rx2, tx2, t2 = await loop.run_in_executor(None, read_net_bytes)
                elapsed = t2 - t1
                if elapsed > 0:
                    result["network"] = {
                        "rx_bytes_per_s": max(0, round((rx2 - rx1) / elapsed)),
                        "tx_bytes_per_s": max(0, round((tx2 - tx1) / elapsed)),
                    }
            except Exception as e:
                logger.info(f"Failed to read network stats: {e}")

        try:
            result["disk"] = await loop.run_in_executor(None, read_disk)
        except Exception as e:
            logger.info(f"Failed to read disk usage: {e}")

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

    @router.get("/logs")
    async def read_logs(
        since: str = Query("24h", pattern=LOG_WINDOW_PATTERN),
        level: Optional[Literal["debug", "info", "warning", "error", "critical"]] = None,
        service: Optional[str] = Query(None, max_length=200),
        q: Optional[str] = Query(None, max_length=200),
        limit: int = Query(200, ge=1, le=1000),
    ):
        """The logs of the window `since` back from now, grouped and redacted.

        Read when asked, nothing in the background: errors.log and its
        rotations, the journal of the `milo-*` units, each online speaker's
        own tails. Repeated lines come as one group (count, first and last
        occurrence); `level` is a minimum severity, `service` one service,
        `q` a text matched against the redacted line. Every line is redacted
        as the diagnostic report's are; a place that could not be read is
        listed in `not_collected` with its reason.
        """
        async with api_error_handler("Failed to read the logs", logger):
            if log_service is None:
                raise HTTPException(status_code=503, detail="Log service unavailable")
            page = await log_service.read(
                since=_log_window(since), level=level, service=service or None, q=q or None,
                limit=limit,
            )
            return {"status": "success", "data": page.model_dump(mode="json")}

    @router.get("/logs/context")
    async def read_log_context(
        at: datetime,
        service: Optional[str] = Query(None, max_length=200),
    ):
        """What "Copy for correction" sends: the 20 lines on each side of the
        event at `at` (of `service`), every service together, the event's own
        text redacted with them (`sample`), the release the unit runs (version
        and commit), and the audio source at that moment — with how that is
        known (`source_state.basis`)."""
        async with api_error_handler("Failed to read the context of a log line", logger):
            if log_service is None:
                raise HTTPException(status_code=503, detail="Log service unavailable")
            context = await log_service.context(at=at, service=service or None)
            return {"status": "success", "data": context.model_dump(mode="json")}

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
        # Before the marker: a refused reset must leave nothing for the next
        # boot to act on.
        refuse_while_milo_updates(update_service, "setup reset")
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

        The three travel together because they are one panel on screen: while
        the factory password is in place the switch cannot open the door (see
        `PUT /ssh`), and the UI says why in the same read.
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

        Opening reaches root — `milo` is root by construction (CLAUDE.md,
        invariant 5) — so it is refused while the factory password is in
        place: that password is the same on every unit and published with
        the source, so a door opened on it is open to anyone who reads the
        repository. Otherwise it is answered like every other route: nothing
        on this API authenticates, and the LAN is the boundary. Closing is
        never refused. A client of the open recovery AP is refused this write,
        set up or not: it may send only `PRE_SETUP_WRITES` and `SET_UP_WRITES`
        (`api/middleware.py::RecoveryAccessPointGuard`).
        """
        if payload.enabled:
            if not PASSWORD_CHANGED_MARKER.exists():
                logger.warning("Refused to open SSH: the factory password is still in place")
                raise HTTPException(
                    status_code=409,
                    detail="Set the device password before opening SSH: the factory "
                           "password is the same on every Milō and published with its source",
                )

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
                "sudo", "-n", SET_PASSWORD_CMD,
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
    # Notices: the files set aside, until the owner has read the banner
    # =========================================================================

    @router.get("/notices")
    async def get_notices():
        """The files Milō could not read and set aside, not yet acknowledged.

        What a page reads back at every resync: the set-asides of a boot happen
        before any client listens, so the event announcing one is heard by none.
        """
        notices = notice_service.notices() if notice_service is not None else []
        return {"status": "success", "data": {"notices": [n.model_dump() for n in notices]}}

    @router.delete("/notices/{notice_id}")
    async def acknowledge_notice(notice_id: str):
        """Drop one notice once its banner was read, on every screen at once."""
        # A write that fails (the card this notice is about may be full) is a
        # logged 500, and the notice stays listed.
        async with api_error_handler("Error acknowledging a set-aside notice", logger):
            acknowledged = notice_service is not None and await notice_service.acknowledge(notice_id)
        if not acknowledged:
            # Another screen acknowledged it first: expected, not a failure.
            logger.debug("No notice %s to acknowledge", notice_id)
            raise HTTPException(status_code=404, detail="No such notice")
        return {"status": "success"}

    # =========================================================================
    # Timezone
    # =========================================================================

    @router.get("/timezone")
    async def get_timezone():
        """Current zone, whether it is still the shipped default, and the choices.

        `is_default` is what the frontend gates its one-shot adoption on: a
        browser reports the zone it lives in, and it is taken only while nobody
        has chosen one — the shipped zone, which no PUT can set. The
        list rides along so the settings dropdowns are built from the zones this
        system actually has rather than a table restated in the frontend.

        `country_zone` is what the kiosk adopts instead, its browser being this
        system: the zone of the WiFi country the unit was set up with, when that
        country keeps one clock (`zone_coordinates.read_country_zone`), else
        None — and None until setup is complete. The wizard's network step
        writes a country guessed from the UI language as it opens, before anyone
        confirmed it: adopted then, a kiosk reload in that window would mark the
        guessed country's zone as chosen, and the country corrected after it
        would never move the zone again.
        """
        current = current_timezone()
        available = _available_timezones()
        country = None
        if settings_service is not None and await settings_service.get_setting("setup_completed"):
            country = await settings_service.get_setting("wifi.country")
        country_zone = await read_country_zone(country)
        return {
            "status": "success",
            "data": {
                "timezone": current,
                "is_default": current == DEFAULT_TIMEZONE,
                "available": available,
                "country_zone": country_zone if country_zone in available else None,
            },
        }

    @router.get("/daylight-location")
    async def get_daylight_location():
        """Where the zone in force is, for the kiosk's sunset-driven theme.

        The coordinates are the tz database's point for the zone, so they move
        with `PUT /timezone` and need no location service. A zone with no
        point (`Etc/UTC`) answers nulls, which the frontend reads as daytime.
        """
        return {"status": "success", **await _daylight_location()}

    @router.put("/timezone")
    async def set_timezone(payload: TimezoneRequest, background_tasks: BackgroundTasks):
        """Apply an IANA timezone, here and on every satellite online.

        The satellites follow after the answer, from the zone read back off the
        system: a slow or absent one never holds up the settings screen.

        A legacy name is set as the zone it is a link to: Chromium reports CLDR
        ids (`Asia/Calcutta`), which tzdata keeps only as a line of tzdata.zi,
        so a browser in India would otherwise be refused for good and the unit
        left on UTC. It is resolved whether or not the system also ships the
        legacy name as a file (Debian's tzdata-legacy, Ubuntu's tzdata do), so
        the zone set is the same on every host. Anything else the list does not
        offer is refused.
        """
        zone = payload.timezone
        available = _available_timezones()
        target = await read_link_target(zone)
        if target in available:
            if target != zone:
                logger.info("Timezone %s is a link to %s, which is set", zone, target)
            zone = target
        elif zone not in available:
            logger.warning("Rejected unknown timezone %r", zone)
            raise HTTPException(status_code=400, detail=f"Unknown timezone: {zone}")

        async with api_error_handler("Error setting the timezone", logger):
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", SET_TIMEZONE_CMD, zone,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
            if proc.returncode != 0:
                detail = stderr.decode().strip() if stderr else "unknown error"
                logger.error("milo-set-timezone failed (rc=%s): %s", proc.returncode, detail)
                raise HTTPException(status_code=500, detail=f"Failed to set timezone: {detail}")

            logger.info("Timezone set to %s", zone)
            if state_machine is not None:
                await state_machine.broadcast(SystemTimezoneChanged(**await _daylight_location()))
            if satellite_timezone_service is not None:
                background_tasks.add_task(satellite_timezone_service.push_all)
            return {"status": "success", "data": {"timezone": current_timezone()}}

    return router
