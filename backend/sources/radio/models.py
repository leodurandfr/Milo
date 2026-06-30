# backend/sources/radio/models.py
"""
Pydantic models for radio stations.

These models define the data structures for:
- Station metadata (from RadioBrowser API or custom)
- API request/response validation
"""
from typing import Optional, List
from pydantic import BaseModel, Field


class PlayStationRequest(BaseModel):
    """Request to play a station."""
    station_id: str
    station: Optional[dict] = None


class FavoriteRequest(BaseModel):
    """Request to manage favorites."""
    station_id: str
    station: Optional[dict] = None


# === Command-parameter models (validated at the command() boundary) ===

class PlayStationParams(BaseModel):
    """Params for `play_station` / `add_favorite` (station is opaque passthrough)."""
    station_id: str = Field(min_length=1)
    station: Optional[dict] = None


class RemoveFavoriteParams(BaseModel):
    """Params for `remove_favorite`."""
    station_id: str = Field(min_length=1)


class StationSearchResult(BaseModel):
    """Search result with stations and total count."""
    stations: List[dict]
    total: int
