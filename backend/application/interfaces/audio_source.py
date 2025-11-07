"""
Abstract interface for audio sources.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any

class AudioSourcePlugin(ABC):
    """Common interface for all audio sources"""

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the plugin"""
        pass

    @abstractmethod
    async def start(self) -> bool:
        """Start the audio source"""
        pass

    @abstractmethod
    async def stop(self) -> bool:
        """Stop the audio source"""
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Get current status of the audio source"""
        pass

    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a command specific to this source"""
        pass