# backend/presentation/api/models.py
"""
Pydantic models for API request validation
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any


class AudioControlRequest(BaseModel):
    """Audio control request"""
    command: str = Field(..., min_length=1, max_length=50)
    data: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator('command')
    @classmethod
    def validate_command(cls, v: str) -> str:
        """Validates that command contains only allowed characters"""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Command must contain only alphanumeric characters, hyphens, and underscores')
        return v


class VolumeSetRequest(BaseModel):
    """Volume set request"""
    volume: int = Field(..., ge=0, le=100)
    show_bar: bool = Field(default=True)


class VolumeAdjustRequest(BaseModel):
    """Volume adjustment request"""
    delta: int = Field(..., ge=-100, le=100)
    show_bar: bool = Field(default=True)


class SnapcastVolumeRequest(BaseModel):
    """Snapcast volume request"""
    volume: int = Field(..., ge=0, le=100)


class SnapcastClientMuteRequest(BaseModel):
    """Snapcast client mute request"""
    muted: bool


class SnapcastGroupStreamRequest(BaseModel):
    """Group stream change request"""
    stream_id: str = Field(..., min_length=1, max_length=100)
