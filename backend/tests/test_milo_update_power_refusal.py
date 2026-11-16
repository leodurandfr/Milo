"""The six routes that reboot or power off the unit refuse while a Milō update
holds it.

What breaks when these fail: a reboot pressed during the install throws the
update away (the guard puts the previous release back at the next start), and
one pressed during the trial is counted as a failed start of the new release —
two presses and a release that worked is rolled back. The update's logind
inhibitor holds only from the journal's opening to the reboot, and refuses
with a logind error that names nothing. So the refusal is the routes', and it
has to hold in the process that did not start the update — the release on
trial, rebooted into, whose only knowledge of it is the journal on disk.

The update service is the real one, its state directory pointed into tmp_path;
the journal is written there the way the leaving release writes it. What is
asserted is what reached systemd and the disk: no power action, nothing staged
for the boot that would follow.
"""
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import setup as api_setup
from backend.api import system as api_system
from backend.api.settings import create_settings_router
from backend.api.setup import create_setup_router
from backend.api.system import create_system_router
from backend.core.updates import milo_journal as mj
from backend.core.updates.update import UpdateService
from backend.hardware.registry import AUDIO_CARDS, SCREENS

AMP_CARD = next(k for k, v in AUDIO_CARDS.items() if v["category"] == "amplifier")

# One body per route, valid enough to reach the handler: FastAPI validates it
# before the refusal can run.
REQUESTS = [
    ("post", "/api/system/restart", None),
    ("post", "/api/system/shutdown", None),
    ("post", "/api/system/reset-setup", None),
    ("put", "/api/settings/hardware-config", {
        "audio": {"id": "hifiberry_amp2"},
        "screen": {"type": "waveshare_7_usb"},
        "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
        "ir_remote": {"enabled": False, "gpio_pin": 17},
        "power_button": {"enabled": False, "led_gpio_pin": 26},
    }),
    ("post", "/api/setup/complete", {
        "language": "english",
        "audio_id": AMP_CARD,
        "volume_control": True,
        "screen_type": next(k for k in SCREENS if k != "none"),
    }),
    ("post", "/api/setup/become-client", {
        "wifi_ssid": "Maison",
        "wifi_password": "secret",
        "audio_id": AMP_CARD,
        "speaker_name": "Bureau",
        "speaker_type": "bookshelf",
    }),
]


@pytest.fixture
def unit(tmp_path, monkeypatch, mock_settings_service):
    """The four routers as main.py builds them, over one real update service."""
    state = tmp_path / "app-update"
    state.mkdir()
    with patch.dict("os.environ", {}, clear=True):
        updates = UpdateService(
            systemd_manager=Mock(), satellite_update_service=Mock(),
            settings_service=mock_settings_service,
        )
    updates.programs["milo"]["state_path"] = str(state)
    updates.programs["milo"]["backup_path"] = str(tmp_path / "backups")

    # Where each route would leave something for the next boot, if it ran.
    monkeypatch.setattr(api_system, "RESET_SETUP_MARKER", tmp_path / "reset-setup")
    monkeypatch.setattr(api_setup, "MILO_DATA_DIR", tmp_path)
    monkeypatch.setattr(api_setup, "PENDING_CLIENT_ROLE_FILE", tmp_path / "pending_client_role.json")
    helper = tmp_path / "milo-first-boot"
    helper.write_text("#!/bin/bash\n")
    monkeypatch.setattr(api_setup, "FIRST_BOOT_HELPER", helper)

    systemd = Mock()
    systemd.power = AsyncMock(return_value=True)
    hardware = Mock()
    hardware.save_config = AsyncMock()
    hardware.apply = AsyncMock()
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=False)
    settings.set_setting = AsyncMock(return_value=True)
    network = Mock()
    network.save_network = AsyncMock()

    app = FastAPI()
    app.include_router(
        create_system_router(systemd, update_service=updates), prefix="/api/system"
    )
    app.include_router(create_settings_router(
        Mock(), Mock(), Mock(), systemd, Mock(), hardware, settings, Mock(), Mock(),
        update_service=updates,
    ), prefix="/api/settings")
    app.include_router(create_setup_router(
        settings, hardware, network, systemd, update_service=updates,
    ))

    def open_journal(step):
        (state / mj.JOURNAL_NAME).write_text(json.dumps({
            mj.KEY_ORIGIN_COMMIT: "a" * 40,
            mj.KEY_ORIGIN_TAG: "v0.1.4",
            mj.KEY_TARGET_TAG: "v0.1.5",
            mj.KEY_TARGET_COMMIT: "b" * 40,
            mj.KEY_STEP: step,
            mj.KEY_FAILURE: None,
            mj.KEY_VENV_KEPT: False,
        }))

    with patch("backend.api.setup.asyncio.sleep", new=AsyncMock()):
        yield Mock(
            client=TestClient(app), systemd=systemd, hardware=hardware,
            settings=settings, network=network, tmp_path=tmp_path,
            open_journal=open_journal,
        )


# A reboot during the trial is a failed start; one during the post-commit phase
# stops a dependency or a speaker mid-install. (During the install itself the
# process running it holds the unit: test_update_service.py.)
@pytest.mark.parametrize("step", [mj.TRIAL_STEP, mj.COMMITTED_STEP])
@pytest.mark.parametrize("method,path,body", REQUESTS, ids=[r[1] for r in REQUESTS])
def test_a_power_action_is_refused_while_the_journal_is_open(unit, step, method, path, body):
    unit.open_journal(step)

    response = getattr(unit.client, method)(path, **({"json": body} if body else {}))

    assert response.status_code == 409
    assert "v0.1.4 → v0.1.5" in response.json()["detail"], "the refusal must name the update"
    unit.systemd.power.assert_not_called()
    unit.hardware.save_config.assert_not_called()
    unit.hardware.apply.assert_not_called()
    unit.settings.set_setting.assert_not_called()
    unit.network.save_network.assert_not_called()
    assert not (unit.tmp_path / "reset-setup").exists()
    assert not (unit.tmp_path / "pending_client_role.json").exists()


def test_an_install_the_guard_is_to_put_back_lets_the_restart_through(unit):
    """Read by a process that is not installing it, a journal short of the
    trial is an install that stopped: the guard puts it back at the next
    start. Refusing the restart would keep the unit on the half-installed
    tree, the very thing the restart ends."""
    unit.open_journal("checked_out")

    response = unit.client.post("/api/system/restart")

    assert response.status_code == 200
    unit.systemd.power.assert_awaited_once_with("reboot", 2.0)


@pytest.mark.parametrize("path,action", [
    ("/api/system/restart", "reboot"), ("/api/system/shutdown", "poweroff"),
])
def test_without_a_journal_the_power_action_goes_through(unit, path, action):
    """The refusal reads the journal, never a flag that outlives it: after the
    post-commit phase closed it, the unit must be able to reboot again."""
    response = unit.client.post(path)

    assert response.status_code == 200
    unit.systemd.power.assert_awaited_once_with(action, 2.0)
