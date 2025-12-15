# backend/tests/test_routing_service.py
"""
Unit tests for AudioRoutingService
"""
import pytest
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.domain.audio_state import AudioSource


class TestAudioRoutingService:
    """Tests for the audio routing service"""

    @pytest.fixture
    def mock_systemd_manager(self):
        """Mock of SystemdServiceManager"""
        with patch('backend.infrastructure.services.audio_routing_service.SystemdServiceManager') as mock:
            manager = Mock()
            manager.is_active = AsyncMock(return_value=False)
            manager.start = AsyncMock(return_value=True)
            manager.stop = AsyncMock(return_value=True)
            manager.restart = AsyncMock(return_value=True)
            mock.return_value = manager
            yield manager

    @pytest.fixture
    def routing_service(self, mock_settings_service, mock_systemd_manager):
        """Fixture to create a routing service"""
        service = AudioRoutingService(settings_service=mock_settings_service)
        # Initialize _initial_detection_done to avoid automatic detection
        service._initial_detection_done = True
        return service

    def test_initialization(self, routing_service):
        """Service initialization test"""
        assert routing_service.snapcast_websocket_service is None
        assert routing_service.snapcast_service is None
        assert routing_service.state_machine is None
        # No more self.state - uses state_machine.system_state directly

    def test_to_bool_conversion(self):
        """Test of _to_bool helper for safe boolean conversion"""
        # Test None -> False
        assert AudioRoutingService._to_bool(None) is False

        # Test bool values
        assert AudioRoutingService._to_bool(True) is True
        assert AudioRoutingService._to_bool(False) is False

        # Test string true values
        assert AudioRoutingService._to_bool("true") is True
        assert AudioRoutingService._to_bool("True") is True
        assert AudioRoutingService._to_bool("TRUE") is True
        assert AudioRoutingService._to_bool("1") is True
        assert AudioRoutingService._to_bool("yes") is True
        assert AudioRoutingService._to_bool("on") is True
        assert AudioRoutingService._to_bool("enabled") is True

        # Test string false values
        assert AudioRoutingService._to_bool("false") is False
        assert AudioRoutingService._to_bool("False") is False
        assert AudioRoutingService._to_bool("0") is False
        assert AudioRoutingService._to_bool("no") is False
        assert AudioRoutingService._to_bool("off") is False
        assert AudioRoutingService._to_bool("") is False

        # Test other types
        assert AudioRoutingService._to_bool(1) is True
        assert AudioRoutingService._to_bool(0) is False

    def test_set_plugin_callback(self, routing_service):
        """Plugin callback definition test"""
        callback = lambda source: None
        routing_service.set_plugin_callback(callback)

        assert routing_service.get_plugin == callback

    def test_set_snapcast_websocket_service(self, routing_service):
        """Snapcast WebSocket service definition test"""
        mock_service = Mock()
        routing_service.set_snapcast_websocket_service(mock_service)

        assert routing_service.snapcast_websocket_service == mock_service

    def test_set_snapcast_service(self, routing_service):
        """Snapcast service definition test"""
        mock_service = Mock()
        routing_service.set_snapcast_service(mock_service)

        assert routing_service.snapcast_service == mock_service

    def test_set_state_machine(self, routing_service):
        """State machine definition test"""
        mock_sm = Mock()
        routing_service.set_state_machine(mock_sm)

        assert routing_service.state_machine == mock_sm

    def test_get_state(self, routing_service):
        """State retrieval test - now returns a dict"""
        # Add a mock state_machine
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.dsp_enabled = False
        routing_service.set_state_machine(mock_sm)

        state = routing_service.get_state()

        assert isinstance(state, dict)
        assert 'multiroom_enabled' in state
        assert 'dsp_enabled' in state

    @pytest.mark.asyncio
    async def test_initialize_with_settings(self, routing_service, mock_settings_service, mock_async_lock):
        """Initialization test with settings loading"""
        # Reset the flag
        routing_service._initial_detection_done = False

        # Create a mock state_machine
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.dsp_enabled = False
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        # Use AsyncMock with side_effect for async method
        async def get_setting_side_effect(key):
            return {
                'routing.multiroom_enabled': True,
                'dsp.enabled': False
            }.get(key)

        mock_settings_service.get_setting = AsyncMock(side_effect=get_setting_side_effect)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await routing_service.initialize()

        assert mock_sm.system_state.multiroom_enabled is True
        assert mock_sm.system_state.dsp_enabled is False

    @pytest.mark.asyncio
    async def test_initialize_without_settings_service(self):
        """Initialization test without SettingsService (fallback to defaults)"""
        service = AudioRoutingService(settings_service=None)

        with patch.object(service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await service.initialize()

        # Should use default values
        assert service.multiroom_enabled is False
        assert service.dsp_enabled is False

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_already_enabled(self, routing_service, mock_async_lock):
        """set_multiroom_enabled test when already in desired state (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_multiroom_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_success(self, routing_service, mock_settings_service, mock_async_lock):
        """Successful multiroom activation test"""
        mock_state_machine = Mock()
        mock_state_machine.system_state = Mock()
        mock_state_machine.system_state.multiroom_enabled = False
        mock_state_machine.broadcast_event = AsyncMock()
        mock_state_machine._state_lock = mock_async_lock
        # volume_service is accessed via getattr - set to None to skip volume push
        mock_state_machine.volume_service = None
        routing_service.set_state_machine(mock_state_machine)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, '_transition_to_multiroom', new_callable=AsyncMock, return_value=True):
                with patch.object(routing_service, '_auto_configure_multiroom', new_callable=AsyncMock):
                    result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        assert mock_state_machine.system_state.multiroom_enabled is True
        mock_settings_service.set_setting.assert_called_with('routing.multiroom_enabled', True)

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_failure_rollback(self, routing_service, mock_settings_service, mock_async_lock):
        """Activation failure test with state rollback"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, '_transition_to_multiroom', new_callable=AsyncMock, return_value=False):
                result = await routing_service.set_multiroom_enabled(True)

        assert result is False
        # State should have reverted to False
        assert mock_sm.system_state.multiroom_enabled is False
        # Should NOT have saved
        mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_dsp_enabled_already_enabled(self, routing_service, mock_async_lock):
        """set_dsp_enabled test when already in desired state (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.dsp_enabled = True
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_dsp_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_dsp_enabled_success(self, routing_service, mock_settings_service, mock_async_lock):
        """Successful DSP activation test"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.dsp_enabled = False
        mock_sm._state_lock = mock_async_lock
        mock_sm.broadcast_event = AsyncMock()
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_dsp_enabled(True)

        assert result is True
        assert mock_sm.system_state.dsp_enabled is True
        mock_settings_service.set_setting.assert_called_with('dsp.enabled', True)

    @pytest.mark.asyncio
    async def test_set_dsp_enabled_with_plugin_restart(self, routing_service, mock_plugin, mock_settings_service, mock_async_lock):
        """DSP activation test with active plugin restart"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.dsp_enabled = False
        mock_sm._state_lock = mock_async_lock
        mock_sm.broadcast_event = AsyncMock()
        routing_service.set_state_machine(mock_sm)
        routing_service.set_plugin_callback(lambda source: mock_plugin if source == AudioSource.SPOTIFY else None)

        result = await routing_service.set_dsp_enabled(True, active_source=AudioSource.SPOTIFY)

        assert result is True
        # Note: Plugin restart is no longer done by set_dsp_enabled
        # DSP effects toggle doesn't require plugin restart with CamillaDSP

    @pytest.mark.asyncio
    async def test_update_systemd_environment_validation(self, routing_service):
        """Environment file writing test"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.system_state.dsp_enabled = False
        routing_service.set_state_machine(mock_sm)

        # NEW: test file writing instead of sudo
        # Use mock_open from unittest.mock which supports fileno()
        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):  # Mock fsync too
                    await routing_service._update_systemd_environment()

                    # Check that file was opened
                    assert m.called

    @pytest.mark.asyncio
    async def test_update_systemd_environment_file_content(self, routing_service):
        """Environment file content writing test"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.system_state.dsp_enabled = True
        routing_service.set_state_machine(mock_sm)

        # Test file content
        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):
                    await routing_service._update_systemd_environment()

                    # Check that MILO_MODE=multiroom and MILO_EQUALIZER=_eq are written
                    handle = m()
                    calls = [str(call) for call in handle.write.call_args_list]
                    assert any('MILO_MODE=multiroom' in str(call) for call in calls)
                    assert any('MILO_EQUALIZER=_eq' in str(call) for call in calls)

    @pytest.mark.asyncio
    async def test_get_snapcast_status(self, routing_service, mock_systemd_manager):
        """Snapcast status retrieval test"""
        mock_systemd_manager.is_active = AsyncMock(side_effect=[True, True])

        status = await routing_service.get_snapcast_status()

        assert status["server_active"] is True
        assert status["client_active"] is True
        assert status["multiroom_available"] is True

    @pytest.mark.asyncio
    async def test_get_snapcast_status_partial(self, routing_service, mock_systemd_manager):
        """Snapcast status retrieval test with one service stopped"""
        mock_systemd_manager.is_active = AsyncMock(side_effect=[True, False])

        status = await routing_service.get_snapcast_status()

        assert status["server_active"] is True
        assert status["client_active"] is False
        assert status["multiroom_available"] is False

    @pytest.mark.asyncio
    async def test_transition_to_multiroom(self, routing_service, mock_systemd_manager):
        """Transition to multiroom test"""
        mock_systemd_manager.start = AsyncMock(return_value=True)

        result = await routing_service._transition_to_multiroom()

        assert result is True
        assert mock_systemd_manager.start.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_transition_to_direct(self, routing_service, mock_systemd_manager):
        """Transition to direct mode test"""
        mock_systemd_manager.stop = AsyncMock()

        result = await routing_service._transition_to_direct()

        assert result is True
        assert mock_systemd_manager.stop.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_auto_configure_multiroom(self, routing_service):
        """Automatic multiroom configuration test"""
        mock_snapcast = Mock()
        mock_snapcast.is_available = AsyncMock(return_value=True)
        mock_snapcast.set_all_groups_to_multiroom = AsyncMock()
        routing_service.set_snapcast_service(mock_snapcast)

        await routing_service._auto_configure_multiroom()

        mock_snapcast.set_all_groups_to_multiroom.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_configure_multiroom_timeout(self, routing_service):
        """Automatic multiroom configuration timeout test"""
        mock_snapcast = Mock()
        mock_snapcast.is_available = AsyncMock(return_value=False)  # Never available
        routing_service.set_snapcast_service(mock_snapcast)

        # Should timeout after 10 attempts
        await routing_service._auto_configure_multiroom()

        # Check that we tried multiple times
        assert mock_snapcast.is_available.call_count == 10
