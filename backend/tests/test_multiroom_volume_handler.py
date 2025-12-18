# backend/tests/test_multiroom_volume_handler.py
"""
Unit tests for MultiroomVolumeHandler - Tests for multiroom volume management
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.services.multiroom_volume_handler import MultiroomVolumeHandler


class TestMultiroomVolumeHandler:
    """Tests for the multiroom volume handler"""

    @pytest.fixture
    def mock_converter(self):
        """Mock of the volume converter"""
        converter = Mock()
        converter.clamp_db = Mock(side_effect=lambda x: max(-80.0, min(-21.0, x)))
        return converter

    @pytest.fixture
    def mock_snapcast_service(self):
        """Mock of snapcast service"""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """Mock of the state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        return sm

    @pytest.fixture
    def mock_dsp_service(self):
        """Mock of the DSP service"""
        dsp = Mock()
        dsp.get_volume = AsyncMock(return_value={"main": -30.0})
        dsp.set_volume = AsyncMock(return_value=True)
        return dsp

    @pytest.fixture
    def handler(self, mock_converter, mock_snapcast_service, mock_state_machine, mock_dsp_service):
        """Fixture to create a MultiroomVolumeHandler"""
        return MultiroomVolumeHandler(
            mock_converter,
            mock_snapcast_service,
            mock_state_machine,
            mock_dsp_service
        )

    # === Initialization Tests ===

    def test_initialization(self, handler):
        """Handler initialization test"""
        assert handler._global_volume_db == -30.0
        assert handler._client_volume_db == {}
        assert handler._client_offset_db == {}
        assert handler._client_mute == {}
        assert handler._snapcast_clients_cache == []

    # === get_average_volume_db Tests ===

    def test_get_average_volume_db_no_clients(self, handler):
        """Returns global_volume_db when no clients"""
        handler._global_volume_db = -40.0
        handler._client_volume_db = {}

        result = handler.get_average_volume_db()

        assert result == -40.0

    def test_get_average_volume_db_single_client(self, handler):
        """Returns correct average for single client"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {"local": -35.0}

        result = handler.get_average_volume_db()

        assert result == -35.0

    def test_get_average_volume_db_multiple_clients(self, handler):
        """Returns correct average for multiple clients"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {
            "local": -40.0,
            "192.168.1.100": -40.0,
            "192.168.1.101": -40.0
        }

        result = handler.get_average_volume_db()

        assert result == -40.0

    def test_get_average_volume_db_different_volumes(self, handler):
        """Returns correct average for clients with different volumes"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {
            "local": -30.0,
            "192.168.1.100": -40.0,
            "192.168.1.101": -50.0
        }

        result = handler.get_average_volume_db()

        assert result == -40.0  # (-30 + -40 + -50) / 3 = -40

    def test_get_average_volume_db_excludes_muted_clients(self, handler):
        """Excludes muted clients from average calculation"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {
            "local": -40.0,
            "192.168.1.100": -40.0,
            "192.168.1.101": -30.0  # This one is muted
        }
        handler._client_mute = {
            "local": False,
            "192.168.1.100": False,
            "192.168.1.101": True  # Muted
        }

        result = handler.get_average_volume_db()

        # Only non-muted clients: (-40 + -40) / 2 = -40
        assert result == -40.0

    def test_get_average_volume_db_all_clients_muted(self, handler):
        """Returns global_volume_db when all clients are muted"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {
            "local": -40.0,
            "192.168.1.100": -50.0
        }
        handler._client_mute = {
            "local": True,
            "192.168.1.100": True
        }

        result = handler.get_average_volume_db()

        assert result == -30.0  # Falls back to global_volume_db

    def test_get_average_volume_db_partial_mute_cache(self, handler):
        """Handles clients not in mute cache (assumes not muted)"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {
            "local": -40.0,
            "192.168.1.100": -40.0,
            "192.168.1.101": -40.0
        }
        # Only one client has mute state cached
        handler._client_mute = {
            "192.168.1.100": True  # Only this one is muted
        }

        result = handler.get_average_volume_db()

        # local and 192.168.1.101 are not muted (not in cache = False)
        # (-40 + -40) / 2 = -40
        assert result == -40.0

    # === set_client_mute Tests ===

    def test_set_client_mute_new_client(self, handler):
        """Sets mute state for new client"""
        handler.set_client_mute("local", True)

        assert handler._client_mute["local"] is True

    def test_set_client_mute_update_existing(self, handler):
        """Updates mute state for existing client"""
        handler._client_mute["local"] = False

        handler.set_client_mute("local", True)

        assert handler._client_mute["local"] is True

    def test_set_client_mute_multiple_clients(self, handler):
        """Sets mute state for multiple clients independently"""
        handler.set_client_mute("local", True)
        handler.set_client_mute("192.168.1.100", False)
        handler.set_client_mute("192.168.1.101", True)

        assert handler._client_mute["local"] is True
        assert handler._client_mute["192.168.1.100"] is False
        assert handler._client_mute["192.168.1.101"] is True

    # === invalidate_caches Tests ===

    def test_invalidate_caches_clears_all(self, handler):
        """Clears all caches including mute cache"""
        handler._client_volume_db = {"local": -40.0}
        handler._client_offset_db = {"local": -10.0}
        handler._client_mute = {"local": True}
        handler._snapcast_clients_cache = [{"id": "test"}]
        handler._snapcast_cache_time = 12345

        handler.invalidate_caches()

        assert handler._client_volume_db == {}
        assert handler._client_offset_db == {}
        assert handler._client_mute == {}
        assert handler._snapcast_clients_cache == []
        assert handler._snapcast_cache_time == 0

    # === get_client_volume_db Tests ===

    def test_get_client_volume_db_existing(self, handler):
        """Returns cached volume for existing client"""
        handler._client_volume_db = {"local": -45.0}

        result = handler.get_client_volume_db("local")

        assert result == -45.0

    def test_get_client_volume_db_missing(self, handler):
        """Returns global_volume_db for missing client"""
        handler._global_volume_db = -30.0
        handler._client_volume_db = {}

        result = handler.get_client_volume_db("unknown")

        assert result == -30.0

    # === get_client_offset_db Tests ===

    def test_get_client_offset_db_existing(self, handler):
        """Returns cached offset for existing client"""
        handler._client_offset_db = {"local": -5.0}

        result = handler.get_client_offset_db("local")

        assert result == -5.0

    def test_get_client_offset_db_missing(self, handler):
        """Returns 0.0 for missing client"""
        handler._client_offset_db = {}

        result = handler.get_client_offset_db("unknown")

        assert result == 0.0

    # === Global Volume Tests ===

    def test_get_global_volume_db(self, handler):
        """Returns current global volume"""
        handler._global_volume_db = -35.0

        result = handler.get_global_volume_db()

        assert result == -35.0

    def test_set_global_volume_db(self, handler):
        """Sets global volume"""
        handler.set_global_volume_db(-45.0)

        assert handler._global_volume_db == -45.0

    # === Hostname Extraction Tests ===

    def test_get_hostname_from_client_local_milo(self, handler):
        """Returns 'local' for main Milo (host='milo')"""
        client = {"host": "milo", "ip": "192.168.1.50"}

        result = handler._get_hostname_from_client(client)

        assert result == "local"

    def test_get_hostname_from_client_local_loopback(self, handler):
        """Returns 'local' for loopback IP"""
        client = {"host": "something", "ip": "127.0.0.1"}

        result = handler._get_hostname_from_client(client)

        assert result == "local"

    def test_get_hostname_from_client_remote_ip(self, handler):
        """Returns IP for remote client"""
        client = {"host": "milo-client-01", "ip": "192.168.1.100"}

        result = handler._get_hostname_from_client(client)

        assert result == "192.168.1.100"

    def test_get_hostname_from_client_no_ip(self, handler):
        """Returns host when no IP available"""
        client = {"host": "milo-client-01", "ip": ""}

        result = handler._get_hostname_from_client(client)

        assert result == "milo-client-01"

    def test_extract_hostnames(self, handler):
        """Extracts hostnames from multiple clients"""
        clients = [
            {"host": "milo", "ip": "127.0.0.1"},
            {"host": "milo-client-01", "ip": "192.168.1.100"},
            {"host": "milo-client-02", "ip": "192.168.1.101"}
        ]

        result = handler._extract_hostnames(clients)

        assert result == ["local", "192.168.1.100", "192.168.1.101"]


