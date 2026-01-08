# backend/tests/test_volume_state.py
"""
Unit tests for VolumeStateStore - Single Source of Truth for volume state.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.infrastructure.services.volume.volume_state import VolumeStateStore, ZoneConfig
from backend.domain.volume_state import ClientVolume


class TestVolumeStateStore:
    """Tests for VolumeStateStore core functionality."""

    @pytest.fixture
    def mock_settings_service(self):
        """Mock of settings service."""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        return service

    @pytest.fixture
    def store(self, mock_settings_service):
        """Create a VolumeStateStore instance."""
        return VolumeStateStore(mock_settings_service)


class TestZoneTargetVolumes:
    """Tests for zone target volume caching (race condition fix)."""

    @pytest.fixture
    def mock_settings_service(self):
        """Mock of settings service."""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        return service

    @pytest.fixture
    def store(self, mock_settings_service):
        """Create a VolumeStateStore instance."""
        return VolumeStateStore(mock_settings_service)

    def test_compute_initial_zone_targets_with_persisted_clients(self, store):
        """Zone targets computed from persisted client volumes."""
        # Setup: zone with 3 clients, different persisted volumes
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['local', 'client-01', 'client-02']
            )
        }
        store._clients = {
            'local': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False),
            'client-01': ClientVolume(volume_db=-28.0, offset_db=0.0, mute=False, available=False),
            'client-02': ClientVolume(volume_db=-31.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        store._compute_initial_zone_targets()

        # Assert: target = average of persisted volumes
        expected_avg = (-25.0 + -28.0 + -31.0) / 3  # = -28.0
        assert 'zone_1' in store._zone_target_volumes
        assert store._zone_target_volumes['zone_1'] == pytest.approx(expected_avg, rel=1e-6)

    def test_compute_initial_zone_targets_partial_clients(self, store):
        """Zone targets computed from only available persisted clients."""
        # Setup: zone with 3 clients, but only 2 have persisted volumes
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['local', 'client-01', 'client-02']
            )
        }
        store._clients = {
            'local': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False),
            'client-01': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=False)
            # client-02 not in _clients (not persisted)
        }

        # Action
        store._compute_initial_zone_targets()

        # Assert: target = average of available persisted volumes
        expected_avg = (-25.0 + -35.0) / 2  # = -30.0
        assert store._zone_target_volumes['zone_1'] == pytest.approx(expected_avg, rel=1e-6)

    def test_compute_initial_zone_targets_missing_all_clients(self, store):
        """Zone targets use default when no clients are persisted."""
        # Setup: zone with clients not in _clients dict
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['unknown-01', 'unknown-02']
            )
        }
        store._clients = {}  # No persisted clients

        # Action
        store._compute_initial_zone_targets()

        # Assert: target = DEFAULT_VOLUME_DB
        assert store._zone_target_volumes['zone_1'] == store.DEFAULT_VOLUME_DB

    def test_compute_initial_zone_targets_empty_zones(self, store):
        """No targets computed when no zones configured."""
        # Setup: no zones
        store._zones = {}
        store._clients = {
            'local': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        store._compute_initial_zone_targets()

        # Assert: _zone_target_volumes is empty
        assert store._zone_target_volumes == {}

    def test_compute_initial_zone_targets_multiple_zones(self, store):
        """Zone targets computed independently for each zone."""
        # Setup: 2 zones with different clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Zone 1',
                client_ids=['local', 'client-01']
            ),
            'zone_2': ZoneConfig(
                zone_id='zone_2',
                name='Zone 2',
                client_ids=['client-02', 'client-03']
            )
        }
        store._clients = {
            'local': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=False),
            'client-01': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False),
            'client-02': ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=False),
            'client-03': ClientVolume(volume_db=-50.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        store._compute_initial_zone_targets()

        # Assert: each zone has its own average
        assert store._zone_target_volumes['zone_1'] == pytest.approx(-25.0, rel=1e-6)  # avg(-20, -30)
        assert store._zone_target_volumes['zone_2'] == pytest.approx(-45.0, rel=1e-6)  # avg(-40, -50)

    def test_get_zone_target_volume_returns_cached(self, store):
        """get_zone_target_volume returns cached target."""
        # Setup: compute targets
        store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Zone 1', client_ids=['local'])
        }
        store._clients = {
            'local': ClientVolume(volume_db=-28.5, offset_db=0.0, mute=False, available=False)
        }
        store._compute_initial_zone_targets()

        # Action & Assert
        result = store.get_zone_target_volume('zone_1')
        assert result == pytest.approx(-28.5, rel=1e-6)

    def test_get_zone_target_volume_returns_none_for_unknown_zone(self, store):
        """get_zone_target_volume returns None for unknown zone."""
        # Setup: compute some targets
        store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Zone 1', client_ids=['local'])
        }
        store._clients = {
            'local': ClientVolume(volume_db=-28.5, offset_db=0.0, mute=False, available=False)
        }
        store._compute_initial_zone_targets()

        # Action & Assert
        result = store.get_zone_target_volume('unknown_zone')
        assert result is None

    def test_get_zone_target_volume_returns_none_when_empty(self, store):
        """get_zone_target_volume returns None when no targets computed."""
        # Setup: no targets computed
        store._zone_target_volumes = {}

        # Action & Assert
        result = store.get_zone_target_volume('zone_1')
        assert result is None

    def test_get_zone_target_volume_returns_none_after_clear(self, store):
        """get_zone_target_volume returns None after clear."""
        # Setup: compute targets, then clear
        store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Zone 1', client_ids=['local'])
        }
        store._clients = {
            'local': ClientVolume(volume_db=-28.5, offset_db=0.0, mute=False, available=False)
        }
        store._compute_initial_zone_targets()

        # Verify target exists
        assert store.get_zone_target_volume('zone_1') is not None

        # Action: clear targets
        store.clear_zone_targets()

        # Assert: returns None
        assert store.get_zone_target_volume('zone_1') is None

    def test_clear_zone_targets(self, store):
        """clear_zone_targets empties the cache."""
        # Setup: compute targets
        store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Zone 1', client_ids=['local']),
            'zone_2': ZoneConfig(zone_id='zone_2', name='Zone 2', client_ids=['client-01'])
        }
        store._clients = {
            'local': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False),
            'client-01': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }
        store._compute_initial_zone_targets()

        # Verify targets exist
        assert len(store._zone_target_volumes) == 2

        # Action
        store.clear_zone_targets()

        # Assert: _zone_target_volumes is empty
        assert store._zone_target_volumes == {}

    def test_clear_zone_targets_when_already_empty(self, store):
        """clear_zone_targets handles empty cache gracefully."""
        # Setup: no targets
        store._zone_target_volumes = {}

        # Action: should not raise
        store.clear_zone_targets()

        # Assert: still empty
        assert store._zone_target_volumes == {}

    def test_compute_clears_previous_targets(self, store):
        """_compute_initial_zone_targets clears previous cache before computing."""
        # Setup: pre-existing targets
        store._zone_target_volumes = {'old_zone': -99.0}

        # Setup new zones
        store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Zone 1', client_ids=['local'])
        }
        store._clients = {
            'local': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        store._compute_initial_zone_targets()

        # Assert: old target removed, new target computed
        assert 'old_zone' not in store._zone_target_volumes
        assert 'zone_1' in store._zone_target_volumes
