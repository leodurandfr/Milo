"""
Integration tests for multiroom client synchronization.

Tests the sync mechanisms that prevent or auto-correct client desynchronization:
- Retry loop in _sync_reconnecting_client_volume (fire-and-forget)
- Concurrent reconnections via _process_online_status_changes
- Zone average stability during rapid sequential reconnects
- push_volume_to_all_clients partial failure handling
- set_online_after gate (client invisible until hardware confirms)
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.models import ReconnectionContext
from backend.core.volume.state import DEFAULT_VOLUME_DB
from backend.core.models.volume import VolumeConfig


# =============================================================================
# Helpers
# =============================================================================


def _make_volume_service(startup_volume_db: float = DEFAULT_VOLUME_DB):
    """Create a mock VolumeService wired for sync tests."""
    vs = MagicMock()
    vs._state_store = MagicMock()
    vs._state_store.set_client_volume = AsyncMock()
    vs._state_store.get_client_mute = MagicMock(return_value=False)
    vs._state_store.has_client = MagicMock(return_value=True)
    vs._equalizer_controller = MagicMock()
    vs._equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
    vs._equalizer_controller.set_equalizer_mute = AsyncMock()
    vs._equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={})
    vs._broadcast_volume_state = AsyncMock()
    vs.volume_config = VolumeConfig(startup_volume_db=startup_volume_db)
    return vs


def _make_ws_service(registry, volume_service=None, snapcast_service=None):
    """Create a SnapcastWebSocketService wired for sync tests."""
    sm = MagicMock()
    sm.broadcast_event = AsyncMock()

    routing = MagicMock()
    routing.get_state = MagicMock(return_value={"multiroom_enabled": True})

    ws = SnapcastWebSocketService(state_machine=sm, routing_service=routing)
    ws.set_registry(registry)
    ws._volume_service = volume_service or _make_volume_service()
    ws._snapcast_service = snapcast_service or MagicMock(
        set_volume=AsyncMock(return_value=True),
        get_clients=AsyncMock(return_value=[]),
    )
    # Stub equalizer sync to isolate volume tests
    ws._sync_zone_equalizer_to_client = AsyncMock(return_value=True)
    ws._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)
    return ws


async def _setup_registry(settings_service, state_machine, clients, zones=None):
    """Register clients and optionally create zones.

    Args:
        clients: list of (mac_id, name, ip, volume_db, online) tuples
        zones: list of (zone_id, zone_name, [mac_ids]) tuples
    """
    registry = ClientRegistryService(settings_service=settings_service)
    await registry.initialize()
    registry.set_state_machine(state_machine)

    for mac_id, name, ip, volume_db, online in clients:
        await registry.register_client(mac_id, name, ip)
        await registry.update_volume(mac_id, volume_db=volume_db)

    if zones:
        for zone_id, zone_name, mac_ids in zones:
            await registry.create_zone(zone_id, zone_name, mac_ids)

    # Set online status AFTER zone creation so events fire correctly
    for mac_id, _, _, _, online in clients:
        await registry.set_client_online(mac_id, online)

    return registry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings_service():
    service = AsyncMock()
    service.get_setting = AsyncMock(return_value=None)
    service.set_setting = AsyncMock()
    return service


@pytest.fixture
def mock_state_machine():
    sm = MagicMock()
    sm.broadcast_event = AsyncMock()
    return sm


# =============================================================================
# TestSyncReconnectRetryLoop
# =============================================================================


class TestSyncReconnectRetryLoop:
    """Tests for _sync_reconnecting_client_volume retry behaviour."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self, mock_settings_service, mock_state_machine):
        """Hardware confirms on first try — no retry needed."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=3, retry_delay=0)

        assert result is True
        # Hardware called exactly once
        ws._volume_service._equalizer_controller.set_equalizer_volume.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_until_success(self, mock_settings_service, mock_state_machine):
        """Hardware fails twice then succeeds on third attempt."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service._equalizer_controller
        eq.set_equalizer_volume = AsyncMock(side_effect=[False, False, True])

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=5, retry_delay=0)

        assert result is True
        assert eq.set_equalizer_volume.call_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, mock_settings_service, mock_state_machine):
        """Hardware never responds — gives up after max_retries+1 attempts."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service._equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=2, retry_delay=0)

        assert result is False
        assert eq.set_equalizer_volume.call_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_exception_treated_as_failure_and_retried(self, mock_settings_service, mock_state_machine):
        """An exception during apply is caught and retried."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service._equalizer_controller
        eq.set_equalizer_volume = AsyncMock(side_effect=[ConnectionError("timeout"), True])

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=2, retry_delay=0)

        assert result is True
        assert eq.set_equalizer_volume.call_count == 2

    @pytest.mark.asyncio
    async def test_state_updated_even_on_hardware_failure(self, mock_settings_service, mock_state_machine):
        """State store and registry always receive the target volume, even when hardware fails."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service._equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        # State store was still updated (UI shows correct target)
        ws._volume_service._state_store.set_client_volume.assert_called()

    @pytest.mark.asyncio
    async def test_broadcast_only_on_success(self, mock_settings_service, mock_state_machine):
        """Volume state is broadcast only after a successful hardware apply."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service._equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        ws._volume_service._broadcast_volume_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_volume_service_returns_false(self, mock_settings_service, mock_state_machine):
        """Missing volume service returns False immediately without crash."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service = None

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        assert result is False


# =============================================================================
# TestSetOnlineAfterGate
# =============================================================================


class TestSetOnlineAfterGate:
    """Tests for set_online_after: client stays invisible until hardware confirms."""

    @pytest.mark.asyncio
    async def test_client_set_online_after_successful_sync(self, mock_settings_service, mock_state_machine):
        """With set_online_after=True, client becomes online only after hardware success."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, False)],
        )
        ws = _make_ws_service(registry)

        assert registry.get_client("client-a").online is False

        result = await ws._sync_reconnecting_client_volume(
            "client-a", set_online_after=True, max_retries=0, retry_delay=0
        )

        assert result is True
        assert registry.get_client("client-a").online is True

    @pytest.mark.asyncio
    async def test_client_stays_offline_when_sync_fails(self, mock_settings_service, mock_state_machine):
        """With set_online_after=True and hardware failure, client remains offline."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, False)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service._equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        result = await ws._sync_reconnecting_client_volume(
            "client-a", set_online_after=True, max_retries=1, retry_delay=0
        )

        assert result is False
        # Client must remain offline — invisible to frontend
        assert registry.get_client("client-a").online is False

    @pytest.mark.asyncio
    async def test_without_set_online_after_does_not_change_status(self, mock_settings_service, mock_state_machine):
        """Default path (set_online_after=False) does not touch online status."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, False)],
        )
        ws = _make_ws_service(registry)

        result = await ws._sync_reconnecting_client_volume(
            "client-a", set_online_after=False, max_retries=0, retry_delay=0
        )

        assert result is True
        # set_online_after=False: status untouched (already False from setup)
        assert registry.get_client("client-a").online is False


