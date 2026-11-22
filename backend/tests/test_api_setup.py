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
import asyncio
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import setup as api_setup
from backend.api.setup import create_setup_router
from backend.api.system import create_system_router
from backend.config.constants import SELECTABLE_GPIO_PINS
from backend.core.network.models import EthernetStatus, NetworkStatus, WifiConnectionStatus
from backend.core.settings import VALID_LANGUAGES
from backend.hardware.registry import AUDIO_CARDS, SCREENS, is_dac_card, reserved_pins
from backend.hardware.service import HardwareService


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
    settings.set_settings = AsyncMock(return_value=True)

    # `replace_config` is the transaction the Hardware page runs too: written,
    # applied, or the previous config put back. What it does with a refusal is
    # the real service's, run on a scratch file where a test needs it
    # (`test_a_refused_apply_puts_the_previous_hardware_config_back`).
    hardware = Mock()
    hardware.get_full_config = Mock(return_value={
        "audio": {"id": "none"},
        "screen": {"type": "none", "resolution": None},
        "rotary_encoder": {"enabled": True, "clk_pin": 22, "dt_pin": 27, "sw_pin": 23},
        "ir_remote": {"enabled": True, "gpio_pin": 17},
        "power_button": {"enabled": True, "led_gpio_pin": 5},
    })
    hardware.replace_config = AsyncMock()

    network = Mock()
    network.save_network = AsyncMock()
    network.forget_network = AsyncMock()
    # Linked by default: the wizard run from the LAN or the kiosk. The recovery
    # AP's case — no link — is TestFirstLink's.
    network.get_network_status = AsyncMock(return_value=_status(ethernet=True))

    systemd = Mock()
    systemd.power = AsyncMock()

    return MagicMock(
        settings=settings, hardware=hardware, network=network, systemd=systemd
    )


def _status(ethernet=False, wifi=False):
    return NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=ethernet, ip_address="192.168.1.20" if ethernet else None),
        wifi=WifiConnectionStatus(connected=wifi, ssid="Maison" if wifi else None),
        hotspot_active=False,
        link_lost=False,
    )


def _written(services):
    """Every (key, value) the route wrote, one key at a time or several at once."""
    single = [call.args for call in services.settings.set_setting.await_args_list]
    many = [
        item
        for call in services.settings.set_settings.await_args_list
        for item in call.args[0].items()
    ]
    return single + many


def _client(services):
    app = FastAPI()
    app.include_router(create_setup_router(
        services.settings, services.hardware, services.network, services.systemd
    ))
    return TestClient(app)


