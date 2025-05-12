"""
Classe de base concise pour les plugins de sources audio.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Callable

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class UnifiedAudioPlugin(AudioSourcePlugin, ABC):
    """Classe de base pour plugins audio avec fonctionnalités communes."""
    
    def __init__(self, event_bus, name: str):
        self.event_bus = event_bus
        self.name = name
        self.logger = logging.getLogger(f"plugin.{name}")
        self._state_machine = None
        self._current_state = PluginState.INACTIVE
        self.service_manager = SystemdServiceManager()
    
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
    
    # Méthodes abstraites que les plugins doivent implémenter
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
        """Traite une commande pour cette source."""
        pass

    # Méthode optionnelle que les plugins peuvent implémenter
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial pour les nouvelles connexions WebSocket."""
        return await self.get_status()