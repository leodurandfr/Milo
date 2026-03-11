# backend/tests/test_routing_service.py
"""
Unit tests for AudioRoutingService
"""
import asyncio
import pytest
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.core.multiroom import AudioRoutingService
from backend.core.models.audio_state import AudioSource


class TestAudioRoutingService:
    """Tests for the audio routing service"""

    @pytest.fixture
    def mock_systemd_manager(self):
        """Mock of SystemdServiceManager"""
        with patch('backend.core.multiroom.routing.SystemdServiceManager') as mock:
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
        service = AudioRoutingService(settings_service=mock_settings_service, systemd_manager=mock_systemd_manager)
        # Initialize _initial_detection_done to avoid automatic detection
        service._initial_detection_done = True
        # Set up state machine (normally done via set_state_machine())
        mock_state_machine = Mock()
        mock_state_machine._transition_lock = asyncio.Lock()
        mock_state_machine.update_multiroom_state = AsyncMock()
        mock_state_machine.update_equalizer_effects_state = AsyncMock()
        service.state_machine = mock_state_machine
        return service

    def test_initialization(self, routing_service):
        """Service initialization test"""
        assert routing_service.snapcast_websocket_service is None
        assert routing_service.snapcast_service is None
        # state_machine is set in fixture for _transition() support

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
        mock_sm.system_state.equalizer_effects_enabled = False
        routing_service.set_state_machine(mock_sm)

        state = routing_service.get_state()

        assert isinstance(state, dict)
        assert 'multiroom_enabled' in state
        assert 'equalizer_effects_enabled' in state

    @pytest.mark.asyncio
    async def test_initialize_with_settings(self, routing_service, mock_settings_service):
        """Initialization test with settings loading"""
        # Reset the flag
        routing_service._initial_detection_done = False

        # Create a mock state_machine with AsyncMock for public update methods
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'multiroom_enabled', v)
        )
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)

        # Use AsyncMock with side_effect for async method
        async def get_setting_side_effect(key):
            return {
                'routing.multiroom_enabled': True,
                'equalizer.effects_enabled': False
            }.get(key)

        mock_settings_service.get_setting = AsyncMock(side_effect=get_setting_side_effect)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await routing_service.initialize()

        assert mock_sm.system_state.multiroom_enabled is True
        assert mock_sm.system_state.equalizer_effects_enabled is False

    @pytest.mark.asyncio
    async def test_initialize_without_settings_service(self):
        """Initialization test without SettingsService (fallback to defaults)"""
        service = AudioRoutingService(settings_service=None)

        with patch.object(service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await service.initialize()

        # Should use default values
        assert service.multiroom_enabled is False
        assert service.equalizer_effects_enabled is False

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_already_enabled(self, routing_service):
        """set_multiroom_enabled test when already in desired state (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.update_multiroom_state = AsyncMock()
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_multiroom_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_success(self, routing_service, mock_settings_service):
        """Successful multiroom activation test"""
        mock_state_machine = Mock()
        mock_state_machine.system_state = Mock()
        mock_state_machine.system_state.multiroom_enabled = False
        mock_state_machine.broadcast_event = AsyncMock()
        mock_state_machine.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_state_machine.system_state, 'multiroom_enabled', v)
        )
        routing_service.set_state_machine(mock_state_machine)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, '_transition_to_multiroom', new_callable=AsyncMock, return_value=True):
                result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        assert mock_state_machine.system_state.multiroom_enabled is True
        mock_settings_service.set_setting.assert_called_with('routing.multiroom_enabled', True)

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_failure_rollback(self, routing_service, mock_settings_service):
        """Activation failure test with state rollback"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'multiroom_enabled', v)
        )
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
    async def test_set_equalizer_effects_enabled_already_enabled(self, routing_service):
        """set_equalizer_effects_enabled test when already in desired state (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = True
        mock_sm.update_equalizer_effects_state = AsyncMock()
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_equalizer_effects_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_equalizer_effects_enabled_success(self, routing_service, mock_settings_service):
        """Successful Equalizer effects activation test"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_equalizer_effects_enabled(True)

        assert result is True
        assert mock_sm.system_state.equalizer_effects_enabled is True
        mock_settings_service.set_setting.assert_called_with('equalizer.effects_enabled', True)

    @pytest.mark.asyncio
    async def test_set_equalizer_effects_enabled_with_plugin_restart(self, routing_service, mock_plugin, mock_settings_service):
        """Equalizer effects activation test with active plugin restart"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)
        routing_service.set_plugin_callback(lambda source: mock_plugin if source == AudioSource.SPOTIFY else None)

        result = await routing_service.set_equalizer_effects_enabled(True, active_source=AudioSource.SPOTIFY)

        assert result is True
        # Note: Plugin restart is no longer done by set_equalizer_effects_enabled
        # Equalizer effects toggle doesn't require plugin restart with CamillaDSP

    @pytest.mark.asyncio
    async def test_update_systemd_environment_validation(self, routing_service):
        """Environment file writing test"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.system_state.equalizer_effects_enabled = False
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
        mock_sm.system_state.equalizer_effects_enabled = True
        routing_service.set_state_machine(mock_sm)

        # Test file content
        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):
                    await routing_service._update_systemd_environment()

                    # Check that MILO_MODE=multiroom is written
                    handle = m()
                    calls = [str(call) for call in handle.write.call_args_list]
                    assert any('MILO_MODE=multiroom' in str(call) for call in calls)

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

