# backend/config/container.py
"""
Conteneur d'injection de dépendances - Version unifiée
"""
from dependency_injector import containers, providers
from backend.application.event_bus import EventBus
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.infrastructure.plugins.librespot import LibrespotPlugin
from backend.infrastructure.plugins.snapclient import SnapclientPlugin
from backend.domain.audio_state import AudioSource


class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour oakOS"""
    
    config = providers.Configuration()
    
    # Services centraux
    event_bus = providers.Singleton(EventBus)
    
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
            "executable_path": "/usr/bin/snapclient",
            "auto_discover": True, 
            "auto_connect": True
        })
    )
    
    # Méthode pour enregistrer les plugins
    @providers.Callable
    def register_plugins():
        """Enregistre tous les plugins dans la machine à états"""
        # Récupération des instances via le conteneur global
        state_machine = container.audio_state_machine()
        state_machine.register_plugin(AudioSource.LIBRESPOT, container.librespot_plugin())
        state_machine.register_plugin(AudioSource.SNAPCLIENT, container.snapclient_plugin())
        
        # Injecter la référence à la machine à états dans les plugins
        container.librespot_plugin().set_state_machine(state_machine)
        container.snapclient_plugin().set_state_machine(state_machine)


# Création et configuration du conteneur
container = Container()