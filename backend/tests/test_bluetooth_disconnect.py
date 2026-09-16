# backend/tests/test_bluetooth_disconnect.py
"""`sources/bluetooth/source.py` — dropping a sender, and the AVRCP dispatch.

Two paths ask for a sender to let go: the API (`_cmd_disconnect`, the
"Disconnect" button) and the single-device rule (`_on_device_connected`, which
kicks a second phone arriving while one is already connected). They used to be
the same `bluetoothctl disconnect <address>` written twice with different return
conventions, so a timeout fixed in one was not fixed in the other. Both now call
`BluetoothAdapter.disconnect_device`, which verifies the peer is gone — what
that verification buys is pinned in `test_bluetooth_adapter.py`; what is pinned
here is that each path asks for the *right* address and reports the answer.

Rule 5: the package-wide `MessageBus` guard is re-declared (four modules open a
bus; the B7 lesson is that covering one is not covering the package), and the
adapter is doubled, because the real one would drop this room's actual phone.

Note on scope: disconnecting drops the link, it does not `remove` the pairing.
The unpairing hazard that cost this appliance its A2DP bonds twice lives in
`hardware/bt_remote.py`, not here.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock

from backend.sources.bluetooth import (
    adapter as adapter_module,
    agent as agent_module,
    avrcp as avrcp_module,
    monitor as monitor_module,
)
from backend.sources.bluetooth.source import BluetoothSource


@pytest.fixture(autouse=True)
def never_the_real_system_bus(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the appliance's real D-Bus system bus")

    for module in (adapter_module, agent_module, avrcp_module, monitor_module):
        monkeypatch.setattr(module, "MessageBus", refuse, raising=False)


@pytest.fixture
def source():
    src = BluetoothSource({
        "bluetooth_service": "bluetooth.service",
        "bluealsa_service": "milo-bluealsa.service",
        "bluealsa_aplay_service": "milo-bluealsa-aplay.service",
    })
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    src.emit_connection_state = Mock()
    src._apply_exposure = AsyncMock(return_value=True)
    src.avrcp = MagicMock()
    src.monitor = MagicMock()
    src.adapter = MagicMock()
    src.adapter.disconnect_device = AsyncMock(return_value=True)
    return src


class TestTheDisconnectButton:
    """`_cmd_disconnect` — the only command this source takes that is not AVRCP."""

    async def test_the_connected_address_is_what_the_adapter_is_given(self, source):
        """A wrong address drops somebody else's link or nothing at all."""
        source.connected_device = {"address": "AA:BB:CC:DD:EE:FF", "name": "iPhone"}

        result = await source._cmd_disconnect()

        assert result["success"] is True
        source.adapter.disconnect_device.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")

    async def test_nothing_connected_is_a_refusal_and_nothing_is_asked(self, source):
        """Without the guard this hands the adapter a `None` address."""
        source.connected_device = None

        result = await source._cmd_disconnect()

        assert result["success"] is False
        assert "No device connected" in result["error"]
        source.adapter.disconnect_device.assert_not_awaited()

    async def test_a_peer_that_kept_the_link_is_reported_by_name(self, source):
        """This text is what reaches the UI, and the sender's name is the whole
        of its usefulness: the person is looking at a room with two paired
        Apple devices in it."""
        source.adapter.disconnect_device = AsyncMock(return_value=False)
        source.connected_device = {"address": "AA:BB", "name": "iPhone de Léo"}

        result = await source._cmd_disconnect()

        assert result["success"] is False
        assert "iPhone de Léo" in result["error"]

    async def test_the_request_is_traced_at_info_naming_the_sender(
        self, source, caplog
    ):
        """`command()` traces at debug, so without this a disconnect leaves no
        mark in the journal — which is how a button that worked and a link
        taken back seconds later by a second paired device became
        indistinguishable."""
        source.connected_device = {"address": "AA:BB", "name": "iPhone de Léo"}

        with caplog.at_level("INFO", logger="source.bluetooth"):
            await source._cmd_disconnect()

        assert "iPhone de Léo" in caplog.text
        assert "AA:BB" in caplog.text


class TestTheSingleDeviceRule:
    """What kicks a second phone off.

    Two senders on one A2DP sink is what the rule exists to prevent; the second
    is dropped rather than the first, so the person already listening keeps the
    room.
    """

    async def test_a_second_sender_is_dropped_by_address(self, source):
        source.connected_device = {"address": "AA:AA", "name": "First"}

        await source._on_device_connected("BB:BB", "Second")

        source.adapter.disconnect_device.assert_awaited_once_with("BB:BB")

    async def test_the_first_sender_keeps_the_room(self, source):
        """The card must not follow the phone that was refused."""
        source.connected_device = {"address": "AA:AA", "name": "First"}

        await source._on_device_connected("BB:BB", "Second")

        assert source.connected_device == {"address": "AA:AA", "name": "First"}

    async def test_the_same_sender_reconnecting_is_not_kicked(self, source):
        """BlueALSA re-announces a PCM on a codec change; kicking there would
        drop the phone that is playing."""
        source.connected_device = {"address": "AA:AA", "name": "First"}

        await source._on_device_connected("AA:AA", "First")

        source.adapter.disconnect_device.assert_not_awaited()
        assert source.connected_device == {"address": "AA:AA", "name": "First"}


