# backend/tests/test_snapcast_notifications.py
"""The snapserver frame → handler table, which nothing else confronts.

What breaks when these fail: `SnapcastWebSocketService` learns that a satellite
arrived, left, or was renamed from one place only — a JSON-RPC notification read
off the snapserver control socket and routed by name through a hand-written
dispatch table in `_handle_notification`. Measured 2026-08-25, the whole
reception path ran at 0 %: every other test in the suite calls
`_handle_client_connect` / `_handle_client_disconnect` **directly**, so which
snapserver method name reaches which handler was verified by nothing. A typo in
`"Client.OnConnect"` falls through to the `Unhandled notification` arm, which is
a `logger.debug` — no red, no visible log (the operator journal carries the
level in the text, not the priority), and a speaker that never appears.

Consumers: every multiroom screen in `frontend/src/components/multiroom/`, fed
by the `multiroom` WS events the registry emits once these handlers run.

`Client.OnNameChanged` is reachable in production even though Milō never calls
`Client.SetName` itself: snapserver's own web UI is served on `0.0.0.0:1780`
(`http.enabled = true` in `/etc/snapserver.conf`), so anyone on the LAN can
rename a client there and that is what emits the notification.
"""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.tests.conftest import drain_background_tasks
from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.websocket import SnapcastWebSocketService

MAC = "aa:bb:cc:dd:ee:01"
IP = "192.168.1.150"
HOST = "milo-client-kitchen"


def _frame(method: str, params: dict) -> dict:
    """A snapserver notification: a method, parameters, and no id.

    The absence of `id` is what makes it a notification rather than a reply —
    `_handle_message` discriminates on exactly that.
    """
    return {"jsonrpc": "2.0", "method": method, "params": params}


def _client_params(*, name: str = "Kitchen", ip: str = IP, client_id: str = MAC) -> dict:
    return {"client": {
        "id": client_id,
        "connected": True,
        "config": {"name": name, "volume": {"percent": 100, "muted": False}},
        "host": {"name": HOST, "ip": f"::ffff:{ip}", "mac": client_id},
    }}


@pytest.fixture
async def registry():
    settings = AsyncMock()
    settings.get_setting = AsyncMock(return_value=None)
    reg = ClientRegistryService(settings_service=settings)
    await reg.initialize()
    return reg


@pytest.fixture
def snapcast():
    """Stand-in for snapserver: the only outside world this service has."""
    service = MagicMock()
    service.set_volume = AsyncMock(return_value=True)
    service.set_latency = AsyncMock(return_value=True)
    service.get_clients = AsyncMock(return_value=[])
    service.get_server_status = AsyncMock(return_value={"server": {"groups": []}})
    return service


@pytest.fixture
def volume_service():
    """Stand-in for VolumeService, wired so an admission sync succeeds."""
    service = MagicMock()
    service.state_store.set_client_volume = AsyncMock()
    service.state_store.get_client_volume = MagicMock(return_value=None)
    service.state_store.get_client_mute = MagicMock(return_value=False)
    service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
    service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
    service.broadcast_volume_state = AsyncMock()
    service.volume_config.restore_last_volume = False
    service.volume_config.startup_volume_db = DEFAULT_VOLUME_DB
    return service


@pytest.fixture
def ws_service(registry, snapcast, volume_service, no_satellite_network):
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    service = SnapcastWebSocketService(
        state_machine=state_machine,
        routing_service=MagicMock(),
        snapcast_service=snapcast,
    )
    service.set_registry(registry)
    service._volume_service = volume_service
    return service


