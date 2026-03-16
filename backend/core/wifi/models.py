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


class EthernetStatus(BaseModel):
    """Current ethernet connection status."""
    connected: bool
    ip_address: Optional[str] = None


class WifiConnectionStatus(BaseModel):
    """Current WiFi connection status."""
    connected: bool
    ssid: Optional[str] = None
    ip_address: Optional[str] = None
    signal: Optional[int] = None
    saved_ssid: Optional[str] = None


class NetworkStatus(BaseModel):
    """Combined network status for both ethernet and WiFi."""
    wifi_enabled: bool
    ethernet: EthernetStatus
    wifi: WifiConnectionStatus


class WifiConnectRequest(BaseModel):
    """Request to connect to a WiFi network."""
    ssid: str = Field(..., min_length=1)
    password: Optional[str] = None


class WifiRadioRequest(BaseModel):
    """Request to enable or disable WiFi radio."""
    enabled: bool


class SavedNetwork(BaseModel):
    """A saved WiFi network connection."""
    ssid: str
