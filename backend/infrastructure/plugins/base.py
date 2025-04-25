"""
Classe de base pour les plugins de sources audio.
"""
import logging
from abc import ABC
from typing import Dict, Any, Optional

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus


class BaseAudioPlugin(AudioSourcePlugin, ABC):
    """
    Classe de base qui implémente les fonctionnalités communes à tous les plugins audio.
    """
    
    # États communs standardisés simplifiés
    STATE_INACTIVE = "inactive"  # Plugin complètement arrêté
    STATE_READY = "ready"       # Plugin initialisé, en attente de connexion
    STATE_CONNECTED = "connected" # Plugin connecté et opérationnel
    STATE_ERROR = "error"       # Plugin en état d'erreur
    
    # Transitions valides entre états - simplifiées
    VALID_TRANSITIONS = {
        STATE_INACTIVE: {STATE_READY, STATE_ERROR},
        STATE_READY: {STATE_CONNECTED, STATE_INACTIVE, STATE_ERROR},
        STATE_CONNECTED: {STATE_READY, STATE_INACTIVE, STATE_ERROR},
        STATE_ERROR: {STATE_READY, STATE_INACTIVE},
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
        # Si nous sommes déjà dans l'état cible, mettre à jour uniquement les détails
        if self._current_state == target_state:
            if details:
                await self.publish_plugin_state(target_state, details)
            return True
            
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
            state: État standardisé (STATE_INACTIVE, STATE_READY, etc.)
            details: Détails supplémentaires à inclure
        """
        status_data = {
            "source": self.name,
            "plugin_state": state
        }
        
        if details:
            status_data.update(details)
        
        event_name = f"{self.name}_status_updated"
        await self.event_bus.publish(event_name, status_data)
    
    async def publish_error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie une erreur sur le bus d'événements et met à jour l'état du plugin.
        
        Args:
            message: Message d'erreur
            details: Détails supplémentaires de l'erreur
        """
        error_data = {
            "source": self.name,
            "error_message": message
        }
        
        if details:
            error_data.update(details)
        
        # Publier l'erreur
        await self.event_bus.publish("audio_error", error_data)
        
        # Mettre à jour l'état si possible
        await self.transition_to_state(self.STATE_ERROR, {
            "error": True,
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
        return self._current_state == self.STATE_CONNECTED