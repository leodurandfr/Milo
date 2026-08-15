# backend/dependencies.py
"""
Service Registry for Milo - Feature-based architecture.

Replaces dependency-injector with a simple dict-based registry.
Supports lazy singleton creation and circular dependency resolution.
"""
import asyncio
import logging
import sys
from typing import Any, Dict, Optional

from backend.shared.persistence import SchemaVersionMismatch

logger = logging.getLogger(__name__)

# Service registry - singleton instances
_services: Dict[str, Any] = {}

_init_task: Optional[asyncio.Task] = None


def get_service(name: str) -> Any:
    """
    Get a service by name, creating it if needed.

    Services are created lazily on first access.
    """
    if name not in _services:
        _services[name] = _create_service(name)
    return _services[name]


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


def _create_rotary_controller():
    """Create RotaryVolumeController with GPIO pins from hardware.json."""
    hardware_service = get_service("hardware_service")
    if not hardware_service.get_rotary_enabled():
        logging.getLogger(__name__).info("Rotary encoder disabled in hardware config")
        return None
    if not hardware_service.get_volume_control():
        logging.getLogger(__name__).info("DAC mode: rotary encoder disabled (volume managed externally)")
        return None
    clk, dt, sw = hardware_service.get_rotary_pins()
    return _import("backend.hardware.rotary", "RotaryVolumeController")(
        volume_service=get_service("volume_service"),
        state_machine=get_service("audio_state_machine"),
        clk_pin=clk, dt_pin=dt, sw_pin=sw
    )


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
        "hardware_service": lambda: _import("backend.hardware.service", "HardwareService")(),
        "snapcast_service": lambda: _import("backend.core.multiroom.snapcast", "SnapcastService")(systemd_manager=get_service("systemd_manager"), host="127.0.0.1"),
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
            systemd_manager=get_service("systemd_manager"),
            snapcast_service=get_service("snapcast_service"),
            camilladsp_service=get_service("camilladsp_service")
        ),
        "snapcast_websocket_service": lambda: _import("backend.core.multiroom.websocket", "SnapcastWebSocketService")(
            state_machine=get_service("audio_state_machine"),
            routing_service=get_service("audio_routing_service"),
            settings_service=get_service("settings_service"),
            host="127.0.0.1",
            port=1780,
            snapcast_service=get_service("snapcast_service"),
            crossover_service=get_service("crossover_service"),
            equalizer_client_proxy_service=get_service("equalizer_client_proxy_service"),
            pending_clients_service=get_service("pending_clients_service")
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
        "rotary_controller": lambda: _create_rotary_controller(),
        "screen_controller": lambda: _import("backend.hardware.screen", "ScreenController")(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            hardware_service=get_service("hardware_service")
        ),
        "bt_remote_controller": lambda: _import("backend.hardware.bt_remote", "BtRemoteController")(
            volume_service=get_service("volume_service"),
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service")
        ),
        "ir_remote_controller": lambda: _import("backend.hardware.ir_remote", "IrRemoteController")(
            volume_service=get_service("volume_service"),
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            screen_controller=get_service("screen_controller")
        ),
        "fan_controller": lambda: _import("backend.hardware.fan", "FanController")(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service")
        ),
        "pending_clients_service": lambda: _import("backend.core.multiroom.pending_clients", "PendingClientsService")(
            state_machine=get_service("audio_state_machine")
        ),
        "crossover_service": lambda: _import("backend.core.multiroom.crossover", "CrossoverService")(
            settings_service=get_service("settings_service"),
            camilladsp_service=get_service("camilladsp_service"),
            state_machine=get_service("audio_state_machine"),
            volume_service=get_service("volume_service"),
            proxy_service=get_service("equalizer_client_proxy_service")
        ),
        "multiroom_equalizer_service": lambda: _import("backend.core.equalizer", "MultiroomEqualizerService")(
            client_registry_service=get_service("client_registry_service"),
            camilladsp_service=get_service("camilladsp_service"),
            proxy_service=get_service("equalizer_client_proxy_service"),
            routing_service=get_service("audio_routing_service"),
            equalizer_router=get_service("equalizer_router"),
            state_machine=get_service("audio_state_machine")
        ),
        "equalizer_router": lambda: _create_equalizer_router(),
        "levels_monitor": lambda: _import("backend.core.equalizer", "LevelsMonitor")(
            state_machine=get_service("audio_state_machine"),
            equalizer_router=get_service("equalizer_router"),
            camilladsp_service=get_service("camilladsp_service")
        ),

        # Network service (Ethernet + WiFi)
        "network_service": lambda: _import("backend.core.network.service", "NetworkService")(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service")
        ),
        "wifi_adoption_service": lambda: _import("backend.core.multiroom.wifi_adoption", "WifiAdoptionService")(
            network_service=get_service("network_service")
        ),

        # System utilities
        "hostname_conflict_service": lambda: _import("backend.core.system", "HostnameConflictService")(systemd_manager=get_service("systemd_manager")),
        "connectivity_service": lambda: _import("backend.core.connectivity", "ConnectivityService")(),

        # Lyrics (transverse Lyrics app — LRCLIB lookup + disk cache; no source, no boot init)
        "lyrics_service": lambda: _import("backend.core.lyrics", "LyricsService")(),

        # Update services
        "update_service": lambda: _import("backend.core.updates", "UpdateService")(systemd_manager=get_service("systemd_manager")),
        "satellite_update_service": lambda: _import("backend.core.updates", "SatelliteUpdateService")(
            snapcast_service=get_service("snapcast_service"),
            client_registry_service=get_service("client_registry_service")
        ),

        # Audio sources
        "spotify_source": lambda: _import("backend.sources.spotify", "SpotifySource")(
            config={"config_path": "/var/lib/milo/go-librespot/config.yml"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "mac_source": lambda: _import("backend.sources.mac", "MacSource")(
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
        "bluetooth_source": lambda: _import("backend.sources.bluetooth", "BluetoothSource")(
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
        "radio_source": lambda: _import("backend.sources.radio", "RadioSource")(
            config={"mpv_socket": "/run/milo/radio-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "podcast_source": lambda: _import("backend.sources.podcast", "PodcastSource")(
            config={"mpv_socket": "/run/milo/podcast-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "airplay_source": lambda: _import("backend.sources.airplay", "AirPlaySource")(
            config={"metadata_pipe": "/tmp/shairport-sync-metadata"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "cd_source": lambda: _import("backend.sources.cd", "CdSource")(
            config={"mpv_socket": "/run/milo/cd-ipc.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "dlna_source": lambda: _import("backend.sources.dlna", "DlnaSource")(
            config={"port": 49494},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "qobuz_source": lambda: _import("backend.sources.qobuz", "QobuzSource")(
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "tidal_source": lambda: _import("backend.sources.tidal", "TidalSource")(
            config={"socket_path": "/run/milo/tidal-controller.sock"},
            state_machine=get_service("audio_state_machine"),
            settings_service=get_service("settings_service"),
            systemd_manager=get_service("systemd_manager")
        ),
        "music_library_source": lambda: _import("backend.sources.music_library", "MusicLibrarySource")(
            config={"mpv_socket": "/run/milo/music_library-ipc.sock"},
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
    Initialize services after creation.

    STEPS:
    1. Retrieve instances (triggers lazy creation)
    2. Resolve circular dependencies via setters
    3. Register sources in state machine
    4. Start parallel async initialization

    The steps run in this order, but within STEP 2 only one thing is actually
    order-sensitive: the registry-subscription block at the end (subscribers are
    notified in subscription order). Every other STEP 2 line is a plain
    assignment and commutes. The cross-step constraints are STEP 3 before the
    async init, and STEP 3b writing the env files before any source unit starts
    — both stated where they apply.
    """
    from backend.core.models.audio_state import AudioSource

    # =========================================================================
    # STEP 1: Retrieve instances (triggers lazy creation)
    # =========================================================================
    settings_service = get_service("settings_service")
    hardware_service = get_service("hardware_service")
    state_machine = get_service("audio_state_machine")
    routing_service = get_service("audio_routing_service")
    volume_service = get_service("volume_service")
    rotary_controller = get_service("rotary_controller")
    screen_controller = get_service("screen_controller")
    bt_remote_controller = get_service("bt_remote_controller")
    ir_remote_controller = get_service("ir_remote_controller")
    fan_controller = get_service("fan_controller")
    snapcast_websocket_service = get_service("snapcast_websocket_service")
    camilladsp_service = get_service("camilladsp_service")
    crossover_service = get_service("crossover_service")
    client_registry_service = get_service("client_registry_service")
    websocket_manager = get_service("websocket_manager")
    pending_clients_service = get_service("pending_clients_service")
    # Created at boot for API access; all deps are constructor-injected, so no
    # STEP 2 wiring and no local handle is needed.
    get_service("multiroom_equalizer_service")
    hostname_conflict_service = get_service("hostname_conflict_service")
    connectivity_service = get_service("connectivity_service")
    network_service = get_service("network_service")

    state_machine.ws_manager = websocket_manager
    hostname_conflict_service.set_state_machine(state_machine)
    # Cycle: state_machine ↔ connectivity_service
    #   state_machine reads the NM level to derive full_state.network_unavailable;
    #   connectivity broadcasts its own event through the state machine.
    connectivity_service.set_state_machine(state_machine)
    state_machine.connectivity_service = connectivity_service

    # =========================================================================
    # STEP 2: Wire the dependencies that CANNOT be constructor-injected.
    # =========================================================================
    # Acyclic deps are injected in _create_service (lazy get_service() guarantees
    # creation order). What remains here breaks a genuine A↔B cycle, or enforces a
    # subscription ordering — each block says which. Adding a new acyclic dep goes
    # in the factory, NOT here.

    # Cycle: routing_service ↔ state_machine
    #   routing resolves sources via state_machine.get_source() and broadcasts
    #   through it; state_machine reads back routing.multiroom_enabled — for
    #   full_state aggregation only, never for a transition decision.
    routing_service.set_source_callback(lambda source: state_machine.get_source(source))
    routing_service.set_state_machine(state_machine)
    state_machine.routing_service = routing_service

    # Cycle: routing_service ↔ snapcast_websocket_service
    #   snapcast_ws is constructed with routing_service; routing needs it back to
    #   start/stop the control WS on a multiroom toggle.
    routing_service.set_snapcast_websocket_service(snapcast_websocket_service)

    # Cycle: state_machine ↔ camilladsp_service
    #   state_machine reads effects_enabled when aggregating full_state for
    #   source/system broadcasts; camilladsp needs state_machine to broadcast.
    state_machine.camilladsp_service = camilladsp_service
    camilladsp_service.set_state_machine(state_machine)

    # Cycle: camilladsp_service ↔ volume_service
    #   volume is constructed with camilladsp; camilladsp calls back to re-apply
    #   the current volume after a reconnection.
    camilladsp_service.set_on_reconnect_callback(volume_service.reapply_current_volume)

    # Cycle: volume_service ↔ snapcast_websocket_service
    volume_service.set_snapcast_websocket_service(snapcast_websocket_service)
    snapcast_websocket_service.set_volume_service(volume_service)

    # Cycle: volume_service ↔ routing_service
    #   volume reads routing for multiroom-mode detection; routing pushes volume
    #   on mode changes.
    volume_service.set_routing_service(routing_service)
    routing_service.set_volume_service(volume_service)

    # Fail loud on a missing full_state back-reference. get_current_state() reads
    # multiroom_enabled / equalizer_effects_enabled / network_unavailable through
    # these three and falls back to the benign value when one is unset —
    # indistinguishable on the wire from "multiroom off, effects off, network
    # fine", so a wiring regression would ship as a silent UI lie rather than a
    # crash.
    for attr in ("routing_service", "camilladsp_service", "connectivity_service"):
        if getattr(state_machine, attr) is None:
            raise RuntimeError(
                f"state_machine.{attr} not wired — full_state would report its "
                f"global flag as False for every client."
            )

    # Ordered registry subscriptions (NOT cycles — ClientRegistryService holds no
    # back-reference; the constraint is subscription order, not construction).
    # The volume state store MUST subscribe before the snapcast WS broadcaster,
    # so volume state is current when a registry event fires a multiroom broadcast.
    volume_service.attach_registry(client_registry_service)
    crossover_service.set_registry(client_registry_service)
    snapcast_websocket_service.set_registry(client_registry_service)

    # =========================================================================
    # STEP 3: Register sources (MUST be done BEFORE init_async)
    # =========================================================================
    state_machine.register_source(AudioSource.SPOTIFY, get_service("spotify_source"))
    state_machine.register_source(AudioSource.BLUETOOTH, get_service("bluetooth_source"))
    state_machine.register_source(AudioSource.MAC, get_service("mac_source"))
    state_machine.register_source(AudioSource.RADIO, get_service("radio_source"))
    state_machine.register_source(AudioSource.PODCAST, get_service("podcast_source"))
    state_machine.register_source(AudioSource.AIRPLAY, get_service("airplay_source"))
    state_machine.register_source(AudioSource.CD, get_service("cd_source"))
    state_machine.register_source(AudioSource.DLNA, get_service("dlna_source"))
    state_machine.register_source(AudioSource.QOBUZ, get_service("qobuz_source"))
    state_machine.register_source(AudioSource.TIDAL, get_service("tidal_source"))
    state_machine.register_source(AudioSource.MUSIC_LIBRARY, get_service("music_library_source"))

    # =========================================================================
    # STEP 3b: Write routing.env / mac.env / snapclient.env synchronously BEFORE async init
    # =========================================================================
    # Audio source systemd services read their EnvironmentFiles at start.
    # If they start before routing_service.initialize() completes (which runs
    # in async init_async), they would read stale values. Pre-writing here
    # guarantees the three env files match settings.json before any service starts.
    routing_service.regenerate_env_files()

    # =========================================================================
    # STEP 4: Parallel async initialization
    # =========================================================================
    global _init_task

    # Get sources that need early init
    radio_source = get_service("radio_source")
    cd_source = get_service("cd_source")
    podcast_source = get_service("podcast_source")
    music_library_source = get_service("music_library_source")

    async def init_async():
        """Async initialization with error handling."""
        services = [
            # SettingsService + HardwareService come first in this list, but
            # that buys no ordering: gather() below starts every entry at once,
            # so routing_service.initialize() reads settings concurrently with
            # the schema check — and STEP 3b already consumed settings.json
            # synchronously, before any of this. A schema mismatch therefore
            # surfaces *after* a stale shape has been read. Closing that hole
            # means giving get_setting_sync and _read_locked the same version
            # check load_settings performs — not reordering this list, which
            # cannot fix it.
            ("settings_service", settings_service.initialize()),
            ("hardware_service", hardware_service.initialize()),
            ("client_registry_service", client_registry_service.initialize()),
            ("routing_service", routing_service.initialize()),
            ("volume_service", volume_service.initialize()),
            *([("rotary_controller", rotary_controller.initialize())] if rotary_controller else []),
            ("screen_controller", screen_controller.initialize()),
            ("bt_remote_controller", bt_remote_controller.initialize()),
            ("ir_remote_controller", ir_remote_controller.initialize()),
            ("fan_controller", fan_controller.initialize()),
            ("snapcast_websocket_service", snapcast_websocket_service.initialize()),
            ("camilladsp_service", camilladsp_service.initialize()),
            ("crossover_service", crossover_service.initialize()),
            ("pending_clients_service", pending_clients_service.initialize()),
            # Radio station data needs early init for API access
            ("radio_source", radio_source.initialize()),
            # CD disc watcher needs early init for auto-detection
            ("cd_source", cd_source.initialize()),
            # Podcast persistence schema check (fail-loud on schema_version drift)
            ("podcast_source", podcast_source.initialize()),
            # USB storage watcher (pyudev) — mounts a plugged-in key under
            # /media/milo + triggers a Navidrome rescan, independent of playback.
            ("music_library_source", music_library_source.initialize()),
            # mDNS hostname conflict detection (fail-open, never raises)
            ("hostname_conflict_service", hostname_conflict_service.check()),
            # Internet connectivity monitoring (D-Bus subscription, fail-open)
            ("connectivity_service", connectivity_service.initialize()),
            # Network status live updates (Ethernet + WiFi, NM D-Bus, fail-open)
            ("network_service", network_service.initialize())
        ]

        results = await asyncio.gather(
            *[coro for _, coro in services],
            return_exceptions=True
        )

        # Fail-loud on persisted-data schema mismatch: log the banner, flush stderr
        # so the journal captures it, then SystemExit(1) to trigger a systemd-restart
        # loop with a stable error until the operator deletes the offending file.
        for (service_name, _), result in zip(services, results):
            if isinstance(result, SchemaVersionMismatch):
                logger.error(
                    "Schema version mismatch during %s init — bailing.\n%s",
                    service_name,
                    result,
                )
                sys.stderr.flush()
                raise SystemExit(1)

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

        # Start periodic hostname conflict re-check after the boot check completed
        hostname_conflict_service.start_periodic()

    _init_task = asyncio.create_task(init_async())
