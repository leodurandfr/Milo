# backend/tests/test_fan_routes.py
"""The four fan API routes — the file had no test at all (51.5 %).

`fanStore.js` applies a config change optimistically and reverts only on
`!result.ok`, so the PUT's failure arm is the store's single correction
channel: a persist that fails but answers 200 leaves the fan page showing a
curve the appliance is not running, and the fan is the difference between a
silent Pi and an audible one.

`FanController` and `SettingsService` are doubles here — the controller has its
own file (`test_fan_controller.py`) and the sysfs writes must never leave it.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from backend.hardware.fan_routes import create_fan_router

CONFIG = {
    "enabled": True,
    "mode": "auto",
    "manual_percent": 50,
    "target_temp_c": 65,
    "curve": [
        {"temp_c": 55, "percent": 0},
        {"temp_c": 66, "percent": 22},
        {"temp_c": 82, "percent": 100},
    ],
}

STATUS = {
    "source": "settings",
    "available": True,
    **CONFIG,
    "temp_c": 61.3,
    "rpm": 2400,
    "pwm_percent": 22,
}


@pytest.fixture
def fan():
    controller = MagicMock()
    controller.read_status = AsyncMock(return_value=dict(STATUS))
    controller.get_status = MagicMock(return_value=dict(STATUS))
    controller.reload_config = AsyncMock()
    controller.test_speed = AsyncMock()
    return controller


@pytest.fixture
def settings():
    service = MagicMock()
    service.get_setting = AsyncMock(return_value=dict(CONFIG))
    service.set_setting = AsyncMock(return_value=True)
    return service


@pytest.fixture
def client(fan, settings):
    app = FastAPI()
    app.include_router(create_fan_router(fan, settings))
    return TestClient(app)


class TestStatus:
    def test_the_hardware_is_sampled_before_the_answer_is_built(self, client, fan):
        """`read_status` samples then returns; `get_status` is the cached copy.
        The page polls this route for live telemetry, so answering the cache
        would freeze the temperature at whatever the last loop tick wrote."""
        body = client.get("/api/fan/status").json()

        assert body == {"status": "success", **STATUS}
        fan.read_status.assert_awaited_once_with()
        fan.get_status.assert_not_called()

    def test_a_controller_that_raises_is_a_500_not_a_success_envelope(
        self, client, fan
    ):
        fan.read_status = AsyncMock(side_effect=OSError("hwmon gone"))
        assert client.get("/api/fan/status").status_code == 500


class TestConfigRead:
    def test_the_persisted_section_is_served_not_the_controller_state(
        self, client, settings, fan
    ):
        """The page edits what is stored. Serving the controller's live fields
        would show a `test_speed` preview as if it were the saved curve."""
        body = client.get("/api/fan/config").json()

        assert body == {"status": "success", "config": CONFIG}
        settings.get_setting.assert_awaited_once_with("fan")
        fan.get_status.assert_not_called()


class TestConfigWrite:
    def test_the_config_is_persisted_before_it_is_applied(
        self, client, settings, fan
    ):
        """Applying first and persisting second would leave a fan running a
        curve that is not on disk after a failed write — the state
        `docs/architecture.md` calls out for snapserver.conf, one layer down."""
        order = []
        settings.set_setting = AsyncMock(
            side_effect=lambda *_a: order.append("persist") or True
        )
        fan.reload_config = AsyncMock(side_effect=lambda *_a: order.append("apply"))

        response = client.put("/api/fan/config", json=CONFIG)

        assert response.status_code == 200
        assert order == ["persist", "apply"]

    def test_the_validated_body_reaches_both_the_store_and_the_controller(
        self, client, settings, fan
    ):
        client.put("/api/fan/config", json={**CONFIG, "mode": "manual", "manual_percent": 30})

        section = settings.set_setting.await_args.args[1]
        assert section["mode"] == "manual" and section["manual_percent"] == 30
        assert fan.reload_config.await_args.args[0] == section

    def test_the_answer_is_the_controller_state_after_the_reload(self, client, fan):
        """The store overwrites its optimistic copy with this body, so it has
        to be what the controller adopted, not what was asked of it."""
        fan.get_status = MagicMock(return_value={**STATUS, "mode": "manual"})

        body = client.put("/api/fan/config", json=CONFIG).json()

        assert body == {"status": "success", **STATUS, "mode": "manual"}

    def test_a_persist_that_fails_is_a_500_and_the_fan_is_left_alone(
        self, client, settings, fan
    ):
        """`set_setting` answers False on a locked or full filesystem. Applying
        anyway would run a curve no reboot could restore."""
        settings.set_setting = AsyncMock(return_value=False)

        response = client.put("/api/fan/config", json=CONFIG)

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to persist fan config"
        fan.reload_config.assert_not_awaited()

    def test_a_reload_that_raises_is_reported_rather_than_swallowed(
        self, client, fan
    ):
        fan.reload_config = AsyncMock(side_effect=OSError("pwm1 refused"))
        assert client.put("/api/fan/config", json=CONFIG).status_code == 500

    @pytest.mark.parametrize("bad, why", [
        ({"mode": "turbo"}, "an unknown mode would fall back to auto in silence"),
        ({"manual_percent": 140}, "a duty over 100 is clamped, not honoured"),
        ({"target_temp_c": 90}, "a setpoint past the SoC throttle is not a setpoint"),
        ({"curve": [{"temp_c": 66, "percent": 22}, {"temp_c": 55, "percent": 0}]},
         "an unsorted curve makes the interpolation read the wrong segment"),
        ({"curve": [{"temp_c": 55, "percent": 0}]},
         "a one-point curve is a constant duty, not a curve"),
    ])
    def test_a_body_the_controller_could_not_run_never_reaches_it(
        self, client, settings, fan, bad, why
    ):
        response = client.put("/api/fan/config", json={**CONFIG, **bad})

        assert response.status_code == 422, why
        settings.set_setting.assert_not_awaited()
        fan.reload_config.assert_not_awaited()


class TestSpeedPreview:
    def test_the_requested_duty_is_driven_and_echoed(self, client, fan):
        response = client.post("/api/fan/test", json={"percent": 70})

        assert response.json() == {"status": "success", "percent": 70}
        fan.test_speed.assert_awaited_once_with(70)

    @pytest.mark.parametrize("percent", [-1, 101])
    def test_a_duty_outside_the_pwm_range_is_refused_at_the_door(
        self, client, fan, percent
    ):
        assert client.post("/api/fan/test", json={"percent": percent}).status_code == 422
        fan.test_speed.assert_not_awaited()

    def test_a_controller_that_refuses_the_preview_is_a_500(self, client, fan):
        fan.test_speed = AsyncMock(side_effect=OSError("pwm1 refused"))
        assert client.post("/api/fan/test", json={"percent": 70}).status_code == 500