class TestMultiroomVolumeHandlerAsync:
    """Async tests for MultiroomVolumeHandler"""

    @pytest.fixture
    def mock_converter(self):
        """Mock of the volume converter"""
        converter = Mock()
        converter.clamp_db = Mock(side_effect=lambda x: max(-80.0, min(-21.0, x)))
        return converter

    @pytest.fixture
    def mock_snapcast_service(self):
        """Mock of snapcast service"""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[
            {"id": "client1", "host": "milo", "ip": "127.0.0.1", "muted": False},
            {"id": "client2", "host": "milo-client-01", "ip": "192.168.1.100", "muted": False},
            {"id": "client3", "host": "milo-client-02", "ip": "192.168.1.101", "muted": True}
        ])
        service.set_volume = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """Mock of the state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        return sm

    @pytest.fixture
    def mock_dsp_service(self):
        """Mock of the DSP service"""
        dsp = Mock()
        dsp.get_volume = AsyncMock(return_value={"main": -40.0})
        dsp.set_volume = AsyncMock(return_value=True)
        return dsp

    @pytest.fixture
    def handler(self, mock_converter, mock_snapcast_service, mock_state_machine, mock_dsp_service):
        """Fixture to create a MultiroomVolumeHandler"""
        return MultiroomVolumeHandler(
            mock_converter,
            mock_snapcast_service,
            mock_state_machine,
            mock_dsp_service
        )

    @pytest.mark.asyncio
    async def test_update_client_volume_db(self, handler):
        """Updates client volume and recalculates offset"""
        handler._global_volume_db = -30.0

        await handler.update_client_volume_db("local", -40.0)

        assert handler._client_volume_db["local"] == -40.0
        assert handler._client_offset_db["local"] == -10.0  # -40 - (-30) = -10

    @pytest.mark.asyncio
    async def test_sync_all_clients_cleans_stale_entries(self, handler, mock_snapcast_service):
        """sync_all_clients_from_dsp cleans stale entries"""
        # Pre-populate with a stale client
        handler._client_volume_db = {
            "local": -30.0,
            "stale_client": -25.0  # This should be removed
        }
        handler._client_offset_db = {
            "local": 0.0,
            "stale_client": 5.0
        }
        handler._client_mute = {
            "local": False,
            "stale_client": False
        }

        # Mock only returns 3 current clients, not "stale_client"
        with patch.object(handler, '_fetch_client_dsp_volume', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = -40.0

            await handler.sync_all_clients_from_dsp()

        # Stale client should be removed
        assert "stale_client" not in handler._client_volume_db
        assert "stale_client" not in handler._client_offset_db
        assert "stale_client" not in handler._client_mute

    @pytest.mark.asyncio
    async def test_sync_all_clients_syncs_mute_state(self, handler, mock_snapcast_service):
        """sync_all_clients_from_dsp syncs mute state from Snapcast"""
        with patch.object(handler, '_fetch_client_dsp_volume', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = -40.0

            await handler.sync_all_clients_from_dsp()

        # Mute state should be synced from Snapcast clients
        assert handler._client_mute.get("local") is False
        assert handler._client_mute.get("192.168.1.100") is False
        assert handler._client_mute.get("192.168.1.101") is True

    @pytest.mark.asyncio
    async def test_push_volume_cleans_stale_entries(self, handler, mock_snapcast_service):
        """push_volume_to_all_clients cleans stale entries"""
        # Pre-populate with a stale client
        handler._client_volume_db = {"stale_client": -25.0}
        handler._client_offset_db = {"stale_client": 5.0}
        handler._client_mute = {"stale_client": False}

        with patch.object(handler, '_set_client_dsp_volume', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            await handler.push_volume_to_all_clients(-40.0)

        # Stale client should be removed
        assert "stale_client" not in handler._client_volume_db
        assert "stale_client" not in handler._client_offset_db
        assert "stale_client" not in handler._client_mute

    @pytest.mark.asyncio
    async def test_push_volume_syncs_mute_state(self, handler, mock_snapcast_service):
        """push_volume_to_all_clients syncs mute state"""
        with patch.object(handler, '_set_client_dsp_volume', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = True

            await handler.push_volume_to_all_clients(-40.0)

        # Mute state should be synced
        assert handler._client_mute.get("local") is False
        assert handler._client_mute.get("192.168.1.100") is False
        assert handler._client_mute.get("192.168.1.101") is True
