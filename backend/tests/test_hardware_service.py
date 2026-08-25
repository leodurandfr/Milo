# backend/tests/test_hardware_service.py
"""The bootstrap read accessors of `HardwareService`.

Four of the 29 greens of the Lot A eviscration sweep. They are the sync path
`_ensure_cache` serves before `initialize()` runs, and what they answer decides
the wiring at boot: which screen overlay is applied, which card is configured,
whether the rotary encoder is started at all, and whether CamillaDSP owns
attenuation.

What was unguarded is the key names. Each getter reads `hardware.json` through
a chain of `.get(...)` with a default, so a renamed key does not fail -- it
answers the default, and the setting silently stops existing. These tests pin
the file's shape by writing one and reading it back.
"""
import json

import pytest

from backend.hardware.registry import AUDIO_CARDS
from backend.hardware.service import HardwareService


@pytest.fixture
def service(tmp_path):
    """A service pointed at a scratch hardware.json, never the appliance's."""
    svc = HardwareService()
    svc.hardware_file = tmp_path / "hardware.json"
    return svc


def _write(service, config):
    service.hardware_file.write_text(json.dumps(config))
    service._cache = None
    return service


class TestTheKeysHardwareJsonIsReadThrough:
    """A renamed key answers the default instead of failing — this pins them."""

    def test_the_screen_type_comes_from_screen_type(self, service):
        _write(service, {"screen": {"type": "waveshare_8_dsi"}})
        assert service.get_screen_type() == "waveshare_8_dsi"

    def test_the_audio_id_comes_from_audio_id(self, service):
        _write(service, {"audio": {"id": "hifiberry_amp2"}})
        assert service.get_audio_id() == "hifiberry_amp2"

    def test_the_rotary_switch_comes_from_rotary_encoder_enabled(self, service):
        _write(service, {"rotary_encoder": {"enabled": False}})
        assert service.get_rotary_enabled() is False


class TestWhatAnAbsentSectionAnswers:
    """The defaults are what a fresh unit boots on, before anything is configured."""

    def test_no_screen_section_means_no_screen(self, service):
        _write(service, {})
        assert service.get_screen_type() == "none"

    def test_no_audio_section_means_no_card_chosen(self, service):
        _write(service, {})
        assert service.get_audio_id() is None

    def test_no_rotary_section_leaves_the_encoder_on(self, service):
        _write(service, {})
        assert service.get_rotary_enabled() is True

    def test_a_missing_file_is_read_as_empty_rather_than_raising(self, service):
        assert service.get_screen_type() == "none"
        assert service.get_audio_id() is None


class TestVolumeControlDerivation:
    """`get_volume_control` — who attenuates, the card or CamillaDSP.

    False means an external amplifier owns the volume, so Milo must not also
    attenuate. The value is derived from the card category unless the operator
    set it explicitly, and CLAUDE.md pins CamillaDSP as the only attenuation
    stage — an inverted answer here is a second one.
    """

    def test_an_explicit_choice_wins_over_the_card(self, service):
        dac = next(k for k, v in AUDIO_CARDS.items() if v.get("category") == "dac")
        _write(service, {"audio": {"id": dac, "volume_control": True}})
        assert service.get_volume_control() is True

    def test_an_explicit_false_is_honoured_and_not_read_as_absent(self, service):
        amp = next(k for k, v in AUDIO_CARDS.items() if v.get("category") == "amplifier")
        _write(service, {"audio": {"id": amp, "volume_control": False}})
        assert service.get_volume_control() is False

    def test_a_dac_with_no_explicit_choice_hands_volume_away(self, service):
        dac = next(k for k, v in AUDIO_CARDS.items() if v.get("category") == "dac")
        _write(service, {"audio": {"id": dac}})
        assert service.get_volume_control() is False

    def test_an_amplifier_with_no_explicit_choice_keeps_volume(self, service):
        amp = next(k for k, v in AUDIO_CARDS.items() if v.get("category") == "amplifier")
        _write(service, {"audio": {"id": amp}})
        assert service.get_volume_control() is True

    @pytest.mark.parametrize("audio", [{}, {"id": "none"}])
    def test_no_card_configured_keeps_volume_rather_than_losing_it(self, service, audio):
        _write(service, {"audio": audio})
        assert service.get_volume_control() is True
