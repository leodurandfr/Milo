# backend/tests/test_service_registry.py
"""
Unit tests for ServiceRegistry.

Tests cover:
- Lazy service creation (AC1)
- Reset for test isolation (AC2)
- Factory pattern (AC3)
- Override for mocks (AC5)
"""
import pytest
from unittest.mock import Mock, AsyncMock

from backend.core.registry import (
    ServiceRegistry,
    get_registry,
    reset_registry,
    create_new_registry
)


class TestServiceRegistryBasics:
    """Test basic registry operations."""

    def test_register_factory(self):
        """Test factory registration."""
        registry = ServiceRegistry()

        registry.register("test_service", lambda: "instance")

        assert registry.has("test_service")

    def test_get_creates_instance(self):
        """Test that get() creates instance via factory."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: {"created": True})

        instance = registry.get("test_service")

        assert instance == {"created": True}

    def test_get_returns_singleton(self):
        """Test that get() returns same instance on subsequent calls."""
        registry = ServiceRegistry()
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return {"id": call_count}

        registry.register("test_service", factory)

        instance1 = registry.get("test_service")
        instance2 = registry.get("test_service")

        assert instance1 is instance2
        assert call_count == 1  # Factory called only once

    def test_get_unknown_service_raises(self):
        """Test that get() raises KeyError for unknown service."""
        registry = ServiceRegistry()

        with pytest.raises(KeyError) as exc_info:
            registry.get("unknown_service")

        assert "unknown_service" in str(exc_info.value)

    def test_has_returns_false_for_unknown(self):
        """Test has() returns False for unknown service."""
        registry = ServiceRegistry()

        assert registry.has("unknown") is False

    def test_is_created_false_before_get(self):
        """Test is_created() returns False before get()."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "instance")

        assert registry.is_created("test_service") is False

    def test_is_created_true_after_get(self):
        """Test is_created() returns True after get()."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "instance")

        registry.get("test_service")

        assert registry.is_created("test_service") is True


class TestServiceRegistryReset:
    """Test reset functionality for test isolation (AC2)."""

    def test_reset_clears_instances(self):
        """Test reset() clears all cached instances."""
        registry = ServiceRegistry()
        registry.register("service1", lambda: "instance1")
        registry.register("service2", lambda: "instance2")

        # Create instances
        registry.get("service1")
        registry.get("service2")

        # Reset
        registry.reset()

        # Verify cleared
        assert registry.is_created("service1") is False
        assert registry.is_created("service2") is False

    def test_reset_clears_overrides(self):
        """Test reset() clears all overrides."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "real")
        registry.override("test_service", "mock")

        registry.reset()

        # Override should be gone, should use factory
        instance = registry.get("test_service")
        assert instance == "real"

    def test_reset_keeps_factories(self):
        """Test reset() keeps factory registrations."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "instance")
        registry.get("test_service")

        registry.reset()

        # Should be able to create again
        instance = registry.get("test_service")
        assert instance == "instance"

    def test_clear_overrides_keeps_instances(self):
        """Test clear_overrides() keeps existing instances."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "real")

        # Create instance first
        instance1 = registry.get("test_service")
        # Add override
        registry.override("test_service", "mock")
        # Clear only overrides
        registry.clear_overrides()

        # Should get original instance (not recreate)
        instance2 = registry.get("test_service")
        assert instance2 is instance1

    def test_clear_instance_specific(self):
        """Test clear_instance() clears specific service."""
        registry = ServiceRegistry()
        call_count = {"service1": 0, "service2": 0}

        registry.register("service1", lambda: (call_count.__setitem__("service1", call_count["service1"] + 1), "s1")[1])
        registry.register("service2", lambda: (call_count.__setitem__("service2", call_count["service2"] + 1), "s2")[1])

        registry.get("service1")
        registry.get("service2")

        # Clear only service1
        registry.clear_instance("service1")

        # service1 should be recreated, service2 should not
        registry.get("service1")
        registry.get("service2")

        assert call_count["service1"] == 2
        assert call_count["service2"] == 1


