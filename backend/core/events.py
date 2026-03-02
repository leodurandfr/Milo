# backend/core/events.py
"""
Simple async EventBus for decoupled service communication.

This EventBus enables services to communicate without direct dependencies,
eliminating circular imports and improving testability.

Event Naming Convention:
    Events use 'domain.action' format:
    - source.started, source.stopped, source.error
    - volume.changed, volume.muted
    - routing.mode_changed
    - equalizer.config_changed
    - multiroom.client_state_changed, multiroom.zone_changed

Usage:
    # Create bus (typically a singleton)
    bus = EventBus()

    # Register handlers
    bus.on("volume.changed", handle_volume)
    bus.on("volume.changed", async_handle_volume)  # async also works

    # Emit events
    await bus.emit("volume.changed", {"volume_db": -25.0, "mute": False})

    # Remove handler
    bus.off("volume.changed", handle_volume)

Standard Events:
    source.started      {source_id: str, metadata: dict}
    source.stopped      {source_id: str}
    source.error        {source_id: str, error: str}
    volume.changed      {volume_db: float, mute: bool}
    routing.mode_changed {multiroom_enabled: bool, equalizer_effects_enabled: bool}
    equalizer.config_changed  {config: dict}
"""
from typing import Any, Callable, Dict, List, Optional
import asyncio
import inspect
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple async EventBus for decoupled service communication.

    Supports both sync and async handlers. Errors in handlers are logged
    but do not prevent other handlers from being called.
    """

    def __init__(self, debug: bool = False):
        """
        Initialize EventBus.

        Args:
            debug: If True, log all emitted events
        """
        self._handlers: Dict[str, List[Callable]] = {}
        self._debug = debug

    def on(self, event: str, handler: Callable) -> None:
        """
        Register a handler for an event.

        Args:
            event: Event name (e.g., 'volume.changed')
            handler: Callable to invoke when event is emitted
        """
        if event not in self._handlers:
            self._handlers[event] = []
        if handler not in self._handlers[event]:
            self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable) -> None:
        """
        Remove a handler for an event.

        Args:
            event: Event name
            handler: Handler to remove
        """
        if event in self._handlers:
            self._handlers[event] = [
                h for h in self._handlers[event] if h != handler
            ]

    def clear(self, event: Optional[str] = None) -> None:
        """
        Clear handlers for an event or all events.

        Args:
            event: Event name to clear, or None to clear all
        """
        if event is None:
            self._handlers.clear()
        elif event in self._handlers:
            del self._handlers[event]

    async def emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event to all registered handlers.

        Handlers are called in registration order. Errors in handlers
        are logged but do not stop other handlers from being called.

        Args:
            event: Event name
            data: Data to pass to handlers
        """
        if self._debug:
            logger.debug(f"EventBus: {event} -> {data}")

        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"Error in handler for '{event}': {e}")

    def has_handlers(self, event: str) -> bool:
        """Check if an event has any registered handlers."""
        return bool(self._handlers.get(event))

    def handler_count(self, event: str) -> int:
        """Get the number of handlers for an event."""
        return len(self._handlers.get(event, []))


# Standard event name constants
class Events:
    """Standard event names for type safety."""

    # Source events
    SOURCE_STARTED = "source.started"
    SOURCE_STOPPED = "source.stopped"
    SOURCE_ERROR = "source.error"
    SOURCE_STATE_CHANGED = "source.state_changed"

    # System events
    TRANSITION_START = "system.transition_start"
    TRANSITION_COMPLETE = "system.transition_complete"

    # Volume events
    VOLUME_CHANGED = "volume.changed"

    # Routing events
    ROUTING_MODE_CHANGED = "routing.mode_changed"

    # Equalizer events
    EQUALIZER_CONFIG_CHANGED = "equalizer.config_changed"

    # Radio events
    RADIO_FAVORITE_RESTORED = "radio.favorite_restored"
    RADIO_CUSTOM_STATION_ADDED = "radio.custom_station_added"
    RADIO_CUSTOM_STATION_REMOVED = "radio.custom_station_removed"
    RADIO_CUSTOM_STATION_UPDATED = "radio.custom_station_updated"


# Global singleton instance (can be overridden in tests)
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create the global EventBus singleton."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus() -> None:
    """Reset the global EventBus (for testing)."""
    global _event_bus
    _event_bus = None
