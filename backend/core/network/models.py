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


def is_wpa_psk(key: str) -> bool:
    """Whether NetworkManager takes `key` as a WPA-PSK: 8 to 63 characters, or
    64 hexadecimal digits (the raw key). The one key type a station profile is
    written with (`NetworkService._write_station_profile`); any other is refused
    by nmcli as ``802-11-wireless-security.psk: property is invalid``, logged at
    error and shown as a system fault for what was a typing mistake.
    Mirrored by frontend/src/utils/wifiKey.js, which refuses it before sending."""
    if len(key) == 64:
        return all(c in "0123456789abcdefABCDEF" for c in key)
    return 8 <= len(key) <= 63


class WifiConnectRequest(BaseModel):
    """Request to connect to a WiFi network."""
    ssid: str = Field(..., min_length=1)
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def must_be_a_wpa_key(cls, v: Optional[str]) -> Optional[str]:
        if v and not is_wpa_psk(v):
            raise ValueError("a WiFi key holds 8 to 63 characters, or 64 hexadecimal digits")
        return v


class WifiSaveRequest(WifiConnectRequest):
    """Request to save a WiFi network without joining it."""
    try_now: bool = Field(
        default=False,
        description="Over the recovery access point, try the network a few "
                    "seconds after answering instead of at the next retry, "
                    "which a client of the AP holds off for up to a quarter of "
                    "an hour. The setup wizard leaves it off: it saves, then "
                    "goes on over the AP to its apply and reboot.",
    )


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
