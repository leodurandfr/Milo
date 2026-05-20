# backend/tests/test_base_audio_source.py
"""
Unit tests for BaseAudioSource.

Tests cover:
- BaseAudioSource inheritance
- Status format
- BaseAudioSource lifecycle
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState


class ConcreteAudioSource(BaseAudioSource):
    """Concrete implementation for testing."""

    def __init__(self, start_success=True, stop_success=True):
        super().__init__(
            source_id="test",
            service_name="milo-test",
        )
        self._start_success = start_success
        self._stop_success = stop_success
        self.start_called = False
        self.stop_called = False

    async def _do_start(self) -> bool:
        self.start_called = True
        if self._start_success:
            self.set_state(SourceState.ACTIVE, {"connected": True})
        return self._start_success

    async def _do_stop(self) -> bool:
        self.stop_called = True
        return self._stop_success

    async def _get_status(self):
        return {"custom_field": "value"}

    async def _handle_command(self, cmd, data):
        if cmd == "test_command":
            return self.success_response("Command executed")
        return self.error_response(f"Unknown command: {cmd}")


class TestBaseAudioSourceLifecycle:
    """Test BaseAudioSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self):
        """Test successful start."""
        source = ConcreteAudioSource()

        result = await source.start()

        assert result is True
        assert source.start_called
        assert source.state == SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_start_failure(self):
        """Test failed start."""
        source = ConcreteAudioSource(start_success=False)

        result = await source.start()

        assert result is False
        assert source.state == SourceState.ERROR

    @pytest.mark.asyncio
    async def test_stop_success(self):
        """Test successful stop."""
        source = ConcreteAudioSource()
        await source.start()

        result = await source.stop()

        assert result is True
        assert source.stop_called
        assert source.state == SourceState.WAITING

    @pytest.mark.asyncio
    async def test_stop_failure(self):
        """Test failed stop."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source.stop()

        assert result is False

    @pytest.mark.asyncio
    async def test_do_restart_success(self):
        """Test the default _do_restart() (stop + start) behind _on_auto_stop."""
        source = ConcreteAudioSource()
        await source.start()
        source.start_called = False
        source.stop_called = False

        result = await source._do_restart()

        assert result is True
        assert source.stop_called
        assert source.start_called

    @pytest.mark.asyncio
    async def test_do_restart_failure_on_stop(self):
        """Test _do_restart() fails if stop fails."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source._do_restart()

        assert result is False


class TestBaseAudioSourceStatus:
    """Test BaseAudioSource status method."""

    @pytest.mark.asyncio
    async def test_status_format(self):
        """Test status returns standard format."""
        source = ConcreteAudioSource()

        with patch.object(source, '_is_service_active', return_value=True):
            status = await source.status()

        assert "state" in status
        assert "service_active" in status
        assert "metadata" in status
        assert "error" in status
        assert "custom_field" in status  # From _get_status

    @pytest.mark.asyncio
    async def test_status_after_start(self):
        """Test status reflects started state."""
        source = ConcreteAudioSource()

        with patch.object(source, '_is_service_active', return_value=True):
            await source.start()
            status = await source.status()

        assert status["state"] == SourceState.ACTIVE.value
        assert status["metadata"]["connected"] is True

    @pytest.mark.asyncio
    async def test_status_after_error(self):
        """Test status reflects error state."""
        source = ConcreteAudioSource(start_success=False)

        with patch.object(source, '_is_service_active', return_value=False):
            await source.start()
            status = await source.status()

        assert status["state"] == SourceState.ERROR.value
        assert status["error"] is not None


class TestBaseAudioSourceCommand:
    """Test BaseAudioSource command method."""

    @pytest.mark.asyncio
    async def test_known_command(self):
        """Test handling known command."""
        source = ConcreteAudioSource()

        result = await source.command("test_command", {})

        assert result["success"] is True
        assert "message" in result

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        """Test handling unknown command."""
        source = ConcreteAudioSource()

        result = await source.command("unknown", {})

        assert result["success"] is False
        assert "error" in result


class TestBaseAudioSourceHelpers:
    """Test BaseAudioSource helper methods."""

    def test_success_response(self):
        """Test success_response helper."""
        source = ConcreteAudioSource()

        response = source.success_response("Test message", extra="data")

        assert response["success"] is True
        assert response["message"] == "Test message"
        assert response["extra"] == "data"

    def test_success_response_no_message(self):
        """Test success_response without message."""
        source = ConcreteAudioSource()

        response = source.success_response()

        assert response["success"] is True
        assert "message" not in response

    def test_error_response(self):
        """Test error_response helper."""
        source = ConcreteAudioSource()

        response = source.error_response("Test error", code=500)

        assert response["success"] is False
        assert response["error"] == "Test error"
        assert response["code"] == 500

    def test_set_state(self):
        """Test set_state helper."""
        source = ConcreteAudioSource()

        source.set_state(SourceState.ACTIVE, {"key": "value"})

        assert source.state == SourceState.ACTIVE
        assert source.metadata["key"] == "value"


class TestSourceStateValues:
    """Test SourceState enum values."""

    def test_state_values(self):
        """Test state values match expected strings."""
        assert SourceState.STARTING.value == "starting"
        assert SourceState.WAITING.value == "waiting"
        assert SourceState.ACTIVE.value == "active"
        assert SourceState.ERROR.value == "error"


class TestBaseAudioSourceServiceManager:
    """Test BaseAudioSource systemd service management."""

    @pytest.mark.asyncio
    async def test_start_service(self):
        """Test _start_service helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.start = AsyncMock(return_value=True)

        result = await source._start_service()

        assert result is True
        source._service_manager.start.assert_called_once_with("milo-test")

    @pytest.mark.asyncio
    async def test_stop_service(self):
        """Test _stop_service helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.stop = AsyncMock(return_value=True)

        result = await source._stop_service()

        assert result is True
        source._service_manager.stop.assert_called_once_with("milo-test")

    @pytest.mark.asyncio
    async def test_is_service_active(self):
        """Test _is_service_active helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.is_active = AsyncMock(return_value=True)

        result = await source._is_service_active()

        assert result is True


class TestBaseAudioSourceProperties:
    """Test BaseAudioSource properties."""

    def test_state_property(self):
        """Test state property."""
        source = ConcreteAudioSource()

        assert source.state == SourceState.WAITING

        source._state = SourceState.ACTIVE
        assert source.state == SourceState.ACTIVE

    def test_metadata_property_returns_copy(self):
        """Test metadata property returns a copy."""
        source = ConcreteAudioSource()
        source._metadata = {"key": "value"}

        metadata = source.metadata
        metadata["new_key"] = "new_value"

        # Original should not be modified
        assert "new_key" not in source._metadata


class TestBaseAudioSourceInheritance:
    """Test that BaseAudioSource subclasses are properly typed."""

    def test_concrete_source_is_base_audio_source(self):
        """Test ConcreteAudioSource inherits from BaseAudioSource."""
        source = ConcreteAudioSource()
        assert isinstance(source, BaseAudioSource)

    def test_base_source_has_required_attributes(self):
        """Test BaseAudioSource has required attributes."""
        source = ConcreteAudioSource()

        assert hasattr(source, 'source_id')
        assert hasattr(source, 'service_name')
        assert source.source_id == "test"
        assert source.service_name == "milo-test"
