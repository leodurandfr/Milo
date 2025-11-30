# backend/tests/test_librespot_plugin.py
"""
Tests unitaires pour LibrespotPlugin
"""
import pytest
import asyncio
import os
import tempfile
import yaml
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.infrastructure.plugins.librespot.plugin import LibrespotPlugin
from backend.domain.audio_state import PluginState, AudioSource


class TestLibrespotPlugin:
    """Tests pour le plugin Librespot"""

    @pytest.fixture
    def mock_state_machine(self):
        """Mock de la state machine"""
        sm = Mock()
        sm.update_plugin_state = AsyncMock()
        sm.system_state = Mock()
        sm.system_state.active_source = AudioSource.SPOTIFY
        sm.system_state.plugin_state = PluginState.READY
        sm.system_state.metadata = {}
        return sm

    @pytest.fixture
    def temp_config_file(self):
        """Crée un fichier de config temporaire pour les tests"""
        config_data = {
            'audio_device': 'milo_spotify',
            'server': {
                'address': 'localhost',
                'port': 3678
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        yield temp_path

        # Cleanup
        try:
            os.unlink(temp_path)
        except:
            pass

    @pytest.fixture
    def plugin_config(self, temp_config_file):
        """Configuration du plugin"""
        return {
            'service_name': 'milo-go-librespot.service',
            'config_path': temp_config_file
        }

    @pytest.fixture
    def plugin(self, plugin_config, mock_state_machine, mock_settings_service):
        """Fixture pour créer un plugin librespot"""
        return LibrespotPlugin(
            config=plugin_config,
            state_machine=mock_state_machine,
            settings_service=mock_settings_service
        )

    def test_initialization_config(self, plugin):
        """Test de l'initialisation de base du plugin"""
        assert plugin.name == "librespot"
        assert plugin.service_name == "milo-go-librespot.service"
        assert plugin._initialized is False
        assert plugin.auto_disconnect_enabled is True
        assert plugin.pause_disconnect_delay == 10.0

    @pytest.mark.asyncio
    async def test_initialize_success(self, plugin):
        """Test de l'initialisation réussie"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Mock du processus systemctl
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b'milo-go-librespot.service', b''))
            mock_exec.return_value = mock_process

            result = await plugin.initialize()

            assert result is True
            assert plugin._initialized is True
            assert plugin.api_url == "http://localhost:3678"
            assert plugin.ws_url == "ws://localhost:3678/events"

    @pytest.mark.asyncio
    async def test_initialize_service_not_found(self, plugin):
        """Test de l'initialisation avec service introuvable"""
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            # Mock du processus systemctl qui ne trouve pas le service
            mock_process = AsyncMock()
            mock_process.returncode = 1
            mock_process.communicate = AsyncMock(return_value=(b'', b'not found'))
            mock_exec.return_value = mock_process

            result = await plugin.initialize()

            assert result is False
            assert plugin._initialized is False

    @pytest.mark.asyncio
    async def test_load_settings_config_enabled(self, plugin, mock_settings_service):
        """Test du chargement de config avec auto-disconnect activé"""
        mock_settings_service.get_setting = Mock(return_value=15.0)

        await plugin._load_settings_config()

        assert plugin.auto_disconnect_enabled is True
        assert plugin.pause_disconnect_delay == 15.0

    @pytest.mark.asyncio
    async def test_load_settings_config_disabled_with_zero(self, plugin, mock_settings_service):
        """Test du chargement de config avec delay = 0 (désactivé)"""
        mock_settings_service.get_setting = Mock(return_value=0.0)

        await plugin._load_settings_config()

        assert plugin.auto_disconnect_enabled is False
        assert plugin.pause_disconnect_delay == 10.0  # Valeur par défaut

    @pytest.mark.asyncio
    async def test_load_settings_config_no_settings_service(self):
        """Test du chargement de config sans SettingsService"""
        plugin = LibrespotPlugin(
            config={'service_name': 'test.service', 'config_path': '/tmp/test.yaml'},
            state_machine=None,
            settings_service=None
        )

        # Ne devrait pas lever d'exception
        await plugin._load_settings_config()

        assert plugin.auto_disconnect_enabled is True
        assert plugin.pause_disconnect_delay == 10.0

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_enable(self, plugin, mock_settings_service):
        """Test de l'activation de l'auto-disconnect"""
        result = await plugin.set_auto_disconnect_config(enabled=True, delay=20.0)

        assert result is True
        assert plugin.auto_disconnect_enabled is True
        assert plugin.pause_disconnect_delay == 20.0
        mock_settings_service.set_setting.assert_called_with('spotify.auto_disconnect_delay', 20.0)

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_disable_with_zero(self, plugin, mock_settings_service):
        """Test de la désactivation avec delay = 0"""
        result = await plugin.set_auto_disconnect_config(enabled=True, delay=0.0)

        assert result is True
        assert plugin.auto_disconnect_enabled is False
        assert plugin.pause_disconnect_delay == 10.0  # Valeur par défaut
        mock_settings_service.set_setting.assert_called_with('spotify.auto_disconnect_delay', 0.0)

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_no_save(self, plugin, mock_settings_service):
        """Test de config sans sauvegarde"""
        result = await plugin.set_auto_disconnect_config(enabled=False, delay=5.0, save_to_settings=False)

        assert result is True
        assert plugin.auto_disconnect_enabled is False
        mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_auto_disconnect_config_save_failure_rollback(self, plugin, mock_settings_service):
        """Test du rollback en cas d'échec de sauvegarde"""
        # Sauvegarder les valeurs initiales
        old_enabled = plugin.auto_disconnect_enabled
        old_delay = plugin.pause_disconnect_delay

        # Mock un échec de sauvegarde
        mock_settings_service.set_setting = Mock(return_value=False)

        result = await plugin.set_auto_disconnect_config(enabled=False, delay=5.0)

        assert result is False
        # Vérifier le rollback
        assert plugin.auto_disconnect_enabled == old_enabled
        assert plugin.pause_disconnect_delay == old_delay

    def test_get_auto_disconnect_config(self, plugin):
        """Test de récupération de la config auto-disconnect"""
        plugin.auto_disconnect_enabled = True
        plugin.pause_disconnect_delay = 15.0
        plugin._pause_disconnect_timer = None

        config = plugin.get_auto_disconnect_config()

        assert config["enabled"] is True
        assert config["delay"] == 15.0
        assert config["timer_active"] is False

    def test_cancel_pause_timer(self, plugin):
        """Test de l'annulation du timer de pause"""
        # Créer un faux timer
        mock_timer = Mock()
        plugin._pause_disconnect_timer = mock_timer

        plugin._cancel_pause_timer()

        mock_timer.cancel.assert_called_once()
        assert plugin._pause_disconnect_timer is None

    def test_start_pause_timer_when_disabled(self, plugin):
        """Test que le timer ne démarre pas quand auto-disconnect est désactivé"""
        plugin.auto_disconnect_enabled = False

        plugin._start_pause_timer()

        assert plugin._pause_disconnect_timer is None

    @pytest.mark.asyncio
    async def test_start_pause_timer_when_enabled(self, plugin):
        """Test que le timer démarre quand auto-disconnect est activé"""
        plugin.auto_disconnect_enabled = True
        plugin.pause_disconnect_delay = 5.0

        plugin._start_pause_timer()

        # Vérifier qu'un timer a été créé
        assert plugin._pause_disconnect_timer is not None

        # Cleanup
        plugin._cancel_pause_timer()
        # Attendre que la task soit annulée
        await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_get_status(self, plugin):
        """Test de récupération du status"""
        plugin._device_connected = True
        plugin._ws_connected = True
        plugin._is_playing = True
        plugin._metadata = {"title": "Test Song", "artist": "Test Artist"}
        plugin._current_device = "milo_spotify"

        with patch.object(plugin.service_manager, 'get_status', new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"active": True, "state": "running"}

            status = await plugin.get_status()

            assert status["device_connected"] is True
            assert status["ws_connected"] is True
            assert status["is_playing"] is True
            assert status["metadata"]["title"] == "Test Song"
            assert status["current_device"] == "milo_spotify"
            assert status["service_active"] is True

    @pytest.mark.asyncio
    async def test_get_status_error_fallback(self, plugin):
        """Test du fallback en cas d'erreur lors de get_status"""
        plugin._current_device = "milo_spotify"

        with patch.object(plugin.service_manager, 'get_status', new_callable=AsyncMock) as mock_status:
            mock_status.side_effect = Exception("Service error")

            status = await plugin.get_status()

            assert status["device_connected"] is False
            assert status["ws_connected"] is False
            assert status["is_playing"] is False
            assert "error" in status

    @pytest.mark.asyncio
    async def test_stop_plugin(self, plugin):
        """Test de l'arrêt du plugin"""
        # Setup état initial
        mock_timer = Mock()
        plugin._pause_disconnect_timer = mock_timer

        mock_session = AsyncMock()
        plugin.session = mock_session

        mock_ws = Mock()
        mock_ws.stop = AsyncMock()
        plugin.ws_manager = mock_ws

        plugin._device_connected = True
        plugin._is_playing = True

        with patch.object(plugin, 'control_service', new_callable=AsyncMock) as mock_control:
            mock_control.return_value = True

            result = await plugin.stop()

            assert result is True
            assert plugin._device_connected is False
            assert plugin._is_playing is False
            assert plugin._ws_connected is False
            mock_ws.stop.assert_called_once()
            mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_command_unsupported(self, plugin):
        """Test de commande non supportée"""
        result = await plugin.handle_command("invalid_command", {})

        assert result["success"] is False
        assert "non supportée" in result["error"].lower()

    def test_get_audio_source(self, plugin):
        """Test de récupération de l'AudioSource enum"""
        assert plugin._get_audio_source() == AudioSource.SPOTIFY

    def test_is_active_plugin_true(self, plugin, mock_state_machine):
        """Test is_active_plugin quand le plugin est actif"""
        mock_state_machine.system_state.active_source = AudioSource.SPOTIFY

        assert plugin.is_active_plugin() is True

    def test_is_active_plugin_false(self, plugin, mock_state_machine):
        """Test is_active_plugin quand le plugin n'est pas actif"""
        mock_state_machine.system_state.active_source = AudioSource.BLUETOOTH

        assert plugin.is_active_plugin() is False
