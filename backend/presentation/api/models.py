# backend/presentation/api/models.py
"""
Modèles Pydantic pour validation des requêtes API
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any


class AudioControlRequest(BaseModel):
    """Requête de contrôle audio"""
    command: str = Field(..., min_length=1, max_length=50)
    data: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator('command')
    @classmethod
    def validate_command(cls, v: str) -> str:
        """Valide que la commande ne contient que des caractères autorisés"""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Command must contain only alphanumeric characters, hyphens, and underscores')
        return v


class VolumeSetRequest(BaseModel):
    """Requête de modification de volume"""
    volume: int = Field(..., ge=0, le=100)
    show_bar: bool = Field(default=True)


class VolumeAdjustRequest(BaseModel):
    """Requête d'ajustement de volume"""
    delta: int = Field(..., ge=-100, le=100)
    show_bar: bool = Field(default=True)


class SnapcastVolumeRequest(BaseModel):
    """Requête de volume Snapcast"""
    volume: int = Field(..., ge=0, le=100)


class SnapcastClientMuteRequest(BaseModel):
    """Requête de mute client Snapcast"""
    muted: bool


class SnapcastGroupStreamRequest(BaseModel):
    """Requête de changement de stream pour un groupe"""
    stream_id: str = Field(..., min_length=1, max_length=100)
