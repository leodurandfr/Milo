"""
API routes for the Podcast plugin
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
import logging

from backend.config.container import container
from backend.domain.audio_state import AudioSource


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/podcast", tags=["podcast"])


# === Pydantic models for validation ===

class PlayEpisodeRequest(BaseModel):
    """Request to play an episode"""
    episode_uuid: str


class SeekRequest(BaseModel):
    """Request to seek to position"""
    position: int


class SubscribeRequest(BaseModel):
    """Request to subscribe to a podcast"""
    podcast_uuid: str


class FavoriteEpisodeRequest(BaseModel):
    """Request to favorite an episode"""
    episode_uuid: str


# === Routes ===

@router.get("/search/podcasts")
async def search_podcasts(
    term: str = Query("", description="Search term"),
    sort_by: str = Query("EXACTNESS", description="Sort by EXACTNESS or POPULARITY"),
    limit: int = Query(25, ge=1, le=25, description="Max number of results per page (max 25)"),
    page: int = Query(1, ge=1, le=20, description="Page number (1-20)"),
    country: str = Query("", description="Country filter (Taddy enum, e.g., FRANCE)"),
    genre: str = Query("", description="Genre filter (Taddy enum, e.g., PODCASTSERIES_NEWS)"),
    language: str = Query("", description="Language filter (Taddy enum, e.g., FRENCH)")
):
    """
    Search podcasts

    Args:
        term: Search term
        sort_by: EXACTNESS or POPULARITY (default: EXACTNESS)
        limit: Max number of results per page (max 25, Taddy API constraint)
        page: Page number (1-20)
        country: Country filter (Taddy enum)
        genre: Genre filter (Taddy enum)
        language: Language filter (Taddy enum)

    Returns:
        Dict with results and total: {results: [...], total: int}
    """
    try:
        plugin = container.podcast_plugin()

        if not term:
            return {"results": [], "total": 0}

        result = await plugin.taddy_api.search(
            term=term,
            filter_type="PODCASTSERIES",
            sort_by=sort_by,
            limit=limit,
            page=page,
            filter_country=country or None,
            filter_genre=genre or None,
            filter_language=language or None
        )

        # Enrich with subscription status
        subscriptions = await plugin.podcast_data_service.get_subscriptions()
        for podcast in result['results']:
            podcast['is_subscribed'] = podcast['uuid'] in subscriptions

        return result

    except Exception as e:
        logger.error(f"Error searching podcasts: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching podcasts: {str(e)}")


@router.get("/search/episodes")
async def search_episodes(
    term: str = Query("", description="Search term"),
    sort_by: str = Query("EXACTNESS", description="Sort by EXACTNESS or POPULARITY"),
    limit: int = Query(25, ge=1, le=25, description="Max number of results per page (max 25)"),
    page: int = Query(1, ge=1, le=20, description="Page number (1-20)"),
    country: str = Query("", description="Country filter (Taddy enum, e.g., FRANCE)"),
    genre: str = Query("", description="Genre filter (Taddy enum, e.g., PODCASTSERIES_NEWS)"),
    language: str = Query("", description="Language filter (Taddy enum, e.g., FRENCH)")
):
    """
    Search episodes

    Args:
        term: Search term
        sort_by: EXACTNESS or POPULARITY (default: EXACTNESS)
        limit: Max number of results per page (max 25, Taddy API constraint)
        page: Page number (1-20)
        country: Country filter (Taddy enum)
        genre: Genre filter (Taddy enum)
        language: Language filter (Taddy enum)

    Returns:
        Dict with results and total: {results: [...], total: int}
    """
    try:
        plugin = container.podcast_plugin()

        if not term:
            return {"results": [], "total": 0}

        result = await plugin.taddy_api.search(
            term=term,
            filter_type="PODCASTEPISODE",
            sort_by=sort_by,
            limit=limit,
            page=page,
            filter_country=country or None,
            filter_genre=genre or None,
            filter_language=language or None
        )

        # Enrich with favorite status
        favorites = await plugin.podcast_data_service.get_favorites()
        for episode in result['results']:
            episode['is_favorite'] = episode['uuid'] in favorites

            # Add playback progress if available
            progress = await plugin.podcast_data_service.get_playback_progress(episode['uuid'])
            if progress:
                episode['playback_progress'] = progress

        return result

    except Exception as e:
        logger.error(f"Error searching episodes: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching episodes: {str(e)}")


@router.get("/series/{podcast_uuid}")
async def get_podcast_series(
    podcast_uuid: str,
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=50, description="Episodes per page")
):
    """
    Get podcast series details with episodes

    Args:
        podcast_uuid: Podcast series UUID
        page: Page number (default 1)
        limit: Episodes per page (1-50)

    Returns:
        Podcast series with episodes
    """
    try:
        plugin = container.podcast_plugin()

        series = await plugin.taddy_api.get_podcast_series(
            uuid=podcast_uuid,
            episodes_page=page,
            episodes_limit=limit
        )

        if not series:
            raise HTTPException(status_code=404, detail="Podcast not found")

        # Add subscription status
        subscriptions = await plugin.podcast_data_service.get_subscriptions()
        series['is_subscribed'] = podcast_uuid in subscriptions

        # Add favorite and progress status to episodes
        favorites = await plugin.podcast_data_service.get_favorites()
        for episode in series.get('episodes', []):
            episode['is_favorite'] = episode['uuid'] in favorites

            # Add playback progress
            progress = await plugin.podcast_data_service.get_playback_progress(episode['uuid'])
            if progress:
                episode['playback_progress'] = progress

        return series

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting podcast series: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting podcast series: {str(e)}")


@router.get("/episode/{episode_uuid}")
async def get_episode(episode_uuid: str):
    """
    Get episode details

    Args:
        episode_uuid: Episode UUID

    Returns:
        Episode details
    """
    try:
        plugin = container.podcast_plugin()

        episode = await plugin.taddy_api.get_episode(episode_uuid)

        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Add favorite status
        favorites = await plugin.podcast_data_service.get_favorites()
        episode['is_favorite'] = episode_uuid in favorites

        # Add playback progress
        progress = await plugin.podcast_data_service.get_playback_progress(episode_uuid)
        if progress:
            episode['playback_progress'] = progress

        return episode

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting episode: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting episode: {str(e)}")


@router.post("/play")
async def play_episode(request: PlayEpisodeRequest):
    """
    Play an episode

    Args:
        request: PlayEpisodeRequest with episode_uuid

    Returns:
        Success status
    """
    try:
        # Get plugin and play episode
        plugin = container.podcast_plugin()
        success = await plugin.play_episode(request.episode_uuid)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to play episode")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error playing episode: {e}")
        raise HTTPException(status_code=500, detail=f"Error playing episode: {str(e)}")


@router.post("/pause")
async def pause_playback():
    """Pause playback"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.pause()

        return {"success": success}

    except Exception as e:
        logger.error(f"Error pausing: {e}")
        raise HTTPException(status_code=500, detail=f"Error pausing: {str(e)}")