class TestServiceRegistryOverride:
    """Test override functionality for mocking (AC5)."""

    def test_override_replaces_factory(self):
        """Test override takes precedence over factory."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "real")
        mock = Mock()

        registry.override("test_service", mock)

        instance = registry.get("test_service")
        assert instance is mock

    def test_override_replaces_existing_instance(self):
        """Test override takes precedence over existing instance."""
        registry = ServiceRegistry()
        registry.register("test_service", lambda: "real")

        # Create instance first
        real_instance = registry.get("test_service")
        assert real_instance == "real"

        # Override
        mock = Mock()
        registry.override("test_service", mock)

        # Now should get mock
        instance = registry.get("test_service")
        assert instance is mock

    def test_override_without_factory(self):
        """Test override works even without factory registered."""
        registry = ServiceRegistry()
        mock = Mock()

        registry.override("unregistered_service", mock)

        instance = registry.get("unregistered_service")
        assert instance is mock

    def test_is_created_true_with_override(self):
        """Test is_created() returns True for overridden services."""
        registry = ServiceRegistry()
        registry.override("test_service", Mock())

        assert registry.is_created("test_service") is True


class TestServiceRegistryWithDependencies:
    """Test registry with service dependencies."""

    def test_dependent_services(self):
        """Test services that depend on other services."""
        registry = ServiceRegistry()

        # Register base service
        registry.register("config", lambda: {"db_url": "localhost"})

        # Register dependent service
        def create_db_service():
            config = registry.get("config")
            return {"connection": config["db_url"]}

        registry.register("database", create_db_service)

        # Get dependent service
        db = registry.get("database")

        assert db["connection"] == "localhost"

    def test_circular_dependency_via_setter(self):
        """Test circular dependencies resolved via setters."""
        registry = ServiceRegistry()

        class ServiceA:
            def __init__(self):
                self.b = None

            def set_b(self, b):
                self.b = b

        class ServiceB:
            def __init__(self):
                self.a = None

            def set_a(self, a):
                self.a = a

        # Register factories
        registry.register("service_a", ServiceA)
        registry.register("service_b", ServiceB)

        # Get instances
        a = registry.get("service_a")
        b = registry.get("service_b")

        # Resolve circular dependency
        a.set_b(b)
        b.set_a(a)

        # Verify
        assert a.b is b
        assert b.a is a


class TestServiceRegistryProperties:
    """Test registry inspection properties."""

    def test_registered_services(self):
        """Test registered_services property."""
        registry = ServiceRegistry()
        registry.register("service1", lambda: "s1")
        registry.register("service2", lambda: "s2")

        services = registry.registered_services

        assert "service1" in services
        assert "service2" in services
        assert len(services) == 2

    def test_created_services(self):
        """Test created_services property."""
        registry = ServiceRegistry()
        registry.register("service1", lambda: "s1")
        registry.register("service2", lambda: "s2")

        # Create only one
        registry.get("service1")

        created = registry.created_services

        assert "service1" in created
        assert "service2" not in created


class TestGlobalRegistry:
    """Test global registry singleton functions."""

    def test_get_registry_returns_singleton(self):
        """Test get_registry() returns same instance."""
        # Reset first to ensure clean state
        create_new_registry()

        registry1 = get_registry()
        registry2 = get_registry()

        assert registry1 is registry2

    def test_reset_registry_clears_instances(self):
        """Test reset_registry() clears global registry."""
        registry = get_registry()
        registry.register("test", lambda: "instance")
        registry.get("test")

        reset_registry()

        assert registry.is_created("test") is False

    def test_create_new_registry_replaces_global(self):
        """Test create_new_registry() creates fresh registry."""
        old_registry = get_registry()
        old_registry.register("old_service", lambda: "old")

        new_registry = create_new_registry()

        assert new_registry is not old_registry
        assert not new_registry.has("old_service")


class TestServiceRegistryWithMocks:
    """Test typical testing patterns with mocks."""

    def test_async_mock_service(self):
        """Test using AsyncMock for async service methods."""
        registry = ServiceRegistry()

        mock_service = Mock()
        mock_service.async_method = AsyncMock(return_value={"status": "ok"})

        registry.override("async_service", mock_service)

        service = registry.get("async_service")
        assert service is mock_service

    def test_mock_with_side_effect(self):
        """Test mock with side_effect for complex behavior."""
        registry = ServiceRegistry()

        mock_service = Mock()
        mock_service.get_data = Mock(side_effect=[
            {"first": True},
            {"second": True},
            Exception("No more data")
        ])

        registry.override("data_service", mock_service)

        service = registry.get("data_service")
        assert service.get_data() == {"first": True}
        assert service.get_data() == {"second": True}

        with pytest.raises(Exception):
            service.get_data()

    def test_fixture_pattern(self):
        """Test typical pytest fixture pattern."""
        # Simulate fixture setup
        registry = create_new_registry()

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value="value")

        registry.override("settings_service", mock_settings)

        # Test code
        settings = registry.get("settings_service")
        assert settings is mock_settings

        # Simulate fixture teardown
        reset_registry()
