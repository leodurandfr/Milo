# backend/infrastructure/services/volume_storage_service.py
"""
Volume storage service for persisting and restoring volume state.
Handles saving/loading the last volume to/from persistent storage.
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

    def save(self, display_volume: int, enabled: bool = True) -> None:
        """
        Save volume in background (non-blocking).

        Args:
            display_volume: Volume to save (0-100)
            enabled: Whether volume restore is enabled
        """
        if not enabled:
            return

        async def save_async():
            try:
                data = {
                    "last_volume": display_volume,
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

    def load(self) -> Optional[int]:
        """
        Load last saved volume from persistent storage.

        Returns:
            Last volume (0-100) or None if not available/expired
        """
        try:
            if not self.file_path.exists():
                return None

            with open(self.file_path, 'r') as f:
                data = json.load(f)

            last_volume = data.get('last_volume')
            timestamp = data.get('timestamp', 0)
            age_days = (time.time() - timestamp) / (24 * 3600)

            # Validate: not too old and within valid range
            if age_days > self.MAX_AGE_DAYS or not (0 <= last_volume <= 100):
                return None

            self.logger.info(f"Restored last volume: {last_volume}%")
            return last_volume

        except Exception:
            return None

    def get_startup_volume(self, default_volume: int, restore_enabled: bool) -> int:
        """
        Determine startup volume (restored or default).

        Args:
            default_volume: Default startup volume
            restore_enabled: Whether volume restore is enabled

        Returns:
            Volume to use at startup
        """
        if restore_enabled:
            last_volume = self.load()
            if last_volume is not None:
                return last_volume
        return default_volume

    async def cleanup(self) -> None:
        """Wait for pending save task to complete."""
        if self._save_task and not self._save_task.done():
            try:
                await self._save_task
            except Exception as e:
                self.logger.error(f"Error waiting for save task: {e}")
