# backend/infrastructure/services/hardware_service.py
"""
Service de gestion du hardware - Type d'écran, audio, etc.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Dict


class HardwareService:
    """Service pour lire la configuration hardware (écran, audio, etc.)"""

    def __init__(self):
        self.hardware_file = Path("/var/lib/milo/milo_hardware.json")
        self.logger = logging.getLogger(__name__)
        self._cache: Optional[Dict] = None

    def _load_hardware_config(self) -> Dict:
        """Charge la configuration hardware depuis le fichier JSON"""
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
        Retourne le type d'écran configuré lors de l'installation.

        Returns:
            str: "waveshare_7_usb", "waveshare_8_dsi", ou "none"
        """
        if self._cache is None:
            self._cache = self._load_hardware_config()

        screen_type = self._cache.get('screen', {}).get('type', 'none')
        return screen_type

    def reload(self):
        """Force le rechargement de la configuration hardware"""
        self._cache = None
