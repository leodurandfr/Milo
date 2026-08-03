# backend/sources/podcast/routes.py
"""
FastAPI routes for Podcast feature.

Provides REST API for:
- Discovery (top charts, by genre — iTunes RSS, exact Apple Podcasts order)
- Search (podcasts only — Podcast Index has no cross-podcast episode search)
- Content (series details, episode details)
- Playback (play, pause, resume, speed)
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
    SubscribeRequest,
)
from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.podcastindex_api import map_milo_language_to_itunes_country

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
    limit: int = Query(25, ge=1, le=200)
) -> Dict[str, Any]:
    """Get Apple Podcasts top charts (iTunes RSS, podcasts-only) using user's language."""
    async with api_error_handler("Error getting top charts", logger):
        from backend.dependencies import get_service
        settings_service = get_service("settings_service")
        settings = await settings_service.load_settings()
        milo_language = settings.get('language', 'english')
        itunes_country = map_milo_language_to_itunes_country(milo_language)

        result = await source.podcast_api.get_itunes_top_podcasts(
            country_code=itunes_country,
            limit=limit
        )

        # Enrich with subscription status (uuid is None for unresolved iTunes
        # entries — those simply stay unsubscribed-looking, like /by-genre)
        subscriptions = await source.podcast_data.get_subscription_uuids()
        for podcast in result.get('results', []):
            podcast['is_subscribed'] = podcast.get('uuid') in subscriptions

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
        itunes_country = map_milo_language_to_itunes_country(milo_language)

        podcasts_result = await source.podcast_api.get_itunes_top_podcasts_by_genre(
            genre=genre,
            country_code=itunes_country,
            limit=limit
        )

        podcasts = podcasts_result.get('results', [])

        response = {
            "podcasts": podcasts,
            "language": milo_language,
            "country": itunes_country
        }
        if podcasts_result.get("api_error"):
            response["api_error"] = True
        return response


