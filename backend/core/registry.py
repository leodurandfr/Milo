# backend/core/registry.py
"""
Simple Service Registry for dependency injection.

This registry provides lazy service creation, singleton management,
and easy mock injection for testing. It replaces the more complex
dependency-injector library with a simpler, more maintainable approach.

Usage:
    # Get the global registry
    registry = get_registry()

    # Register a service factory
    registry.register("settings", lambda: SettingsService())

    # Get a service (created on first access)
    settings = registry.get("settings")

    # Override for testing
    registry.override("settings", mock_settings)

    # Reset all services (for test isolation)
    registry.reset()

Testing Pattern:
    @pytest.fixture(autouse=True)
    def clean_registry():
        reset_registry()
        yield
        reset_registry()

    def test_with_mock(mock_service):
        registry = get_registry()
        registry.override("my_service", mock_service)
        # Test code uses mock automatically
"""
from typing import Any, Callable, Dict, Optional, TypeVar, Type
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ServiceRegistry:
    """
    Simple service registry with lazy creation and test support.

    Features:
    - Lazy creation: Services created on first access
    - Singleton pattern: Same instance returned on subsequent calls
    - Override support: Replace services with mocks for testing
    - Reset support: Clear all instances for test isolation
    """

    def __init__(self):
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._instances: Dict[str, Any] = {}
        self._overrides: Dict[str, Any] = {}

    def register(self, name: str, factory: Callable[[], T]) -> None:
        """
        Register a factory function for a service.

        Args:
            name: Service name (e.g., "settings_service")
            factory: Callable that creates the service instance
        """
        self._factories[name] = factory
        logger.debug(f"Registered factory for service: {name}")

    def get(self, name: str) -> Any:
        """
        Get or create a service instance.

        Services are created lazily on first access and cached
        as singletons. Overrides take precedence for testing.

        Args:
            name: Service name

        Returns:
            Service instance

        Raises:
            KeyError: If no factory is registered for the service
        """
        # Check for override first (testing)
        if name in self._overrides:
            return self._overrides[name]

        # Return existing instance
        if name in self._instances:
            return self._instances[name]

        # Create new instance via factory
        if name not in self._factories:
            raise KeyError(f"No factory registered for service: {name}")

        logger.debug(f"Creating service instance: {name}")
        instance = self._factories[name]()
        self._instances[name] = instance
        return instance

    def override(self, name: str, instance: Any) -> None:
        """
        Override a service with a specific instance (for testing).

        Overrides take precedence over both existing instances
        and factory creation.

        Args:
            name: Service name
            instance: Instance to use (typically a mock)
        """
        self._overrides[name] = instance
        logger.debug(f"Override set for service: {name}")

    def has(self, name: str) -> bool:
        """Check if a service factory is registered."""
        return name in self._factories

    def is_created(self, name: str) -> bool:
        """Check if a service instance has been created."""
        return name in self._instances or name in self._overrides

    def reset(self) -> None:
        """
        Reset all instances and overrides (for testing).

        This clears all cached instances and overrides but keeps
        the factory registrations.
        """
        self._instances.clear()
        self._overrides.clear()
        logger.debug("Registry reset: all instances and overrides cleared")

    def clear_overrides(self) -> None:
        """Clear only overrides, keep instances."""
        self._overrides.clear()
        logger.debug("Registry overrides cleared")

    def clear_instance(self, name: str) -> None:
        """
        Clear a specific service instance.

        Useful when a service needs to be recreated with
        different configuration.

        Args:
            name: Service name to clear
        """
        self._instances.pop(name, None)
        self._overrides.pop(name, None)

    @property
    def registered_services(self) -> list:
        """List all registered service names."""
        return list(self._factories.keys())

    @property
    def created_services(self) -> list:
        """List all created service instance names."""
        return list(self._instances.keys())


# Global singleton instance
_registry: Optional[ServiceRegistry] = None


def get_registry() -> ServiceRegistry:
    """
    Get or create the global ServiceRegistry singleton.

    Returns:
        The global ServiceRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = ServiceRegistry()
    return _registry


def reset_registry() -> None:
    """
    Reset the global registry (for testing).

    This clears all instances and overrides but keeps
    factory registrations.
    """
    global _registry
    if _registry is not None:
        _registry.reset()


def create_new_registry() -> ServiceRegistry:
    """
    Create a fresh registry instance (for testing).

    This replaces the global registry with a new empty one.
    Useful for complete isolation in tests.

    Returns:
        New empty ServiceRegistry
    """
    global _registry
    _registry = ServiceRegistry()
    return _registry
