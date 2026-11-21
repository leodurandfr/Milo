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
    DEFAULT_POWER_BUTTON,
    DEFAULT_ROTARY_PINS,
    OUTPUT_CARD,
    SCREENS,
    alsa_card_ids,
    missing_card_label,
)
from backend.hardware.service import HardwareService


@pytest.fixture
def service(tmp_path):
    """A service pointed at a scratch hardware.json, never the appliance's."""
    svc = HardwareService()
    svc.hardware_file = tmp_path / "hardware.json"
    return svc


def _write(service, config):
    service.hardware_file.write_text(
        json.dumps({"schema_version": HardwareService.SCHEMA_VERSION, **config})
    )
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


class TestVolumeControlIsReadAsStored:
    """`get_volume_control` — who attenuates, the card or CamillaDSP.

    False means an external amplifier owns the volume, so Milo must not also
    attenuate. Every write of the audio block stores the choice
    (`registry.audio_config`), so the read derives nothing: a second derivation
    here is how a DAC's stored "Milō manages the volume" could be read back as
    the category's False, and CLAUDE.md pins CamillaDSP as the only attenuation
    stage.
    """

    @pytest.mark.parametrize("stored", [True, False])
    def test_the_stored_choice_is_the_answer_whatever_the_card(self, service, stored):
        for audio_id in AUDIO_CARDS:
            _write(service, {"audio": {"id": audio_id, "volume_control": stored}})
            assert service.get_volume_control() is stored, audio_id

    def test_a_unit_the_wizard_has_not_configured_keeps_volume(self, service):
        _write(service, {})
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
            "color_correction": None,
            "pixels_per_mm": None,
        }

    def test_only_a_measured_screen_carries_a_correction(self, service):
        """The kiosk's color layer reads the correction from here alone: the
        measured panel gets gains, an unmeasured one none — and so does a type
        the registry does not know, which must not raise on the lookup."""
        _write(service, {"screen": {"type": "waveshare_8_dsi"}})
        assert len(service.get_screen_info()["color_correction"]) == 3
        for screen_type in ("waveshare_7_usb", "none", "retired_panel"):
            _write(service, {"screen": {"type": screen_type}})
            assert service.get_screen_info()["color_correction"] is None, screen_type

    def test_only_a_panel_of_known_width_carries_a_pitch(self, service):
        """The kiosk turns its bezel inset from millimeters into pixels with this
        pitch, and takes no inset without one: the panel whose lit width the
        registry declares gets it from its own resolution, an unknown panel and a
        panel with no resolution on record get none, and the lookup never raises."""
        pitches = []
        for resolution in ("1280x800", "2560x1600"):
            _write(service, {"screen": {"type": "waveshare_8_dsi", "resolution": resolution}})
            pitches.append(service.get_screen_info()["pixels_per_mm"])
        assert pitches[0] > 0
        # The pixels on record, over the panel's width: twice the pixels on the
        # same glass, twice the pitch.
        assert pitches[1] == pytest.approx(2 * pitches[0])
        for screen in ({"type": "waveshare_8_dsi"},
                       {"type": "waveshare_7_usb", "resolution": "1024x600"},
                       {"type": "retired_panel", "resolution": "1280x800"}):
            _write(service, {"screen": screen})
            assert service.get_screen_info()["pixels_per_mm"] is None, screen

    def test_every_registry_correction_only_attenuates_and_keeps_one_channel_whole(self):
        """The kiosk's color matrix can only darken: a gain above 1 is clipped at
        white in silence, and a correction whose largest gain is under 1 dims the
        panel for nothing — the white it aims at is reached with one channel at 1."""
        for screen_id, screen in SCREENS.items():
            gains = screen.get("color_correction")
            if gains is None:
                continue
            assert len(gains) == 3, screen_id
            assert all(0 < g <= 1 for g in gains), screen_id
            assert max(gains) == 1, screen_id


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
    to preserve the rotary, IR and power-button blocks it is not editing — so a section it
    drops is a section the setup wizard erases from hardware.json.
    """

    STORED = {
        "audio": {"id": "hifiberry_amp2", "overlay": "hifiberry-dacplus-std", "volume_control": True},
        "screen": {"type": "waveshare_8_dsi", "resolution": "1280x800"},
        "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
        "ir_remote": {"enabled": False, "gpio_pin": 26},
        "power_button": {"enabled": True, "led_gpio_pin": 5},
    }

    def test_every_section_the_wizard_re_saves_is_present_and_whole(self, service):
        _write(service, self.STORED)

        assert service.get_full_config() == {
            "audio": self.STORED["audio"],
            "screen": self.STORED["screen"],
            "rotary_encoder": {"enabled": False, "clk_pin": 5, "dt_pin": 6, "sw_pin": 13},
            "ir_remote": {"enabled": False, "gpio_pin": 26},
            "power_button": {"enabled": True, "led_gpio_pin": 5},
        }

    def test_an_empty_file_still_answers_a_complete_shape(self, service):
        """`setup.py` reads `current["rotary_encoder"]` and `current["ir_remote"]`
        by key with no default: a missing one is a KeyError inside the wizard's
        own except arm, which rolls the whole setup back."""
        _write(service, {})

        config = service.get_full_config()

        assert set(config) == {"audio", "screen", "rotary_encoder", "ir_remote", "power_button"}
        assert config["screen"] == {"type": "none", "resolution": None}
        assert config["rotary_encoder"]["clk_pin"] == DEFAULT_ROTARY_PINS["clk_pin"]
        assert config["ir_remote"]["gpio_pin"] == DEFAULT_IR_REMOTE["gpio_pin"]
        assert config["power_button"] == DEFAULT_POWER_BUTTON


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
        """The loader keys off it; an unstamped file is set aside at the next
        boot, and the card, the screen and the encoder go back to defaults."""
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

    async def test_a_fresh_unit_with_no_file_loads_empty(self, service):
        await service.initialize()
        assert service._cache == {}

    def test_the_sync_path_sets_aside_what_initialize_would(self, service):
        """The getters run before `initialize`. Read leniently, as they were, a
        drifted file answered them in a shape this code does not know — a
        screen or a card silently reconfigured — while `initialize` refused it."""
        service.hardware_file.write_text(json.dumps({
            "schema_version": HardwareService.SCHEMA_VERSION + 1,
            "screen": {"type": "waveshare_8_dsi"},
        }))
        service._cache = None

        fresh = HardwareService()
        fresh.hardware_file = service.hardware_file.with_name("absent.json")

        assert service.get_screen_type() == fresh.get_screen_type()
        assert len(list(service.hardware_file.parent.glob("hardware.json.*.bad"))) == 1

    def test_a_card_the_sync_path_cannot_read_answers_empty_and_moves_nothing(
        self, service, caplog
    ):
        service.hardware_file.mkdir()  # read → IsADirectoryError, an OSError
        service._cache = None

        fresh = HardwareService()
        fresh.hardware_file = service.hardware_file.with_name("absent.json")

        with caplog.at_level(logging.WARNING):
            assert service.get_screen_type() == fresh.get_screen_type()

        assert "hardware sync read fallback" in caplog.text
        assert service.hardware_file.is_dir()


class TestApply:
    """The privileged apply, at 0 %. `sudo /usr/local/bin/milo-apply-hardware
    --no-reboot` rewrites config.txt and leaves the reboot to the caller. The
    setup wizard and `PUT /hardware-config` both run it within the request
    (`replace_config`), so a refused apply can restore the previous config.

    The subprocess is replaced rather than watched: on this host the real one
    would rewrite the appliance's config.txt.
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

    async def test_the_pinned_sudoers_helper_is_run_without_its_reboot(self, service, spawn):
        """The absolute path is the sudoers rule; a relative one or a different
        name is a permission denial, not a different program. `--no-reboot` is
        the wizard's ordering — apply, then persist `setup_completed`, then
        reboot: a helper that rebooted on its own would put back the window
        where a power cut left a unit that believed it was configured with no
        dtoverlay in config.txt — and the Hardware page's restore, which needs
        the box still up when the apply is refused."""
        await service.apply()

        assert spawn.calls == [("sudo", "-n", "/usr/local/bin/milo-apply-hardware", "--no-reboot")]

    async def test_a_helper_that_fails_is_raised_with_what_it_printed(
        self, service, spawn
    ):
        """Both callers log this and roll the wizard back on it. Swallowing it
        would answer "rebooting" to a unit that will come back unchanged."""
        spawn.proc.returncode = 2
        spawn.proc.communicate = AsyncMock(return_value=(b"", b"  no such overlay\n"))

        with pytest.raises(RuntimeError, match="no such overlay"):
            await service.apply()

    async def test_an_apply_killed_by_a_signal_is_a_failure(
        self, service, spawn
    ):
        """With `--no-reboot` the script never reboots, so a signal can only
        come from outside (a backend stop mid-apply), possibly between removing
        the MILO AUDIO block from config.txt and writing it back. Read as a
        success, the setup wizard persists `setup_completed` and reboots into a
        unit with no audio overlay, silent, and with the wizard gone."""
        spawn.proc.returncode = -15

        with pytest.raises(RuntimeError, match="SIGTERM"):
            await service.apply()

    async def test_a_signal_with_no_name_is_still_a_failure_not_a_crash(
        self, service, spawn
    ):
        """`signal.Signals` names only some real-time signals; an unnamed one
        must still reach the wizard as the RuntimeError it rolls back on, with
        what the helper printed before it died."""
        spawn.proc.returncode = -40
        spawn.proc.communicate = AsyncMock(return_value=(b"", b"Bootloader staged\n"))

        with pytest.raises(RuntimeError, match="signal 40: Bootloader staged"):
            await service.apply()

    async def test_a_failure_with_nothing_on_stderr_still_says_something(
        self, service, spawn
    ):
        spawn.proc.returncode = 1
        spawn.proc.communicate = AsyncMock(return_value=(b"", b""))

        with pytest.raises(RuntimeError, match="unknown error"):
            await service.apply()


