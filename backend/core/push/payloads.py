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
from typing import Any, Dict, List, Optional, Tuple


# Two conversions Milō owns, so no client re-implements them:
#   * positions and durations are MILLISECONDS on the wire and SECONDS here;
#   * a device volume is dB internally and 0..1 here.
# The second one is declared by `core/models/volume.py`, next to its reciprocal
# and to the limits it spans: the incoming direction is read by the API layer
# too, and a conversion with one home cannot drift from itself.
MS_PER_S = 1000.0

# The card of a source whose state names no track — a Mac's stream, a receiver
# with no sender, a service selected with nothing playing — and of no source at
# all: the source's name over its dock icon. Static files nginx serves from
# dist/ (frontend/public/now-playing/), rendered once at 600 px from
# frontend/src/assets/app-icons/, full bleed (iOS rounds them).
#
# The names are the dock's French labels, fixed rather than read from the
# language setting: Milo-iOS builds the same card while it runs
# (`MiloNowPlayingBridge.sourceCards`), and two spellings of one card make the
# Lock Screen flip between them at every round trip. Change both or neither.
SOURCE_CARDS: Dict[str, Tuple[str, str]] = {
    "spotify": ("Spotify", "spotify"),
    "qobuz": ("Qobuz", "qobuz"),
    "tidal": ("TIDAL", "tidal"),
    "airplay": ("AirPlay", "airplay"),
    "bluetooth": ("Bluetooth", "bluetooth"),
    "mac": ("Récepteur macOS", "macos"),
    "radio": ("Webradio", "radio"),
    "podcast": ("Podcasts", "podcast"),
    "music_library": ("Bibliothèque", "music-library"),
    "cd": ("Lecteur CD", "cd"),
}
# A source this table does not know yet — and `none`, though no card is left up
# on `none` (the push service ends it at once): only a device already drawing
# the card as the state flips may build it.
MILO_CARD: Tuple[str, str] = ("Milō", "milo")


@dataclass
class NowPlayingDevice:
    """One speaker, as the lock screen's per-room slider sees it."""
    id: str
    name: str
    volume: float
    type: str = "speaker"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type, "volume": self.volume}


def widget_payload() -> Dict[str, Any]:
    """The WidgetKit refresh push. It carries no data at all — only "reload".

    The widget then makes its own HTTP call over the LAN. Apple budgets these
    and delivers them opportunistically, and they ADD to the timeline rather
    than replacing it, so the app keeps its own refresh policy underneath.
    """
    return {"aps": {"content-changed": True}}


def shown_track(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the card shows: the session if it names something, else the
    resume point, else nothing (docs: "le fil", §9 — the rule Milo-iOS'
    `MiloAudioState.shown` follows too)."""
    for key in ("session", "resume"):
        candidate = state.get(key)
        if candidate and candidate.get("title"):
            return candidate
    return None


def source_card(state: Dict[str, Any]) -> Dict[str, Any]:
    """The card when `shown_track` has nothing: the selected source's name and
    icon — Milō's when none is selected — and who is sending, when someone is.

    Every idle state now draws something, so a session is never shown empty;
    it is only ever ended, after `SESSION_IDLE_GRACE_S`.
    """
    title, icon = SOURCE_CARDS.get(str(state.get("source") or "none"), MILO_CARD)
    senders = (state.get("session") or {}).get("senders") or []
    return {
        "title": title,
        "artist": ", ".join(senders) or None,
        "artwork": f"/now-playing/{icon}.jpg",
    }


def build_attributes(
    session_id: str,
    state: Dict[str, Any],
    devices: List[NowPlayingDevice],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The whole mutable state, re-sent on every start and every update.

    Built from the audio state the way Milo-iOS builds its own card
    (`MiloNowPlayingBridge.buildAttributes`), so a push and the app agree:
    `isPlaying` is the session's phase, `elapsedTime` and `timestamp` are its
    position anchor — the playhead at an instant, which iOS extrapolates from,
    so the position is never streamed. Without an anchor they are 0 and the
    moment of building.
    """
    session = state.get("session") or {}
    anchor = session.get("position")
    shown = shown_track(state) or source_card(state)
    title = shown.get("title")

    return {
        "id": session_id,
        "isPlaying": session.get("phase") == "playing",
        "elapsedTime": _seconds(anchor["ms"] if anchor else 0),
        "timestamp": _iso8601(anchor["at"] if anchor else (now if now is not None else time.time())),
        "currentTrack": {
            # Changes with what is displayed: without it the system keeps the
            # previous artwork and title, having no way to know the content moved.
            "id": f"{state.get('source') or 'none'}:{title or ''}",
            "title": title,
            "artist": shown.get("artist"),
            "album": shown.get("album"),
            "duration": _seconds(shown.get("duration_ms")),
            "artworkURL": shown.get("artwork"),
        },
        "devices": [d.to_dict() for d in devices],
        # What the lock screen may offer: the extension enables a button only
        # for a command listed here (Qobuz and a Mac take none, Tidal no seek).
        "controls": lock_screen_controls(state),
    }


# The commands that bring back what an idle card shows — the one thing a card
# with no session behind it may still offer.
RESUME_COMMANDS = ("resume", "resume_playback")


def lock_screen_controls(state: Dict[str, Any]) -> List[str]:
    """The commands the Lock Screen may offer for this state.

    With a session, what the source takes now. Without one, only resuming
    what the card names: a source's idle `controls` can list more — radio
    keeps `next`/`prev` to step its favorites while stopped — but the owner
    asked on 2026-09-25 that a card with nothing playing offer nothing else,
    so an idle card reads as idle. Milo-iOS filters the same way
    (`MiloSourceCard.lockScreenControls`).
    """
    controls = list(state.get("controls") or [])
    if state.get("session"):
        return controls
    return [c for c in controls if c in RESUME_COMMANDS]


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


def _iso8601(at: float) -> str:
    """UTC, with the fraction of a second: an anchor carries it, and dropping
    it would shift the playhead by as much. The app's extension reads both
    forms."""
    stamp = datetime.fromtimestamp(at, tz=timezone.utc).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


def _unix_now() -> int:
    return int(time.time())