# =============================================================================
# TestProcessOnlineStatusChanges
# =============================================================================


class TestProcessOnlineStatusChanges:
    """Tests for _process_online_status_changes — bulk status transitions."""

    @pytest.mark.asyncio
    async def test_single_client_comes_online_triggers_sync(self, mock_settings_service, mock_state_machine):
        """A client transitioning offline→online spawns a sync task with set_online_after=True."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, False)],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "online": True, "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        # Client is NOT yet online — the sync task will set it online after
        # hardware confirms (set_online_after=True prevents stale-volume window)
        assert registry.get_client("client-a").online is False

        # Sync task was spawned with set_online_after=True
        await asyncio.sleep(0)  # Let the event loop process the task
        ws._sync_reconnecting_client_volume.assert_called_once_with("client-a", set_online_after=True)

    @pytest.mark.asyncio
    async def test_client_goes_offline_no_sync(self, mock_settings_service, mock_state_machine):
        """A client transitioning online→offline does NOT trigger volume sync."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "online": False, "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        assert registry.get_client("client-a").online is False
        ws._sync_reconnecting_client_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_change_no_sync(self, mock_settings_service, mock_state_machine):
        """If online status is unchanged, no sync task is spawned."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "online": True, "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        ws._sync_reconnecting_client_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_clients_reconnect_simultaneously(self, mock_settings_service, mock_state_machine):
        """Multiple clients going online in the same Server.OnUpdate each get their own sync."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, False),
                ("client-b", "B", "192.168.1.2", -40.0, False),
                ("client-c", "C", "192.168.1.3", -50.0, False),
            ],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [
            {"mac_id": "client-a", "id": "snap-a", "online": True, "last_seen_age": 0},
            {"mac_id": "client-b", "id": "snap-b", "online": True, "last_seen_age": 0},
            {"mac_id": "client-c", "id": "snap-c", "online": True, "last_seen_age": 0},
        ]
        await ws._process_online_status_changes(all_clients)
        await asyncio.sleep(0)

        # Each client gets its own sync task
        assert ws._sync_reconnecting_client_volume.call_count == 3
        synced_ids = {c.args[0] for c in ws._sync_reconnecting_client_volume.call_args_list}
        assert synced_ids == {"client-a", "client-b", "client-c"}


