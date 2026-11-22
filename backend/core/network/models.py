"""
Pydantic models for WiFi service.
"""
from pydantic import BaseModel, Field, field_validator
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
    # An activation in progress (association, authentication or DHCP); `ssid`
    # then names the network being joined.
    connecting: bool = False
    ssid: Optional[str] = None
    ip_address: Optional[str] = None
    signal: Optional[int] = None
    saved_ssid: Optional[str] = None


class NetworkStatus(BaseModel):
    """Combined network status for both ethernet and WiFi."""
    wifi_enabled: bool
    ethernet: EthernetStatus
    wifi: WifiConnectionStatus
    # The recovery access point is up: the unit has no link, and the AP carries
    # the clients that reach it (`NetworkService.hotspot_active`). In the status
    # so a client that stays connected — the kiosk, over localhost — is told
    # when it rises and when it ends.
    hotspot_active: bool
    # The unit lost its network: the recovery AP rose for want of a link, and
    # no link has come back since (`NetworkService.evaluate_reachability`).
    # Held while the AP is down for a home-network try (`_retry_station`) or a
    # connect, so the "WiFi lost" banner does not blink with the AP; never set
    # by a link that drops without the AP rising — a boot before its lease, a
    # speaker adoption, a reassociation.
    link_lost: bool

    @property
    def linked(self) -> bool:
        """Whether the unit is linked to a network: an Ethernet lease, or WiFi
        associated to a network that is not a Milō access point (the service's
        `_get_wifi_info` excludes both). The one rule for "a link came up"
        (`NetworkService.add_link_listener`) and for the setup wizard's "linked
        now" (`api/setup.py`)."""
        return self.ethernet.connected or self.wifi.connected


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


class WifiCountryRequest(BaseModel):
    """Request to set the WiFi regulatory domain country code."""
    country_code: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 country code")

    @field_validator("country_code")
    @classmethod
    def must_be_uppercase_alpha(cls, v: str) -> str:
        if not v.isalpha() or not v.isupper():
            raise ValueError("country_code must be two uppercase letters")
        return v
