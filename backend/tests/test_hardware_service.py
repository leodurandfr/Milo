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
import asyncio
import json
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.hardware.registry import (
    AUDIO_CARDS,
    DEFAULT_IR_REMOTE,
    DEFAULT_ROTARY_PINS,
    SCREENS,
)
from backend.hardware.service import HardwareService
from backend.shared.persistence import SchemaVersionMismatch


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


class TestTheScreenResolution:
    """`get_screen_resolution` was at 0 % — the whole body.

    It reaches `GET /api/settings/hardware-info`, which is what the kiosk reads
    to size itself. A silent None there is a page laid out for a screen that
    is not the one plugged in.
    """

    def test_the_stored_string_is_split_into_two_integers(self, service):
        _write(service, {"screen": {"type": "waveshare_8_dsi", "resolution": "1280x800"}})
        assert service.get_screen_resolution() == {"width": 1280, "height": 800}

    def test_a_screen_with_no_resolution_answers_none_in_silence(self, service, caplog):
        """A screenless unit is the normal case, not drift. Falling through to
        the parser answers None too — but warns on every read of
        `/hardware-info`, which is what makes the warning worth reading."""
        _write(service, {"screen": {"type": "none"}})

        with caplog.at_level(logging.WARNING):
            assert service.get_screen_resolution() is None

        assert caplog.text == ""

    @pytest.mark.parametrize("stored", ["1280", "1280x800x60", "wide", {"w": 1}, 1280])
    def test_a_resolution_it_cannot_parse_answers_none_and_says_so(
        self, service, stored, caplog
    ):
        """The value comes from `registry.SCREENS`, so a malformed one means the
        registry and this parser have drifted — the warning is the only place
        that would ever say which."""
        _write(service, {"screen": {"type": "waveshare_8_dsi", "resolution": stored}})

        with caplog.at_level(logging.WARNING):
            assert service.get_screen_resolution() is None

        assert "Invalid resolution format" in caplog.text

    def test_every_screen_in_the_registry_parses(self, service):
        """The two sides are written apart: `SCREENS` declares the strings and
        this splits them. A registry entry this cannot read installs a screen
        the kiosk cannot size."""
        for screen_id, screen in SCREENS.items():
            _write(service, {"screen": {"type": screen_id, **screen}})
            parsed = service.get_screen_resolution()
            if screen.get("resolution") is None:
                assert parsed is None, screen_id
            else:
                assert parsed is not None, f"{screen_id}: {screen['resolution']!r}"
                assert parsed["width"] > 0 and parsed["height"] > 0

    def test_the_combined_info_carries_both_halves(self, service):
        _write(service, {"screen": {"type": "waveshare_7_usb", "resolution": "1024x600"}})
        assert service.get_screen_info() == {
            "type": "waveshare_7_usb",
            "resolution": {"width": 1024, "height": 600},
        }


class TestTheIrRemoteKeys:
    """`get_ir_enabled` / `get_ir_gpio_pin`, both at 0 %.

    `milo-apply-hardware` strips the gpio-ir block out of config.txt unless
    `ir_remote.enabled` is true here, so a renamed key does not fail — it
    answers the default and the receiver is configured on the wrong pin, or
    stripped out at the next apply.
    """

    def test_the_switch_comes_from_ir_remote_enabled(self, service):
        _write(service, {"ir_remote": {"enabled": False}})
        assert service.get_ir_enabled() is False

    def test_the_pin_comes_from_ir_remote_gpio_pin(self, service):
        _write(service, {"ir_remote": {"enabled": True, "gpio_pin": 26}})
        assert service.get_ir_gpio_pin() == 26

    def test_an_absent_section_answers_the_registry_default(self, service):
        _write(service, {})
        assert service.get_ir_enabled() == DEFAULT_IR_REMOTE["enabled"]
        assert service.get_ir_gpio_pin() == DEFAULT_IR_REMOTE["gpio_pin"]


