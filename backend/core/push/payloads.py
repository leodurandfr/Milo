# backend/core/push/payloads.py
"""The two APNs payload shapes, as pure functions.

Kept free of services so they can be tested against values rather than mocks,
and so the two unit conversions live in exactly one place each.

**Now Playing is not Live Activities.** Its `attributes` carry the mutable
state and are re-sent whole on every event, because `RemoteMediaSessionAttributes`
declares no associated ContentState type the way `ActivityAttributes` does —
`update(_ attributes:)` takes one type where ActivityKit's
`update(using contentState:)` takes two. So there is no `content-state` key and
no `attributes-type` key here, and adding either would be importing a different
protocol's envelope. Confirmed against the iOS 27 SDK's NowPlaying.swiftinterface
(zero occurrences of ContentState) by the app's author, 2026-09-19.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Two conversions Milō owns, so no client re-implements them:
#   * positions and durations are MILLISECONDS on the wire and SECONDS here;
#   * a device volume is dB internally and 0..1 here.
MS_PER_S = 1000.0


@dataclass
class NowPlayingDevice:
    """One speaker, as the lock screen's per-room slider sees it."""
    id: str
    name: str
    volume: float
    type: str = "speaker"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type, "volume": self.volume}


def normalize_volume(volume_db: float, min_db: float, max_db: float) -> float:
    """dB → 0..1 over the operator's `volume_limits`.

    Milō normalizes rather than the client because only Milō knows the limits,
    and they move: a slider calibrated against a hardcoded -80..0 would sit at
    a third of its travel on a unit limited to -78..-8, and would jump the day
    an operator changed them.

    Degenerate spans answer 0.0 rather than dividing by zero — the validator
    already refuses a span under 6 dB, so this is defence, not a real case.
    """
    span = max_db - min_db
    if span <= 0:
        return 0.0
    return round(min(1.0, max(0.0, (volume_db - min_db) / span)), 4)


def widget_payload() -> Dict[str, Any]:
    """The WidgetKit refresh push. It carries no data at all — only "reload".

    The widget then makes its own HTTP call over the LAN. Apple budgets these
    and delivers them opportunistically, and they ADD to the timeline rather
    than replacing it, so the app keeps its own refresh policy underneath.
    """
    return {"aps": {"content-changed": True}}


def build_attributes(
    session_id: str,
    metadata: Optional[Dict[str, Any]],
    devices: List[NowPlayingDevice],
    active_source: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """The whole mutable state, re-sent on every start and every update.

    `elapsedTime` is sent once per push with its timestamp, and iOS
    extrapolates from there — the position is never streamed. That is the
    difference between one push per second at worst and one per position tick,
    which would be several per second and would get the app's delivery
    throttled for everything.
    """
    metadata = metadata or {}
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    shown = displayed_track(metadata)

    return {
        "id": session_id,
        "isPlaying": bool(metadata.get("is_playing")),
        "elapsedTime": _seconds(metadata.get("position")),
        "timestamp": stamp,
        "currentTrack": {
            "id": f"{active_source}:{shown['title'] or ''}",
            "title": shown["title"],
            "artist": shown["artist"],
            "album": shown["album"],
            "duration": _seconds(metadata.get("duration")),
            "artworkURL": shown["artworkURL"],
        },
        "devices": [d.to_dict() for d in devices],
    }


def displayed_track(metadata: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """What a lock screen actually shows, whatever source filled the metadata.

    The model defines a common floor — title, artist, album, album_art_url —
    presented as the contract between sources. Radio does not fill it: those
    four stay empty and the track travels beside them, in `track_title`,
    `track_artist`, `station_name` and `favicon`. Reading only the floor sent a
    push whose every field was null, which on the phone is a session with no
    track at all — the lock screen went blank on the source that needs it most.

    Named after what it answers rather than after radio, because it names no
    source: it takes the floor when the floor is there and falls back to the
    names observed beside it. To delete the day every source fills the floor —
    this is the cascade Milo-iOS already carries, and duplicating it is exactly
    what the floor exists to prevent.
    """
    metadata = metadata or {}

    def first(*keys: str) -> Optional[str]:
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    return {
        "title": first("title", "track_title", "station_name"),
        "artist": first("artist", "track_artist"),
        "album": first("album", "station_name"),
        "artworkURL": first("album_art_url", "track_artwork", "favicon"),
    }


def now_playing_payload(
    event: str,
    session_id: str,
    attributes: Optional[Dict[str, Any]] = None,
    timestamp: Optional[int] = None,
) -> Dict[str, Any]:
    """Wrap attributes in the `aps` envelope for one session event.

    `end` carries only the session id: there is no state left to describe, and
    sending a full snapshot with it would race the teardown on the device.
    """
    if event == "end":
        attributes = {"id": session_id}
    elif attributes is None:
        raise ValueError(f"a '{event}' push needs attributes")

    return {
        "aps": {
            "event": event,
            "timestamp": timestamp if timestamp is not None else _unix_now(),
            "attributes": attributes,
        }
    }


def _seconds(milliseconds: Optional[float]) -> float:
    """ms → s, rounded to the centisecond. A missing value is 0.0, not null:
    the iOS type declares these non-optional."""
    return round((milliseconds or 0) / MS_PER_S, 2)


def _unix_now() -> int:
    return int(time.time())
