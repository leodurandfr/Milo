# backend/infrastructure/plugins/base.py
"""
Classe de base pour les plugins de sources audio - Version avec contrôle centralisé
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus
from backend.domain.audio_state import PluginState, AudioSource


class UnifiedAudioPlugin(AudioSourcePlugin, ABC):
    """
    Classe de base qui implémente les fonctionnalités communes à tous les plugins audio
    dans le nouveau modèle unifié avec contrôle centralisé des processus.
    """
    
    def __init__(self, event_bus: EventBus, name: str):
        """
        Initialise le plugin de base avec un bus d'événements et un nom.
        """
        self.event_bus = event_bus
        self.name = name
        self.logger = logging.getLogger(f"plugin.{name}")
        self._state_machine = None
        self._metadata = {}
        self._current_state = PluginState.INACTIVE
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence à la machine à états"""
        self._state_machine = state_machine
    
    async def notify_state_change(self, new_state: PluginState, 
                                metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Notifie la machine à états d'un changement d'état du plugin.
        """
        self._current_state = new_state
        
        if self._state_machine:
            await self._state_machine.update_plugin_state(
                source=self._get_audio_source(),
                new_state=new_state,
                metadata=metadata
            )
        else:
            self.logger.error("State machine not set, cannot notify state change")
    
    def _get_audio_source(self) -> AudioSource:
        """Retourne l'enum AudioSource correspondant à ce plugin"""
        source_map = {
            'librespot': AudioSource.LIBRESPOT,
            'snapclient': AudioSource.SNAPCLIENT,
            'bluetooth': AudioSource.BLUETOOTH,
            'webradio': AudioSource.WEBRADIO
        }
        return source_map.get(self.name, AudioSource.NONE)
    
    @property
    def current_state(self) -> PluginState:
        """Récupère l'état actuel du plugin"""
        return self._current_state
    
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """
        Récupère l'état initial complet du plugin.
        Peut être surchargé par les plugins pour fournir des données spécifiques.
        """
        return await self.get_status()
    
    
    # Méthodes abstraites existantes
    @abstractmethod
    async def initialize(self) -> bool:
        """Initialise le plugin"""
        pass
        
    @abstractmethod
    async def start(self) -> bool:
        """Démarre la source audio"""
        pass
        
    @abstractmethod
    async def stop(self) -> bool:
        """Arrête la source audio"""
        pass
        
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel de la source audio"""
        pass
        
    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite une commande spécifique à cette source"""
        pass