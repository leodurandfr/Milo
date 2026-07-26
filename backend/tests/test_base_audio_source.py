# backend/tests/test_base_audio_source.py
"""
Unit tests for BaseAudioSource.

Tests cover:
- BaseAudioSource inheritance
- Status format
- BaseAudioSource lifecycle
"""
import pytest
from unittest.mock import Mock, AsyncMock
from pydantic import BaseModel, Field

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState


class _ValueParams(BaseModel):
    value: int = Field(ge=0)


class ConcreteAudioSource(BaseAudioSource):
    """Concrete implementation for testing."""

    COMMANDS = {"test_command": None, "validated_command": _ValueParams}

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

    async def _handle_command(self, cmd, params):
        if cmd == "test_command":
            return self.success_response("Command executed")
        if cmd == "validated_command":
            return self.success_response("Validated", value=params.value)
        return self.error_response(f"Unhandled command: {cmd}")


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
        """Unknown command is rejected centrally by command()."""
        source = ConcreteAudioSource()

        result = await source.command("unknown", {})

        assert result["success"] is False
        assert "Unknown command" in result["error"]

    @pytest.mark.asyncio
    async def test_none_data_treated_as_empty(self):
        """data=None (explicit {"data": null} on the wire) is coerced to {}."""
        source = ConcreteAudioSource()

        result = await source.command("test_command", None)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_valid_params_reach_handler(self):
        """Validated params are passed to the handler as a typed model."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {"value": 5})

        assert result["success"] is True
        assert result["value"] == 5

    @pytest.mark.asyncio
    async def test_invalid_params_rejected(self):
        """Out-of-range params fail validation and never reach the handler."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {"value": -1})

        assert result["success"] is False
        assert "Invalid parameters" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_required_param_rejected(self):
        """Missing required field fails validation."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {})

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
