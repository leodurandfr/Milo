# backend/infrastructure/services/routing/routing_env.py
"""
Routing environment file management.

Handles writing ALSA routing environment variables to /var/lib/milo/routing.env
which is read by systemd services for audio routing configuration.
"""
import os
import logging
from typing import Literal

logger = logging.getLogger(__name__)


class RoutingEnvironment:
    """
    Manages the routing environment file for ALSA configuration.

    Environment variables written:
    - MILO_MODE: "direct" or "multiroom"
    - MILO_EQUALIZER: "" or "_eq"
    - MILO_SNAPCLIENT_SOUNDCARD: always "camilladsp"
    """

    ENVIRONMENT_FILE = "/var/lib/milo/routing.env"
    ALLOWED_MODES = frozenset(["direct", "multiroom"])
    ALLOWED_EQUALIZER = frozenset(["", "_eq"])

    @classmethod
    def update(cls, multiroom_enabled: bool, dsp_effects_enabled: bool) -> None:
        """
        Update routing environment file atomically.

        Args:
            multiroom_enabled: Whether multiroom mode is active
            dsp_effects_enabled: Whether DSP effects are enabled

        Raises:
            ValueError: If invalid mode or equalizer value
            RuntimeError: If file write fails
        """
        mode_value = "multiroom" if multiroom_enabled else "direct"
        equalizer_value = "_eq" if dsp_effects_enabled else ""

        # Strict validation
        if mode_value not in cls.ALLOWED_MODES:
            raise ValueError(f"Invalid mode value: {mode_value}. Allowed: {cls.ALLOWED_MODES}")

        if equalizer_value not in cls.ALLOWED_EQUALIZER:
            raise ValueError(f"Invalid equalizer value: {equalizer_value}. Allowed: {cls.ALLOWED_EQUALIZER}")

        temp_file = cls.ENVIRONMENT_FILE + ".tmp"

        try:
            # CamillaDSP is ALWAYS active (for volume control)
            snapclient_soundcard = "camilladsp"

            # Atomic write of environment file
            with open(temp_file, 'w') as f:
                f.write("# Milo Audio Routing Environment Variables\n")
                f.write("# This file is automatically modified by Milo backend\n")
                f.write("# Do not edit manually\n\n")
                f.write(f"# Audio routing mode: \"direct\" or \"multiroom\"\n")
                f.write(f"MILO_MODE={mode_value}\n\n")
                f.write(f"# Equalizer: \"\" (disabled) or \"_eq\" (enabled)\n")
                f.write(f"MILO_EQUALIZER={equalizer_value}\n\n")
                f.write(f"# Snapclient output soundcard\n")
                f.write(f"MILO_SNAPCLIENT_SOUNDCARD={snapclient_soundcard}\n")
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(temp_file, cls.ENVIRONMENT_FILE)

            # Local update for compatibility
            os.environ["MILO_MODE"] = mode_value
            os.environ["MILO_EQUALIZER"] = equalizer_value

            logger.info(f"Updated routing.env: MODE={mode_value}, EQUALIZER={equalizer_value}")

        except Exception as e:
            logger.error(f"Failed to update environment file: {e}")
            # Clean up temp file on failure
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
            raise RuntimeError(f"Failed to update environment file: {e}")

    @classmethod
    def get_mode(cls) -> Literal["direct", "multiroom"]:
        """Get current routing mode from environment."""
        return os.environ.get("MILO_MODE", "direct")

    @classmethod
    def get_equalizer(cls) -> Literal["", "_eq"]:
        """Get current equalizer suffix from environment."""
        return os.environ.get("MILO_EQUALIZER", "")
