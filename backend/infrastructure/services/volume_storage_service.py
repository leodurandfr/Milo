# backend/infrastructure/services/volume_storage_service.py
"""
Volume storage service for persisting and restoring volume state.
All values are in decibels (dB).
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional
import aiofiles


class VolumeStorageService:
    """Service for persisting volume state to disk."""

    DEFAULT_FILE = Path("/var/lib/milo/last_volume.json")
    MAX_AGE_DAYS = 7

    def __init__(self, file_path: Optional[Path] = None):
        """
        Initialize the storage service.

        Args:
            file_path: Path to storage file (default: /var/lib/milo/last_volume.json)
        """
        self.file_path = file_path or self.DEFAULT_FILE
        self.logger = logging.getLogger(__name__)
        self._save_task: Optional[asyncio.Task] = None
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Create data directory if it doesn't exist."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create data directory: {e}")

    def save(self, volume_db: float, enabled: bool = True) -> None:
        """
        Save volume in background (non-blocking).

        Args:
            volume_db: Volume to save in dB (-80 to 0)
            enabled: Whether volume restore is enabled
        """
        if not enabled:
            return

        async def save_async():
            try:
                data = {
                    "volume_db": volume_db,
                    "timestamp": time.time()
                }
                temp_file = self.file_path.with_suffix('.tmp')

                async with aiofiles.open(temp_file, 'w') as f:
                    content = json.dumps(data)
                    await f.write(content)
                    await f.flush()

                temp_file.replace(self.file_path)
            except Exception as e:
                self.logger.error(f"Failed to save last volume: {e}")

        # Keep reference to prevent task from being garbage collected
        self._save_task = asyncio.create_task(save_async())

    def load(self) -> Optional[float]:
        """
        Load last saved volume from persistent storage.

        Returns:
            Last volume in dB or None if not available/expired
        """
        try:
            if not self.file_path.exists():
                return None

            with open(self.file_path, 'r') as f:
                data = json.load(f)

            volume_db = data.get('volume_db')
            if volume_db is None:
                return None

            timestamp = data.get('timestamp', 0)
            age_days = (time.time() - timestamp) / (24 * 3600)

            # Validate: not too old and within valid range
            if age_days > self.MAX_AGE_DAYS or not (-80.0 <= volume_db <= 0.0):
                return None

            self.logger.info(f"Restored last volume: {volume_db:.1f} dB")
            return volume_db

        except Exception:
            return None

    def get_startup_volume(self, default_volume_db: float, restore_enabled: bool) -> float:
        """
        Determine startup volume (restored or default).

        Args:
            default_volume_db: Default startup volume in dB
            restore_enabled: Whether volume restore is enabled

        Returns:
            Volume to use at startup in dB
        """
        if restore_enabled:
            last_volume = self.load()
            if last_volume is not None:
                return last_volume
        return default_volume_db

    async def cleanup(self) -> None:
        """Wait for pending save task to complete."""
        if self._save_task and not self._save_task.done():
            try:
                await self._save_task
            except Exception as e:
                self.logger.error(f"Error waiting for save task: {e}")
