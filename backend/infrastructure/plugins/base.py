"""
Classe de base pour les plugins de sources audio.
"""
import logging
from abc import ABC
from typing import Dict, Any, Optional, Set

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus


class BaseAudioPlugin(AudioSourcePlugin, ABC):
    """
    Classe de base qui implémente les fonctionnalités communes à tous les plugins audio.
    """
    
    # États communs standardisés
    STATE_INACTIVE = "inactive"  
    STATE_READY_TO_CONNECT = "ready_to_connect"
    STATE_DEVICE_CHANGE_REQUESTED = "device_change_requested" 
    STATE_CONNECTED = "connected"
    
    # États spécifiques pour plugins avec contrôle de lecture
    STATE_PLAYING = "playing"
    STATE_PAUSED = "paused"
    STATE_STOPPED = "stopped"
    
    # États d'erreur standardisés
    STATE_ERROR_CONNECTION = "error_connection"
    STATE_ERROR_AUTHENTICATION = "error_authentication"
    STATE_ERROR_PLAYBACK = "error_playback"
    
    # Transitions valides entre états
    VALID_TRANSITIONS = {
        STATE_INACTIVE: {STATE_READY_TO_CONNECT},
        STATE_READY_TO_CONNECT: {STATE_CONNECTED, STATE_DEVICE_CHANGE_REQUESTED, STATE_INACTIVE, 
                                STATE_ERROR_CONNECTION},
        STATE_DEVICE_CHANGE_REQUESTED: {STATE_CONNECTED, STATE_READY_TO_CONNECT, STATE_INACTIVE},
        STATE_CONNECTED: {STATE_READY_TO_CONNECT, STATE_INACTIVE, STATE_PLAYING, STATE_PAUSED, 
                        STATE_STOPPED},
        STATE_PLAYING: {STATE_PAUSED, STATE_STOPPED, STATE_READY_TO_CONNECT, STATE_INACTIVE, 
                      STATE_ERROR_PLAYBACK},
        STATE_PAUSED: {STATE_PLAYING, STATE_STOPPED, STATE_READY_TO_CONNECT, STATE_INACTIVE},
        STATE_STOPPED: {STATE_PLAYING, STATE_READY_TO_CONNECT, STATE_INACTIVE},
        STATE_ERROR_CONNECTION: {STATE_READY_TO_CONNECT, STATE_INACTIVE},
        STATE_ERROR_AUTHENTICATION: {STATE_READY_TO_CONNECT, STATE_INACTIVE},
        STATE_ERROR_PLAYBACK: {STATE_READY_TO_CONNECT, STATE_CONNECTED, STATE_INACTIVE},
    }
    
    def __init__(self, event_bus: EventBus, name: str):
        """
        Initialise le plugin de base avec un bus d'événements et un nom.
        
        Args:
            event_bus: Bus d'événements pour la communication
            name: Nom du plugin pour l'identification et le logging
        """
        self.event_bus = event_bus
        self.name = name
        self.is_active = False
        self.logger = logging.getLogger(f"plugin.{name}")
        self.metadata = {}
        self._current_state = self.STATE_INACTIVE
    
    async def transition_to_state(self, target_state: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """
        Effectue une transition validée vers un nouvel état.
        
        Args:
            target_state: État cible de la transition
            details: Détails supplémentaires à inclure dans l'événement d'état
            
        Returns:
            bool: True si la transition a réussi, False sinon
        """
        # Vérifier si la transition est valide
        valid_targets = self.VALID_TRANSITIONS.get(self._current_state, set())
        if target_state not in valid_targets:
            self.logger.warning(f"Transition non valide: {self._current_state} -> {target_state}")
            return False
        
        # Enregistrer l'état précédent pour le logging
        previous_state = self._current_state
        
        # Mettre à jour l'état interne et publier l'événement
        self._current_state = target_state
        await self.publish_plugin_state(target_state, details)
        
        self.logger.info(f"Transition d'état: {previous_state} -> {target_state}")
        return True
    
    async def publish_plugin_state(self, state: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un état standardisé sur le bus d'événements.
        
        Args:
            state: État standardisé (STATE_INACTIVE, STATE_READY_TO_CONNECT, etc.)
            details: Détails supplémentaires à inclure
        """
        status_data = {
            "source": self.name,
            "plugin_state": state
        }
        
        if details:
            status_data.update(details)
            
        await self.event_bus.publish("audio_status_updated", status_data)
    
    async def publish_error(self, error_type: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie une erreur sur le bus d'événements et met à jour l'état du plugin.
        
        Args:
            error_type: Type d'erreur (connection, authentication, playback)
            message: Message d'erreur
            details: Détails supplémentaires de l'erreur
        """
        error_data = {
            "source": self.name,
            "error_type": error_type,
            "error_message": message
        }
        
        if details:
            error_data.update(details)
        
        # Déterminer l'état d'erreur correspondant
        error_state = None
        if error_type == "connection":
            error_state = self.STATE_ERROR_CONNECTION
        elif error_type == "authentication":
            error_state = self.STATE_ERROR_AUTHENTICATION
        elif error_type == "playback":
            error_state = self.STATE_ERROR_PLAYBACK
        
        # Publier l'erreur et mettre à jour l'état si possible
        await self.event_bus.publish("audio_error", error_data)
        
        if error_state:
            await self.transition_to_state(error_state, {
                "error": True,
                "error_type": error_type,
                "error_message": message
            })
    
    @property
    def current_state(self) -> str:
        """
        Récupère l'état actuel du plugin.
        
        Returns:
            str: État actuel du plugin
        """
        return self._current_state
    
    @property
    def is_connected(self) -> bool:
        """
        Vérifie si le plugin est actuellement connecté.
        
        Returns:
            bool: True si le plugin est connecté, False sinon
        """
        return self._current_state in {self.STATE_CONNECTED, self.STATE_PLAYING, 
                                     self.STATE_PAUSED, self.STATE_STOPPED}