class TestTheRotaryPins:
    def test_the_three_pins_come_back_in_clk_dt_sw_order(self, service):
        """`dependencies.py` unpacks the tuple positionally straight into the
        controller's constructor: a swapped pair inverts the knob's direction
        or turns a detent into a button press."""
        _write(service, {"rotary_encoder": {"clk_pin": 5, "dt_pin": 6, "sw_pin": 13}})
        assert service.get_rotary_pins() == (5, 6, 13)

    @pytest.mark.parametrize("missing", ["clk_pin", "dt_pin", "sw_pin"])
    def test_one_absent_pin_falls_back_alone(self, service, missing):
        stored = {"clk_pin": 5, "dt_pin": 6, "sw_pin": 13}
        del stored[missing]
        _write(service, {"rotary_encoder": stored})

        answered = dict(zip(("clk_pin", "dt_pin", "sw_pin"), service.get_rotary_pins()))

        assert answered[missing] == DEFAULT_ROTARY_PINS[missing]
        for key, value in stored.items():
            assert answered[key] == value, "the stored pins must survive"


class TestTheFullConfig:
    """`get_full_config` was at 0 %. It is the body of
    `GET /api/settings/hardware-config` and the read that `api/setup.py` uses
    to preserve the rotary and IR blocks it is not editing — so a section it
    drops is a section the setup wizard erases from hardware.json.
    """

    STORED = {
        "audio": {"id": "hifiberry_amp2", "card_name": "sndrpihifiberry"},
        "screen": {"type": "waveshare_8_dsi", "resolution": "1280x800"},
        "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
        "ir_remote": {"enabled": False, "gpio_pin": 26},
    }

    def test_every_section_the_wizard_re_saves_is_present_and_whole(self, service):
        _write(service, self.STORED)

        assert service.get_full_config() == {
            "audio": self.STORED["audio"],
            "screen": self.STORED["screen"],
            "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
            "ir_remote": {"enabled": False, "gpio_pin": 26},
        }

    def test_an_empty_file_still_answers_a_complete_shape(self, service):
        """`setup.py` reads `current["rotary_encoder"]` and `current["ir_remote"]`
        by key with no default: a missing one is a KeyError inside the wizard's
        own except arm, which rolls the whole setup back."""
        _write(service, {})

        config = service.get_full_config()

        assert set(config) == {"audio", "screen", "rotary_encoder", "ir_remote"}
        assert config["screen"] == {"type": "none", "resolution": None}
        assert config["rotary_encoder"]["clk_pin"] == DEFAULT_ROTARY_PINS["clk_pin"]
        assert config["ir_remote"]["gpio_pin"] == DEFAULT_IR_REMOTE["gpio_pin"]


class TestWritingBackToHardwareJson:
    async def test_a_volume_control_override_survives_a_reload(self, service):
        """`volume/service.py` calls this when the operator flips "this card has
        its own volume". It is durable state: CamillaDSP is the only
        attenuation stage, so getting it wrong is either double attenuation or
        none at all."""
        _write(service, {"audio": {"id": "hifiberry_amp2"}})

        await service.set_volume_control(False)

        reloaded = HardwareService()
        reloaded.hardware_file = service.hardware_file
        assert reloaded.get_volume_control() is False
        assert reloaded.get_audio_id() == "hifiberry_amp2", "the rest is untouched"

    async def test_a_card_with_no_audio_section_yet_gets_one(self, service):
        _write(service, {"screen": {"type": "none"}})

        await service.set_volume_control(True)

        reloaded = HardwareService()
        reloaded.hardware_file = service.hardware_file
        assert reloaded.get_volume_control() is True

    async def test_the_file_is_stamped_with_the_schema_version(self, service):
        """The fail-loud protocol keys off it; an unstamped file makes the next
        boot read a version-0 hardware.json and SystemExit(1)."""
        _write(service, {"audio": {"id": "hifiberry_amp2"}})

        await service.save_config({"audio": {"id": "hifiberry_amp2"}})

        stored = json.loads(service.hardware_file.read_text())
        assert stored["schema_version"] == HardwareService.SCHEMA_VERSION

    async def test_a_save_drops_the_cache_so_the_next_read_is_the_file(self, service):
        _write(service, {"audio": {"id": "hifiberry_amp2"}})
        assert service.get_audio_id() == "hifiberry_amp2"

        await service.save_config({"audio": {"id": "hifiberry_dacplus"}})

        assert service.get_audio_id() == "hifiberry_dacplus"