class TestArrivalAndDeparture:
    async def test_a_first_sender_is_adopted_and_published(self, source):
        await source._on_device_connected("AA:AA", "iPhone de Léo")

        assert source.connected_device == {"address": "AA:AA", "name": "iPhone de Léo"}
        source.emit_connection_state.assert_called()

    async def test_the_appliance_hides_itself_before_it_is_published(self, source):
        """"Hide first" is the source's own comment: the appliance now has a
        sender, so it must stop offering itself to a second one rather than
        kicking it afterwards."""
        order = []
        source._apply_exposure = AsyncMock(side_effect=lambda: order.append("hide"))
        source.emit_connection_state = Mock(
            side_effect=lambda *a, **kw: order.append("publish")
        )

        await source._on_device_connected("AA:AA", "iPhone")

        assert order == ["hide", "publish"]

    async def test_a_departure_clears_the_card(self, source):
        source.connected_device = {"address": "AA:AA", "name": "iPhone"}
        source._playback = {"title": "Breathe"}

        await source._on_device_disconnected("AA:AA", "iPhone")

        assert source.connected_device is None

    async def test_a_departure_drops_the_track_with_the_link(self, source):
        """A device reconnecting before its AVRCP player is back would
        otherwise re-publish the previous track."""
        source.connected_device = {"address": "AA:AA", "name": "iPhone"}
        source._playback = {"title": "Breathe"}

        await source._on_device_disconnected("AA:AA", "iPhone")

        assert source._playback == {}

    async def test_the_appliance_offers_itself_again_after_a_departure(self, source):
        source.connected_device = {"address": "AA:AA", "name": "iPhone"}

        await source._on_device_disconnected("AA:AA", "iPhone")

        source._apply_exposure.assert_awaited()

    async def test_a_departure_of_a_device_that_is_not_ours_is_ignored(self, source):
        """BlueALSA reports PCMs for HID and headset profiles too; acting on
        one would clear the card for a phone that is still playing."""
        source.connected_device = {"address": "AA:AA", "name": "iPhone"}

        await source._on_device_disconnected("CC:CC", "Someone else")

        assert source.connected_device == {"address": "AA:AA", "name": "iPhone"}

    async def test_a_departure_with_nothing_connected_is_ignored(self, source):
        source.connected_device = None

        await source._on_device_disconnected("AA:AA", "iPhone")

        source._apply_exposure.assert_not_awaited()


class TestTheFeedDying:
    async def test_the_loss_is_logged_at_error_and_changes_no_state(self, source, caplog):
        """Deliberately inert on state: the monitor is the only thing that
        knows a sender is connected, so a source that reacted by dropping
        `connected_device` would be guessing — the audio may well still be
        flowing through bluealsa-aplay. What the message buys is naming which
        card stopped updating."""
        source.connected_device = {"address": "AA:AA", "name": "iPhone"}

        with caplog.at_level("ERROR", logger="source.bluetooth"):
            await source._on_monitor_lost("daemon went away")

        assert source.connected_device == {"address": "AA:AA", "name": "iPhone"}
        assert "will no longer be detected" in caplog.text
        assert "daemon went away" in caplog.text


class TestTheAvrcpDispatch:
    async def test_a_transport_command_reaches_the_players_own_verb(self, source):
        """Milō's vocabulary is canonical across sources; AVRCP's is its own.
        The mapping is what keeps `Previous` out of the API and `prev` out of
        the D-Bus call."""
        source.avrcp.has_player = True
        source.avrcp.send = AsyncMock(return_value=True)

        assert (await source._handle_command("prev", None))["success"] is True
        source.avrcp.send.assert_awaited_once_with("Previous")

    async def test_every_declared_command_has_an_avrcp_verb(self, source):
        """Derived from the production tables rather than restated: a command
        in COMMANDS with no entry here is a KeyError on a button press."""
        transport = set(BluetoothSource.COMMANDS) - {"disconnect"}

        assert transport == set(BluetoothSource.AVRCP_COMMANDS)

    async def test_a_sender_with_no_avrcp_player_is_refused_with_a_reason(
        self, source
    ):
        """An AVRCP target is optional and plenty of senders publish none. The
        buttons are drawn from the metadata, so pressing one on such a sender
        must say why rather than fail silently."""
        source.avrcp.has_player = False
        source.avrcp.send = AsyncMock()

        result = await source._handle_command("pause", None)

        assert result["success"] is False
        assert "no AVRCP player" in result["error"]
        source.avrcp.send.assert_not_awaited()

    async def test_a_command_the_device_refuses_is_this_commands_failure(
        self, source
    ):
        """An AVRCP target answers NotSupported per method — a sender may take
        Play and refuse Next — so a refusal is the command's failure, not the
        source's."""
        source.avrcp.has_player = True
        source.avrcp.send = AsyncMock(return_value=False)

        result = await source._handle_command("next", None)

        assert result["success"] is False
        assert "'next' was refused" in result["error"]

    async def test_disconnect_does_not_go_through_the_player(self, source):
        """It is the one command that works with no AVRCP target at all."""
        source.avrcp.has_player = False
        source.connected_device = None

        result = await source._handle_command("disconnect", None)

        assert "No device connected" in result["error"]
