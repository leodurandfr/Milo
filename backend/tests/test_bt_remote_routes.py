# backend/tests/test_bt_remote_routes.py
"""The five BT-remote API routes — the file had no test at all (41.9 %).

`settingsStore.js` drives every one of them from the Réglages panel and holds
its own optimistic copy of `enabled` / `connected` / `discovering` / `paired`,
which only these responses and the `bt_remote_status_changed` event correct. A
route that answers the wrong shape leaves that panel wrong until a reload; a
route that swallows a failure leaves it wrong *and* silent.

`DELETE /pairing` is the one with teeth: it lands on `forget_remote()`, i.e.
`bluetoothctl remove` over every bond whose name matches the filter. Its 400
arm is what tells the user the bond is still there.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from backend.hardware.bt_remote_routes import create_bt_remote_router

REMOTE_MAC = "AA:BB:CC:DD:EE:FF"
REMOTE_NAME = "ANTICATER VK-01"

STATUS = {
    "available": True,
    "enabled": True,
    "running": True,
    "discovering": False,
    "connected_devices": [
        {"path": "/dev/input/event5", "name": REMOTE_NAME, "address": REMOTE_MAC}
    ],
    "device_name_filter": "ANTICATER",
    "key_map": {"115": "volume_up", "114": "volume_down", "113": "click"},
}


@pytest.fixture
def controller():
    ctrl = MagicMock()
    ctrl.get_status = MagicMock(return_value=dict(STATUS))
    ctrl.is_paired = AsyncMock(return_value=True)
    ctrl.get_device_info = MagicMock(return_value=[
        {"path": "/dev/input/event5", "address": REMOTE_MAC, "name": REMOTE_NAME},
    ])
    ctrl.read_battery_level = AsyncMock(return_value=87)
    ctrl.trigger_discovery = AsyncMock(
        return_value={"status": "success", "message": "Device found and connected"}
    )
    ctrl.forget_remote = AsyncMock(
        return_value={"status": "success", "message": "Remote unpaired"}
    )
    ctrl.update_config = AsyncMock()
    ctrl.enabled = True
    ctrl.device_name_filter = "ANTICATER"
    ctrl.key_map = dict(STATUS["key_map"])
    return ctrl


@pytest.fixture
def client(controller):
    app = FastAPI()
    app.include_router(create_bt_remote_router(controller))
    return TestClient(app)


class TestStatus:
    def test_the_snapshot_is_returned_whole_with_the_live_pairing_state(
        self, client, controller
    ):
        """`paired` is not part of `get_status()` — it is a fresh BlueZ read.

        The store hides the "Unpair" button on it, and it is the only field
        that stays true while the remote sleeps, so serving the cached status
        alone would make the button vanish every time the remote dozes off.
        """
        response = client.get("/api/bt-remote/status")

        assert response.status_code == 200
        assert response.json() == {"status": "success", **STATUS, "paired": True}
        controller.is_paired.assert_awaited_once_with()

    def test_an_unbonded_remote_is_reported_as_such(self, client, controller):
        controller.is_paired = AsyncMock(return_value=False)
        assert client.get("/api/bt-remote/status").json()["paired"] is False


class TestBattery:
    def test_each_monitored_remote_is_read_by_its_own_address(
        self, client, controller
    ):
        controller.get_device_info = MagicMock(return_value=[
            {"path": "/dev/input/event5", "address": REMOTE_MAC, "name": REMOTE_NAME},
            {"path": "/dev/input/event7", "address": "11:22:33:44:55:66", "name": "Other"},
        ])
        controller.read_battery_level = AsyncMock(side_effect=[87, 12])

        body = client.get("/api/bt-remote/battery").json()

        assert body == {"status": "success", "devices": [
            {"address": REMOTE_MAC, "name": REMOTE_NAME, "battery_percentage": 87},
            {"address": "11:22:33:44:55:66", "name": "Other", "battery_percentage": 12},
        ]}
        assert [c.args[0] for c in controller.read_battery_level.await_args_list] == [
            REMOTE_MAC, "11:22:33:44:55:66",
        ]

    def test_a_remote_whose_battery_service_is_silent_still_appears(
        self, client, controller
    ):
        """A BLE remote with no Battery1 interface reads None. Dropping it from
        the list would make the panel report the remote as disconnected."""
        controller.read_battery_level = AsyncMock(return_value=None)

        body = client.get("/api/bt-remote/battery").json()

        assert body["devices"] == [
            {"address": REMOTE_MAC, "name": REMOTE_NAME, "battery_percentage": None},
        ]

    def test_no_remote_monitored_is_an_empty_list_not_a_failure(
        self, client, controller
    ):
        controller.get_device_info = MagicMock(return_value=[])

        body = client.get("/api/bt-remote/battery").json()

        assert body == {"status": "success", "devices": []}
        controller.read_battery_level.assert_not_awaited()


class TestDiscover:
    @pytest.mark.parametrize("outcome", [
        {"status": "success", "message": "Device found and connected"},
        {"status": "already_connected", "message": "Device already connected"},
        {"status": "not_found", "message": "No matching device found"},
        {"status": "error", "message": "BT remote is disabled"},
    ])
    def test_every_outcome_reaches_the_store_verbatim_under_a_200(
        self, client, controller, outcome
    ):
        """`discoverBtRemote()` returns `result.data.status` and the CTA reads
        it: the four outcomes are flow control, not HTTP failures. Translating
        any of them to a non-2xx leaves `discovering` stuck true, and the
        "Search" button binds both :loading and :disabled to it."""
        controller.trigger_discovery = AsyncMock(return_value=outcome)

        response = client.post("/api/bt-remote/discover")

        assert response.status_code == 200
        assert response.json() == outcome


class TestUnpair:
    def test_a_successful_unpair_answers_the_controller_verbatim(
        self, client, controller
    ):
        response = client.delete("/api/bt-remote/pairing")

        assert response.status_code == 200
        assert response.json() == {"status": "success", "message": "Remote unpaired"}
        controller.forget_remote.assert_awaited_once_with()

    def test_a_refused_unpair_is_a_400_carrying_the_reason(self, client, controller):
        """The store clears `paired` on `result.ok` alone. A refusal answered
        200 hides the "Unpair" button while the bond is still in BlueZ, so the
        remote keeps reconnecting and the button is gone."""
        controller.forget_remote = AsyncMock(
            return_value={"status": "error", "message": "evdev not available"}
        )

        response = client.delete("/api/bt-remote/pairing")

        assert response.status_code == 400
        assert response.json()["detail"] == "evdev not available"

    def test_a_refusal_with_no_message_still_says_what_failed(
        self, client, controller
    ):
        controller.forget_remote = AsyncMock(return_value={"status": "error"})
        assert client.delete("/api/bt-remote/pairing").json()["detail"] == "Unpair failed"

    def test_a_controller_that_raises_is_a_500_not_a_400(self, client, controller):
        """The 400 means "BlueZ refused"; anything else is a fault of ours and
        must not read as a refusal the user could act on."""
        controller.forget_remote = AsyncMock(side_effect=RuntimeError("bus gone"))

        response = client.delete("/api/bt-remote/pairing")

        assert response.status_code == 500
        assert response.json()["detail"] == "bus gone"


class TestConfig:
    def test_only_the_keys_the_caller_sent_reach_the_controller(
        self, client, controller
    ):
        """`exclude_unset` is load-bearing: the three fields default to None,
        and `update_config` tests `'device_name_filter' in partial`, so an
        unsent filter arriving as None would be stored as the string "None" —
        matching no device, and turning the remote off in silence."""
        client.patch("/api/bt-remote/config", json={"enabled": False})

        controller.update_config.assert_awaited_once_with({"enabled": False})

    def test_the_answer_is_read_back_from_the_controller_not_from_the_request(
        self, client, controller
    ):
        """The controller is what settings.json and the running scanner agree
        on; echoing the request back would let the panel show a filter the
        scanner never adopted."""
        async def adopt(_partial):
            controller.enabled = True
            controller.device_name_filter = "ADOPTED"
            controller.key_map = {"115": "volume_up"}

        controller.update_config = AsyncMock(side_effect=adopt)

        body = client.patch(
            "/api/bt-remote/config", json={"device_name_filter": "REQUESTED"}
        ).json()

        assert body == {"status": "success", "config": {
            "enabled": True,
            "device_name_filter": "ADOPTED",
            "key_map": {"115": "volume_up"},
        }}

    def test_a_blank_filter_never_reaches_the_controller(self, client, controller):
        """A falsy filter makes `_get_matching_devices` select EVERY bonded
        device, so `forget_remote()` would remove the A2DP phone's bond too.
        The guard lives on the request model; this pins that the route is
        behind it."""
        response = client.patch("/api/bt-remote/config", json={"device_name_filter": "  "})

        assert response.status_code == 422
        controller.update_config.assert_not_awaited()

    def test_a_controller_that_raises_is_reported_as_a_500(self, client, controller):
        controller.update_config = AsyncMock(side_effect=RuntimeError("settings locked"))

        response = client.patch("/api/bt-remote/config", json={"enabled": True})

        assert response.status_code == 500
        assert response.json()["detail"] == "settings locked"
