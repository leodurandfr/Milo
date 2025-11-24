"""
API routes for the Podcast plugin
Complete implementation with discovery, search, playback, subscriptions, queue, and settings
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
import logging

from backend.config.container import container

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/podcast", tags=["podcast"])


# === Language/Country Mappings ===

# Map iTunes country codes to Taddy country enums
ITUNES_COUNTRY_TO_TADDY_COUNTRY = {
    'us': 'UNITED_STATES_OF_AMERICA',
    'fr': 'FRANCE',
    'de': 'GERMANY',
    'es': 'SPAIN',
    'it': 'ITALY',
    'pt': 'PORTUGAL',
    'cn': 'CHINA',
    'in': 'INDIA',
    'gb': 'UNITED_KINGDOM',
    'ca': 'CANADA',
    'au': 'AUSTRALIA',
    'mx': 'MEXICO',
    'br': 'BRAZIL',
    'jp': 'JAPAN',
    'kr': 'SOUTH_KOREA',
    'nl': 'NETHERLANDS',
    'se': 'SWEDEN',
    'no': 'NORWAY',
    'dk': 'DENMARK',
    'fi': 'FINLAND',
    'pl': 'POLAND',
    'ru': 'RUSSIA',
    'tr': 'TURKEY',
    'sa': 'SAUDI_ARABIA',
    'ae': 'UNITED_ARAB_EMIRATES',
    'za': 'SOUTH_AFRICA',
    'ar': 'ARGENTINA',
    'cl': 'CHILE',
    'co': 'COLOMBIA',
}


# === Pydantic Models ===

class PlayEpisodeRequest(BaseModel):
    episode_uuid: str
    position: Optional[int] = None  # Resume position


class SeekRequest(BaseModel):
    position: int


class SpeedRequest(BaseModel):
    speed: float


class SubscribeRequest(BaseModel):
    uuid: str
    name: str
    image_url: str
    children_hash: Optional[str] = ""


class ProgressRequest(BaseModel):
    position: int
    duration: int


class SettingsRequest(BaseModel):
    defaultCountry: Optional[str] = None
    defaultLanguage: Optional[str] = None
    safeMode: Optional[bool] = None
    playbackSpeed: Optional[float] = None


# === Discovery Routes ===

@router.get("/discover/popular")
async def get_popular_content(
    language: str = Query(None, description="Language filter (e.g., FRENCH)"),
    genres: str = Query(None, description="Comma-separated genre list"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
):
    """Get popular podcasts globally"""
    try:
        plugin = container.podcast_plugin()

        # Parse genres if provided
        genre_list = None
        if genres:
            genre_list = [g.strip() for g in genres.split(",")]

        result = await plugin.taddy_api.get_popular_content(
            language=language,
            genres=genre_list,
            page=page,
            limit=limit
        )

        # Enrich with subscription status
        subscriptions = await plugin.podcast_data_service.get_subscription_uuids()
        for podcast in result.get('results', []):
            podcast['is_subscribed'] = podcast.get('uuid') in subscriptions

        return result

    except Exception as e:
        import traceback
        logger.error(f"Error getting popular content: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover/top-charts/{country}")
async def get_top_charts(
    country: str,
    content_type: str = Query("PODCASTSERIES", description="PODCASTSERIES or PODCASTEPISODE"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
):
    """Get top charts by country"""
    try:
        plugin = container.podcast_plugin()

        result = await plugin.taddy_api.get_top_charts_by_country(
            country=country,
            content_type=content_type,
            page=page,
            limit=limit
        )

        # Enrich with subscription/progress status
        if content_type == "PODCASTSERIES":
            subscriptions = await plugin.podcast_data_service.get_subscription_uuids()
            for podcast in result.get('results', []):
                podcast['is_subscribed'] = podcast.get('uuid') in subscriptions
        else:
            for episode in result.get('results', []):
                progress = await plugin.podcast_data_service.get_playback_progress(episode.get('uuid'))
                if progress:
                    episode['playback_progress'] = progress

        return result

    except Exception as e:
        logger.error(f"Error getting top charts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover/genres")
async def get_top_by_genres(
    genres: str = Query(..., description="Comma-separated genre list"),
    country: str = Query(None, description="Country filter"),
    content_type: str = Query("PODCASTSERIES"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
):
    """Get top content by genres"""
    try:
        plugin = container.podcast_plugin()

        genre_list = [g.strip() for g in genres.split(",")]

        result = await plugin.taddy_api.get_top_charts_by_genres(
            genres=genre_list,
            country=country,
            content_type=content_type,
            page=page,
            limit=limit
        )

        # Enrich with subscription status
        if content_type == "PODCASTSERIES":
            subscriptions = await plugin.podcast_data_service.get_subscription_uuids()
            for podcast in result.get('results', []):
                podcast['is_subscribed'] = podcast.get('uuid') in subscriptions

        return result

    except Exception as e:
        logger.error(f"Error getting top by genres: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover/popular-episodes")
async def get_popular_episodes_by_genre(
    genres: str = Query(..., description="Comma-separated genre list"),
    language: str = Query("FRENCH", description="Language filter"),
    limit: int = Query(10, ge=1, le=25)
):
    """
    Get popular episodes filtered by genre and language.

    Uses Taddy's search() with sortBy: POPULARITY to find episodes
    from podcasts of the specified genre, sorted by popularity.
    """
    try:
        plugin = container.podcast_plugin()

        genre_list = [g.strip() for g in genres.split(",")]

        result = await plugin.taddy_api.get_popular_episodes_by_genre(
            genres=genre_list,
            language=language,
            limit=limit
        )

        return result

    except Exception as e:
        logger.error(f"Error getting popular episodes by genre: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover/by-genre")
async def get_content_by_genre(
    genre: str = Query(..., description="Genre (e.g., PODCASTSERIES_TECHNOLOGY)"),
    limit: int = Query(30, ge=1, le=200, description="Number of podcasts to return (default: 30, max: 200)")
):
    """
    Get top podcasts for a specific genre using user's language.

    This endpoint returns the EXACT same results as Apple Podcasts for the user's country.

    Process:
    - Retrieves top podcasts from iTunes RSS (Apple's official rankings by country+genre)
    - Enriches each podcast with Taddy UUID via lookup (by iTunes ID or name)
    - Uses the language setting from /var/lib/milo/settings.json

    Note: Top episodes are not available via public APIs (Apple uses a private API).
    Only top podcast series can be reliably retrieved.

    Returns:
        {
            "podcasts": [...],  # List of top podcasts from Apple Podcasts (with Taddy UUIDs)
            "language": "FRENCH",  # Taddy language enum
            "country": "fr"  # iTunes country code used
        }
    """
    try:
        plugin = container.podcast_plugin()
        settings_service = container.settings_service()

        # Get language from central Milo settings
        settings = await settings_service.load_settings()
        milo_language = settings.get('language', 'english')

        # Import the mapping function
        from backend.infrastructure.plugins.podcast.taddy_api import (
            map_milo_language_to_taddy,
            map_milo_language_to_itunes_country
        )

        taddy_language = map_milo_language_to_taddy(milo_language)
        itunes_country = map_milo_language_to_itunes_country(milo_language)

        # Get top podcasts from iTunes RSS (EXACT Apple Podcasts rankings)
        # This is the most reliable way to match what users see on podcasts.apple.com
        podcasts_result = await plugin.taddy_api.get_itunes_top_podcasts_by_genre(
            genre=genre,
            country_code=itunes_country,
            limit=limit
        )

        podcasts = podcasts_result.get('results', [])

        # Return iTunes RSS data directly without UUID enrichment
        # UUIDs will be fetched lazily when user clicks on a podcast (better performance)
        return {
            "podcasts": podcasts,
            "language": taddy_language,
            "country": itunes_country
        }

    except Exception as e:
        import traceback
        logger.error(f"Error getting content by genre: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lookup/itunes/{itunes_id}")
async def lookup_podcast_by_itunes_id(
    itunes_id: str,
    name: str = Query(None, description="Podcast name for fallback search")
):
    """
    Lookup Taddy UUID for a podcast using its iTunes ID

    This endpoint is used for lazy loading - when displaying podcasts from iTunes RSS,
    we don't fetch UUIDs upfront. Instead, we fetch them on-demand when user clicks.
    """
    try:
        plugin = container.podcast_plugin()

        # Lookup UUID using iTunes ID and name
        uuid = await plugin.taddy_api.lookup_podcast_uuid_by_itunes_id(
            itunes_id=itunes_id,
            podcast_name=name
        )

        if not uuid:
            raise HTTPException(status_code=404, detail=f"No podcast found for iTunes ID: {itunes_id}")

        return {"uuid": uuid, "itunes_id": itunes_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up podcast by iTunes ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Search Routes ===

@router.get("/search")
async def search_mixed(
    term: str = Query("", description="Search term (optional)"),
    genres: str = Query(None, description="Comma-separated genre list"),
    languages: str = Query(None, description="Comma-separated language list"),
    countries: str = Query(None, description="Comma-separated country list"),
    duration_min: int = Query(None, description="Min duration in seconds"),
    duration_max: int = Query(None, description="Max duration in seconds"),
    safe_mode: bool = Query(False),
    sort_by: str = Query("EXACTNESS", description="EXACTNESS or POPULARITY"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
):
    """Search for podcasts AND episodes simultaneously"""
    try:
        plugin = container.podcast_plugin()

        # Only return empty if BOTH term is empty AND no filters are active
        if not term and not genres and not duration_min and not duration_max:
            return {
                "podcasts": [],
                "episodes": [],
                "pagination": {
                    "podcasts": {"total": 0, "pages": 0},
                    "episodes": {"total": 0, "pages": 0}
                }
            }

        # Parse lists
        genre_list = [g.strip() for g in genres.split(",")] if genres else None
        language_list = [l.strip() for l in languages.split(",")] if languages else None
        country_list = [c.strip() for c in countries.split(",")] if countries else None

        result = await plugin.taddy_api.search_mixed(
            term=term,
            genres=genre_list,
            languages=language_list,
            countries=country_list,
            duration_min=duration_min,
            duration_max=duration_max,
            safe_mode=safe_mode,
            sort_by=sort_by,
            page=page,
            limit=limit
        )

        # Enrich podcasts with subscription status
        subscriptions = await plugin.podcast_data_service.get_subscription_uuids()
        for podcast in result.get('podcasts', []):
            podcast['is_subscribed'] = podcast.get('uuid') in subscriptions

        # Enrich episodes with progress
        for episode in result.get('episodes', []):
            progress = await plugin.podcast_data_service.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return result

    except Exception as e:
        logger.error(f"Error in mixed search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Content Routes ===

@router.get("/series/{uuid}")
async def get_podcast_series(
    uuid: str,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=25),
    sort_order: str = Query("LATEST", description="LATEST, OLDEST, or SEARCH")
):
    """Get podcast series details with episodes"""
    try:
        plugin = container.podcast_plugin()

        series = await plugin.taddy_api.get_podcast_series(
            uuid=uuid,
            episodes_page=page,
            episodes_limit=limit,
            sort_order=sort_order
        )

        if not series:
            raise HTTPException(status_code=404, detail="Podcast not found")

        # Add subscription status
        series['is_subscribed'] = await plugin.podcast_data_service.is_subscribed(uuid)

        # Add progress to episodes
        for episode in series.get('episodes', []):
            progress = await plugin.podcast_data_service.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return series

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting podcast series: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/episode/{uuid}")
async def get_episode(uuid: str):
    """Get episode details"""
    try:
        plugin = container.podcast_plugin()

        episode = await plugin.taddy_api.get_episode(uuid)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Add progress
        progress = await plugin.podcast_data_service.get_playback_progress(uuid)
        if progress:
            episode['playback_progress'] = progress

        return episode

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting episode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Playback Routes ===

@router.post("/play")
async def play_episode(request: PlayEpisodeRequest):
    """Play an episode"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.play_episode(request.episode_uuid)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to play episode")

        # If position specified, seek to it
        if request.position is not None and request.position > 0:
            await plugin.seek(request.position)

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error playing episode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pause")
async def pause_playback():
    """Pause playback"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.pause()
        return {"success": success}
    except Exception as e:
        logger.error(f"Error pausing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resume")
async def resume_playback():
    """Resume playback"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.resume()
        return {"success": success}
    except Exception as e:
        logger.error(f"Error resuming: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seek")
