# backend/features/podcast/routes.py
"""
FastAPI routes for Podcast feature.

Provides REST API for:
- Discovery (top charts, by genre)
- Search (podcasts and episodes)
- Content (series details, episode details)
- Playback (play, pause, resume, seek, stop, speed)
- Subscriptions (add, remove, list)
- Queue (in-progress episodes)
- Settings (podcast-specific settings)
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from backend.api.route_helpers import run_source_command
from typing import Dict, Any
import logging

from backend.api.source_dependency import make_source_dependency
from backend.features.podcast.models import (
    PlayEpisodeRequest,
    SeekRequest,
    SpeedRequest,
    SubscribeRequest,
    SettingsRequest
)
from backend.features.podcast.source import PodcastSource
from backend.features.podcast.taddy_api import (
    map_milo_language_to_taddy,
    map_milo_language_to_itunes_country,
    map_milo_language_to_taddy_country,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/podcast",
    tags=["podcast"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Podcast")


def setup_podcast_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


# === Status Route ===

@router.get("/status")
async def get_status(source: PodcastSource = Depends(get_source)) -> Dict[str, Any]:
    """Get current playback status."""
    try:
        status = await source.status()
        return {
            "status": "success",
            **status
        }
    except Exception as e:
        logger.error(f"Status error: {e}")
        return {
            "status": "error",
            "message": str(e),
            "service_active": False,
            "mpv_connected": False,
            "is_playing": False
        }


# === Discovery Routes ===

@router.get("/discover/top-charts")
async def get_top_charts(
    source: PodcastSource = Depends(get_source),
    content_type: str = Query("PODCASTSERIES", description="PODCASTSERIES or PODCASTEPISODE"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
) -> Dict[str, Any]:
    """Get top charts using user's language from settings."""
    try:
        from backend.dependencies import get_service
        settings_service = get_service("settings_service")
        settings = await settings_service.load_settings()
        milo_language = settings.get('language', 'english')
        itunes_country = map_milo_language_to_itunes_country(milo_language)
        taddy_country = map_milo_language_to_taddy_country(milo_language)

        result = await source.taddy_api.get_top_charts_by_country(
            country=taddy_country,
            content_type=content_type,
            page=page,
            limit=limit
        )

        # Enrich with subscription/progress status
        if content_type == "PODCASTSERIES":
            subscriptions = await source.podcast_data.get_subscription_uuids()
            for podcast in result.get('results', []):
                podcast['is_subscribed'] = podcast.get('uuid') in subscriptions
        else:
            for episode in result.get('results', []):
                progress = await source.podcast_data.get_playback_progress(episode.get('uuid'))
                if progress:
                    episode['playback_progress'] = progress

        result['country'] = itunes_country
        result['language'] = milo_language

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting top charts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover/by-genre")
async def get_content_by_genre(
    source: PodcastSource = Depends(get_source),
    genre: str = Query(..., description="Genre (e.g., PODCASTSERIES_TECHNOLOGY)"),
    limit: int = Query(30, ge=1, le=200)
) -> Dict[str, Any]:
    """Get top podcasts for a specific genre using user's language."""
    try:
        from backend.dependencies import get_service
        settings_service = get_service("settings_service")
        settings = await settings_service.load_settings()
        milo_language = settings.get('language', 'english')

        taddy_language = map_milo_language_to_taddy(milo_language)
        itunes_country = map_milo_language_to_itunes_country(milo_language)

        podcasts_result = await source.taddy_api.get_itunes_top_podcasts_by_genre(
            genre=genre,
            country_code=itunes_country,
            limit=limit
        )

        podcasts = podcasts_result.get('results', [])

        return {
            "podcasts": podcasts,
            "language": taddy_language,
            "country": itunes_country
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting content by genre: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lookup/itunes/{itunes_id}")
async def lookup_podcast_by_itunes_id(
    itunes_id: str,
    source: PodcastSource = Depends(get_source),
    name: str = Query(None, description="Podcast name for fallback search")
) -> Dict[str, Any]:
    """Lookup Taddy UUID for a podcast using its iTunes ID."""
    try:
        uuid = await source.taddy_api.lookup_podcast_uuid_by_itunes_id(
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
    source: PodcastSource = Depends(get_source),
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
) -> Dict[str, Any]:
    """Search for podcasts AND episodes simultaneously."""
    try:
        # Only return empty if BOTH term is empty AND no filters are active
        if not term and not genres and not languages and not duration_min and not duration_max:
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

        # Note: Do NOT use published_after filter - it breaks podcast search relevance
        # The Taddy API returns random podcasts instead of matching the search term
        result = await source.taddy_api.search_mixed(
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
        subscriptions = await source.podcast_data.get_subscription_uuids()
        for podcast in result.get('podcasts', []):
            podcast['is_subscribed'] = podcast.get('uuid') in subscriptions

        # Enrich episodes with progress
        for episode in result.get('episodes', []):
            progress = await source.podcast_data.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in mixed search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Content Routes ===

@router.get("/series/{uuid}")
async def get_podcast_series(
    uuid: str,
    source: PodcastSource = Depends(get_source),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=25),
    sort_order: str = Query("LATEST", description="LATEST, OLDEST, or SEARCH")
) -> Dict[str, Any]:
    """Get podcast series details with episodes."""
    try:
        series = await source.taddy_api.get_podcast_series(
            uuid=uuid,
            episodes_page=page,
            episodes_limit=limit,
            sort_order=sort_order
        )

        if not series:
            raise HTTPException(status_code=404, detail="Podcast not found")

        # Add subscription status
        series['is_subscribed'] = await source.podcast_data.is_subscribed(uuid)

        # Add progress to episodes
        for episode in series.get('episodes', []):
            progress = await source.podcast_data.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return series

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting podcast series: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/episode/{uuid}")
async def get_episode(
    uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get episode details."""
    try:
        episode = await source.taddy_api.get_episode(uuid)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Add progress
        progress = await source.podcast_data.get_playback_progress(uuid)
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
async def play_episode(
    request: PlayEpisodeRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Play an episode."""
    result = await run_source_command(
        source, "play_episode", {"episode_uuid": request.episode_uuid}, "Play"
    )

    # If position specified, seek to it
    if request.position is not None and request.position > 0:
        await run_source_command(
            source, "seek", {"position": request.position}, "Seek"
        )

    return result


@router.post("/pause")
async def pause_playback(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Pause playback."""
    return await run_source_command(source, "pause", {}, "Pause")


@router.post("/resume")
async def resume_playback(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Resume playback."""
    return await run_source_command(source, "resume", {}, "Resume")


@router.post("/seek")
async def seek_playback(
    request: SeekRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Seek to position."""
    return await run_source_command(source, "seek", {"position": request.position}, "Seek")


@router.post("/stop")
async def stop_playback(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Stop playback."""
    return await run_source_command(source, "stop", {}, "Stop")


@router.post("/speed")
async def set_speed(
    request: SpeedRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Set playback speed (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)."""
    return await run_source_command(source, "set_speed", {"speed": request.speed}, "Speed")


# === Subscription Routes ===

@router.get("/subscriptions")
async def get_subscriptions(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get all subscriptions with metadata."""
    try:
        subscriptions = await source.podcast_data.get_subscriptions()
        return {"subscriptions": subscriptions, "total": len(subscriptions)}
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/subscriptions")
async def add_subscription(
    request: SubscribeRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Subscribe to a podcast with metadata."""
    try:
        success = await source.podcast_data.add_subscription(
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
async def remove_subscription(
    uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Unsubscribe from a podcast."""
    try:
        success = await source.podcast_data.remove_subscription(uuid)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error unsubscribing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subscriptions/latest-episodes")
async def get_latest_episodes_from_subscriptions(
    source: PodcastSource = Depends(get_source),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(50, ge=1, le=50)
) -> Dict[str, Any]:
    """Get latest episodes from all subscribed podcasts."""
    try:
        # Get subscription UUIDs
        uuids = await source.podcast_data.get_subscription_uuids()

        if not uuids:
            return {"results": [], "total": 0}

        result = await source.taddy_api.get_latest_episodes(
            podcast_uuids=uuids,
            page=page,
            limit=limit
        )

        # Add progress to episodes
        for episode in result.get('results', []):
            progress = await source.podcast_data.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return result

    except Exception as e:
        logger.error(f"Error getting latest episodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Queue Routes ===

@router.get("/queue")
async def get_queue(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get in-progress episodes (queue)."""
    try:
        episodes = await source.podcast_data.get_in_progress_episodes()
        return {"episodes": episodes, "total": len(episodes)}
    except Exception as e:
        logger.error(f"Error getting queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/queue/{episode_uuid}/complete")
async def mark_episode_complete(
    episode_uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Mark episode as completed."""
    try:
        success = await source.podcast_data.mark_episode_completed(episode_uuid)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error marking complete: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Settings Routes ===

@router.get("/settings")
async def get_settings(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get podcast settings."""
    try:
        settings = await source.podcast_data.get_podcast_settings()
        return {"settings": settings}
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def update_settings(
    request: SettingsRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Update podcast settings."""
    try:
        updates = {}
        if request.safe_mode is not None:
            updates['safe_mode'] = request.safe_mode
        if request.playback_speed is not None:
            updates['playback_speed'] = request.playback_speed

        success = await source.podcast_data.update_podcast_settings(updates)
        return {"success": success}

    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))
