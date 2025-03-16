"""
Conteneur d'injection de dépendances pour l'application.
"""
from dependency_injector import containers, providers
from backend.application.event_bus import EventBus
from backend.infrastructure.state.state_machine import AudioStateMachine


class Container(containers.DeclarativeContainer):
    """Conteneur d'injection de dépendances pour l'application oakOS"""
    
    config = providers.Configuration()
    
    # Services centraux
    event_bus = providers.Singleton(EventBus)
    
    # Gestionnaires d'état
    audio_state_machine = providers.Singleton(
        AudioStateMachine,
        event_bus=event_bus
    )
    
    # Plugins audio (à ajouter ultérieurement)


# Création d'une instance du conteneur
container = Container()