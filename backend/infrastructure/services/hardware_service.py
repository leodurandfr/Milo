# backend/infrastructure/services/hardware_service.py
"""
Hardware management service - Screen type, audio, etc.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Dict


class HardwareService:
    """Service to read hardware configuration (screen, audio, etc.)"""

    def __init__(self):
        self.hardware_file = Path("/var/lib/milo/hardware.json")
        self.logger = logging.getLogger(__name__)
        self._cache: Optional[Dict] = None

    def _load_hardware_config(self) -> Dict:
        """Loads hardware configuration from JSON file"""
        try:
            if self.hardware_file.exists():
                with open(self.hardware_file, 'r') as f:
                    config = json.load(f)
                    self.logger.info(f"Hardware config loaded: {config}")
                    return config
            else:
                self.logger.warning(f"Hardware config file not found: {self.hardware_file}")
                return {}
        except Exception as e:
            self.logger.error(f"Error loading hardware config: {e}")
            return {}

    def get_screen_type(self) -> str:
        """
        Returns the screen type configured during installation.

        Format: {"screen": {"waveshare_8_dsi": {"resolution": "1280x800"}}}

        Returns:
            str: "waveshare_7_usb", "waveshare_8_dsi", or "none"
        """
        if self._cache is None:
            self._cache = self._load_hardware_config()

        screen_config = self._cache.get('screen', {})

        # Screen type is the main key
        for key in screen_config.keys():
            if key in ['waveshare_7_usb', 'waveshare_8_dsi', 'none']:
                return key

        return 'none'

    def get_screen_resolution(self) -> Optional[Dict[str, int]]:
        """
        Returns the configured screen resolution.

        Returns:
            dict: {"width": 1280, "height": 800} or None if not defined
        """
        if self._cache is None:
            self._cache = self._load_hardware_config()

        screen_config = self._cache.get('screen', {})
        screen_type = self.get_screen_type()

        # Read resolution from screen type config
        if screen_type in screen_config and isinstance(screen_config[screen_type], dict):
            resolution_str = screen_config[screen_type].get('resolution')
            if resolution_str:
                try:
                    # Parse "1280x800" -> {"width": 1280, "height": 800}
                    width, height = resolution_str.split('x')
                    return {"width": int(width), "height": int(height)}
                except (ValueError, AttributeError) as e:
                    self.logger.warning(f"Invalid resolution format: {resolution_str}, error: {e}")

        return None

    def get_screen_info(self) -> Dict:
        """
        Retourne toutes les informations sur l'écran.

        Returns:
            dict: {"type": "waveshare_8_dsi", "resolution": {"width": 1280, "height": 800}}
        """
        return {
            "type": self.get_screen_type(),
            "resolution": self.get_screen_resolution()
        }

    def reload(self):
        """Forces hardware configuration reload"""
        self._cache = None
