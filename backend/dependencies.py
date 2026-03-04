# backend/dependencies.py
"""
Service Registry for Milo - Feature-based architecture.

Replaces dependency-injector with a simple dict-based registry.
Supports lazy singleton creation, circular dependency resolution, and test reset.
"""
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

def _import(module: str, attr: str):
    """Import a single attribute from a module (lazy per-service import)."""
    from importlib import import_module
    return getattr(import_module(module), attr)


def _const(name: str):
    """Import a constant from backend.config.constants."""
    from backend.config import constants
    return getattr(constants, name)


def _create_service(name: str) -> Any:
    """
    Factory for creating service instances.

    Services are created with their direct dependencies only.
    Circular dependencies are resolved in initialize_services().

    Each creator imports only the modules it needs to avoid loading
    all 20+ modules on every lazy service creation call.
    """
    creators = {
        # Core services (no dependencies or simple deps)
        "systemd_manager": lambda: _import("backend.core.systemd", "SystemdServiceManager")(),
        "settings_service": lambda: _import("backend.core.settings", "SettingsService")(),
        "hardware_service": lambda: _import("backend.hardware", "HardwareService")(),
        "snapcast_service": lambda: _import("backend.core.multiroom.snapcast", "SnapcastService")(host="127.0.0.1"),
        "websocket_manager": lambda: _import("backend.ws", "WebSocketManager")(),

        # Services with dependencies
        "audio_state_machine": lambda: _import("backend.core.state", "AudioStateMachine")(),
        "client_registry_service": lambda: _import("backend.core.multiroom.client_registry", "ClientRegistryService")(
            settings_service=get_service("settings_service")
        ),
        "camilladsp_service": lambda: _import("backend.core.equalizer", "CamillaDSPService")(
            settings_service=get_service("settings_service")
        ),
        "audio_routing_service": lambda: _import("backend.core.multiroom", "AudioRoutingService")(
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "snapcast_websocket_service": lambda: _import("backend.core.multiroom.websocket", "SnapcastWebSocketService")(
            state_machine=get_service("audio_state_machine"),
            routing_service=get_service("audio_routing_service"),
            settings_service=get_service("settings_service"),
            host="127.0.0.1",
            port=1780
        ),
        "equalizer_client_proxy_service": lambda: _import("backend.core.equalizer", "EqualizerClientProxyService")(
            routing_service=get_service("audio_routing_service")
        ),
        "volume_service": lambda: _import("backend.core.volume", "VolumeService")(
            state_machine=get_service("audio_state_machine"),
            snapcast_service=get_service("snapcast_service"),
            settings_service=get_service("settings_service"),
            camilladsp_service=get_service("camilladsp_service"),
            equalizer_client_proxy_service=get_service("equalizer_client_proxy_service"),
            hardware_service=get_service("hardware_service"),
            equalizer_router=get_service("equalizer_router")
        ),
        "rotary_controller": lambda: _import("backend.hardware", "RotaryVolumeController")(
            volume_service=get_service("volume_service"),
            clk_pin=_const("ROTARY_CLK_PIN"),
            dt_pin=_const("ROTARY_DT_PIN"),
            sw_pin=_const("ROTARY_SW_PIN")
        ),
        "screen_controller": lambda: _import("backend.hardware", "ScreenController")(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            hardware_service=get_service("hardware_service")
        ),
        "crossover_service": lambda: _import("backend.core.multiroom.crossover", "CrossoverService")(
            settings_service=get_service("settings_service"),
            camilladsp_service=get_service("camilladsp_service")
        ),
        "equalizer_settings_sync_service": lambda: _import("backend.core.equalizer", "EqualizerSettingsSyncService")(
            proxy_service=get_service("equalizer_client_proxy_service"),
            camilladsp_service=get_service("camilladsp_service"),
            client_registry=get_service("client_registry_service")
        ),
        "multiroom_equalizer_service": lambda: _import("backend.core.equalizer", "MultiroomEqualizerService")(
            client_registry_service=get_service("client_registry_service"),
            camilladsp_service=get_service("camilladsp_service")
        ),
        "equalizer_router": lambda: _create_equalizer_router(),

        # Update services
        "update_service": lambda: _import("backend.core.updates", "UpdateService")(),
        "satellite_update_service": lambda: _import("backend.core.updates", "SatelliteUpdateService")(
            snapcast_service=get_service("snapcast_service"),
            client_registry_service=get_service("client_registry_service")
        ),

        # Audio sources
        "spotify_source": lambda: _import("backend.features.spotify", "SpotifySource")(
            config={"config_path": "/var/lib/milo/go-librespot/config.yml"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "mac_source": lambda: _import("backend.features.mac", "MacSource")(
            config={
                "rtp_port": _const("MAC_RTP_PORT"),
                "rs8m_port": _const("MAC_RS8M_PORT"),
                "rtcp_port": _const("MAC_RTCP_PORT"),
                "audio_output": _const("MAC_AUDIO_OUTPUT"),
            },
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "bluetooth_source": lambda: _import("backend.features.bluetooth", "BluetoothSource")(
            config={
                "daemon_options": "--keep-alive=5",
                "bluetooth_service": "bluetooth.service",
                "stop_bluetooth_on_exit": True,
                "auto_agent": True
            },
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "radio_source": lambda: _import("backend.features.radio", "RadioSource")(
            config={"mpv_socket": "/run/milo/radio-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "podcast_source": lambda: _import("backend.features.podcast", "PodcastSource")(
            config={"mpv_socket": "/run/milo/podcast-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "airplay_source": lambda: _import("backend.features.airplay", "AirPlaySource")(
            config={"metadata_pipe": "/tmp/shairport-sync-metadata"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
    }

    if name not in creators:
        raise ValueError(f"Unknown service: {name}")

    return creators[name]()


def _create_equalizer_router():
    """Create EqualizerRouter with dependencies."""
    from backend.core.multiroom.equalizer_router import EqualizerRouter

    return EqualizerRouter(
        client_registry=get_service("client_registry_service"),
        camilladsp_service=get_service("camilladsp_service"),
        proxy_service=get_service("equalizer_client_proxy_service")
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
    websocket_manager = get_service("websocket_manager")
    equalizer_settings_sync_service = get_service("equalizer_settings_sync_service")
    equalizer_client_proxy_service = get_service("equalizer_client_proxy_service")
    multiroom_equalizer_service = get_service("multiroom_equalizer_service")

    state_machine.ws_manager = websocket_manager

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

    # (crossover_service, equalizer_settings_sync_service, equalizer_client_proxy_service
    # are wired directly to their consumers — no longer stored on state_machine)

    # 2.6 - camilladsp_service → state_machine
    camilladsp_service.set_state_machine(state_machine)

    # 2.6b - camilladsp_service → volume restore callback (re-apply volume after reconnection)
    camilladsp_service.set_on_reconnect_callback(volume_service.reapply_current_volume)

    # 2.7 - routing_service → camilladsp_service
    routing_service.set_camilladsp_service(camilladsp_service)

    # 2.8 - crossover_service → state_machine
    crossover_service.set_state_machine(state_machine)

    # 2.9 - client_registry_service → state_machine
    client_registry_service.set_state_machine(state_machine)

    # 2.10 - volume_service._state_store → client_registry_service
    volume_service._state_store.set_registry(client_registry_service)

    # 2.11 - volume_service → snapcast_websocket_service
    volume_service.set_snapcast_websocket_service(snapcast_websocket_service)

    # 2.11b - volume_service → client_registry (for EqualizerController IP lookup)
    volume_service.set_client_registry(client_registry_service)

    # 2.11c - volume_service → routing_service (for multiroom mode detection)
    volume_service.set_routing_service(routing_service)

    # 2.12 - crossover_service → client_registry_service
    crossover_service.set_registry(client_registry_service)

    # 2.13 - routing_service + crossover_service → volume_service (direct injection)
    routing_service.set_volume_service(volume_service)
    crossover_service.set_volume_service(volume_service)

    # 2.14 - multiroom_equalizer_service → state_machine (for event broadcasting)
    multiroom_equalizer_service.set_state_machine(state_machine)

    # 2.15 - multiroom_equalizer_service → proxy_service + routing_service (for remote client control)
    multiroom_equalizer_service.set_proxy_service(equalizer_client_proxy_service)
    multiroom_equalizer_service.set_routing_service(routing_service)

    # 2.16 - multiroom_equalizer_service → equalizer_router (for targeted filter updates)
    multiroom_equalizer_service.set_equalizer_router(get_service("equalizer_router"))

    # 2.17 - snapcast_websocket_service → direct service references
    snapcast_websocket_service.set_registry(client_registry_service)
    snapcast_websocket_service.set_snapcast_service(get_service("snapcast_service"))
    snapcast_websocket_service.set_volume_service(volume_service)
    snapcast_websocket_service.set_crossover_service(crossover_service)
    snapcast_websocket_service.set_equalizer_client_proxy_service(equalizer_client_proxy_service)
    snapcast_websocket_service.set_equalizer_settings_sync_service(equalizer_settings_sync_service)
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
