# backend/features/radio/models.py
"""
Pydantic models for radio stations.

These models define the data structures for:
- Station metadata (from RadioBrowser API or custom)
- API request/response validation
"""
from typing import Optional, List
from pydantic import BaseModel


class Station(BaseModel):
    """Radio station metadata."""
    id: str
    name: str
    url: str
    country: str = ""
    genre: str = ""
    favicon: str = ""
    bitrate: int = 0
    codec: str = ""
    votes: int = 0
    clickcount: int = 0
    score: int = 0
    is_favorite: bool = False
    is_custom: bool = False
    image_filename: str = ""

    class Config:
        extra = "allow"


class PlayStationRequest(BaseModel):
    """Request to play a station."""
    station_id: str
    station: Optional[dict] = None


class FavoriteRequest(BaseModel):
    """Request to manage favorites."""
    station_id: str
    station: Optional[dict] = None


class AddCustomStationRequest(BaseModel):
    """Request to add a custom station."""
    name: str
    url: str
    country: str = ""
    genre: str = ""
    favicon: str = ""
    bitrate: int = 0
    codec: str = ""


class RemoveCustomStationRequest(BaseModel):
    """Request to remove a custom station."""
    station_id: str


class StationSearchResult(BaseModel):
    """Search result with stations and total count."""
    stations: List[dict]
    total: int
