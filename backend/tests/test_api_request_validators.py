# backend/tests/test_api_request_validators.py
"""Rejection paths of the request validators in `backend/api/models.py`.

Five validators came out of the Lot A eviscration sweep green: replacing each
body with a neutral return left the whole suite passing. They are guards that
raise, so a neutral return means the guard is gone -- an unknown audio card id,
a fan curve that is not monotonic, or a 1-second screen timeout would all be
accepted and written to the hardware config. Nothing else validates them: the
routes hand the parsed model straight to `SettingsService`.

Each test here asserts the raise, not the message, and the pass-through of a
value that must remain acceptable -- a validator that rejected everything would
be just as broken and is what the second half of each test catches.
"""
import pytest
from pydantic import ValidationError

from backend.api.models import (
    AudioControlRequest,
    AudioStopRequest,
    BtRemoteConfigRequest,
    ConfigureClientAudioRequest,
    ConfigurePendingClientRequest,
    FanConfigRequest,
    HardwareAudioRequest,
    HardwareRotaryEncoderRequest,
    HardwareScreenRequest,
    ScreenTimeoutRequest,
    VolumeLimitsRequest,
    ZoneCreate,
    ZoneUpdate,
)
from backend.config.constants import BT_REMOTE_ACTIONS
from backend.hardware.registry import AUDIO_CARDS, SCREENS


def _fan(curve):
    """A complete, otherwise-valid fan config -- only the curve varies."""
    return dict(enabled=True, mode="auto", manual_percent=50, target_temp_c=60, curve=curve)


class TestFanCurveIsStrictlyIncreasing:
    """`FanConfigRequest.validate_curve_increasing` -- the auto-mode fan curve.

    The curve is interpolated by the fan controller; a non-monotonic or
    duplicated temperature makes the lookup pick an arbitrary neighbour, so the
    fan speed for a given temperature stops being defined.
    """

    def test_a_curve_out_of_order_is_refused(self):
        with pytest.raises(ValidationError):
            FanConfigRequest(**_fan([{"temp_c": 70, "percent": 90},
                                     {"temp_c": 50, "percent": 30}]))

    def test_a_curve_repeating_a_temperature_is_refused(self):
        with pytest.raises(ValidationError):
            FanConfigRequest(**_fan([{"temp_c": 50, "percent": 30},
                                     {"temp_c": 50, "percent": 90}]))

    def test_an_increasing_curve_survives_and_keeps_its_points(self):
        req = FanConfigRequest(**_fan([{"temp_c": 50, "percent": 30},
                                       {"temp_c": 70, "percent": 90}]))
        assert [p.temp_c for p in req.curve] == [50, 70]


class TestAutoStopDelayFloor:
    """`AudioStopRequest.validate_delay` -- 0 disables, anything else is >= 1 s.

    A delay under a second makes the inactivity monitor stop the source almost
    as soon as it goes idle, which reads as playback dying on its own.
    """

    def test_a_delay_between_zero_and_one_is_refused(self):
        with pytest.raises(ValidationError):
            AudioStopRequest(auto_stop_delay=0.5)

    def test_zero_is_accepted_because_it_means_disabled(self):
        assert AudioStopRequest(auto_stop_delay=0).auto_stop_delay == 0

    def test_a_delay_above_the_floor_passes_through_unchanged(self):
        assert AudioStopRequest(auto_stop_delay=42.5).auto_stop_delay == 42.5


class TestScreenTimeoutFloor:
    """`ScreenTimeoutRequest.validate_timeout` -- 0 disables, anything else >= 3 s.

    Below three seconds the kiosk screen blanks while the operator is still
    touching it.
    """

    def test_a_timeout_between_zero_and_three_is_refused(self):
        with pytest.raises(ValidationError):
            ScreenTimeoutRequest(screen_timeout_enabled=True, screen_timeout_seconds=2)

    def test_zero_is_accepted_because_it_means_disabled(self):
        assert ScreenTimeoutRequest(screen_timeout_enabled=True, screen_timeout_seconds=0).screen_timeout_seconds == 0

    def test_a_timeout_above_the_floor_passes_through_unchanged(self):
        assert ScreenTimeoutRequest(screen_timeout_enabled=True, screen_timeout_seconds=90).screen_timeout_seconds == 90


