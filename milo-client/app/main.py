#!/usr/bin/env python3
"""
Milo Client - API Service for Snapclient Management and Equalizer Control
Version: 2.0 - Feature-based architecture
"""
import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from services import EqualizerService, SnapclientService, AppUpdateService, CamillaDSPUpdateService
from routes import create_health_router, create_snapclient_router, create_equalizer_router, create_app_update_router, create_hardware_router, create_camilladsp_update_router, create_diagnostic_router, create_probe_router, create_system_router
from routes.health import get_hostname
from access_log import quiet_volume_steps
from origin_gate import OriginGate
from services.camilladsp_update import CAMILLADSP_SERVICE
from services.registration import register_with_main_milo
from services.server_address_watch import follow_server_address
from services.snapclient import SNAPCLIENT_SERVICE

# Constants
API_PORT = 8001

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
quiet_volume_steps()

def _sd_notify_ready():
    """Notify systemd that the service is ready (no external dependency)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"READY=1", addr)
    logger.info("sd_notify: READY=1 sent to systemd")


async def _start_bound_unit(unit: str) -> None:
    """`sudo systemctl start <unit>`, logged at error when it fails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "start", unit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except Exception as e:
        logger.error("systemctl start %s failed: %s", unit, e)
        return
    if proc.returncode != 0:
        logger.error("systemctl start %s failed: %s", unit, stderr.decode(errors="replace").strip())


async def _start_bound_units() -> None:
    """Start the two units BindsTo= this one, which a crash of the app stops.

    `Restart=always` brings the app back, but BindsTo= has already stopped
    CamillaDSP and snapclient and nothing restarts them: the room stays mute,
    and with no snapclient the server never admits the satellite again. At boot
    and during an update their start job is already queued, and this start
    merges into it. Concurrent, since neither unit is ordered after the other:
    a slow CamillaDSP start must not hold back the snapclient that gets the
    satellite admitted. Two calls rather than a loop: the sudoers contract test
    resolves a unit name from a constant, not from a loop over constants.
    """
    await asyncio.gather(
        _start_bound_unit(CAMILLADSP_SERVICE),
        _start_bound_unit(SNAPCLIENT_SERVICE),
    )


# Create service instances
equalizer_service = EqualizerService()
snapclient_service = SnapclientService()
app_update_service = AppUpdateService()
camilladsp_update_service = CamillaDSPUpdateService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.

    CamillaDSP starts MUTED and at a safe floor (-m and --gain in the systemd
    service) and stays there until the backend pushes the correct volume.
    """
    logger.info("Milo Client API starting up...")

    # No connect attempt here on purpose. milo-client-camilladsp.service is ordered
    # After=milo-client.service, and this unit is Type=notify — so CamillaDSP does
    # not even start until the READY=1 sent a few lines below. A cold-boot connect
    # can therefore never succeed; it only bought ~4.5 s of sleeps and an ERROR in
    # the journal on every boot. The loop owns every attempt, the first included,
    # and it also restores volume/mute, which the startup connect never did.
    # Nothing else may connect: _exec refuses when there is none, so no command
    # can reach a daemon still sitting at the floor its unit started it on.
    equalizer_service.start_connection_loop()

    logger.info("Milo Client API startup complete")

    # Signal systemd that the service is ready (Type=notify).
    # This unblocks milo-client-snapclient.service so snapclient only
    # connects to snapserver AFTER our API is accepting requests.
    _sd_notify_ready()

    # After READY=1, not before: both units are After= this one, so a start
    # issued earlier would wait for this unit's own start to finish.
    bound_units_task = asyncio.create_task(_start_bound_units())

    # Register with main Milo (background task, retries until successful)
    registration_task = asyncio.create_task(register_with_main_milo())

    # Follow the main Milo when it changes address: snapclient resolved it
    # once, at its own start, and cannot re-resolve on its own.
    address_watch_task = asyncio.create_task(follow_server_address())

    yield  # Application runs here

    bound_units_task.cancel()
    registration_task.cancel()
    address_watch_task.cancel()
    await equalizer_service.stop_connection_loop()
    logger.info("Milo Client API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Milo Client API",
    description="API for Milo client management",
    version="2.0.0",
    lifespan=lifespan
)
app.add_middleware(OriginGate)

# Register routers
app.include_router(create_health_router(equalizer_service, snapclient_service, app_update_service, camilladsp_update_service))
app.include_router(create_snapclient_router(snapclient_service))
app.include_router(create_equalizer_router(equalizer_service))
app.include_router(create_app_update_router(app_update_service))
app.include_router(create_camilladsp_update_router(camilladsp_update_service))
app.include_router(create_hardware_router())
app.include_router(create_diagnostic_router())
app.include_router(create_probe_router())
app.include_router(create_system_router())


# Main entry point
if __name__ == "__main__":
    logger.info(f"Starting Milo Client API on port {API_PORT}")
    logger.info(f"Hostname: {get_hostname()}")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        access_log=True
    )
