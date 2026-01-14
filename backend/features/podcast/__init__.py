# backend/features/podcast/__init__.py
"""
Podcast audio source feature using MPV and Taddy API.

This module provides podcast streaming with discovery, search,
subscription management, and playback control.

Usage:
    from backend.features.podcast import PodcastSource, router

    # Create source
    source = PodcastSource(event_bus, config)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.podcast.source import PodcastSource
from backend.features.podcast.routes import router, setup_podcast_routes
from backend.features.podcast.data import PodcastDataService
from backend.features.podcast.models import (
    PlayEpisodeRequest,
    SeekRequest,
    SpeedRequest,
    SubscribeRequest,
    ProgressRequest,
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
    "SeekRequest",
    "SpeedRequest",
    "SubscribeRequest",
    "ProgressRequest",
    "SettingsRequest",
    "Podcast",
    "Episode",
    "Subscription",
    "PlaybackProgress",
    "SearchResult"
]
