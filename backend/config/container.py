# backend/config/container.py - Ajout de la configuration auto-disconnect
"""
Conteneur d'injection de dépendances - Version avec configuration auto-disconnect
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
from backend.infrastructure.hardware.rotary_volume_controller import RotaryVolumeController
from backend.infrastructure.hardware.screen_controller import ScreenController
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource

class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour Milo - Version avec auto-disconnect configurable"""
    
    config = providers.Configuration()
    
    # Services centraux
    systemd_manager = providers.Singleton(SystemdServiceManager)
    snapcast_service = providers.Singleton(SnapcastService)
    equalizer_service = providers.Singleton(EqualizerService)
    
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
    
    # Service de routage audio
    audio_routing_service = providers.Singleton(AudioRoutingService)
    
    # Service WebSocket Snapcast
    snapcast_websocket_service = providers.Singleton(
        SnapcastWebSocketService,
        state_machine=audio_state_machine,
        routing_service=audio_routing_service,
        host="localhost",
        port=1780
    )
    
    # Service Volume
    volume_service = providers.Singleton(
        VolumeService,
        state_machine=audio_state_machine,
        snapcast_service=snapcast_service
    )
    
    # Contrôleurs hardware
    rotary_controller = providers.Singleton(
        RotaryVolumeController,
        volume_service=volume_service,
        clk_pin=22,
        dt_pin=27,
        sw_pin=23
    )
    
    screen_controller = providers.Singleton(
        ScreenController,
        state_machine=audio_state_machine
    )
    
    # Plugins audio avec configuration auto-disconnect
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        config=providers.Dict({
            "config_path": "/var/lib/milo/go-librespot/config.yml", 
            "service_name": "milo-go-librespot.service",
            # Configuration de la déconnexion automatique
            "auto_disconnect_on_pause": True,  # True = activé par défaut, False = désactivé
            "pause_disconnect_delay": 10.0     # Délai en secondes (configurable)
        }),
        state_machine=audio_state_machine
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

        # Résoudre la référence circulaire state_machine ↔ routing_service
        state_machine.routing_service = routing_service
        
        # Enregistrement des plugins dans la machine à états
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.ROC, container.roc_plugin())
        
        # Initialisation asynchrone simplifiée avec gestion d'erreurs
        import asyncio
        
        async def init_async():
            """Initialisation asynchrone avec gestion d'erreurs individuelles"""
            services = [
                ("routing_service", routing_service.initialize()),
                ("volume_service", volume_service.initialize()),
                ("rotary_controller", rotary_controller.initialize()),
                ("screen_controller", screen_controller.initialize()),
                ("snapcast_websocket_service", snapcast_websocket_service.initialize())
            ]
            
            for service_name, init_coroutine in services:
                try:
                    await init_coroutine
                    print(f"✅ {service_name} initialized successfully")
                except Exception as e:
                    print(f"❌ {service_name} initialization failed: {e}")
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(init_async())
            else:
                loop.run_until_complete(init_async())
        except RuntimeError:
            asyncio.create_task(init_async())

# Création et configuration du conteneur
container = Container()