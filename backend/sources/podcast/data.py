# backend/sources/podcast/data.py
"""
Podcast data service for subscription and progress management.

This service manages:
- Subscriptions with full metadata
- Playback progress with episode context
- Episode and podcast cache
- User settings (safe_mode, playback_speed)

Data is persisted to /var/lib/milo/podcast_data.json
"""
import json
import os
import logging
import aiofiles
import asyncio
import time
from typing import Dict, Any, List, Optional

from backend.shared.decorators import handle_errors


class PodcastDataService:
    """
    Service for podcast data persistence.

    Manages:
    - Subscriptions with full metadata (name, image_url, children_hash, added_at, last_checked)
    - Playback progress with episode context (position, duration, last_played, episode/podcast info)
    - Episode and podcast cache
    - User settings (safe_mode, playback_speed)

    Note: Language/country settings are centralized in /var/lib/milo/settings.json
    """

    def __init__(self, state_machine=None):
        self._logger = logging.getLogger(__name__)
        self._data_file = '/var/lib/milo/podcast_data.json'
        self._file_lock = asyncio.Lock()
        self._state_machine = state_machine

    async def load_data(self) -> Dict[str, Any]:
        """Load podcast_data.json."""
        try:
            if os.path.exists(self._data_file):
                ensured_data = None
                needs_save = False

                async with self._file_lock:
                    async with aiofiles.open(self._data_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        ensured_data, needs_save = self._ensure_structure(data)

                # Save outside the lock to avoid deadlock
                if needs_save:
                    await self.save_data(ensured_data)

                return ensured_data
            else:
                self._logger.info("podcast_data.json not found, creating new file")
                default_data = self._get_default_structure()
                await self.save_data(default_data)
                return default_data

        except json.JSONDecodeError as e:
            self._logger.error(f"JSON error in podcast_data.json: {e}")
            return self._get_default_structure()
        except Exception as e:
            self._logger.error(f"Error loading podcast_data.json: {e}")
            return self._get_default_structure()

    def _get_default_structure(self) -> Dict[str, Any]:
        """Get default data structure."""
        return {
            "subscriptions": [],
            "playback_progress": {},
            "cache": {
                "episodes": {},
                "podcasts": {}
            },
            "settings": {
                "safe_mode": False,
                "playback_speed": 1.0
            }
        }

    def _ensure_structure(self, data: Dict[str, Any]):
        """Fill missing top-level keys with defaults. Returns (data, needs_save)."""
        defaults = self._get_default_structure()
        needs_save = False

        data.setdefault('subscriptions', defaults['subscriptions'])
        data.setdefault('playback_progress', defaults['playback_progress'])

        if 'cache' not in data:
            data['cache'] = defaults['cache']
            needs_save = True
        else:
            data['cache'].setdefault('episodes', {})
            data['cache'].setdefault('podcasts', {})

        if 'settings' not in data:
            data['settings'] = defaults['settings']
            needs_save = True
        else:
            for key, value in defaults['settings'].items():
                if key not in data['settings']:
                    data['settings'][key] = value
                    needs_save = True

        return data, needs_save

    @handle_errors(default=False)
    async def save_data(self, data: Dict[str, Any]) -> bool:
        """Save podcast_data.json with atomic write."""
        async with self._file_lock:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self._data_file), exist_ok=True)

            temp_file = self._data_file + '.tmp'

            async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                await f.write('\n')
                await f.flush()
                os.fsync(f.fileno())

            os.replace(temp_file, self._data_file)

        return True

    # ========== SUBSCRIPTIONS ==========

    async def add_subscription(
        self,
        podcast_uuid: str,
        name: str,
        image_url: str,
        children_hash: str = ""
    ) -> bool:
        """
        Add podcast to subscriptions with full metadata.

        Args:
            podcast_uuid: Podcast series UUID
            name: Podcast name
            image_url: Podcast image URL
            children_hash: Hash of episodes (for detecting new episodes)
        """
        data = await self.load_data()

        # Check if already subscribed
        existing = next(
            (s for s in data['subscriptions'] if s.get('uuid') == podcast_uuid),
            None
        )

        if existing:
            # Update metadata
            existing['name'] = name
            existing['image_url'] = image_url
            existing['children_hash'] = children_hash
            existing['last_checked'] = int(time.time())
        else:
            data['subscriptions'].append({
                'uuid': podcast_uuid,
                'name': name,
                'image_url': image_url,
                'children_hash': children_hash,
                'added_at': int(time.time()),
                'last_checked': int(time.time())
            })

        return await self.save_data(data)

    async def remove_subscription(self, podcast_uuid: str) -> bool:
        """Remove podcast from subscriptions."""
        data = await self.load_data()

        original_count = len(data['subscriptions'])
        data['subscriptions'] = [
            s for s in data['subscriptions']
            if s.get('uuid') != podcast_uuid
        ]

        if len(data['subscriptions']) != original_count:
            return await self.save_data(data)

        return True

    async def get_subscriptions(self) -> List[Dict[str, Any]]:
        """Get all subscriptions with full metadata."""
        data = await self.load_data()
        return data.get('subscriptions', [])

    async def get_subscription_uuids(self) -> List[str]:
        """Get just the UUIDs of subscribed podcasts."""
        subscriptions = await self.get_subscriptions()
        return [s['uuid'] for s in subscriptions if s.get('uuid')]

    async def is_subscribed(self, podcast_uuid: str) -> bool:
        """Check if podcast is subscribed."""
        subscriptions = await self.get_subscription_uuids()
        return podcast_uuid in subscriptions

    # ========== PLAYBACK PROGRESS ==========

    async def update_playback_progress(
        self,
        episode_uuid: str,
        position: int,
        duration: int,
        podcast_uuid: str = "",
        episode_name: str = "",
        podcast_name: str = "",
        image_url: str = ""
    ) -> bool:
        """
        Update playback progress with full metadata.

        Args:
            episode_uuid: Episode UUID
            position: Current position in seconds
            duration: Total duration in seconds
            podcast_uuid: Parent podcast UUID
            episode_name: Episode name
            podcast_name: Podcast name
            image_url: Episode or podcast image URL
        """
        data = await self.load_data()

        # Get existing entry to preserve metadata
        existing = data['playback_progress'].get(episode_uuid, {})

        # Mark as completed if within 30 seconds of end
        completed = position >= (duration - 30) if duration > 0 else False

        data['playback_progress'][episode_uuid] = {
            'position': position,
            'duration': duration,
            'last_played': int(time.time()),
            'completed': completed,
            'podcast_uuid': podcast_uuid or existing.get('podcast_uuid', ''),
            'episode_name': episode_name or existing.get('episode_name', ''),
            'podcast_name': podcast_name or existing.get('podcast_name', ''),
            'image_url': image_url or existing.get('image_url', '')
        }

        return await self.save_data(data)

    async def get_playback_progress(self, episode_uuid: str) -> Optional[Dict[str, Any]]:
        """Get playback progress for an episode."""
        data = await self.load_data()
        return data.get('playback_progress', {}).get(episode_uuid)

    async def get_in_progress_episodes(self) -> List[Dict[str, Any]]:
        """
        Get all episodes that are in progress (for queue view).

        Returns episodes where:
        - position > 0
        - position < (duration - 30)
        - not marked as completed
        """
        data = await self.load_data()
        in_progress = []

        for episode_uuid, progress in data.get('playback_progress', {}).items():
            position = progress.get('position', 0)
            duration = progress.get('duration', 0)
            completed = progress.get('completed', False)

            # Episode is in progress if:
            # - Has been started (position > 0)
            # - Not at the end (position < duration - 30)
            # - Not marked as completed
            if position > 0 and duration > 0 and not completed:
                if position < (duration - 30):
                    in_progress.append({
                        'episode_uuid': episode_uuid,
                        **progress
                    })

        # Sort by last_played (most recent first)
        in_progress.sort(key=lambda x: x.get('last_played', 0), reverse=True)

        return in_progress

    async def mark_episode_completed(self, episode_uuid: str) -> bool:
        """Mark an episode as completed."""
        data = await self.load_data()

        if episode_uuid in data.get('playback_progress', {}):
            data['playback_progress'][episode_uuid]['completed'] = True
            data['playback_progress'][episode_uuid]['last_played'] = int(time.time())
            return await self.save_data(data)

        return True

    async def clear_playback_progress(self, episode_uuid: str) -> bool:
        """Clear playback progress for an episode (remove from queue)."""
        data = await self.load_data()

        if episode_uuid in data.get('playback_progress', {}):
            del data['playback_progress'][episode_uuid]
            return await self.save_data(data)

        return True

    # ========== CACHE ==========

    async def cache_episode(
        self,
        episode_uuid: str,
        episode_data: Dict[str, Any]
    ) -> bool:
        """Cache episode data."""
        data = await self.load_data()
        data['cache']['episodes'][episode_uuid] = {
            'data': episode_data,
            'cached_at': int(time.time())
        }
        return await self.save_data(data)

    # ========== SETTINGS ==========

    async def get_podcast_settings(self) -> Dict[str, Any]:
        """Get podcast-specific settings."""
        data = await self.load_data()
        return data.get('settings', {})

    async def update_podcast_settings(self, settings: Dict[str, Any]) -> bool:
        """
        Update podcast settings.

        Args:
            settings: Dict with settings to update (partial update supported)
        """
        data = await self.load_data()

        # Update only provided settings
        for key, value in settings.items():
            if key in data['settings']:
                data['settings'][key] = value

        return await self.save_data(data)

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a single setting value."""
        settings = await self.get_podcast_settings()
        return settings.get(key, default)

    async def set_setting(self, key: str, value: Any) -> bool:
        """Set a single setting value."""
        return await self.update_podcast_settings({key: value})
