# backend/hardware/service.py
"""Hardware management service — read/write hardware.json configuration.

Handles screen type, audio card, rotary encoder GPIO pins, and IR remote
configuration. Uses the schema_version protocol — see CLAUDE.md
§"Persistence & schema-version protocol".
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from backend.config.constants import HARDWARE_FILE
from backend.hardware.registry import DEFAULT_ROTARY_PINS, DEFAULT_IR_REMOTE
from backend.shared.persistence import load_versioned_json, save_versioned_json


class HardwareService:
    """Service to read and write hardware configuration (screen, audio, rotary encoder, IR)."""

    SCHEMA_VERSION: int = 2

    def __init__(self):
        self.hardware_file: Path = HARDWARE_FILE
        self.logger = logging.getLogger(__name__)
        self._cache: Optional[Dict] = None

    # =========================================================================
    # PRIVATE — load
    # =========================================================================

    async def initialize(self) -> None:
        """Pre-load hardware.json so a schema mismatch surfaces at boot.

        Raises SchemaVersionMismatch on version drift; the handler in
        dependencies.py::init_async logs the banner and SystemExit(1)s.
        """
        self._cache = await load_versioned_json(self.hardware_file, self.SCHEMA_VERSION)

    def _ensure_cache(self) -> Dict:
        """Sync access path for bootstrap getters called before ``initialize()``.

        Reads the file leniently — schema validation happens in async
        ``initialize()``. If the file is missing or unreadable, returns ``{}``.
        """
        if self._cache is None:
            if self.hardware_file.exists():
                try:
                    with open(self.hardware_file, 'r') as f:
                        self._cache = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    self.logger.warning(f"hardware sync read fallback: {e}")
                    self._cache = {}
            else:
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
        """Returns combined screen info: {"type": str, "resolution": dict|None}."""
        return {
            "type": self.get_screen_type(),
            "resolution": self.get_screen_resolution()
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

    def get_volume_control(self) -> bool:
        """Returns False if audio card is a DAC with external amp managing volume.

        Reads explicit 'volume_control' from hardware.json if set by user,
        otherwise auto-detects from audio card category.
        """
        config = self._ensure_cache()
        explicit = config.get('audio', {}).get('volume_control')
        if explicit is not None:
            return explicit
        from backend.hardware.registry import is_dac_card
        audio_id = self.get_audio_id()
        if not audio_id or audio_id == "none":
            return True
        return not is_dac_card(audio_id)

    async def set_volume_control(self, enabled: bool) -> None:
        """Persist volume_control override in hardware.json without reboot."""
        config = self._ensure_cache()
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

        Fails open in both directions that are not a real answer — no card
        configured, or /proc/asound unreadable (a dev host has no such tree) —
        because reporting hardware trouble we have not observed is worse than
        reporting none.
        """
        from backend.hardware.registry import AUDIO_CARDS

        audio_id = self.get_audio_id()
        if not audio_id or audio_id == "none":
            return None

        card = AUDIO_CARDS.get(audio_id)
        if not card or not card.get("card_name"):
            return None

        try:
            cards = Path("/proc/asound/cards").read_text()
        except OSError:
            return None

        if card["card_name"] in cards:
            return None

        self.logger.error(
            "Audio card '%s' (%s) is configured but ALSA does not see it — "
            "check that the board is seated, or pick another card in Settings",
            card["label"], card["card_name"],
        )
        return card["label"]

    # =========================================================================
    # PUBLIC — write
    # =========================================================================

    async def save_config(self, config: Dict) -> None:
        """Atomic write to hardware.json. Stamps schema_version. Invalidates cache."""
        await save_versioned_json(self.hardware_file, config, self.SCHEMA_VERSION)
        self._cache = None

    async def apply_and_reboot(self, reboot: bool = True) -> None:
        """Apply hardware config to config.txt via milo-apply-hardware.

        `reboot=False` returns instead of taking the box down, and exists for
        the setup wizard alone: the wizard must persist `setup_completed`
        before the reboot, and doing that before the overlay reached config.txt
        left a window where a power cut produced a unit that believed it was
        configured and had no audio card — silent, and with the wizard gone.
        """
        self.logger.info(
            "Applying hardware configuration%s...", " and rebooting" if reboot else ""
        )
        argv = ['sudo', '/usr/local/bin/milo-apply-hardware']
        if not reboot:
            argv.append('--no-reboot')
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        # Negative returncode means killed by signal (e.g. system reboot) — not an error
        if proc.returncode is not None and proc.returncode > 0:
            error_msg = stderr.decode().strip() if stderr else "unknown error"
            self.logger.error(f"milo-apply-hardware failed (rc={proc.returncode}): {error_msg}")
            raise RuntimeError(f"Hardware apply failed: {error_msg}")
