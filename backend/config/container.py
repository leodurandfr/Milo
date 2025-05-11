# backend/config/container.py
"""
Conteneur d'injection de dépendances - Version unifiée avec support systemd
"""
from dependency_injector import containers, providers
from backend.application.event_bus import EventBus
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.snapclient import SnapclientPlugin
from backend.infrastructure.plugins.bluetooth import BluetoothPlugin
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

from backend.domain.audio_state import AudioSource


class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour oakOS"""
    
    config = providers.Configuration()
    
    # Services centraux
    event_bus = providers.Singleton(EventBus)
    
    # Gestionnaire de services systemd
    systemd_manager = providers.Singleton(SystemdServiceManager)
    
    # Machine à états unifiée
    audio_state_machine = providers.Singleton(
        UnifiedAudioStateMachine,
        event_bus=event_bus
    )
    
    # Plugins audio
    librespot_plugin = providers.Singleton(
        LibrespotPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "config_path": "~/.config/go-librespot/config.yml",
            "executable_path": "~/oakOS/go-librespot/go-librespot"
        })
    )
    
    snapclient_plugin = providers.Singleton(
        SnapclientPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "service_name": "snapclient.service",  # Nom du service systemd
            "auto_discover": True, 
            "auto_connect": True
        })
    )
    
    # VERSION SYSTEMD
    bluetooth_plugin = providers.Singleton(
        BluetoothPlugin,
        event_bus=event_bus,
        config=providers.Dict({
            "daemon_options": "--keep-alive=5",
            "service_name": "bluealsa.service",
            "bluetooth_service": "bluetooth.service",
            "stop_bluetooth_on_exit": True,
            "auto_agent": True
        })
    )
    
    # VERSION LEGACY
    # bluetooth_plugin = providers.Singleton(
    #     BluetoothPlugin,
    #     event_bus=event_bus,
    #     config=providers.Dict({
    #         "daemon_options": "--keep-alive=5 --initial-volume=80"
    #     })
    # )
    
    
    # Méthode pour enregistrer les plugins
    @providers.Callable
    def register_plugins():
        """Enregistre tous les plugins dans la machine à états"""
        # Récupération des instances via le conteneur global
        state_machine = container.audio_state_machine()
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.SNAPCLIENT, container.snapclient_plugin())
        state_machine.register_plugin(AudioSource.BLUETOOTH, container.bluetooth_plugin())

        
        # Injecter la référence à la machine à états dans les plugins
        container.librespot_plugin().set_state_machine(state_machine)
        container.snapclient_plugin().set_state_machine(state_machine)
        container.bluetooth_plugin().set_state_machine(state_machine)


# Création et configuration du conteneur
container = Container()