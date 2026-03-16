"""
Pydantic models for WiFi service.
"""
from pydantic import BaseModel, Field
from typing import Optional


class WifiNetwork(BaseModel):
    """A WiFi network discovered during scan."""
    ssid: str
    signal: int = Field(..., ge=0, le=100, description="Signal strength percentage")
    security: str = Field(..., description="Security type (e.g. WPA2, WPA3, Open)")
    in_use: bool = Field(default=False, description="Currently connected network")


class WifiStatus(BaseModel):
    """Current network connection status."""
    connected: bool
    connection_type: Optional[str] = Field(None, description="Active connection type: 'ethernet' or 'wifi'")
    ssid: Optional[str] = None
    ip_address: Optional[str] = None
    signal: Optional[int] = None
    saved_ssid: Optional[str] = None


class WifiConnectRequest(BaseModel):
    """Request to connect to a WiFi network."""
    ssid: str = Field(..., min_length=1)
    password: Optional[str] = None


class SavedNetwork(BaseModel):
    """A saved WiFi network connection."""
    ssid: str
