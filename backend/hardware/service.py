# backend/hardware/service.py
"""
Hardware management service — read/write hardware.json configuration.

Handles screen type, audio card, and rotary encoder GPIO pin configuration.
Supports migration from the legacy format (screen type as dict key) to the
normalized format (screen type as "type" field).
"""
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, Tuple

from backend.config.constants import HARDWARE_FILE
from backend.hardware.registry import AUDIO_CARDS, SCREENS, DEFAULT_ROTARY_PINS
from backend.shared.decorators import handle_errors


class HardwareService:
    """Service to read and write hardware configuration (screen, audio, rotary encoder)."""

    def __init__(self):
        self.hardware_file = HARDWARE_FILE
        self.logger = logging.getLogger(__name__)
        self._cache: Optional[Dict] = None

    # =========================================================================
    # PRIVATE — load, migrate, ensure cache
    # =========================================================================

    def _ensure_cache(self) -> Dict:
        """Load and cache hardware config, migrating legacy format if needed."""
        if self._cache is None:
            self._cache = self._load_hardware_config()
        return self._cache

    @handle_errors(default={})
    def _load_hardware_config(self) -> Dict:
        """Load hardware.json, migrating legacy format transparently."""
        if not self.hardware_file.exists():
            self.logger.warning(f"Hardware config file not found: {self.hardware_file}")
            return {}

        with open(self.hardware_file, 'r') as f:
            config = json.load(f)

        # Migrate legacy format if detected
        migrated = self._migrate_legacy_format(config)
        if migrated is not None:
            config = migrated
            self._write_sync(config)
            self.logger.info("Migrated hardware.json to new format")

        self.logger.info(f"Hardware config loaded: {config}")
        return config

    def _migrate_legacy_format(self, config: Dict) -> Optional[Dict]:
        """
        One-time data migration from legacy hardware.json format.
        Rewrites the file in-place on first load, then uses the new format.

        Legacy: {"screen": {"waveshare_8_dsi": {"resolution": "1280x800"}}}
        New:    {"screen": {"type": "waveshare_8_dsi", "resolution": "1280x800"}}

        Returns the migrated config, or None if no migration needed.
        TODO: Remove after all installations have been migrated (v2.x).
        """
        screen = config.get('screen', {})

        # Already new format (has "type" key)
        if 'type' in screen:
            return None

        # Detect legacy: screen dict has a known screen type as key
        legacy_types = {'waveshare_7_usb', 'waveshare_8_dsi', 'none'}
        found_type = None
        for key in screen:
            if key in legacy_types:
                found_type = key
                break

        if found_type is None:
            return None

        # Build migrated config
        migrated = dict(config)

        # Migrate screen section
        screen_data = screen.get(found_type, {})
        migrated['screen'] = {
            'type': found_type,
            'resolution': screen_data.get('resolution'),
        }

        # Migrate audio section — add 'id' by reverse-looking up the registry
        audio = config.get('audio', {})
        if audio and 'id' not in audio:
            audio_id = self._resolve_audio_id(audio)
            migrated['audio'] = {
                'id': audio_id,
                **audio,
            }
            # Add overlay from registry if missing
            if audio_id and 'overlay' not in migrated['audio']:
                card = AUDIO_CARDS.get(audio_id)
                if card:
                    migrated['audio']['overlay'] = card['overlay']

        # Add default rotary encoder pins if missing
        if 'rotary_encoder' not in migrated:
            migrated['rotary_encoder'] = dict(DEFAULT_ROTARY_PINS)

        return migrated

    @staticmethod
    def _resolve_audio_id(audio: Dict) -> Optional[str]:
        """Reverse-lookup audio card ID from overlay + card_name + alsa_control."""
        overlay = audio.get('overlay', '')
        card_name = audio.get('card_name', '')
        alsa_control = audio.get('alsa_control', '')

        # Try exact match on all three fields first (most reliable)
        for card_id, card in AUDIO_CARDS.items():
            if (card['overlay'] == overlay
                    and card['card_name'] == card_name
                    and card['alsa_control'] == alsa_control):
                return card_id

        # Fallback: match without overlay (for very old configs missing overlay)
        for card_id, card in AUDIO_CARDS.items():
            if card['card_name'] == card_name and card['alsa_control'] == alsa_control:
                return card_id

        return None

    def _write_sync(self, config: Dict) -> None:
        """Atomic write to hardware.json (sync, for migration on load)."""
        dir_path = self.hardware_file.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(config, f, indent=2)
            os.replace(tmp_path, str(self.hardware_file))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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

    def get_alsa_control(self) -> Optional[str]:
        """Returns the ALSA mixer control name (e.g. "Digital", "DAC")."""
        config = self._ensure_cache()
        return config.get('audio', {}).get('alsa_control') or None

    def get_audio_id(self) -> Optional[str]:
        """Returns the audio card registry ID (e.g. "hifiberry_amp2")."""
        config = self._ensure_cache()
        return config.get('audio', {}).get('id')

    def get_rotary_pins(self) -> Tuple[int, int, int]:
        """Returns (clk_pin, dt_pin, sw_pin) from config or defaults."""
        config = self._ensure_cache()
        rotary = config.get('rotary_encoder', {})
        return (
            rotary.get('clk_pin', DEFAULT_ROTARY_PINS['clk_pin']),
            rotary.get('dt_pin', DEFAULT_ROTARY_PINS['dt_pin']),
            rotary.get('sw_pin', DEFAULT_ROTARY_PINS['sw_pin']),
        )

    def get_volume_control(self) -> bool:
        """Returns False if the audio card is a DAC (external amp manages volume)."""
        from backend.hardware.registry import is_dac_card
        audio_id = self.get_audio_id()
        if not audio_id or audio_id == "none":
            return True
        return not is_dac_card(audio_id)

    def get_full_config(self) -> Dict:
        """Returns the complete normalized hardware config."""
        config = self._ensure_cache()
        clk, dt, sw = self.get_rotary_pins()
        return {
            "audio": config.get('audio', {}),
            "screen": config.get('screen', {'type': 'none', 'resolution': None}),
            "rotary_encoder": {"clk_pin": clk, "dt_pin": dt, "sw_pin": sw},
        }

    # =========================================================================
    # PUBLIC — write
    # =========================================================================

    async def save_config(self, config: Dict) -> None:
        """Atomic write to hardware.json. Invalidates cache after write."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_sync, config)
        self._cache = None

    async def apply_and_reboot(self) -> None:
        """Apply hardware config to config.txt and reboot via milo-apply-hardware."""
        self.logger.info("Applying hardware configuration and rebooting...")
        proc = await asyncio.create_subprocess_exec(
            'sudo', '/usr/local/bin/milo-apply-hardware',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode().strip() if stderr else "unknown error"
            self.logger.error(f"milo-apply-hardware failed (rc={proc.returncode}): {error_msg}")
            raise RuntimeError(f"Hardware apply failed: {error_msg}")

    def reload(self):
        """Forces hardware configuration reload."""
        self._cache = None