class TestHardwareIdsMustExistInTheRegistry:
    """`validate_audio_id` / `validate_screen_type` -- the two registry gates.

    Both ids are written to the hardware config and then applied by
    `milo-apply-hardware`, which overlays a device tree for them. An id the
    registry does not know reaches a root helper that cannot resolve it.
    """

    def test_an_unknown_audio_card_is_refused(self):
        with pytest.raises(ValidationError):
            HardwareAudioRequest(id="no-such-card")

    def test_every_card_the_registry_declares_is_accepted(self):
        for card_id in AUDIO_CARDS:
            assert HardwareAudioRequest(id=card_id).id == card_id

    def test_an_unknown_screen_type_is_refused(self):
        with pytest.raises(ValidationError):
            HardwareScreenRequest(type="no-such-screen")

    def test_every_screen_the_registry_declares_is_accepted(self):
        for screen_id in SCREENS:
            assert HardwareScreenRequest(type=screen_id).type == screen_id


# =============================================================================
# The eight guards whose raise arm had never run (measured 2026-08-25)
# =============================================================================
# The five above came out of the eviscration sweep. These came out of the line
# coverage of `backend/api/` — same class of hole, found the other way: every
# `raise ValueError` below sat at 0 %, so each guard was a gate nobody had ever
# proved closes. Two of them carry the tranche-11 finding in their own
# docstring, which makes the gap sharper: the fault is named in the file and the
# guard against it had never fired.


class TestBtRemoteFilterIsNeverBlank:
    """`BtRemoteConfigRequest.validate_device_name_filter`.

    `_get_matching_devices` skips its name test when the filter is falsy, so a
    blank one selects *every* bonded BT device and `forget_remote()` becomes
    `bluetoothctl remove` over the lot — the A2DP phone included. Measured on a
    unit in tranche 11; this is the guard that was added for it.
    """

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_a_blank_filter_is_refused(self, blank):
        with pytest.raises(ValidationError, match="must not be blank"):
            BtRemoteConfigRequest(device_name_filter=blank)

    def test_a_padded_name_is_kept_trimmed(self):
        """Surrounding spaces are the quiet half: they match no device name at
        all, and the remote simply stops working with nothing to see.
        """
        assert BtRemoteConfigRequest(device_name_filter="  Bluetooth Remote ").device_name_filter \
            == "Bluetooth Remote"

    def test_an_omitted_filter_stays_omitted(self):
        """The request is a partial update — absent means "leave it alone", and
        refusing it here would make every other field of the form unsendable.
        """
        assert BtRemoteConfigRequest(enabled=True).device_name_filter is None


class TestBtRemoteKeyMapIsChecked:
    """`BtRemoteConfigRequest.validate_key_map`.

    Both halves used to be accepted unchecked and failed downstream at debug
    level: a non-numeric keycode broke device matching for every remote, and an
    unknown action dispatched to nothing. Either way the remote stopped working
    and nothing said so.
    """

    def test_a_keycode_that_is_not_a_number_is_refused(self):
        with pytest.raises(ValidationError, match="keycode must be a positive integer"):
            BtRemoteConfigRequest(key_map={"KEY_PLAY": next(iter(BT_REMOTE_ACTIONS))})

    def test_an_action_the_dispatcher_does_not_know_is_refused(self):
        with pytest.raises(ValidationError, match="unknown action"):
            BtRemoteConfigRequest(key_map={"164": "explode"})

    def test_every_action_the_dispatcher_declares_is_accepted(self):
        """Read out of `BT_REMOTE_ACTIONS`, so a guard that rejected everything
        — the other way this validator can break — is red here.
        """
        key_map = {str(200 + i): action for i, action in enumerate(sorted(BT_REMOTE_ACTIONS))}

        assert BtRemoteConfigRequest(key_map=key_map).key_map == key_map


