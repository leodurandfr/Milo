# backend/main.py
"""
Main entry point for the Milo application.
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import contextlib
import logging
import socket
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.dependencies import get_service, initialize_services, get_init_task
from backend.api import audio
from backend.api.middleware import (
    ALLOWED_ORIGINS, RecoveryAccessPointGuard, RecoveryClientTracker, RequestOriginGate,
)
from backend.api.routing import create_routing_router
from backend.api.equalizer import create_equalizer_router
from backend.api.volume import create_volume_router
from backend.sources.radio.routes import setup_radio_routes
from backend.sources.podcast.routes import setup_podcast_routes
from backend.sources.airplay.routes import setup_airplay_routes
from backend.sources.cd.routes import setup_cd_routes
from backend.sources.music_library.routes import setup_music_library_routes
from backend.sources.spotify.routes import setup_spotify_routes
from backend.api.settings import create_settings_router
from backend.api.system import create_system_router
from backend.api.programs import create_programs_router
from backend.hardware.bt_remote_routes import create_bt_remote_router
from backend.hardware.ir_remote_routes import create_ir_remote_router
from backend.hardware.fan_routes import create_fan_router
from backend.api.health import create_health_router
from backend.api.errors import create_errors_router
from backend.api.setup import create_setup_router
from backend.api.network import create_network_router
from backend.api.discovery import create_discovery_router
from backend.api.multiroom import create_multiroom_router
from backend.api.qobuz_account import create_qobuz_account_router
from backend.api.lyrics import create_lyrics_router
from backend.api.push import create_push_router
from backend.ws import WebSocketServer
from backend.core.models.audio_state import AudioSource
from backend.core.updates.milo_journal import GUARD, HOLD_S
from backend.shared.background import BackgroundTaskSet

# Configurable log level via MILO_LOG_LEVEL environment variable
# Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: WARNING)
_log_level_name = os.environ.get('MILO_LOG_LEVEL', 'WARNING').upper()
_log_level = getattr(logging, _log_level_name, logging.WARNING)
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)

# Persist errors/warnings to rotating log file. Fail open if the data dir is
# missing (CI, non-Pi dev) — systemd's StateDirectory guarantees it in production.
from backend.config.constants import ERROR_LOG_FILE
try:
    _file_handler = RotatingFileHandler(ERROR_LOG_FILE, maxBytes=2*1024*1024, backupCount=3)
    _file_handler.setLevel(logging.WARNING)
    _file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(name)s - %(message)s'))
    logging.getLogger().addHandler(_file_handler)
except OSError as e:
    logger.warning(f"Error log persistence disabled ({ERROR_LOG_FILE}): {e}")

# Broadcast backend errors to frontend via WebSocket. ERROR-only: WARNINGs are
# operational noise (radio mirror hiccups, Snapcast/fan/mpv) and would surface as
# misleading user-facing banners. Genuine user-facing failures use broadcast_error().
from backend.core.log_handler import WebSocketLogHandler
_ws_log_handler = WebSocketLogHandler(level=logging.ERROR)
logging.getLogger("backend").addHandler(_ws_log_handler)

# Default level is WARNING, so audio-source/mpv playback INFO (Playing station,
# stream load failed, mpv idle/disconnect) never reaches the journal. Raise those
# to INFO for diagnosis unless MILO_LOG_LEVEL already set a lower floor; errors.log
# and the WS handler stay WARNING+/ERROR. Shazam logs every cycle, so keep it quiet.
if _log_level > logging.INFO:
    logging.getLogger("source").setLevel(logging.INFO)
    logging.getLogger("backend.shared.mpv").setLevel(logging.INFO)
    logging.getLogger("source.radio.shazam").setLevel(logging.WARNING)
# httpx logs every request at INFO: one line per APNs push, device token included
# (~1k a day, E74). Its failures still reach the journal at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Get services from registry
state_machine = get_service("audio_state_machine")
routing_service = get_service("audio_routing_service")
snapcast_service = get_service("snapcast_service")
snapcast_websocket_service = get_service("snapcast_websocket_service")
camilladsp_service = get_service("camilladsp_service")
settings_service = get_service("settings_service")
volume_service = get_service("volume_service")
rotary_controller = get_service("rotary_controller")
screen_controller = get_service("screen_controller")
bt_remote_controller = get_service("bt_remote_controller")
ir_remote_controller = get_service("ir_remote_controller")
fan_controller = get_service("fan_controller")
systemd_manager = get_service("systemd_manager")
hardware_service = get_service("hardware_service")
crossover_service = get_service("crossover_service")
equalizer_proxy_service = get_service("equalizer_client_proxy_service")
client_registry_service = get_service("client_registry_service")
equalizer_router_service = get_service("equalizer_router")
levels_monitor = get_service("levels_monitor")
network_service = get_service("network_service")
wifi_adoption_service = get_service("wifi_adoption_service")
update_service = get_service("update_service")
notice_service = get_service("notice_service")
ws_manager = get_service("websocket_manager")
websocket_server = WebSocketServer(
    ws_manager, state_machine, volume_service, settings_service, network_service,
    notice_service=notice_service,
)

# Per-entry ceiling for the shutdown teardown below
CLEANUP_TIMEOUT_S = 3.0

# `milo-update-guard commit` drops what an update kept aside, a venv among it,
# on an SD card. Past this the guard is killed; it resumes a cut commit.
GUARD_TIMEOUT_S = 60.0
# A commit that failed is tried again while the trial is open: left open over a
# release that held, it would roll that release back two restarts later.
COMMIT_RETRY_S = 60.0
# How many commits are tried before the unit is let go of. A guard that keeps
# failing (a card remounted read-only, a full card) fails the same way every
# minute, and the trial holds the update screen and refuses the reboot — the
# first useful gesture on a sick card — until the unit is unplugged.
COMMIT_ATTEMPTS = 5


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


# Set once uvicorn's socket accepts connections (`_Server.startup`).
_listening: asyncio.Event | None = None
_after_listening = BackgroundTaskSet(logger, "after_listening")


def mark_listening() -> None:
    if _listening is not None:
        _listening.set()


async def _commit_milo_trial() -> tuple[bool, str]:
    """`milo-update-guard commit`, as milo like the guard's every run (no sudo:
    the journal is this account's own state). Returns whether the guard exited
    0, and its last line on stderr — on a failure, why the attempt failed.
    Exit 0 is not "committed": the guard also exits 0 over a trial it will not
    commit (a restore in progress, a failure recorded, a HEAD that is not the
    release on trial), saying why on stderr, so the caller reads the journal
    again before it calls the trial done, and has that line to name the cause.

    A failed attempt is a warning: the caller tries again, and says it once,
    at error, if it gives up.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            GUARD, "commit",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        cause = f"milo-update-guard commit could not run: {e}"
        logger.warning(cause)
        return False, cause
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), GUARD_TIMEOUT_S)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        cause = f"milo-update-guard commit did not finish within {GUARD_TIMEOUT_S:.0f} s"
        logger.warning(cause)
        return False, cause
    lines = [line.strip() for line in stderr.decode(errors="replace").splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if proc.returncode != 0:
        cause = "milo-update-guard commit failed: " + (last or f"exit code {proc.returncode}")
        logger.warning(cause)
        return False, cause
    return True, last


async def _finish_milo_update() -> None:
    """End the trial of the release this process runs once it has held, then
    let the update service run what the update left for the arriving release.

    Dying before HOLD_S is the failure the trial exists for: the guard counts
    the next start, and puts the previous release back at the last one. The
    hold is `asyncio.sleep`, on the loop's monotonic clock: the wall clock
    steps during boot. `run_post_commit` does nothing unless a committed
    journal is open — found so at start when a post-commit phase was cut. A
    journal the guard should have put back before this start is handed back
    to it first.

    A commit that fails is tried again, COMMIT_ATTEMPTS times in all; past
    that the trial is let go of (`let_go_of_the_trial`, which says so once, at
    error): the update screen lifts and the unit may be restarted again, which
    the guard counts as a start of the trial. A guard that exits 0 is asked to
    let go as well: it does so over a trial it did not commit, and the let-go
    reads the journal first, so a committed one is left to the post-commit
    phase. Read as done instead, that trial held the update screen and
    refused the reboot and the power-off until the unit was unplugged.
    """
    await update_service.hand_stranded_journal_to_the_guard()
    state = update_service.milo_update_state()
    if state is not None and state["phase"] == "verify":
        logger.info(
            f"Milo {state['to_version']} is on trial: committed if it holds {HOLD_S} s"
        )
        await asyncio.sleep(HOLD_S)
        for attempt in range(1, COMMIT_ATTEMPTS + 1):
            exited, cause = await _commit_milo_trial()
            if exited:
                cause = cause or "the update guard did not commit the trial"
                break
            if attempt < COMMIT_ATTEMPTS:
                await asyncio.sleep(COMMIT_RETRY_S)
        else:
            cause = f"{COMMIT_ATTEMPTS} commits failed, the last: {cause}"
        # A no-op over a committed journal — one the guard wrote, reported or
        # not —, which is then the post-commit phase's below.
        await update_service.let_go_of_the_trial(cause)
    await update_service.run_post_commit()


async def _once_listening(listening: asyncio.Event) -> None:
    """READY=1 and what follows it, once :8000 actually answers.

    Not at the lifespan's end: uvicorn binds after the lifespan startup
    returns, so READY=1 from there would let systemd call the unit started
    before it could answer, and a release on trial would count its hold from
    before it served anything.

    milo-backend.service is Type=notify, and every unit ordered After= it — the
    source units, milo-navidrome, and milo-bluealsa(-aplay), which systemd
    starts at boot — has its start job held until READY=1, and dispatched a
    moment after it. So the lingering sweep runs here, and reads each unit's
    queued job as well as its state (`sweep_state`): a start not dispatched yet
    reads `inactive`, and spared it would be up a moment later; stopped, it is
    cancelled. Here rather than in the lifespan, it also never waits behind a
    transition whose unit start is itself held until READY=1 (a MENU press
    during init).
    Two starts the backend issues itself during init are still held that way,
    bounded by their own timeout rather than waited on: the catalog's
    milo-navidrome (`MusicLibrarySource`, spawned, not awaited) and the unit of
    a source selected before READY=1.
    """
    await listening.wait()
    try:
        if _sd_notify(b"READY=1"):
            logger.info("READY=1 sent to systemd")
    except OSError as e:
        # The start then fails on TimeoutStartSec, which the update guard counts.
        logger.error(f"READY=1 could not be sent to systemd: {e}")
    try:
        await state_machine.stop_lingering_units(systemd_manager)
    except Exception as e:
        logger.error(f"Lingering source units not swept: {e}")
    try:
        await update_service.release_stale_inhibitor()
    except Exception as e:
        logger.error(f"The update's shutdown inhibitor not checked: {e}")
    await _finish_milo_update()


async def run_teardown(entries, timeout: float = CLEANUP_TIMEOUT_S) -> None:
    """
    Run each shutdown entry isolated and bounded.

    `entries` is a list of (name, zero-arg coroutine function). One entry that
    raises or blocks must not deny the others theirs — which is why this is a
    loop and not a straight-line sequence under one try.
    """
    for name, entry in entries:
        try:
            await asyncio.wait_for(entry(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Cleanup timed out after {timeout}s: {name}")
        except Exception as e:
            logger.error(f"Cleanup error in {name}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management with async service initialization."""
    global _listening
    try:
        # First, before any source can start: the kernel reports a clock step
        # once, to a timerfd that exists when it lands, and timesyncd's boot
        # step came 5-7 s after the init join. With no source active yet, a
        # step only finds nothing to re-date.
        state_machine.start_clock_step_watch()

        await initialize_services()

        # The schema's `info.version` names the build this unit runs, the string
        # its own version row shows, rather than FastAPI's "0.1.0".
        try:
            app.version = await get_service("satellite_update_service").get_server_version() or app.version
        except OSError as e:
            logger.warning("The API schema keeps version %s: git could not run (%s)", app.version, e)

        logger.info("Waiting for services initialization to complete...")
        init_task = get_init_task()
        if init_task:
            await init_task
        logger.info("Services initialization completed")

        # Enable WebSocket broadcasting for backend errors/warnings
        _ws_log_handler.set_state_machine(state_machine)

        state_machine.start_inactivity_monitor()

        # The unit must always be reachable: with no link at all it raises its
        # own access point. Re-evaluated on every NetworkManager property
        # change thereafter — this is only the first look.
        await network_service.evaluate_reachability()

        logger.info("Milo backend startup completed with unified settings")

    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        raise

    _listening = asyncio.Event()
    _after_listening.spawn(_once_listening(_listening), label="once_listening")

    yield

    logger.info("Milo backend shutting down...")
    state_machine.cleanup()

    # Order: silence the registry's producer, then the two flushes, then
    # everything else. client_registry and volume are the only entries that
    # WRITE (pending multiroom state, pending volume) and they used to sit
    # behind camilladsp — the entry most likely to block on a daemon going down
    # with us. snapcast_websocket still leads them, because it is what mutates
    # the registry: flushing before cancelling its loop would leave a debounce
    # armed that nothing gets to fire. Each entry is now isolated and bounded,
    # so one hanging cleanup can no longer deny every later one its own.
    # Bound: systemd's TimeoutStopSec is 10 s (system/milo-backend.service), and
    # cutting a flush is survivable because both write through os.replace — an
    # interrupted one leaves the previous file, never a truncated one.
    teardown = [
        # First: a post-commit phase still running would start and stop the
        # units the entries below are tearing down. Cut, it resumes at the next
        # start.
        ("after_listening", _after_listening.cancel_all),
        ("snapcast_websocket", snapcast_websocket_service.cleanup),
        # The client app pushes satellites' admissions owe them: each restarts
        # a speaker, and a cut one is read back from its status at the next
        # start. After snapcast_websocket, whose admissions spawn them: cut
        # first, an admission still running would spawn one into a set
        # nothing cancels any more.
        ("satellite_updates", get_service("satellite_update_service").cleanup),
        ("client_registry", client_registry_service.cleanup),
        ("volume", volume_service.cleanup),
        ("camilladsp", camilladsp_service.cleanup),
        ("pending_clients", get_service("pending_clients_service").shutdown),
        ("calibration", get_service("calibration_service").cleanup),
        ("mac_link_calibration", get_service("mac_link_calibration_service").cleanup),
        ("multiroom_equalizer", multiroom_equalizer_service.cleanup),
        ("equalizer_proxy", equalizer_proxy_service.cleanup),
        ("levels_monitor", levels_monitor.cleanup),
        ("screen", screen_controller.cleanup),
        ("bt_remote", bt_remote_controller.cleanup),
        ("ir_remote", ir_remote_controller.cleanup),
        ("fan", fan_controller.cleanup),
        ("connectivity", get_service("connectivity_service").cleanup),
        ("wifi_adoption", wifi_adoption_service.cleanup),
        ("network", network_service.cleanup),
        ("crossover", crossover_service.cleanup),
        ("hostname_conflict", get_service("hostname_conflict_service").cleanup),
        ("music_library_shares", get_service("music_library_source").shares.cleanup),
        ("push_service", get_service("push_service").cleanup),
        ("notices", notice_service.cleanup),
        ("apns", get_service("apns_client").cleanup),
        # Each source's mailbox: its task is ended and a caller still waiting
        # is answered, so nothing is left pending on a closing loop.
        ("audio_sources", state_machine.shutdown_sources),
        ("websockets", ws_manager.cleanup),
    ]
    if rotary_controller:
        # Absent on a unit with no encoder; a release, so position is free
        teardown.append(("rotary", rotary_controller.cleanup))

    await run_teardown(teardown)
    logger.info("Cleanup completed")

# The docs live under /api because that is the only prefix nginx proxies to the
# backend (plus /ws; the site is rootfs/etc/nginx/sites-enabled/milo) — at their
# default paths they were served, but only on :8000, while port 80 fell through
# to the SPA's index.html and answered every fetch 200 text/html. Three clients
# derive from this schema (frontend, Milo-Mac, Milo-iOS); Swagger UI itself
# loads from a CDN, so /api/docs needs the internet while /api/openapi.json, the
# machine-readable half, does not.
app = FastAPI(
    title="Milo API",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

# Starlette runs the last-added middleware first. The tracker is added first, so it
# sits inside every gate: a request one of them refuses is nobody using the
# recovery access point.
app.add_middleware(RecoveryClientTracker, network_service=network_service)

# Outside the tracker for that reason, inside CORS so an admitted reader reads its
# 403 like any route's. See backend/api/middleware.py.
app.add_middleware(
    RecoveryAccessPointGuard,
    network_service=network_service, settings_service=settings_service,
)

# CORS configuration - restricted to authorized origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization"],
)

# Added last, so it wraps CORS: what CORS admits as a reader still has to be a
# request we accept at all. See backend/api/middleware.py.
app.add_middleware(RequestOriginGate)

audio_router = audio.create_router(state_machine)
app.include_router(audio_router)

routing_router = create_routing_router(
    routing_service, state_machine, snapcast_service,
    settings_service=settings_service,
    client_registry_service=client_registry_service,
    calibration_service=get_service("calibration_service"),
)
app.include_router(routing_router)

multiroom_equalizer_service = get_service("multiroom_equalizer_service")
equalizer_router = create_equalizer_router(
    camilladsp_service, routing_service,
    crossover_service,
    client_registry_service, equalizer_router_service, multiroom_equalizer_service,
    levels_monitor
)
app.include_router(equalizer_router)

volume_router = create_volume_router(volume_service, client_registry_service)
app.include_router(volume_router)

radio_router = setup_radio_routes(
    lambda: state_machine.sources.get(AudioSource.RADIO)
)
app.include_router(radio_router, prefix="/api")

podcast_router = setup_podcast_routes(
    lambda: state_machine.sources.get(AudioSource.PODCAST)
)
app.include_router(podcast_router, prefix="/api")

airplay_router = setup_airplay_routes(
    lambda: state_machine.sources.get(AudioSource.AIRPLAY)
)
app.include_router(airplay_router, prefix="/api")

cd_router = setup_cd_routes(
    lambda: state_machine.sources.get(AudioSource.CD)
)
app.include_router(cd_router, prefix="/api")

music_library_router = setup_music_library_routes(
    lambda: state_machine.sources.get(AudioSource.MUSIC_LIBRARY)
)
app.include_router(music_library_router, prefix="/api")

spotify_router = setup_spotify_routes(
    lambda: state_machine.sources.get(AudioSource.SPOTIFY)
)
app.include_router(spotify_router, prefix="/api")

settings_router = create_settings_router(
    volume_service,
    state_machine,
    screen_controller,
    systemd_manager,
    routing_service,
    hardware_service,
    settings_service,
    get_service("multiroom_equalizer_service"),
    get_service("mac_link_calibration_service"),
    update_service=update_service,
)
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])

system_router = create_system_router(
    systemd_manager,
    hostname_conflict_service=get_service("hostname_conflict_service"),
    hardware_service=hardware_service,
    diagnostic_service=get_service("diagnostic_service"),
    state_machine=state_machine,
    satellite_timezone_service=get_service("satellite_timezone_service"),
    update_service=update_service,
    settings_service=settings_service,
    notice_service=notice_service,
)
app.include_router(system_router, prefix="/api/system", tags=["system"])

programs_router = create_programs_router(
    update_service=update_service,
    satellite_update_service=get_service("satellite_update_service"),
    state_machine=state_machine,
)
app.include_router(programs_router)

health_router = create_health_router(
    state_machine, routing_service,
    settings_service=settings_service, network_service=network_service,
    camilladsp_service=camilladsp_service,
    snapcast_websocket_service=snapcast_websocket_service,
    notice_service=notice_service,
)
app.include_router(health_router)

errors_router = create_errors_router()
app.include_router(errors_router)

pending_clients_service = get_service("pending_clients_service")
multiroom_router = create_multiroom_router(
    client_registry_service, multiroom_equalizer_service, pending_clients_service, crossover_service,
    snapcast_service, equalizer_router_service, volume_service,
    satellite_secret=get_service("satellite_secret"),
)
app.include_router(multiroom_router)

bt_remote_router = create_bt_remote_router(bt_remote_controller)
app.include_router(bt_remote_router)

ir_remote_router = create_ir_remote_router(ir_remote_controller)
app.include_router(ir_remote_router)

fan_router = create_fan_router(fan_controller, settings_service)
app.include_router(fan_router)

setup_router = create_setup_router(
    settings_service, hardware_service, network_service, systemd_manager,
    update_service=update_service,
)
app.include_router(setup_router)

network_router = create_network_router(network_service)
app.include_router(network_router)

discovery_router = create_discovery_router(
    network_service, wifi_adoption_service, get_service("calibration_service")
)
app.include_router(discovery_router)

app.include_router(create_qobuz_account_router(
    systemd_manager, get_service("qobuz_source").account_changed
))

app.include_router(create_lyrics_router(get_service("lyrics_service")))
app.include_router(create_push_router(
    get_service("push_token_registry"), get_service("push_service")
))

app.add_api_websocket_route("/ws", websocket_server.websocket_endpoint)

class _Server(uvicorn.Server):
    """uvicorn's server, telling the app when its socket is bound."""

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            mark_listening()


if __name__ == "__main__":
    # Suppress noisy uvicorn WebSocket connection logs
    # Must set before uvicorn.Config() to affect internal loggers
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # The app object, never the "backend.main:app" import string: the unit runs
    # this file as __main__, so an import string makes uvicorn import the module
    # a second time under its real name and every module-level statement above
    # runs twice in the one process — both log handlers attached twice, so every
    # warning landed in errors.log as two identical lines. Passing the object
    # keeps one import. (reload/workers, which need the string form, are off.)
    # A second import would also leave the lifespan waiting on an event this
    # module's server never sets.
    server = _Server(uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        access_log=False,
        log_level="warning",
        # Only nginx, on loopback, may name the client in X-Forwarded-For. Pinned so
        # FORWARDED_ALLOW_IPS cannot widen it: the recovery-AP guard reads the result.
        forwarded_allow_ips="127.0.0.1",
    ))
    server.run()
    # uvicorn.run's own exit for a startup that failed (lifespan or bind), so
    # systemd records a failure rather than a clean stop.
    if not server.started:
        sys.exit(3)
