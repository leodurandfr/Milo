# backend/sources/podcast/routes.py
"""
FastAPI routes for Podcast feature.

Provides REST API for:
- Discovery (top charts, by genre — iTunes RSS, exact Apple Podcasts order),
  and the genres a listener can browse
- Search (podcasts only — there is no cross-podcast episode search)
- Content (series details, episode details)
- Subscriptions (add, remove, list)
- Queue (in-progress episodes)
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from backend.api.route_helpers import api_error_handler
from typing import Any, Dict, Iterable
import logging

from backend.api.source_dependency import make_source_dependency
from backend.sources.podcast.models import (
    SubscribeRequest,
)
from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.podcast_catalog import (
    GENRE_TO_ITUNES_ID,
    PodcastCatalog,
    is_upstream_error,
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


async def _user_locale(source: PodcastSource) -> tuple[str, str]:
    """(Milō language, iTunes storefront) — which Apple storefront to read.

    The charts are per-country, and the product-page tier of feed resolution
    takes the same storefront, so both come from one place: the catalogue's
    `storefront()`, which playback asks too.
    """
    from backend.dependencies import get_service
    settings = await get_service("settings_service").load_settings()
    milo_language = settings['language']
    storefront = await source.podcast_api.storefront(settings['wifi']['country'], milo_language)
    return milo_language, storefront


async def _itunes_country(source: PodcastSource) -> str:
    """The storefront alone, for the routes that do not report the language."""
    return (await _user_locale(source))[1]


async def _flag_subscribed(
    source: PodcastSource, podcasts: Iterable[Dict[str, Any]]
) -> None:
    """Stamp `is_subscribed` on each podcast, from one read of the store.

    The one join every route uses — the charts, a genre, a search and a series
    — so a followed podcast is marked in all four or in none. A podcast's
    `uuid` is its Apple id wherever it comes from, which is what a subscription
    stores.
    """
    subscribed = set(await source.podcast_data.get_subscription_uuids())
    for podcast in podcasts:
        podcast['is_subscribed'] = podcast.get('uuid') in subscribed


# === Discovery Routes ===

@router.get("/discover/top-charts")
async def get_top_charts(
    source: PodcastSource = Depends(get_source),
    limit: int = Query(25, ge=1, le=200)
) -> Dict[str, Any]:
    """Get Apple Podcasts top charts (iTunes RSS, podcasts-only) using user's language."""
    async with api_error_handler("Error getting top charts", logger):
        milo_language, itunes_country = await _user_locale(source)

        result = await source.podcast_api.get_itunes_top_podcasts(
            country_code=itunes_country,
            limit=limit
        )

        await _flag_subscribed(source, result.get('results', []))

        result['country'] = itunes_country
        result['language'] = milo_language

        return result


@router.get("/genres")
async def get_genres() -> Dict[str, Any]:
    """Return the genre keys a listener can browse, in the order the grid shows them."""
    return {"status": "success", "genres": list(GENRE_TO_ITUNES_ID)}


@router.get("/discover/by-genre")
async def get_content_by_genre(
    source: PodcastSource = Depends(get_source),
    genre: str = Query(..., description="Genre key, one GET /podcast/genres lists (e.g., PODCASTSERIES_COMEDY)"),
    limit: int = Query(30, ge=1, le=200)
) -> Dict[str, Any]:
    """Get top podcasts for a specific genre using user's language."""
    async with api_error_handler("Error getting content by genre", logger):
        milo_language, itunes_country = await _user_locale(source)

        podcasts_result = await source.podcast_api.get_itunes_top_podcasts_by_genre(
            genre=genre,
            country_code=itunes_country,
            limit=limit
        )

        podcasts = podcasts_result.get('results', [])
        await _flag_subscribed(source, podcasts)

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
            country=await _itunes_country(source),
        )

        await _flag_subscribed(source, result.get('podcasts', []))

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
            country=await _itunes_country(source),
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

        await _flag_subscribed(source, [series])

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
            uuid, country=await _itunes_country(source)
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


# === Playback ===
#
# Every playback command, play_episode included, goes through
# POST /api/audio/control/podcast like every other source's: none composes two
# commands any more (the start position rides on play_episode's load).


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
    limit: int = Query(
        PodcastCatalog.LATEST_EPISODES_MAX, ge=1, le=PodcastCatalog.LATEST_EPISODES_MAX
    ),
) -> Dict[str, Any]:
    """The newest episodes across every subscription (one page, N parallel
    feed reads)."""
    async with api_error_handler("Error getting latest episodes", logger):
        subscriptions = await source.podcast_data.get_subscriptions()

        if not subscriptions:
            return {"results": [], "total": 0}

        # Each feed names and illustrates itself, so no stored fallback is
        # needed for the episode's podcast block.
        result = await source.podcast_api.get_latest_episodes(
            itunes_ids=[s['uuid'] for s in subscriptions if s.get('uuid')],
            limit=limit,
            country=await _itunes_country(source),
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
