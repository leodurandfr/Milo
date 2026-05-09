# backend/tests/test_routing_service.py
"""
Unit tests for AudioRoutingService
"""
import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
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

    def test_set_source_callback(self, routing_service):
        """Source callback definition test"""
        callback = lambda source: None
        routing_service.set_source_callback(callback)

        assert routing_service.get_source == callback

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

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock):
                with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock):
                    result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        assert mock_state_machine.system_state.multiroom_enabled is True
        # settings.json is the FIRST write in _commit_state — no longer the LAST
        mock_settings_service.set_setting.assert_called_with('routing.multiroom_enabled', True)

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_failure_rollback(self, routing_service, mock_settings_service):
        """Apply-phase failure test with full state + settings + services rollback (symmetry)."""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'multiroom_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)

        # Track _apply_transition calls so we can verify rollback symmetry:
        # call 1 with target=True must raise; call 2 with target=False must run.
        apply_calls = []

        async def apply_side_effect(enabled, active_source=None):
            apply_calls.append(enabled)
            if len(apply_calls) == 1:
                raise RuntimeError("simulated apply failure")
            return None

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate') as mock_regenerate:
            with patch.object(routing_service, '_apply_transition', side_effect=apply_side_effect):
                with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock) as mock_best_effort:
                    result = await routing_service.set_multiroom_enabled(True)

        assert result is False
        # State machine reverted to False
        assert mock_sm.system_state.multiroom_enabled is False
        # _apply_transition called twice: target then rollback to old state
        assert apply_calls == [True, False]
        # routing.env was regenerated to True (commit) AND back to False (rollback)
        regen_args = [c.args[0] for c in mock_regenerate.call_args_list]
        assert regen_args == [True, False]
        # settings.json was written to True (commit), then back to False (rollback)
        set_calls = [c for c in mock_settings_service.set_setting.call_args_list
                     if c.args == ('routing.multiroom_enabled', True) or c.args == ('routing.multiroom_enabled', False)]
        assert any(c.args == ('routing.multiroom_enabled', True) for c in set_calls)
        assert set_calls[-1].args == ('routing.multiroom_enabled', False)
        # PHASE 3 must be skipped on rollback
        mock_best_effort.assert_not_called()
        # multiroom_error event was broadcast
        error_events = [c for c in mock_sm.broadcast_event.call_args_list
                        if c.args[:2] == ("routing", "multiroom_error")]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_best_effort_failure_does_not_fail_transition(
        self, routing_service, mock_settings_service
    ):
        """PHASE 3 (post_transition WS/volume) failure must not fail the transition."""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'multiroom_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)

        # Wire a WS service that raises on start_connection
        ws_service = Mock()
        ws_service.start_connection = AsyncMock(side_effect=RuntimeError("ws boom"))
        ws_service.stop_connection = AsyncMock()
        ws_service.wait_for_ready = AsyncMock(return_value=True)
        routing_service.set_snapcast_websocket_service(ws_service)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock):
                result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        # State committed, ready event still attempted, no rollback
        assert mock_sm.system_state.multiroom_enabled is True
        mock_settings_service.set_setting.assert_called_with('routing.multiroom_enabled', True)

    @pytest.mark.asyncio
    async def test_detect_initial_state_failure_keeps_flag_false(self, routing_service, mock_settings_service):
        """Init-retry test: a transient failure in _detect_initial_state must NOT
        flip _initial_detection_done to True, so subsequent calls retry."""
        # Force _initial_detection_done back to False (fixture sets True)
        routing_service._initial_detection_done = False

        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.update_multiroom_state = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock()
        routing_service.set_state_machine(mock_sm)

        # Make the very first settings read raise — covers a transient I/O fault
        mock_settings_service.get_setting = AsyncMock(side_effect=RuntimeError("settings boom"))

        await routing_service._detect_initial_state()

        # Flag MUST stay False so the next caller retries detection
        assert routing_service._initial_detection_done is False

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
    async def test_set_equalizer_effects_enabled_with_source_restart(self, routing_service, mock_source, mock_settings_service):
        """Equalizer effects activation test with active source restart"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing_service.set_state_machine(mock_sm)
        routing_service.set_source_callback(lambda source: mock_source if source == AudioSource.SPOTIFY else None)

        result = await routing_service.set_equalizer_effects_enabled(True, active_source=AudioSource.SPOTIFY)

        assert result is True
        # Note: Source restart is no longer done by set_equalizer_effects_enabled
        # Equalizer effects toggle doesn't require source restart with CamillaDSP

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
    async def test_apply_transition_to_multiroom(self, routing_service, mock_systemd_manager):
        """_apply_transition(enabled=True) starts both snapcast services."""
        mock_systemd_manager.start = AsyncMock(return_value=True)

        # _apply_transition raises on failure, returns None on success
        await routing_service._apply_transition(True)

        assert mock_systemd_manager.start.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_apply_transition_to_direct(self, routing_service, mock_systemd_manager):
        """_apply_transition(enabled=False) stops both snapcast services."""
        mock_systemd_manager.stop = AsyncMock()

        await routing_service._apply_transition(False)

        assert mock_systemd_manager.stop.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_apply_transition_raises_on_snapcast_failure(self, routing_service, mock_systemd_manager):
        """_apply_transition raises RuntimeError when snapcast services fail to start."""
        mock_systemd_manager.start = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError):
            await routing_service._apply_transition(True)

