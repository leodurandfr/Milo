# backend/sources/podcast/__init__.py
"""Podcast audio source via mpv, Apple discovery and publisher RSS feeds (Family C).

Discovery, search, subscription management and playback, with per-episode
progress persisted so a partially-heard episode resumes where it stopped.
"""
from backend.sources.podcast.source import PodcastSource

__all__ = ["PodcastSource"]
