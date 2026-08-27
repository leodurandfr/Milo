"""The two settings routes that reach the audio path and the boot config.

What breaks when these fail:

* **`PUT /dock-apps` switches audio functions on and off.** Removing `multiroom`
  from the dock disables multiroom routing — the whole ALSA reconfiguration and
  a source restart with it; removing `equalizer` bypasses CamillaDSP's effects;
  removing an audio source stops its systemd units, or transitions away from it
  if it is the one playing. Each effect is committed to `settings.json` as soon
  as it lands, so a failure mid-batch leaves the stored list describing exactly
  the effects that were applied. It is not a transaction and nothing is
  compensated — a rollback would mean re-entering the audio path from an error
  handler — so the *only* thing keeping the dock from showing a tile for a
  function that was just switched off is that per-step commit. Measured
  2026-08-27, all 23 of its uncovered lines were the failure branches and the
  step-by-step commit.
* **`PUT /hardware-config` writes `hardware.json` and then reboots the box.**
  The apply runs from a `BackgroundTask` so the HTTP response can flush first —
  and TestClient runs BackgroundTasks for real, against an appliance whose
  `apply_and_reboot` shells `sudo milo-apply-hardware` and then reboots. Its
  `volume_control` field is the DAC decision: `False` means an external amp owns
  the level and CamillaDSP must stay pinned at 0 dB.

Nothing here is allowed to reach the real services. `hardware_service` is an
AsyncMock and `settings_service` a stand-in built from the real declared
defaults, so a route reading a key the validator guarantees is testable without
a fallback that cannot happen in production.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.settings import create_settings_router
from backend.core.models.audio_state import AudioSource
from backend.core.settings import SettingsService


@pytest.fixture
def state_machine():
    sm = Mock()
    sm.broadcast = AsyncMock()
    sm.transition_to_source = AsyncMock(return_value=True)
    sm.system_state.active_source = AudioSource.RADIO
    sm.get_current_state = Mock(return_value={"active_source": "radio"})
    return sm


@pytest.fixture
def systemd():
    manager = Mock()
    manager.stop = AsyncMock(return_value=True)
    manager.start = AsyncMock(return_value=True)
    manager.restart = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def routing():
    svc = Mock()
    svc.set_multiroom_enabled = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def equalizer():
    svc = Mock()
    svc.set_local_equalizer_effects_enabled = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def hardware():
    """`apply_and_reboot` shells `sudo milo-apply-hardware` and reboots this Pi."""
    svc = Mock()
    svc.save_config = AsyncMock()
    svc.apply_and_reboot = AsyncMock()
    svc.get_screen_info = Mock(
        return_value={"type": "none", "resolution": {"width": None, "height": None}}
    )
    return svc


@pytest.fixture
def settings():
    defaults = SettingsService().defaults
    mock = Mock()
    mock.get_setting = AsyncMock(side_effect=lambda key: defaults.get(key))
    mock.set_setting = AsyncMock(return_value=True)
    mock.set_settings = AsyncMock(return_value=True)
    mock.load_settings = AsyncMock(return_value={"dock": {"enabled_apps": []}})
    mock.invalidate_cache = Mock()
    mock._cache = None
    return mock


@pytest.fixture
def client(state_machine, systemd, routing, equalizer, hardware, settings):
    app = FastAPI()
    app.include_router(
        create_settings_router(
            volume_service=Mock(),
            state_machine=state_machine,
            screen_controller=Mock(),
            systemd_manager=systemd,
            routing_service=routing,
            hardware_service=hardware,
            settings_service=settings,
            multiroom_equalizer_service=equalizer,
        ),
        prefix="/api/settings",
    )
    return TestClient(app)


def _dock(settings, apps):
    settings.load_settings = AsyncMock(return_value={"dock": {"enabled_apps": list(apps)}})


def _saved_dock_lists(settings):
    """Every `dock.enabled_apps` value written, in order."""
    return [
        c.args[1] for c in settings.set_setting.await_args_list
        if c.args and c.args[0] == "dock.enabled_apps"
    ]


class TestTheDockWithNothingToApply:
    """A reorder is not a state change, and must not re-enter the audio path."""

    def test_reordering_saves_and_announces_without_touching_a_service(
        self, client, settings, routing, equalizer, systemd, state_machine
    ):
        """The dock is drag-reorderable. Running the enable/disable machinery for
        a set that did not change would disable and re-enable multiroom — a full
        ALSA reconfiguration and a source restart — because the tiles moved."""
        _dock(settings, ["spotify", "multiroom", "equalizer"])

        response = client.put(
            "/api/settings/dock-apps",
            json={"enabled_apps": ["equalizer", "multiroom", "spotify"]},
        )

        assert response.status_code == 200
        assert response.json()["config"]["enabled_apps"] == [
            "equalizer", "multiroom", "spotify"
        ]
        routing.set_multiroom_enabled.assert_not_called()
        equalizer.set_local_equalizer_effects_enabled.assert_not_called()
        systemd.stop.assert_not_called()
        assert _saved_dock_lists(settings) == [["equalizer", "multiroom", "spotify"]]
        assert state_machine.broadcast.await_count == 1
        # The short-circuit's one visible mark: no operations log, because no
        # operation ran. Falling through would answer the batch shape for a
        # request that applied nothing.
        assert "operations" not in response.json()

    def test_a_reorder_that_cannot_be_saved_is_a_failure_not_a_silent_success(
        self, client, settings
    ):
        """The list is what the dock renders on the next load; reporting success
        for a write that did not happen means the order reverts on reload with
        nothing to explain it."""
        _dock(settings, ["spotify", "multiroom"])
        settings.set_setting = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["multiroom", "spotify"]}
        )

        assert response.status_code == 500


class TestTheDockDisables:
    """Turning a function off. Each one reaches the audio path."""

    def test_disabling_the_playing_source_transitions_away_from_it_first(
        self, client, settings, state_machine, systemd
    ):
        """Stopping its units under a live transition leaves the state machine
        pointing at a source with no process — the UI shows it playing and
        nothing answers a command."""
        _dock(settings, ["radio", "spotify", "multiroom"])

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["spotify", "multiroom"]}
        )

        assert response.status_code == 200
        state_machine.transition_to_source.assert_awaited_once_with(AudioSource.NONE)
        systemd.stop.assert_not_called()

    def test_disabling_an_idle_source_stops_its_units_directly(
        self, client, settings, state_machine, systemd
    ):
        """No transition is due — it is not playing — but its units are running:
        go-librespot holds its ALSA device whether or not it is the active
        source, and a second source cannot open the same loopback leg."""
        _dock(settings, ["radio", "spotify", "multiroom"])

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom"]}
        )

        assert response.status_code == 200
        state_machine.transition_to_source.assert_not_called()
        assert [c.args[0] for c in systemd.stop.await_args_list] == ["milo-spotify.service"]

    def test_disabling_bluetooth_stops_both_of_its_units(self, client, settings, systemd):
        """BlueALSA answers *who is connected* and bluealsa-aplay carries the
        audio; leaving either up keeps the phone paired and streaming into a
        source the dock says is off."""
        _dock(settings, ["radio", "bluetooth", "multiroom"])

        client.put("/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom"]})

        assert [c.args[0] for c in systemd.stop.await_args_list] == [
            "milo-bluealsa-aplay.service", "milo-bluealsa.service"
        ]

    def test_disabling_multiroom_goes_through_the_routing_service(
        self, client, settings, routing
    ):
        """It owns the whole transition — source restart, snapcast stop, the
        settings and routing.env writes, and the broadcast. Doing any of it here
        would be a second implementation of the mode switch."""
        _dock(settings, ["radio", "multiroom", "equalizer"])

        client.put("/api/settings/dock-apps", json={"enabled_apps": ["radio", "equalizer"]})

        routing.set_multiroom_enabled.assert_awaited_once()
        assert routing.set_multiroom_enabled.await_args.args[0] is False

    def test_disabling_the_equalizer_bypasses_the_effects(
        self, client, settings, equalizer
    ):
        """CamillaDSP itself stays running — it is the only attenuation stage —
        so this is a bypass of EQ, compressor and loudness, never a stop."""
        _dock(settings, ["radio", "equalizer", "multiroom"])

        client.put("/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom"]})

        equalizer.set_local_equalizer_effects_enabled.assert_awaited_once_with(False)


class TestTheDockEnables:
    """Turning a function on, and where the tile is placed."""

    def test_enabling_an_audio_source_starts_nothing(
        self, client, settings, systemd, state_machine
    ):
        """A source's units start when it becomes the active source. Starting
        them here would open its ALSA device next to the one that is playing."""
        _dock(settings, ["radio", "multiroom"])

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom", "spotify"]}
        )

        assert response.status_code == 200
        systemd.start.assert_not_called()

    def test_enabling_multiroom_goes_through_the_routing_service(
        self, client, settings, routing
    ):
        _dock(settings, ["radio", "equalizer"])

        client.put("/api/settings/dock-apps", json={"enabled_apps": ["radio", "equalizer", "multiroom"]})

        assert routing.set_multiroom_enabled.await_args.args[0] is True

    def test_enabling_the_equalizer_restores_the_effects(
        self, client, settings, equalizer
    ):
        _dock(settings, ["radio", "multiroom"])

        client.put("/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom", "equalizer"]})

        equalizer.set_local_equalizer_effects_enabled.assert_awaited_once_with(True)

    def test_the_new_tile_lands_where_the_payload_put_it(self, client, settings):
        """The intermediate commits carry membership; this is what keeps them
        from also reordering the dock behind the user's back while the batch
        runs."""
        _dock(settings, ["multiroom", "spotify"])

        client.put(
            "/api/settings/dock-apps",
            json={"enabled_apps": ["equalizer", "multiroom", "spotify"]},
        )

        assert _saved_dock_lists(settings)[0] == ["equalizer", "multiroom", "spotify"]


class TestTheDockAtomicity:
    """The rule the whole handler exists for: what is stored is what was applied."""

    def test_a_failed_disable_leaves_the_tile_in_the_dock(
        self, client, settings, routing
    ):
        """Nothing is compensated here, so the stored list is the only record of
        what actually happened. A dock that dropped the tile would say multiroom
        is off while snapcast is still running and the ALSA routing unchanged."""
        _dock(settings, ["radio", "multiroom", "equalizer"])
        routing.set_multiroom_enabled = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["radio", "equalizer"]}
        )

        assert response.status_code == 500
        assert _saved_dock_lists(settings) == [], "a failed effect was committed anyway"

    def test_the_effects_that_did_land_are_kept_when_a_later_one_fails(
        self, client, settings, equalizer, routing
    ):
        """One disable that lands, then one enable that fails.

        Ordered deliberately across the two phases rather than within one: the
        handler iterates `set` differences, so which of two disables runs first
        is not something a test can pin — but disables always precede enables.
        The equalizer really is bypassed by the time multiroom refuses, so the
        stored list must have lost its tile and gained nothing.
        """
        _dock(settings, ["equalizer", "spotify"])
        routing.set_multiroom_enabled = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["spotify", "multiroom"]}
        )

        assert response.status_code == 500
        equalizer.set_local_equalizer_effects_enabled.assert_awaited_once_with(False)
        saved = _saved_dock_lists(settings)
        assert saved, "the equalizer bypass landed but was never committed"
        assert "equalizer" not in saved[-1]
        assert "multiroom" not in saved[-1], "multiroom never came up; its tile must not"

    def test_a_commit_that_cannot_be_written_stops_the_batch(
        self, client, settings, equalizer, routing
    ):
        """The commit is the only record of what was applied. Once the store
        refuses it, every further effect is one the dock can no longer describe —
        so the batch has to stop there, not run to the end and fail at the last
        write."""
        _dock(settings, ["equalizer", "spotify"])
        settings.set_setting = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["spotify", "multiroom"]}
        )

        assert response.status_code == 500
        equalizer.set_local_equalizer_effects_enabled.assert_awaited_once_with(False)
        routing.set_multiroom_enabled.assert_not_called()

    def test_a_failed_source_stop_is_a_failure(self, client, settings, systemd):
        """A unit that refused to stop still holds its ALSA device."""
        _dock(settings, ["radio", "spotify", "multiroom"])
        systemd.stop = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom"]}
        )

        assert response.status_code == 500

    def test_a_failed_transition_away_from_the_playing_source_is_a_failure(
        self, client, settings, state_machine
    ):
        _dock(settings, ["radio", "spotify", "multiroom"])
        state_machine.transition_to_source = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["spotify", "multiroom"]}
        )

        assert response.status_code == 500

    def test_a_failed_equalizer_bypass_is_a_failure(self, client, settings, equalizer):
        _dock(settings, ["radio", "equalizer", "multiroom"])
        equalizer.set_local_equalizer_effects_enabled = AsyncMock(return_value=False)

        response = client.put(
            "/api/settings/dock-apps", json={"enabled_apps": ["radio", "multiroom"]}
        )

        assert response.status_code == 500


class TestTheHardwareConfig:
    """The write that decides what the box boots as."""

    PAYLOAD = {
        "audio": {"id": "hifiberry_amp2"},
        "screen": {"type": "waveshare_7_usb"},
        "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
        "ir_remote": {"enabled": False, "gpio_pin": 17},
    }

    def test_the_config_is_saved_before_anything_is_applied(self, client, hardware):
        """`apply_and_reboot` reboots. A config applied before it was stored is a
        box that comes back on the previous hardware with `config.txt` describing
        the new one."""
        response = client.put("/api/settings/hardware-config", json=self.PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"status": "rebooting"}
        hardware.save_config.assert_awaited_once()

    def test_the_card_properties_are_resolved_from_the_registry_not_the_payload(
        self, client, hardware
    ):
        """The request carries an id; `config.txt` needs the overlay name. Taking
        an overlay from the body would let a request write an arbitrary dtoverlay
        line into the boot config."""
        client.put("/api/settings/hardware-config", json=self.PAYLOAD)

        saved = hardware.save_config.await_args.args[0]
        assert saved["audio"]["id"] == "hifiberry_amp2"
        assert saved["audio"]["overlay"], "the overlay was not resolved"
        assert saved["screen"]["resolution"] is not None

    def test_a_dac_card_is_stored_as_not_managing_its_own_volume(self, client, hardware):
        """This flag is the whole DAC mode: False means an external amp owns the
        level and CamillaDSP stays pinned at 0 dB. Auto-detected from the card
        category, because a unit that took the managed arm by mistake starts at
        0 dB — that is, at full output."""
        from backend.hardware.registry import AUDIO_CARDS, is_dac_card

        dac_id = next(cid for cid in AUDIO_CARDS if is_dac_card(cid))
        payload = {**self.PAYLOAD, "audio": {"id": dac_id}}

        client.put("/api/settings/hardware-config", json=payload)

        assert hardware.save_config.await_args.args[0]["audio"]["volume_control"] is False

    def test_an_explicit_override_wins_over_the_auto_detection(self, client, hardware):
        """A DAC board wired to powered speakers rather than an amp; the operator
        says so in the wizard and the registry's category must not overrule it."""
        from backend.hardware.registry import AUDIO_CARDS, is_dac_card

        dac_id = next(cid for cid in AUDIO_CARDS if is_dac_card(cid))
        payload = {**self.PAYLOAD, "audio": {"id": dac_id, "volume_control": True}}

        client.put("/api/settings/hardware-config", json=payload)

        assert hardware.save_config.await_args.args[0]["audio"]["volume_control"] is True

    def test_the_reboot_is_deferred_so_the_response_can_reach_the_browser(
        self, client, hardware, monkeypatch
    ):
        """Rebooting inline drops the connection mid-response, and the wizard
        reports a failure for the one action that worked."""
        slept = []

        async def _sleep(delay, *a, **k):
            slept.append(delay)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        response = client.put("/api/settings/hardware-config", json=self.PAYLOAD)

        assert response.status_code == 200
        # TestClient runs BackgroundTasks after the response, so by here it ran.
        assert slept == [1]
        hardware.apply_and_reboot.assert_awaited_once()

    def test_an_apply_that_fails_is_logged_rather_than_lost(
        self, client, hardware, monkeypatch, caplog
    ):
        """It runs after the response, so there is no status code left to carry
        the failure — the log line is the only place a wizard that answered
        "rebooting" over a box that did not reboot can be diagnosed from."""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        hardware.apply_and_reboot = AsyncMock(side_effect=RuntimeError("config.txt is read-only"))

        with caplog.at_level(logging.ERROR, logger="backend.api.settings"):
            response = client.put("/api/settings/hardware-config", json=self.PAYLOAD)

        assert response.status_code == 200
        assert any("Hardware apply/reboot failed" in r.message for r in caplog.records)

    def test_an_unknown_card_is_refused_before_anything_is_written(
        self, client, hardware
    ):
        """The id indexes the registry directly; an unknown one must not reach
        `save_config` with a half-built record."""
        payload = {**self.PAYLOAD, "audio": {"id": "not-a-card"}}

        response = client.put("/api/settings/hardware-config", json=payload)

        assert response.status_code >= 400
        hardware.save_config.assert_not_called()
        hardware.apply_and_reboot.assert_not_called()


class TestTheHardwareRead:
    """`GET /hardware-info`, read by the settings screen on every open."""

    def test_it_reports_the_screen_the_service_knows_about(self, client, hardware):
        hardware.get_screen_info = Mock(
            return_value={"type": "waveshare-7", "resolution": {"width": 1024, "height": 600}}
        )

        body = client.get("/api/settings/hardware-info").json()

        assert body["status"] == "success"
        assert body["hardware"]["screen_type"] == "waveshare-7"
        assert body["hardware"]["screen_resolution"]["width"] == 1024

    def test_a_failure_answers_200_with_an_error_and_a_usable_shape(
        self, client, hardware, caplog
    ):
        """`/status`-style resilience: the settings screen reads
        `hardware.screen_type` unconditionally, so a 500 here is a blank page
        rather than a screen that says the hardware is unknown."""
        hardware.get_screen_info = Mock(side_effect=RuntimeError("no i2c bus"))

        with caplog.at_level(logging.ERROR, logger="backend.api.settings"):
            response = client.get("/api/settings/hardware-info")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["hardware"]["screen_type"] == "none"
        assert "success" not in body
