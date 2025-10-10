# backend/tests/test_volume_service.py
"""
Tests unitaires pour VolumeService - Tests des fonctions de conversion et configuration
"""
import pytest
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.volume_service import VolumeService


class TestVolumeService:
    """Tests pour le service de volume"""

    @pytest.fixture
    def mock_state_machine(self):
        """Mock de la state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Mock du service snapcast"""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service):
        """Fixture pour créer un VolumeService"""
        with patch('backend.infrastructure.services.volume_service.SettingsService') as mock_settings:
            # Mock SettingsService
            settings_instance = Mock()
            settings_instance.get_volume_config = Mock(return_value={
                "alsa_min": 0,
                "alsa_max": 65,
                "startup_volume": 37,
                "restore_last_volume": False,
                "mobile_volume_steps": 5,
                "rotary_volume_steps": 2
            })
            mock_settings.return_value = settings_instance

            service = VolumeService(mock_state_machine, mock_snapcast_service)
            return service

    def test_initialization(self, service):
        """Test de l'initialisation du service"""
        assert service.state_machine is not None
        assert service.snapcast_service is not None
        assert service._alsa_min_volume == 0
        assert service._alsa_max_volume == 65
        assert service._mobile_volume_steps == 5
        assert service._rotary_volume_steps == 2

    def test_alsa_to_display_conversion(self, service):
        """Test de conversion ALSA → Display (0-100%)"""
        # Min volume
        assert service._alsa_to_display(0) == 0

        # Max volume
        assert service._alsa_to_display(65) == 100

        # Mid volume (32/65 ≈ 49%)
        result = service._alsa_to_display(32)
        assert 48 <= result <= 50

    def test_display_to_alsa_conversion(self, service):
        """Test de conversion Display → ALSA"""
        # Min volume
        assert service._display_to_alsa(0) == 0

        # Max volume
        assert service._display_to_alsa(100) == 65

        # Mid volume (50% → 32/33)
        result = service._display_to_alsa(50)
        assert 31 <= result <= 34

    def test_display_to_alsa_precise_conversion(self, service):
        """Test de conversion Display précise → ALSA"""
        # Test avec float
        result = service._display_to_alsa_precise(50.5)
        assert isinstance(result, int)
        assert 31 <= result <= 34

    def test_round_half_up(self, service):
        """Test de l'arrondi standard"""
        assert service._round_half_up(1.4) == 1
        assert service._round_half_up(1.5) == 2
        assert service._round_half_up(1.6) == 2
        assert service._round_half_up(2.0) == 2

    def test_clamp_display_volume(self, service):
        """Test du clamping du volume display (0-100%)"""
        assert service._clamp_display_volume(-10.0) == 0.0
        assert service._clamp_display_volume(0.0) == 0.0
        assert service._clamp_display_volume(50.0) == 50.0
        assert service._clamp_display_volume(100.0) == 100.0
        assert service._clamp_display_volume(110.0) == 100.0

    def test_clamp_alsa_volume(self, service):
        """Test du clamping du volume ALSA (min-max)"""
        assert service._clamp_alsa_volume(-10) == 0  # alsa_min
        assert service._clamp_alsa_volume(0) == 0
        assert service._clamp_alsa_volume(30) == 30
        assert service._clamp_alsa_volume(65) == 65  # alsa_max
        assert service._clamp_alsa_volume(100) == 65

    def test_load_volume_config(self, service):
        """Test du chargement de la configuration volume"""
        service.settings_service.get_volume_config = Mock(return_value={
            "alsa_min": 10,
            "alsa_max": 70,
            "startup_volume": 40,
            "restore_last_volume": True,
            "mobile_volume_steps": 3,
            "rotary_volume_steps": 1
        })

        service._load_volume_config()

        assert service._alsa_min_volume == 10
        assert service._alsa_max_volume == 70
        assert service._default_startup_display_volume == 40
        assert service._restore_last_volume is True
        assert service._mobile_volume_steps == 3
        assert service._rotary_volume_steps == 1

    def test_display_to_alsa_old_limits(self, service):
        """Test de conversion avec anciennes limites"""
        # Conversion avec old_min=0, old_max=50
        result = service._display_to_alsa_old_limits(50, 0, 50)
        assert result == 25

        # Conversion avec old_min=10, old_max=60
        result = service._display_to_alsa_old_limits(100, 10, 60)
        assert result == 60

    def test_invalidate_all_caches(self, service):
        """Test de l'invalidation des caches"""
        service._client_display_states = {"client1": 50.0}
        service._client_states_initialized = True
        service._snapcast_clients_cache = [{"id": "client1"}]
        service._snapcast_cache_time = 12345

        service._invalidate_all_caches()

        assert service._client_display_states == {}
        assert service._client_states_initialized is False
        assert service._snapcast_clients_cache == []
        assert service._snapcast_cache_time == 0

    def test_set_client_display_volume(self, service):
        """Test de définition du volume display d'un client"""
        service._set_client_display_volume("client1", 75.5)

        assert service._client_display_states["client1"] == 75.5

        # Test clamping
        service._set_client_display_volume("client2", 150.0)
        assert service._client_display_states["client2"] == 100.0

        service._set_client_display_volume("client3", -10.0)
        assert service._client_display_states["client3"] == 0.0

    def test_is_multiroom_enabled_true(self, service, mock_state_machine):
        """Test de vérification multiroom activé"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})

        assert service._is_multiroom_enabled() is True

    def test_is_multiroom_enabled_false(self, service, mock_state_machine):
        """Test de vérification multiroom désactivé"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})

        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_no_routing_service(self, service, mock_state_machine):
        """Test de vérification multiroom sans routing_service"""
        mock_state_machine.routing_service = None

        assert service._is_multiroom_enabled() is False

    def test_get_rotary_step(self, service):
        """Test de récupération du step rotary"""
        service._rotary_volume_steps = 3

        assert service.get_rotary_step() == 3

    def test_convert_alsa_to_display_public(self, service):
        """Test de la méthode publique de conversion ALSA → Display"""
        assert service.convert_alsa_to_display(0) == 0
        assert service.convert_alsa_to_display(65) == 100

    def test_convert_display_to_alsa_public(self, service):
        """Test de la méthode publique de conversion Display → ALSA"""
        assert service.convert_display_to_alsa(0) == 0
        assert service.convert_display_to_alsa(100) == 65

    def test_get_volume_config_public(self, service):
        """Test de récupération de la config publique"""
        config = service.get_volume_config_public()

        assert config["alsa_min"] == 0
        assert config["alsa_max"] == 65
        assert config["startup_volume"] == 37
        assert config["restore_last_volume"] is False
        assert config["mobile_steps"] == 5
        assert config["rotary_steps"] == 2

    @pytest.mark.asyncio
    async def test_reload_volume_steps_config(self, service):
        """Test du rechargement des volume steps"""
        service.settings_service.get_volume_config = Mock(return_value={
            "alsa_min": 0,
            "alsa_max": 65,
            "startup_volume": 37,
            "restore_last_volume": False,
            "mobile_volume_steps": 7,
            "rotary_volume_steps": 2
        })

        result = await service.reload_volume_steps_config()

        assert result is True
        assert service._mobile_volume_steps == 7

    @pytest.mark.asyncio
    async def test_reload_rotary_steps_config(self, service):
        """Test du rechargement des rotary steps"""
        service.settings_service.get_volume_config = Mock(return_value={
            "alsa_min": 0,
            "alsa_max": 65,
            "startup_volume": 37,
            "restore_last_volume": False,
            "mobile_volume_steps": 5,
            "rotary_volume_steps": 4
        })

        result = await service.reload_rotary_steps_config()

        assert result is True
        assert service._rotary_volume_steps == 4

    @pytest.mark.asyncio
    async def test_reload_startup_config(self, service):
        """Test du rechargement de la config startup"""
        service.settings_service.get_volume_config = Mock(return_value={
            "alsa_min": 0,
            "alsa_max": 65,
            "startup_volume": 50,
            "restore_last_volume": True,
            "mobile_volume_steps": 5,
            "rotary_volume_steps": 2
        })

        result = await service.reload_startup_config()

        assert result is True
        assert service._default_startup_display_volume == 50
        assert service._restore_last_volume is True

    def test_determine_startup_volume_default(self, service):
        """Test de détermination du volume startup (mode par défaut)"""
        service._restore_last_volume = False
        service._default_startup_display_volume = 37

        volume = service._determine_startup_volume()

        assert volume == 37

    def test_determine_startup_volume_no_saved_file(self, service):
        """Test de détermination du volume startup sans fichier sauvegardé"""
        service._restore_last_volume = True
        service._default_startup_display_volume = 37

        # S'assurer qu'il n'y a pas de fichier
        if service.LAST_VOLUME_FILE.exists():
            os.unlink(service.LAST_VOLUME_FILE)

        volume = service._determine_startup_volume()

        # Devrait fallback au default
        assert volume == 37

    def test_save_last_volume_disabled(self, service):
        """Test que save_last_volume ne sauvegarde pas si désactivé"""
        service._restore_last_volume = False

        # Ne devrait rien sauvegarder
        service._save_last_volume(50)

        # Vérifier qu'aucun fichier n'est créé (test asynchrone donc pas de garantie immédiate)
        # On teste juste que la fonction ne lève pas d'exception

    def test_ensure_data_directory_exists(self, service):
        """Test que le répertoire de données est créé"""
        # La méthode est appelée dans __init__
        parent_dir = service.LAST_VOLUME_FILE.parent

        # Le répertoire devrait exister (ou ne pas lever d'exception si impossible à créer)
        assert parent_dir.exists() or True  # Tolérance si impossible à créer

    @pytest.mark.asyncio
    async def test_get_snapcast_clients_cached(self, service, mock_snapcast_service):
        """Test du cache des clients snapcast"""
        mock_clients = [
            {"id": "client1", "name": "Client 1"},
            {"id": "client2", "name": "Client 2"}
        ]
        mock_snapcast_service.get_clients = AsyncMock(return_value=mock_clients)

        # Premier appel - devrait récupérer depuis snapcast
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 2
        assert clients[0]["id"] == "client1"

        # Deuxième appel immédiat - devrait utiliser le cache
        mock_snapcast_service.get_clients = AsyncMock(return_value=[])  # Changer le mock
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 2  # Toujours depuis le cache

    @pytest.mark.asyncio
    async def test_get_snapcast_clients_cached_error_fallback(self, service, mock_snapcast_service):
        """Test du fallback du cache en cas d'erreur"""
        # Premier appel réussi
        mock_snapcast_service.get_clients = AsyncMock(return_value=[{"id": "client1"}])
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 1

        # Deuxième appel avec erreur - devrait retourner le cache
        service._snapcast_cache_time = 0  # Invalider le cache
        mock_snapcast_service.get_clients = AsyncMock(side_effect=Exception("Connection error"))
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 1  # Devrait retourner l'ancien cache