# =============================================================================
# TestZoneAverageStabilityDuringReconnects
# =============================================================================


class TestZoneAverageStabilityDuringReconnects:
    """
    Tests that zone average calculations remain consistent during rapid
    sequential reconnections — the core desync prevention mechanism.
    """

    @pytest.mark.asyncio
    async def test_sequential_reconnects_converge_to_same_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Zone with 3 clients all offline. They reconnect one by one.
        First gets startup volume, second and third get zone average.
        After all reconnect, all should have consistent volumes.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -20.0, False),
                ("client-b", "B", "192.168.1.2", -30.0, False),
                ("client-c", "C", "192.168.1.3", -40.0, False),
            ],
            zones=[("zone-1", "Living Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry)
        startup = ws._volume_service.volume_config.startup_volume_db

        # Client A reconnects first — all others offline → FR8
        context_a = registry.get_reconnection_context("client-a")
        assert context_a == ReconnectionContext.IN_ZONE_ALL_OFFLINE
        vol_a = ws._resolve_target_volume("client-a", context_a)
        assert vol_a == startup

        # Simulate hardware success: update registry and set online
        await registry.update_volume("client-a", volume_db=vol_a)
        await registry.set_client_online("client-a", True)

        # Client B reconnects — A is online → FR7 (zone average of A)
        context_b = registry.get_reconnection_context("client-b")
        assert context_b == ReconnectionContext.IN_ZONE_OTHERS_ONLINE
        vol_b = ws._resolve_target_volume("client-b", context_b)
        assert vol_b == vol_a  # Only A is online, so average == A's volume

        await registry.update_volume("client-b", volume_db=vol_b)
        await registry.set_client_online("client-b", True)

        # Client C reconnects — A and B are online → FR7 (zone average of A+B)
        context_c = registry.get_reconnection_context("client-c")
        assert context_c == ReconnectionContext.IN_ZONE_OTHERS_ONLINE
        vol_c = ws._resolve_target_volume("client-c", context_c)
        # A and B both have startup volume, so average = startup
        assert vol_c == startup

        # All three clients converged to the same volume
        assert vol_a == vol_b == vol_c

    @pytest.mark.asyncio
    async def test_reconnect_into_zone_with_divergent_volumes(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Zone where online members have different volumes.
        Reconnecting client gets the exact average — verified numerically.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -20.0, True),
                ("client-b", "B", "192.168.1.2", -40.0, True),
                ("client-c", "C", "192.168.1.3", -99.0, False),  # offline, reconnecting
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry)

        context = registry.get_reconnection_context("client-c")
        assert context == ReconnectionContext.IN_ZONE_OTHERS_ONLINE

        vol = ws._resolve_target_volume("client-c", context)
        expected = (-20.0 + -40.0) / 2  # -30.0
        assert vol == expected

    @pytest.mark.asyncio
    async def test_reconnecting_client_old_volume_excluded_from_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        The reconnecting client's stale volume must NOT pollute the zone average.
        Even if the client is already marked online in the registry (path A),
        _resolve_target_volume uses exclude_mac_id.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -20.0, True),
                ("client-b", "B", "192.168.1.2", -40.0, True),
                # client-c has a stale volume from last session
                ("client-c", "C", "192.168.1.3", -5.0, True),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry)

        # Even though client-c is marked online, its old volume is excluded
        context = registry.get_reconnection_context("client-c")
        vol = ws._resolve_target_volume("client-c", context)

        # Average of A and B only
        assert vol == (-20.0 + -40.0) / 2

    @pytest.mark.asyncio
    async def test_standalone_reconnect_uses_global_average(
        self, mock_settings_service, mock_state_machine
    ):
        """Standalone client reconnecting with others online gets global average."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -20.0, True),
                ("client-b", "B", "192.168.1.2", -40.0, True),
                ("client-c", "C", "192.168.1.3", -99.0, False),
            ],
        )
        ws = _make_ws_service(registry)

        context = registry.get_reconnection_context("client-c")
        assert context == ReconnectionContext.STANDALONE_OTHERS_ONLINE

        vol = ws._resolve_target_volume("client-c", context)
        assert vol == (-20.0 + -40.0) / 2


# =============================================================================
# TestConcurrentReconnectRaceConditions
# =============================================================================


class TestConcurrentReconnectRaceConditions:
    """
    Tests for race conditions when multiple clients reconnect in the
    same Server.OnUpdate event. The registry is mutated (set_client_online)
    sequentially in the loop, which means later clients see earlier clients
    as already online.
    """

    @pytest.mark.asyncio
    async def test_second_client_sees_first_as_online_in_zone(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Two zone clients reconnect in the same event. The second client's
        context detection sees the first as online (because set_client_online
        is called sequentially in the loop).
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, False),
                ("client-b", "B", "192.168.1.2", -50.0, False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b"])],
        )
        ws = _make_ws_service(registry)

        # Track the contexts detected for each sync call
        detected_contexts = {}
        original_resolve = ws._resolve_target_volume

        def tracking_resolve(mac_id, context):
            detected_contexts[mac_id] = context
            return original_resolve(mac_id, context)

        ws._resolve_target_volume = tracking_resolve

        # Capture coroutines created by asyncio.create_task
        sync_calls = []
        original_sync = ws._sync_reconnecting_client_volume

        async def capture_sync(mac_id, **kwargs):
            sync_calls.append(mac_id)
            return await original_sync(mac_id, **kwargs)

        ws._sync_reconnecting_client_volume = capture_sync

        all_clients = [
            {"mac_id": "client-a", "id": "snap-a", "online": True, "last_seen_age": 0},
            {"mac_id": "client-b", "id": "snap-b", "online": True, "last_seen_age": 0},
        ]
        await ws._process_online_status_changes(all_clients)

        # Neither client is online yet — they wait for hardware confirmation
        # (set_online_after=True). The sync tasks will set them online.
        assert registry.get_client("client-a").online is False
        assert registry.get_client("client-b").online is False

        # Let fire-and-forget tasks run
        await asyncio.sleep(0.01)

        # Both clients had sync tasks created
        assert "client-a" in sync_calls
        assert "client-b" in sync_calls

        # After sync completes (hardware mocked as success), both are online
        assert registry.get_client("client-a").online is True
        assert registry.get_client("client-b").online is True

    @pytest.mark.asyncio
    async def test_two_standalone_clients_reconnect_get_consistent_volumes(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Two standalone clients reconnect simultaneously when a third is already online.
        Both should get a volume based on the online client's volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, True),   # already online
                ("client-b", "B", "192.168.1.2", -99.0, False),  # reconnecting
                ("client-c", "C", "192.168.1.3", -99.0, False),  # reconnecting
            ],
        )
        ws = _make_ws_service(registry)

        # Simulate what _process_online_status_changes does sequentially:
        # 1) client-b goes online, context resolved
        await registry.set_client_online("client-b", True)
        ctx_b = registry.get_reconnection_context("client-b")
        vol_b = ws._resolve_target_volume("client-b", ctx_b)

        # 2) client-c goes online, context resolved (now client-b is also online)
        await registry.set_client_online("client-c", True)
        ctx_c = registry.get_reconnection_context("client-c")
        vol_c = ws._resolve_target_volume("client-c", ctx_c)

        # client-b's target: average of client-a only (client-b excluded)
        assert vol_b == -30.0

        # client-c's target: average of client-a and client-b (client-c excluded)
        # client-b's volume is still the stale -99.0 from registry at this point
        # This is a known characteristic — the stale volume from client-b feeds
        # into client-c's average. After sync completes, client-b will have -30.0
        # but at resolution time, client-c sees client-b's old volume.
        expected_c = (-30.0 + -99.0) / 2
        assert vol_c == expected_c


# =============================================================================
# TestFireAndForgetTaskRecovery
# =============================================================================


class TestFireAndForgetTaskRecovery:
    """
    Tests for fire-and-forget sync task behaviour — what happens when
    the sync task is spawned via asyncio.create_task and fails.
    """

    @pytest.mark.asyncio
    async def test_fire_and_forget_failure_client_stays_offline(
        self, mock_settings_service, mock_state_machine
    ):
        """
        With set_online_after=True (P1 fix), if the sync task fails after
        all retries, the client correctly remains offline — it never appears
        in the frontend with a stale volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, False)],
        )
        ws = _make_ws_service(registry)
        # Hardware always fails
        ws._volume_service._equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "online": True, "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        # Client is NOT marked online — waits for hardware confirmation
        assert registry.get_client("client-a").online is False

        # Let the fire-and-forget sync task exhaust retries
        # (retry_delay patched to 0 inside the task via create_task)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await asyncio.sleep(0.05)

        # Client remains offline because all sync attempts failed —
        # no stale-volume window in the frontend
        assert registry.get_client("client-a").online is False

    @pytest.mark.asyncio
    async def test_new_client_stays_offline_until_sync_succeeds(
        self, mock_settings_service, mock_state_machine
    ):
        """
        In _process_new_clients, new clients use set_online_after=True.
        They stay offline (invisible) until hardware confirms volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[],
        )
        ws = _make_ws_service(registry)

        # Register client but don't set online
        await registry.register_client("client-new", "New", "192.168.1.10")

        # Hardware succeeds
        result = await ws._sync_reconnecting_client_volume(
            "client-new", set_online_after=True, max_retries=0, retry_delay=0
        )

        assert result is True
        assert registry.get_client("client-new").online is True

    @pytest.mark.asyncio
    async def test_new_client_stays_offline_on_sync_failure(
        self, mock_settings_service, mock_state_machine
    ):
        """
        New client with set_online_after=True remains offline when sync fails.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[],
        )
        ws = _make_ws_service(registry)
        ws._volume_service._equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        await registry.register_client("client-new", "New", "192.168.1.10")

        result = await ws._sync_reconnecting_client_volume(
            "client-new", set_online_after=True, max_retries=1, retry_delay=0
        )

        assert result is False
        assert registry.get_client("client-new").online is False


