# backend/config/container.py
"""
Dependency injection container with SettingsService injection
"""
import json
import os
from dependency_injector import containers, providers
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.spotify import SpotifyPlugin
from backend.infrastructure.plugins.mac import MacPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.plugins.radio import RadioPlugin
from backend.infrastructure.plugins.podcast import PodcastPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.infrastructure.services.snapcast_service import SnapcastService
from backend.infrastructure.services.snapcast_websocket_service import SnapcastWebSocketService
from backend.infrastructure.services.camilladsp_service import CamillaDSPService
from backend.infrastructure.services.volume_service import VolumeService
from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.hardware_service import HardwareService
from backend.infrastructure.hardware.rotary_volume_controller import RotaryVolumeController
from backend.infrastructure.hardware.screen_controller import ScreenController
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.program_version_service import ProgramVersionService
from backend.infrastructure.services.program_update_service import ProgramUpdateService
from backend.infrastructure.services.satellite_program_update_service import SatelliteProgramUpdateService
from backend.infrastructure.services.crossover_service import CrossoverService
from backend.infrastructure.services.dsp_client_proxy_service import DspClientProxyService
from backend.infrastructure.services.dsp_settings_sync_service import DspSettingsSyncService

class Container(containers.DeclarativeContainer):
    """Dependency injection container for Milo with SettingsService injection"""

    config = providers.Configuration()

    # Core services
    systemd_manager = providers.Singleton(SystemdServiceManager)
    snapcast_service = providers.Singleton(SnapcastService)
    settings_service = providers.Singleton(SettingsService)
    hardware_service = providers.Singleton(HardwareService)

    # CamillaDSP service (DSP engine for EQ, compression, etc.)
    camilladsp_service = providers.Singleton(
        CamillaDSPService,
        settings_service=settings_service
    )
    
    # WebSocket
    websocket_manager = providers.Singleton(WebSocketManager)
    websocket_event_handler = providers.Singleton(
        WebSocketEventHandler,
        ws_manager=websocket_manager
    )
    
    # Unified state machine
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        routing_service=providers.Self,
        websocket_handler=websocket_event_handler
    )

    # Audio routing service with SettingsService
    audio_routing_service = providers.Singleton(
        AudioRoutingService,
        settings_service=settings_service
    )

    # Snapcast WebSocket service
    snapcast_websocket_service = providers.Singleton(
        SnapcastWebSocketService,
        state_machine=audio_state_machine,
        routing_service=audio_routing_service,
        host="localhost",
        port=1780
    )

    # Volume service with SettingsService and CamillaDSP injection
    volume_service = providers.Singleton(
        VolumeService,
        state_machine=audio_state_machine,
        snapcast_service=snapcast_service,
        settings_service=settings_service,
        camilladsp_service=camilladsp_service
    )

    # Hardware controllers with SettingsService
    rotary_controller = providers.Singleton(
        RotaryVolumeController,
        volume_service=volume_service,
        clk_pin=22,
        dt_pin=27,
        sw_pin=23
    )

    # ScreenController with SettingsService and HardwareService injection
    screen_controller = providers.Singleton(
        ScreenController,
        state_machine=audio_state_machine,
        settings_service=settings_service,
        hardware_service=hardware_service
    )

    # Program management services (Singletons to avoid multiple instantiations)
    program_version_service = providers.Singleton(ProgramVersionService)
    program_update_service = providers.Singleton(ProgramUpdateService)
    satellite_program_update_service = providers.Singleton(
        SatelliteProgramUpdateService,
        snapcast_service=snapcast_service
    )

    # Crossover service for subwoofer integration
    crossover_service = providers.Singleton(
        CrossoverService,
        settings_service=settings_service,
        dsp_service=camilladsp_service
    )

    # DSP client proxy service for multiroom DSP communication
    dsp_client_proxy_service = providers.Singleton(
        DspClientProxyService,
        routing_service=audio_routing_service
    )

    # DSP settings sync service for persisting and syncing client settings
    dsp_settings_sync_service = providers.Singleton(
        DspSettingsSyncService,
        proxy_service=dsp_client_proxy_service,
        dsp_service=camilladsp_service
    )

    # Audio plugins with SettingsService instead of static config
    spotify_plugin = providers.Singleton(
        SpotifyPlugin,
        config=providers.Dict({
            "config_path": "/var/lib/milo/go-librespot/config.yml",
            "service_name": "milo-spotify.service"
        }),
        state_machine=audio_state_machine,
        settings_service=settings_service
    )

    mac_plugin = providers.Singleton(
        MacPlugin,
        config=providers.Dict({
            "service_name": "milo-mac.service",
            "rtp_port": 10001,
            "rs8m_port": 10002,
            "rtcp_port": 10003,
            "audio_output": "hw:1,0"
        }),
        state_machine=audio_state_machine
    )
    
    bluetooth_plugin = providers.Singleton(
        BluetoothPlugin,
        config=providers.Dict({
            "daemon_options": "--keep-alive=5",
            "service_name": "milo-bluealsa.service",
            "bluetooth_service": "bluetooth.service",
            "stop_bluetooth_on_exit": True,
            "auto_agent": True
        }),
        state_machine=audio_state_machine
    )

    radio_plugin = providers.Singleton(
        RadioPlugin,
        config=providers.Dict({
            "service_name": "milo-radio.service",
            "ipc_socket": "/run/milo/radio-ipc.sock"
        }),
        state_machine=audio_state_machine,
        settings_service=settings_service
    )

    # Load Taddy API credentials from settings.json
    def _load_taddy_credentials():
        default_creds = {
            "taddy_user_id": "",
            "taddy_api_key": ""
        }

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

        return default_creds

    _taddy_creds = _load_taddy_credentials()

    podcast_plugin = providers.Singleton(
        PodcastPlugin,
        config=providers.Dict({
            "service_name": "milo-podcast.service",
            "ipc_socket": "/run/milo/podcast-ipc.sock",
            "taddy_user_id": _taddy_creds["taddy_user_id"],
            "taddy_api_key": _taddy_creds["taddy_api_key"]
        }),
        state_machine=audio_state_machine,
        settings_service=settings_service
    )

    # Post-creation configuration
    @providers.Callable
    def initialize_services():
        """
        Initialize services after creation - CRITICAL ORDER

        WARNING: The execution order of this function is CRITICAL.
        Do not modify without understanding circular dependencies.

        INITIALIZATION ORDER:

        1. Retrieve instances from container
           - Instances are created by dependency-injector in Singleton mode
           - At this stage, circular dependencies are NOT resolved

        2. Manual resolution of circular dependencies
           - routing_service ↔ state_machine (mutual need)
           - routing_service ↔ snapcast_websocket_service (mutual need)
           - state_machine → plugins (via callback)

        3. Register plugins in state machine
           - MUST be done BEFORE async initialization
           - Plugins must be available before routing_service.initialize() runs

        4. Parallel async initialization
           - Services initialize in parallel via asyncio.gather()
           - Critical services (routing_service, volume_service) cause failure on error
           - Non-critical services log error but don't block startup

        CIRCULAR DEPENDENCIES:

        state_machine ←→ routing_service
        - state_machine needs routing_service to sync multiroom/DSP state
        - routing_service needs state_machine to broadcast events and access plugins

        routing_service ←→ snapcast_websocket_service
        - routing_service controls snapcast_websocket_service start/stop
        - snapcast_websocket_service needs routing_service to know multiroom state

        MODIFYING THIS FUNCTION:
        If you need to add a new service or modify the order:
        1. Identify the new service dependencies
        2. Add it AFTER resolving its circular dependencies
        3. Add it in init_async() only if it has an async initialize() method
        4. Test complete application startup

        """
        # ============================================================
        # STEP 1: Retrieve instances (order non-critical)
        # ============================================================
        state_machine = container.audio_state_machine()
        routing_service = container.audio_routing_service()
        volume_service = container.volume_service()
        rotary_controller = container.rotary_controller()
        screen_controller = container.screen_controller()
        snapcast_websocket_service = container.snapcast_websocket_service()
        camilladsp_service = container.camilladsp_service()
        crossover_service = container.crossover_service()

        # ============================================================
        # STEP 2: Resolve circular dependencies (CRITICAL ORDER)
        # ============================================================

        # 2.1 - routing_service → state_machine.get_plugin()
        #       Allows routing_service to restart active plugins
        routing_service.set_plugin_callback(lambda source: state_machine.get_plugin(source))

        # 2.2 - routing_service ↔ snapcast_websocket_service
        #       Enables Snapcast WebSocket lifecycle control
        routing_service.set_snapcast_websocket_service(snapcast_websocket_service)

        # 2.3 - routing_service → snapcast_service
        #       Enables auto-configuration of Snapcast groups when multiroom is activated
        routing_service.set_snapcast_service(container.snapcast_service())

        # 2.4 - routing_service → state_machine
        #       Allows routing_service to broadcast routing events
        routing_service.set_state_machine(state_machine)

        # 2.5 - state_machine ← routing_service (circular reference)
        #       Allows state_machine to synchronize multiroom/DSP state
        state_machine.routing_service = routing_service

        # 2.6 - camilladsp_service → state_machine
        #       Allows DSP service to broadcast events
        camilladsp_service.set_state_machine(state_machine)

        # 2.7 - routing_service → camilladsp_service
        #       Allows routing_service to connect/disconnect CamillaDSP when DSP is enabled/disabled
        routing_service.set_camilladsp_service(camilladsp_service)

        # 2.8 - crossover_service → state_machine
        #       Allows crossover_service to broadcast events
        crossover_service.set_state_machine(state_machine)

        # ============================================================
        # STEP 3: Register plugins (MUST be done BEFORE init_async)
        # ============================================================
        state_machine.register_plugin(AudioSource.SPOTIFY, container.spotify_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.MAC, container.mac_plugin())
        state_machine.register_plugin(AudioSource.RADIO, container.radio_plugin())
        state_machine.register_plugin(AudioSource.PODCAST, container.podcast_plugin())

        # ============================================================
        # STEP 4: Parallel async initialization
        # ============================================================
        import asyncio

        async def init_async():
            """
            Async initialization with error handling and execution guarantee

            This function runs in parallel to optimize startup time.
            Critical services (routing_service, volume_service) cause total failure on error.
            Non-critical services log the error but don't block startup.
            """
            import logging
            logger = logging.getLogger(__name__)

            # List of services to initialize (order non-critical, run in parallel)
            services = [
                ("routing_service", routing_service.initialize()),
                ("volume_service", volume_service.initialize()),
                ("rotary_controller", rotary_controller.initialize()),
                ("screen_controller", screen_controller.initialize()),
                ("snapcast_websocket_service", snapcast_websocket_service.initialize()),
                ("camilladsp_service", camilladsp_service.initialize()),
                ("crossover_service", crossover_service.initialize())
            ]

            # Run all initializations in parallel with gather
            results = await asyncio.gather(
                *[coro for _, coro in services],
                return_exceptions=True
            )

            # Log results individually
            for (service_name, _), result in zip(services, results):
                if isinstance(result, Exception):
                    logger.error("%s initialization failed: %s", service_name, result)
                else:
                    logger.info("%s initialized successfully", service_name)

            # Check if critical services failed
            critical_services = ["routing_service", "volume_service"]
            for i, (service_name, _) in enumerate(services):
                if service_name in critical_services and isinstance(results[i], Exception):
                    logger.critical("Critical service %s failed to initialize", service_name)
                    raise results[i]

        # Create initialization task (will be awaited in main.py lifespan)
        container._init_task = asyncio.create_task(init_async())

# Container creation and configuration
container = Container()