"""
Integration tests for multiroom client synchronization.

Tests the sync mechanisms that prevent or auto-correct client desynchronization:
- Retry loop in _sync_reconnecting_client_volume (fire-and-forget)
- Concurrent reconnections via _process_online_status_changes
- Level restoration during rapid sequential reconnects
- set_online_after gate (client invisible until hardware confirms)
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests.conftest import attach_registry_broadcaster
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.models import EqualizerSettings
from backend.core.volume.state import DEFAULT_VOLUME_DB
from backend.core.models.volume import VolumeConfig


# =============================================================================
# Helpers
# =============================================================================


def _make_volume_service(startup_volume_db: float = DEFAULT_VOLUME_DB,
                        stored_volumes: dict | None = None):
    """Create a mock VolumeService wired for sync tests.

    `stored_volumes` stands for VolumeStateStore's per-client levels — what a
    reconnection now resolves to. Unset means the store knows no client, so
    every admission falls back to startup_volume_db.
    """
    stored = stored_volumes or {}

    async def _write(mac_id, volume_db):
        # The real store reads back what it was given; a write-only mock would
        # let an admission "apply" a level the next resolution never sees.
        stored[mac_id] = volume_db

    vs = MagicMock()
    vs.state_store = MagicMock()
    vs.state_store.set_client_volume = AsyncMock(side_effect=_write)
    vs.state_store.get_client_mute = MagicMock(return_value=False)
    vs.state_store.has_client = MagicMock(side_effect=lambda mac_id: mac_id in stored)
    vs.state_store.get_client_volume = MagicMock(side_effect=stored.get)
    vs.equalizer_controller = MagicMock()
    vs.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
    vs.equalizer_controller.set_equalizer_mute = AsyncMock()
    vs.equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={})
    vs.broadcast_volume_state = AsyncMock()
    vs.volume_config = VolumeConfig(startup_volume_db=startup_volume_db)
    return vs


def _make_ws_service(registry, volume_service=None, snapcast_service=None):
    """Create a SnapcastWebSocketService wired for sync tests."""
    sm = MagicMock()
    sm.broadcast = AsyncMock()

    routing = MagicMock()
    routing.get_state = MagicMock(return_value={"multiroom_enabled": True})

    ws = SnapcastWebSocketService(state_machine=sm, routing_service=routing)
    ws.set_registry(registry)
    ws._volume_service = volume_service or _make_volume_service()
    ws._snapcast_service = snapcast_service or MagicMock(
        set_volume=AsyncMock(return_value=True),
        set_latency=AsyncMock(return_value=True),
        get_clients=AsyncMock(return_value=[]),
    )
    # Stub equalizer sync to isolate volume tests
    ws._sync_zone_equalizer_to_client = AsyncMock(return_value=True)
    ws._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)
    return ws


async def _setup_registry(settings_service, state_machine, clients, zones=None):
    """Register clients and optionally create zones.

    The registry holds no level: a client's volume lives in VolumeStateStore
    alone, so a test states it through `_make_volume_service(stored_volumes=…)`.

    Args:
        clients: list of (mac_id, name, ip, online) tuples
        zones: list of (zone_id, zone_name, [mac_ids]) tuples
    """
    registry = ClientRegistryService(settings_service=settings_service)
    await registry.initialize()
    attach_registry_broadcaster(registry, state_machine)

    for mac_id, name, ip, _online in clients:
        await registry.register_client(mac_id, name, ip)

    if zones:
        for zone_id, zone_name, mac_ids in zones:
            await registry.create_zone(zone_id, zone_name, mac_ids)

    # Set online status AFTER zone creation so events fire correctly
    for mac_id, _, _, online in clients:
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
    sm.broadcast = AsyncMock()
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
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=3, retry_delay=0)

        assert result is True
        # Hardware called exactly once
        ws._volume_service.equalizer_controller.set_equalizer_volume.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_until_success(self, mock_settings_service, mock_state_machine):
        """Hardware fails twice then succeeds on third attempt."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(side_effect=[False, False, True])

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=5, retry_delay=0)

        assert result is True
        assert eq.set_equalizer_volume.call_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, mock_settings_service, mock_state_machine):
        """Hardware never responds — gives up after max_retries+1 attempts."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=2, retry_delay=0)

        assert result is False
        assert eq.set_equalizer_volume.call_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_exception_treated_as_failure_and_retried(self, mock_settings_service, mock_state_machine):
        """An exception during apply is caught and retried."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(side_effect=[ConnectionError("timeout"), True])

        result = await ws._sync_reconnecting_client_volume("client-a", max_retries=2, retry_delay=0)

        assert result is True
        assert eq.set_equalizer_volume.call_count == 2

    @pytest.mark.asyncio
    async def test_state_updated_even_on_hardware_failure(self, mock_settings_service, mock_state_machine):
        """State store and registry always receive the target volume, even when hardware fails."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        # State store was still updated (UI shows correct target)
        ws._volume_service.state_store.set_client_volume.assert_called()

    @pytest.mark.asyncio
    async def test_broadcast_only_on_success(self, mock_settings_service, mock_state_machine):
        """Volume state is broadcast only after a successful hardware apply."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        eq = ws._volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)

        await ws._sync_reconnecting_client_volume("client-a", max_retries=0, retry_delay=0)

        ws._volume_service.broadcast_volume_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_volume_service_returns_false(self, mock_settings_service, mock_state_machine):
        """Missing volume service returns False immediately without crash."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
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
            clients=[("client-a", "A", "192.168.1.1", False)],
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
            clients=[("client-a", "A", "192.168.1.1", False)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

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
            clients=[("client-a", "A", "192.168.1.1", False)],
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
            clients=[("client-a", "A", "192.168.1.1", False)],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        # Client is NOT yet online — the sync task will set it online after
        # hardware confirms (set_online_after=True prevents stale-volume window)
        assert registry.get_client("client-a").online is False

        # Sync task was spawned with set_online_after=True
        await asyncio.sleep(0)  # Let the event loop process the task
        ws._sync_reconnecting_client_volume.assert_called_once_with(
            "client-a", set_online_after=True, snapcast_id="snap-a"
        )

    @pytest.mark.asyncio
    async def test_no_change_no_sync(self, mock_settings_service, mock_state_machine):
        """A client the registry already holds online is left alone.

        Snapcast lists it on every sweep; re-syncing each time would re-push the
        volume to a speaker that already has it, every RECONCILE_INTERVAL_S.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "last_seen_age": 0}]
        await ws._process_online_status_changes(all_clients)

        ws._sync_reconnecting_client_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_clients_reconnect_simultaneously(self, mock_settings_service, mock_state_machine):
        """Multiple clients going online in the same Server.OnUpdate each get their own sync."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", False),
                ("client-b", "B", "192.168.1.2", False),
                ("client-c", "C", "192.168.1.3", False),
            ],
        )
        ws = _make_ws_service(registry)
        ws._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        all_clients = [
            {"mac_id": "client-a", "id": "snap-a", "last_seen_age": 0},
            {"mac_id": "client-b", "id": "snap-b", "last_seen_age": 0},
            {"mac_id": "client-c", "id": "snap-c", "last_seen_age": 0},
        ]
        await ws._process_online_status_changes(all_clients)
        await asyncio.sleep(0)

        # Each client gets its own sync task
        assert ws._sync_reconnecting_client_volume.call_count == 3
        synced_ids = {c.args[0] for c in ws._sync_reconnecting_client_volume.call_args_list}
        assert synced_ids == {"client-a", "client-b", "client-c"}


# =============================================================================
# TestReconnectsRestoreEachClientsOwnLevel
# =============================================================================


class TestReconnectsRestoreEachClientsOwnLevel:
    """
    What a series of reconnections does to the levels in a room.

    These used to assert the opposite: a reconnecting client took the average
    of its online peers, and the class existed to show that average stayed
    stable through rapid sequential reconnects. Since the volume-ownership
    plan's phase 1 no peer is read at all — each client comes back at the level
    it had — which is what makes the outcome independent of reconnection order
    instead of merely stable under it.
    """

    @pytest.mark.asyncio
    async def test_reconnect_order_does_not_change_any_target(
        self, mock_settings_service, mock_state_machine
    ):
        """Zone of three, all offline, reconnecting one by one at distinct levels.

        Each admission changes what the room averages, so under the old rule
        each client's target depended on who had already come back. Now none of
        them moves the others.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", False),
                ("client-b", "B", "192.168.1.2", False),
                ("client-c", "C", "192.168.1.3", False),
            ],
            zones=[("zone-1", "Living Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -20.0, "client-b": -30.0, "client-c": -40.0},
        ))

        # Client A reconnects first, alone.
        assert ws._resolve_target_volume("client-a") == -20.0
        await registry.set_client_online("client-a", True)

        # B and C follow, with the room now non-empty and its average moving.
        assert ws._resolve_target_volume("client-b") == -30.0
        await registry.set_client_online("client-b", True)

        assert ws._resolve_target_volume("client-c") == -40.0

        # And A's target is still A's, after the two others came back.
        assert ws._resolve_target_volume("client-a") == -20.0

    @pytest.mark.asyncio
    async def test_reconnect_into_zone_with_divergent_volumes(
        self, mock_settings_service, mock_state_machine
    ):
        """Zone whose online members sit far apart: the returning one ignores both."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
                ("client-c", "C", "192.168.1.3", False),  # offline, reconnecting
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -20.0, "client-b": -40.0, "client-c": -60.0},
        ))

        vol = ws._resolve_target_volume("client-c")

        assert vol == -60.0
        assert vol != (-20.0 + -40.0) / 2, "the peers' average is not the target"

    @pytest.mark.asyncio
    async def test_already_marked_online_changes_nothing(
        self, mock_settings_service, mock_state_machine
    ):
        """Whether the registry already shows the client online is not an input.

        It used to be: the client's own level had to be excluded from the average
        it was about to receive, which only the admission path knew to do.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
                ("client-c", "C", "192.168.1.3", True),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -20.0, "client-b": -40.0, "client-c": -5.0},
        ))

        assert ws._resolve_target_volume("client-c") == -5.0

        await registry.set_client_online("client-c", False)
        assert ws._resolve_target_volume("client-c") == -5.0

    @pytest.mark.asyncio
    async def test_standalone_reconnect_keeps_its_own_level(
        self, mock_settings_service, mock_state_machine
    ):
        """Off-zone the rule is the same — the global average is not a target either."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
                ("client-c", "C", "192.168.1.3", False),
            ],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -20.0, "client-b": -40.0, "client-c": -60.0},
        ))

        assert ws._resolve_target_volume("client-c") == -60.0



# =============================================================================
# TestConcurrentReconnectRaceConditions
# =============================================================================


class TestConcurrentReconnectRaceConditions:
    """
    Tests for what happens when several clients reconnect in the same
    Server.OnUpdate event. The registry is mutated (set_client_online)
    sequentially in the loop, so later clients see earlier ones as already
    online — which used to decide the level each of them was brought to.
    """

    @pytest.mark.asyncio
    async def test_both_clients_of_one_event_are_synced_and_shown_online(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Two zone clients reconnect in the same event: each gets its own sync
        task, and neither is shown online before its hardware confirms.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", False),
                ("client-b", "B", "192.168.1.2", False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -30.0, "client-b": -50.0},
        ))

        # Track the level each sync resolved to
        resolved = {}
        original_resolve = ws._resolve_target_volume

        def tracking_resolve(mac_id):
            resolved[mac_id] = original_resolve(mac_id)
            return resolved[mac_id]

        ws._resolve_target_volume = tracking_resolve

        # Capture coroutines created by asyncio.create_task
        sync_calls = []
        original_sync = ws._sync_reconnecting_client_volume

        async def capture_sync(mac_id, **kwargs):
            sync_calls.append(mac_id)
            return await original_sync(mac_id, **kwargs)

        ws._sync_reconnecting_client_volume = capture_sync

        all_clients = [
            {"mac_id": "client-a", "id": "snap-a", "last_seen_age": 0},
            {"mac_id": "client-b", "id": "snap-b", "last_seen_age": 0},
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

        # Each was brought to its own level, not to anything the other's
        # admission put in the room first.
        assert resolved == {"client-a": -30.0, "client-b": -50.0}

    @pytest.mark.asyncio
    async def test_two_standalone_clients_reconnect_keep_their_own_levels(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Two standalone clients reconnect simultaneously with a third already online.

        This is the case the old rule got worst: the second client resolved
        while the first was already marked online but had not been synced yet,
        so the first's stale level fed the average the second was given.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", True),   # already online
                ("client-b", "B", "192.168.1.2", False),  # reconnecting
                ("client-c", "C", "192.168.1.3", False),  # reconnecting
            ],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -30.0, "client-b": -50.0, "client-c": -60.0},
        ))

        # Simulate what _process_online_status_changes does sequentially:
        # 1) client-b goes online, its target is resolved
        await registry.set_client_online("client-b", True)
        vol_b = ws._resolve_target_volume("client-b")

        # 2) client-c goes online, its target is resolved (client-b now online
        #    too, and still carrying whatever level it had before)
        await registry.set_client_online("client-c", True)
        vol_c = ws._resolve_target_volume("client-c")

        assert vol_b == -50.0
        assert vol_c == -60.0


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
            clients=[("client-a", "A", "192.168.1.1", False)],
        )
        ws = _make_ws_service(registry)
        # Hardware always fails
        ws._volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        all_clients = [{"mac_id": "client-a", "id": "snap-a", "last_seen_age": 0}]
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
        ws._volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

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
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service.state_store.get_client_mute = MagicMock(return_value=True)

        result = await ws._apply_target_volume_to_client("client-a", -25.0)

        assert result is True
        # Volume applied with force=True
        ws._volume_service.equalizer_controller.set_equalizer_volume.assert_called_once_with(
            "client-a", -25.0, force=True
        )
        # Mute state restored
        ws._volume_service.equalizer_controller.set_equalizer_mute.assert_called_once_with(
            "client-a", True, force=True
        )
        # The store took the applied level — it is where the next admission reads
        ws._volume_service.state_store.set_client_volume.assert_called_once_with(
            "client-a", -25.0
        )

    @pytest.mark.asyncio
    async def test_hardware_failure_still_updates_state(self, mock_settings_service, mock_state_machine):
        """On hardware failure: state store still updated, returns False."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
        )
        ws = _make_ws_service(registry)
        ws._volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=False)

        result = await ws._apply_target_volume_to_client("client-a", -25.0)

        assert result is False
        # State store was still updated (UI correctness)
        ws._volume_service.state_store.set_client_volume.assert_called_with("client-a", -25.0)
        # Mute is still applied even on hardware volume failure — CamillaDSP
        # starts muted, so skipping unmute would leave the client silent.
        ws._volume_service.equalizer_controller.set_equalizer_mute.assert_called_once_with(
            "client-a", False, force=True
        )

    @pytest.mark.asyncio
    async def test_no_volume_service_returns_false(self, mock_settings_service, mock_state_machine):
        """Missing volume service returns False without crash."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", True)],
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
    async def test_e2e_zone_reconnect_applies_the_clients_own_level(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Client in zone reconnects. Verify the volume applied to
        hardware is the level the client itself last held, not its room's.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
                ("client-c", "C", "192.168.1.3", False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -20.0, "client-b": -40.0, "client-c": -60.0},
        ))

        # Track the volume sent to hardware
        applied_volumes = []
        original_eq = ws._volume_service.equalizer_controller

        async def capture_volume(mac_id, volume, force=False):
            applied_volumes.append((mac_id, volume))
            return True

        original_eq.set_equalizer_volume = capture_volume

        result = await ws._sync_reconnecting_client_volume("client-c", max_retries=0, retry_delay=0)

        assert result is True
        assert len(applied_volumes) == 1
        assert applied_volumes[0] == ("client-c", -60.0)  # its own, not avg of -20/-40

    @pytest.mark.asyncio
    async def test_e2e_standalone_alone_applies_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: a client the volume store has never seen gets startup volume.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("client-a", "A", "192.168.1.1", False)],
        )
        # No stored_volumes: this speaker has no level of its own yet.
        ws = _make_ws_service(registry)
        startup = ws._volume_service.volume_config.startup_volume_db

        applied_volumes = []
        original_eq = ws._volume_service.equalizer_controller

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
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -30.0, "client-b": -50.0},
        ))

        attempts = []
        eq = ws._volume_service.equalizer_controller

        async def flaky_hw(mac_id, volume, force=False):
            attempts.append(volume)
            return len(attempts) >= 2  # Fail first, succeed second

        eq.set_equalizer_volume = flaky_hw

        result = await ws._sync_reconnecting_client_volume("client-b", max_retries=2, retry_delay=0)

        assert result is True
        # Both attempts used the same target volume (client-b's own level)
        assert all(v == -50.0 for v in attempts)
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_e2e_three_zone_clients_sequential_reconnect(
        self, mock_settings_service, mock_state_machine
    ):
        """
        Full E2E: Three zone clients reconnect sequentially, each at its own level.

        The real flow: every sync writes the level back to the store, so under
        the old rule each admission moved the target of the next one. The room
        ends up as it was left, whatever the order.
        """
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[
                ("client-a", "A", "192.168.1.1", False),
                ("client-b", "B", "192.168.1.2", False),
                ("client-c", "C", "192.168.1.3", False),
            ],
            zones=[("zone-1", "Room", ["client-a", "client-b", "client-c"])],
        )
        ws = _make_ws_service(registry, volume_service=_make_volume_service(
            stored_volumes={"client-a": -10.0, "client-b": -20.0, "client-c": -30.0},
        ))
        store = ws._volume_service.state_store

        for mac_id, level in (("client-a", -10.0), ("client-b", -20.0), ("client-c", -30.0)):
            assert await ws._sync_reconnecting_client_volume(
                mac_id, max_retries=0, retry_delay=0
            ) is True
            # _apply_target_volume_to_client writes the applied level back
            assert store.get_client_volume(mac_id) == level
            await registry.set_client_online(mac_id, True)

        # Nothing an earlier admission did moved a later one, or the reverse.
        assert store.get_client_volume("client-a") == -10.0
        assert store.get_client_volume("client-b") == -20.0
        assert store.get_client_volume("client-c") == -30.0


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
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
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
                ("client-a", "A", "192.168.1.1", True),
                ("client-b", "B", "192.168.1.2", True),
            ],
        )
        ws = _make_ws_service(registry)

        current_mac_ids = {"client-a", "client-b"}
        known_mac_ids = {"client-a", "client-b"}
        await ws._process_disconnected_clients(current_mac_ids, known_mac_ids)

        assert registry.get_client("client-a").online is True
        assert registry.get_client("client-b").online is True


# =============================================================================
# TestReconnectSyncAppliesMonoAndEnabled — reconnect re-applies mono + enabled
# =============================================================================


def _make_ws_with_proxy(registry):
    """SnapcastWebSocketService with a real equalizer proxy, HTTP mocked out.

    The proxy is real so the assertions below see the actual wire calls the
    reconnect sync produces — the record push is the proxy's, not the websocket
    service's, and stubbing it would assert a fixture instead of the contract.
    """
    from backend.core.equalizer.client_proxy import EqualizerClientProxyService

    sm = MagicMock()
    sm.broadcast = AsyncMock()
    routing = MagicMock()
    routing.get_state = MagicMock(return_value={"multiroom_enabled": True})
    ws = SnapcastWebSocketService(state_machine=sm, routing_service=routing)
    ws.set_registry(registry)
    proxy = EqualizerClientProxyService()
    proxy.request = AsyncMock(return_value={"status": "success"})
    ws._equalizer_client_proxy_service = proxy
    ws._crossover_service = None
    return ws, proxy


class TestReconnectSyncAppliesMonoAndEnabled:
    """Reconnect sync must re-apply mono + master enabled/bypass, not just
    filters/compressor/loudness — otherwise a reconnecting member comes back
    in stereo with effects active (regression the EQ map flagged)."""

    @pytest.mark.asyncio
    async def test_standalone_sync_pushes_saved_mono_and_enabled(self, mock_settings_service, mock_state_machine):
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("cc:dd", "Speaker2", "192.168.1.51", True)],
        )
        ws, proxy = _make_ws_with_proxy(registry)
        # Standalone EQ now lives in the registry standalone-equalizer store (SoT).
        await registry.set_client_equalizer(
            "cc:dd", EqualizerSettings(mono=True, enabled=False, filters=[]), broadcast=False
        )

        result = await ws._sync_standalone_equalizer_to_client("cc:dd")
        assert result is True

        by_path = {c.args[2]: c.args[3] for c in proxy.request.call_args_list}
        assert by_path.get("/equalizer/mono") == {"enabled": True}
        assert by_path.get("/equalizer/enabled") == {"enabled": False}

    @pytest.mark.asyncio
    async def test_standalone_sync_noop_when_no_saved_settings(self, mock_settings_service, mock_state_machine):
        """No persisted standalone EQ → nothing is pushed (defaults stay on the satellite)."""
        registry = await _setup_registry(
            mock_settings_service, mock_state_machine,
            clients=[("ee:ff", "Speaker3", "192.168.1.52", True)],
        )
        ws, proxy = _make_ws_with_proxy(registry)
        # No set_client_equalizer → get_client_equalizer returns None.

        result = await ws._sync_standalone_equalizer_to_client("ee:ff")
        assert result is True
        assert proxy.request.call_count == 0
