# backend/sources/podcast/routes.py
"""
FastAPI routes for Podcast feature.

Provides REST API for:
- Discovery (top charts, by genre — iTunes RSS, exact Apple Podcasts order)
- Search (podcasts only — there is no cross-podcast episode search)
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
from backend.sources.podcast.podcast_catalog import (
    is_upstream_error,
    map_milo_language_to_itunes_country,
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


async def _user_locale() -> tuple[str, str]:
    """(Milō language, iTunes country) — which Apple storefront to read.

    The charts are per-country, and the product-page tier of feed resolution
    takes the same storefront, so both come from one place.
    """
    from backend.dependencies import get_service
    settings = await get_service("settings_service").load_settings()
    milo_language = settings['language']
    return milo_language, map_milo_language_to_itunes_country(milo_language)


async def _itunes_country() -> str:
    """The storefront alone, for the routes that do not report the language."""
    return (await _user_locale())[1]


# === Discovery Routes ===

@router.get("/discover/top-charts")
async def get_top_charts(
    source: PodcastSource = Depends(get_source),
    limit: int = Query(25, ge=1, le=200)
) -> Dict[str, Any]:
    """Get Apple Podcasts top charts (iTunes RSS, podcasts-only) using user's language."""
    async with api_error_handler("Error getting top charts", logger):
        milo_language, itunes_country = await _user_locale()

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
        milo_language, itunes_country = await _user_locale()

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


# === Search Routes ===

@router.get("/search")
async def search_podcasts(
    source: PodcastSource = Depends(get_source),
    term: str = Query("", description="Search term"),
    page: int = Query(1, ge=1, le=20),
    limit: int = Query(25, ge=1, le=25)
) -> Dict[str, Any]:
    """Search for podcasts (feeds-only; iTunes-backed, each hit ready to open)."""
    async with api_error_handler("Error in podcast search", logger):
        empty = {
            "podcasts": [],
            "pagination": {"podcasts": {"total": 0, "pages": 0}},
        }
        if not term:
            return empty

        result = await source.podcast_api.search_podcasts(
            term=term,
            page=page,
            limit=limit,
            country=await _itunes_country(),
        )

        # Enrich with subscription status. A hit's uuid is its Apple id, which
        # is what a subscription stores, so one set answers it.
        subscriptions = await source.podcast_data.get_subscriptions()
        subscribed = {s['uuid'] for s in subscriptions if s.get('uuid')}
        for podcast in result.get('podcasts', []):
            podcast['is_subscribed'] = podcast.get('uuid') in subscribed

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
            itunes_id=uuid,
            episodes_page=page,
            episodes_limit=limit,
            sort_order=sort_order,
            country=await _itunes_country(),
        )

        if is_upstream_error(series):
            # The feed exists, this attempt failed. A 404 here would tell the
            # owner the podcast is gone over a passing CDN outage.
            logger.error("Podcast catalog unreachable for: %s", uuid)
            raise HTTPException(status_code=503, detail="Podcast catalog unavailable")

        if not series:
            # Apple publishes no feed for this id — a subscriber-only show, or
            # an id that names nothing. Expected, so it must not reach the
            # WebSocketLogHandler banner.
            logger.debug("No public feed for podcast: %s", uuid)
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
        episode = await source.podcast_api.get_episode(
            uuid, country=await _itunes_country()
        )
        if is_upstream_error(episode):
            logger.error("Podcast catalog unreachable for episode: %s", uuid)
            raise HTTPException(status_code=503, detail="Podcast catalog unavailable")

        if not episode:
            logger.debug("Episode not in its feed: %s", uuid)
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

        # Each feed names and illustrates itself, so no stored fallback is
        # needed for the episode's podcast block.
        result = await source.podcast_api.get_latest_episodes(
            itunes_ids=[s['uuid'] for s in subscriptions if s.get('uuid')],
            page=page,
            limit=limit,
            country=await _itunes_country(),
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


