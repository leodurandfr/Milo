# backend/tests/test_volume_service.py
"""
Unit tests for VolumeService - Tests for dB-based volume management
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.volume_service import VolumeService
from backend.infrastructure.services.volume_converter_service import VolumeConverterService


class TestVolumeService:
    """Tests for the volume service (dB-based)"""

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
        """Mock of snapcast service"""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service):
        """Fixture to create a VolumeService"""
        with patch('backend.infrastructure.services.volume_service.SettingsService') as mock_settings:
            # Mock SettingsService with dB-based config
            settings_instance = Mock()
            settings_instance.get_volume_config = Mock(return_value={
                "limit_min_db": -80.0,
                "limit_max_db": -21.0,
                "startup_volume_db": -30.0,
                "restore_last_volume": False,
                "step_mobile_db": 3.0,
                "step_rotary_db": 2.0
            })
            mock_settings.return_value = settings_instance

            service = VolumeService(mock_state_machine, mock_snapcast_service)
            return service

    def test_initialization(self, service):
        """Service initialization test"""
        assert service.state_machine is not None
        assert service.snapcast_service is not None
        assert service.config.config.limit_min_db == -80.0
        assert service.config.config.limit_max_db == -21.0
        assert service.config.config.step_mobile_db == 3.0
        assert service.config.config.step_rotary_db == 2.0

    def test_clamp_db_volume(self, service):
        """dB volume clamping test"""
        assert service.converter.clamp_db(-90.0) == -80.0  # Below min
        assert service.converter.clamp_db(-80.0) == -80.0  # At min
        assert service.converter.clamp_db(-30.0) == -30.0  # Middle
        assert service.converter.clamp_db(-21.0) == -21.0  # At max
        assert service.converter.clamp_db(0.0) == -21.0    # Above max (clamped to limit)

    def test_db_to_percent(self, service):
        """dB to percent conversion test (for progress bar)"""
        # -80 dB = 0%
        assert service.converter.db_to_percent(-80.0) == 0

        # 0 dB = 100%
        assert service.converter.db_to_percent(0.0) == 100

        # -40 dB = 50%
        assert service.converter.db_to_percent(-40.0) == 50

    def test_percent_to_db(self, service):
        """Percent to dB conversion test"""
        # 0% = -80 dB
        assert service.converter.percent_to_db(0) == -80.0

        # 100% = 0 dB
        assert service.converter.percent_to_db(100) == 0.0

        # 50% = -40 dB
        assert service.converter.percent_to_db(50) == -40.0

    @pytest.mark.asyncio
    async def test_load_volume_config(self, service):
        """Volume configuration loading test"""
        service.settings_service.invalidate_cache = Mock()
        service.settings_service.get_setting = AsyncMock(return_value={
            "limit_min_db": -50.0,
            "limit_max_db": -15.0,
            "startup_volume_db": -25.0,
            "restore_last_volume": True,
            "step_mobile_db": 4.0,
            "step_rotary_db": 3.0
        })

        await service._load_volume_config()

        assert service.config.config.limit_min_db == -50.0
        assert service.config.config.limit_max_db == -15.0
        assert service.config.config.startup_volume_db == -25.0
        assert service.config.config.restore_last_volume is True
        assert service.config.config.step_mobile_db == 4.0
        assert service.config.config.step_rotary_db == 3.0

    def test_is_multiroom_enabled_true(self, service, mock_state_machine):
        """Multiroom enabled check test"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})
        assert service._is_multiroom_enabled() is True

    def test_is_multiroom_enabled_false(self, service, mock_state_machine):
        """Multiroom disabled check test"""
        mock_state_machine.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_no_routing_service(self, service, mock_state_machine):
        """Multiroom check test without routing_service"""
        mock_state_machine.routing_service = None
        assert service._is_multiroom_enabled() is False

    def test_config_rotary_steps(self, service):
        """Rotary step access test via sub-service config"""
        assert service.config.config.step_rotary_db == 2.0

        # Test with different value via config service
        service._config_service._config.step_rotary_db = 3.0
        assert service.config.config.step_rotary_db == 3.0

    @pytest.mark.asyncio
    async def test_reload_volume_steps_config(self, service):
        """Volume steps reload test"""
        service.settings_service.invalidate_cache = Mock()
        service.settings_service.get_setting = AsyncMock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": False,
            "step_mobile_db": 5.0,
            "step_rotary_db": 2.0
        })

        result = await service.reload_volume_steps_config()

        assert result is True
        assert service.config.config.step_mobile_db == 5.0

    @pytest.mark.asyncio
    async def test_reload_rotary_steps_config(self, service):
        """Rotary steps reload test"""
        service.settings_service.invalidate_cache = Mock()
        service.settings_service.get_setting = AsyncMock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": False,
            "step_mobile_db": 3.0,
            "step_rotary_db": 4.0
        })

        result = await service.reload_rotary_steps_config()

        assert result is True
        assert service.config.config.step_rotary_db == 4.0

    @pytest.mark.asyncio
    async def test_reload_startup_config(self, service):
        """Startup config reload test"""
        service.settings_service.invalidate_cache = Mock()
        service.settings_service.get_setting = AsyncMock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -25.0,
            "restore_last_volume": True,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        })

        result = await service.reload_startup_config()

        assert result is True
        assert service.config.config.startup_volume_db == -25.0
        assert service.config.config.restore_last_volume is True


class TestVolumeConverterService:
    """Tests for VolumeConverterService"""

    @pytest.fixture
    def converter(self):
        """Create a VolumeConverterService instance"""
        return VolumeConverterService()

    def test_default_limits(self, converter):
        """Test default limits"""
        assert converter.limit_min_db == -80.0
        assert converter.limit_max_db == -21.0

    def test_update_limits(self, converter):
        """Test updating limits"""
        converter.update_limits(-50.0, -10.0)
        assert converter.limit_min_db == -50.0
        assert converter.limit_max_db == -10.0

    def test_clamp_db(self, converter):
        """Test clamping dB values"""
        # Within range
        assert converter.clamp_db(-40.0) == -40.0

        # Below min
        assert converter.clamp_db(-90.0) == -80.0

        # Above max
        assert converter.clamp_db(-10.0) == -21.0

    def test_db_to_percent(self, converter):
        """Test dB to percent conversion"""
        assert converter.db_to_percent(-80.0) == 0
        assert converter.db_to_percent(-40.0) == 50
        assert converter.db_to_percent(0.0) == 100

    def test_percent_to_db(self, converter):
        """Test percent to dB conversion"""
        assert converter.percent_to_db(0) == -80.0
        assert converter.percent_to_db(50) == -40.0
        assert converter.percent_to_db(100) == 0.0