# /proc/asound/cards on the server, 2026-11-15: a Pi 5 with an Amp4 Pro seated
# and the two loopback cards Milō loads.
PI5_WITH_HAT = (
    " 0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry_dacplus\n"
    "                      snd_rpi_hifiberry_dacplus\n"
    " 1 [Loopback       ]: Loopback - Loopback\n"
    "                      Loopback 1\n"
    " 2 [LoopbackDLNA   ]: Loopback - Loopback\n"
    "                      Loopback 2\n"
)


class TestReplaceConfig:
    """The transaction the setup wizard and the Hardware page share: write,
    apply, or put the previous config back. `milo-apply-hardware` is stood in
    for at the subprocess, the one thing it touches outside this process."""

    async def test_two_replacements_one_refused_leave_the_file_on_what_config_txt_loads(
        self, service, monkeypatch
    ):
        """The kiosk and a phone each send a card. The first apply is refused
        while the second waits: interleaved, the second reads the first's
        unapplied card as "previous", applies its own, and the first's restore
        then writes the original back over it — hardware.json naming a card
        config.txt does not load, and the next submit reading that as the
        unit's state."""
        _write(service, {"audio": {"id": "hifiberry_amp2"}})
        entered, release = asyncio.Event(), asyncio.Event()
        runs = []

        async def spawn(*_argv, **_kwargs):
            run = len(runs)
            runs.append(run)
            proc = MagicMock()

            async def communicate():
                if run == 0:
                    entered.set()
                    await release.wait()
                    return b"", b"EEPROM write refused"
                return b"", b""

            proc.communicate = communicate
            proc.returncode = 1 if run == 0 else 0
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        refused = asyncio.create_task(service.replace_config({"audio": {"id": "hifiberry_amp100"}}))
        await entered.wait()
        applied = asyncio.create_task(service.replace_config({"audio": {"id": "hifiberry_amp4pro"}}))
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()

        with pytest.raises(RuntimeError, match="EEPROM write refused"):
            await refused
        await applied

        assert len(runs) == 2
        reloaded = HardwareService()
        reloaded.hardware_file = service.hardware_file
        assert reloaded.get_audio_id() == "hifiberry_amp4pro"


