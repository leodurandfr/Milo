# backend/infrastructure/state/state_machine.py
"""
Machine à états unifiée avec contrôle centralisé des processus
"""
import asyncio
import time
from typing import Dict, Any, Optional
import logging
from backend.domain.audio_state import AudioSource, PluginState, SystemAudioState
from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.application.event_bus import EventBus
from backend.infrastructure.process.process_manager import ProcessManager
from backend.domain.events import StandardEvent, EventCategory, EventType

class UnifiedAudioStateMachine:
    """Gère l'état complet du système audio avec contrôle centralisé des processus"""
    
    TRANSITION_TIMEOUT = 5.0  # Timeout pour les transitions
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.system_state = SystemAudioState()
        self.plugins: Dict[AudioSource, Optional[AudioSourcePlugin]] = {
            source: None for source in AudioSource 
            if source not in (AudioSource.NONE,)
        }
        self.logger = logging.getLogger(__name__)
        self._transition_lock = asyncio.Lock()
        self.process_manager = ProcessManager()  # Gestionnaire centralisé des processus
    
    def register_plugin(self, source: AudioSource, plugin: AudioSourcePlugin) -> None:
        """Enregistre un plugin pour une source spécifique"""
        if source in self.plugins:
            self.plugins[source] = plugin
            self.logger.info(f"Plugin registered for source: {source.value}")
        else:
            self.logger.error(f"Cannot register plugin for invalid source: {source.value}")
    
    async def get_current_state(self) -> Dict[str, Any]:
        """Renvoie l'état actuel du système"""
        return self.system_state.to_dict()
    
    async def transition_to_source(self, target_source: AudioSource) -> bool:
        """
        Effectue une transition vers une nouvelle source avec contrôle centralisé.
        """
        async with self._transition_lock:
            # Vérification si déjà sur la bonne source
            if self.system_state.active_source == target_source and \
               self.system_state.plugin_state != PluginState.ERROR:
                self.logger.info(f"Already on source {target_source.value}")
                return True
            
            # Vérification du plugin
            if target_source != AudioSource.NONE and target_source not in self.plugins:
                self.logger.error(f"No plugin registered for source: {target_source.value}")
                return False
            
            try:
                # Début de la transition
                self.system_state.transitioning = True
                await self._publish_standardized_event(
                    EventCategory.SYSTEM,
                    EventType.TRANSITION_START,
                    "system",
                    {
                        "from_source": self.system_state.active_source.value,
                        "to_source": target_source.value
                    }
                )
                
                # Arrêt de la source actuelle avec gestion centralisée
                await self._stop_current_source()
                
                # Démarrage de la nouvelle source avec gestion centralisée
                if target_source != AudioSource.NONE:
                    success = await self._start_new_source(target_source)
                    if not success:
                        raise ValueError(f"Failed to start {target_source.value}")
                else:
                    # Cas spécial pour NONE
                    self.system_state.active_source = AudioSource.NONE
                    self.system_state.plugin_state = PluginState.INACTIVE
                    self.system_state.metadata = {}
                
                # Fin de la transition
                self.system_state.transitioning = False
                await self._publish_standardized_event(
                    EventCategory.SYSTEM,
                    EventType.TRANSITION_COMPLETE,
                    "system",
                    {
                        "active_source": self.system_state.active_source.value,
                        "plugin_state": self.system_state.plugin_state.value
                    }
                )
                
                return True
                
            except Exception as e:
                self.logger.error(f"Transition error: {str(e)}")
                self.system_state.transitioning = False
                self.system_state.error = str(e)
                
                # En cas d'erreur, arrêter tous les processus
                await self._emergency_stop()
                
                await self._publish_standardized_event(
                    EventCategory.SYSTEM,
                    EventType.ERROR,
                    "system",
                    {
                        "error": str(e),
                        "attempted_source": target_source.value
                    }
                )
                
                return False
    
    async def update_plugin_state(self, source: AudioSource, new_state: PluginState, 
                               metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Met à jour l'état d'un plugin.
        Appelé par les plugins pour notifier leurs changements d'état.
        """
        if source != self.system_state.active_source:
            self.logger.warning(f"Ignoring state update from inactive source: {source.value}")
            return
        
        old_state = self.system_state.plugin_state
        self.system_state.plugin_state = new_state
        
        if metadata:
            self.system_state.metadata.update(metadata)
        
        # Si le plugin passe en erreur, on nettoie
        if new_state == PluginState.ERROR:
            self.system_state.error = metadata.get("error") if metadata else "Unknown error"
        else:
            self.system_state.error = None
        
        await self._publish_standardized_event(
            EventCategory.PLUGIN,
            EventType.PLUGIN_STATE_CHANGED,
            source.value,
            {
                "old_state": old_state.value,
                "new_state": new_state.value,
                "metadata": metadata
            }
        )
    
    async def _stop_current_source(self) -> None:
        """Arrête la source actuellement active avec gestion centralisée"""
        if self.system_state.active_source != AudioSource.NONE:
            current_plugin = self.plugins.get(self.system_state.active_source)
            if current_plugin:
                try:
                    # Arrêter le plugin
                    await current_plugin.stop()
                    
                    # Arrêter le processus via le gestionnaire centralisé
                    await self.process_manager.stop_process(self.system_state.active_source)
                    
                    self.system_state.plugin_state = PluginState.INACTIVE
                    self.system_state.metadata = {}
                except Exception as e:
                    self.logger.error(f"Error stopping {self.system_state.active_source.value}: {e}")
    
    async def _start_new_source(self, source: AudioSource) -> bool:
        """Démarre une nouvelle source avec gestion centralisée ou déléguée selon le plugin"""
        plugin = self.plugins.get(source)
        if not plugin:
            return False
        
        try:
            # Initialisation si nécessaire
            if not getattr(plugin, '_initialized', False):
                if await plugin.initialize():
                    plugin._initialized = True
                else:
                    self.logger.error(f"Échec de l'initialisation du plugin {source.value}")
                    return False
            
            # Important: mettre à jour la source active AVANT de démarrer le plugin
            # Cela permettra au plugin d'envoyer des mises à jour d'état correctement
            self.system_state.active_source = source
            
            # Détermine si le plugin gère son propre processus
            plugin_manages_process = plugin.manages_own_process()
            
            # Démarrer le processus via le gestionnaire si nécessaire
            if not plugin_manages_process and hasattr(plugin, 'get_process_command'):
                command = plugin.get_process_command()
                process_started = await self.process_manager.start_process(source, command)
                if not process_started:
                    self.logger.error(f"Échec du démarrage du processus pour {source.value}")
                    return False
            
            # Démarrer le plugin
            success = await plugin.start()
            if success:
                return True
            else:
                # En cas d'échec, réinitialiser la source active
                self.system_state.active_source = AudioSource.NONE
                # Nettoyer le processus si nécessaire
                if not plugin_manages_process:
                    await self.process_manager.stop_process(source)
                return False
                    
        except Exception as e:
            self.logger.error(f"Error starting {source.value}: {e}")
            # Réinitialiser la source active en cas d'erreur
            self.system_state.active_source = AudioSource.NONE
            # Nettoyer le processus si nécessaire
            if not getattr(plugin, 'manages_own_process', lambda: False)():
                await self.process_manager.stop_process(source)
            return False
    
    async def _emergency_stop(self) -> None:
        """Arrêt d'urgence - arrête tous les processus"""
        # Arrêter tous les processus via le gestionnaire centralisé
        await self.process_manager.stop_all_processes()
        
        # Arrêter tous les plugins
        for plugin in self.plugins.values():
            if plugin:
                try:
                    await plugin.stop()
                except Exception as e:
                    self.logger.error(f"Emergency stop error: {e}")
        
        self.system_state.active_source = AudioSource.NONE
        self.system_state.plugin_state = PluginState.INACTIVE
        self.system_state.metadata = {}
        self.system_state.error = None
    
    
    async def _publish_standardized_event(self, category: EventCategory, type: EventType, 
                                        source: str, data: Dict[str, Any]) -> None:
        """Publie un événement standardisé"""
        event = StandardEvent(
            category=category,
            type=type,
            source=source,
            data={**data, "full_state": self.system_state.to_dict()}
        )
        await self.event_bus.publish_event(event)