async def seek_playback(request: SeekRequest):
    """Seek to position"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.seek(request.position)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error seeking: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_playback():
    """Stop playback"""
    try:
        plugin = container.podcast_plugin()
        result = await plugin.handle_command("stop", {})
        return result
    except Exception as e:
        logger.error(f"Error stopping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speed")
async def set_speed(request: SpeedRequest):
    """Set playback speed (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.set_speed(request.speed)
        return {"success": success, "speed": plugin._playback_speed}
    except Exception as e:
        logger.error(f"Error setting speed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status():
    """Get current playback status"""
    try:
        plugin = container.podcast_plugin()
        return await plugin.get_status()
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Subscription Routes ===

@router.get("/subscriptions")
async def get_subscriptions():
    """Get all subscriptions with metadata"""
    try:
        plugin = container.podcast_plugin()
        subscriptions = await plugin.podcast_data_service.get_subscriptions()
        return {"subscriptions": subscriptions, "total": len(subscriptions)}
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/subscriptions")
async def add_subscription(request: SubscribeRequest):
    """Subscribe to a podcast with metadata"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.add_subscription(
            podcast_uuid=request.uuid,
            name=request.name,
            image_url=request.image_url,
            children_hash=request.children_hash
        )
        return {"success": success}
    except Exception as e:
        logger.error(f"Error subscribing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/subscriptions/{uuid}")
async def remove_subscription(uuid: str):
    """Unsubscribe from a podcast"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.remove_subscription(uuid)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error unsubscribing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subscriptions/latest-episodes")
async def get_latest_episodes_from_subscriptions(
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(50, ge=1, le=50)
):
    """Get latest episodes from all subscribed podcasts"""
    try:
        plugin = container.podcast_plugin()

        # Get subscription UUIDs
        uuids = await plugin.podcast_data_service.get_subscription_uuids()

        if not uuids:
            return {"results": [], "total": 0}

        result = await plugin.taddy_api.get_latest_episodes(
            podcast_uuids=uuids,
            page=page,
            limit=limit
        )

        # Add progress to episodes
        for episode in result.get('results', []):
            progress = await plugin.podcast_data_service.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return result

    except Exception as e:
        logger.error(f"Error getting latest episodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Queue Routes ===

@router.get("/queue")
async def get_queue():
    """Get in-progress episodes (queue)"""
    try:
        plugin = container.podcast_plugin()
        episodes = await plugin.podcast_data_service.get_in_progress_episodes()
        return {"episodes": episodes, "total": len(episodes)}
    except Exception as e:
        logger.error(f"Error getting queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/{episode_uuid}/complete")
async def mark_episode_complete(episode_uuid: str):
    """Mark episode as completed"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.mark_episode_completed(episode_uuid)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error marking complete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queue/{episode_uuid}")
async def remove_from_queue(episode_uuid: str):
    """Remove episode from queue"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.clear_playback_progress(episode_uuid)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error removing from queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Progress Routes ===

@router.get("/progress/{episode_uuid}")
async def get_progress(episode_uuid: str):
    """Get playback progress for an episode"""
    try:
        plugin = container.podcast_plugin()
        progress = await plugin.podcast_data_service.get_playback_progress(episode_uuid)
        return {"progress": progress}
    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/progress/{episode_uuid}")
async def update_progress(episode_uuid: str, request: ProgressRequest):
    """Update playback progress"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.update_playback_progress(
            episode_uuid=episode_uuid,
            position=request.position,
            duration=request.duration
        )
        return {"success": success}
    except Exception as e:
        logger.error(f"Error updating progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Settings Routes ===

@router.get("/settings")
async def get_settings():
    """Get podcast settings"""
    try:
        plugin = container.podcast_plugin()
        settings = await plugin.podcast_data_service.get_podcast_settings()
        return {"settings": settings}
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def update_settings(request: SettingsRequest):
    """Update podcast settings"""
    try:
        plugin = container.podcast_plugin()

        updates = {}
        if request.defaultCountry is not None:
            updates['defaultCountry'] = request.defaultCountry
        if request.defaultLanguage is not None:
            updates['defaultLanguage'] = request.defaultLanguage
        if request.safeMode is not None:
            updates['safeMode'] = request.safeMode
        if request.playbackSpeed is not None:
            updates['playbackSpeed'] = request.playbackSpeed

        success = await plugin.podcast_data_service.update_podcast_settings(updates)
        return {"success": success}

    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === API Monitoring ===

@router.get("/api-quota")
async def get_api_quota():
    """Get remaining API requests for current hour"""
    try:
        plugin = container.podcast_plugin()
        remaining = await plugin.taddy_api.get_api_requests_remaining()
        return {"remaining": remaining}
    except Exception as e:
        logger.error(f"Error getting API quota: {e}")
        raise HTTPException(status_code=500, detail=str(e))