class TestTheOutputCardInProcAsound:
    """`alsa_card_ids` + `missing_card_label` — the HAT found by its id, the
    only thing every overlay agrees on (`OUTPUT_CARD`). The server reads its own
    /proc/asound/cards through them, and each satellite's heartbeat carries the
    same ids, judged by the same function."""

    def test_the_ids_are_read_from_their_brackets_padding_dropped(self):
        assert alsa_card_ids(PI5_WITH_HAT) == [OUTPUT_CARD, "Loopback", "LoopbackDLNA"]

    def test_the_hat_among_the_loopbacks_is_not_missing(self):
        assert missing_card_label("hifiberry_amp2", alsa_card_ids(PI5_WITH_HAT)) is None

    def test_the_loopbacks_alone_name_the_configured_card(self):
        without = "".join(PI5_WITH_HAT.splitlines(keepends=True)[2:])
        assert missing_card_label("hifiberry_amp2", alsa_card_ids(without)) == AUDIO_CARDS["hifiberry_amp2"]["label"]

    def test_the_id_is_matched_in_its_brackets_not_in_a_long_name(self):
        """A card's second line is free text: a USB DAC named after the HAT
        is not the HAT."""
        ids = alsa_card_ids(
            f" 0 [Device         ]: USB-Audio - {OUTPUT_CARD} clone\n"
            f"                      {OUTPUT_CARD} clone at usb-xhci-hcd.0-1\n"
        )
        assert missing_card_label("hifiberry_amp2", ids) == AUDIO_CARDS["hifiberry_amp2"]["label"]

    @pytest.mark.parametrize("audio_id, card_ids", [
        ("hifiberry_amp2", None),   # no card list: unreadable, or a satellite one push behind
        ("retired_card", []),       # an id no card answers to
    ])
    def test_nothing_to_judge_is_never_a_complaint(self, audio_id, card_ids):
        assert missing_card_label(audio_id, card_ids) is None


