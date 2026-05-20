# backend/sources/podcast/__init__.py
"""
Podcast audio source feature using MPV and Taddy API.

This module provides podcast streaming with discovery, search,
subscription management, and playback control.

Usage:
    from backend.sources.podcast import PodcastSource, router

    # Create source
    source = PodcastSource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.sources.podcast.source import PodcastSource
from backend.sources.podcast.routes import router, setup_podcast_routes
from backend.sources.podcast.data import PodcastDataService
from backend.sources.podcast.models import (
    PlayEpisodeRequest,
    SpeedRequest,
    SubscribeRequest,
    SettingsRequest,
    Podcast,
    Episode,
    Subscription,
    PlaybackProgress,
    SearchResult
)

__all__ = [
    "PodcastSource",
    "router",
    "setup_podcast_routes",
    "PodcastDataService",
    "PlayEpisodeRequest",
    "SpeedRequest",
    "SubscribeRequest",
    "SettingsRequest",
    "Podcast",
    "Episode",
    "Subscription",
    "PlaybackProgress",
    "SearchResult"
]
