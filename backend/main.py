# backend/main.py
"""
Main entry point for the Milo application.
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from backend.dependencies import get_service, initialize_services, get_init_task
from backend.api import audio
from backend.api.routing import create_routing_router
from backend.core.multiroom.routes import create_snapcast_router
from backend.api.equalizer import create_equalizer_router
from backend.api.volume import create_volume_router
from backend.features.bluetooth.routes import setup_bluetooth_routes
from backend.features.radio.routes import setup_radio_routes
from backend.features.podcast.routes import setup_podcast_routes
from backend.features.airplay.routes import setup_airplay_routes
from backend.features.cd.routes import setup_cd_routes
from backend.api.settings import create_settings_router
from backend.api.system import create_system_router
from backend.api.programs import create_programs_router
from backend.hardware.bt_remote_routes import create_bt_remote_router
from backend.api.health import create_health_router
from backend.api.errors import create_errors_router
from backend.api.setup import create_setup_router
from backend.api.wifi import create_wifi_router
from backend.api.multiroom import create_multiroom_router
from backend.ws import WebSocketServer
from backend.core.models.audio_state import AudioSource

# Configurable log level via MILO_LOG_LEVEL environment variable
# Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: WARNING)
_log_level_name = os.environ.get('MILO_LOG_LEVEL', 'WARNING').upper()
_log_level = getattr(logging, _log_level_name, logging.WARNING)
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)

# Persist errors/warnings to rotating log file
from backend.config.constants import ERROR_LOG_FILE
_file_handler = RotatingFileHandler(ERROR_LOG_FILE, maxBytes=2*1024*1024, backupCount=3)
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(name)s - %(message)s'))
logging.getLogger().addHandler(_file_handler)

# Broadcast backend errors/warnings to frontend via WebSocket
from backend.core.log_handler import WebSocketLogHandler
_ws_log_handler = WebSocketLogHandler(level=logging.WARNING)
logging.getLogger("backend").addHandler(_ws_log_handler)

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

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
systemd_manager = get_service("systemd_manager")
hardware_service = get_service("hardware_service")
crossover_service = get_service("crossover_service")
equalizer_proxy_service = get_service("equalizer_client_proxy_service")
equalizer_sync_service = get_service("equalizer_settings_sync_service")
client_registry_service = get_service("client_registry_service")
equalizer_router_service = get_service("equalizer_router")
wifi_service = get_service("wifi_service")
ws_manager = get_service("websocket_manager")
websocket_server = WebSocketServer(ws_manager, state_machine, volume_service, settings_service, wifi_service)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management with async service initialization."""
    try:
        initialize_services()

        logger.info("Waiting for services initialization to complete...")
        init_task = get_init_task()
        if init_task:
            await init_task
        logger.info("Services initialization completed")

        # Enable WebSocket broadcasting for backend errors/warnings
        _ws_log_handler.set_state_machine(state_machine)

        # Plugins are initialized on-demand when activated (state.py:_start_source)
        # Radio is pre-initialized in init_async() for API access

        # Load inactivity timeout from settings (0 = disabled, default 7200s = 2h)
        audio_settings = await settings_service.get_setting('audio') or {}
        inactivity_timeout = audio_settings.get('inactivity_timeout', 7200)
        state_machine.start_inactivity_monitor(inactivity_timeout)

        # Activate WiFi hotspot for first-boot setup if no network is available
        await wifi_service.maybe_start_hotspot(settings_service)

        logger.info("Milo backend startup completed with unified settings")

    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        raise

    yield

    logger.info("Milo backend shutting down...")
    try:
        state_machine.cleanup()
        await camilladsp_service.cleanup()
        await snapcast_websocket_service.cleanup()
        await volume_service.cleanup()
        rotary_controller.cleanup()
        screen_controller.cleanup()
        bt_remote_controller.cleanup()
        logger.info("Cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

app = FastAPI(title="Milo API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration - restricted to authorized origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://milo.local",
        "https://milo.local",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization"],
)

audio_router = audio.create_router(state_machine)
app.include_router(audio_router)

routing_router = create_routing_router(routing_service, state_machine)
app.include_router(routing_router)

snapcast_router = create_snapcast_router(
    routing_service, snapcast_service, state_machine,
    camilladsp_service=camilladsp_service, proxy_service=equalizer_proxy_service,
    settings_service=settings_service
)
app.include_router(snapcast_router)

multiroom_equalizer_service = get_service("multiroom_equalizer_service")
equalizer_router = create_equalizer_router(
    camilladsp_service, state_machine, settings_service, routing_service,
    crossover_service, equalizer_proxy_service, equalizer_sync_service,
    client_registry_service, equalizer_router_service, multiroom_equalizer_service,
    volume_service
)
app.include_router(equalizer_router)

volume_router = create_volume_router(volume_service, client_registry_service, settings_service)
app.include_router(volume_router)

bluetooth_router = setup_bluetooth_routes(
    lambda: state_machine.plugins.get(AudioSource.BLUETOOTH)
)
app.include_router(bluetooth_router, prefix="/api")

radio_router = setup_radio_routes(
    lambda: state_machine.plugins.get(AudioSource.RADIO)
)
app.include_router(radio_router, prefix="/api")

podcast_router = setup_podcast_routes(
    lambda: state_machine.plugins.get(AudioSource.PODCAST)
)
app.include_router(podcast_router, prefix="/api")

airplay_router = setup_airplay_routes(
    lambda: state_machine.plugins.get(AudioSource.AIRPLAY)
)
app.include_router(airplay_router, prefix="/api")

cd_router = setup_cd_routes(
    lambda: state_machine.plugins.get(AudioSource.CD)
)
app.include_router(cd_router, prefix="/api")

settings_router = create_settings_router(
    volume_service,
    state_machine,
    screen_controller,
    systemd_manager,
    routing_service,
    hardware_service,
    settings_service
)
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])

system_router = create_system_router()
app.include_router(system_router, prefix="/api/system", tags=["system"])

programs_router = create_programs_router(
    update_service=get_service("update_service"),
    satellite_update_service=get_service("satellite_update_service"),
    state_machine=state_machine
)
app.include_router(programs_router)

health_router = create_health_router(
    state_machine, routing_service, snapcast_service,
    settings_service=settings_service, wifi_service=wifi_service
)
app.include_router(health_router)

errors_router = create_errors_router()
app.include_router(errors_router)

pending_clients_service = get_service("pending_clients_service")
multiroom_router = create_multiroom_router(client_registry_service, multiroom_equalizer_service, pending_clients_service)
app.include_router(multiroom_router)

bt_remote_router = create_bt_remote_router(bt_remote_controller)
app.include_router(bt_remote_router)

setup_router = create_setup_router(settings_service, hardware_service, systemd_manager)
app.include_router(setup_router)

wifi_router = create_wifi_router(wifi_service)
app.include_router(wifi_router)

app.add_websocket_route("/ws", websocket_server.websocket_endpoint)

if __name__ == "__main__":
    import uvicorn

    # Suppress noisy uvicorn WebSocket connection logs
    # Must set before uvicorn.run() to affect internal loggers
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        access_log=False,
        log_level="warning",
    )