@router.post("/resume")
async def resume_playback():
    """Resume playback"""
    try:
        plugin = container.podcast_plugin()
        success = await plugin.resume()

        return {"success": success}

    except Exception as e:
        logger.error(f"Error resuming: {e}")
        raise HTTPException(status_code=500, detail=f"Error resuming: {str(e)}")


@router.post("/seek")
async def seek_playback(request: SeekRequest):
    """
    Seek to position

    Args:
        request: SeekRequest with position in seconds

    Returns:
        Success status
    """
    try:
        plugin = container.podcast_plugin()
        success = await plugin.seek(request.position)

        return {"success": success}

    except Exception as e:
        logger.error(f"Error seeking: {e}")
        raise HTTPException(status_code=500, detail=f"Error seeking: {str(e)}")


@router.post("/stop")
async def stop_playback():
    """Stop playback"""
    try:
        plugin = container.podcast_plugin()

        # Use handle_command to stop
        result = await plugin.handle_command("stop", {})

        return result

    except Exception as e:
        logger.error(f"Error stopping: {e}")
        raise HTTPException(status_code=500, detail=f"Error stopping: {str(e)}")


@router.get("/status")
async def get_status():
    """Get current playback status"""
    try:
        plugin = container.podcast_plugin()
        status = await plugin.get_status()

        return status

    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")


# === Subscriptions ===

