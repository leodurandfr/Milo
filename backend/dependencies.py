# backend/dependencies.py
"""
Service Registry for Milo - Feature-based architecture.

Replaces dependency-injector with a simple dict-based registry.
Supports lazy singleton creation, circular dependency resolution, and test reset.
"""
import json
import os
import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Service registry - singleton instances
_services: Dict[str, Any] = {}

# Initialization task reference
_init_task: Optional[asyncio.Task] = None


def get_service(name: str) -> Any:
    """
    Get a service by name, creating it if needed.

    Services are created lazily on first access.
    """
    if name not in _services:
        _services[name] = _create_service(name)
    return _services[name]


def reset_services() -> None:
    """Reset all services - for testing only."""
    global _init_task
    _services.clear()
    _init_task = None


def get_init_task() -> Optional[asyncio.Task]:
    """Get the initialization task for awaiting in lifespan."""
    return _init_task


# =============================================================================
# Service Factory
# =============================================================================

def _load_taddy_credentials() -> Dict[str, str]:
    """Load Taddy API credentials from settings.json."""
    settings_file = '/var/lib/milo/settings.json'
    try:
        if os.path.exists(settings_file):
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                podcast_settings = settings.get('podcast', {})
                return {
                    "taddy_user_id": podcast_settings.get('taddy_user_id', ''),
                    "taddy_api_key": podcast_settings.get('taddy_api_key', '')
                }
    except Exception:
        pass
    return {"taddy_user_id": "", "taddy_api_key": ""}


