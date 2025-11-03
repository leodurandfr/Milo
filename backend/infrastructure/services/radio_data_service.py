"""
Service minimal pour gérer radio_data.json
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

    Fournit uniquement load_data() et save_data()
    La logique métier reste dans StationManager
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.data_file = os.path.expanduser('~/milo/radio_data.json')
        self._file_lock = asyncio.Lock()

    async def load_data(self) -> Dict[str, Any]:
        """
        Charge radio_data.json ou migre depuis milo_settings.json

        Returns:
            Dict avec favorites, broken_stations, custom_stations
        """
        try:
            if os.path.exists(self.data_file):
                # Fichier existe, le charger
                async with self._file_lock:
                    async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as f:
                        return json.loads(await f.read())
            else:
                # Première fois : migrer depuis milo_settings.json
                self.logger.info("radio_data.json not found, migrating from milo_settings.json")
                return await self._migrate_from_settings()

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON error in radio_data.json: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": []}
        except Exception as e:
            self.logger.error(f"Error loading radio_data.json: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": []}

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """
        Sauvegarde radio_data.json avec écriture atomique

        Args:
            data: Dict avec favorites, broken_stations, custom_stations

        Returns:
            True si succès
        """
        try:
            async with self._file_lock:
                temp_file = self.data_file + '.tmp'

                # Écrire dans fichier temporaire
                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                # Renommage atomique
                os.replace(temp_file, self.data_file)

            return True

        except Exception as e:
            self.logger.error(f"Error saving radio_data.json: {e}")
            return False

    async def _migrate_from_settings(self) -> Dict[str, Any]:
        """
        Migre les données depuis milo_settings.json (une seule fois)

        Returns:
            Données migrées
        """
        settings_file = os.path.expanduser('~/milo/milo_settings.json')

        if not os.path.exists(settings_file):
            return {"favorites": [], "broken_stations": [], "custom_stations": []}

        try:
            async with aiofiles.open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.loads(await f.read())

            radio_section = settings.get('radio', {})

            # Extraire les données
            old_favorites_ids = radio_section.get('favorites', [])
            broken_stations = radio_section.get('broken_stations', [])
            custom_stations = radio_section.get('custom_stations', [])
            station_images = radio_section.get('station_images', {})

            # Convertir favoris : fusionner IDs + métadonnées
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
                    # Station avec métadonnées
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
                    # Juste l'ID
                    new_favorites.append({"id": station_id})

            migrated = {
                "favorites": new_favorites,
                "broken_stations": broken_stations,
                "custom_stations": custom_stations
            }

            # Sauvegarder immédiatement
            await self.save_data(migrated)
            self.logger.info(f"✅ Migrated {len(new_favorites)} favorites from milo_settings.json")

            return migrated

        except Exception as e:
            self.logger.error(f"Migration error: {e}")
            return {"favorites": [], "broken_stations": [], "custom_stations": []}