@router.get("/lookup/itunes/{itunes_id}")
async def lookup_podcast_by_itunes_id(
    itunes_id: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Lookup the Podcast Index feedId for a podcast using its iTunes ID."""
    async with api_error_handler("Error looking up podcast by iTunes ID", logger):
        uuid = await source.podcast_api.lookup_by_itunes_id(itunes_id)

        if not uuid:
            logger.error("No podcast found for iTunes ID: %s", itunes_id)
            raise HTTPException(status_code=404, detail=f"No podcast found for iTunes ID: {itunes_id}")

        return {"uuid": uuid, "itunes_id": itunes_id}


# === Search Routes ===

@router.get("/search")
async def search_podcasts(
    source: PodcastSource = Depends(get_source),
    term: str = Query("", description="Search term"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
) -> Dict[str, Any]:
    """Search for podcasts (feeds-only; iTunes-backed, resolved to Podcast Index on open)."""
    async with api_error_handler("Error in podcast search", logger):
        empty = {
            "podcasts": [],
            "pagination": {"podcasts": {"total": 0, "pages": 0}},
        }
        if not term:
            return empty

        from backend.dependencies import get_service
        settings_service = get_service("settings_service")
        settings = await settings_service.load_settings()
        milo_language = settings.get('language', 'english')
        itunes_country = map_milo_language_to_itunes_country(milo_language)

        result = await source.podcast_api.search_podcasts(
            term=term,
            page=page,
            limit=limit,
            country=itunes_country,
        )

        # Enrich with subscription status. iTunes-sourced hits carry uuid=None
        # (resolved on open), so match on itunes_id — captured at subscribe time.
        # One read: derive both lookup sets from a single subscriptions fetch.
        subscriptions = await source.podcast_data.get_subscriptions()
        subscribed_uuids = {s['uuid'] for s in subscriptions if s.get('uuid')}
        subscribed_itunes = {s['itunes_id'] for s in subscriptions if s.get('itunes_id')}
        for podcast in result.get('podcasts', []):
            podcast['is_subscribed'] = (
                podcast.get('uuid') in subscribed_uuids
                or podcast.get('itunes_id') in subscribed_itunes
            )

        response = {
            "podcasts": result.get('podcasts', []),
            "pagination": {
                "podcasts": result.get('pagination', {}).get('podcasts', {"total": 0, "pages": 0}),
            },
        }
        if result.get("api_error"):
            response["api_error"] = True
        return response


# === Content Routes ===

@router.get("/series/{uuid}")
async def get_podcast_series(
    uuid: str,
    source: PodcastSource = Depends(get_source),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=25),
    sort_order: str = Query("LATEST", description="LATEST or OLDEST")
) -> Dict[str, Any]:
    """Get podcast series details with episodes."""
    async with api_error_handler("Error getting podcast series", logger):
        series = await source.podcast_api.get_podcast_series(
            feed_id=uuid,
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
        episode = await source.podcast_api.get_episode(uuid)
        if not episode:
            logger.error("Episode not found: %s", uuid)
            raise HTTPException(status_code=404, detail="Episode not found")

        # Add progress
        progress = await source.podcast_data.get_playback_progress(uuid)
        if progress:
            episode['playback_progress'] = progress

        return episode


# === Playback Routes ===
#
# Only the composite lives here. pause/resume/seek/set_speed are plain commands
# and go through POST /api/audio/control/podcast like every other source's.

@router.post("/play")
async def play_episode(
    request: PlayEpisodeRequest,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Play an episode, resuming at `position` when one is carried.

    Two commands in one request: the resume seek must not be a second
    round-trip, or the episode audibly starts at 0:00 first.
    """
    result = await run_source_command(
        source, "play_episode", {"episode_uuid": request.episode_uuid}, "Play"
    )

    # If position specified, seek to it
    if request.position is not None and request.position > 0:
        await run_source_command(
            source, "seek", {"position": request.position}, "Seek"
        )

    return result


@router.get("/playback-speeds")
async def get_playback_speeds() -> Dict[str, Any]:
    """Return the canonical list of valid playback speeds."""
    from backend.sources.podcast.source import VALID_PLAYBACK_SPEEDS
    return {"status": "success", "speeds": VALID_PLAYBACK_SPEEDS}


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
        await source.podcast_data.add_subscription(
            podcast_uuid=request.uuid,
            name=request.name,
            image_url=request.image_url,
            children_hash=request.children_hash,
            itunes_id=request.itunes_id
        )
        return {"status": "success"}


@router.delete("/subscriptions/{uuid}")
async def remove_subscription(
    uuid: str,
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Unsubscribe from a podcast."""
    async with api_error_handler("Error unsubscribing", logger):
        await source.podcast_data.remove_subscription(uuid)
        return {"status": "success"}


@router.get("/subscriptions/latest-episodes")
async def get_latest_episodes_from_subscriptions(
    source: PodcastSource = Depends(get_source),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(50, ge=1, le=50)
) -> Dict[str, Any]:
    """Get latest episodes from all subscribed podcasts (N parallel calls)."""
    async with api_error_handler("Error getting latest episodes", logger):
        subscriptions = await source.podcast_data.get_subscriptions()

        if not subscriptions:
            return {"results": [], "total": 0}

        # Stored name/image fill the episode's podcast block when the
        # /episodes/byfeedid items omit feedTitle
        feed_meta = {
            s['uuid']: {"name": s.get('name', ''), "image_url": s.get('image_url', '')}
            for s in subscriptions if s.get('uuid')
        }

        result = await source.podcast_api.get_latest_episodes(
            feed_ids=list(feed_meta),
            page=page,
            limit=limit,
            feed_meta=feed_meta
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
        await source.podcast_data.mark_episode_completed(episode_uuid)
        return {"status": "success"}


# === Settings Routes ===

@router.get("/settings")
async def get_settings(
    source: PodcastSource = Depends(get_source)
) -> Dict[str, Any]:
    """Get podcast settings."""
    async with api_error_handler("Error getting settings", logger):
        settings = await source.podcast_data.get_podcast_settings()
        return {"settings": settings}


