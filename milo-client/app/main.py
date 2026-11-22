#!/usr/bin/env python3
"""
Milo Client - API Service for Snapclient Management and Equalizer Control

It carries no version of its own: what a satellite runs is the server's version
string and the payload fingerprint of its milo-client/ tree, which `/status`
reports (`app.version`, `app.payload`).
"""
import asyncio
import faulthandler
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from services import EqualizerService, SnapclientService, AppUpdateService, CamillaDSPUpdateService
from services.app_update import HOLD_S
from routes import create_health_router, create_snapclient_router, create_equalizer_router, create_app_update_router, create_hardware_router, create_camilladsp_update_router, create_diagnostic_router, create_probe_router, create_system_router
import routes.health
from routes.health import get_hostname
from access_log import quiet_frequent_requests
from origin_gate import OriginGate
import server_secret
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
quiet_frequent_requests()
# A watchdog kill (SIGABRT) prints where the loop was stuck, in the journal.
faulthandler.enable()

def _sd_notify(message: bytes) -> bool:
    """One datagram to systemd's notify socket (no external dependency);
    False when not run by systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message, addr)
    return True


def _sd_notify_ready():
    """Notify systemd that the service is ready."""
    if _sd_notify(b"READY=1"):
        logger.info("sd_notify: READY=1 sent to systemd")


def _watchdog_interval() -> float | None:
    """Seconds between two WATCHDOG=1, or None when the unit set no watchdog.

    A third of WatchdogSec, as sd_watchdog_enabled(3) advises. WATCHDOG_PID
    names the process systemd watches: a child that inherited the variables is
    not it, and feeding from there would keep a dead main process "alive".
    """
    usec = os.environ.get("WATCHDOG_USEC")
    pid = os.environ.get("WATCHDOG_PID")
    if not usec or (pid and pid != str(os.getpid())):
        return None
    try:
        return int(usec) / 3_000_000
    except ValueError:
        logger.error("WATCHDOG_USEC=%r is not a number: the watchdog is not fed", usec)
        return None


async def _feed_watchdog() -> None:
    """WATCHDOG=1 from the event loop itself, so a loop that stops turning
    stops feeding it, and systemd restarts the app (WatchdogSec=)."""
    interval = _watchdog_interval()
    if interval is None:
        return
    while True:
        try:
            _sd_notify(b"WATCHDOG=1")
        except OSError as e:
            logger.error("sd_notify WATCHDOG=1 failed: %s", e)
        await asyncio.sleep(interval)


async def _start_bound_unit(unit: str) -> None:
    """`sudo systemctl start <unit>`, logged at error when it fails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", "start", unit,
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
# Asks the room's CamillaDSP whether anything plays before a push reboots it.
app_update_service = AppUpdateService(room_is_silent=equalizer_service.room_is_silent)
camilladsp_update_service = CamillaDSPUpdateService()

# Set by the server once its socket accepts connections (`_Server.startup`).
_listening: asyncio.Event | None = None


def mark_listening() -> None:
    if _listening is not None:
        _listening.set()


async def _hold_then_commit() -> None:
    """Ends an update's trial once this process has stayed up HOLD_S.

    Dying before that is the failure the trial exists for: the guard counts the
    next start, and puts the previous version back at the last one.
    """
    await asyncio.sleep(HOLD_S)
    await app_update_service.commit_trial(routes.health.APP_PAYLOAD)


async def _once_listening(listening: asyncio.Event) -> None:
    """READY=1 and what follows it, once :8001 actually answers.

    The lifespan's end is not that moment: uvicorn binds the socket after the
    lifespan startup returns, so a READY=1 sent from there started snapclient,
    and got the satellite admitted, against an API not yet listening — and the
    hold of a new version would count from before it could serve anything.
    """
    await listening.wait()
    # Signal systemd that the service is ready (Type=notify).
    # This unblocks milo-client-snapclient.service so snapclient only
    # connects to snapserver AFTER our API is accepting requests.
    _sd_notify_ready()
    # After READY=1, not before: both units are After= this one, so a start
    # issued earlier would wait for this unit's own start to finish. systemd
    # arms the watchdog at READY=1 too.
    await asyncio.gather(_start_bound_units(), _hold_then_commit(), _feed_watchdog())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.

    CamillaDSP starts MUTED and at a safe floor (-m and --gain in the systemd
    service) and stays there until the backend pushes the correct volume.
    """
    global _listening
    logger.info("Milo Client API starting up...")

    # Before anything can reach a route: whether this start follows a power cut
    # decides whether the hand-over may follow a new server (server_secret.py).
    await asyncio.to_thread(server_secret.open_window_after_unclean_stop)

    # No connect attempt here on purpose. milo-client-camilladsp.service is ordered
    # After=milo-client.service, and this unit is Type=notify — so CamillaDSP does
    # not even start until READY=1, sent once the server listens. The loop's first
    # connect is therefore refused by construction, and the loop retries a refusal
    # after REFUSED_RETRY_DELAY rather than the backoff a failing daemon earns:
    # the daemon listens ~0.2 s after READY=1, and the room stays muted at its
    # floor until the loop's restore. The loop owns every attempt, the first
    # included, and it also restores volume/mute.
    # Nothing else may connect: _exec refuses when there is none, so no command
    # can reach a daemon still sitting at the floor its unit started it on.
    equalizer_service.start_connection_loop()

    logger.info("Milo Client API startup complete")

    _listening = asyncio.Event()
    after_listening_task = asyncio.create_task(_once_listening(_listening))

    # Register with main Milo (background task, retries until successful)
    registration_task = asyncio.create_task(register_with_main_milo())

    # Follow the main Milo when it changes address: snapclient resolved it
    # once, at its own start, and cannot re-resolve on its own.
    address_watch_task = asyncio.create_task(follow_server_address())

    yield  # Application runs here

    # First: a stop systemd asked for, which the next start does not take for a
    # power cut — before a step below can raise or outlast TimeoutStopSec, since
    # a stop that leaves no marker opens the window (server_secret.py).
    await asyncio.to_thread(server_secret.record_clean_stop)
    after_listening_task.cancel()
    registration_task.cancel()
    address_watch_task.cancel()
    await equalizer_service.stop_connection_loop()
    logger.info("Milo Client API shutting down...")


class _Server(uvicorn.Server):
    """uvicorn's server, telling the app when its socket is bound."""

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            mark_listening()


# Create FastAPI app
app = FastAPI(
    title="Milo Client API",
    description="API for Milo client management",
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

    # The app object, not "main:app": an import string makes uvicorn import
    # this file a second time as `main`, whose lifespan would wait on an event
    # this module's server never sets.
    server = _Server(uvicorn.Config(
        app,
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        access_log=True
    ))
    server.run()
    # uvicorn.run's own exit for a startup that failed (lifespan or bind), so
    # systemd records a failure rather than a clean stop.
    if not server.started:
        sys.exit(3)
