# backend/infrastructure/state/state_machine.py
"""
Machine à états unifiée OPTIM avec observer pattern pour événements routing
"""
import asyncio
import time
from typing import Dict, Any, Optional
import logging
from backend.domain.audio_state import AudioSource, PluginState, SystemAudioState
from backend.application.interfaces.audio_source import AudioSourcePlugin

class UnifiedAudioStateMachine:
    """Gère l'état complet du système audio - Version OPTIM avec observer pattern"""
    
    TRANSITION_TIMEOUT = 5.0
    
    def __init__(self, routing_service=None, websocket_handler=None):
        self.routing_service = routing_service  # Sera résolu plus tard
        self.websocket_handler = websocket_handler
        self.system_state = SystemAudioState()
        self.plugins: Dict[AudioSource, Optional[AudioSourcePlugin]] = {
            source: None for source in AudioSource 
            if source not in (AudioSource.NONE,)
        }
        self.logger = logging.getLogger(__name__)
        self._transition_lock = asyncio.Lock()

    
    def _sync_routing_state(self) -> None:
        """Synchronise l'état de routage initial"""
        if self.routing_service:
            routing_state = self.routing_service.get_state()
            self.system_state.multiroom_enabled = routing_state.multiroom_enabled
            self.system_state.equalizer_enabled = routing_state.equalizer_enabled
    
    def register_plugin(self, source: AudioSource, plugin: AudioSourcePlugin) -> None:
        """Enregistre un plugin pour une source spécifique"""
        if source in self.plugins:
            self.plugins[source] = plugin
            self.logger.info(f"Plugin registered for source: {source.value}")
        else:
            self.logger.error(f"Cannot register plugin for invalid source: {source.value}")
    
    def get_plugin(self, source: AudioSource) -> Optional[AudioSourcePlugin]:
        """Récupère un plugin pour le routing service"""
        return self.plugins.get(source)
    
    def get_plugin_metadata(self, source: AudioSource) -> Dict[str, Any]:
        """Récupère les métadonnées d'un plugin spécifique"""
        if source == self.system_state.active_source:
            return self.system_state.metadata
        return {}
    
    def get_plugin_state(self, source: AudioSource) -> PluginState:
        """Récupère l'état d'un plugin spécifique"""
        if source == self.system_state.active_source:
            return self.system_state.plugin_state
        return PluginState.INACTIVE
    
    async def get_current_state(self) -> Dict[str, Any]:
        """Renvoie l'état actuel du système avec synchronisation automatique"""
        if self.routing_service:
            self._sync_routing_state()
        return self.system_state.to_dict()
    
    async def transition_to_source(self, target_source: AudioSource) -> bool:
        """Effectue une transition vers une nouvelle source"""
        async with self._transition_lock:
            # 🐛 DEBUG : Log début de transition
            print(f"🚀 START TRANSITION: {self.system_state.active_source.value} -> {target_source.value}")
            print(f"🚀 STATE AVANT: active={self.system_state.active_source.value}, plugin_state={self.system_state.plugin_state.value}, transitioning={self.system_state.transitioning}")
            
            if self.system_state.active_source == target_source and \
            self.system_state.plugin_state != PluginState.ERROR:
                self.logger.info(f"Already on source {target_source.value}")
                return True
            
            if target_source != AudioSource.NONE and target_source not in self.plugins:
                self.logger.error(f"No plugin registered for source: {target_source.value}")
                return False
            
            try:
                print(f"🚀 SETTING TRANSITION STATE")
                self.system_state.transitioning = True
                self.system_state.target_source = target_source
                
                print(f"🚀 BROADCASTING TRANSITION START")
                await self._broadcast_event("system", "transition_start", {
                    "from_source": self.system_state.active_source.value,
                    "to_source": target_source.value,
                    "source": "system"
                })
                
                print(f"🚀 STOPPING CURRENT SOURCE")
                await self._stop_current_source()
                
                if target_source != AudioSource.NONE:
                    print(f"🚀 STARTING NEW SOURCE: {target_source.value}")
                    success = await self._start_new_source(target_source)
                    print(f"🚀 START NEW SOURCE RESULT: {success}")
                    if not success:
                        raise ValueError(f"Failed to start {target_source.value}")
                else:
                    print(f"🚀 SETTING TO NONE")
                    self.system_state.active_source = AudioSource.NONE
                    self.system_state.plugin_state = PluginState.INACTIVE
                    self.system_state.metadata = {}
                
                print(f"🚀 RESETTING TRANSITION STATE")
                self.system_state.transitioning = False
                self.system_state.target_source = None
                
                print(f"🚀 BROADCASTING TRANSITION COMPLETE")
                await self._broadcast_event("system", "transition_complete", {
                    "active_source": self.system_state.active_source.value,
                    "plugin_state": self.system_state.plugin_state.value,
                    "source": "system"
                })
                
                print(f"🚀 TRANSITION SUCCESS")
                return True
                
            except Exception as e:
                print(f"🚀 TRANSITION ERROR: {str(e)}")
                self.logger.error(f"Transition error: {str(e)}")
                self.system_state.transitioning = False
                self.system_state.target_source = None
                self.system_state.error = str(e)
                await self._emergency_stop()
                await self._broadcast_event("system", "error", {
                    "error": str(e),
                    "attempted_source": target_source.value,
                    "source": "system"
                })
                return False
    
    async def update_plugin_state(self, source: AudioSource, new_state: PluginState, 
                               metadata: Optional[Dict[str, Any]] = None) -> None:
        """Met à jour l'état d'un plugin"""
        if source != self.system_state.active_source:
            self.logger.warning(f"Ignoring state update from inactive source: {source.value}")
            return
        
        # ✅ CORRECTION : Ignorer les updates pendant les transitions
        if self.system_state.transitioning:
            print(f"🚫 IGNORING UPDATE DURING TRANSITION: {source.value} -> {new_state.value}")
            self.logger.debug(f"Ignoring state update during transition: {source.value} -> {new_state.value}")
            return
        
        old_state = self.system_state.plugin_state
        self.system_state.plugin_state = new_state
        
        if metadata:
            self.system_state.metadata.update(metadata)
        
        if new_state == PluginState.ERROR:
            self.system_state.error = metadata.get("error") if metadata else "Unknown error"
        else:
            self.system_state.error = None
        
        await self._broadcast_event("plugin", "state_changed", {
            "source": source.value,
            "old_state": old_state.value,
            "new_state": new_state.value,
            "metadata": metadata
        })
    
    async def update_multiroom_state(self, enabled: bool) -> None:
        """Met à jour l'état multiroom dans l'état système"""
        old_state = self.system_state.multiroom_enabled
        self.system_state.multiroom_enabled = enabled
        
        await self._broadcast_event("system", "state_changed", {
            "old_state": old_state,
            "new_state": enabled,
            "multiroom_changed": True,
            "multiroom_enabled": enabled,
            "source": "routing"
        })
    
    async def update_equalizer_state(self, enabled: bool) -> None:
        """Met à jour l'état de l'equalizer dans l'état système"""
        old_state = self.system_state.equalizer_enabled
        self.system_state.equalizer_enabled = enabled
        
        await self._broadcast_event("system", "state_changed", {
            "old_state": old_state,
            "new_state": enabled,
            "equalizer_changed": True,
            "source": "equalizer"
        })
    
    async def _stop_current_source(self) -> None:
        """Arrête la source actuellement active"""
        if self.system_state.active_source != AudioSource.NONE:
            current_plugin = self.plugins.get(self.system_state.active_source)
            if current_plugin:
                try:
                    await current_plugin.stop()
                    self.system_state.plugin_state = PluginState.INACTIVE
                    self.system_state.metadata = {}
                except Exception as e:
                    self.logger.error(f"Error stopping {self.system_state.active_source.value}: {e}")
    
    async def _start_new_source(self, source: AudioSource) -> bool:
        """Démarre une nouvelle source - VERSION CORRIGÉE"""
        plugin = self.plugins.get(source)
        if not plugin:
            return False
        
        try:
            if not getattr(plugin, '_initialized', False):
                if await plugin.initialize():
                    plugin._initialized = True
                else:
                    self.logger.error(f"Échec de l'initialisation du plugin {source.value}")
                    return False
            
            # ✅ CORRECTION : NE PAS changer active_source avant plugin.start()
            # self.system_state.active_source = source  ← SUPPRIMÉ
            
            success = await plugin.start()
            if success:
                self.system_state.active_source = source
                
                # ✅ AJOUTER : Force l'état à READY si le plugin n'a pas notifié
                if self.system_state.plugin_state == PluginState.INACTIVE:
                    self.system_state.plugin_state = PluginState.READY
                print(f"🚀 ACTIVE SOURCE CHANGED TO: {source.value}")
                return True
            else:
                return False
                    
        except Exception as e:
            self.logger.error(f"Error starting {source.value}: {e}")
            return False
    
    async def _emergency_stop(self) -> None:
        """Arrêt d'urgence - arrête tous les processus"""
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
    
    async def broadcast_event(self, category: str, event_type: str, data: Dict[str, Any]) -> None:
        """Publie un événement directement au WebSocket - Méthode publique pour les routes"""
        await self._broadcast_event(category, event_type, data)
    
    async def _broadcast_event(self, category: str, event_type: str, data: Dict[str, Any]) -> None:
        """Publie un événement directement au WebSocket"""
        if not self.websocket_handler:
            return
        
        # 🐛 DEBUG : Log tous les broadcasts avec état complet
        current_state = self.system_state.to_dict()
        print(f"🔔 BROADCAST: {category}/{event_type} | active_source:{current_state['active_source']}, plugin_state:{current_state['plugin_state']}, transitioning:{current_state['transitioning']}, target_source:{current_state.get('target_source')}")
        
        event_data = {
            "category": category,
            "type": event_type,
            "source": data.get("source", category),
            "data": {**data, "full_state": self.system_state.to_dict()},
            "timestamp": time.time()
        }
        
        await self.websocket_handler.handle_event(event_data)