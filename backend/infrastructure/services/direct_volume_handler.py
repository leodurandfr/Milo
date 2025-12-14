# backend/infrastructure/services/direct_volume_handler.py
"""
Direct mode volume handler - ALSA passthrough only.
ALSA is always set to 100% - volume control is via CamillaDSP.
"""
import asyncio
import logging


class DirectVolumeHandler:
    """Handles ALSA passthrough setup (100% volume)."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._passthrough_set = False

    async def set_alsa_passthrough(self) -> bool:
        """
        Set ALSA Digital mixer to 100% passthrough.
        This is the only ALSA operation needed - volume is controlled by CamillaDSP.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", "100%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            success = proc.returncode == 0

            if success:
                self._passthrough_set = True
                self.logger.debug("ALSA set to 100% passthrough")

            return success
        except Exception as e:
            self.logger.error(f"Error setting ALSA passthrough: {e}")
            return False

    def is_passthrough_set(self) -> bool:
        """Check if ALSA passthrough has been configured."""
        return self._passthrough_set
