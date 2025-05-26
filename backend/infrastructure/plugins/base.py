"""
Classe de base optimisée pour les plugins de sources audio.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class UnifiedAudioPlugin(AudioSourcePlugin, ABC):
    """Classe de base pour plugins audio avec fonctionnalités communes et gestion d'état améliorée."""
    
    def __init__(self, event_bus, name: str):
        self.event_bus = event_bus
        self.name = name
        self.logger = logging.getLogger(f"plugin.{name}")
        self._state_machine = None
        self._current_state = PluginState.INACTIVE
        self.service_manager = SystemdServiceManager()
        self._initialized = False  # Nouveau flag pour éviter les réinitialisations
    
    def set_state_machine(self, state_machine) -> None:
        """Définit la référence à la machine à états."""
        self._state_machine = state_machine
    
    async def notify_state_change(self, new_state: PluginState, metadata: Dict[str, Any] = None) -> None:
        """Notifie la machine à états d'un changement d'état du plugin."""
        self._current_state = new_state
        
        if self._state_machine:
            await self._state_machine.update_plugin_state(
                source=self._get_audio_source(),
                new_state=new_state,
                metadata=metadata or {}
            )
    
    def _get_audio_source(self) -> AudioSource:
        """Retourne l'enum AudioSource correspondant à ce plugin."""
        sources = {
            'librespot': AudioSource.LIBRESPOT,
            'roc': AudioSource.ROC,
            'snapclient': AudioSource.SNAPCLIENT,
            'bluetooth': AudioSource.BLUETOOTH,
            'webradio': AudioSource.WEBRADIO
        }
        return sources.get(self.name, AudioSource.NONE)
    
    @property
    def current_state(self) -> PluginState:
        """Récupère l'état actuel du plugin."""
        return self._current_state
    
    # Méthodes utilitaires communes
    
    async def control_service(self, service_name: str, action: str) -> bool:
        """Contrôle un service systemd (start, stop, restart)."""
        try:
            self.logger.info(f"{action.capitalize()} du service {service_name}")
            
            actions = {
                "start": self.service_manager.start,
                "stop": self.service_manager.stop,
                "restart": self.service_manager.restart
            }
            
            if action not in actions:
                self.logger.error(f"Action non supportée: {action}")
                return False
                
            success = await actions[action](service_name)
            
            if not success:
                self.logger.error(f"Échec de l'opération {action} sur {service_name}")
                
            return success
        except Exception as e:
            self.logger.error(f"Erreur {action} service {service_name}: {e}")
            return False
    
    def format_response(self, success: bool, message: str = None, error: str = None, **kwargs) -> Dict[str, Any]:
        """Formate une réponse standard pour les commandes."""
        response = {"success": success}
        
        if success and message:
            response["message"] = message
        elif not success and error:
            response["error"] = error
            
        return {**response, **kwargs}
    
    # Nouvelles méthodes standardisées
    
    async def initialize(self) -> bool:
        """Initialise le plugin avec idempotence."""
        if self._initialized:
            return True
            
        try:
            success = await self._do_initialize()
            if success:
                self._initialized = True
            return success
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation {self.name}: {e}")
            return False
    
    @abstractmethod
    async def _do_initialize(self) -> bool:
        """Implémentation spécifique de l'initialisation."""
        pass
    
    async def start(self) -> bool:
        """Démarre la source audio avec gestion d'état."""
        # S'assurer que le plugin est initialisé
        if not self._initialized and not await self.initialize():
            await self.notify_state_change(PluginState.ERROR, {"error": "Échec d'initialisation"})
            return False
            
        try:
            # Démarrer le plugin
            success = await self._do_start()
            
            # Notifier le changement d'état
            if success:
                await self.notify_state_change(PluginState.READY)
            else:
                await self.notify_state_change(PluginState.ERROR, {"error": "Échec du démarrage"})
                
            return success
        except Exception as e:
            self.logger.error(f"Erreur démarrage {self.name}: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    @abstractmethod
    async def _do_start(self) -> bool:
        """Implémentation spécifique du démarrage."""
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
        """Traite une commande pour cette source."""
        pass

    # Méthode optionnelle avec implémentation par défaut
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial pour les nouvelles connexions WebSocket."""
        return await self.get_status()