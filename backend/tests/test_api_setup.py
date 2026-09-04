# backend/tests/test_api_setup.py
"""
The two first-boot routes of /api/setup — the wizard and the wifi adoption.

Why this file exists: measured 2026-08-25, `backend/api/setup.py` ran at 24.6 %
of its lines under the whole suite. `complete_setup` (40 lines) and
`become_client` (43) had never been entered at all — the two routes that decide
whether a fresh unit comes back as a server or as a satellite, both of which
end in a reboot.

What breaks when one of these fails:

* `become_client` writes `/var/lib/milo/pending_client_role.json` and reboots.
  `rootfs/usr/local/bin/milo-first-boot` reads that file on the next boot and
  exits 1 if a key is missing — the unit then comes back as a plain server with
  the marker still on disk, and the operator sees an adoption that "worked"
  followed by a speaker that never appears. The key set is a contract between a
  Python route and a bash script; nothing held it before this file.
* Order is the other half: the marker, then the wifi profile, then
  `setup_completed`, then the reboot. Reboot before the wifi profile exists and
  the speaker comes back on no network at all, unreachable, with no UI.
* `complete_setup` rewrites hardware.json wholesale. The `ir_remote` block it
  carries over is what stops `milo-apply-hardware` from stripping the gpio-ir
  overlay out of config.txt — dropping it disables the receiver on a unit that
  has one, silently, at the one moment the user cannot yet notice.
"""
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import setup as api_setup
from backend.api.setup import create_setup_router
from backend.core.settings import VALID_LANGUAGES
from backend.hardware.registry import AUDIO_CARDS, SCREENS, is_dac_card


FIRST_BOOT = Path(__file__).resolve().parents[2] / "rootfs/usr/local/bin/milo-first-boot"

AMP_CARD = next(k for k, v in AUDIO_CARDS.items() if v["category"] == "amplifier")
DAC_CARD = next(k for k, v in AUDIO_CARDS.items() if v["category"] == "dac")

ADOPT = {
    "wifi_ssid": "Maison",
    "wifi_password": "secret",
    "audio_id": AMP_CARD,
    "speaker_name": "Bureau",
    "speaker_type": "bookshelf",
}


@pytest.fixture
def marker(tmp_path, monkeypatch):
    """Point the marker file — and the helper that consumes it — at tmp_path.

    Not a convenience: this checkout is the appliance, and the real path is
    `/var/lib/milo/pending_client_role.json`, which `milo-first-boot` acts on at
    the next boot. The fourth conftest guard would refuse the write and fail the
    test — pointing it here is what makes the route runnable at all.

    `FIRST_BOOT_HELPER` is stood in for the same way and for a sharper reason:
    the route now refuses when it is absent, and whether
    `/usr/local/bin/milo-first-boot` exists depends on whether the machine
    running the suite happens to be a flashed unit. Pinning it here is what makes
    every test below answer the same on a laptop, in CI and on the appliance.
    """
    monkeypatch.setattr(api_setup, "MILO_DATA_DIR", tmp_path)
    path = tmp_path / "pending_client_role.json"
    monkeypatch.setattr(api_setup, "PENDING_CLIENT_ROLE_FILE", path)

    helper = tmp_path / "milo-first-boot"
    helper.write_text("#!/bin/bash\n")
    monkeypatch.setattr(api_setup, "FIRST_BOOT_HELPER", helper)
    return path


@pytest.fixture
def services():
    """The four injected services, all stood in for.

    `systemd_manager.power` is the reboot: TestClient runs BackgroundTasks for
    real once the response is sent, so a live manager here would reboot this
    machine.
    """
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=False)
    settings.set_setting = AsyncMock(return_value=True)

    hardware = Mock()
    hardware.get_full_config = Mock(return_value={
        "audio": {"id": "none"},
        "screen": {"type": "none", "resolution": None},
        "rotary_encoder": {"enabled": True, "clk_pin": 22, "dt_pin": 27, "sw_pin": 23},
        "ir_remote": {"enabled": True, "gpio_pin": 17},
    })
    hardware.save_config = AsyncMock()
    hardware.apply_and_reboot = AsyncMock()

    network = Mock()
    network.save_network = AsyncMock()
    network.forget_network = AsyncMock()

    systemd = Mock()
    systemd.power = AsyncMock()

    return MagicMock(
        settings=settings, hardware=hardware, network=network, systemd=systemd
    )


@pytest.fixture
def client(services):
    app = FastAPI()
    app.include_router(create_setup_router(
        services.settings, services.hardware, services.network, services.systemd
    ))
    # `_delayed_apply` sleeps a second to let the response flush; nothing here
    # asserts on the delay, and paying it once per test is a minute of suite.
    with patch("backend.api.setup.asyncio.sleep", new=AsyncMock()):
        yield TestClient(app)


