# backend/config/container.py
"""
Conteneur d'injection de dépendances - Version unifiée avec support systemd et routage audio
"""
from dependency_injector import containers, providers
from backend.application.event_bus import EventBus
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.roc import RocPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.services.audio_routing_service import AudioRoutingService

from backend.domain.audio_state import AudioSource


class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour oakOS"""
    
    config = providers.Configuration()
    
    # Services centraux
    event_bus = providers.Singleton(EventBus)
    
    # Gestionnaire de services systemd
    systemd_manager = providers.Singleton(SystemdServiceManager)
    
    # Service de routage audio
    audio_routing_service = providers.Singleton(AudioRoutingService)
    
    # Machine à états unifiée
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        event_bus=event_bus
    )
    
    # Plugins audio
    # GO-LIBRESPOT
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "config_path": "/var/lib/oakos/go-librespot/config.yml", 
            "service_name": "oakos-go-librespot.service" 
        })
    )
    
    # ROC-TOOLKIT
    roc_plugin = providers.Singleton(
        RocPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "service_name": "oakos-roc.service",
            "rtp_port": 10001,
            "rs8m_port": 10002,
            "rtcp_port": 10003,
            "audio_output": "hw:1,0"
        })
    )
    
    # BLUETOOTH
    bluetooth_plugin = providers.Singleton(
        BluetoothPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "daemon_options": "--keep-alive=5",
            "service_name": "oakos-bluealsa.service",
            "bluetooth_service": "bluetooth.service",
            "stop_bluetooth_on_exit": True,
            "auto_agent": True
        })
    )
    
    
    # Méthode pour enregistrer les plugins et configurer les dépendances
    @providers.Callable
    def register_plugins():
        """Enregistre tous les plugins dans la machine à états"""
        # Récupération des instances via le conteneur global
        state_machine = container.audio_state_machine()
        routing_service = container.audio_routing_service()
        
        # Configuration du service de routage dans la machine à états
        state_machine.set_routing_service(routing_service)
        
        # Enregistrement des plugins
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())
        state_machine.register_plugin(AudioSource.ROC, container.roc_plugin())

        # Injecter la référence à la machine à états dans les plugins
        container.librespot_plugin().set_state_machine(state_machine)
        container.bluetooth_plugin().set_state_machine(state_machine)
        container.roc_plugin().set_state_machine(state_machine)

# Création et configuration du conteneur
container = Container()