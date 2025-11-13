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
                        # Ensure new structure keys exist (for backwards compatibility)
                        if 'favorites_cache' not in data:
                            data['favorites_cache'] = {}
                        if isinstance(data.get('custom_stations'), list):
                            # Old format: convert list to dict
                            data['custom_stations'] = {}
                        return data
            else:
                # First time: migrate from milo_settings.json
                self.logger.info("radio_data.json not found, migrating from milo_settings.json")
                return await self._migrate_from_settings()

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

    async def _migrate_from_settings(self) -> Dict[str, Any]:
        """
        Migrates data from milo_settings.json (once only)

        Returns:
            Migrated data
        """
        settings_file = '/var/lib/milo/settings.json'

        if not os.path.exists(settings_file):
            return {"favorites": [], "broken_stations": [], "custom_stations": []}

        try:
            async with aiofiles.open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.loads(await f.read())

            radio_section = settings.get('radio', {})

            # Extract data
            old_favorites_ids = radio_section.get('favorites', [])
            broken_stations = radio_section.get('broken_stations', [])
            custom_stations = radio_section.get('custom_stations', [])
            station_images = radio_section.get('station_images', {})

            # Convert favorites: merge IDs + metadata
            new_favorites = []
            for station_id in old_favorites_ids:
                if station_id.startswith("custom_"):
                    # Custom station
                    custom = next((s for s in custom_stations if s.get('id') == station_id), None)
                    if custom:
                        new_favorites.append(custom)
                    else:
                        new_favorites.append({"id": station_id})
                elif station_id in station_images:
                    # Station with metadata
                    metadata = station_images[station_id]
                    new_favorites.append({
                        'id': station_id,
                        'name': metadata.get('name', ''),
                        'country': metadata.get('country', ''),
                        'genre': metadata.get('genre', ''),
                        'favicon': metadata.get('favicon', ''),
                        'url': '',
                        'bitrate': 0,
                        'codec': 'Unknown'
                    })
                else:
                    # Just the ID
                    new_favorites.append({"id": station_id})

            migrated = {
                "favorites": new_favorites,
                "broken_stations": broken_stations,
                "custom_stations": custom_stations,
                "favorites_cache": {}  # Empty cache, will be populated on first load
            }

            # Save immediately
            await self.save_data(migrated)
            self.logger.info(f"✅ Migrated {len(new_favorites)} favorites from milo_settings.json")

            return migrated

        except Exception as e:
            self.logger.error(f"Migration error: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": {}, "favorites_cache": {}}