class TestTheConfiguredCardBeingThere:
    """`get_missing_audio_card` — the only thing that notices a card that isn't.

    The wizard offers a static list and nothing checks the pick against the
    board: a wrong choice reboots into a unit with no `sndrpihifiberry`, a
    CamillaDSP that cannot open its playback device, and no sound. Every part of
    that is silent from the UI, which is why the answer has to travel to
    `GET /api/system/status`.
    """

    @pytest.fixture
    def cards_file(self, service, tmp_path, monkeypatch):
        """Stand in for /proc/asound/cards, which on this host lists the real
        board — so the test would otherwise assert against the appliance."""
        path = tmp_path / "cards"
        real_read = type(path).read_text

        def fake_read(self, *args, **kwargs):
            if str(self) == "/proc/asound/cards":
                return real_read(path, *args, **kwargs)
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr("pathlib.Path.read_text", fake_read)
        return path

    def _configure(self, service, audio_id):
        return _write(service, {
            "schema_version": HardwareService.SCHEMA_VERSION,
            "audio": {"id": audio_id},
        })

    def test_a_card_alsa_lists_is_not_reported(self, service, cards_file):
        cards_file.write_text(PI5_WITH_HAT)
        self._configure(service, "hifiberry_amp2")

        assert service.get_missing_audio_card() is None

    def test_a_card_alsa_does_not_list_is_reported_by_its_label(self, service, cards_file):
        """The label, not a boolean: the banner has to name the board, because
        "HiFiBerry Amp2 is configured but not detected" is actionable where "no
        audio card" is only the symptom the user already has."""
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        self._configure(service, "hifiberry_amp2")

        assert service.get_missing_audio_card() == AUDIO_CARDS["hifiberry_amp2"]["label"]

    def test_a_unit_the_wizard_has_not_configured_is_never_a_complaint(self, service, cards_file):
        """Before the wizard there is no card to name, and the wizard is on
        screen saying so."""
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        _write(service, {"schema_version": HardwareService.SCHEMA_VERSION})

        assert service.get_missing_audio_card() is None

    def test_a_server_configured_with_no_card_is_reported(self, service, cards_file):
        """The server plays through a HAT or not at all: `none` is no longer a
        choice the wizard or the Hardware page accepts, so a record naming it is
        a silent unit, which is what the banner exists for."""
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        self._configure(service, "none")

        assert service.get_missing_audio_card() == AUDIO_CARDS["none"]["label"]

    def test_a_missing_card_is_logged_once_not_at_every_reading(self, service, cards_file, caplog):
        """Every browser reads the status at each load, reconnection and return
        to its tab. An error logged at each reading reached every screen as a
        generic "system error" banner beside the notice that names the card,
        and errors.log gained the same line each time. Read three times, the
        answer is the same three times and the error is written once — and once
        more only after the card was seen in between."""
        caplog.set_level(logging.INFO, logger="backend.hardware.service")
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        self._configure(service, "hifiberry_amp2")

        def errors():
            return [r for r in caplog.records
                    if r.name == "backend.hardware.service" and r.levelno >= logging.ERROR]

        label = AUDIO_CARDS["hifiberry_amp2"]["label"]
        assert [service.get_missing_audio_card() for _ in range(3)] == [label] * 3
        assert len(errors()) == 1

        cards_file.write_text(PI5_WITH_HAT)
        assert service.get_missing_audio_card() is None
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        assert service.get_missing_audio_card() == label
        assert len(errors()) == 2

    def test_a_reading_with_no_answer_forgets_the_card_logged(self, service, cards_file, caplog):
        """An unreadable /proc/asound answers None without saying the card came
        back. Kept as the card last logged, the same card missing after it would
        write no error at all — the one reading that is a new fault."""
        caplog.set_level(logging.INFO, logger="backend.hardware.service")
        self._configure(service, "hifiberry_amp2")
        assert service.get_audio_id() == "hifiberry_amp2"

        def errors():
            return [r for r in caplog.records
                    if r.name == "backend.hardware.service" and r.levelno >= logging.ERROR]

        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        assert service.get_missing_audio_card() is not None
        cards_file.unlink()
        assert service.get_missing_audio_card() is None
        cards_file.write_text(" 0 [vc4hdmi        ]: vc4-hdmi\n")
        assert service.get_missing_audio_card() is not None

        assert len(errors()) == 2

    def test_an_unreadable_proc_reports_nothing(self, service, monkeypatch):
        """Fails open: a dev host has no /proc/asound at all, and reporting
        hardware trouble nobody observed is worse than reporting none."""
        self._configure(service, "hifiberry_amp2")
        # Prime the cache before breaking every read, or hardware.json becomes
        # unreadable too and the card reads as unconfigured — which returns None
        # through the wrong branch and makes this test unable to fail.
        assert service.get_audio_id() == "hifiberry_amp2"

        def boom(self, *args, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr("pathlib.Path.read_text", boom)

        assert service.get_missing_audio_card() is None
