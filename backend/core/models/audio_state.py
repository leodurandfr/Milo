# backend/core/models/audio_state.py
"""
Unified audio system state model with multiroom support.
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


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
    DLNA = "dlna"
    QOBUZ = "qobuz"
    TIDAL = "tidal"
    MUSIC_LIBRARY = "music_library"


class SourceState(Enum):
    """The four states an audio source can be in — all reachable, no other.

    READY and ACTIVE split on whether a session exists, not on whether audio is
    coming out: a paused radio stays ACTIVE because a station is still tuned.
    "ready" rather than "connected" because nothing connects to Radio, CD or
    the Music Library — they are simply ready to play.
    """
    STARTING = "starting"      # Starting or restarting
    READY = "ready"            # Engine up, nothing in session
    ACTIVE = "active"          # A session or content exists
    ERROR = "error"            # Not operational (a failed transition)


class NetworkRequirement(Enum):
    """What a source needs from the network to be usable at all.

    Declared per source as `BaseAudioSource.NETWORK_REQUIREMENT` and crossed
    with NetworkManager's connectivity level to produce `network_unavailable`
    in full_state — so "no internet" is only ever reported to the user when it
    actually blocks the source they selected.
    """
    NONE = "none"          # Works with the network unplugged (Bluetooth, CD, Music Library)
    LAN = "lan"            # Needs the local network only (AirPlay, DLNA, Mac/ROC)
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
    """Why the *active* source cannot work right now, or absent when it can.

    PORTAL collapses into NO_INTERNET on purpose: an appliance with no browser
    cannot accept a captive portal's terms, so the user-facing answer — and the
    action it points to — is identical to a LAN-only link.
    """
    NO_NETWORK = "no_network"    # Nothing is reachable
    NO_INTERNET = "no_internet"  # LAN is up, the internet is not


@dataclass
class SystemAudioState:
    """
    Source-scoped audio state. Global feature flags (`multiroom_enabled`,
    `equalizer_effects_enabled`) are owned by their respective services
    (AudioRoutingService, CamillaDSPService) and merged into the wire payload
    by AudioStateMachine.broadcast() when aggregating full_state.
    """
    active_source: AudioSource = AudioSource.NONE
    source_state: SourceState = SourceState.READY
    transitioning: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "active_source": self.active_source.value,
            "source_state": self.source_state.value,
            "transitioning": self.transitioning,
            "metadata": self.metadata,
            "error": self.error,
        }

