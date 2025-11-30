# backend/tests/test_routing_service.py
"""
Tests unitaires pour AudioRoutingService
"""
import pytest
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.services.audio_routing_service import AudioRoutingService
from backend.domain.audio_state import AudioSource


class TestAudioRoutingService:
    """Tests pour le service de routage audio"""

    @pytest.fixture
    def mock_systemd_manager(self):
        """Mock du SystemdServiceManager"""
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
        """Fixture pour créer un routing service"""
        service = AudioRoutingService(settings_service=mock_settings_service)
        # Initialiser _initial_detection_done pour éviter la détection automatique
        service._initial_detection_done = True
        return service

    def test_initialization(self, routing_service):
        """Test de l'initialisation du service"""
        assert routing_service.snapcast_websocket_service is None
        assert routing_service.snapcast_service is None
        assert routing_service.state_machine is None
        # Plus de self.state - utilise directement state_machine.system_state

    def test_set_plugin_callback(self, routing_service):
        """Test de définition du callback des plugins"""
        callback = lambda source: None
        routing_service.set_plugin_callback(callback)

        assert routing_service.get_plugin == callback

    def test_set_snapcast_websocket_service(self, routing_service):
        """Test de définition du service WebSocket Snapcast"""
        mock_service = Mock()
        routing_service.set_snapcast_websocket_service(mock_service)

        assert routing_service.snapcast_websocket_service == mock_service

    def test_set_snapcast_service(self, routing_service):
        """Test de définition du service Snapcast"""
        mock_service = Mock()
        routing_service.set_snapcast_service(mock_service)

        assert routing_service.snapcast_service == mock_service

    def test_set_state_machine(self, routing_service):
        """Test de définition de la state machine"""
        mock_sm = Mock()
        routing_service.set_state_machine(mock_sm)

        assert routing_service.state_machine == mock_sm

    def test_get_state(self, routing_service):
        """Test de récupération de l'état - retourne maintenant un dict"""
        # Ajouter un state_machine mock
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.equalizer_enabled = False
        routing_service.set_state_machine(mock_sm)

        state = routing_service.get_state()

        assert isinstance(state, dict)
        assert 'multiroom_enabled' in state
        assert 'equalizer_enabled' in state

    @pytest.mark.asyncio
    async def test_initialize_with_settings(self, routing_service, mock_settings_service, mock_async_lock):
        """Test de l'initialisation avec chargement des settings"""
        # Réinitialiser le flag
        routing_service._initial_detection_done = False

        # Créer un mock state_machine
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.equalizer_enabled = False
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        mock_settings_service.get_setting = Mock(side_effect=lambda key: {
            'routing.multiroom_enabled': True,
            'routing.equalizer_enabled': False
        }.get(key))

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(routing_service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await routing_service.initialize()

        assert mock_sm.system_state.multiroom_enabled is True
        assert mock_sm.system_state.equalizer_enabled is False

    @pytest.mark.asyncio
    async def test_initialize_without_settings_service(self):
        """Test de l'initialisation sans SettingsService (fallback aux defaults)"""
        service = AudioRoutingService(settings_service=None)

        with patch.object(service, '_update_systemd_environment', new_callable=AsyncMock):
            with patch.object(service, 'get_snapcast_status', new_callable=AsyncMock, return_value={"multiroom_available": False}):
                await service.initialize()

        # Devrait utiliser les valeurs par défaut
        assert service.multiroom_enabled is False
        assert service.equalizer_enabled is False

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_already_enabled(self, routing_service, mock_async_lock):
        """Test de set_multiroom_enabled quand déjà dans l'état souhaité (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_multiroom_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_multiroom_enabled_success(self, routing_service, mock_settings_service, mock_async_lock):
        """Test d'activation réussie du multiroom"""
        mock_state_machine = Mock()
        mock_state_machine.system_state = Mock()
        mock_state_machine.system_state.multiroom_enabled = False
        mock_state_machine.broadcast_event = AsyncMock()
        mock_state_machine._state_lock = mock_async_lock
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
        """Test d'échec d'activation avec rollback de l'état"""
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
        # L'état devrait être revenu à False
        assert mock_sm.system_state.multiroom_enabled is False
        # Ne devrait PAS avoir sauvegardé
        mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_equalizer_enabled_already_enabled(self, routing_service, mock_async_lock):
        """Test de set_equalizer_enabled quand déjà dans l'état souhaité (no-op)"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_enabled = True
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        result = await routing_service.set_equalizer_enabled(True)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_equalizer_enabled_success(self, routing_service, mock_settings_service, mock_async_lock):
        """Test d'activation réussie de l'equalizer"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_enabled = False
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            result = await routing_service.set_equalizer_enabled(True)

        assert result is True
        assert mock_sm.system_state.equalizer_enabled is True
        mock_settings_service.set_setting.assert_called_with('routing.equalizer_enabled', True)

    @pytest.mark.asyncio
    async def test_set_equalizer_enabled_with_plugin_restart(self, routing_service, mock_plugin, mock_async_lock):
        """Test d'activation de l'equalizer avec restart du plugin actif"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_enabled = False
        mock_sm._state_lock = mock_async_lock
        routing_service.set_state_machine(mock_sm)
        routing_service.set_plugin_callback(lambda source: mock_plugin if source == AudioSource.SPOTIFY else None)

        with patch.object(routing_service, '_update_systemd_environment', new_callable=AsyncMock):
            result = await routing_service.set_equalizer_enabled(True, active_source=AudioSource.SPOTIFY)

        assert result is True
        mock_plugin.restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_systemd_environment_validation(self, routing_service):
        """Test d'écriture du fichier d'environnement"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.system_state.equalizer_enabled = False
        routing_service.set_state_machine(mock_sm)

        # NOUVEAU: teste l'écriture du fichier au lieu de sudo
        # Utiliser mock_open de unittest.mock qui supporte fileno()
        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):  # Mock fsync aussi
                    await routing_service._update_systemd_environment()

                    # Vérifier que le fichier a été ouvert
                    assert m.called

    @pytest.mark.asyncio
    async def test_update_systemd_environment_file_content(self, routing_service):
        """Test du contenu du fichier d'environnement écrit"""
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = True
        mock_sm.system_state.equalizer_enabled = True
        routing_service.set_state_machine(mock_sm)

        # Teste le contenu du fichier
        from unittest.mock import mock_open as create_mock_open
        m = create_mock_open()

        with patch('builtins.open', m):
            with patch('os.replace'):
                with patch('os.fsync'):
                    await routing_service._update_systemd_environment()

                    # Vérifier que MILO_MODE=multiroom et MILO_EQUALIZER=_eq sont écrits
                    handle = m()
                    calls = [str(call) for call in handle.write.call_args_list]
                    assert any('MILO_MODE=multiroom' in str(call) for call in calls)
                    assert any('MILO_EQUALIZER=_eq' in str(call) for call in calls)

    @pytest.mark.asyncio
    async def test_get_snapcast_status(self, routing_service, mock_systemd_manager):
        """Test de récupération du statut snapcast"""
        mock_systemd_manager.is_active = AsyncMock(side_effect=[True, True])

        status = await routing_service.get_snapcast_status()

        assert status["server_active"] is True
        assert status["client_active"] is True
        assert status["multiroom_available"] is True

    @pytest.mark.asyncio
    async def test_get_snapcast_status_partial(self, routing_service, mock_systemd_manager):
        """Test de récupération du statut snapcast avec un service arrêté"""
        mock_systemd_manager.is_active = AsyncMock(side_effect=[True, False])

        status = await routing_service.get_snapcast_status()

        assert status["server_active"] is True
        assert status["client_active"] is False
        assert status["multiroom_available"] is False

    @pytest.mark.asyncio
    async def test_transition_to_multiroom(self, routing_service, mock_systemd_manager):
        """Test de transition vers le multiroom"""
        mock_systemd_manager.start = AsyncMock(return_value=True)

        result = await routing_service._transition_to_multiroom()

        assert result is True
        assert mock_systemd_manager.start.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_transition_to_direct(self, routing_service, mock_systemd_manager):
        """Test de transition vers le mode direct"""
        mock_systemd_manager.stop = AsyncMock()

        result = await routing_service._transition_to_direct()

        assert result is True
        assert mock_systemd_manager.stop.call_count == 2  # server + client

    @pytest.mark.asyncio
    async def test_auto_configure_multiroom(self, routing_service):
        """Test de configuration automatique du multiroom"""
        mock_snapcast = Mock()
        mock_snapcast.is_available = AsyncMock(return_value=True)
        mock_snapcast.set_all_groups_to_multiroom = AsyncMock()
        routing_service.set_snapcast_service(mock_snapcast)

        await routing_service._auto_configure_multiroom()

        mock_snapcast.set_all_groups_to_multiroom.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_configure_multiroom_timeout(self, routing_service):
        """Test de timeout de configuration automatique du multiroom"""
        mock_snapcast = Mock()
        mock_snapcast.is_available = AsyncMock(return_value=False)  # Jamais disponible
        routing_service.set_snapcast_service(mock_snapcast)

        # Devrait timeout après 10 tentatives
        await routing_service._auto_configure_multiroom()

        # Vérifier qu'on a essayé plusieurs fois
        assert mock_snapcast.is_available.call_count == 10