# =============================================================================
# The marker's contract with milo-first-boot
# =============================================================================

class TestMarkerContract:
    """The five keys crossing from a FastAPI route into a bash script."""

    def test_the_marker_carries_exactly_the_keys_first_boot_requires(self, client, marker, services):
        """A key renamed on either side exits milo-first-boot 1 and the speaker
        never converts — the adoption reports success and the unit reboots into
        the role it already had.

        The expectation is read out of the script itself, so this cannot pass by
        agreeing with a list typed here.
        """
        source = FIRST_BOOT.read_text()
        match = re.search(r"^required = \(([^)]*)\)", source, re.MULTILINE)
        assert match, "milo-first-boot no longer declares a `required` tuple"
        required = set(re.findall(r'"([^"]+)"', match.group(1)))
        assert len(required) == 5, f"expected 5 required keys, script declares {required}"

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 200
        written = set(api_setup.json.loads(marker.read_text()))
        assert written == required, (
            f"marker keys {sorted(written)} do not match what milo-first-boot "
            f"requires, {sorted(required)}"
        )

    def test_the_marker_path_is_the_one_first_boot_reads(self):
        """The route and the script name the same file, independently."""
        source = FIRST_BOOT.read_text()
        data_dir = re.search(r'^MILO_DATA_DIR="([^"]+)"', source, re.MULTILINE)
        marker_line = re.search(
            r'^PENDING_CLIENT_ROLE_FILE="\$MILO_DATA_DIR/([^"]+)"', source, re.MULTILINE
        )
        assert data_dir and marker_line, "milo-first-boot no longer declares the marker path"
        expected = Path(data_dir.group(1)) / marker_line.group(1)
        assert api_setup.PENDING_CLIENT_ROLE_FILE == expected

    @pytest.mark.parametrize("audio_id", [AMP_CARD, DAC_CARD])
    def test_volume_control_is_derived_from_the_card_category(
        self, client, marker, audio_id
    ):
        """A DAC drives an external amp, so the satellite must not attenuate
        twice. The wizard asks nothing here — the answer comes from the registry,
        and it is the marker that carries it to the client's hardware.json.
        """
        response = client.post(
            "/api/setup/become-client", json={**ADOPT, "audio_id": audio_id}
        )

        assert response.status_code == 200
        written = api_setup.json.loads(marker.read_text())
        assert written["volume_control"] is not is_dac_card(audio_id)
        assert written["overlay"] == AUDIO_CARDS[audio_id]["overlay"]


# =============================================================================
# become-client: the order, and the two rollbacks
# =============================================================================

class TestBecomeClientOrder:

    def test_the_reboot_is_scheduled_after_the_wifi_profile_and_the_flag(
        self, client, marker, services
    ):
        """Order, not presence: all four steps happen on the success path, so a
        test that only checks they were called passes on any permutation. Reboot
        before the wifi profile is saved and the speaker comes back with no
        network — no LAN, no UI, no way in but a reflash.
        """
        timeline = []
        services.network.save_network.side_effect = lambda *a, **k: timeline.append("wifi")

        async def _flag(key, value):
            timeline.append(f"set:{key}={value}")
            return True
        services.settings.set_setting.side_effect = _flag

        async def _power(action, delay):
            timeline.append(f"power:{action}")
        services.systemd.power.side_effect = _power

        # The marker is the step with no mock to record it — its own mtime would
        # not order against calls, so read it at the first recorded step instead.
        marker_seen_at_first_step = []
        original = services.network.save_network.side_effect

        def _wifi(*a, **k):
            marker_seen_at_first_step.append(marker.exists())
            return original(*a, **k)
        services.network.save_network.side_effect = _wifi

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 200
        assert marker_seen_at_first_step == [True], "wifi was saved before the marker existed"
        assert timeline == ["wifi", "set:setup_completed=True", "power:reboot"], timeline

    def test_a_wifi_save_that_fails_removes_the_marker_and_does_not_reboot(
        self, client, marker, services
    ):
        """Rebooting here converts the unit with no profile to come back on."""
        services.network.save_network.side_effect = RuntimeError("nmcli refused")

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 500
        assert not marker.exists(), "the unit will convert on its next boot anyway"
        services.systemd.power.assert_not_called()
        services.settings.set_setting.assert_not_awaited()

    def test_a_flag_that_will_not_persist_rolls_back_the_marker_and_the_profile(
        self, client, marker, services
    ):
        """`setup_completed` is what stops milo-first-boot re-running the wizard
        on the boot after the switch. Without it the marker must go too, and so
        must the wifi profile that was just written for a conversion that is not
        happening.
        """
        services.settings.set_setting = AsyncMock(return_value=False)

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 500
        assert not marker.exists()
        services.network.forget_network.assert_awaited_once_with(ADOPT["wifi_ssid"])
        services.systemd.power.assert_not_called()

    def test_an_open_target_network_is_saved_without_a_psk(self, client, marker, services):
        """`save_network` adds `wifi-sec.key-mgmt wpa-psk` for any password that
        is not None — an empty string included, which produces a profile that
        can never associate.
        """
        client.post("/api/setup/become-client", json={**ADOPT, "wifi_password": ""})

        services.network.save_network.assert_awaited_once_with(ADOPT["wifi_ssid"], None)


