"""
Service to manage podcast_data.json
"""
import json
import os
import logging
import aiofiles
import asyncio
from typing import Dict, Any, List, Optional


class PodcastDataService:
    """
    Service for podcast_data.json

    Manages:
    - Subscriptions (podcast series UUIDs)
    - Favorites (episode UUIDs)
    - Playback progress (episode_uuid -> {position, duration, last_played})
    - Episode cache (for offline access)
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.data_file = '/var/lib/milo/podcast_data.json'
        self._file_lock = asyncio.Lock()

    async def load_data(self) -> Dict[str, Any]:
        """
        Loads podcast_data.json

        Returns:
            Dict with subscriptions, favorites, playback_progress, episode_cache
        """
        try:
            if os.path.exists(self.data_file):
                async with self._file_lock:
                    async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        # Ensure all keys exist
                        data.setdefault('subscriptions', [])
                        data.setdefault('favorites', [])
                        data.setdefault('playback_progress', {})
                        data.setdefault('episode_cache', {})
                        return data
            else:
                # First time: create empty structure
                self.logger.info("podcast_data.json not found, creating new file")
                default_data = {
                    "subscriptions": [],
                    "favorites": [],
                    "playback_progress": {},
                    "episode_cache": {}
                }
                await self.save_data(default_data)
                return default_data

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON error in podcast_data.json: {e}")
            return {
                "subscriptions": [],
                "favorites": [],
                "playback_progress": {},
                "episode_cache": {}
            }
        except Exception as e:
            self.logger.error(f"Error loading podcast_data.json: {e}")
            return {
                "subscriptions": [],
                "favorites": [],
                "playback_progress": {},
                "episode_cache": {}
            }

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """
        Saves podcast_data.json with atomic write

        Args:
            data: Dict with subscriptions, favorites, playback_progress, episode_cache

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
            self.logger.error(f"Error saving podcast_data.json: {e}")
            return False

    async def add_subscription(self, podcast_uuid: str) -> bool:
        """
        Add podcast to subscriptions

        Args:
            podcast_uuid: Podcast series UUID

        Returns:
            True if successful
        """
        data = await self.load_data()

        if podcast_uuid not in data['subscriptions']:
            data['subscriptions'].append(podcast_uuid)
            return await self.save_data(data)

        return True

    async def remove_subscription(self, podcast_uuid: str) -> bool:
        """
        Remove podcast from subscriptions

        Args:
            podcast_uuid: Podcast series UUID

        Returns:
            True if successful
        """
        data = await self.load_data()

        if podcast_uuid in data['subscriptions']:
            data['subscriptions'].remove(podcast_uuid)
            return await self.save_data(data)

        return True

    async def get_subscriptions(self) -> List[str]:
        """
        Get all subscribed podcast UUIDs

        Returns:
            List of podcast UUIDs
        """
        data = await self.load_data()
        return data.get('subscriptions', [])

    async def add_favorite(self, episode_uuid: str) -> bool:
        """
        Add episode to favorites

        Args:
            episode_uuid: Episode UUID

        Returns:
            True if successful
        """
        data = await self.load_data()

        if episode_uuid not in data['favorites']:
            data['favorites'].append(episode_uuid)
            return await self.save_data(data)

        return True

    async def remove_favorite(self, episode_uuid: str) -> bool:
        """
        Remove episode from favorites

        Args:
            episode_uuid: Episode UUID

        Returns:
            True if successful
        """
        data = await self.load_data()

        if episode_uuid in data['favorites']:
            data['favorites'].remove(episode_uuid)
            return await self.save_data(data)

        return True

    async def get_favorites(self) -> List[str]:
        """
        Get all favorite episode UUIDs

        Returns:
            List of episode UUIDs
        """
        data = await self.load_data()
        return data.get('favorites', [])

    async def update_playback_progress(
        self,
        episode_uuid: str,
        position: int,
        duration: int
    ) -> bool:
        """
        Update playback progress for an episode

        Args:
            episode_uuid: Episode UUID
            position: Current position in seconds
            duration: Total duration in seconds

        Returns:
            True if successful
        """
        data = await self.load_data()

        data['playback_progress'][episode_uuid] = {
            'position': position,
            'duration': duration,
            'last_played': int(asyncio.get_event_loop().time())
        }

        return await self.save_data(data)

    async def get_playback_progress(self, episode_uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get playback progress for an episode

        Args:
            episode_uuid: Episode UUID

        Returns:
            Dict with position, duration, last_played or None
        """
        data = await self.load_data()
        return data.get('playback_progress', {}).get(episode_uuid)

    async def clear_playback_progress(self, episode_uuid: str) -> bool:
        """
        Clear playback progress for an episode (mark as completed)

        Args:
            episode_uuid: Episode UUID

        Returns:
            True if successful
        """
        data = await self.load_data()

        if episode_uuid in data.get('playback_progress', {}):
            del data['playback_progress'][episode_uuid]
            return await self.save_data(data)

        return True

    async def cache_episode(self, episode_uuid: str, episode_data: Dict[str, Any]) -> bool:
        """
        Cache episode data for offline access

        Args:
            episode_uuid: Episode UUID
            episode_data: Episode metadata

        Returns:
            True if successful
        """
        data = await self.load_data()
        data['episode_cache'][episode_uuid] = episode_data
        return await self.save_data(data)

    async def get_cached_episode(self, episode_uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get cached episode data

        Args:
            episode_uuid: Episode UUID

        Returns:
            Episode data or None
        """
        data = await self.load_data()
        return data.get('episode_cache', {}).get(episode_uuid)
