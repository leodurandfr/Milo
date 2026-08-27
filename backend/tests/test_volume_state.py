# backend/tests/test_volume_state.py
"""
Unit tests for VolumeStateStore - Single Source of Truth for volume state.
"""
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock
from backend.core.volume.state import VolumeStateStore, ZoneConfig
from backend.core.models.volume import VolumeConfig
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


# ==============================================================================
# Unit tests for zone volume delta
# ==============================================================================


class TestZoneVolumeDelta:
    """Tests for zone volume delta functionality."""

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
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))
        return store

    @pytest.mark.asyncio
    async def test_zone_delta_preserves_relative_offsets(self, store):
        """
        Zone delta preserves relative offsets between clients.

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
    async def test_zone_delta_covers_an_offline_member_too(self, store):
        """An OFFLINE member is in the updates: a delta is relative.

        Was the opposite rule until 2026-08-21. Excluding the absent member made
        it miss the adjustment permanently and come back at the wrong level for
        its room. The caller re-splits this dict on availability, so including
        it costs no hardware call — see apply_zone_volume_delta.
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

        # Assert: both members moved by the delta
        assert updates['online-client'] == -27.0  # -30 + 3
        assert updates['offline-client'] == -27.0  # -30 + 3

    @pytest.mark.asyncio
    async def test_zone_delta_covers_a_zone_that_is_entirely_offline(self, store):
        """
        A zone whose members are all OFFLINE still records the delta.
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

        # Assert: each member moved by the delta, relative offset kept
        assert updates == {'client-1': -25.0, 'client-2': -20.0}

    @pytest.mark.asyncio
    async def test_zone_delta_clamps_at_min_limit(self, store):
        """
        Volume clamped at minimum limit during delta application.
        """
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0))

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
        Volume clamped at maximum limit during delta application.
        """
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0))

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
        apply_zone_delta raises ValueError for unknown zone.
        """
        store._zones = {}

        # Action & Assert: should raise ValueError
        with pytest.raises(ValueError, match="Unknown zone"):
            await store.apply_zone_delta('nonexistent_zone', 5.0)

    @pytest.mark.asyncio
    async def test_apply_zone_updates_persists_volumes(self, store):
        """
        apply_zone_updates updates client volumes in state.
        """
        # Setup: existing clients
        store._clients = {
            'client-a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True)
        }

        # Mock persist to avoid file I/O
        store._schedule_persist = MagicMock()

        # Action: apply updates
        await store.apply_zone_updates({'client-a': -25.0, 'client-b': -30.0})

        # Assert: volumes updated
        assert store._clients['client-a'].volume_db == -25.0
        assert store._clients['client-b'].volume_db == -30.0
        # Verify persist was scheduled
        store._schedule_persist.assert_called_once()


# ==============================================================================
# Unit tests for zone average calculation
# ==============================================================================


class TestZoneAverageCalculation:
    """Tests for zone average volume calculation."""

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
        Zone average computed from ONLINE clients only.
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
        Zone average returns DEFAULT_VOLUME_DB when no clients ONLINE.
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
        Zone average returns DEFAULT_VOLUME_DB for unknown zone.
        """
        store._zones = {}

        # Action
        average = store.compute_zone_average('nonexistent')

        # Assert: returns default
        assert average == DEFAULT_VOLUME_DB

    def test_zone_average_single_online_client(self, store):
        """
        Zone average equals client volume when only one client online.
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
        Zone average updates after client volume change.
        """
        # Setup: set limits to allow full range for testing
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))

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

        # Action: change client-1 volume
        await store.set_client_volume('client-1', -10.0)

        # Assert: average updated
        # New average: (-10 + -30) / 2 = -20
        assert store.compute_zone_average('zone_1') == pytest.approx(-20.0, rel=1e-6)

    def test_zone_average_includes_muted_clients(self, store):
        """
        Zone average includes muted clients (volume still counts).
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
