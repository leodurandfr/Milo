"""
Interface abstraite pour les sources audio.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any

class AudioSourcePlugin(ABC):
    """Interface commune pour toutes les sources audio"""
    
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