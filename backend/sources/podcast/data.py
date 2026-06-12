# backend/sources/podcast/data.py
"""
Podcast data service for subscription and progress management.

This service manages:
- Subscriptions with full metadata
- Playback progress with episode context
- User settings (playback_speed)

Data is persisted to /var/lib/milo/podcast_data.json — uses the
schema_version protocol (see CLAUDE.md §"Persistence & schema-version protocol").
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.shared.persistence import load_versioned_json, save_versioned_json

REQUIRED_TOP_LEVEL_KEYS = ("subscriptions", "playback_progress", "settings")


class PodcastDataService:
    """
    Service for podcast data persistence.

    Manages:
    - Subscriptions with full metadata (name, image_url, children_hash, added_at, last_checked)
    - Playback progress with episode context (position, duration, last_played, episode/podcast info)
    - User settings (playback_speed)

    Note: Language/country settings are centralized in /var/lib/milo/settings.json
    """

    SCHEMA_VERSION: int = 1

    def __init__(self, state_machine=None):
        self._logger = logging.getLogger("source.podcast.data")
        self._data_file: Path = Path('/var/lib/milo/podcast_data.json')
        self._file_lock = asyncio.Lock()
        self._state_machine = state_machine

    async def initialize(self) -> None:
        """Pre-load podcast_data.json so a schema mismatch surfaces at boot.

        Seeds defaults on fresh install. Raises SchemaVersionMismatch on
        version drift or RuntimeError on missing required keys; the handler
        in dependencies.py::init_async logs the banner and SystemExit(1)s.
        """
        async with self._file_lock:
            data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            await self.save_data(self._get_default_structure())
            return

        self._validate_required_keys(data)

    async def load_data(self) -> Dict[str, Any]:
        """Load podcast_data.json. Trusts shape (validated at boot in initialize())."""
        async with self._file_lock:
            data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            return self._get_default_structure()

        self._validate_required_keys(data)
        return data

    def _validate_required_keys(self, data: Dict[str, Any]) -> None:
        """Fail-loud if any expected top-level key is missing."""
        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in data]
        if missing:
            raise RuntimeError(
                f"podcast_data.json missing required keys: {missing} — "
                f"delete it to reset (rm {self._data_file})"
            )

    def _get_default_structure(self) -> Dict[str, Any]:
        """Get default data structure."""
        return {
            "subscriptions": [],
            "playback_progress": {},
            "settings": {
                "playback_speed": 1.0
            }
        }

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """Save podcast_data.json with atomic write (schema_version stamped automatically)."""
        async with self._file_lock:
            await save_versioned_json(self._data_file, data, self.SCHEMA_VERSION)
        return True

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast podcast event via state machine (WebSocket)."""
        if self._state_machine:
            await self._state_machine.broadcast_event("source", event_type, {**data, "source": "podcast"})

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
            subscription = existing
        else:
            subscription = {
                'uuid': podcast_uuid,
                'name': name,
                'image_url': image_url,
                'children_hash': children_hash,
                'added_at': int(time.time()),
                'last_checked': int(time.time())
            }
            data['subscriptions'].append(subscription)

        success = await self.save_data(data)

        if success:
            # WS payload: {"source": "podcast", "podcast": <subscription dict>}
            await self._broadcast_event("favorite_added", {"podcast": subscription})

        return success

    async def remove_subscription(self, podcast_uuid: str) -> bool:
        """Remove podcast from subscriptions."""
        data = await self.load_data()

        original_count = len(data['subscriptions'])
        data['subscriptions'] = [
            s for s in data['subscriptions']
            if s.get('uuid') != podcast_uuid
        ]

        if len(data['subscriptions']) != original_count:
            success = await self.save_data(data)

            if success:
                # WS payload: {"source": "podcast", "uuid": <podcast series uuid>}
                await self._broadcast_event("favorite_removed", {"uuid": podcast_uuid})

            return success

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