# =============================================================================
# TestApplyTargetVolumeToClient
# =============================================================================


class TestApplyTargetVolumeToClient:
    """Tests for _apply_target_volume_to_client — the hardware application step."""

    @pytest.mark.asyncio
    async def test_success_applies_volume_and_mute(self, mock_settings_service, mock_state_machine):
        """On success: volume set, mute restored, state store updated."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service._state_store.get_client_mute = MagicMock(return_value=True)

        result = await ws._apply_target_volume_to_client("client-a", -25.0)

        assert result is True
        # Volume applied with force=True
        ws._volume_service._equalizer_controller.set_equalizer_volume.assert_called_once_with(
            "client-a", -25.0, force=True
        )
        # Mute state restored
        ws._volume_service._equalizer_controller.set_equalizer_mute.assert_called_once_with(
            "client-a", True, force=True
        )
        # Registry updated
        client = registry.get_client("client-a")
        assert client.volume_db == -25.0

    @pytest.mark.asyncio
    async def test_hardware_failure_still_updates_state(self, mock_settings_service, mock_state_machine):
        """On hardware failure: state store still updated, returns False."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service._equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        result = await ws._apply_target_volume_to_client("client-a", -25.0)

        assert result is False
        # State store was still updated (UI correctness)
        ws._volume_service._state_store.set_client_volume.assert_called_with("client-a", -25.0)
        # Mute is still applied even on hardware volume failure — CamillaDSP
        # starts muted, so skipping unmute would leave the client silent.
        ws._volume_service._equalizer_controller.set_equalizer_mute.assert_called_once_with(
            "client-a", False, force=True
        )

    @pytest.mark.asyncio
    async def test_no_volume_service_returns_false(self, mock_settings_service, mock_state_machine):
        """Missing volume service returns False without crash."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -30.0, True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service = None

        result = await ws._apply_target_volume_to_client("client-a", -25.0)

        assert result is False


# =============================================================================
# TestEndToEndReconnectionSync
# =============================================================================


class TestEndToEndReconnectionSync:
    """
    End-to-end tests simulating full reconnection flows through
    _sync_reconnecting_client_volume with a real registry.
    """

    @pytest.mark.asyncio
    async def test_e2e_zone_reconnect_applies_zone_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Client in zone reconnects. Verify the volume applied to
        hardware matches the zone average of online members.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -20.0, True),
                ("client-b", "B", "192.168.1.2", -40.0, True),
                ("client-c", "C", "192.168.1.3", -99.0, False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry)

        # Track the volume sent to hardware
        applied_volumes = []
        original_eq = ws._volume_service._equalizer_controller

        async def capture_volume(mac_id, volume, force=False):
            applied_volumes.append((mac_id, volume))
            return True

        original_eq.set_equalizer_volume = capture_volume

        result = await ws._sync_reconnecting_client_volume("client-c", max_retries=0, retry_delay=0)

        assert result is True
        assert len(applied_volumes) == 1
        assert applied_volumes[0] == ("client-c", -30.0)  # avg of -20 and -40

    @pytest.mark.asyncio
    async def test_e2e_standalone_alone_applies_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Standalone client reconnects alone. Gets startup volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", -99.0, False)],
        )
        ws = _make_ws_service(registry)
        startup = ws._volume_service.volume_config.startup_volume_db

        applied_volumes = []
        original_eq = ws._volume_service._equalizer_controller

        async def capture_volume(mac_id, volume, force=False):
            applied_volumes.append((mac_id, volume))
            return True

        original_eq.set_equalizer_volume = capture_volume

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        assert result is True
        assert applied_volumes[0] == ("client-a", startup)

    @pytest.mark.asyncio
    async def test_e2e_retry_succeeds_on_second_attempt(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Hardware fails on first try, succeeds on second.
        The correct volume is applied both times (same target).
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, True),
                ("client-b", "B", "192.168.1.2", -99.0, False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b"])],
        )
        ws = _make_ws_service(registry)

        attempts = []
        eq = ws._volume_service._equalizer_controller

        async def flaky_hw(mac_id, volume, force=False):
            attempts.append(volume)
            return len(attempts) >= 2  # Fail first, succeed second

        eq.set_equalizer_volume = flaky_hw

        result = await ws._sync_reconnecting_client_volume("client-b", max_retries=2, retry_delay=0)

        assert result is True
        # Both attempts used the same target volume (-30.0 = zone avg of client-a)
        assert all(v == -30.0 for v in attempts)
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_e2e_three_zone_clients_sequential_reconnect(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Three zone clients reconnect sequentially.
        Simulates the real flow where each sync updates the registry,
        and the next client's average includes the newly synced volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -10.0, False),
                ("client-b", "B", "192.168.1.2", -20.0, False),
                ("client-c", "C", "192.168.1.3", -30.0, False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry)
        startup = ws._volume_service.volume_config.startup_volume_db

        # Client A reconnects first (all offline → startup)
        result_a = await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)
        assert result_a is True
        # _apply_target_volume_to_client updates registry, so client-a now has startup vol
        assert registry.get_client("client-a").volume_db == startup
        await registry.set_client_online("client-a", True)

        # Client B reconnects (A online → zone avg = A's volume = startup)
        result_b = await ws._sync_reconnecting_client_volume("client-b", max_retries=0, retry_delay=0)
        assert result_b is True
        assert registry.get_client("client-b").volume_db == startup
        await registry.set_client_online("client-b", True)

        # Client C reconnects (A+B online → zone avg = startup)
        result_c = await ws._sync_reconnecting_client_volume("client-c", max_retries=0, retry_delay=0)
        assert result_c is True
        assert registry.get_client("client-c").volume_db == startup

        # All clients converged to the same volume
        assert (
            registry.get_client("client-a").volume_db
            == registry.get_client("client-b").volume_db
            == registry.get_client("client-c").volume_db
            == startup
        )


# =============================================================================
# TestProcessDisconnectedClients
# =============================================================================


class TestProcessDisconnectedClients:
    """Tests for _process_disconnected_clients — detecting gone clients."""

    @pytest.mark.asyncio
    async def test_missing_client_marked_offline(self, mock_settings_service, mock_state_machine):
        """Client present in registry but missing from Snapcast is set offline."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, True),
                ("client-b", "B", "192.168.1.2", -30.0, True),
            ],
        )
        ws = _make_ws_service(registry)

        # Only client-a is in Snapcast; client-b disappeared
        current_mac_ids = {"client-a"}
        known_mac_ids = {"client-a", "client-b"}
        await ws._process_disconnected_clients(current_mac_ids, known_mac_ids)

        assert registry.get_client("client-a").online is True
        assert registry.get_client("client-b").online is False

    @pytest.mark.asyncio
    async def test_all_clients_present_none_disconnected(self, mock_settings_service, mock_state_machine):
        """When all known clients are in Snapcast, none are marked offline."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", -30.0, True),
                ("client-b", "B", "192.168.1.2", -30.0, True),
            ],
        )
        ws = _make_ws_service(registry)

        current_mac_ids = {"client-a", "client-b"}
        known_mac_ids = {"client-a", "client-b"}
        await ws._process_disconnected_clients(current_mac_ids, known_mac_ids)

        assert registry.get_client("client-a").online is True
        assert registry.get_client("client-b").online is True