class TestTheBootLoad:
    async def test_initialize_pre_loads_the_file(self, service):
        service.hardware_file.write_text(json.dumps({
            "schema_version": HardwareService.SCHEMA_VERSION,
            "audio": {"id": "hifiberry_amp2"},
        }))
        service._cache = None

        await service.initialize()

        assert service._cache["audio"]["id"] == "hifiberry_amp2"

    async def test_a_version_drift_is_raised_rather_than_read_around(self, service):
        """The banner + SystemExit(1) in dependencies.py hangs off this raise.
        Swallowing it would boot a unit against a hardware.json whose shape it
        does not know — a screen or card silently reconfigured."""
        service.hardware_file.write_text(json.dumps({
            "schema_version": HardwareService.SCHEMA_VERSION + 1,
            "audio": {"id": "hifiberry_amp2"},
        }))

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    async def test_a_fresh_unit_with_no_file_loads_empty(self, service):
        await service.initialize()
        assert service._cache == {}

    def test_an_unreadable_file_is_read_as_empty_by_the_sync_path(self, service, caplog):
        """The sync getters run before `initialize`, so they are lenient by
        design — the strict read is the async one, which fails loud."""
        service.hardware_file.write_text("{ this is not json")
        service._cache = None

        with caplog.at_level(logging.WARNING):
            assert service.get_screen_type() == "none"

        assert "hardware sync read fallback" in caplog.text


class TestApplyAndReboot:
    """The privileged apply, at 0 %. `sudo /usr/local/bin/milo-apply-hardware`
    rewrites config.txt and reboots; both the setup wizard and
    `PUT /hardware-config` fire it after their response has flushed.

    The subprocess is replaced rather than watched: on this host the real one
    would rewrite the appliance's config.txt and reboot it.
    """

    @pytest.fixture
    def spawn(self, monkeypatch):
        calls = []
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0

        async def fake(*argv, **kwargs):
            calls.append(argv)
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
        return types.SimpleNamespace(calls=calls, proc=proc)

    async def test_the_pinned_sudoers_helper_is_what_is_run(self, service, spawn):
        """The absolute path is the sudoers rule; a relative one or a different
        name is a permission denial, not a different program."""
        await service.apply_and_reboot()

        assert spawn.calls == [("sudo", "/usr/local/bin/milo-apply-hardware")]

    async def test_a_helper_that_fails_is_raised_with_what_it_printed(
        self, service, spawn
    ):
        """Both callers log this and roll the wizard back on it. Swallowing it
        would answer "rebooting" to a unit that will come back unchanged."""
        spawn.proc.returncode = 2
        spawn.proc.communicate = AsyncMock(return_value=(b"", b"  no such overlay\n"))

        with pytest.raises(RuntimeError, match="no such overlay"):
            await service.apply_and_reboot()

    async def test_the_reboot_killing_the_helper_is_not_an_error(self, service, spawn):
        """The script reboots the machine, so being killed by a signal is the
        success path — a negative return code must not read as a failure and
        roll back a setup that already applied."""
        spawn.proc.returncode = -15

        await service.apply_and_reboot()

    async def test_a_failure_with_nothing_on_stderr_still_says_something(
        self, service, spawn
    ):
        spawn.proc.returncode = 1
        spawn.proc.communicate = AsyncMock(return_value=(b"", b""))

        with pytest.raises(RuntimeError, match="unknown error"):
            await service.apply_and_reboot()