class TestNotificationDispatch:
    """One frame in, one registry effect out — for each name in the table."""

    async def test_a_connect_frame_admits_the_client(self, ws_service, registry):
        """`Client.OnConnect` is how every satellite booted after the backend arrives."""
        assert registry.get_client(MAC) is None, "the registry must start empty here"

        await ws_service._handle_message(_frame("Client.OnConnect", _client_params()))
        await drain_background_tasks()

        client = registry.get_client(MAC)
        assert client is not None, "the frame never reached _handle_client_connect"
        assert client.name == "Kitchen"
        assert client.ip == IP

    async def test_a_disconnect_frame_takes_the_client_offline(self, ws_service, registry):
        """Without it a speaker that is gone keeps offering controls in the UI."""
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)
        await registry.set_client_online(MAC, True)

        await ws_service._handle_message(_frame("Client.OnDisconnect", _client_params()))
        await drain_background_tasks()

        assert registry.get_client(MAC).online is False

    async def test_a_name_changed_frame_renames_the_client(self, ws_service, registry):
        """A rename done in snapserver's own web UI must land in the registry.

        It is the only handler of the four with no other caller in the suite, so
        this frame is the only thing that runs it at all.
        """
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)

        await ws_service._handle_message(
            _frame("Client.OnNameChanged", {"id": MAC, "name": "Cuisine"})
        )
        await drain_background_tasks()

        assert registry.get_client(MAC).name == "Cuisine"

    async def test_a_name_changed_frame_carrying_no_name_keeps_the_current_one(
        self, ws_service, registry
    ):
        """Clearing the name in snapweb must not blank the one the user chose.

        `register_client` preserves an existing non-empty name and cannot repair
        an emptied one, so writing "" here would be permanent.
        """
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)

        await ws_service._handle_message(
            _frame("Client.OnNameChanged", {"id": MAC, "name": ""})
        )
        await drain_background_tasks()

        assert registry.get_client(MAC).name == "Kitchen"

    async def test_a_server_update_frame_takes_a_vanished_client_offline(
        self, ws_service, registry, snapcast
    ):
        """`Server.OnUpdate` is the sweep path: absence from the list is departure."""
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)
        await registry.set_client_online(MAC, True)
        snapcast.get_clients = AsyncMock(return_value=[])

        await ws_service._handle_message(_frame("Server.OnUpdate", {}))
        await drain_background_tasks()

        snapcast.get_clients.assert_awaited_once()
        assert registry.get_client(MAC).online is False

    async def test_a_volume_frame_is_left_to_the_volume_path(self, ws_service, registry, caplog):
        """Volume and mute travel through VolumeService, not through here.

        They are excluded by name from the `Unhandled notification` arm, so
        moving them into the table (or out of the exclusion) would have this
        service fight the volume path for the same state.
        """
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)
        await registry.set_client_online(MAC, True)

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            await ws_service._handle_message(_frame(
                "Client.OnVolumeChanged",
                {"id": MAC, "volume": {"percent": 12, "muted": True}},
            ))
        await drain_background_tasks()

        assert registry.get_client(MAC).online is True
        assert not [r for r in caplog.records if "Unhandled notification" in r.getMessage()]

    async def test_an_unknown_notification_is_reported_and_changes_nothing(
        self, ws_service, registry, caplog
    ):
        """The catch-all arm — the one a mistyped table entry silently falls into."""
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            await ws_service._handle_message(_frame("Stream.OnUpdate", {"id": "Multiroom"}))
        await drain_background_tasks()

        assert [r for r in caplog.records if "Unhandled notification" in r.getMessage()]
        assert registry.get_client(MAC).online is False

    async def test_a_reply_is_not_dispatched_as_a_notification(self, ws_service, registry):
        """A frame carrying an `id` is snapserver answering us, not telling us.

        `_handle_message` splits the two on the presence of `id` alone. Dropping
        that clause would replay our own request methods through the handler
        table.
        """
        await registry.register_client(MAC, "Kitchen", IP, host=HOST)
        await registry.set_client_online(MAC, True)

        await ws_service._handle_message(
            {"jsonrpc": "2.0", "id": 7, "method": "Client.OnDisconnect",
             "params": _client_params()}
        )
        await drain_background_tasks()

        assert registry.get_client(MAC).online is True

    async def test_an_rpc_error_reply_reaches_the_operator(self, ws_service, caplog):
        """A snapserver error is the one thing a reply carries that anyone reads.

        It is logged at ERROR, which is what `WebSocketLogHandler` turns into the
        UI's error banner — the only channel that reports a refused snapserver
        command at all.
        """
        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await ws_service._handle_message(
                {"jsonrpc": "2.0", "id": 4, "error": {"code": -32601, "message": "nope"}}
            )

        assert [r for r in caplog.records if "Snapcast RPC error" in r.getMessage()]

    async def test_a_frame_milo_cannot_key_does_not_drop_the_snapserver_link(
        self, ws_service, registry
    ):
        """One malformed frame must not cost the connection to snapserver.

        A remote client announcing no id makes `compute_mac_id` raise, and the
        handler does not catch it. `_handle_message` is the only thing between
        that and `_connect_and_listen`'s `async for`, whose broad except tears
        the socket down and reconnects five seconds later — so the whole fleet
        would drop out on a single bad frame.
        """
        await ws_service._handle_message(
            _frame("Client.OnConnect", _client_params(client_id=""))
        )
        await drain_background_tasks()

        assert registry.get_client("") is None

        # The service is still usable: the next good frame is admitted.
        await ws_service._handle_message(_frame("Client.OnConnect", _client_params()))
        await drain_background_tasks()

        assert registry.get_client(MAC) is not None


class TestOutboundRequests:
    """The only frame this service sends to snapserver."""

    async def test_a_request_is_sent_as_json_rpc_with_a_fresh_id(self, ws_service):
        """The id is what lets snapserver's reply be matched to a request.

        Reusing one would have two replies collapse onto the same request, which
        is why it increments per send rather than per connection.
        """
        websocket = MagicMock()
        websocket.send_str = AsyncMock()
        ws_service.websocket = websocket

        await ws_service._send_request("Server.GetRPCVersion")
        await ws_service._send_request("Client.SetVolume", {"id": MAC})

        first, second = (json.loads(c.args[0]) for c in websocket.send_str.call_args_list)
        assert first == {"jsonrpc": "2.0", "method": "Server.GetRPCVersion", "id": 1}
        assert second == {"jsonrpc": "2.0", "method": "Client.SetVolume",
                          "id": 2, "params": {"id": MAC}}

    async def test_nothing_is_sent_without_a_socket(self, ws_service):
        """`_connect_and_listen` pings before the socket is proven; with the
        socket gone the send must be skipped, not raise into the connect path."""
        ws_service.websocket = None

        await ws_service._send_request("Server.GetRPCVersion")

        assert ws_service.request_id == 0
