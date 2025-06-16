# backend/config/container.py
"""
Conteneur d'injection de dépendances - Version OPTIM sans références circulaires
"""
from dependency_injector import containers, providers
from backend.application.event_bus import EventBus
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.roc import RocPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.infrastructure.services.snapcast_service import SnapcastService
from backend.domain.audio_state import AudioSource

class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour oakOS - Version sans cross-references"""
    
    config = providers.Configuration()
    
    # Services centraux
    event_bus = providers.Singleton(EventBus)
    systemd_manager = providers.Singleton(SystemdServiceManager)
    snapcast_service = providers.Singleton(SnapcastService)
    
    # Service de routage audio (sans référence à state_machine)
    audio_routing_service = providers.Singleton(AudioRoutingService)
    
    # Machine à états unifiée (avec injection du routing_service)
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        event_bus=event_bus,
        routing_service=audio_routing_service
    )
    
    # Plugins audio (avec injection de state_machine)
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "config_path": "/var/lib/oakos/go-librespot/config.yml", 
            "service_name": "oakos-go-librespot.service" 
        }),
        state_machine=audio_state_machine
    )
    
    roc_plugin = providers.Singleton(
        RocPlugin,
        event_bus=event_bus,
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
        event_bus=event_bus,
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
        """Initialise les services après création - Version OPTIM"""
        # Récupération des instances
        state_machine = container.audio_state_machine()
        routing_service = container.audio_routing_service()
        
        # Configuration du callback pour que routing_service puisse accéder aux plugins
        routing_service.set_plugin_callback(lambda source: state_machine.get_plugin(source))
        
        # Enregistrement des plugins dans la machine à états
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.ROC, container.roc_plugin())
        
        # Initialisation asynchrone du routing_service
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Si boucle en cours, programmer l'initialisation
                asyncio.create_task(routing_service.initialize())
            else:
                # Sinon, initialiser directement
                loop.run_until_complete(routing_service.initialize())
        except RuntimeError:
            # Pas de boucle active, créer une tâche
            asyncio.create_task(routing_service.initialize())

# Création et configuration du conteneur
container = Container()