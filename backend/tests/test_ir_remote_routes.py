# backend/tests/test_ir_remote_routes.py
"""
Unit tests for the IR remote API routes.

The pairing wizard (IrRemoteSettings.vue) branches on the `status` field of
`POST /api/ir-remote/pair` and on the HTTP code that carries it: `error` is a
failure (500), the four other statuses are flow control (200). A route that
blurs the two strands the wizard on its spinner or shows a fault where the
user simply pressed nothing.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from backend.hardware.ir_remote_routes import create_ir_remote_router


STATUS = {
    "available": True,
    "enabled": True,
    "paired": True,
    "device_id": 0x8D,
    "paired_at": 1700000000.0,
    "listening": True,
    "pairing_in_progress": False,
}


@pytest.fixture
def controller():
    ctrl = MagicMock()
    ctrl.get_status = MagicMock(return_value=dict(STATUS))
    ctrl.update_config = AsyncMock()
    ctrl.start_pairing = AsyncMock()
    ctrl.cancel_pairing = AsyncMock(return_value=True)
    ctrl.unpair = AsyncMock()
    return ctrl


@pytest.fixture
def client(controller):
    app = FastAPI()
    app.include_router(create_ir_remote_router(controller))
    return TestClient(app)


class TestStatusRoute:
    def test_status_is_the_controller_snapshot_under_the_success_envelope(
        self, client
    ):
        response = client.get("/api/ir-remote/status")
        assert response.status_code == 200
        assert response.json() == {"status": "success", **STATUS}


class TestConfigRoute:
    def test_an_empty_body_carries_no_enabled_key(self, client, controller):
        """`exclude_unset` is load-bearing: a `None` default reaching the
        controller reads as `'enabled' in partial` with `bool(None)` False,
        which silently switches the remote off."""
        response = client.patch("/api/ir-remote/config", json={})
        assert response.status_code == 200
        controller.update_config.assert_awaited_once_with({})

    def test_the_enabled_flag_is_forwarded_and_the_new_status_returned(
        self, client, controller
    ):
        response = client.patch("/api/ir-remote/config", json={"enabled": False})
        assert response.status_code == 200
        controller.update_config.assert_awaited_once_with({"enabled": False})
        assert response.json() == {"status": "success", **STATUS}

    def test_a_failing_controller_is_a_500(self, client, controller):
        controller.update_config.side_effect = RuntimeError("settings locked")
        response = client.patch("/api/ir-remote/config", json={"enabled": True})
        assert response.status_code == 500
        assert "settings locked" in response.json()["detail"]


class TestPairRoute:
    def test_a_captured_remote_is_returned_verbatim(self, client, controller):
        controller.start_pairing.return_value = {
            "status": "success", "device_id": 0x8D,
        }
        response = client.post("/api/ir-remote/pair")
        assert response.status_code == 200
        assert response.json() == {"status": "success", "device_id": 0x8D}

    @pytest.mark.parametrize("status", ["timeout", "cancelled", "unsupported"])
    def test_flow_control_statuses_stay_http_200(self, client, controller, status):
        """The wizard renders its own message for each of these; a 500 would
        make apiCall surface a generic failure banner instead."""
        controller.start_pairing.return_value = {"status": status, "message": "m"}
        response = client.post("/api/ir-remote/pair")
        assert response.status_code == 200
        assert response.json()["status"] == status

    def test_an_error_status_becomes_a_500_carrying_its_message(
        self, client, controller
    ):
        controller.start_pairing.return_value = {
            "status": "error",
            "message": "IR receiver not detected",
        }
        response = client.post("/api/ir-remote/pair")
        assert response.status_code == 500
        assert response.json()["detail"] == "IR receiver not detected"

    def test_a_raising_controller_is_a_500(self, client, controller):
        controller.start_pairing.side_effect = RuntimeError("evdev exploded")
        response = client.post("/api/ir-remote/pair")
        assert response.status_code == 500
        assert "evdev exploded" in response.json()["detail"]


class TestCancelRoute:
    @pytest.mark.parametrize("cancelled", [True, False])
    def test_the_cancel_verdict_is_reported_not_swallowed(
        self, client, controller, cancelled
    ):
        """False means there was nothing to cancel — the wizard uses it to
        tell an aborted capture from a stale button press."""
        controller.cancel_pairing.return_value = cancelled
        response = client.post("/api/ir-remote/pair/cancel")
        assert response.status_code == 200
        assert response.json() == {"status": "success", "cancelled": cancelled}


class TestUnpairRoute:
    def test_unpairing_answers_with_the_post_unpair_status(
        self, client, controller
    ):
        unpaired = {**STATUS, "paired": False, "device_id": None,
                    "paired_at": None, "listening": False}
        controller.get_status.side_effect = [dict(unpaired)]
        response = client.delete("/api/ir-remote/pair")
        assert response.status_code == 200
        assert response.json() == {"status": "success", **unpaired}
        controller.unpair.assert_awaited_once()

    def test_a_failing_unpair_is_a_500(self, client, controller):
        controller.unpair.side_effect = RuntimeError("keymap helper missing")
        response = client.delete("/api/ir-remote/pair")
        assert response.status_code == 500
        assert "keymap helper missing" in response.json()["detail"]
