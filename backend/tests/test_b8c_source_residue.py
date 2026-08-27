"""The source arms left after B3, B6 and B8a, taken by what they cost.

Three groups:

* **the BlueZ pairing agent.** Eight `org.bluez.Agent1` methods BlueZ calls
  *on Milō* while a phone is pairing. They are the whole of what makes the
  appliance headless-pairable: the PIN it answers, the passkey, and the two
  auto-accepts. None of them had ever run, and none of them is reachable from
  any other test — a wrong answer is a phone that cannot pair, diagnosable only
  by watching `bluetoothctl` on the unit.
* **the AVRCP bridge's two halves that no signal drives.** Adopting a player
  object that already existed when the listener started is the only path for a
  phone that was connected before the source started — which is every phone
  after a backend restart. The position poll is the only thing that moves the
  playhead at all: BlueZ signals a Position on a state change and never
  between, so without the poll the bar sits still through a whole track.
* **the station search's filter matching.** B3's finding was a station that was
  never listed; the manual-station filter here is the half that decides whether
  a station the user typed in themselves survives a query.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest
from dbus_next import Variant
from dbus_next.constants import MessageType

from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.avrcp import MEDIA_PLAYER_IFACE, AvrcpController
from backend.sources.radio.browser_api import RadioBrowserAPI

ADDRESS = "AA:BB:CC:DD:EE:FF"
PLAYER_PATH = f"/org/bluez/hci0/dev_{ADDRESS.replace(':', '_')}/player0"
DEVICE_PATH = f"/org/bluez/hci0/dev_{ADDRESS.replace(':', '_')}"


def _bus_handler(agent, name):
    """The callable BlueZ actually reaches, not the attribute.

    `dbus_next`'s `@method()` returns a wrapper that calls the function and
    *discards its return value*; the bus dispatches through the `_Method` it
    stashed instead. Asserting on the attribute would therefore read `None` for
    every one of these — including the two whose whole content is what they
    return.
    """
    wrapped = getattr(type(agent), name)
    return getattr(wrapped.__dict__["__DBUS_METHOD"], "fn").__get__(agent)


class TestThePairingAgent:
    """What Milō answers BlueZ while a phone is pairing. It has no screen."""

    @pytest.fixture
    def agent(self):
        return BluetoothAgent()

    def test_a_pin_request_is_answered_with_the_headless_default(self, agent):
        """There is no keypad on this box. A device that asks for a PIN and gets
        no answer stays in "pairing" until it times out, and the phone reports
        the failure with no reason."""
        assert _bus_handler(agent, "RequestPinCode")(DEVICE_PATH) == "0000"

    def test_a_passkey_request_is_answered_with_zero(self, agent):
        """Same reason, the numeric variant. BlueZ types the reply as a `u`, so
        answering nothing is a D-Bus error the sender reads as a refusal."""
        assert _bus_handler(agent, "RequestPasskey")(DEVICE_PATH) == 0

    @pytest.mark.parametrize("name,args", [
        ("Release", ()),
        ("DisplayPinCode", (DEVICE_PATH, "0000")),
        ("DisplayPasskey", (DEVICE_PATH, 123456, 0)),
        ("RequestConfirmation", (DEVICE_PATH, 123456)),
        ("RequestAuthorization", (DEVICE_PATH,)),
        ("AuthorizeService", (DEVICE_PATH, "0000110b-0000-1000-8000-00805f9b34fb")),
    ])
    def test_every_other_agent_call_accepts_rather_than_refuses(self, agent, name, args):
        """Returning without raising IS the acceptance on the `Agent1`
        interface: BlueZ reads a raised exception as a rejected pairing. These
        six are what makes the box pair with nobody touching it — and
        `AuthorizeService` is what lets the A2DP profile through once paired,
        i.e. whether the phone can play at all."""
        assert _bus_handler(agent, name)(*args) is None

    def test_the_agent_path_is_the_one_it_registers_and_exports(self, agent):
        """BlueZ calls back on the object path `RegisterAgent` announced, and the
        export uses the same one; a mismatch is an agent that is registered and
        never reached. It is per-instance because two registrations on one path
        collide."""
        assert agent.path.startswith("/org/milo/agent_")
        assert BluetoothAgent().path != agent.path


class TestTheAvrcpAdoption:
    """A phone connected before the listener started — i.e. after any restart."""

    @pytest.fixture
    def controller(self):
        ctrl = AvrcpController.__new__(AvrcpController)
        ctrl._logger = logging.getLogger("source.bluetooth.avrcp")
        ctrl._bus = Mock()
        ctrl._adopt_player = Mock()
        ctrl._mark_dirty = Mock()
        return ctrl

    @staticmethod
    def _reply(body):
        return Mock(message_type=MessageType.METHOD_RETURN, body=[body])

    async def test_an_existing_player_is_adopted_with_its_current_properties(
        self, controller
    ):
        """`InterfacesAdded` fired before this service had a listener, so the
        object-manager sweep is the only way that phone's transport is ever
        seen. Without it the source shows READY with a phone playing into it."""
        props = {"Status": Variant("s", "playing"), "Position": Variant("u", 4200)}
        controller._bus.call = AsyncMock(return_value=self._reply({
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
            PLAYER_PATH: {MEDIA_PLAYER_IFACE: props},
        }))

        await controller._adopt_existing_player()

        controller._adopt_player.assert_called_once_with(PLAYER_PATH, props)
        controller._mark_dirty.assert_called_once()

    async def test_an_object_tree_with_no_player_adopts_nothing(self, controller):
        """The ordinary case: a phone connected for audio with no AVRCP target.
        Adopting an adapter or a device object as a player would mount the
        full-screen player over a track that does not exist."""
        controller._bus.call = AsyncMock(return_value=self._reply({
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
            DEVICE_PATH: {"org.bluez.Device1": {}},
        }))

        await controller._adopt_existing_player()

        controller._adopt_player.assert_not_called()

    async def test_only_the_first_player_is_adopted(self, controller):
        """Two phones can be connected at once; the mirrored state holds one
        player, so adopting the second over the first would leave the transport
        pointing at a device the metadata does not describe."""
        controller._bus.call = AsyncMock(return_value=self._reply({
            PLAYER_PATH: {MEDIA_PLAYER_IFACE: {"Status": Variant("s", "playing")}},
            "/org/bluez/hci0/dev_11_22_33_44_55_66/player0": {
                MEDIA_PLAYER_IFACE: {"Status": Variant("s", "paused")}
            },
        }))

        await controller._adopt_existing_player()

        assert controller._adopt_player.call_count == 1

    @pytest.mark.parametrize("reply", [None, "error"], ids=["no-reply", "dbus-error"])
    async def test_an_object_manager_that_does_not_answer_is_not_a_failure(
        self, controller, reply, caplog
    ):
        """It runs inside `start()`, which is `@handle_errors` fail-open by
        design: BlueZ can be mid-restart. Raising here would take the whole
        AVRCP feed down for a sweep that is an optimisation."""
        controller._bus.call = AsyncMock(
            return_value=None if reply is None else Mock(message_type=MessageType.ERROR)
        )

        with caplog.at_level(logging.DEBUG, logger="source.bluetooth.avrcp"):
            await controller._adopt_existing_player()

        controller._adopt_player.assert_not_called()
        assert any("no pre-existing player adopted" in r.message for r in caplog.records)


class TestTheAvrcpPositionPoll:
    """The only thing that moves the playhead. BlueZ reports nothing between
    state changes — it subscribes to position-changed at the maximum interval
    on purpose — so a bar that advances at all advances from here."""

    @pytest.fixture
    def controller(self):
        ctrl = AvrcpController.__new__(AvrcpController)
        ctrl._logger = logging.getLogger("source.bluetooth.avrcp")
        ctrl._stopped = False
        ctrl._player_path = PLAYER_PATH
        ctrl._status = "playing"
        ctrl.read_position = AsyncMock()
        ctrl._mark_dirty = Mock()
        return ctrl

    def _bounded_sleep(self, controller, stop_after):
        """Grant every wait immediately, and end the loop after N passes.

        Bounded on every path the mutation can open: the loop's condition is
        `while not self._stopped`, so a mutation that drops it would otherwise
        spin at full CPU on this machine — which is itself what desynchronises
        snapcast.
        """
        passes = {"n": 0}
        real_sleep = asyncio.sleep

        async def _sleep(delay, *a, **k):
            passes["n"] += 1
            if passes["n"] > stop_after + 3:
                raise KeyboardInterrupt("the poll loop ignored its stop flag")
            if passes["n"] >= stop_after:
                controller._stopped = True
            await real_sleep(0)

        return passes, _sleep

    async def test_it_reads_the_position_on_every_pass_while_playing(self, controller):
        passes, sleep = self._bounded_sleep(controller, stop_after=3)

        with patch.object(asyncio, "sleep", sleep):
            await controller._poll_loop()

        assert controller.read_position.await_count == 3
        assert controller._mark_dirty.call_count == 3

    async def test_a_paused_player_is_not_polled(self, controller):
        """A Get taken while paused answers from BlueZ's last anchor, which is
        stale — reading it would move a bar the user can see is stopped."""
        controller._status = "paused"
        passes, sleep = self._bounded_sleep(controller, stop_after=3)

        with patch.object(asyncio, "sleep", sleep):
            await controller._poll_loop()

        controller.read_position.assert_not_called()

    async def test_no_player_at_all_is_not_polled(self, controller):
        """A phone connected for audio with no AVRCP target."""
        controller._player_path = None
        passes, sleep = self._bounded_sleep(controller, stop_after=2)

        with patch.object(asyncio, "sleep", sleep):
            await controller._poll_loop()

        controller.read_position.assert_not_called()

    async def test_a_read_that_fails_does_not_end_the_poll(self, controller, caplog):
        """The loop body is wrapped for exactly this: a phone that drops mid-poll
        raises on the Get, and a loop that died there would leave the playhead
        frozen for the rest of the session with no way back."""
        controller.read_position = AsyncMock(side_effect=RuntimeError("device gone"))
        passes, sleep = self._bounded_sleep(controller, stop_after=3)

        with patch.object(asyncio, "sleep", sleep):
            with caplog.at_level(logging.ERROR, logger="source.bluetooth.avrcp"):
                await controller._poll_loop()

        assert controller.read_position.await_count == 3
        assert sum("position poll failed" in r.message for r in caplog.records) == 3

    async def test_cancellation_ends_the_loop_rather_than_being_logged(
        self, controller, caplog
    ):
        """The teardown cancels this task; treating it as a poll failure would
        put a fault line in the operator log on every Bluetooth stop."""
        controller.read_position = AsyncMock(side_effect=asyncio.CancelledError)
        passes = {"n": 0}

        async def _sleep(delay, *a, **k):
            # Bounded on the path the mutation opens: widening the catch to
            # BaseException swallows the cancellation, and an unbounded double
            # here would spin the loop at full CPU instead of failing.
            passes["n"] += 1
            if passes["n"] > 3:
                raise KeyboardInterrupt("cancellation was swallowed by the loop")
            return None

        with patch.object(asyncio, "sleep", _sleep):
            with caplog.at_level(logging.ERROR, logger="source.bluetooth.avrcp"):
                with pytest.raises(asyncio.CancelledError):
                    await controller._poll_loop()

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestTheStationSearchFilters:
    """A station the user typed in themselves, and whether a query finds it."""

    @pytest.fixture
    def api(self):
        browser = RadioBrowserAPI.__new__(RadioBrowserAPI)
        browser.logger = logging.getLogger("source.radio.browser_api")
        browser.station_manager = Mock()
        browser.station_manager.get_manual_stations = Mock(return_value={})
        browser._fetch_top_stations = AsyncMock(return_value=[])
        browser._fetch_with_search_params = AsyncMock(return_value=[])
        browser._build_search_params = Mock(return_value={})
        return browser

    @staticmethod
    def _manual(name="Radio Maison", country="France", genre="jazz"):
        return {"custom_1": {"name": name, "country": country, "genre": genre,
                             "url": "http://example/stream"}}

    async def test_a_manual_station_matches_on_its_name(self, api):
        """B3's finding was a station that was never listed. These four filters
        are what decides whether one the user created themselves survives a
        query, and nothing upstream can compensate: the radio-browser API has
        never heard of it."""
        api.station_manager.get_manual_stations = Mock(return_value=self._manual())

        result = await api.search_stations(query="maison", limit=10)

        assert [s["name"] for s in result["stations"]] == ["Radio Maison"]

    async def test_a_manual_station_matches_on_its_genre_too(self, api):
        """The query box is one field for both; searching a genre and getting
        only the API's answers hides every station the user tagged that way."""
        api.station_manager.get_manual_stations = Mock(return_value=self._manual())

        result = await api.search_stations(query="jazz", limit=10)

        assert len(result["stations"]) == 1

    async def test_a_query_that_matches_neither_drops_it(self, api):
        api.station_manager.get_manual_stations = Mock(return_value=self._manual())

        result = await api.search_stations(query="classique", limit=10)

        assert result["stations"] == []

    @pytest.mark.parametrize("kwargs,expected", [
        ({"country": "france"}, 1),
        ({"country": "belgique"}, 0),
        ({"genre": "jazz"}, 1),
        ({"genre": "metal"}, 0),
        ({"query": "maison", "country": "belgique"}, 0),
    ], ids=["country-hit", "country-miss", "genre-hit", "genre-miss", "both-required"])
    async def test_the_dropdown_filters_apply_to_manual_stations_as_well(
        self, api, kwargs, expected
    ):
        """They are `and`-ed, like the API's own: a country and a genre that
        contradict must return nothing rather than the union."""
        api.station_manager.get_manual_stations = Mock(return_value=self._manual())

        result = await api.search_stations(limit=10, **kwargs)

        assert len(result["stations"]) == expected

    @pytest.mark.parametrize("kwargs", [
        {"query": "jazz"}, {"country": "france"}, {"genre": "jazz"},
    ], ids=["query", "country", "genre"])
    async def test_any_filter_at_all_goes_through_the_search_endpoint(self, api, kwargs):
        """The top-500 shortcut is a global popularity ranking that ignores every
        filter. Taking it for a country- or genre-only search answers the same
        five hundred stations whatever the dropdown says — and the browse screen
        has no way to tell that from a filter with no matches."""
        await api.search_stations(limit=10, **kwargs)

        api._fetch_with_search_params.assert_awaited_once()
        api._fetch_top_stations.assert_not_called()

    async def test_a_search_with_no_filters_asks_for_the_top_stations(self, api):
        """The browse screen opens on this. Building search params from three
        empty strings asks radio-browser for everything it has."""
        await api.search_stations(limit=10)

        api._fetch_top_stations.assert_awaited_once()
        api._fetch_with_search_params.assert_not_called()

    async def test_an_unreachable_directory_is_reported_as_such_not_as_empty(
        self, api, caplog
    ):
        """`api_error` is what lets the browse screen say "no network" instead of
        "no station found" — the two look identical from an empty list, and only
        one of them is worth retrying."""
        from backend.shared.network import NetworkUnavailableError

        api._fetch_top_stations = AsyncMock(side_effect=NetworkUnavailableError("no dns"))

        with caplog.at_level(logging.INFO, logger="source.radio.browser_api"):
            result = await api.search_stations(limit=10)

        assert result == {"stations": [], "total": 0, "api_error": True}
        assert any("Network unavailable" in r.message for r in caplog.records)
