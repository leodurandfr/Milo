"""
Implémentation du bus d'événements pour la communication entre composants.
"""
from collections import defaultdict
from typing import Callable, Dict, Any

class EventBus:
    """Bus d'événements central pour la communication entre composants"""
    
    def __init__(self):
        self.subscribers = defaultdict(list)
        
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """S'abonne à un type d'événement"""
        self.subscribers[event_type].append(callback)
        
    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Se désabonne d'un type d'événement"""
        if event_type in self.subscribers:
            self.subscribers[event_type].remove(callback)
            
    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Publie un événement aux abonnés"""
        for callback in self.subscribers[event_type]:
            await callback(data)