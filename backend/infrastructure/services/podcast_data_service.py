"""
Service to manage podcast_data.json with complete metadata
"""
import json
import os
import logging
import aiofiles
import asyncio
import time
from typing import Dict, Any, List, Optional


class PodcastDataService:
    """
    Service for podcast_data.json

    Manages:
    - Subscriptions with full metadata (name, imageUrl, childrenHash, addedAt, lastChecked)
    - Playback progress with episode context (position, duration, lastPlayed, episode/podcast info)
    - Episode and podcast cache
    - User settings (safeMode, playbackSpeed)

    Note: Language/country settings are centralized in /var/lib/milo/settings.json
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.data_file = '/var/lib/milo/podcast_data.json'
        self._file_lock = asyncio.Lock()

    async def load_data(self) -> Dict[str, Any]:
        """Load podcast_data.json"""
        try:
            if os.path.exists(self.data_file):
                ensured_data = None
                needs_migration = False

                async with self._file_lock:
                    async with aiofiles.open(self.data_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        ensured_data, needs_migration = self._ensure_structure(data)

                # Save outside the lock to avoid deadlock
                if needs_migration:
                    self.logger.info("Saving migrated data to disk")
                    await self.save_data(ensured_data)

                return ensured_data
            else:
                self.logger.info("podcast_data.json not found, creating new file")
                default_data, _ = self._get_default_structure()
                await self.save_data(default_data)
                return default_data

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON error in podcast_data.json: {e}")
            data, _ = self._get_default_structure()
            return data
        except Exception as e:
            self.logger.error(f"Error loading podcast_data.json: {e}")
            data, _ = self._get_default_structure()
            return data

    def _get_default_structure(self):
        """Get default data structure. Returns (data, needs_migration)"""
        return {
            "subscriptions": [],
            "playback_progress": {},
            "cache": {
                "episodes": {},
                "podcasts": {}
            },
            "settings": {
                # Note: Language/country are centralized in /var/lib/milo/settings.json
                "safeMode": False,
                "playbackSpeed": 1.0
            }
        }, False

    def _ensure_structure(self, data: Dict[str, Any]):
        """Ensure all required keys exist in data. Returns (data, needs_migration)"""
        defaults, _ = self._get_default_structure()
        needs_migration = False

        data.setdefault('subscriptions', defaults['subscriptions'])
        data.setdefault('playback_progress', defaults['playback_progress'])

        # Migrate old subscriptions format (list of strings) to new format (list of dicts)
        if 'subscriptions' in data and isinstance(data['subscriptions'], list):
            migrated_subscriptions = []
            found_old_format = False

            for sub in data['subscriptions']:
                if isinstance(sub, str):
                    # Old format: just UUID string - convert to new format with minimal data
                    found_old_format = True
                    migrated_subscriptions.append({
                        'uuid': sub,
                        'name': 'Unknown Podcast',  # Will be updated when user views it
                        'imageUrl': '',
                        'childrenHash': '',
                        'addedAt': int(time.time()),
                        'lastChecked': 0
                    })
                elif isinstance(sub, dict):
                    # New format: already a dict
                    migrated_subscriptions.append(sub)

            if found_old_format:
                needs_migration = True
                self.logger.info(f"Migrated {len([s for s in data['subscriptions'] if isinstance(s, str)])} subscriptions to new format")
                data['subscriptions'] = migrated_subscriptions

        # Migrate cache structure if needed
        if 'cache' not in data:
            data['cache'] = defaults['cache']
            needs_migration = True
            # Migrate old episode_cache
            if 'episode_cache' in data:
                data['cache']['episodes'] = data.pop('episode_cache')
        else:
            data['cache'].setdefault('episodes', {})
            data['cache'].setdefault('podcasts', {})

        # Ensure settings exist
        if 'settings' not in data:
            data['settings'] = defaults['settings']
            needs_migration = True
        else:
            for key, value in defaults['settings'].items():
                if key not in data['settings']:
                    data['settings'][key] = value
                    needs_migration = True

        # Remove old favorites field if exists (no longer used)
        if 'favorites' in data:
            data.pop('favorites', None)
            needs_migration = True

        return data, needs_migration

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """Save podcast_data.json with atomic write"""
        try:
            async with self._file_lock:
                # Ensure directory exists
                os.makedirs(os.path.dirname(self.data_file), exist_ok=True)

                temp_file = self.data_file + '.tmp'

                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_file, self.data_file)

            return True

        except Exception as e:
            self.logger.error(f"Error saving podcast_data.json: {e}")
            return False

    # ========== SUBSCRIPTIONS ==========

    async def add_subscription(
        self,
        podcast_uuid: str,
        name: str,
        image_url: str,
        children_hash: str = ""
    ) -> bool:
        """
        Add podcast to subscriptions with full metadata

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
            existing['imageUrl'] = image_url
            existing['childrenHash'] = children_hash
            existing['lastChecked'] = int(time.time())
        else:
            data['subscriptions'].append({
                'uuid': podcast_uuid,
                'name': name,
                'imageUrl': image_url,
                'childrenHash': children_hash,
                'addedAt': int(time.time()),
                'lastChecked': int(time.time())
            })

        return await self.save_data(data)

    async def remove_subscription(self, podcast_uuid: str) -> bool:
        """Remove podcast from subscriptions"""
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
        """Get all subscriptions with full metadata"""
        data = await self.load_data()
        return data.get('subscriptions', [])

    async def get_subscription_uuids(self) -> List[str]:
        """Get just the UUIDs of subscribed podcasts"""
        subscriptions = await self.get_subscriptions()
        # Handle both old format (list of strings) and new format (list of dicts)
        result = []
        for s in subscriptions:
            if isinstance(s, str):
                result.append(s)  # Old format: direct UUID string
            elif isinstance(s, dict) and s.get('uuid'):
                result.append(s['uuid'])  # New format: dict with uuid key
        return result

    async def is_subscribed(self, podcast_uuid: str) -> bool:
        """Check if podcast is subscribed"""
        subscriptions = await self.get_subscription_uuids()
        return podcast_uuid in subscriptions

    async def update_subscription_hash(
        self,
        podcast_uuid: str,
        new_hash: str
    ) -> bool:
        """
        Update children_hash for a subscription (for detecting new episodes)

        Args:
            podcast_uuid: Podcast series UUID
            new_hash: New children_hash from API
        """
        data = await self.load_data()

        for subscription in data['subscriptions']:
            if subscription.get('uuid') == podcast_uuid:
                subscription['childrenHash'] = new_hash
                subscription['lastChecked'] = int(time.time())
                return await self.save_data(data)

        return False

    async def check_new_episodes(
        self,
        podcast_uuid: str,
        current_hash: str
    ) -> bool:
        """
        Check if podcast has new episodes by comparing hash

        Args:
            podcast_uuid: Podcast series UUID
            current_hash: Current childrenHash from API

        Returns:
            True if hash is different (new episodes available)
        """
        subscriptions = await self.get_subscriptions()

        for subscription in subscriptions:
            if subscription.get('uuid') == podcast_uuid:
                stored_hash = subscription.get('childrenHash', '')
                return stored_hash != '' and stored_hash != current_hash

        return False

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
        Update playback progress with full metadata

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
            'lastPlayed': int(time.time()),
            'completed': completed,
            'podcastUuid': podcast_uuid or existing.get('podcastUuid', ''),
            'episodeName': episode_name or existing.get('episodeName', ''),
            'podcastName': podcast_name or existing.get('podcastName', ''),
            'imageUrl': image_url or existing.get('imageUrl', '')
        }

        return await self.save_data(data)

    async def get_playback_progress(self, episode_uuid: str) -> Optional[Dict[str, Any]]:
        """Get playback progress for an episode"""
        data = await self.load_data()
        return data.get('playback_progress', {}).get(episode_uuid)

    async def get_in_progress_episodes(self) -> List[Dict[str, Any]]:
        """
        Get all episodes that are in progress (for queue view)

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
                        'episodeUuid': episode_uuid,
                        **progress
                    })

        # Sort by lastPlayed (most recent first)
        in_progress.sort(key=lambda x: x.get('lastPlayed', 0), reverse=True)

        return in_progress

    async def mark_episode_completed(self, episode_uuid: str) -> bool:
        """Mark an episode as completed"""
        data = await self.load_data()

        if episode_uuid in data.get('playback_progress', {}):
            data['playback_progress'][episode_uuid]['completed'] = True
            data['playback_progress'][episode_uuid]['lastPlayed'] = int(time.time())
            return await self.save_data(data)

        return True

    async def clear_playback_progress(self, episode_uuid: str) -> bool:
        """Clear playback progress for an episode (remove from queue)"""
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
        """Cache episode data"""
        data = await self.load_data()
        data['cache']['episodes'][episode_uuid] = {
            'data': episode_data,
            'cachedAt': int(time.time())
        }
        return await self.save_data(data)

    async def get_cached_episode(self, episode_uuid: str) -> Optional[Dict[str, Any]]:
        """Get cached episode data"""
        data = await self.load_data()
        cached = data.get('cache', {}).get('episodes', {}).get(episode_uuid)
        if cached:
            return cached.get('data')
        return None

    async def cache_podcast(
        self,
        podcast_uuid: str,
        podcast_data: Dict[str, Any]
    ) -> bool:
        """Cache podcast data"""
        data = await self.load_data()
        data['cache']['podcasts'][podcast_uuid] = {
            'data': podcast_data,
            'cachedAt': int(time.time())
        }
        return await self.save_data(data)

    async def get_cached_podcast(self, podcast_uuid: str) -> Optional[Dict[str, Any]]:
        """Get cached podcast data"""
        data = await self.load_data()
        cached = data.get('cache', {}).get('podcasts', {}).get(podcast_uuid)
        if cached:
            return cached.get('data')
        return None

    async def clean_old_cache(self, max_age_seconds: int = 7200) -> int:
        """
        Remove cached entries older than max_age_seconds

        Args:
            max_age_seconds: Maximum age in seconds (default 2 hours)

        Returns:
            Number of entries removed
        """
        data = await self.load_data()
        now = int(time.time())
        removed = 0

        # Clean episode cache
        episodes_to_remove = []
        for episode_uuid, cached in data['cache']['episodes'].items():
            if now - cached.get('cachedAt', 0) > max_age_seconds:
                episodes_to_remove.append(episode_uuid)

        for uuid in episodes_to_remove:
            del data['cache']['episodes'][uuid]
            removed += 1

        # Clean podcast cache
        podcasts_to_remove = []
        for podcast_uuid, cached in data['cache']['podcasts'].items():
            if now - cached.get('cachedAt', 0) > max_age_seconds:
                podcasts_to_remove.append(podcast_uuid)

        for uuid in podcasts_to_remove:
            del data['cache']['podcasts'][uuid]
            removed += 1

        if removed > 0:
            await self.save_data(data)
            self.logger.info(f"Cleaned {removed} old cache entries")

        return removed

    # ========== SETTINGS ==========

    async def get_podcast_settings(self) -> Dict[str, Any]:
        """Get podcast-specific settings"""
        data = await self.load_data()
        return data.get('settings', {})

    async def update_podcast_settings(self, settings: Dict[str, Any]) -> bool:
        """
        Update podcast settings

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
        """Get a single setting value"""
        settings = await self.get_podcast_settings()
        return settings.get(key, default)

    async def set_setting(self, key: str, value: Any) -> bool:
        """Set a single setting value"""
        return await self.update_podcast_settings({key: value})
