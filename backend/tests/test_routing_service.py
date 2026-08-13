# backend/tests/test_routing_service.py
"""
Unit tests for AudioRoutingService
"""
import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import Mock, AsyncMock, patch
from backend.core.multiroom import AudioRoutingService
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.settings import SettingsWriteError
from backend.tests.conftest import events_of


class _CamillaStub:
    """Lightweight stub for CamillaDSPService used in routing tests.

    Mimics the effects_enabled property + set_effects_enabled cache writer
    surface that AudioRoutingService talks to.
    """

    def __init__(self, enabled: bool = False):
        self._effects_enabled = enabled
        self.bypass_effects = AsyncMock(return_value=True)
        self.restore_effects = AsyncMock(return_value=True)

    @property
    def effects_enabled(self) -> bool:
        return self._effects_enabled

    def set_effects_enabled(self, value: bool) -> None:
        self._effects_enabled = bool(value)


def _seed_multiroom(mock_settings_service, value: bool) -> None:
    """Set the persisted multiroom flag in the mock settings storage.

    AudioRoutingService.multiroom_enabled reads from
    ``settings_service.get_setting_sync('routing.multiroom_enabled')`` since
    Phase 3 (no in-memory cache). Tests that previously mutated
    ``routing_service._multiroom_enabled`` now seed the storage instead.
    """
    mock_settings_service._storage['routing.multiroom_enabled'] = value


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
        """Fixture to create a routing service.

        Seeds the persistent multiroom flag to False so the multiroom_enabled
        property reads False by default. Tests that need a different starting
        state call ``_seed_multiroom(mock_settings_service, True)``.
        """
        service = AudioRoutingService(settings_service=mock_settings_service, systemd_manager=mock_systemd_manager)
        # Skip async detection in tests
        service._initial_detection_done = True
        # Set up state machine (normally done via set_state_machine())
        mock_state_machine = Mock()
        _transition_lock = asyncio.Lock()

        @asynccontextmanager
        async def _exclusive_transition():
            async with _transition_lock:
                yield

        mock_state_machine.exclusive_transition = _exclusive_transition
        mock_state_machine.broadcast = AsyncMock()
        mock_state_machine.update_source_state = AsyncMock()
        service.state_machine = mock_state_machine
        # Wire a camilladsp stub so equalizer_effects_enabled property works
        service.camilladsp_service = _CamillaStub()
        # Default initial state: multiroom OFF (property reads from storage)
        _seed_multiroom(mock_settings_service, False)
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

    def test_set_state_machine(self, routing_service):
        """State machine definition test"""
        mock_sm = Mock()
        routing_service.set_state_machine(mock_sm)

        assert routing_service.state_machine == mock_sm

    def test_multiroom_enabled_property_reads_from_settings(self, routing_service, mock_settings_service):
        """Property reads through to settings.get_setting_sync (no in-memory cache)."""
        _seed_multiroom(mock_settings_service, False)
        assert routing_service.multiroom_enabled is False

        _seed_multiroom(mock_settings_service, True)
        assert routing_service.multiroom_enabled is True

    def test_multiroom_enabled_returns_false_without_settings_service(self):
        """Without a settings service the property degrades to False safely."""
        service = AudioRoutingService(settings_service=None)
        assert service.multiroom_enabled is False

    def test_get_state(self, routing_service, mock_settings_service):
        """State retrieval returns dict reading from settings + camilladsp."""
        _seed_multiroom(mock_settings_service, False)
        routing_service.camilladsp_service = _CamillaStub(enabled=False)

        state = routing_service.get_state()

        assert isinstance(state, dict)
        assert state['multiroom_enabled'] is False
        assert state['equalizer_effects_enabled'] is False

    @pytest.mark.asyncio
    async def test_initialize_with_settings(self, routing_service, mock_settings_service):
        """Initialization warms settings cache and lets the property read True."""
        routing_service._initial_detection_done = False
        _seed_multiroom(mock_settings_service, True)

        with patch.object(routing_service, 'regenerate_env_files'):
            with patch.object(routing_service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await routing_service.initialize()

        assert routing_service.multiroom_enabled is True
        assert routing_service._initial_detection_done is True

    @pytest.mark.asyncio
    async def test_initialize_without_settings_service(self):
        """Without a settings service, initialize() still runs and property reads False."""
        service = AudioRoutingService(settings_service=None)

        with patch.object(service, 'regenerate_env_files'):
            with patch.object(service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await service.initialize()

        assert service.multiroom_enabled is False
        assert service.equalizer_effects_enabled is False

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_already_enabled(self, routing_service, mock_settings_service):
        """Idempotent no-op when settings already match the target."""
        _seed_multiroom(mock_settings_service, True)

        result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        # No write occurred since we were already in the target state
        mock_settings_service.set_setting_strict.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_idempotent_repeated_calls(self, routing_service, mock_settings_service):
        """A second call with the same target after a successful first call is a no-op."""
        _seed_multiroom(mock_settings_service, False)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock):
                with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock):
                    first = await routing_service.set_multiroom_enabled(True)
                    second = await routing_service.set_multiroom_enabled(True)

        assert first is True and second is True
        # set_setting_strict only invoked on the first (real) transition
        assert mock_settings_service.set_setting_strict.call_count == 1

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_success(self, routing_service, mock_settings_service):
        """Successful multiroom activation: persists via set_setting_strict, broadcasts state."""
        _seed_multiroom(mock_settings_service, False)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock) as mock_apply:
                with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock):
                    result = await routing_service.set_multiroom_enabled(True)

        assert result is True
        assert routing_service.multiroom_enabled is True
        # Strict (failure-loud) write API used since Phase 3
        mock_settings_service.set_setting_strict.assert_called_once_with('routing.multiroom_enabled', True)
        # _apply_transition was invoked once with the target
        mock_apply.assert_awaited_once()
        # Final state broadcast carries the multiroom_changed discriminator
        broadcast_calls = events_of(routing_service.state_machine.broadcast,
                                    "system", "state_changed")
        assert any(e.multiroom_changed is True for e in broadcast_calls)

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_apply_failure_does_not_persist_settings(
        self, routing_service, mock_settings_service
    ):
        """_apply_transition failure aborts before set_setting_strict — settings stays old."""
        _seed_multiroom(mock_settings_service, False)

        with patch.object(routing_service, '_apply_transition',
                          AsyncMock(side_effect=RuntimeError("snapcast boom"))):
            with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock) as mock_best_effort:
                result = await routing_service.set_multiroom_enabled(True)

        assert result is False
        # Property still reads False — settings untouched
        assert routing_service.multiroom_enabled is False
        mock_settings_service.set_setting_strict.assert_not_called()
        # PHASE 3 (best-effort post-transition) must be skipped on failure
        mock_best_effort.assert_not_called()
        # multiroom_error event broadcast
        error_events = events_of(routing_service.state_machine.broadcast,
                                 "routing", "multiroom_error")
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_settings_strict_failure_returns_false(
        self, routing_service, mock_settings_service
    ):
        """set_setting_strict raising SettingsWriteError causes the transition to fail loudly.

        Critical: settings stays at the old value AND the failure is surfaced
        (result=False, multiroom_error broadcast). Before Phase 3 the lossy
        set_setting swallowed errors and returned True with split-brain state.
        """
        _seed_multiroom(mock_settings_service, False)
        mock_settings_service.set_setting_strict = AsyncMock(
            side_effect=SettingsWriteError("disk full")
        )

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock):
                with patch.object(routing_service, '_post_transition_setup_best_effort', new_callable=AsyncMock) as mock_best_effort:
                    result = await routing_service.set_multiroom_enabled(True)

        assert result is False
        # Settings was never written successfully — property reads old
        assert routing_service.multiroom_enabled is False
        # Post-transition skipped
        mock_best_effort.assert_not_called()
        # multiroom_error broadcast
        error_events = events_of(routing_service.state_machine.broadcast,
                                 "routing", "multiroom_error")
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_followup_failure_is_best_effort(
        self, routing_service, mock_settings_service
    ):
        """A post-transition WS/volume hiccup when enabling is self-healing: it
        is logged but must NOT fail the transition — the mode is committed, so
        the call returns True and still broadcasts system/state_changed (so the
        UI toggle and full_state reflect reality). No multiroom_error."""
        _seed_multiroom(mock_settings_service, False)

        # Wire a WS service that raises on start_connection (worst-case followup).
        ws_service = Mock()
        ws_service.start_connection = AsyncMock(side_effect=RuntimeError("ws boom"))
        ws_service.stop_connection = AsyncMock()
        ws_service.wait_for_ready = AsyncMock(return_value=True)
        routing_service.set_snapcast_websocket_service(ws_service)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with patch.object(routing_service, '_apply_transition', new_callable=AsyncMock):
                result = await routing_service.set_multiroom_enabled(True)

        # Enable succeeds — the physical mode switched.
        assert result is True
        assert routing_service.multiroom_enabled is True
        mock_settings_service.set_setting_strict.assert_called_once_with('routing.multiroom_enabled', True)
        # state_changed broadcast so the UI toggle / full_state are truthful.
        state_changed = events_of(routing_service.state_machine.broadcast,
                                  "system", "state_changed")
        assert len(state_changed) == 1
        assert state_changed[0].multiroom_changed is True
        # A self-healing followup hiccup does not raise a user-facing error.
        error_events = events_of(routing_service.state_machine.broadcast,
                                 "routing", "multiroom_error")
        assert not error_events

    @pytest.mark.asyncio
    async def test_detect_initial_state_failure_keeps_flag_false(self, routing_service, mock_settings_service):
        """Init-retry test: a transient failure in _detect_initial_state must NOT
        flip _initial_detection_done to True, so subsequent calls retry."""
        routing_service._initial_detection_done = False
        # Make the very first settings read raise — covers a transient I/O fault
        mock_settings_service.get_setting = AsyncMock(side_effect=RuntimeError("settings boom"))

        await routing_service._detect_initial_state()

        # Flag MUST stay False so the next caller retries detection
        assert routing_service._initial_detection_done is False

    @pytest.mark.asyncio
    async def test_set_equalizer_effects_enabled_already_enabled(self, routing_service):
        """set_equalizer_effects_enabled test when already in desired state (no-op)"""
        routing_service.camilladsp_service = _CamillaStub(enabled=True)

        result = await routing_service.set_equalizer_effects_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_equalizer_effects_enabled_success(self, routing_service, mock_settings_service):
        """Successful Equalizer effects activation test"""
        routing_service.camilladsp_service = _CamillaStub(enabled=False)

        result = await routing_service.set_equalizer_effects_enabled(True)

        assert result is True
        assert routing_service.equalizer_effects_enabled is True
        mock_settings_service.set_setting.assert_called_with('routing.equalizer_effects_enabled', True)

    def test_regenerate_env_files_writes_routing_env_from_settings(
        self, routing_service, mock_settings_service
    ):
        """regenerate_env_files (Phase 4 helper) writes routing.env derived from settings.

        Replaces the old _update_systemd_environment helper, which only wrote
        routing.env. The new helper writes all three env files; this test
        focuses on the routing.env content to keep coverage of the same
        invariant (settings → MILO_MODE).
        """
        _seed_multiroom(mock_settings_service, True)

        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):
                    routing_service.regenerate_env_files()

                    assert m.called
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

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            await routing_service._apply_transition(True)

        assert mock_systemd_manager.start.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_apply_transition_to_direct(self, routing_service, mock_systemd_manager):
        """_apply_transition(enabled=False) stops both snapcast services."""
        mock_systemd_manager.stop = AsyncMock(return_value=True)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            await routing_service._apply_transition(False)

        assert mock_systemd_manager.stop.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_apply_transition_raises_on_snapcast_failure(self, routing_service, mock_systemd_manager):
        """_apply_transition raises RuntimeError when snapcast services fail to start."""
        mock_systemd_manager.start = AsyncMock(return_value=False)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with pytest.raises(RuntimeError):
                await routing_service._apply_transition(True)

    @pytest.mark.asyncio
    async def test_apply_transition_raises_on_snapcast_stop_failure(self, routing_service, mock_systemd_manager):
        """_apply_transition raises RuntimeError when snapcast services fail to stop.

        Symmetric to the start-failure case. A silent stop would leave
        snapclient holding hw:Loopback,0,0 while routing.env flips to direct
        — the original 2026-05-13 incident class.
        """
        mock_systemd_manager.stop = AsyncMock(return_value=False)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate') as mock_regen:
            with pytest.raises(RuntimeError):
                await routing_service._apply_transition(False)

        # routing.env must NOT have been written — _stop_snapcast raised before step 4
        mock_regen.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_transition_regenerates_env_before_source_acquire(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """routing.env must be regenerated BEFORE the source re-acquires its ALSA
        device so the source unit picks up the new MILO_MODE when systemd starts it."""
        mock_systemd_manager.start = AsyncMock(return_value=True)
        routing_service.set_source_callback(lambda source: mock_source if source == AudioSource.SPOTIFY else None)

        # Track ordering: regenerate vs source re-acquire
        order: list[str] = []

        def _regen(_):
            order.append("regenerate")

        async def _acquire():
            order.append("source.acquire")
            return True

        mock_source.acquire_after_reroute = AsyncMock(side_effect=_acquire)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate', side_effect=_regen):
            await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

        assert order == ["regenerate", "source.acquire"]

    @pytest.mark.asyncio
    async def test_apply_transition_calls_release_not_stop(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """_apply_transition must release via release_for_reroute(), not stop(),
        so a source whose upstream link lives in a separate process from the ALSA
        writer (Bluetooth) keeps that link alive across a multiroom toggle."""
        mock_systemd_manager.start = AsyncMock(return_value=True)
        routing_service.set_source_callback(
            lambda source: mock_source if source == AudioSource.SPOTIFY else None
        )

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

        mock_source.release_for_reroute.assert_awaited_once()
        mock_source.acquire_after_reroute.assert_awaited_once()
        mock_source.stop.assert_not_called()
        mock_source.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_transition_releases_the_starting_state_on_failure(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """A failed reroute must not leave the card on "Starting" forever.

        Step 1 publishes STARTING and the caller only broadcasts multiroom_error
        afterwards, so nothing else republishes on this path — and STARTING is
        not in IDLE_STATES, so the 12 h inactivity sweep never clears it either.
        """
        mock_systemd_manager.start = AsyncMock(return_value=False)
        routing_service.set_source_callback(
            lambda source: mock_source if source == AudioSource.SPOTIFY else None
        )

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            with pytest.raises(RuntimeError):
                await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

        published = [
            entry.kwargs["new_state"]
            for entry in routing_service.state_machine.update_source_state.await_args_list
        ]
        assert published[0] == SourceState.STARTING
        assert published[-1] == mock_source.state

    @pytest.mark.asyncio
    async def test_apply_transition_republishes_when_acquire_returns_false(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """A best-effort step-5 failure must still replace the STARTING of step 1.

        The success path relies on the source's own start broadcast to clear it.
        A source that merely returns False never emits one, and the outer except
        does not fire because step 5 is non-fatal — so the card spun for the rest
        of the session, and re-tapping the source was a no-op because the state
        machine already believed it was starting.
        """
        mock_systemd_manager.start = AsyncMock(return_value=True)
        routing_service.set_source_callback(
            lambda source: mock_source if source == AudioSource.SPOTIFY else None
        )
        mock_source.acquire_after_reroute = AsyncMock(return_value=False)

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

        published = [
            entry.kwargs["new_state"]
            for entry in routing_service.state_machine.update_source_state.await_args_list
        ]
        assert published[0] == SourceState.STARTING
        assert published[-1] == mock_source.state

    @pytest.mark.asyncio
    async def test_apply_transition_republishes_when_acquire_raises(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """The second step-5 branch owes the same republish as the first.

        Step 5 catches its own exception, so a raising source leaves the outer
        handler untouched and lands on exactly the stuck STARTING above.
        """
        mock_systemd_manager.start = AsyncMock(return_value=True)
        routing_service.set_source_callback(
            lambda source: mock_source if source == AudioSource.SPOTIFY else None
        )
        mock_source.acquire_after_reroute = AsyncMock(side_effect=RuntimeError("source boom"))

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

        published = [
            entry.kwargs["new_state"]
            for entry in routing_service.state_machine.update_source_state.await_args_list
        ]
        assert published[0] == SourceState.STARTING
        assert published[-1] == mock_source.state

    @pytest.mark.asyncio
    async def test_apply_transition_source_acquire_failure_is_non_fatal(
        self, routing_service, mock_systemd_manager, mock_source
    ):
        """A failing source re-acquire no longer fails the transition (Phase 3)."""
        mock_systemd_manager.start = AsyncMock(return_value=True)
        routing_service.set_source_callback(lambda source: mock_source if source == AudioSource.SPOTIFY else None)
        mock_source.acquire_after_reroute = AsyncMock(side_effect=RuntimeError("source boom"))

        with patch('backend.core.multiroom.routing.RoutingEnv.regenerate'):
            # Should NOT raise — source failure is best-effort
            await routing_service._apply_transition(True, active_source=AudioSource.SPOTIFY)

    @pytest.mark.asyncio
    async def test_sync_snapcast_state_starts_when_enabled_and_down(self, routing_service, mock_settings_service, mock_systemd_manager):
        """Phase 2: settings says multiroom=true and snapcast is down → reconcile starts both."""
        _seed_multiroom(mock_settings_service, True)
        mock_systemd_manager.is_active = AsyncMock(return_value=False)
        mock_systemd_manager.start = AsyncMock(return_value=True)

        await routing_service._sync_snapcast_state()

        assert mock_systemd_manager.start.call_count == 2
        start_targets = [c.args[0] for c in mock_systemd_manager.start.call_args_list]
        assert "milo-snapserver-multiroom.service" in start_targets
        assert "milo-snapclient-multiroom.service" in start_targets

    @pytest.mark.asyncio
    async def test_sync_snapcast_state_stops_when_disabled_and_up(self, routing_service, mock_settings_service, mock_systemd_manager):
        """Phase 2: settings says multiroom=false but snapcast is up (tamper / leftover
        WantedBy symlink) → reconcile stops both services."""
        _seed_multiroom(mock_settings_service, False)
        mock_systemd_manager.is_active = AsyncMock(return_value=True)
        mock_systemd_manager.stop = AsyncMock(return_value=True)

        await routing_service._sync_snapcast_state()

        assert mock_systemd_manager.stop.call_count == 2
        stop_targets = [c.args[0] for c in mock_systemd_manager.stop.call_args_list]
        assert "milo-snapserver-multiroom.service" in stop_targets
        assert "milo-snapclient-multiroom.service" in stop_targets

    @pytest.mark.asyncio
    async def test_sync_snapcast_state_stops_partial_when_disabled(self, routing_service, mock_settings_service, mock_systemd_manager):
        """Phase 2: settings says multiroom=false and only one of {server, client} is up
        (split state) → reconcile still stops both. Previously this path was skipped
        because the reconcile gated on (server AND client) running."""
        _seed_multiroom(mock_settings_service, False)
        # server up, client down — was previously misread as "already coherent"
        mock_systemd_manager.is_active = AsyncMock(side_effect=[True, False])
        mock_systemd_manager.stop = AsyncMock(return_value=True)

        await routing_service._sync_snapcast_state()

        assert mock_systemd_manager.stop.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_snapcast_state_noop_when_coherent(self, routing_service, mock_settings_service, mock_systemd_manager):
        """Phase 2: settings says multiroom=false and snapcast is already down → no calls."""
        _seed_multiroom(mock_settings_service, False)
        mock_systemd_manager.is_active = AsyncMock(return_value=False)
        mock_systemd_manager.start = AsyncMock(return_value=True)
        mock_systemd_manager.stop = AsyncMock(return_value=True)

        await routing_service._sync_snapcast_state()

        mock_systemd_manager.start.assert_not_called()
        mock_systemd_manager.stop.assert_not_called()
