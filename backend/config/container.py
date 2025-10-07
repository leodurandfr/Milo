# backend/config/container.py - Injection SettingsService dans LibrespotPlugin et ScreenController
"""
Conteneur d'injection de dépendances - Version avec SettingsService injecté
"""
from dependency_injector import containers, providers
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.roc import RocPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.infrastructure.services.snapcast_service import SnapcastService
from backend.infrastructure.services.snapcast_websocket_service import SnapcastWebSocketService
from backend.infrastructure.services.equalizer_service import EqualizerService
from backend.infrastructure.services.volume_service import VolumeService
from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.hardware.rotary_volume_controller import RotaryVolumeController
from backend.infrastructure.hardware.screen_controller import ScreenController
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource

class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour Milo - Version avec SettingsService injecté"""
    
    config = providers.Configuration()
    
    # Services centraux
    systemd_manager = providers.Singleton(SystemdServiceManager)
    snapcast_service = providers.Singleton(SnapcastService)
    equalizer_service = providers.Singleton(EqualizerService)
    settings_service = providers.Singleton(SettingsService)
    
    # WebSocket
    websocket_manager = providers.Singleton(WebSocketManager)
    websocket_event_handler = providers.Singleton(
        WebSocketEventHandler,
        ws_manager=websocket_manager
    )
    
    # Machine à états unifiée
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        routing_service=providers.Self,
        websocket_handler=websocket_event_handler
    )
    
    # Service de routage audio avec SettingsService
    audio_routing_service = providers.Singleton(
        AudioRoutingService,
        settings_service=settings_service
    )
    
    # Service WebSocket Snapcast
    snapcast_websocket_service = providers.Singleton(
        SnapcastWebSocketService,
        state_machine=audio_state_machine,
        routing_service=audio_routing_service,
        host="localhost",
        port=1780
    )
    
    # Service Volume avec SettingsService
    volume_service = providers.Singleton(
        VolumeService,
        state_machine=audio_state_machine,
        snapcast_service=snapcast_service
    )
    
    # Contrôleurs hardware avec SettingsService
    rotary_controller = providers.Singleton(
        RotaryVolumeController,
        volume_service=volume_service,
        clk_pin=22,
        dt_pin=27,
        sw_pin=23
    )
    
    # MODIFIÉ : Injection SettingsService dans ScreenController
    screen_controller = providers.Singleton(
        ScreenController,
        state_machine=audio_state_machine,
        settings_service=settings_service
    )
    
    # MODIFIÉ : Plugins audio avec SettingsService au lieu de config statique
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        config=providers.Dict({
            "config_path": "/var/lib/milo/go-librespot/config.yml", 
            "service_name": "milo-go-librespot.service"
        }),
        state_machine=audio_state_machine,
        settings_service=settings_service
    )
    
    roc_plugin = providers.Singleton(
        RocPlugin,
        config=providers.Dict({
            "service_name": "milo-roc.service",
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
    
    # Configuration post-création
    @providers.Callable
    def initialize_services():
        """Initialise les services après création"""
        # Récupération des instances
        state_machine = container.audio_state_machine()
        routing_service = container.audio_routing_service()
        volume_service = container.volume_service()
        rotary_controller = container.rotary_controller()
        screen_controller = container.screen_controller()
        snapcast_websocket_service = container.snapcast_websocket_service()
        
        # Configuration du callback pour que routing_service puisse accéder aux plugins
        routing_service.set_plugin_callback(lambda source: state_machine.get_plugin(source))
        
        # Configuration cross-référence routing_service ↔ snapcast_websocket_service
        routing_service.set_snapcast_websocket_service(snapcast_websocket_service)
        
        # Configuration snapcast_service pour auto-configuration sur "Multiroom"
        routing_service.set_snapcast_service(container.snapcast_service())

        # Configuration state_machine pour routing_service
        routing_service.set_state_machine(state_machine)

        # Résoudre la référence circulaire state_machine ↔ routing_service
        state_machine.routing_service = routing_service
        
        # Enregistrement des plugins dans la machine à états
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.ROC, container.roc_plugin())
        
        # Initialisation asynchrone avec attente garantie
        import asyncio

        async def init_async():
            """Initialisation asynchrone avec gestion d'erreurs et garantie d'exécution"""
            import logging
            logger = logging.getLogger(__name__)

            services = [
                ("routing_service", routing_service.initialize()),
                ("volume_service", volume_service.initialize()),
                ("rotary_controller", rotary_controller.initialize()),
                ("screen_controller", screen_controller.initialize()),
                ("snapcast_websocket_service", snapcast_websocket_service.initialize())
            ]

            # Exécuter toutes les initialisations en parallèle avec gather
            results = await asyncio.gather(
                *[coro for _, coro in services],
                return_exceptions=True
            )

            # Logger les résultats individuellement
            for (service_name, _), result in zip(services, results):
                if isinstance(result, Exception):
                    logger.error("%s initialization failed: %s", service_name, result)
                else:
                    logger.info("%s initialized successfully", service_name)

            # Vérifier si des services critiques ont échoué
            critical_services = ["routing_service", "volume_service"]
            for i, (service_name, _) in enumerate(services):
                if service_name in critical_services and isinstance(results[i], Exception):
                    logger.critical("Critical service %s failed to initialize", service_name)
                    raise results[i]

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Dans le contexte FastAPI, créer une task et l'attendre
                task = asyncio.create_task(init_async())
                # Stocker la task pour pouvoir l'attendre au démarrage
                container._init_task = task
            else:
                loop.run_until_complete(init_async())
        except RuntimeError:
            # Fallback : créer une task sans l'attendre (à éviter)
            import logging
            logging.getLogger(__name__).warning(
                "Could not guarantee initialization completion - running in background"
            )
            asyncio.create_task(init_async())

# Création et configuration du conteneur
container = Container()