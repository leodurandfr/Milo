"""
Minimal service to manage radio_data.json
"""
import json
import os
import logging
import aiofiles
import asyncio
from typing import Dict, Any


class RadioDataService:
    """
    Service minimal pour radio_data.json

    Only provides load_data() and save_data()
    Business logic remains in StationManager
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.data_file = '/var/lib/milo/radio_data.json'
        self._file_lock = asyncio.Lock()

    async def load_data(self) -> Dict[str, Any]:
        """
        Loads radio_data.json or migrates from milo_settings.json

        Returns:
            Dict with favorites, custom_stations, favorites_cache, broken_stations
        """
        try:
            if os.path.exists(self.data_file):
                # File exists, load it
                async with self._file_lock:
                    async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        # Ensure favorites_cache key exists
                        if 'favorites_cache' not in data:
                            data['favorites_cache'] = {}
                        return data
            else:
                # First time: create empty data file
                self.logger.info("radio_data.json not found, creating new file")
                default_data = {"favorites": [], "broken_stations": [], "custom_stations": {}, "favorites_cache": {}}
                await self.save_data(default_data)
                return default_data

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON error in radio_data.json: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": {}, "favorites_cache": {}}
        except Exception as e:
            self.logger.error(f"Error loading radio_data.json: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": {}, "favorites_cache": {}}

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """
        Saves radio_data.json with atomic write

        Args:
            data: Dict with favorites, broken_stations, custom_stations

        Returns:
            True if successful
        """
        try:
            async with self._file_lock:
                temp_file = self.data_file + '.tmp'

                # Write to temporary file
                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                # Atomic rename
                os.replace(temp_file, self.data_file)

            return True

        except Exception as e:
            self.logger.error(f"Error saving radio_data.json: {e}")
            return False