class TestBecomeClientGuards:

    def test_a_unit_without_the_first_boot_helper_refuses_the_adoption(
        self, client, marker, services, tmp_path, monkeypatch
    ):
        """The marker has one consumer, and only the image installs it.

        A backend running from a plain checkout — a dev host, not a flashed card
        — has neither `/usr/local/bin/milo-first-boot` nor its unit. Without this
        guard the route succeeds there: the marker is written, `setup_completed`
        flips, the WiFi profile is saved, the device reboots, and it comes back a
        plain server with the marker still on disk while the adopting server
        waits for a speaker that never appears.
        """
        monkeypatch.setattr(
            api_setup, "FIRST_BOOT_HELPER", tmp_path / "absent" / "milo-first-boot"
        )

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 409
        assert "milo-first-boot" in response.json()["detail"]
        assert not marker.exists()
        services.network.save_network.assert_not_awaited()
        services.settings.set_setting.assert_not_awaited()
        services.systemd.power.assert_not_called()

    def test_the_helper_the_route_checks_for_is_the_one_first_boot_ships_as(self):
        """A rename on either side turns the guard into a permanent refusal.

        The route refuses on a missing helper, so a path that stops matching the
        deployed name does not fail loudly — it makes every adoption answer 409
        on units that are perfectly able to convert.
        """
        assert api_setup.FIRST_BOOT_HELPER.name == FIRST_BOOT.name
        assert FIRST_BOOT.is_file(), f"{FIRST_BOOT} is no longer in the repo tree"

    def test_an_already_configured_device_answers_409(self, client, marker, services):
        """`WifiAdoptionService._push_config` reads this exact status to raise
        `already_configured`; anything else becomes `push_rejected`, and the UI
        tells the user their speaker refused instead of that it is already set up.
        """
        services.settings.get_setting = AsyncMock(return_value=True)

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 409
        assert not marker.exists()
        services.systemd.power.assert_not_called()

    @pytest.mark.parametrize("audio_id", ["none", "not-a-card"])
    def test_a_device_with_no_usable_card_is_refused_before_anything_is_written(
        self, client, marker, services, audio_id
    ):
        """A satellite with `audio_id: none` has no overlay to apply and comes
        back mute; the reboot would be spent for nothing.
        """
        response = client.post(
            "/api/setup/become-client", json={**ADOPT, "audio_id": audio_id}
        )

        assert response.status_code == 400
        assert not marker.exists()
        services.network.save_network.assert_not_awaited()
        services.systemd.power.assert_not_called()


# =============================================================================
# complete: the wizard
# =============================================================================

