"""
Implémentation de la machine à états pour gérer les transitions audio.
"""
import asyncio
import time
from typing import Dict, Any, Optional
import logging
from backend.domain.audio import AudioState, AudioStateInfo
from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus


class AudioStateMachine:
    """Gère les transitions entre états audio et l'état des plugins"""
    
    # Configuration du temps de transition standardisé
    STANDARD_TRANSITION_TIME = 1.0
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.state_info = AudioStateInfo()
        self.plugins: Dict[AudioState, Optional[AudioSourcePlugin]] = {
            state: None for state in AudioState if state != AudioState.NONE and state != AudioState.TRANSITIONING
        }
        self.logger = logging.getLogger(__name__)
        # États internes des plugins (connecté, en attente, etc.)
        self.plugin_states: Dict[AudioState, Dict[str, Any]] = {}
    
    def register_plugin(self, state: AudioState, plugin: AudioSourcePlugin) -> None:
        """Enregistre un plugin pour un état spécifique"""
        if state in self.plugins:
            self.plugins[state] = plugin
            self.plugin_states[state] = {
                "is_connected": False,
                "metadata": {},
                "plugin_state": "inactive"
            }
            
            # Injecter la référence à la machine à états dans le plugin
            if hasattr(plugin, 'set_state_machine'):
                plugin.set_state_machine(self)
            
            self.logger.info(f"Plugin registered for state: {state.value}")
        else:
            self.logger.error(f"Cannot register plugin for invalid state: {state.value}")
    
    async def get_current_state(self) -> Dict[str, Any]:
        """Renvoie l'état actuel du système"""
        current_state_data = self.state_info.to_dict()
        # Ajouter l'état du plugin actif si disponible
        if self.state_info.state in self.plugin_states:
            current_state_data["plugin_info"] = self.plugin_states[self.state_info.state]
        return current_state_data
    
    async def update_plugin_state(self, audio_state: AudioState, plugin_state: str, details: Dict[str, Any] = None) -> None:
        """Met à jour l'état interne d'un plugin"""
        if audio_state in self.plugin_states:
            self.plugin_states[audio_state]["plugin_state"] = plugin_state
            if details:
                self.plugin_states[audio_state].update(details)
            
            # Si c'est le plugin actif, publier un événement
            if audio_state == self.state_info.state:
                await self.event_bus.publish("audio_plugin_state_changed", {
                    "audio_state": audio_state.value,
                    "plugin_state": plugin_state,
                    "details": details or {}
                })
    
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
            start_time = time.time()
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
                
            # Temps standardisé
            elapsed_time = time.time() - start_time
            if elapsed_time < self.STANDARD_TRANSITION_TIME:
                delay = self.STANDARD_TRANSITION_TIME - elapsed_time
                await asyncio.sleep(delay)
            
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
            
            return True
            
        except Exception as e:
            self.logger.error(f"Transition error: {str(e)}")
            await self.event_bus.publish("audio_transition_error", {
                "error": str(e),
                "from_state": self.state_info.state.value,
                "to_state": target_state.value
            })
            return False
            
        finally:
            self.state_info.transitioning = False