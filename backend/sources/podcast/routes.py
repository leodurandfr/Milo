# backend/sources/podcast/routes.py
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
from backend.api.route_helpers import api_error_handler, run_source_command
from typing import Dict, Any
import logging

from backend.api.source_dependency import make_source_dependency
from backend.sources.podcast.models import (
    PlayEpisodeRequest,
    SpeedRequest,
    SubscribeRequest,
    SettingsRequest
)
from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.taddy_api import (
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


# === Discovery Routes ===

@router.get("/discover/top-charts")
async def get_top_charts(
    source: PodcastSource = Depends(get_source),
    content_type: str = Query("PODCASTSERIES", description="PODCASTSERIES or PODCASTEPISODE"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
) -> Dict[str, Any]:
    """Get top charts using user's language from settings."""
    async with api_error_handler("Error getting top charts", logger):
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


@router.get("/discover/by-genre")
async def get_content_by_genre(
    source: PodcastSource = Depends(get_source),
    genre: str = Query(..., description="Genre (e.g., PODCASTSERIES_TECHNOLOGY)"),
    limit: int = Query(30, ge=1, le=200)
) -> Dict[str, Any]:
    """Get top podcasts for a specific genre using user's language."""
    async with api_error_handler("Error getting content by genre", logger):
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

        response = {
            "podcasts": podcasts,
            "language": taddy_language,
            "country": itunes_country
        }
        if podcasts_result.get("network_error"):
            response["network_error"] = True
        return response


@router.get("/lookup/itunes/{itunes_id}")
async def lookup_podcast_by_itunes_id(
    itunes_id: str,
    source: PodcastSource = Depends(get_source),
    name: str = Query(None, description="Podcast name for fallback lookup"),
    artist: str = Query(None, description="Podcast author/publisher to disambiguate same-title homonyms")
) -> Dict[str, Any]:
    """Lookup Taddy UUID for a podcast using its iTunes ID."""
    async with api_error_handler("Error looking up podcast by iTunes ID", logger):
        uuid = await source.taddy_api.lookup_podcast_uuid_by_itunes_id(
            itunes_id=itunes_id,
            podcast_name=name,
            podcast_artist=artist
        )

        if not uuid:
            logger.error("No podcast found for iTunes ID: %s", itunes_id)
            raise HTTPException(status_code=404, detail=f"No podcast found for iTunes ID: {itunes_id}")

        return {"uuid": uuid, "itunes_id": itunes_id}


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
    sort_by: str = Query("EXACTNESS", description="EXACTNESS or POPULARITY"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
) -> Dict[str, Any]:
    """Search for podcasts AND episodes simultaneously."""
    async with api_error_handler("Error in mixed search", logger):
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
        language_list = [lang.strip() for lang in languages.split(",")] if languages else None
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
    async with api_error_handler("Error getting podcast series", logger):
        series = await source.taddy_api.get_podcast_series(
            uuid=uuid,
            episodes_page=page,
            episodes_limit=limit,
            sort_order=sort_order
        )

        if not series:
            logger.error("Podcast not found: %s", uuid)
            raise HTTPException(status_code=404, detail="Podcast not found")

        # Add subscription status
        series['is_subscribed'] = await source.podcast_data.is_subscribed(uuid)

        # Add progress to episodes
        for episode in series.get('episodes', []):
            progress = await source.podcast_data.get_playback_progress(episode.get('uuid'))
            if progress:
                episode['playback_progress'] = progress

        return series


@router.get("/episode/{uuid}")
async def get_episode(
    uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get episode details."""
    async with api_error_handler("Error getting episode", logger):
        episode = await source.taddy_api.get_episode(uuid)
        if not episode:
            logger.error("Episode not found: %s", uuid)
            raise HTTPException(status_code=404, detail="Episode not found")

        # Add progress
        progress = await source.podcast_data.get_playback_progress(uuid)
        if progress:
            episode['playback_progress'] = progress

        return episode


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


@router.post("/stop")
async def stop_playback(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Stop playback."""
    return await run_source_command(source, "stop", {}, "Stop")


@router.get("/playback-speeds")
async def get_playback_speeds() -> Dict[str, Any]:
    """Return the canonical list of valid playback speeds."""
    from backend.sources.podcast.source import VALID_PLAYBACK_SPEEDS
    return {"status": "success", "speeds": VALID_PLAYBACK_SPEEDS}


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
    async with api_error_handler("Error getting subscriptions", logger):
        subscriptions = await source.podcast_data.get_subscriptions()
        return {"subscriptions": subscriptions, "total": len(subscriptions)}


@router.post("/subscriptions")
async def add_subscription(
    request: SubscribeRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Subscribe to a podcast with metadata."""
    async with api_error_handler("Error subscribing", logger):
        success = await source.podcast_data.add_subscription(
            podcast_uuid=request.uuid,
            name=request.name,
            image_url=request.image_url,
            children_hash=request.children_hash
        )
        return {"success": success}


@router.delete("/subscriptions/{uuid}")
async def remove_subscription(
    uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Unsubscribe from a podcast."""
    async with api_error_handler("Error unsubscribing", logger):
        success = await source.podcast_data.remove_subscription(uuid)
        return {"success": success}


@router.get("/subscriptions/latest-episodes")
async def get_latest_episodes_from_subscriptions(
    source: PodcastSource = Depends(get_source),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(50, ge=1, le=50)
) -> Dict[str, Any]:
    """Get latest episodes from all subscribed podcasts."""
    async with api_error_handler("Error getting latest episodes", logger):
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


# === Queue Routes ===

@router.get("/queue")
async def get_queue(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get in-progress episodes (queue)."""
    async with api_error_handler("Error getting queue", logger):
        episodes = await source.podcast_data.get_in_progress_episodes()
        return {"episodes": episodes, "total": len(episodes)}


@router.post("/queue/{episode_uuid}/complete")
async def mark_episode_complete(
    episode_uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Mark episode as completed."""
    async with api_error_handler("Error marking complete", logger):
        success = await source.podcast_data.mark_episode_completed(episode_uuid)
        return {"success": success}


# === Settings Routes ===

@router.get("/settings")
async def get_settings(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get podcast settings."""
    async with api_error_handler("Error getting settings", logger):
        settings = await source.podcast_data.get_podcast_settings()
        return {"settings": settings}


@router.post("/settings")
async def update_settings(
    request: SettingsRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Update podcast settings."""
    async with api_error_handler("Error updating settings", logger):
        updates = {}
        if request.playback_speed is not None:
            updates['playback_speed'] = request.playback_speed

        success = await source.podcast_data.update_podcast_settings(updates)
        return {"success": success}
