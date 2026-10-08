# backend/core/models/audio_wire.py
"""The audio state on the wire — one object, the same in REST and WS.

`GET /api/audio/state`, the `source/state` event and `system/initial_state`
(key `state`) all carry an `AudioState`. It publishes the six axes as they
are (docs: source architecture, "Développeurs : le fil", frozen 2026-09-24):
the selection (`source`, `switching`), the service (`service`,
`service_error`), what each source can do right now (`availability`), the
live session with its position anchor, the resume point, the commands the
selected source takes now (`controls`), and the source's own content
(`details`).

Every field is always present: an absent value is null, never a missing key.
Durations and positions are milliseconds (int); instants are UTC seconds
since the epoch (float).

Consumers: frontend `unifiedAudioStore` (strict Zod schema), Milo-Mac and
Milo-iOS through the shared `MiloAudioState.swift` decoder, and the iOS push
payloads built from it (core/push/payloads.py).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Annotated

from backend.core.models.audio_state import AudioSource
from backend.core.models.session import Phase, ServiceState

# Why a source cannot work right now. Connectivity comes first (every source
# with a network requirement), then the source's own reason.
AvailabilityReason = Literal[
    "no_network", "no_internet",          # connectivity × NETWORK_REQUIREMENT
    "no_account",                         # Qobuz
    "no_drive", "no_disc", "reading_disc", "unreadable_disc", "ejecting",  # CD
    "no_storage", "catalog_unavailable",  # Music Library
]

# What a queue repeats, and what `set_repeat` takes: nothing, the whole
# context (a Spotify context, the library's queue), or the track playing. One
# vocabulary for every source that repeats, so one player button cycles it.
RepeatMode = Literal["off", "context", "track"]


class PositionAnchor(BaseModel):
    """The playhead: `ms` at the instant `at`, moving at `rate` while the
    session plays. position_now = ms + (phase == playing ? (now − at) × 1000 ×
    rate : 0), bounded to [0, duration_ms]. `rate` is 1.0 for every source —
    nothing plays faster or slower — and stays because Milo-Mac and Milo-iOS
    decode it (`session.position.rate`, pinned by both manifests)."""
    ms: int
    at: float
    rate: float


class SessionView(BaseModel):
    """The live session of the selected source, as the wire shows it."""
    id: str
    phase: Phase
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    artwork: Optional[str]
    senders: List[str]
    duration_ms: Optional[int]
    position: Optional[PositionAnchor]


class ResumeView(BaseModel):
    """What "play" would bring back while no session runs."""
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    artwork: Optional[str]
    duration_ms: Optional[int]
    position_ms: Optional[int]


class ServiceError(BaseModel):
    """Why the last start failed. `message` is for the journal, never shown."""
    reason: Literal["start_timeout", "start_failed"]
    message: str


class Availability(BaseModel):
    """One entry per source, always all ten: the reason it cannot work now,
    or null when it can."""
    radio: Optional[AvailabilityReason] = None
    podcast: Optional[AvailabilityReason] = None
    music_library: Optional[AvailabilityReason] = None
    cd: Optional[AvailabilityReason] = None
    spotify: Optional[AvailabilityReason] = None
    tidal: Optional[AvailabilityReason] = None
    qobuz: Optional[AvailabilityReason] = None
    airplay: Optional[AvailabilityReason] = None
    bluetooth: Optional[AvailabilityReason] = None
    mac: Optional[AvailabilityReason] = None


class RadioStation(BaseModel):
    id: Optional[str]
    name: Optional[str]
    url: Optional[str]
    country: Optional[str]
    genre: Optional[str]
    favicon: Optional[str]
    bitrate: Optional[int]
    codec: Optional[str]

    @field_validator("bitrate", mode="before")
    @classmethod
    def _bitrate(cls, value: Any) -> Optional[int]:
        """A directory's number, or whatever a custom station was saved with
        ("", "128"): a number or nothing."""
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @field_validator("id", "name", "url", "country", "genre", "favicon", "codec", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Optional[str]:
        return None if value in (None, "") else str(value)


class RadioTrack(BaseModel):
    """The song recognized in the stream (in-band) or by Shazam."""
    title: Optional[str]
    artist: Optional[str]
    artwork: Optional[str]


class RadioDetails(BaseModel):
    kind: Literal["radio"] = "radio"
    station: RadioStation
    track: Optional[RadioTrack]


class PodcastDetails(BaseModel):
    kind: Literal["podcast"] = "podcast"
    # The episode as the podcast catalog routes return it (uuid, name,
    # description, image_url, podcast{uuid, name, image_url}, …).
    episode: Optional[Dict[str, Any]]


class MusicLibraryDetails(BaseModel):
    kind: Literal["music_library"] = "music_library"
    queue: List[Dict[str, Any]]      # the Subsonic tracks as they are
    queue_index: Optional[int]
    shuffle: bool
    repeat: RepeatMode
    track_id: Optional[str]
    album_id: Optional[str]
    artist_id: Optional[str]


class CdTrack(BaseModel):
    number: int
    title: Optional[str]
    duration_ms: Optional[int]


class CdDisc(BaseModel):
    id: Optional[str]
    album: Optional[str]
    artist: Optional[str]
    year: Optional[str]
    cover_url: Optional[str]
    tracks: List[CdTrack]


class CdDetails(BaseModel):
    kind: Literal["cd"] = "cd"
    disc: Optional[CdDisc]
    current_track: Optional[int]     # 1-based
    artwork_pending: bool
    shuffle: bool
    repeat: RepeatMode


class AirPlayDetails(BaseModel):
    kind: Literal["airplay"] = "airplay"
    # The cover's width in pixels: the screen keeps an untrusted sender's
    # tiny covers off the full-screen player.
    artwork_width: Optional[int]


class SpotifyRemote(BaseModel):
    """What the signed-in account plays on another Spotify Connect device (a
    phone, a computer) while Milō holds no session: nothing of it plays here,
    so it is never a session. `take_over` brings it here."""
    # As the device's own app names it ("iPhone"), and Spotify's type for it,
    # lowercased: smartphone, computer, tablet, speaker…
    device_name: str
    device_type: str
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    artwork: Optional[str]
    track_uri: Optional[str]
    duration_ms: Optional[int]
    paused: bool
    # Moves by the session's formula while not paused.
    position: PositionAnchor


class SpotifyArtist(BaseModel):
    name: str
    uri: Optional[str]              # null where Spotify names no page (an episode's show)


class SpotifyQueueEntry(BaseModel):
    """One track of the play order around what plays: what the phone's
    mini-bar slides in under the finger before the swipe lands on it."""
    uri: str
    # Null until go-librespot has cached the track's metadata.
    title: Optional[str]
    artist: Optional[str]           # the artist line, its names joined


class SpotifyDetails(BaseModel):
    kind: Literal["spotify"] = "spotify"
    # The account go-librespot is signed in as, or about to be: its stored
    # credentials sign it back in after every session end and at every start.
    # Null when nobody is (it waits for a phone).
    account: Optional[str]
    # Stored credentials handed to the daemon, no account confirmed yet.
    signing_in: bool
    # What plays now; all four null with no session.
    context_uri: Optional[str]
    context_name: Optional[str]
    track_uri: Optional[str]
    album_uri: Optional[str]
    # The names of the artist line, in its order: each one opens its page.
    artists: List[SpotifyArtist]
    # The play order around what plays, as go-librespot holds it (the user's
    # queue and shuffling included, up to 32 tracks on either side), and the
    # entry playing in it. Empty and null with no session, or when the daemon
    # lists nothing around the track.
    queue: List[SpotifyQueueEntry]
    queue_index: Optional[int]
    shuffle: bool
    repeat: RepeatMode
    # Null while Milō holds a session, or when no other device is active.
    remote: Optional[SpotifyRemote]


Details = Annotated[
    Union[RadioDetails, PodcastDetails, MusicLibraryDetails, CdDetails, AirPlayDetails, SpotifyDetails],
    Field(discriminator="kind"),
]


class AudioState(BaseModel):
    """The whole audio state (docs: "Développeurs : le fil", §1)."""
    source: AudioSource
    switching: bool
    service: ServiceState
    service_error: Optional[ServiceError]
    availability: Availability
    session: Optional[SessionView]
    controls: List[str]
    resume: Optional[ResumeView]
    details: Optional[Details]
    multiroom_enabled: bool
    equalizer_effects_enabled: bool

    def wire(self) -> Dict[str, Any]:
        """The JSON-ready dict: enums as their values, nulls kept."""
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class SourceView:
    """What a source contributes to the state: its session, its resume point,
    its content and the commands it takes now. Built by the source
    (`BaseAudioSource.view()`); the state machine adds the rest."""
    session: Optional[SessionView] = None
    resume: Optional[ResumeView] = None
    details: Optional[Any] = None       # one of the Details models
    controls: Tuple[str, ...] = ()
