"""
Implémentation de la machine à états pour gérer les transitions audio.
"""
from typing import Dict, Any, Optional
import logging
from backend.domain.audio import AudioState, AudioStateInfo
from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus


class AudioStateMachine:
    """Gère les transitions entre états audio"""
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.state_info = AudioStateInfo()
        self.plugins: Dict[AudioState, Optional[AudioSourcePlugin]] = {
            state: None for state in AudioState if state != AudioState.NONE and state != AudioState.TRANSITIONING
        }
        self.logger = logging.getLogger(__name__)
    
    def register_plugin(self, state: AudioState, plugin: AudioSourcePlugin) -> None:
        """Enregistre un plugin pour un état spécifique"""
        if state in self.plugins:
            self.plugins[state] = plugin
            self.logger.info(f"Plugin registered for state: {state.value}")
        else:
            self.logger.error(f"Cannot register plugin for invalid state: {state.value}")
    
    async def get_current_state(self) -> Dict[str, Any]:
        """Renvoie l'état actuel du système"""
        return self.state_info.to_dict()
    
    async def transition_to(self, target_state: AudioState) -> bool:
        """Effectue une transition vers un nouvel état"""
        if self.state_info.transitioning:
            self.logger.warning("Already in transition, ignoring request")
            return False
            
        if target_state == self.state_info.state:
            self.logger.info(f"Already in state {target_state.value}, no transition needed")
            return True
            
        plugin = self.plugins.get(target_state)
        if not plugin:
            self.logger.error(f"No plugin registered for state: {target_state.value}")
            return False
            
        try:
            self.state_info.transitioning = True
            
            # Publier événement de début de transition
            await self.event_bus.publish("audio_state_changing", {
                "from_state": self.state_info.state.value,
                "to_state": target_state.value
            })
            
            # Arrêter la source actuelle si elle existe
            current_plugin = self.plugins.get(self.state_info.state)
            if current_plugin:
                await current_plugin.stop()
                
            # Démarrer la nouvelle source
            success = await plugin.start()
            if not success:
                raise ValueError(f"Failed to start {target_state.value} plugin")
                
            # Mettre à jour l'état
            previous_state = self.state_info.state
            self.state_info.state = target_state
            self.state_info.transitioning = False
            
            # Publier événement de fin de transition
            await self.event_bus.publish("audio_state_changed", {
                "from_state": previous_state.value,
                "current_state": self.state_info.state.value,
                "transitioning": False
            })
            
            self.logger.info(f"Transition successful: {previous_state.value} -> {target_state.value}")
            return True
            
        except Exception as e:
            self.logger.error(f"Transition error: {str(e)}")
            # Publier événement d'erreur
            await self.event_bus.publish("audio_transition_error", {
                "error": str(e),
                "from_state": self.state_info.state.value,
                "to_state": target_state.value
            })
            return False
            
        finally:
            self.state_info.transitioning = False