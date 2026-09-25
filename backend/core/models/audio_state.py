# backend/core/models/audio_state.py
"""
The audio vocabulary shared across the backend: which sources exist, what each
needs from the network, and what the network gives. The state itself is
core/models/audio_wire.py.
"""
from enum import Enum


class AudioSource(Enum):
    """Available audio sources in the system."""
    NONE = "none"
    SPOTIFY = "spotify"
    BLUETOOTH = "bluetooth"
    RADIO = "radio"
    PODCAST = "podcast"
    AIRPLAY = "airplay"
    MAC = "mac"
    CD = "cd"
    QOBUZ = "qobuz"
    TIDAL = "tidal"
    MUSIC_LIBRARY = "music_library"


class NetworkRequirement(Enum):
    """What a source needs from the network to be usable at all.

    Declared per source as `BaseAudioSource.NETWORK_REQUIREMENT` and crossed
    with NetworkManager's connectivity level into that source's `availability`
    entry — so "no internet" is only ever reported for a source it actually
    blocks.
    """
    NONE = "none"          # Works with the network unplugged (Bluetooth, CD, Music Library)
    LAN = "lan"            # Needs the local network only (AirPlay, Mac/ROC)
    INTERNET = "internet"  # Needs a route out (Spotify, Qobuz, Radio, Podcast)


class ConnectivityLevel(Enum):
    """NetworkManager's Connectivity property, kept whole.

    UNKNOWN is the fail-open value: NM down, D-Bus unavailable, or the cached
    property read before NM's first probe. It is treated exactly like FULL —
    never report a problem we have not observed.
    """
    UNKNOWN = "unknown"    # NM 0
    NONE = "none"          # NM 1 — no network at all
    PORTAL = "portal"      # NM 2 — captive portal; Milō has no browser to log in with
    LIMITED = "limited"    # NM 3 — LAN reachable, no internet
    FULL = "full"          # NM 4


class NetworkUnavailable(Enum):
    """Why a source cannot work right now because of the link, or absent when
    it can.

    PORTAL collapses into NO_INTERNET on purpose: an appliance with no browser
    cannot accept a captive portal's terms, so the user-facing answer — and the
    action it points to — is identical to a LAN-only link.
    """
    NO_NETWORK = "no_network"    # Nothing is reachable
    NO_INTERNET = "no_internet"  # LAN is up, the internet is not