@pytest.fixture
def client(services):
    return _client(services)


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

    def test_a_rollback_that_cannot_forget_keeps_the_original_error(
        self, client, marker, services, caplog
    ):
        """`forget_network` raises when NetworkManager keeps the profile. The
        wizard must still show why the adoption failed — the flag — with the
        rollback's own failure in the log beside it, not in its place.
        """
        services.settings.set_setting = AsyncMock(return_value=False)
        services.network.forget_network = AsyncMock(side_effect=RuntimeError("delete refused"))

        with caplog.at_level(logging.WARNING):
            response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 500
        assert "setup_completed" in response.json()["detail"]
        assert "delete refused" not in response.json()["detail"]
        assert "delete refused" in caplog.text
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

    def test_a_server_waiting_for_its_first_link_is_not_adopted(self, client, marker, services):
        """Its wizard ran over the AP, as a server, and it waits for its first
        link to count as set up: still on its AP, a server adopting speakers
        nearby would otherwise convert it to a satellite, reboot it and lose the
        wizard's choices."""
        services.settings.get_setting = AsyncMock(
            side_effect=lambda key: key == "setup_awaiting_network"
        )

        response = client.post("/api/setup/become-client", json=ADOPT)

        assert response.status_code == 409
        assert not marker.exists()
        services.network.save_network.assert_not_awaited()
        services.systemd.power.assert_not_called()

    @pytest.mark.parametrize("audio_id", ["none", "not-a-card", ""])
    def test_a_device_with_no_usable_card_is_refused_before_anything_is_written(
        self, client, marker, services, audio_id
    ):
        """A satellite with `audio_id: none` has no overlay to apply and comes
        back mute; the reboot would be spent for nothing. Refused by the rule
        every request that configures a card shares.
        """
        response = client.post(
            "/api/setup/become-client", json={**ADOPT, "audio_id": audio_id}
        )

        assert response.status_code == 422
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
            "volume_control": True,
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

        saved = services.hardware.replace_config.await_args.args[0]
        current = services.hardware.get_full_config.return_value
        assert saved["ir_remote"] == current["ir_remote"]
        assert saved["rotary_encoder"] == current["rotary_encoder"]

    def test_the_wizard_never_keeps_the_ir_on_a_pin_the_card_reserves(self, client, services):
        """The factory IR line is GPIO 17, which the Amp100's overlay takes as
        its codec reset: kept enabled there, one of the two drivers loses the
        line at the next boot, and it may be the sound card. The receiver is
        wired to its pin, so the wizard turns it off rather than moving it.
        """
        assert 17 in reserved_pins("hifiberry_amp100")

        client.post("/api/setup/complete", json=self._payload(audio_id="hifiberry_amp100"))

        saved = services.hardware.replace_config.await_args.args[0]
        assert saved["ir_remote"] == {"enabled": False, "gpio_pin": 17}

    def test_the_wizard_keeps_the_ir_on_a_card_that_leaves_its_pin_free(self, client, services):
        assert 17 not in reserved_pins("hifiberry_amp4pro")

        client.post("/api/setup/complete", json=self._payload(audio_id="hifiberry_amp4pro"))

        saved = services.hardware.replace_config.await_args.args[0]
        assert saved["ir_remote"] == {"enabled": True, "gpio_pin": 17}

    @pytest.mark.parametrize("block, pin_key", [
        (block, pin_keys[-1]) for block, pin_keys in api_setup.WIZARD_KEPT_PERIPHERALS.items()
    ])
    def test_every_kept_peripheral_on_a_held_pin_is_turned_off(self, client, services, block, pin_key):
        """The same line, for the encoder and the power LED: a unit whose saved
        pins were free on its old card can name a line the new one holds. Every
        card, every held pin; the other blocks, on pins no card holds, are left
        exactly as they were."""
        held_anywhere = set().union(*(reserved_pins(a) for a in AUDIO_CARDS))
        free = [p for p in SELECTABLE_GPIO_PINS if p not in held_anywhere]
        current = {
            **services.hardware.get_full_config.return_value,
            "rotary_encoder": {"enabled": True, "clk_pin": free[0], "dt_pin": free[1], "sw_pin": free[2]},
            "ir_remote": {"enabled": True, "gpio_pin": free[3]},
            "power_button": {"enabled": True, "led_gpio_pin": free[4]},
        }
        checked = 0
        for audio_id in AUDIO_CARDS:
            for pin in reserved_pins(audio_id):
                services.hardware.get_full_config.return_value = {
                    **current, block: {**current[block], "enabled": True, pin_key: pin},
                }
                # A fresh router per submit: one that answered "rebooting" holds
                # every submit after it until the unit is down.
                _client(services).post("/api/setup/complete", json=self._payload(audio_id=audio_id))

                saved = services.hardware.replace_config.await_args.args[0]
                assert saved[block] == {**current[block], "enabled": False, pin_key: pin}
                for other in api_setup.WIZARD_KEPT_PERIPHERALS.keys() - {block}:
                    assert saved[other] == current[other]
                checked += 1
        assert checked >= 40, f"only {checked} held pins found — the registry declares none"

    def test_the_power_button_survives_the_wizard(self, client, services):
        """The apply writes the bootloader EEPROM from this block. Dropped, a
        reset-setup on a unit with a wired button would leave it booting on
        power and its button LED dark — undoing a choice the wizard never
        offered."""
        client.post("/api/setup/complete", json=self._payload())

        saved = services.hardware.replace_config.await_args.args[0]
        assert saved["power_button"] == services.hardware.get_full_config.return_value["power_button"]

    def test_the_chosen_card_reaches_the_file_with_its_overlay(self, client, services):
        """The overlay is what config.txt gets; the wizard is the only thing
        that ever picks it on a fresh unit. Nothing else is stored: the ALSA
        card is the registry's one `OUTPUT_CARD`, not a per-unit record.
        """
        client.post("/api/setup/complete", json=self._payload())

        saved = services.hardware.replace_config.await_args.args[0]
        assert saved["audio"] == {
            "id": AMP_CARD,
            "overlay": AUDIO_CARDS[AMP_CARD]["overlay"],
            "volume_control": True,
        }

    @pytest.mark.parametrize("audio_id, volume_control", [(AMP_CARD, False), (DAC_CARD, True)])
    def test_the_volume_choice_sent_is_stored_not_the_category(
        self, client, services, audio_id, volume_control
    ):
        """`get_volume_control` reads the stored key and derives nothing, so the
        choice the wizard showed is the one that must land — against the
        category's default in both directions."""
        assert volume_control is is_dac_card(audio_id)

        client.post(
            "/api/setup/complete",
            json=self._payload(audio_id=audio_id, volume_control=volume_control),
        )

        saved = services.hardware.replace_config.await_args.args[0]
        assert saved["audio"]["volume_control"] is volume_control

    @pytest.mark.parametrize("audio_id", ["none", "not-a-card"])
    def test_a_server_without_a_card_is_refused_before_anything_is_written(
        self, client, services, audio_id
    ):
        """Milō plays through a HAT or not at all: `none` would reboot the
        server into a unit with no playback device, the wizard gone."""
        response = client.post("/api/setup/complete", json=self._payload(audio_id=audio_id))

        assert response.status_code == 422
        services.hardware.replace_config.assert_not_awaited()
        services.settings.set_setting.assert_not_awaited()
        services.settings.set_settings.assert_not_awaited()
        services.systemd.power.assert_not_awaited()

    @staticmethod
    def _reflecting_store(services) -> dict:
        """Settings that read back what the route wrote, starting from a fresh unit.

        The guard reads `setup_completed`, so a stand-in answering a fixed value
        decides by itself whether a second submit is "after" the first one: the
        flag must reach the store through the route, never through the test.
        """
        store = {"setup_completed": False}

        async def _get(key):
            return store.get(key)

        async def _set(key, value):
            store[key] = value
            return True

        async def _set_many(updates):
            store.update(updates)
            return True

        services.settings.get_setting = AsyncMock(side_effect=_get)
        services.settings.set_setting = AsyncMock(side_effect=_set)
        services.settings.set_settings = AsyncMock(side_effect=_set_many)
        return store

    async def test_a_second_submit_inside_the_apply_window_applies_nothing(self, services):
        """The kiosk and a phone both on the wizard, Finish pressed on each a few
        seconds apart. `setup_completed` is written only once
        `milo-apply-hardware` has rewritten config.txt, so a second submit
        landing while the first apply runs used to pass the guard: two helpers
        interleaving their `sed -i` and `cat >>` on config.txt — which can end
        with no MILO AUDIO block, a mute unit marked configured — then two
        reboots, the first one killing the second helper mid-script.

        Only the first apply is slow: the second request must answer while the
        first is still inside its apply, which now runs within the request.
        """
        import httpx

        self._reflecting_store(services)
        entered, release = asyncio.Event(), asyncio.Event()

        async def _apply(_config):
            if services.hardware.replace_config.await_count == 1:
                entered.set()
                await release.wait()
        services.hardware.replace_config.side_effect = _apply

        app = FastAPI()
        app.include_router(create_setup_router(
            services.settings, services.hardware, services.network, services.systemd
        ))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://milo") as http:
            first = asyncio.create_task(
                http.post("/api/setup/complete", json=self._payload())
            )
            await entered.wait()

            second = await http.post("/api/setup/complete", json=self._payload())
            release.set()
            first = await first

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == {"status": "rebooting"}
        assert services.hardware.replace_config.await_count == 1
        services.systemd.power.assert_awaited_once_with("reboot", 1.0)

    def test_a_submit_after_setup_completed_applies_nothing(self, client, services):
        """The wizard's Finish button is reachable again on a slow first boot,
        after the flag is written and while the reboot is on its way: a second
        apply there lands mid-shutdown."""
        self._reflecting_store(services)

        client.post("/api/setup/complete", json=self._payload())
        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        assert response.json() == {"status": "rebooting"}
        services.hardware.replace_config.assert_awaited_once()
        services.systemd.power.assert_awaited_once_with("reboot", 1.0)

    def test_a_failed_apply_lets_the_wizard_be_submitted_again(self, client, services):
        """The wizard stays on screen after a failed apply; a submit refused
        there forever would leave a fresh unit with no way to finish its setup
        short of a backend restart."""
        self._reflecting_store(services)
        services.hardware.replace_config.side_effect = [
            RuntimeError("config.txt is read-only"), None,
        ]

        client.post("/api/setup/complete", json=self._payload())
        client.post("/api/setup/complete", json=self._payload())

        assert services.hardware.replace_config.await_count == 2
        services.systemd.power.assert_awaited_once_with("reboot", 1.0)

    def test_a_refused_payload_does_not_hold_the_wizard(self, client, services):
        """A 400 is answered before anything is scheduled, so nothing would
        ever release a submit held by it."""
        self._reflecting_store(services)

        refused = client.post("/api/setup/complete", json=self._payload(language="klingon"))
        client.post("/api/setup/complete", json=self._payload())

        assert refused.status_code == 400
        services.hardware.replace_config.assert_awaited_once()

    def test_the_reboot_runs_after_the_response_and_not_during_it(self, client, services):
        """Rewriting config.txt and rebooting inline would take the box down
        before the wizard ever sees its answer, so the UI would report a failed
        setup on every successful one.
        """
        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        assert response.json() == {"status": "rebooting"}
        services.hardware.replace_config.assert_awaited_once()
        services.systemd.power.assert_awaited_once_with("reboot", 1.0)

    def test_a_hardware_write_that_fails_never_marks_setup_done(self, client, services):
        """A unit that reboots believing setup is done, on hardware that was
        never written, has no wizard, no audio and no way back but a reflash.

        This used to be a rollback — set the flag, undo it on the way out —
        which left the failure of the *undo* as a second way into that state.
        The flag is now only ever written after config.txt has been rewritten,
        so the guarantee is that it is never set at all here.
        """
        services.hardware.replace_config.side_effect = OSError("read-only filesystem")

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 500
        assert ("setup_completed", True) not in _written(services)

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
        That the helper itself never reboots is `HardwareService.apply`'s, and
        pinned with it.
        """
        order = []
        services.hardware.replace_config = AsyncMock(side_effect=lambda _config: order.append("apply"))
        services.settings.set_setting = AsyncMock(
            side_effect=lambda key, value: order.append(f"{key}={value}") or True
        )
        services.settings.set_settings = AsyncMock(
            side_effect=lambda updates: order.append(
                " ".join(f"{key}={value}" for key, value in updates.items())
            ) or True
        )
        services.systemd.power = AsyncMock(side_effect=lambda *_: order.append("reboot"))

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 200
        # The flag and the end of the wait are one write: torn apart, a unit
        # counted as set up would still skip role detection at every boot.
        assert order == [
            "language=" + self._payload()["language"],
            "apply",
            "setup_completed=True setup_awaiting_network=False",
            "reboot",
        ]

    def test_an_apply_that_fails_leaves_the_wizard_in_place_and_the_box_up(
        self, client, services
    ):
        """config.txt was not rewritten, so rebooting would only lose the
        user's answers: the wizard must still be there on the next boot."""
        services.hardware.replace_config.side_effect = RuntimeError("config.txt is read-only")

        client.post("/api/setup/complete", json=self._payload())

        assert ("setup_completed", True) not in _written(services)
        services.systemd.power.assert_not_awaited()

    def test_a_refused_apply_puts_the_previous_hardware_config_back(self, services, tmp_path):
        """The helper reads hardware.json, so the wizard writes it before the
        apply — and the next submit reads it back as the unit's current state.
        Left as written after a refusal, it names a card config.txt never
        loaded, and the peripherals that card's pins turned off stay off:
        an IR receiver on GPIO 17, refused with an Amp100, stayed disabled on
        the Amp4 Pro applied next, which leaves 17 free.

        The real service on a scratch hardware.json, `sudo milo-apply-hardware`
        alone stood in for: what is asserted is the file the next boot reads.
        """
        import json

        assert 17 in reserved_pins("hifiberry_amp100")
        assert 17 not in reserved_pins("hifiberry_amp4pro")
        original = services.hardware.get_full_config.return_value
        hardware = HardwareService()
        hardware.hardware_file = tmp_path / "hardware.json"
        asyncio.run(hardware.save_config(original))
        hardware.apply = AsyncMock(side_effect=[RuntimeError("EEPROM write refused"), None])
        services.hardware = hardware

        def stored():
            data = json.loads(hardware.hardware_file.read_text())
            data.pop("schema_version")
            return data

        refused = _client(services).post(
            "/api/setup/complete", json=self._payload(audio_id="hifiberry_amp100")
        )

        assert refused.status_code == 500
        assert stored() == original

        _client(services).post("/api/setup/complete", json=self._payload(audio_id="hifiberry_amp4pro"))

        assert stored()["audio"]["id"] == "hifiberry_amp4pro"
        assert stored()["ir_remote"] == {"enabled": True, "gpio_pin": 17}

    def test_a_flag_that_will_not_persist_does_not_reboot(self, client, services):
        """Rebooting on an unwritten flag is a unit that shows the wizard again
        with its hardware already applied — recoverable, but the reboot bought
        nothing and hid the settings write that failed."""
        services.settings.set_settings = AsyncMock(return_value=False)

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 500
        services.systemd.power.assert_not_awaited()

    @pytest.mark.parametrize("bad", [
        {"language": "klingon"},
        {"screen_type": "not-a-screen"},
    ])
    def test_an_unknown_registry_id_is_refused_before_anything_is_written(
        self, client, services, bad
    ):
        """Each of the two is a key into a dict a few lines later; an unchecked
        one is a KeyError inside the try, which rolls back and answers 500 —
        the wizard then reports an internal failure for a typo it could name.
        """
        response = client.post("/api/setup/complete", json=self._payload(**bad))

        assert response.status_code == 400
        services.hardware.replace_config.assert_not_awaited()
        services.settings.set_setting.assert_not_awaited()
        services.settings.set_settings.assert_not_awaited()

    def test_a_refused_apply_answers_its_reason_and_reboots_nothing(self, client, services):
        """The apply runs inside the request, as on the Hardware page: the
        wizard used to answer "rebooting" first and apply in the background,
        so a refusal reached nobody — the wizard waited three minutes for a
        reboot that never came and said it had timed out."""
        services.hardware.replace_config.side_effect = RuntimeError("Hardware apply failed: EEPROM write refused")

        response = client.post("/api/setup/complete", json=self._payload())

        assert response.status_code == 500
        assert "EEPROM write refused" in response.json()["detail"]
        services.systemd.power.assert_not_awaited()