class TestZoneNamesAreNotBlank:
    """`ZoneCreate.validate_name` and `ZoneUpdate.validate_name`.

    A zone with a blank name is a row in the multiroom list with nothing to
    click and nothing to tell it from another blank one, and the name is what
    the EQ tab strip labels its per-zone tab with.
    """

    @pytest.mark.parametrize("blank", ["   ", "\t", "\n"])
    def test_a_blank_name_cannot_create_a_zone(self, blank):
        with pytest.raises(ValidationError, match="cannot be empty"):
            ZoneCreate(name=blank, client_ids=["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"])

    def test_a_blank_name_cannot_rename_a_zone_either(self):
        """The update model is the one a user reaches by clearing the field."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            ZoneUpdate(name="   ")

    def test_a_padded_name_is_stored_trimmed_by_both(self):
        assert ZoneCreate(
            name="  Salon  ", client_ids=["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
        ).name == "Salon"
        assert ZoneUpdate(name="  Salon  ").name == "Salon"

    def test_an_omitted_name_leaves_the_zone_named_as_it_was(self):
        assert ZoneUpdate(name=None).name is None


class TestRotaryPinsAreDistinct:
    """`HardwareRotaryEncoderRequest.validate_unique_pins`.

    The three pins go into config.txt as one `rotary-encoder` overlay. Two of
    them on the same GPIO is an encoder that reports a direction at random, and
    the only way back is the wizard — after a reboot.
    """

    def test_two_pins_on_the_same_gpio_are_refused(self):
        with pytest.raises(ValidationError, match="must be different"):
            HardwareRotaryEncoderRequest(enabled=True, clk_pin=22, dt_pin=22, sw_pin=23)

    def test_a_disabled_encoder_may_keep_whatever_pins_it_had(self):
        """Nothing is written for a disabled encoder, and refusing here would
        make the disable itself unsendable from a form holding stale pins.
        """
        assert HardwareRotaryEncoderRequest(
            enabled=False, clk_pin=22, dt_pin=22, sw_pin=22
        ).enabled is False

    def test_three_distinct_pins_pass(self):
        assert HardwareRotaryEncoderRequest(
            enabled=True, clk_pin=5, dt_pin=6, sw_pin=13
        ).clk_pin == 5


class TestConfigurableAudioIdExcludesNone:
    """`_validate_configurable_audio_id`, shared by the two satellite-facing
    audio requests.

    Both end in `_send_audio_config_and_reboot`: the satellite writes the card
    and reboots. `none` has no overlay, so it reboots a speaker into silence,
    and the wizard offers no way back that does not start with unplugging it.
    """

    @pytest.mark.parametrize("model", [ConfigureClientAudioRequest, ConfigurePendingClientRequest])
    def test_the_absent_card_is_refused(self, model):
        with pytest.raises(ValidationError, match="Invalid audio_id"):
            model(audio_id="none", **({"name": "Bureau"} if model is ConfigurePendingClientRequest else {}))

    @pytest.mark.parametrize("model", [ConfigureClientAudioRequest, ConfigurePendingClientRequest])
    def test_a_card_outside_the_registry_is_refused(self, model):
        with pytest.raises(ValidationError, match="Invalid audio_id"):
            model(audio_id="allo-boss", **({"name": "Bureau"} if model is ConfigurePendingClientRequest else {}))

    def test_every_real_card_the_registry_declares_is_accepted(self):
        """Read out of the registry: a guard that refused everything would ship
        an appliance where no card can be chosen at all.
        """
        for audio_id in (k for k in AUDIO_CARDS if k != "none"):
            assert ConfigureClientAudioRequest(audio_id=audio_id).audio_id == audio_id


class TestVolumeLimitsKeepUsableRange:
    """`VolumeLimitsRequest.validate_range`.

    The two bounds map the 0-100 % the UI shows onto CamillaDSP's dB gain. A
    range narrower than 6 dB makes most of the slider's travel inaudible, and an
    inverted one makes turning it up turn the volume down.
    """

    def test_a_range_under_six_db_is_refused(self):
        with pytest.raises(ValidationError, match="at least 6 dB"):
            VolumeLimitsRequest(min_db=-20.0, max_db=-15.0)

    def test_an_inverted_range_is_refused_by_name(self):
        """Ordering, not merely refusal: an inverted range is also narrower than
        6 dB, so the width check catches it first and answers with the one
        message that does not say what is wrong. That is how the inverted-range
        branch sat unreachable — found here, 2026-08-25.
        """
        with pytest.raises(ValidationError, match="max_db must be greater than min_db"):
            VolumeLimitsRequest(min_db=-5.0, max_db=-30.0)

    def test_exactly_six_db_apart_is_accepted(self):
        """The floor is inclusive; refusing it would move the boundary by one
        step of the slider with nothing saying so.
        """
        assert VolumeLimitsRequest(min_db=-30.0, max_db=-24.0).max_db == -24.0


class TestAudioCommandNamesAreSafe:
    """`AudioControlRequest.validate_command`.

    The command is dispatched by name against each source's `COMMANDS` table.
    Restricting it to a word is what keeps the untrusted half of
    `POST /api/audio/control/{source}` from carrying anything but a name.
    """

    @pytest.mark.parametrize("command", ["play track", "seek;stop", "../../etc/passwd"])
    def test_a_command_that_is_not_a_word_is_refused(self, command):
        with pytest.raises(ValidationError, match="alphanumeric"):
            AudioControlRequest(command=command)

    @pytest.mark.parametrize("command", ["play_track", "resume-playback", "next"])
    def test_the_shapes_the_sources_actually_declare_are_accepted(self, command):
        assert AudioControlRequest(command=command).command == command
