# backend/tests/test_audio_source_protocol.py
"""
Unit tests for AudioSource Protocol and BaseAudioSource.

Tests cover:
- Protocol compliance (AC1, AC2)
- Status format (AC3)
- BaseAudioSource lifecycle (AC4)
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.audio_source import (
    AudioSource,
    BaseAudioSource,
    SourceState
)


class TestAudioSourceProtocol:
    """Test AudioSource Protocol compliance."""

    def test_protocol_is_runtime_checkable(self):
        """Test that Protocol is runtime checkable."""
        # Create a class that implements the protocol
        class MockSource:
            source_id = "mock"
            service_name = "milo-mock"

            async def start(self) -> bool:
                return True

            async def stop(self) -> bool:
                return True

            async def restart(self) -> bool:
                return True

            async def status(self):
                return {"state": "ready"}

            async def command(self, cmd, data):
                return {"success": True}

        source = MockSource()

        # Should pass isinstance check
        assert isinstance(source, AudioSource)

    def test_protocol_rejects_incomplete_implementation(self):
        """Test that incomplete implementations fail isinstance check."""
        class IncompleteSource:
            source_id = "incomplete"
            # Missing service_name and methods

        source = IncompleteSource()

        # Should fail isinstance check
        assert not isinstance(source, AudioSource)

    def test_protocol_requires_source_id(self):
        """Test that source_id attribute is required."""
        class MissingSourceId:
            service_name = "test"

            async def start(self):
                return True

            async def stop(self):
                return True

            async def restart(self):
                return True

            async def status(self):
                return {}

            async def command(self, cmd, data):
                return {}

        source = MissingSourceId()
        assert not isinstance(source, AudioSource)

    def test_protocol_requires_service_name(self):
        """Test that service_name attribute is required."""
        class MissingServiceName:
            source_id = "test"

            async def start(self):
                return True

            async def stop(self):
                return True

            async def restart(self):
                return True

            async def status(self):
                return {}

            async def command(self, cmd, data):
                return {}

        source = MissingServiceName()
        assert not isinstance(source, AudioSource)


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
            self.set_state(SourceState.CONNECTED, {"connected": True})
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
        assert source.state == SourceState.CONNECTED

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
        assert source.state == SourceState.READY

    @pytest.mark.asyncio
    async def test_stop_failure(self):
        """Test failed stop."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source.stop()

        assert result is False

    @pytest.mark.asyncio
    async def test_restart_success(self):
        """Test successful restart (default: stop + start)."""
        source = ConcreteAudioSource()
        await source.start()
        source.start_called = False
        source.stop_called = False

        result = await source.restart()

        assert result is True
        assert source.stop_called
        assert source.start_called

    @pytest.mark.asyncio
    async def test_restart_failure_on_stop(self):
        """Test restart fails if stop fails."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source.restart()

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

        assert status["state"] == SourceState.CONNECTED
        assert status["metadata"]["connected"] is True

    @pytest.mark.asyncio
    async def test_status_after_error(self):
        """Test status reflects error state."""
        source = ConcreteAudioSource(start_success=False)

        with patch.object(source, '_is_service_active', return_value=False):
            await source.start()
            status = await source.status()

        assert status["state"] == SourceState.ERROR
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

        source.set_state(SourceState.CONNECTED, {"key": "value"})

        assert source.state == SourceState.CONNECTED
        assert source.metadata["key"] == "value"


class TestSourceStateConstants:
    """Test SourceState constants."""

    def test_state_values(self):
        """Test state constant values."""
        assert SourceState.STARTING == "starting"
        assert SourceState.READY == "ready"
        assert SourceState.CONNECTED == "connected"
        assert SourceState.ERROR == "error"


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

        assert source.state == SourceState.READY

        source._state = SourceState.CONNECTED
        assert source.state == SourceState.CONNECTED

    def test_metadata_property_returns_copy(self):
        """Test metadata property returns a copy."""
        source = ConcreteAudioSource()
        source._metadata = {"key": "value"}

        metadata = source.metadata
        metadata["new_key"] = "new_value"

        # Original should not be modified
        assert "new_key" not in source._metadata


class TestProtocolWithBaseAudioSource:
    """Test that BaseAudioSource subclasses implement AudioSource protocol."""

    def test_concrete_source_implements_protocol(self):
        """Test ConcreteAudioSource implements AudioSource protocol."""
        source = ConcreteAudioSource()

        # Should pass isinstance check for Protocol
        assert isinstance(source, AudioSource)

    def test_base_source_has_required_attributes(self):
        """Test BaseAudioSource has required attributes."""
        source = ConcreteAudioSource()

        assert hasattr(source, 'source_id')
        assert hasattr(source, 'service_name')
        assert source.source_id == "test"
        assert source.service_name == "milo-test"
