# backend/config/container.py - Mise à jour avec SnapcastWebSocketService
"""
Conteneur d'injection de dépendances - Version avec SnapcastWebSocketService
"""
from dependency_injector import containers, providers
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.roc import RocPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.infrastructure.services.snapcast_service import SnapcastService
from backend.infrastructure.services.snapcast_websocket_service import SnapcastWebSocketService  # AJOUT
from backend.infrastructure.services.equalizer_service import EqualizerService
from backend.infrastructure.services.volume_service import VolumeService  
from backend.infrastructure.hardware.rotary_volume_controller import RotaryVolumeController  
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource

class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour oakOS - Version avec SnapcastWebSocketService"""
    
    config = providers.Configuration()
    
    # Services centraux
    systemd_manager = providers.Singleton(SystemdServiceManager)
    snapcast_service = providers.Singleton(SnapcastService)
    equalizer_service = providers.Singleton(EqualizerService)
    
    # WebSocket (créé ici pour injection)
    websocket_manager = providers.Singleton(WebSocketManager)
    websocket_event_handler = providers.Singleton(
        WebSocketEventHandler,
        ws_manager=websocket_manager
    )
    
    # Service de routage audio (sans référence à state_machine)
    audio_routing_service = providers.Singleton(AudioRoutingService)
    
    # Machine à états unifiée (avec injection du routing_service ET websocket_handler)
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        routing_service=audio_routing_service,
        websocket_handler=websocket_event_handler
    )
    
    # AJOUT : Service WebSocket Snapcast pour notifications temps réel
    snapcast_websocket_service = providers.Singleton(
        SnapcastWebSocketService,
        state_machine=audio_state_machine,
        routing_service=audio_routing_service,  # AJOUT : injection routing_service
        host="localhost",
        port=1780
    )
    
    # Service Volume (avec injection de state_machine pour WebSocket)
    volume_service = providers.Singleton(
        VolumeService,
        state_machine=audio_state_machine,
        snapcast_service=snapcast_service
    )
    
    # Contrôleur rotary hardware (avec injection de volume_service)
    rotary_controller = providers.Singleton(
        RotaryVolumeController,
        volume_service=volume_service,
        clk_pin=22,  # Pin CLK
        dt_pin=27,   # Pin DT
        sw_pin=23    # Pin SW (bouton)
    )
    
    # Plugins audio 
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        config=providers.Dict({
            "config_path": "/var/lib/oakos/go-librespot/config.yml", 
            "service_name": "oakos-go-librespot.service" 
        }),
        state_machine=audio_state_machine
    )
    
    roc_plugin = providers.Singleton(
        RocPlugin,
        config=providers.Dict({
            "service_name": "oakos-roc.service",
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
            "service_name": "oakos-bluealsa.service",
            "bluetooth_service": "bluetooth.service",
            "stop_bluetooth_on_exit": True,
            "auto_agent": True
        }),
        state_machine=audio_state_machine
    )
    
    # Configuration post-création
    @providers.Callable
    def initialize_services():
        """Initialise les services après création - Version avec SnapcastWebSocketService"""
        # Récupération des instances
        state_machine = container.audio_state_machine()
        routing_service = container.audio_routing_service()
        volume_service = container.volume_service()
        rotary_controller = container.rotary_controller()
        snapcast_websocket_service = container.snapcast_websocket_service()  # AJOUT
        
        # Configuration du callback pour que routing_service puisse accéder aux plugins
        routing_service.set_plugin_callback(lambda source: state_machine.get_plugin(source))
        
        # AJOUT : Configuration cross-référence routing_service ↔ snapcast_websocket_service
        routing_service.set_snapcast_websocket_service(snapcast_websocket_service)
        
        # Enregistrement des plugins dans la machine à états
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.ROC, container.roc_plugin())
        
        # Initialisation asynchrone des services
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Services audio
                asyncio.create_task(routing_service.initialize())
                # Services volume
                asyncio.create_task(volume_service.initialize())
                asyncio.create_task(rotary_controller.initialize())
                # AJOUT : Service WebSocket Snapcast
                asyncio.create_task(snapcast_websocket_service.initialize())
            else:
                # Services audio
                loop.run_until_complete(routing_service.initialize())
                # Services volume
                loop.run_until_complete(volume_service.initialize())
                loop.run_until_complete(rotary_controller.initialize())
                # AJOUT : Service WebSocket Snapcast
                loop.run_until_complete(snapcast_websocket_service.initialize())
        except RuntimeError:
            # Services audio
            asyncio.create_task(routing_service.initialize())
            # Services volume
            asyncio.create_task(volume_service.initialize())
            asyncio.create_task(rotary_controller.initialize())
            # AJOUT : Service WebSocket Snapcast
            asyncio.create_task(snapcast_websocket_service.initialize())

# Création et configuration du conteneur
container = Container()