# =============================================================================
# The first link: when a unit set up over its recovery AP counts as set up
# =============================================================================

class TestFirstLink:
    """Over the recovery AP the wizard saves the home network without joining it,
    so nothing has tried the key when the unit reboots. Counted as set up then, a
    mistyped key left a unit whose AP refuses the wizard's writes: the key could
    not be typed again without a cable. The unit now counts as set up at its
    first link, and the wizard comes back on the AP until then."""

    PAYLOAD = {
        "language": "english",
        "audio_id": AMP_CARD,
        "volume_control": True,
        "screen_type": "none",
    }

    @pytest.fixture
    def settings(self, tmp_path):
        """The real settings service on a scratch file: what the guard, the
        status read and the next boot read is what the route wrote."""
        from backend.core.settings import SettingsService

        service = SettingsService()
        service.settings_file = str(tmp_path / "settings.json")
        return service

    @pytest.fixture
    def unit(self, services, settings):
        """A unit on its recovery AP: no link. `link_up()` is the network service
        telling its listeners that a link came up; `boot()` a new backend."""
        services.settings = settings
        services.network.get_network_status = AsyncMock(return_value=_status())
        listeners = []
        services.network.add_link_listener = Mock(side_effect=listeners.append)

        class Unit:
            def boot(self):
                listeners.clear()
                app = FastAPI()
                app.include_router(create_setup_router(
                    settings, services.hardware, services.network, services.systemd
                ))
                app.include_router(
                    create_system_router(services.systemd, settings_service=settings),
                    prefix="/api/system",
                )
                self.http = TestClient(app)
                return self

            async def link_up(self):
                for listener in listeners:
                    await listener()

            def awaiting(self):
                return self.http.get("/api/system/status").json()["data"]["setup_awaiting_network"]

        return Unit().boot()

    async def test_over_the_ap_the_unit_is_set_up_at_its_first_link(self, unit, settings, services):
        response = unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert response.json() == {"status": "rebooting"}
        services.systemd.power.assert_awaited_once_with("reboot", 1.0)
        assert await settings.get_setting("setup_completed") is False
        assert unit.awaiting() is True

        unit.boot()
        await unit.link_up()

        assert await settings.get_setting("setup_completed") is True
        assert unit.awaiting() is False

    async def test_until_then_the_wizard_can_be_sent_again(self, unit, services):
        """The key was wrong: the unit reboots onto its AP, the wizard reopens
        on its Network step, the key is typed again and the wizard sent again —
        applied and rebooted again, the network tried at that boot."""
        unit.http.post("/api/setup/complete", json=self.PAYLOAD)
        unit.boot()

        response = unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert response.json() == {"status": "rebooting"}
        assert services.hardware.replace_config.await_count == 2
        assert services.systemd.power.await_count == 2

    async def test_a_second_submit_before_the_reboot_applies_nothing(self, unit, services):
        """`setup_completed` does not guard these seconds any more: the flag
        waits for the link. A second Finish on its way down would run
        milo-apply-hardware under the shutdown."""
        unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        response = unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert response.json() == {"status": "rebooting"}
        services.hardware.replace_config.assert_awaited_once()
        services.systemd.power.assert_awaited_once()

    async def test_a_reboot_that_did_not_happen_releases_the_wizard(self, unit, services):
        """Held for good, the wizard on screen answers "rebooting" to every
        submit of a unit that stayed up."""
        services.systemd.power = AsyncMock(return_value=False)
        unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert services.hardware.replace_config.await_count == 2

    async def test_a_link_on_a_unit_whose_wizard_never_ran_sets_nothing_up(self, unit, settings):
        """An Ethernet cable on a fresh unit is a link too: its wizard stays."""
        await unit.link_up()

        assert await settings.get_setting("setup_completed") is False
        assert unit.awaiting() is False

    async def test_a_wizard_run_with_a_link_is_set_up_at_once(self, unit, settings, services):
        """From the LAN or a kiosk on a network, the network shown is the one
        the unit has: nothing is left to confirm."""
        services.network.get_network_status = AsyncMock(return_value=_status(wifi=True))

        unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert await settings.get_setting("setup_completed") is True
        assert unit.awaiting() is False

    async def test_a_status_that_cannot_be_read_counts_as_no_link(self, unit, settings, services):
        """nmcli timing out under the AP is no proof of a link: counted as one,
        the key typed over the AP — never tried — would set the unit up, and a
        mistyped one leave it on an AP with no wizard. Raised, the wizard would
        answer 500 to a submit that only has to wait for the link."""
        services.network.get_network_status = AsyncMock(side_effect=asyncio.TimeoutError())

        response = unit.http.post("/api/setup/complete", json=self.PAYLOAD)

        assert response.json() == {"status": "rebooting"}
        assert await settings.get_setting("setup_completed") is False
        assert unit.awaiting() is True

    async def test_a_unit_set_up_never_reopens_its_wizard_on_the_network_step(self, unit, settings):
        """The waiting flag is a step of a setup in progress: left set beside
        `setup_completed` (a file another release wrote, a write torn by a power
        cut), it must not send a set-up unit's clients back to the wizard."""
        await settings.set_settings({"setup_completed": True, "setup_awaiting_network": True})

        assert unit.awaiting() is False

    def test_the_router_listens_to_the_network_service(self, services):
        """Without the listener nothing ever confirms the setup: a unit set up
        over its AP shows the wizard for good, on a network it joined."""
        create_setup_router(services.settings, services.hardware, services.network, services.systemd)

        services.network.add_link_listener.assert_called_once()
