# backend/tests/test_volume_state.py
"""
Unit tests for VolumeStateStore - Single Source of Truth for volume state.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.core.volume.state import VolumeStateStore, ZoneConfig
from backend.core.models.volume_state import ClientVolume
from backend.config.constants import DEFAULT_VOLUME_DB


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
        assert store._zone_target_volumes['zone_1'] == DEFAULT_VOLUME_DB

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


# ==============================================================================
# Task 3: Unit tests for zone volume delta (Story 3.2 AC#1, #2, #3)
# ==============================================================================


class TestZoneVolumeDelta:
    """Tests for zone volume delta functionality (Story 3.2)."""

    @pytest.fixture
    def mock_settings_service(self):
        """Mock of settings service."""
        service = Mock()
        service.get_setting = AsyncMock(return_value=None)
        return service

    @pytest.fixture
    def store(self, mock_settings_service):
        """Create a VolumeStateStore instance with test data."""
        store = VolumeStateStore(mock_settings_service)
        # Set limits to allow full range for testing
        store.update_user_limits(-80.0, 0.0)
        return store

    @pytest.mark.asyncio
    async def test_zone_delta_preserves_relative_offsets(self, store):
        """
        AC1: Zone delta preserves relative offsets between clients.

        Given clients at different volumes, when zone delta applied,
        the difference between client volumes should be preserved.
        """
        # Setup: zone with clients at different volumes (5dB difference)
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-a', 'client-b']
            )
        }
        store._clients = {
            'client-a': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=True)
        }

        # Action: apply +5dB delta
        updates = await store.apply_zone_delta('zone_1', 5.0)

        # Assert: both clients moved by delta, relative difference preserved
        assert updates['client-a'] == -15.0  # -20 + 5
        assert updates['client-b'] == -20.0  # -25 + 5
        # Difference is still 5dB
        assert updates['client-a'] - updates['client-b'] == 5.0

    @pytest.mark.asyncio
    async def test_zone_delta_only_affects_online_clients(self, store):
        """
        AC2: Only ONLINE clients receive immediate volume change.

        OFFLINE clients should not be included in the updates dict.
        """
        # Setup: zone with mixed ONLINE/OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['online-client', 'offline-client']
            )
        }
        store._clients = {
            'online-client': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'offline-client': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }

        # Action: apply delta
        updates = await store.apply_zone_delta('zone_1', 3.0)

        # Assert: only online client in updates
        assert 'online-client' in updates
        assert 'offline-client' not in updates
        assert updates['online-client'] == -27.0  # -30 + 3

    @pytest.mark.asyncio
    async def test_zone_delta_returns_empty_when_all_offline(self, store):
        """
        AC2: Zone with all clients OFFLINE returns no updates.
        """
        # Setup: zone with all OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-1', 'client-2']
            )
        }
        store._clients = {
            'client-1': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False),
            'client-2': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=False)
        }

        # Action: apply delta
        updates = await store.apply_zone_delta('zone_1', 5.0)

        # Assert: empty updates dict
        assert updates == {}

    @pytest.mark.asyncio
    async def test_zone_delta_clamps_at_min_limit(self, store):
        """
        AC3: Volume clamped at minimum limit during delta application.
        """
        store.update_user_limits(-80.0, -21.0)

        # Setup: client near minimum
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-a']
            )
        }
        store._clients = {
            'client-a': ClientVolume(volume_db=-75.0, offset_db=0.0, mute=False, available=True)
        }

        # Action: apply large negative delta that would exceed min
        updates = await store.apply_zone_delta('zone_1', -10.0)

        # Assert: clamped to minimum
        assert updates['client-a'] == -80.0  # Clamped, not -85

    @pytest.mark.asyncio
    async def test_zone_delta_clamps_at_max_limit(self, store):
        """
        AC3: Volume clamped at maximum limit during delta application.
        """
        store.update_user_limits(-80.0, -21.0)

        # Setup: client near maximum
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-a']
            )
        }
        store._clients = {
            'client-a': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=True)
        }

        # Action: apply delta that would exceed max
        updates = await store.apply_zone_delta('zone_1', 10.0)

        # Assert: clamped to maximum
        assert updates['client-a'] == -21.0  # Clamped, not -15

    @pytest.mark.asyncio
    async def test_zone_delta_raises_for_unknown_zone(self, store):
        """
        AC3: apply_zone_delta raises ValueError for unknown zone.
        """
        store._zones = {}

        # Action & Assert: should raise ValueError
        with pytest.raises(ValueError, match="Unknown zone"):
            await store.apply_zone_delta('nonexistent_zone', 5.0)

    @pytest.mark.asyncio
    async def test_apply_zone_updates_persists_volumes(self, store):
        """
        AC3: apply_zone_updates updates client volumes in state.
        """
        # Setup: existing clients
        store._clients = {
            'client-a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True)
        }

        # Mock persist to avoid file I/O
        store._persist_state = AsyncMock()

        # Action: apply updates
        await store.apply_zone_updates({'client-a': -25.0, 'client-b': -30.0})

        # Assert: volumes updated
        assert store._clients['client-a'].volume_db == -25.0
        assert store._clients['client-b'].volume_db == -30.0
        # Verify persist was called
        store._persist_state.assert_called_once()


# ==============================================================================
# Task 4: Unit tests for zone average calculation (Story 3.2 AC#4)
# ==============================================================================


class TestZoneAverageCalculation:
    """Tests for zone average volume calculation (Story 3.2 AC#4)."""

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

    def test_zone_average_computed_from_online_clients_only(self, store):
        """
        AC4: Zone average computed from ONLINE clients only.
        """
        # Setup: zone with mixed ONLINE/OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['online-1', 'online-2', 'offline-1']
            )
        }
        store._clients = {
            'online-1': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=True),
            'online-2': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'offline-1': ClientVolume(volume_db=-50.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        average = store.compute_zone_average('zone_1')

        # Assert: average of only online clients (-20 + -30) / 2 = -25
        assert average == pytest.approx(-25.0, rel=1e-6)

    def test_zone_average_returns_default_when_no_online_clients(self, store):
        """
        AC4: Zone average returns DEFAULT_VOLUME_DB when no clients ONLINE.
        """
        # Setup: zone with all OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['offline-1', 'offline-2']
            )
        }
        store._clients = {
            'offline-1': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=False),
            'offline-2': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }

        # Action
        average = store.compute_zone_average('zone_1')

        # Assert: returns default volume
        assert average == DEFAULT_VOLUME_DB

    def test_zone_average_returns_default_for_unknown_zone(self, store):
        """
        AC4: Zone average returns DEFAULT_VOLUME_DB for unknown zone.
        """
        store._zones = {}

        # Action
        average = store.compute_zone_average('nonexistent')

        # Assert: returns default
        assert average == DEFAULT_VOLUME_DB

    def test_zone_average_single_online_client(self, store):
        """
        AC4: Zone average equals client volume when only one client online.
        """
        # Setup: single online client
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-1']
            )
        }
        store._clients = {
            'client-1': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True)
        }

        # Action
        average = store.compute_zone_average('zone_1')

        # Assert: equals client's volume
        assert average == -35.0

    @pytest.mark.asyncio
    async def test_zone_average_updates_after_client_volume_change(self, store):
        """
        AC4: Zone average updates after client volume change.
        """
        # Setup: set limits to allow full range for testing
        store.update_user_limits(-80.0, 0.0)

        # Setup: zone with clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client-1', 'client-2']
            )
        }
        store._clients = {
            'client-1': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=True),
            'client-2': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True)
        }

        # Verify initial average
        assert store.compute_zone_average('zone_1') == pytest.approx(-25.0, rel=1e-6)

        # Mock persist to avoid file I/O
        store._persist_state = AsyncMock()

        # Action: change client-1 volume
        await store.set_client_volume('client-1', -10.0)

        # Assert: average updated
        # New average: (-10 + -30) / 2 = -20
        assert store.compute_zone_average('zone_1') == pytest.approx(-20.0, rel=1e-6)

    def test_zone_average_includes_muted_clients(self, store):
        """
        AC4: Zone average includes muted clients (volume still counts).
        """
        # Setup: zone with muted client
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['muted-client', 'normal-client']
            )
        }
        store._clients = {
            'muted-client': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=True, available=True),
            'normal-client': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True)
        }

        # Action
        average = store.compute_zone_average('zone_1')

        # Assert: muted client's volume is included
        assert average == pytest.approx(-25.0, rel=1e-6)
