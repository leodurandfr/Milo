# backend/tests/test_volume_service.py
"""
Unit tests for VolumeService - Tests for dB-based volume management
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.core.settings import SettingsService
from backend.core.volume import VolumeService
from backend.core.models.volume import VolumeConfig


def volume_section(**overrides):
    """A complete `volume` section, taken from the one declaration.

    `_load_volume_config` reads every key directly — the fallback operands it
    used to carry were a third declaration of these values and had drifted from
    this dict. A partial section is a settings.json `_validate_and_merge` cannot
    produce, so a test that hands one over is testing a state that cannot occur.
    """
    return {**SettingsService().defaults["volume"], **overrides}


class TestVolumeService:
    """Tests for the volume service (dB-based)"""

    @pytest.fixture
    def mock_state_machine(self):
        """Mock of the state machine"""
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Mock of snapcast service"""
        service = Mock()
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_settings_service(self):
        """Mock settings service."""
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        return settings

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings_service):
        """Fixture to create a VolumeService"""
        service = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings_service
        )
        return service

    def test_initialization(self, service):
        """Service initialization test"""
        assert service.state_machine is not None
        assert service.snapcast_service is not None
        assert service.volume_config.limit_min_db == -80.0
        assert service.volume_config.limit_max_db == -20.0
        assert service.volume_config.step_mobile_db == 2.0
        assert service.volume_config.step_rotary_db == 2.0

    def test_clamp_db_volume(self, service):
        """dB volume clamping test"""
        config = service.volume_config
        assert config.clamp(-90.0) == -80.0  # Below min
        assert config.clamp(-80.0) == -80.0  # At min
        assert config.clamp(-30.0) == -30.0  # Middle
        assert config.clamp(-20.0) == -20.0  # At max
        assert config.clamp(0.0) == -20.0    # Above max (clamped to limit)

    @pytest.mark.asyncio
    async def test_load_volume_config(self, service):
        """Volume configuration loading test"""
        service.settings_service.invalidate_cache = Mock()
        service.settings_service.get_setting = AsyncMock(return_value=volume_section(
            limit_min_db=-50.0,
            limit_max_db=-15.0,
            startup_volume_db=-25.0,
            restore_last_volume=True,
            step_mobile_db=4.0,
            step_rotary_db=3.0,
        ))

        await service._load_volume_config()

        assert service.volume_config.limit_min_db == -50.0
        assert service.volume_config.limit_max_db == -15.0
        assert service.volume_config.startup_volume_db == -25.0
        assert service.volume_config.restore_last_volume is True
        assert service.volume_config.step_mobile_db == 4.0
        assert service.volume_config.step_rotary_db == 3.0

    def test_is_multiroom_enabled_true(self, service):
        """Multiroom enabled check test"""
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        assert service._is_multiroom_enabled() is True

    def test_is_multiroom_enabled_false(self, service):
        """Multiroom disabled check test"""
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': False}
        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_no_routing_service(self, service):
        """Multiroom check test without routing_service"""
        service._routing_service = None
        assert service._is_multiroom_enabled() is False

    def test_config_rotary_steps(self, service):
        """Rotary step access test via sub-service config"""
        assert service.volume_config.step_rotary_db == 2.0

        # Test with different value
        service._volume_config.step_rotary_db = 3.0
        assert service.volume_config.step_rotary_db == 3.0

    @pytest.mark.asyncio
    async def test_reload_volume_steps_config(self, service):
        """Volume steps reload test"""
        service.settings_service.invalidate_cache = Mock()

        async def mock_get_setting(key):
            if key == "volume":
                return volume_section(
                    startup_volume_db=-30.0,
                    restore_last_volume=False,
                    step_mobile_db=5.0,
                    step_rotary_db=2.0,
                )
            elif key == "equalizer.linked_groups":
                return []
            return None

        service.settings_service.get_setting = AsyncMock(side_effect=mock_get_setting)

        result = await service.reload_volume_steps_config()

        assert result is True
        assert service.volume_config.step_mobile_db == 5.0

    @pytest.mark.asyncio
    async def test_reload_steps_config(self, service):
        """Hardware steps reload test (rotary/BT remote)"""
        service.settings_service.invalidate_cache = Mock()

        async def mock_get_setting(key):
            if key == "volume":
                return volume_section(
                    startup_volume_db=-30.0,
                    restore_last_volume=False,
                    step_mobile_db=3.0,
                    step_rotary_db=4.0,
                )
            elif key == "equalizer.linked_groups":
                return []
            return None

        service.settings_service.get_setting = AsyncMock(side_effect=mock_get_setting)

        result = await service.reload_steps_config()

        assert result is True
        assert service.volume_config.step_rotary_db == 4.0

    @pytest.mark.asyncio
    async def test_reload_startup_config(self, service):
        """Startup config reload test"""
        service.settings_service.invalidate_cache = Mock()

        async def mock_get_setting(key):
            if key == "volume":
                return volume_section(
                    startup_volume_db=-25.0,
                    restore_last_volume=True,
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
            elif key == "equalizer.linked_groups":
                return []
            return None

        service.settings_service.get_setting = AsyncMock(side_effect=mock_get_setting)

        result = await service.reload_startup_config()

        assert result is True
        assert service.volume_config.startup_volume_db == -25.0
        assert service.volume_config.restore_last_volume is True


class TestVolumeConfig:
    """Tests for VolumeConfig domain model"""

    @pytest.fixture
    def config(self):
        """Create a VolumeConfig instance with default values"""
        return VolumeConfig()

    def test_default_limits(self, config):
        """Test default limits"""
        assert config.limit_min_db == -80.0
        assert config.limit_max_db == -20.0

    def test_default_values(self, config):
        """Test all default values"""
        assert config.step_mobile_db == 2.0
        assert config.step_rotary_db == 2.0
        assert config.step_bt_remote_db == 2.0
        assert config.step_ir_remote_db == 2.0
        assert config.startup_volume_db == -45.0  # DEFAULT_VOLUME_DB from constants
        assert config.restore_last_volume is True

    def test_custom_limits(self):
        """Test custom limit values"""
        config = VolumeConfig(limit_min_db=-50.0, limit_max_db=-10.0)
        assert config.limit_min_db == -50.0
        assert config.limit_max_db == -10.0

    def test_clamp_within_range(self, config):
        """Test clamping value within range"""
        assert config.clamp(-40.0) == -40.0

    def test_clamp_below_min(self, config):
        """Test clamping value below minimum"""
        assert config.clamp(-90.0) == -80.0

    def test_clamp_above_max(self, config):
        """Test clamping value above maximum"""
        assert config.clamp(-10.0) == -20.0

    def test_to_dict(self, config):
        """Test config serialization to dict"""
        result = config.to_dict()
        assert result["limit_min_db"] == -80.0
        assert result["limit_max_db"] == -20.0
        assert result["step_mobile_db"] == 2.0
        assert result["step_rotary_db"] == 2.0
        assert result["step_bt_remote_db"] == 2.0
        assert result["step_ir_remote_db"] == 2.0
        assert result["startup_volume_db"] == -45.0  # DEFAULT_VOLUME_DB from constants
        assert result["restore_last_volume"] is True

