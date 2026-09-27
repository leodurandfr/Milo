# backend/tests/test_volume_state.py
"""
Unit tests for VolumeStateStore - Single Source of Truth for volume state.
"""
import asyncio
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock
from backend.core.volume.state import VolumeStateStore, ZoneConfig
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import ClientVolume
from backend.config.constants import DEFAULT_VOLUME_DB


# ==============================================================================
# Unit tests for zone volume delta
# ==============================================================================


class TestZoneVolumeDelta:
    """A zone moves as a block: VolumeService._move over the store's levels.

    The store no longer computes moves: a move reads the levels, bounds the
    delta and writes them in one synchronous step in the service.
    """

    @pytest.fixture
    def service(self):
        from backend.core.volume import VolumeService

        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        svc = VolumeService(state_machine=Mock(broadcast=AsyncMock()), snapcast_service=Mock(),
                            settings_service=settings)
        svc._volume_config = VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0)
        svc._state_store.set_volume_config(svc._volume_config)
        svc._state_store._schedule_persist = MagicMock()
        svc._update_startup_volume_if_needed = AsyncMock()
        svc.broadcast_volume_state = AsyncMock()
        svc.sent = {}

        def _submit(mac_id, volume_db, force=False):
            svc.sent[mac_id] = svc._volume_config.clamp(volume_db)
            done = asyncio.get_running_loop().create_future()
            done.set_result(True)
            return done

        svc._equalizer_controller.submit_volume = _submit
        return svc

    @staticmethod
    def _zone(service, clients):
        """Zone 'zone_1' holding `clients` ({mac: (level, available)})."""
        service._state_store._zones = {
            'zone_1': ZoneConfig(zone_id='zone_1', name='Test Zone', client_ids=list(clients))
        }
        service._state_store._clients = {
            mac: ClientVolume(volume_db=level, offset_db=0.0, mute=False, available=available)
            for mac, (level, available) in clients.items()
        }
        service._client_registry = Mock(get_online_client_ids=Mock(
            return_value=[mac for mac, (_, available) in clients.items() if available]
        ))

    @staticmethod
    def _level(service, mac):
        return service._state_store.get_client_volume(mac)

    async def test_zone_delta_preserves_relative_offsets(self, service):
        """
        Zone delta preserves relative offsets between clients.

        Given clients at different volumes, when zone delta applied,
        the difference between client volumes should be preserved.
        """
        self._zone(service, {'client-a': (-20.0, True), 'client-b': (-25.0, True)})

        await service.apply_zone_volume_delta('zone_1', 5.0)

        assert self._level(service, 'client-a') == -15.0
        assert self._level(service, 'client-b') == -20.0
        assert service.sent == {'client-a': -15.0, 'client-b': -20.0}

    async def test_zone_delta_covers_an_offline_member_too(self, service):
        """An OFFLINE member moves too: a delta is relative.

        Was the opposite rule until 2026-08-21. Excluding the absent member made
        it miss the adjustment permanently and come back at the wrong level for
        its room. It costs no hardware call: only the reachable one is sent.
        """
        self._zone(service, {'online-client': (-30.0, True), 'offline-client': (-30.0, False)})

        await service.apply_zone_volume_delta('zone_1', 3.0)

        assert self._level(service, 'online-client') == -27.0
        assert self._level(service, 'offline-client') == -27.0
        assert service.sent == {'online-client': -27.0}

    async def test_zone_delta_covers_a_zone_that_is_entirely_offline(self, service):
        """
        A zone whose members are all OFFLINE still records the delta.
        """
        self._zone(service, {'client-1': (-30.0, False), 'client-2': (-25.0, False)})

        await service.apply_zone_volume_delta('zone_1', 5.0)

        assert self._level(service, 'client-1') == -25.0
        assert self._level(service, 'client-2') == -20.0
        assert service.sent == {}

    async def test_a_block_going_down_stops_when_its_quietest_room_reaches_the_minimum(self, service):
        """The quietest reachable room meets the floor first, and the block stops there.

        Consumer: a zone slider pulled to the bottom. Clamping each room instead
        collapsed a zone onto the floor (measured: -78 / -77.95 / -78), its
        distances gone for good. Fails if a room is sent past the minimum, or if
        the rooms stop keeping their distance.
        """
        service._volume_config = VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0)
        self._zone(service, {'client-a': (-60.0, True), 'client-b': (-75.0, True)})

        await service.apply_zone_volume_delta('zone_1', -10.0)

        assert self._level(service, 'client-a') == -65.0
        assert self._level(service, 'client-b') == -80.0

    async def test_a_block_going_up_stops_when_its_loudest_room_reaches_the_maximum(self, service):
        """Going up, the block stops when its loudest reachable room hits the ceiling.

        Consumer: a zone slider pushed to the top. Fails if any room is asked
        louder than the operator's maximum.
        """
        service._volume_config = VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0)
        self._zone(service, {'client-a': (-25.0, True), 'client-b': (-35.0, True)})

        await service.apply_zone_volume_delta('zone_1', 10.0)

        assert self._level(service, 'client-a') == -21.0
        assert self._level(service, 'client-b') == -31.0

    async def test_zone_delta_raises_for_unknown_zone(self, service):
        """
        A delta asked of an unknown zone raises ValueError (the route answers 404).
        """
        service._state_store._zones = {}

        with pytest.raises(ValueError, match="Unknown zone"):
            await service.apply_zone_volume_delta('nonexistent_zone', 5.0)

    async def test_set_levels_writes_and_persists_volumes(self, service):
        """
        set_levels updates client volumes in state and schedules the write.
        """
        store = service._state_store
        store._clients = {
            'client-a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True)
        }

        store.set_levels({'client-a': -25.0, 'client-b': -30.0})

        assert store._clients['client-a'].volume_db == -25.0
        assert store._clients['client-b'].volume_db == -30.0
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
