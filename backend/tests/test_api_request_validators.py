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
    AudioStopRequest,
    FanConfigRequest,
    HardwareAudioRequest,
    HardwareScreenRequest,
    ScreenTimeoutRequest,
)
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
