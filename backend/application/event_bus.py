"""
Implémentation du bus d'événements pour la communication entre composants.
"""
from collections import defaultdict
from typing import Callable, Dict, Any
import logging
from backend.domain.events import StandardEvent, EventCategory, EventType

class EventBus:
    """Bus d'événements central avec support des événements standardisés"""
    
    def __init__(self):
        self.subscribers = defaultdict(list)
        self.logger = logging.getLogger(__name__)
        
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """S'abonne à un type d'événement (legacy)"""
        self.subscribers[event_type].append(callback)
    
    def subscribe_to_category(self, category: EventCategory, callback: Callable) -> None:
        """S'abonne à une catégorie d'événements"""
        self.subscribers[category.value].append(callback)
    
    def subscribe_to_type(self, event_type: EventType, callback: Callable) -> None:
        """S'abonne à un type spécifique d'événement"""
        self.subscribers[event_type.value].append(callback)
        
    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Se désabonne d'un type d'événement"""
        if event_type in self.subscribers:
            self.subscribers[event_type].remove(callback)
            
    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Publie un événement (legacy)"""
        for callback in self.subscribers[event_type]:
            try:
                await callback(data)
            except Exception as e:
                self.logger.error(f"Error in event callback: {e}")
    
    async def publish_event(self, event: StandardEvent) -> None:
        """Publie un événement standardisé"""
        # Notifier les abonnés à la catégorie
        for callback in self.subscribers[event.category.value]:
            try:
                await callback(event)
            except Exception as e:
                self.logger.error(f"Error in category callback: {e}")
        
        # Notifier les abonnés au type spécifique
        for callback in self.subscribers[event.type.value]:
            try:
                await callback(event)
            except Exception as e:
                self.logger.error(f"Error in type callback: {e}")
        
        # Pour la compatibilité, publier aussi l'ancien format
        legacy_event_type = f"{event.category.value}.{event.type.value}"
        await self.publish(legacy_event_type, event.to_dict())