def _create_service(name: str) -> Any:
    """
    Factory for creating service instances.

    Services are created with their direct dependencies only.
    Circular dependencies are resolved in initialize_services().
    """
    # Import here to avoid circular imports at module load
    from backend.core.events import get_event_bus
    from backend.core.state import AudioStateMachine
    from backend.core.settings import SettingsService
    from backend.core.systemd import SystemdServiceManager
    from backend.core.multiroom import AudioRoutingService
    from backend.core.multiroom.snapcast import SnapcastService
    from backend.core.multiroom.websocket import SnapcastWebSocketService
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.crossover import CrossoverService
    from backend.core.dsp import CamillaDSPService, DspClientProxyService, DspSettingsSyncService, MultiroomDspService
    from backend.core.volume import VolumeService
    from backend.core.updates import VersionService, UpdateService, SatelliteUpdateService
    from backend.hardware import HardwareService, RotaryVolumeController, ScreenController
    from backend.ws import WebSocketManager, WebSocketEventHandler
    from backend.features.spotify import SpotifySource
    from backend.features.mac import MacSource
    from backend.features.bluetooth import BluetoothSource
    from backend.features.radio import RadioSource
    from backend.features.podcast import PodcastSource
    from backend.features.airplay import AirPlaySource

    creators = {
        # Core services (no dependencies or simple deps)
        "event_bus": lambda: get_event_bus(),
        "systemd_manager": lambda: SystemdServiceManager(),
        "settings_service": lambda: SettingsService(),
        "hardware_service": lambda: HardwareService(),
        "snapcast_service": lambda: SnapcastService(host="127.0.0.1"),
        "websocket_manager": lambda: WebSocketManager(),

        # Services with dependencies
        "audio_state_machine": lambda: AudioStateMachine(
            event_bus=get_service("event_bus")
        ),
        "client_registry_service": lambda: ClientRegistryService(
            settings_service=get_service("settings_service")
        ),
        "camilladsp_service": lambda: CamillaDSPService(
            settings_service=get_service("settings_service")
        ),
        "websocket_event_handler": lambda: WebSocketEventHandler(
            ws_manager=get_service("websocket_manager")
        ),
        "audio_routing_service": lambda: AudioRoutingService(
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "snapcast_websocket_service": lambda: SnapcastWebSocketService(
            state_machine=get_service("audio_state_machine"),
            routing_service=get_service("audio_routing_service"),
            settings_service=get_service("settings_service"),
            host="127.0.0.1",
            port=1780
        ),
        "dsp_client_proxy_service": lambda: DspClientProxyService(
            routing_service=get_service("audio_routing_service")
        ),
        "volume_service": lambda: VolumeService(
            state_machine=get_service("audio_state_machine"),
            snapcast_service=get_service("snapcast_service"),
            settings_service=get_service("settings_service"),
            camilladsp_service=get_service("camilladsp_service"),
            dsp_client_proxy_service=get_service("dsp_client_proxy_service"),
            hardware_service=get_service("hardware_service")
        ),
        "rotary_controller": lambda: RotaryVolumeController(
            volume_service=get_service("volume_service"),
            clk_pin=22,
            dt_pin=27,
            sw_pin=23
        ),
        "screen_controller": lambda: ScreenController(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            hardware_service=get_service("hardware_service")
        ),
        "crossover_service": lambda: CrossoverService(
            settings_service=get_service("settings_service"),
            dsp_service=get_service("camilladsp_service")
        ),
        "dsp_settings_sync_service": lambda: DspSettingsSyncService(
            proxy_service=get_service("dsp_client_proxy_service"),
            dsp_service=get_service("camilladsp_service"),
            client_registry=get_service("client_registry_service")
        ),
        "multiroom_dsp_service": lambda: MultiroomDspService(
            client_registry_service=get_service("client_registry_service"),
            camilladsp_service=get_service("camilladsp_service")
        ),
        "dsp_router": lambda: _create_dsp_router(),

        # Update services
        "version_service": lambda: VersionService(),
        "update_service": lambda: UpdateService(),
        "satellite_update_service": lambda: SatelliteUpdateService(
            snapcast_service=get_service("snapcast_service"),
            client_registry_service=get_service("client_registry_service")
        ),

        # Audio sources
        "spotify_source": lambda: SpotifySource(
            event_bus=get_service("event_bus"),
            config={"config_path": "/var/lib/milo/go-librespot/config.yml"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "mac_source": lambda: MacSource(
            event_bus=get_service("event_bus"),
            config={
                "rtp_port": 10001,
                "rs8m_port": 10002,
                "rtcp_port": 10003,
                "audio_output": "hw:1,0"
            },
            state_machine=get_service("audio_state_machine"),
            systemd_manager=get_service("systemd_manager")
        ),
        "bluetooth_source": lambda: BluetoothSource(
            event_bus=get_service("event_bus"),
            config={
                "daemon_options": "--keep-alive=5",
                "bluetooth_service": "bluetooth.service",
                "stop_bluetooth_on_exit": True,
                "auto_agent": True
            },
            state_machine=get_service("audio_state_machine"),
            systemd_manager=get_service("systemd_manager")
        ),
        "radio_source": lambda: RadioSource(
            event_bus=get_service("event_bus"),
            config={"ipc_socket": "/run/milo/radio-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "podcast_source": lambda: _create_podcast_source(),
        "airplay_source": lambda: AirPlaySource(
            event_bus=get_service("event_bus"),
            config={"metadata_pipe": "/tmp/shairport-sync-metadata"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
    }

    if name not in creators:
        raise ValueError(f"Unknown service: {name}")

    return creators[name]()


def _create_podcast_source():
    """Create podcast source with Taddy credentials."""
    from backend.features.podcast import PodcastSource

    creds = _load_taddy_credentials()
    return PodcastSource(
        event_bus=get_service("event_bus"),
        config={
            "ipc_socket": "/run/milo/podcast-ipc.sock",
            "taddy_user_id": creds["taddy_user_id"],
            "taddy_api_key": creds["taddy_api_key"]
        },
        state_machine=get_service("audio_state_machine"),
        settings_service=get_service("settings_service"),
        systemd_manager=get_service("systemd_manager")
    )


def _create_dsp_router():
    """Create DspRouter with dependencies."""
    from backend.core.multiroom.dsp_router import DspRouter

    return DspRouter(
        client_registry=get_service("client_registry_service"),
        dsp_service=get_service("camilladsp_service"),
        proxy_service=get_service("dsp_client_proxy_service")
    )


# =============================================================================
# Service Initialization
# =============================================================================

def initialize_services() -> None:
    """
    Initialize services after creation - CRITICAL ORDER.

    WARNING: The execution order of this function is CRITICAL.
    Do not modify without understanding circular dependencies.

    INITIALIZATION ORDER:
    1. Retrieve instances (triggers lazy creation)
    2. Resolve circular dependencies via setters
    3. Register plugins in state machine
    4. Start parallel async initialization
    """
    from backend.core.models.audio_state import AudioSource

    # =========================================================================
    # STEP 1: Retrieve instances (triggers lazy creation)
    # =========================================================================
    state_machine = get_service("audio_state_machine")
    routing_service = get_service("audio_routing_service")
    volume_service = get_service("volume_service")
    rotary_controller = get_service("rotary_controller")
    screen_controller = get_service("screen_controller")
    snapcast_websocket_service = get_service("snapcast_websocket_service")
    camilladsp_service = get_service("camilladsp_service")
    crossover_service = get_service("crossover_service")
    client_registry_service = get_service("client_registry_service")
    websocket_event_handler = get_service("websocket_event_handler")
    dsp_settings_sync_service = get_service("dsp_settings_sync_service")
    dsp_client_proxy_service = get_service("dsp_client_proxy_service")
    multiroom_dsp_service = get_service("multiroom_dsp_service")

    # Set websocket handler on state machine for backward compatibility
    state_machine.websocket_handler = websocket_event_handler

    # =========================================================================
    # STEP 2: Resolve circular dependencies (CRITICAL ORDER)
    # =========================================================================

    # 2.1 - routing_service → state_machine.get_plugin()
    routing_service.set_plugin_callback(lambda source: state_machine.get_plugin(source))

    # 2.2 - routing_service ↔ snapcast_websocket_service
    routing_service.set_snapcast_websocket_service(snapcast_websocket_service)

    # 2.3 - routing_service → snapcast_service
    routing_service.set_snapcast_service(get_service("snapcast_service"))

    # 2.4 - routing_service → state_machine
    routing_service.set_state_machine(state_machine)

    # 2.5 - state_machine ← routing_service (circular reference)
    state_machine.routing_service = routing_service

    # 2.5b - state_machine ← crossover_service
    state_machine.crossover_service = crossover_service

    # 2.5c - state_machine ← dsp_settings_sync_service (for zone DSP sync on reconnection)
    state_machine.dsp_settings_sync_service = dsp_settings_sync_service

    # 2.5d - state_machine ← dsp_client_proxy_service (for remote DSP control)
    state_machine.dsp_client_proxy_service = dsp_client_proxy_service

    # 2.6 - camilladsp_service → state_machine
    camilladsp_service.set_state_machine(state_machine)

    # 2.7 - routing_service → camilladsp_service
    routing_service.set_camilladsp_service(camilladsp_service)

    # 2.8 - crossover_service → state_machine
    crossover_service.set_state_machine(state_machine)

    # 2.9 - client_registry_service → state_machine
    client_registry_service.set_state_machine(state_machine)
    state_machine.client_registry = client_registry_service

    # 2.10 - volume_service._state_store → client_registry_service
    volume_service._state_store.set_registry(client_registry_service)

    # 2.11 - volume_service → snapcast_websocket_service
    volume_service.set_snapcast_websocket_service(snapcast_websocket_service)

    # 2.11b - volume_service → client_registry (for DSPController IP lookup)
    volume_service.set_client_registry(client_registry_service)

    # 2.11c - volume_service → routing_service (for multiroom mode detection)
    volume_service.set_routing_service(routing_service)

    # 2.12 - crossover_service → client_registry_service
    crossover_service.set_registry(client_registry_service)

    # 2.13 - state_machine → volume_service + snapcast_service (moved from main.py)
    state_machine.volume_service = volume_service
    state_machine.snapcast_service = get_service("snapcast_service")

    # 2.14 - multiroom_dsp_service → state_machine (for event broadcasting)
    multiroom_dsp_service.set_state_machine(state_machine)

    # 2.15 - multiroom_dsp_service → proxy_service + routing_service (for remote client control)
    multiroom_dsp_service.set_proxy_service(dsp_client_proxy_service)
    multiroom_dsp_service.set_routing_service(routing_service)

    # 2.16 - multiroom_dsp_service → dsp_router (for targeted filter updates)
    multiroom_dsp_service.set_dsp_router(get_service("dsp_router"))

    # 2.17 - snapcast_websocket_service → direct service references
    snapcast_websocket_service.set_snapcast_service(get_service("snapcast_service"))
    snapcast_websocket_service.set_volume_service(volume_service)
    snapcast_websocket_service.set_crossover_service(crossover_service)
    snapcast_websocket_service.set_dsp_client_proxy_service(dsp_client_proxy_service)
    snapcast_websocket_service.set_dsp_settings_sync_service(dsp_settings_sync_service)
    snapcast_websocket_service.set_camilladsp_service(camilladsp_service)

    # =========================================================================
    # STEP 3: Register sources (MUST be done BEFORE init_async)
    # =========================================================================
    state_machine.register_plugin(AudioSource.SPOTIFY, get_service("spotify_source"))
    state_machine.register_plugin(AudioSource.BLUETOOTH, get_service("bluetooth_source"))
    state_machine.register_plugin(AudioSource.MAC, get_service("mac_source"))
    state_machine.register_plugin(AudioSource.RADIO, get_service("radio_source"))
    state_machine.register_plugin(AudioSource.PODCAST, get_service("podcast_source"))
    state_machine.register_plugin(AudioSource.AIRPLAY, get_service("airplay_source"))

    # =========================================================================
    # STEP 4: Parallel async initialization
    # =========================================================================
    global _init_task

    # Get radio source for initialization (station data needs early init for API)
    radio_source = get_service("radio_source")

    async def init_async():
        """Async initialization with error handling."""
        services = [
            ("client_registry_service", client_registry_service.initialize()),
            ("routing_service", routing_service.initialize()),
            ("volume_service", volume_service.initialize()),
            ("rotary_controller", rotary_controller.initialize()),
            ("screen_controller", screen_controller.initialize()),
            ("snapcast_websocket_service", snapcast_websocket_service.initialize()),
            ("camilladsp_service", camilladsp_service.initialize()),
            ("crossover_service", crossover_service.initialize()),
            # Radio station data needs early init for API access
            ("radio_source", radio_source.initialize())
        ]

        results = await asyncio.gather(
            *[coro for _, coro in services],
            return_exceptions=True
        )

        for (service_name, _), result in zip(services, results):
            if isinstance(result, Exception):
                logger.error("%s initialization failed: %s", service_name, result)
            else:
                logger.info("%s initialized successfully", service_name)

        # Check critical services
        critical_services = ["routing_service", "volume_service"]
        for i, (service_name, _) in enumerate(services):
            if service_name in critical_services and isinstance(results[i], Exception):
                logger.critical("Critical service %s failed to initialize", service_name)
                raise results[i]

    _init_task = asyncio.create_task(init_async())
