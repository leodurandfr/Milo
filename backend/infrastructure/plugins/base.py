"""
Classe de base simplifiée pour les plugins de sources audio.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.plugins.plugin_utils import get_plugin_logger

class UnifiedAudioPlugin(AudioSourcePlugin, ABC):
    """Classe de base pour les plugins audio avec fonctionnalités essentielles."""
    
    def __init__(self, event_bus: EventBus, name: str):
        """Initialise le plugin avec un bus d'événements et un nom."""
        self.event_bus = event_bus
        self.name = name
        self.logger = get_plugin_logger(name)
        self._state_machine = None
        self._current_state = PluginState.INACTIVE
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence à la machine à états."""
        self._state_machine = state_machine
    
    async def notify_state_change(self, new_state: PluginState, 
                                metadata: Optional[Dict[str, Any]] = None) -> None:
        """Notifie la machine à états d'un changement d'état du plugin."""
        self._current_state = new_state
        
        if self._state_machine:
            await self._state_machine.update_plugin_state(
                source=self._get_audio_source(),
                new_state=new_state,
                metadata=metadata or {}
            )
        else:
            self.logger.error("Machine à états non définie, impossible de notifier le changement d'état")
    
    def _get_audio_source(self) -> AudioSource:
        """Retourne l'enum AudioSource correspondant à ce plugin."""
        source_map = {
            'librespot': AudioSource.LIBRESPOT,
            'snapclient': AudioSource.SNAPCLIENT,
            'bluetooth': AudioSource.BLUETOOTH,
            'webradio': AudioSource.WEBRADIO
        }
        return source_map.get(self.name, AudioSource.NONE)
    
    @property
    def current_state(self) -> PluginState:
        """Récupère l'état actuel du plugin."""
        return self._current_state
    
    # Méthodes abstraites obligatoires
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialise le plugin."""
        pass
        
    @abstractmethod
    async def start(self) -> bool:
        """Démarre la source audio."""
        pass
        
    @abstractmethod
    async def stop(self) -> bool:
        """Arrête la source audio."""
        pass
        
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel de la source audio."""
        pass
        
    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite une commande spécifique à cette source."""
        pass