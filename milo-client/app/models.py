"""
Pydantic models for API request validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List


class FilterUpdate(BaseModel):
    """Model for filter update request (tuning only).

    Carries no `enabled`: pipeline membership is owned by the master toggle
    (PUT /equalizer/enabled), so editing a band never un-bypasses the client.
    """
    gain: float
    freq: Optional[float] = None
    q: Optional[float] = None
    filter_type: Optional[str] = None
    # The attenuation the whole curve needs, computed by the server from every
    # band (backend/core/equalizer/eq_response.py) and sent with each one.
    headroom_db: Optional[float] = None


class CompressorUpdate(BaseModel):
    """Model for compressor update request."""
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    ratio: Optional[float] = None
    attack: Optional[float] = None
    release: Optional[float] = None
    makeup_gain: Optional[float] = None


class LoudnessUpdate(BaseModel):
    """Model for loudness update request."""
    enabled: Optional[bool] = None
    high_boost: Optional[float] = None
    low_boost: Optional[float] = None
    # The main-fader level where the native Loudness filter stops correcting;
    # the server declares it (LOUDNESS_REFERENCE_DB) and sends it every time.
    reference_level: Optional[float] = None


class MonoUpdate(BaseModel):
    """Model for mono mixing update request."""
    enabled: bool


class GainUpdate(BaseModel):
    """Level trim in dB — a fixed Gain stage compensating this speaker's
    sensitivity against the others. Not a volume: the fader is untouched.

    The trim only cuts, but the bound is `set_gain`'s, not this model's: a
    positive trim an older server still pushes must reach `set_gain`, which
    takes it out of the config, rather than a 422 that leaves it there."""

    gain_db: float = Field(..., ge=-12.0, le=12.0)


class VolumeUpdate(BaseModel):
    """Model for volume update request."""
    volume: float


class MuteUpdate(BaseModel):
    """Model for mute update request."""
    muted: bool


class PassFilterUpdate(BaseModel):
    """A zone's crossover filter on one speaker: the highpass of a satellite
    (PUT /equalizer/crossover) or the lowpass of a subwoofer (PUT /equalizer/lowpass).

    Every field required: the server (backend/core/multiroom/crossover.py)
    resolves the frequency and the Q and sends both with every push, disable
    included, so a default here would be a second declaration nothing reads.
    """
    enabled: bool
    frequency: float
    q: float


class FiltersBatchUpdate(BaseModel):
    """Model for batch filter update request."""
    filters: List[dict]  # [{id: "eq_band_00", gain: 5.0, freq: 31, q: 1.41}, ...]
    headroom_db: Optional[float] = None  # see FilterUpdate.headroom_db


class EqualizerEnabledUpdate(BaseModel):
    """Model for equalizer enabled state update."""
    enabled: bool


class SnapclientConfigUpdate(BaseModel):
    """Model for snapclient ALSA buffer configuration update.

    Both fields are required and bounded here rather than clamped in the
    handler: the server sends the pair on every push, already resolved against
    the same range (`SNAPCLIENT_LIMITS` in the backend's multiroom/routing.py,
    which a satellite tarball does not carry). A value outside it means the two
    halves disagree — worth a loud 422 the server logs, not a satellite quietly
    running a different ALSA buffer than the rest of the house.
    """
    buffer_time: int = Field(..., ge=60, le=300)
    fragments: int = Field(..., ge=2, le=8)


class ProgramUpdateRequest(BaseModel):
    """Model for an update request naming the version to install.

    Required, and the satellite installs exactly what it names: a satellite
    carries no `dependencies.env` and no GitHub token, so resolving
    `releases/latest` here is how a client ends up on a release the server
    never validated — while the row that started it announced another one.

    `sha256` is the digest the server's manifest declares for the file that
    version downloads, which the satellite checks the download against and its
    root installer checks again; null when the server names a version its
    manifest does not declare (one past it). The key is required even so: with
    a default, a server that stopped sending it would have every speaker
    install unchecked, as root, with a warning only in the speaker's journal.
    Required, that server gets a 422.

    `target_version` is a bare `x.y.z` in ASCII digits, which is every
    version the server resolves (the manifest's, or an upstream release's
    tag past it): it is interpolated into the download URL, where a path in it (`1/../x`) is
    normalized away and, with no digest to check, had the root installer
    install another repository's binary. The route installs a release
    upstream published at the version the caller names, and only that.
    """
    target_version: str = Field(..., pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    sha256: Optional[str] = Field(..., pattern=r"^[0-9a-f]{64}$")