@router.post("/subscribe")
async def subscribe_podcast(request: SubscribeRequest):
    """
    Subscribe to a podcast

    Args:
        request: SubscribeRequest with podcast_uuid

    Returns:
        Success status
    """
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.add_subscription(request.podcast_uuid)

        return {"success": success}

    except Exception as e:
        logger.error(f"Error subscribing: {e}")
        raise HTTPException(status_code=500, detail=f"Error subscribing: {str(e)}")


@router.post("/unsubscribe")
async def unsubscribe_podcast(request: SubscribeRequest):
    """
    Unsubscribe from a podcast

    Args:
        request: SubscribeRequest with podcast_uuid

    Returns:
        Success status
    """
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.remove_subscription(request.podcast_uuid)

        return {"success": success}

    except Exception as e:
        logger.error(f"Error unsubscribing: {e}")
        raise HTTPException(status_code=500, detail=f"Error unsubscribing: {str(e)}")


@router.get("/subscriptions")
async def get_subscriptions():
    """
    Get all subscribed podcasts

    Returns:
        List of podcasts
    """
    try:
        plugin = container.podcast_plugin()

        # Get subscription UUIDs
        subscription_uuids = await plugin.podcast_data_service.get_subscriptions()

        if not subscription_uuids:
            return {"podcasts": [], "total": 0}

        # Fetch podcast details for each subscription
        podcasts = []
        for uuid in subscription_uuids:
            try:
                series = await plugin.taddy_api.get_podcast_series(uuid, episodes_page=1, episodes_limit=5)
                if series:
                    series['is_subscribed'] = True
                    podcasts.append(series)
            except Exception as e:
                logger.warning(f"Error fetching subscription {uuid}: {e}")

        return {"podcasts": podcasts, "total": len(podcasts)}

    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting subscriptions: {str(e)}")


# === Favorites ===

@router.post("/favorites/add")
async def add_favorite(request: FavoriteEpisodeRequest):
    """
    Add episode to favorites

    Args:
        request: FavoriteEpisodeRequest with episode_uuid

    Returns:
        Success status
    """
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.add_favorite(request.episode_uuid)

        return {"success": success}

    except Exception as e:
        logger.error(f"Error adding favorite: {e}")
        raise HTTPException(status_code=500, detail=f"Error adding favorite: {str(e)}")


@router.post("/favorites/remove")
async def remove_favorite(request: FavoriteEpisodeRequest):
    """
    Remove episode from favorites

    Args:
        request: FavoriteEpisodeRequest with episode_uuid

    Returns:
        Success status
    """
    try:
        plugin = container.podcast_plugin()
        success = await plugin.podcast_data_service.remove_favorite(request.episode_uuid)

        return {"success": success}

    except Exception as e:
        logger.error(f"Error removing favorite: {e}")
        raise HTTPException(status_code=500, detail=f"Error removing favorite: {str(e)}")


@router.get("/favorites")
async def get_favorites():
    """
    Get all favorite episodes

    Returns:
        List of episodes
    """
    try:
        plugin = container.podcast_plugin()

        # Get favorite UUIDs
        favorite_uuids = await plugin.podcast_data_service.get_favorites()

        if not favorite_uuids:
            return {"episodes": [], "total": 0}

        # Fetch episode details for each favorite
        episodes = []
        for uuid in favorite_uuids:
            try:
                # Try to get from cache first
                episode = await plugin.podcast_data_service.get_cached_episode(uuid)

                # If not in cache, fetch from API
                if not episode:
                    episode = await plugin.taddy_api.get_episode(uuid)

                if episode:
                    episode['is_favorite'] = True

                    # Add playback progress
                    progress = await plugin.podcast_data_service.get_playback_progress(uuid)
                    if progress:
                        episode['playback_progress'] = progress

                    episodes.append(episode)

            except Exception as e:
                logger.warning(f"Error fetching favorite {uuid}: {e}")

        return {"episodes": episodes, "total": len(episodes)}

    except Exception as e:
        logger.error(f"Error getting favorites: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting favorites: {str(e)}")


# === Progress ===

@router.get("/progress")
async def get_all_progress():
    """
    Get playback progress for all episodes

    Returns:
        Dict of episode_uuid -> progress
    """
    try:
        plugin = container.podcast_plugin()
        data = await plugin.podcast_data_service.load_data()

        return {"progress": data.get('playback_progress', {})}

    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting progress: {str(e)}")
