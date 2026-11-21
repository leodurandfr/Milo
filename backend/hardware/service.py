# backend/hardware/service.py
"""Hardware management service — read/write hardware.json configuration.

Handles screen type, audio card, rotary encoder GPIO pins, IR remote and power
button configuration. Uses the schema_version protocol — see CLAUDE.md
§"Persistence & the schema-version protocol".
"""
import asyncio
import logging
import signal
from pathlib import Path
from typing import Optional, Dict, Tuple

from backend.config.constants import HARDWARE_FILE
from backend.hardware.registry import (
    DEFAULT_IR_REMOTE,
    DEFAULT_POWER_BUTTON,
    DEFAULT_ROTARY_PINS,
    OUTPUT_CARD,
    SCREENS,
    alsa_card_ids,
    missing_card_label,
)
from backend.shared.persistence import (
    load_versioned_json,
    load_versioned_json_sync,
    save_versioned_json,
)


class HardwareService:
    """Service to read and write hardware configuration (screen, audio, rotary encoder, IR, power button)."""

    SCHEMA_VERSION: int = 2

    def __init__(self):
        self.hardware_file: Path = HARDWARE_FILE
        self.logger = logging.getLogger(__name__)
        self._cache: Optional[Dict] = None
        # Held by `replace_config` from the write to the end of the apply.
        self._replace_lock = asyncio.Lock()
        # The missing card last logged, so the log line marks a change of answer
        # rather than every reading of it (`get_missing_audio_card`).
        self._logged_missing_card: Optional[str] = None

    # =========================================================================
    # PRIVATE — load
    # =========================================================================

    async def initialize(self) -> None:
        """Pre-load hardware.json. An unreadable file is set aside by the loader
        and read as absent: the in-code defaults (``hardware/registry.py``)."""
        self._cache = await load_versioned_json(self.hardware_file, self.SCHEMA_VERSION)

    def _ensure_cache(self) -> Dict:
        """Sync access path for bootstrap getters called before ``initialize()``.

        Through the blocking twin of the same loader, so a getter asked first
        meets what ``initialize()`` would: an unreadable file set aside, and
        ``{}``. A card that cannot be read at all is ``{}`` too, the file left
        where it is for ``initialize()`` to meet.
        """
        if self._cache is None:
            try:
                self._cache = load_versioned_json_sync(self.hardware_file, self.SCHEMA_VERSION)
            except OSError as e:
                self.logger.warning(f"hardware sync read fallback: {e}")
                self._cache = {}
        return self._cache

    # =========================================================================
    # PUBLIC — read accessors
    # =========================================================================

    def get_screen_type(self) -> str:
        """Returns the screen type: "waveshare_7_usb", "waveshare_8_dsi", or "none"."""
        config = self._ensure_cache()
        return config.get('screen', {}).get('type', 'none')

    def get_screen_resolution(self) -> Optional[Dict[str, int]]:
        """Returns parsed resolution dict {"width": int, "height": int} or None."""
        config = self._ensure_cache()
        resolution_str = config.get('screen', {}).get('resolution')
        if not resolution_str:
            return None
        try:
            width, height = resolution_str.split('x')
            return {"width": int(width), "height": int(height)}
        except (ValueError, AttributeError) as e:
            self.logger.warning(f"Invalid resolution format: {resolution_str}, error: {e}")
            return None

    def get_screen_info(self) -> Dict:
        """Returns combined screen info: {"type": str, "resolution": dict|None,
        "color_correction": (r, g, b)|None, "pixels_per_mm": float|None} — the
        last two from the screen registry."""
        screen_type = self.get_screen_type()
        screen = SCREENS.get(screen_type, {})
        resolution = self.get_screen_resolution()
        width_mm = screen.get("active_width_mm")
        return {
            "type": screen_type,
            "resolution": resolution,
            "color_correction": screen.get("color_correction"),
            "pixels_per_mm": resolution["width"] / width_mm if resolution and width_mm else None,
        }

    def get_audio_id(self) -> Optional[str]:
        """Returns the audio card registry ID (e.g. "hifiberry_amp2")."""
        config = self._ensure_cache()
        return config.get('audio', {}).get('id')

    def get_rotary_enabled(self) -> bool:
        """Returns True if the rotary encoder is enabled in hardware.json."""
        config = self._ensure_cache()
        return config.get('rotary_encoder', {}).get('enabled', True)

    def get_rotary_pins(self) -> Tuple[int, int, int]:
        """Returns (clk_pin, dt_pin, sw_pin) from config or defaults."""
        config = self._ensure_cache()
        rotary = config.get('rotary_encoder', {})
        return (
            rotary.get('clk_pin', DEFAULT_ROTARY_PINS['clk_pin']),
            rotary.get('dt_pin', DEFAULT_ROTARY_PINS['dt_pin']),
            rotary.get('sw_pin', DEFAULT_ROTARY_PINS['sw_pin']),
        )

    def get_ir_enabled(self) -> bool:
        """Returns True if the gpio-ir overlay should be loaded at boot."""
        config = self._ensure_cache()
        return config.get('ir_remote', {}).get('enabled', DEFAULT_IR_REMOTE['enabled'])

    def get_ir_gpio_pin(self) -> int:
        """Returns the GPIO pin used by the gpio-ir overlay (TSOP4838 data line)."""
        config = self._ensure_cache()
        return config.get('ir_remote', {}).get('gpio_pin', DEFAULT_IR_REMOTE['gpio_pin'])

    def get_power_button_enabled(self) -> bool:
        """Returns True if a power button is wired, so the board waits for a press to boot."""
        config = self._ensure_cache()
        return config.get('power_button', {}).get('enabled', DEFAULT_POWER_BUTTON['enabled'])

    def get_power_button_led_pin(self) -> int:
        """Returns the GPIO sinking the power button LED's cathode."""
        config = self._ensure_cache()
        return config.get('power_button', {}).get('led_gpio_pin', DEFAULT_POWER_BUTTON['led_gpio_pin'])

    def power_button_supported(self) -> bool:
        """True on a Raspberry Pi 5, the only board whose bootloader can wait for its button.

        Read from the device tree rather than the model string, which carries a
        revision. False wherever the tree is unreadable (a dev host), so the
        Hardware page hides a setting nothing could apply.
        """
        try:
            compatible = Path("/proc/device-tree/compatible").read_bytes()
        except OSError:
            return False
        return b"brcm,bcm2712" in compatible.split(b"\0")

    def get_volume_control(self) -> bool:
        """Returns False if audio card is a DAC with external amp managing volume.

        Every write of the audio block stores it; only a unit the wizard has
        not configured yet has none, and CamillaDSP attenuates there.
        """
        config = self._ensure_cache()
        return config.get('audio', {}).get('volume_control', True)

    async def set_volume_control(self, enabled: bool) -> None:
        """Persist volume_control override in hardware.json without reboot.

        Read from the file, not from the cache: a cache that stands for a file
        the card refused is ``{}``, and written back with this one key it would
        replace the card, the screen and the pins with nothing. A card that
        cannot be read raises, and nothing is written.
        """
        config = await load_versioned_json(self.hardware_file, self.SCHEMA_VERSION)
        if 'audio' not in config:
            config['audio'] = {}
        config['audio']['volume_control'] = enabled
        await self.save_config(config)

    def get_full_config(self) -> Dict:
        """Returns the complete normalized hardware config."""
        config = self._ensure_cache()
        clk, dt, sw = self.get_rotary_pins()
        return {
            "audio": config.get('audio', {}),
            "screen": config.get('screen', {'type': 'none', 'resolution': None}),
            "rotary_encoder": {"enabled": self.get_rotary_enabled(), "clk_pin": clk, "dt_pin": dt, "sw_pin": sw},
            "ir_remote": {"enabled": self.get_ir_enabled(), "gpio_pin": self.get_ir_gpio_pin()},
            "power_button": {
                "enabled": self.get_power_button_enabled(),
                "led_gpio_pin": self.get_power_button_led_pin(),
            },
        }

    def get_missing_audio_card(self) -> Optional[str]:
        """The configured card's label if ALSA does not see it, else None.

        The wizard offers a static list of supported boards and nothing checks
        that the one picked is the one soldered on: a wrong choice — or a HAT
        seated badly — reboots into a unit with no `sndrpihifiberry`, a
        CamillaDSP that cannot open its playback device, and no sound. Every
        part of that is silent from the UI, which is the reason this exists.
        Diagnosis is a state, not an event: a HAT is not hot-pluggable, so the
        answer is settled at boot and read back through `GET /api/system/status`.
        It is logged when it changes, never at each reading: every browser asks
        at each load, reconnection and return to its tab, and an error logged
        there became a second, generic "system error" banner each time, beside
        the notice that names the card, and the same line in errors.log.

        Fails open in both directions that are not a real answer — no card
        configured yet, or /proc/asound unreadable (a dev host has no such tree)
        — because reporting hardware trouble we have not observed is worse than
        reporting none. A stored `none` is not one of them: the server plays
        through a HAT or not at all.
        """
        audio_id = self.get_audio_id()
        if not audio_id:
            # No answer is not an answer of "present": forgotten, so the card
            # missing again after it is logged again.
            self._logged_missing_card = None
            return None

        try:
            cards = Path("/proc/asound/cards").read_text()
        except OSError:
            self._logged_missing_card = None
            return None

        label = missing_card_label(audio_id, alsa_card_ids(cards))
        if label != self._logged_missing_card:
            if label is None:
                self.logger.info("Audio card '%s' is no longer reported missing", self._logged_missing_card)
            else:
                self.logger.error(
                    "Audio card '%s' (%s) is configured but ALSA does not see it — "
                    "check that the board is seated, or pick another card in Settings",
                    label, OUTPUT_CARD,
                )
            self._logged_missing_card = label
        return label

    # =========================================================================
    # PUBLIC — write
    # =========================================================================

    async def save_config(self, config: Dict) -> None:
        """Atomic write to hardware.json. Stamps schema_version. Invalidates cache."""
        await save_versioned_json(self.hardware_file, config, self.SCHEMA_VERSION)
        self._cache = None

    async def replace_config(self, config: Dict) -> None:
        """Write `config` to hardware.json and apply it, or leave the previous one.

        The one transaction both writers run — the setup wizard and the
        Hardware page —, inside their request, so a refusal reaches the screen
        that asked. The helper reads hardware.json, so the new config is
        written first; a refused apply puts the previous one back and raises,
        so the file never names a card config.txt does not load (config.txt is
        then the old one too, unless the helper failed after it staged the
        EEPROM or began rewriting config.txt). Held from the write to the end
        of the apply: a second call interleaved with a refused one would put
        the first's "previous" back over a config.txt the second just applied.
        The reboot stays the caller's.
        """
        async with self._replace_lock:
            previous = self.get_full_config()
            await self.save_config(config)
            try:
                await self.apply()
            except Exception as e:
                self.logger.error(f"{e} — restoring the previous hardware config")
                await self.save_config(previous)
                raise

    async def apply(self) -> None:
        """Apply hardware config to config.txt and the bootloader EEPROM via milo-apply-hardware.

        The helper runs with `--no-reboot`, always: the reboot is the caller's.
        The setup wizard must persist `setup_completed` between the apply and
        the reboot — doing it before the overlay reached config.txt left a
        window where a power cut produced a unit that believed it was
        configured and had no audio card, silent and with the wizard gone —
        and a refused apply must put the previous hardware.json back
        (`replace_config`), answering the failure rather than "rebooting".
        So any non-zero return code is a failure, a signal included: the
        helper never reboots here, so a signal came from outside, and a
        helper that died after it began rewriting config.txt can leave it
        half-written; restoring hardware.json does not undo that.
        """
        self.logger.info("Applying hardware configuration...")
        proc = await asyncio.create_subprocess_exec(
            'sudo', '/usr/local/bin/milo-apply-hardware', '--no-reboot',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode
        if rc == 0:
            return
        printed = stderr.decode().strip() if stderr else ""
        if rc < 0:
            try:
                error_msg = f"killed by {signal.Signals(-rc).name}"
            except ValueError:
                error_msg = f"killed by signal {-rc}"
            if printed:
                error_msg = f"{error_msg}: {printed}"
        else:
            error_msg = printed or "unknown error"
        self.logger.error(f"milo-apply-hardware failed (rc={rc}): {error_msg}")
        raise RuntimeError(f"Hardware apply failed: {error_msg}")