class TestCompleteSetup:

    def _payload(self, **over):
        return {
            "language": next(iter(VALID_LANGUAGES)),
            "audio_id": AMP_CARD,
            "screen_type": next(k for k in SCREENS if k != "none"),
            **over,
        }

    def test_the_ir_block_survives_the_wizard(self, client, services):
        """hardware.json is rewritten whole. `milo-apply-hardware` strips the
        gpio-ir overlay out of config.txt unless `ir_remote.enabled` is true in
        the file it reads — so a wizard that drops the block silently disables
        the receiver of every unit that has one, at first boot.
        """
        client.post("/api/setup/complete", json=self._payload())

        saved = services.hardware.save_config.await_args.args[0]
        current = services.hardware.get_full_config.return_value
        assert saved["ir_remote"] == current["ir_remote"]
        assert saved["rotary_encoder"] == current["rotary_encoder"]

    def test_the_chosen_card_reaches_the_file_with_its_overlay(self, client, services):
        """The overlay is what config.txt gets; the wizard is the only thing
        that ever picks it on a fresh unit.
        """
        client.post("/api/setup/complete", json=self._payload())

        saved = services.hardware.save_config.await_args.args[0]
        assert saved["audio"]["id"] == AMP_CARD
        assert saved["audio"]["overlay"] == AUDIO_CARDS[AMP_CARD]["overlay"]
        assert saved["audio"]["card_name"] == AUDIO_CARDS[AMP_CARD]["card_name"]
        assert "volume_control" not in saved["audio"], (
            "no override was sent; storing one freezes the card's own category"
        )

    def test_a_card_with_no_overlay_stores_no_overlay(self, client, services):
        """`none` has `overlay: None` — writing the key anyway hands
        milo-apply-hardware a null dtoverlay line.
        """
        client.post("/api/setup/complete", json=self._payload(audio_id="none"))

        saved = services.hardware.save_config.await_args.args[0]
        assert saved["audio"] == {"id": "none"}

    def test_an_explicit_volume_override_is_stored(self, client, services):
        client.post("/api/setup/complete", json=self._payload(volume_control=False))

        saved = services.hardware.save_config.await_args.args[0]
        assert saved["audio"]["volume_control"] is False

    def test_a_second_submit_does_not_reboot_again(self, client, services):
        """The wizard's Finish button is reachable twice on a slow first boot;
        the second reboot lands mid-apply.
        """
        services.settings.get_setting = AsyncMock(return_value=True)

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        services.hardware.save_config.assert_not_awaited()
        services.hardware.apply_and_reboot.assert_not_awaited()

    def test_the_reboot_runs_after_the_response_and_not_during_it(self, client, services):
        """Rewriting config.txt and rebooting inline would take the box down
        before the wizard ever sees its answer, so the UI would report a failed
        setup on every successful one.
        """
        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        assert response.json() == {"status": "rebooting"}
        services.hardware.apply_and_reboot.assert_awaited_once()
        services.systemd.power.assert_awaited_once_with("reboot")

    def test_a_hardware_write_that_fails_never_marks_setup_done(self, client, services):
        """A unit that reboots believing setup is done, on hardware that was
        never written, has no wizard, no audio and no way back but a reflash.

        This used to be a rollback — set the flag, undo it on the way out —
        which left the failure of the *undo* as a second way into that state.
        The flag is now only ever written after config.txt has been rewritten,
        so the guarantee is that it is never set at all here.
        """
        services.hardware.save_config.side_effect = OSError("read-only filesystem")

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 500
        assert ("setup_completed", True) not in [
            call.args for call in services.settings.set_setting.await_args_list
        ]
        services.hardware.apply_and_reboot.assert_not_awaited()

    def test_the_overlay_lands_before_the_flag_and_the_flag_before_the_reboot(
        self, client, services
    ):
        """The order is the fix for a silent brick.

        The flag used to be persisted before `milo-apply-hardware` ran, so a
        power cut in that window — the user pulling the plug because "it is
        taking a while" — produced a unit that considered itself configured and
        booted with no dtoverlay: no sound, no wizard, nothing in any log
        saying why. Applying first makes every cut before the flag bring the
        wizard back, and a cut after it lands on a config.txt already correct.

        `reboot=False` is asserted with the order: the helper reboots on its
        own by default, which would take the box down before the flag is
        written and put the window straight back.
        """
        order = []
        services.hardware.apply_and_reboot = AsyncMock(
            side_effect=lambda **kw: order.append(f"apply(reboot={kw.get('reboot', True)})")
        )
        services.settings.set_setting = AsyncMock(
            side_effect=lambda key, value: order.append(f"{key}={value}") or True
        )
        services.systemd.power = AsyncMock(side_effect=lambda *_: order.append("reboot"))

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        assert order == [
            "language=" + self._payload()["language"],
            "apply(reboot=False)",
            "setup_completed=True",
            "reboot",
        ]

    def test_an_apply_that_fails_leaves_the_wizard_in_place_and_the_box_up(
        self, client, services
    ):
        """config.txt was not rewritten, so rebooting would only lose the
        user's answers: the wizard must still be there on the next boot."""
        services.hardware.apply_and_reboot.side_effect = RuntimeError("config.txt is read-only")

        client.post("/api/setup/complete", json=self._payload())

        assert ("setup_completed", True) not in [
            call.args for call in services.settings.set_setting.await_args_list
        ]
        services.systemd.power.assert_not_awaited()

    def test_a_flag_that_will_not_persist_does_not_reboot(self, client, services):
        """Rebooting on an unwritten flag is a unit that shows the wizard again
        with its hardware already applied — recoverable, but the reboot bought
        nothing and hid the settings write that failed."""
        services.settings.set_setting = AsyncMock(
            side_effect=lambda key, value: key != "setup_completed"
        )

        client.post("/api/setup/complete", json=self._payload())

        services.systemd.power.assert_not_awaited()

    @pytest.mark.parametrize("bad", [
        {"language": "klingon"},
        {"audio_id": "not-a-card"},
        {"screen_type": "not-a-screen"},
    ])
    def test_an_unknown_registry_id_is_refused_before_anything_is_written(
        self, client, services, bad
    ):
        """Each of the three is a key into a dict a few lines later; an unchecked
        one is a KeyError inside the try, which rolls back and answers 500 —
        the wizard then reports an internal failure for a typo it could name.
        """
        response = client.post("/api/setup/complete", json=self._payload(**bad))

        assert response.status_code == 400
        services.hardware.save_config.assert_not_awaited()
        services.settings.set_setting.assert_not_awaited()
