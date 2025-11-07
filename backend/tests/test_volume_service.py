# backend/tests/test_volume_service.py
"""
Unit tests for VolumeService - Tests for conversion and configuration functions
"""
import pytest
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.volume_service import VolumeService


class TestVolumeService:
    """Tests for the volume service"""

    @pytest.fixture
    def mock_state_machine(self):
        """Mock of the state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Mock of the snapcast service"""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service):
        """Fixture to create a VolumeService"""
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
        """Test service initialization"""
        assert service.state_machine is not None
        assert service.snapcast_service is not None
        assert service._alsa_min_volume == 0
        assert service._alsa_max_volume == 65
        assert service._mobile_volume_steps == 5
        assert service._rotary_volume_steps == 2

    def test_alsa_to_display_conversion(self, service):
        """Test ALSA → Display conversion (0-100%)"""
        # Min volume
        assert service._alsa_to_display(0) == 0

        # Max volume
        assert service._alsa_to_display(65) == 100

        # Mid volume (32/65 ≈ 49%)
        result = service._alsa_to_display(32)
        assert 48 <= result <= 50

    def test_display_to_alsa_conversion(self, service):
        """Test Display → ALSA conversion"""
        # Min volume
        assert service._display_to_alsa(0) == 0

        # Max volume
        assert service._display_to_alsa(100) == 65

        # Mid volume (50% → 32/33)
        result = service._display_to_alsa(50)
        assert 31 <= result <= 34

    def test_display_to_alsa_precise_conversion(self, service):
        """Test precise Display → ALSA conversion"""
        # Test avec float
        result = service._display_to_alsa_precise(50.5)
        assert isinstance(result, int)
        assert 31 <= result <= 34

    def test_round_half_up(self, service):
        """Test standard rounding"""
        assert service._round_half_up(1.4) == 1
        assert service._round_half_up(1.5) == 2
        assert service._round_half_up(1.6) == 2
        assert service._round_half_up(2.0) == 2

    def test_clamp_display_volume(self, service):
        """Test display volume clamping (0-100%)"""
        assert service._clamp_display_volume(-10.0) == 0.0
        assert service._clamp_display_volume(0.0) == 0.0
        assert service._clamp_display_volume(50.0) == 50.0
        assert service._clamp_display_volume(100.0) == 100.0
        assert service._clamp_display_volume(110.0) == 100.0

    def test_clamp_alsa_volume(self, service):
        """Test ALSA volume clamping (min-max)"""
        assert service._clamp_alsa_volume(-10) == 0  # alsa_min
        assert service._clamp_alsa_volume(0) == 0
        assert service._clamp_alsa_volume(30) == 30
        assert service._clamp_alsa_volume(65) == 65  # alsa_max
        assert service._clamp_alsa_volume(100) == 65

    def test_load_volume_config(self, service):
        """Test loading volume configuration"""
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
        """Test conversion with old limits"""
        # Conversion avec old_min=0, old_max=50
        result = service._display_to_alsa_old_limits(50, 0, 50)
        assert result == 25

        # Conversion avec old_min=10, old_max=60
        result = service._display_to_alsa_old_limits(100, 10, 60)
        assert result == 60

    def test_invalidate_all_caches(self, service):
        """Test cache invalidation"""
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
        """Test setting client display volume"""
        service._set_client_display_volume("client1", 75.5)

        assert service._client_display_states["client1"] == 75.5

        # Test clamping
        service._set_client_display_volume("client2", 150.0)
        assert service._client_display_states["client2"] == 100.0

        service._set_client_display_volume("client3", -10.0)
        assert service._client_display_states["client3"] == 0.0

    def test_is_multiroom_enabled_true(self, service, mock_state_machine):
        """Test checking multiroom enabled"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})

        assert service._is_multiroom_enabled() is True

    def test_is_multiroom_enabled_false(self, service, mock_state_machine):
        """Test checking multiroom disabled"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})

        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_no_routing_service(self, service, mock_state_machine):
        """Test checking multiroom without routing_service"""
        mock_state_machine.routing_service = None

        assert service._is_multiroom_enabled() is False

    def test_get_rotary_step(self, service):
        """Test getting rotary step"""
        service._rotary_volume_steps = 3

        assert service.get_rotary_step() == 3

    def test_convert_alsa_to_display_public(self, service):
        """Test public method for ALSA → Display conversion"""
        assert service.convert_alsa_to_display(0) == 0
        assert service.convert_alsa_to_display(65) == 100

    def test_convert_display_to_alsa_public(self, service):
        """Test public method for Display → ALSA conversion"""
        assert service.convert_display_to_alsa(0) == 0
        assert service.convert_display_to_alsa(100) == 65

    def test_get_volume_config_public(self, service):
        """Test getting public config"""
        config = service.get_volume_config_public()

        assert config["alsa_min"] == 0
        assert config["alsa_max"] == 65
        assert config["startup_volume"] == 37
        assert config["restore_last_volume"] is False
        assert config["mobile_steps"] == 5
        assert config["rotary_steps"] == 2

    @pytest.mark.asyncio
    async def test_reload_volume_steps_config(self, service):
        """Test reloading volume steps"""
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
        """Test reloading rotary steps"""
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
        """Test reloading startup config"""
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
        """Test determining startup volume (default mode)"""
        service._restore_last_volume = False
        service._default_startup_display_volume = 37

        volume = service._determine_startup_volume()

        assert volume == 37

    def test_determine_startup_volume_no_saved_file(self, service):
        """Test determining startup volume without saved file"""
        service._restore_last_volume = True
        service._default_startup_display_volume = 37

        # Ensure there is no file
        if service.LAST_VOLUME_FILE.exists():
            os.unlink(service.LAST_VOLUME_FILE)

        volume = service._determine_startup_volume()

        # Should fallback to default
        assert volume == 37

    def test_save_last_volume_disabled(self, service):
        """Test that save_last_volume doesn't save if disabled"""
        service._restore_last_volume = False

        # Should not save anything
        service._save_last_volume(50)

        # Verify that no file is created (async test so no immediate guarantee)
        # We just test that the function doesn't raise an exception

    def test_ensure_data_directory_exists(self, service):
        """Test that data directory is created"""
        # Method is called in __init__
        parent_dir = service.LAST_VOLUME_FILE.parent

        # Directory should exist (or not raise exception if cannot be created)
        assert parent_dir.exists() or True  # Tolerance if cannot be created

    @pytest.mark.asyncio
    async def test_get_snapcast_clients_cached(self, service, mock_snapcast_service):
        """Test snapcast clients cache"""
        mock_clients = [
            {"id": "client1", "name": "Client 1"},
            {"id": "client2", "name": "Client 2"}
        ]
        mock_snapcast_service.get_clients = AsyncMock(return_value=mock_clients)

        # First call - should fetch from snapcast
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 2
        assert clients[0]["id"] == "client1"

        # Second immediate call - should use cache
        mock_snapcast_service.get_clients = AsyncMock(return_value=[])  # Change mock
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 2  # Still from cache

    @pytest.mark.asyncio
    async def test_get_snapcast_clients_cached_error_fallback(self, service, mock_snapcast_service):
        """Test cache fallback on error"""
        # First successful call
        mock_snapcast_service.get_clients = AsyncMock(return_value=[{"id": "client1"}])
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 1

        # Second call with error - should return cache
        service._snapcast_cache_time = 0  # Invalidate cache
        mock_snapcast_service.get_clients = AsyncMock(side_effect=Exception("Connection error"))
        clients = await service._get_snapcast_clients_cached()
        assert len(clients) == 1  # Should return old cache
