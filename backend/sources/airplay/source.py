# backend/sources/airplay/source.py
"""
AirPlay 2 audio source using shairport-sync.

The session belongs to shairport-sync, not to Milō: a sender connects, streams,
pauses and leaves on its own, and the daemon announces it on its metadata pipe.
The source follows it through `reconcile()` (docs: source architecture,
"reconcile"). Measured on shairport-sync 5.5.1 (2026-09-23, an iPhone and a
Mac), which the phase follows:

- The stream type depends on the app. iPhone Music opens a **Buffered**
  stream, which reports its pauses (`paus`/`pres`): PLAYING and PAUSED. A Mac's
  system audio and Spotify open a **Realtime** stream, which reports nothing:
  CONNECTED — a Mac goes on streaming silence through a pause.
- A skip or a seek is `paus` then `pres` 160 ms later; it is applied as it
  comes (an owner decision: the dip is invisible on the AirPlay screen and the
  lock-screen pushes are coalesced at 1 s).
- A sender that played and then sends nothing while still connected is
  PAUSED: a paused iPhone tears its stream down after 29-184 s, Spotify 1 s
  after a pause, and neither says goodbye.
- The idle timeout asks the daemon to end the session (`DropSession`,
  REQUEST_END): the sender gets a `disc` at once and falls back to its own
  speaker. A killed daemon says nothing at all; its session ends when its
  process does (the base's pidfd watch).

The cover is the last picture the sender pushed, until it withdraws it
(shairport-sync's own rule, metadata/hub.c): tags never touch it, and a new
track does not either. Every picture is preceded by the sender's "no picture"
(an iPhone, measured 2026-09-25: the new one follows 89-126 ms later), so a
withdrawal removes the cover only if no picture comes within
ARTWORK_SETTLE_SECONDS. The picture's rtptime is not read: it is where the
sender was when it wrote the request, and pairing it with the tags' by distance
dropped the playing track's own cover mid-track (E68). The cover is kept in
memory and served via a dedicated HTTP endpoint.
"""
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy, Session,
)
from backend.core.models.audio_wire import AirPlayDetails
from backend.sources.airplay.metadata_reader import MetadataReader, PipeEvent
from backend.sources.airplay.remote import drop_session
from backend.shared.artwork import decode_artwork_dimensions

# Sample rate for RTP frame to millisecond conversion
AIRPLAY_SAMPLE_RATE = 44100

# How long a withdrawn cover stays on screen while its replacement is in flight.
#
# A sender changing covers withdraws the old one first -- an iPhone sends an
# empty picture before every picture, the new one 89-126 ms behind it (measured
# 2026-09-25) -- so taking the withdrawal literally, as shairport-sync does,
# would publish a state with no cover between the two, and the frontend's
# untrusted-sender gate (UNTRUSTED_SENDER_MIN_ARTWORK_PX) reads a missing
# artwork_width as "this sender pushes no real cover": the player is swapped for
# the status card and back. A picture before the deadline replaces the cover;
# none, and the cover goes. The bound is sized on the slowest replacement
# measured, a Mac's 5.4 s on a track change.
ARTWORK_SETTLE_SECONDS = 8.0

# The stream type that reports its pauses; every other one (Realtime, a
# classic AirPlay 1 stream) is CONNECTED while it flows.
BUFFERED = "Buffered"

# What opens a session when none is live: a sender connecting, naming itself,
# or starting a stream (measured, every session opens with `conn`). Tags, a
# cover, a progress report arriving with no session are about one that ended.
_OPENINGS = frozenset({"conn", "client_name", "stream_begin"})


@dataclass(eq=False)
class Cover:
    """The last picture the sender pushed."""
    data: bytes
    mime: str
    hash: str
    width: int

    @property
    def url(self) -> str:
        return f"/api/airplay/artwork?v={self.hash}"


@dataclass(eq=False)
class AirPlaySession(Session):
    """One sender's session: everything it owns goes with it (E19 — the next
    sender used to inherit the previous track's position and duration).

    The stream facts are what shairport-sync announced; the phase is computed
    from them (`AirPlaySource._phase_of`).
    """
    client_name: Optional[str] = None
    stream: bool = False                # pbeg .. pend
    paused: bool = False                # paus .. pres
    first_frame: bool = False           # pffr since this stream began
    stream_type: Optional[str] = None   # styp, kept across the session's streams
    streamed: bool = False              # sound ever arrived in this session
    tags: Dict[str, str] = field(default_factory=dict)
    track_id: Optional[str] = None      # the rtptime of the tags on screen
    cover: Optional[Cover] = None
    # Progress: `prgr` gives a snapshot, handed to the position axis once the
    # burst's phase is known.
    duration_ms: int = 0
    reading: Optional[int] = None


class AirPlaySource(BaseAudioSource):
    """AirPlay 2 source (Family B — passive player): external control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.LAN

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # shairport-sync writes to the output it was started on: a multiroom toggle
    # restarts it, and the sender has to reconnect.
    REROUTE = ReroutePolicy.END_SESSION
    # Milō cannot restart a sender's stream: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    # AirPlay 2 does not support remote playback control
    # (shairport-sync AIRPLAY2.md: "Remote control facilities are not implemented"),
    # so no commands are registered — command() rejects every command as unknown.
    COMMANDS = {}
    COMMAND_SCOPES = {}

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="airplay",
            service_name="milo-airplay.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )
        self._metadata_pipe = self._config.get("metadata_pipe", "/tmp/shairport-sync-metadata")
        self._metadata_reader: Optional[MetadataReader] = None
        self.auto_stop_enabled = True

    # === Lifecycle ===

    async def _do_start(self) -> bool:
        """Start shairport-sync and the metadata reader."""
        try:
            if not await self._start_service_and_wait():
                return False
            await self._load_auto_stop_config()
            await self._ensure_metadata_pipe()
            self._metadata_reader = MetadataReader(self._metadata_pipe, on_event=self._on_pipe_event)
            await self._metadata_reader.start()
            self._publish()
            return True
        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        await self.end_session(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    async def _do_release(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _cleanup(self) -> None:
        if self._metadata_reader:
            await self._metadata_reader.stop()
            self._metadata_reader = None
        # What the reader posted and nobody handled belongs to this run: the
        # next one starts from nothing.
        self._discard_feed()

    async def _request_end(self, session: Session) -> bool:
        return await drop_session()

    # === The pipe ===

    async def _on_pipe_event(self, event: PipeEvent) -> None:
        """The reader's callback: it runs on the reader's task, so it posts."""
        self._post_feed(event)

    async def _handle_feed(self, events) -> None:
        # A STOP or RELEASE that cuts this handler ends the session and
        # discards the feed with it (_cleanup).
        if self._metadata_reader is None:
            return
        while events:
            await self._apply(events.pop(0))
        session = self._session
        if session is not None:
            await self.reconcile(DaemonSnapshot(session.sender, self._phase_of(session)))
            if self._session is session and session.reading is not None:
                reading, session.reading = session.reading, None
                self._observe_position(reading)
        # Compared, not repeated: a burst that only moved the playhead (`prgr`,
        # every 5-15 s) travels on the position axis, never as a full state.
        self._publish_changes()
        if self._session is not None:
            # A live session is the answer to the only error this source
            # raises — the daemon dying under the previous one. Without this
            # the banner would outlive its cause and sit over a sender that
            # reconnected fine; a no-op when no error is active.
            self.broadcast_error_cleared()

    async def _apply(self, event: PipeEvent) -> None:
        """One announcement, applied to the session it is about."""
        if event.kind == "disc":
            await self._sender_left(event.value)
            return
        session = self._session
        if session is None and event.kind not in _OPENINGS:
            return
        sender = event.value if event.kind == "conn" else (session.sender if session else None)
        if event.kind == "conn":
            self._logger.info(f"AirPlay client connected (IP: {event.value})")
        # Another sender's session opens CONNECTED: nothing is known of it yet.
        same = session is not None and (sender is None or session.sender in (None, sender))
        session = await self.reconcile(
            DaemonSnapshot(sender, session.phase if same else Phase.CONNECTED)
        )
        if not isinstance(session, AirPlaySession):
            return
        kind = event.kind
        if kind == "client_name":
            session.client_name = event.value
        elif kind == "stream_begin":
            # A new stream is not paused until it says so: a Realtime one
            # (Spotify, after Music was paused) never sends the `pres`.
            session.stream, session.first_frame, session.paused = True, False, False
        elif kind == "stream_end":
            session.stream, session.first_frame = False, False
        elif kind == "paused":
            session.paused = True
        elif kind == "resumed":
            session.paused = False
        elif kind == "first_frame":
            session.first_frame = session.streamed = True
        elif kind == "stream_type":
            session.stream_type = event.value
        elif kind == "tags":
            self._on_tags(session, event.value, event.rtptime)
        elif kind == "artwork":
            self._on_artwork(session, event.value)
        elif kind == "artwork_withdrawn":
            self._on_artwork_withdrawn(session)
        elif kind == "progress":
            self._on_progress(session, *event.value)

    async def _sender_left(self, ip: Optional[str]) -> None:
        """A `disc` names the sender it is for, and it can arrive long after
        that sender was replaced: measured 2026-09-03, a phone left at
        19:12:13, the next sender was on air at 19:12:18, and the first one's
        'disc' landed at 19:13:13 — sixty seconds into someone else's session.
        A 'disc' for anyone but the sender on air is therefore ignored. When
        either address is unknown there is nothing to tell them apart, and the
        goodbye stands.
        """
        session = self._session
        if session is None:
            return
        if ip is not None and session.sender is not None and ip != session.sender:
            self._logger.info(
                f"Ignoring a disconnect for {ip}, which is not the sender on air ({session.sender})"
            )
            return
        self._logger.info(f"AirPlay client disconnected (IP: {ip})")
        await self.reconcile(None)

    @staticmethod
    def _phase_of(session: AirPlaySession) -> Phase:
        """The phase the announced facts give (owner decisions 2026-09-23)."""
        if session.paused:
            return Phase.PAUSED
        if not session.stream:
            # Played, then sends nothing while still connected: a pause.
            return Phase.PAUSED if session.streamed else Phase.CONNECTED
        if not (session.first_frame and session.stream_type):
            return Phase.LOADING
        return Phase.PLAYING if session.stream_type == BUFFERED else Phase.CONNECTED

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return AirPlaySession(phase=snapshot.phase, sender=snapshot.sender)

    # === Tags and cover ===

    def _on_tags(self, session: AirPlaySession, tags: Dict[str, Any], track_id: Optional[str]) -> None:
        """Track metadata from the pipe (title, artist, album). They never
        touch the cover: see `_on_artwork`.

        A bundle carries only the DAAP tags the sender put in it, so one
        arriving without an `asar` used to leave the previous track's artist
        standing — published under the new title, for the whole of it. A
        bundle under a *new* rtptime is a different track and owns all three
        fields, absences included; one under the stamp already on screen is an
        amendment to it and merges. A sender that sends no RTP-Info stamps
        nothing, so every bundle reads as an amendment and keeps what it had.
        """
        amendment = track_id is None or track_id == session.track_id
        session.track_id = track_id
        session.tags = {
            key: (tags.get(key, session.tags.get(key, "")) if amendment else tags.get(key, ""))
            for key in ("title", "artist", "album")
        }

    def _on_artwork(self, session: AirPlaySession, data: bytes) -> None:
        """A picture from the pipe: it is the cover, kept in memory and served
        via the endpoint, until the sender pushes another or withdraws it.

        That is shairport-sync's own rule, and nothing finer is available: the
        pipe's rtptime on a picture is where the sender was when it wrote the
        request, not which track the picture is for. Paired with the tags' by
        distance, it dropped a cover that was the playing track's own 8 s into
        it on an iPhone (E68) — measured picture-to-tags gaps of 128-146 ms,
        but 293 and 565 ms too, where a real track change came as close as
        535 ms. No threshold separates those.

        Also decodes pixel dimensions so the frontend can gate the rich
        player on artwork quality: browser audio (no MediaSession cover) ends
        up as a small favicon / app-icon, whereas real senders (Apple Music,
        Spotify desktop) push a high-resolution cover. The width is published
        as `details.artwork_width`; the display policy lives on the frontend
        (useRichDisplay).
        """
        self._disarm_timer("artwork")
        digest = hashlib.md5(data).hexdigest()[:12]
        if session.cover is not None and session.cover.hash == digest:
            # An iPhone re-sends the same image up to three times in a track.
            return
        # shairport-sync sends JPEG or PNG
        mime = "image/png" if data[:8] == b'\x89PNG\r\n\x1a\n' else "image/jpeg"
        width, height = decode_artwork_dimensions(data, self._logger, "AirPlay")
        session.cover = Cover(data=data, mime=mime, hash=digest, width=width)
        self._logger.info(f"AirPlay artwork {width}x{height} ({mime})")

    def _on_artwork_withdrawn(self, session: AirPlaySession) -> None:
        """The sender's "no picture": the cover goes if no picture replaces it
        within ARTWORK_SETTLE_SECONDS, counted from the first withdrawal."""
        if session.cover is not None and not self._timer_armed("artwork"):
            self._arm_timer("artwork", ARTWORK_SETTLE_SECONDS, session)

    # === Progress ===

    def _on_progress(self, session: AirPlaySession, start: int, current: int, end: int) -> None:
        """A progress snapshot (RTP frames at 44100 Hz): the length, and a
        playhead reading for the position axis."""
        if end <= start:
            return
        session.duration_ms = int((end - start) / AIRPLAY_SAMPLE_RATE * 1000)
        session.reading = max(0, int((current - start) / AIRPLAY_SAMPLE_RATE * 1000))

    async def _on_timer(self, name: str, token: object) -> None:
        session = self._session
        if session is not token or not isinstance(session, AirPlaySession):
            return
        if name == "artwork":
            self._logger.info("AirPlay artwork withdrawn and not replaced: removed")
            session.cover = None
            self._publish()

    # === Publication ===

    async def _ensure_metadata_pipe(self) -> None:
        """Ensure metadata pipe exists."""
        if not os.path.exists(self._metadata_pipe):
            try:
                os.mkfifo(self._metadata_pipe)
            except FileExistsError:
                pass
            except PermissionError:
                self._logger.warning(
                    f"Cannot create metadata pipe {self._metadata_pipe} "
                    "(will be created by shairport-sync)"
                )

    # === The view (docs: "le fil") ===

    def _session_fields(self, session: AirPlaySession) -> Dict[str, Any]:
        cover = session.cover
        return {
            **session.tags,
            "artwork": cover.url if cover else None,
            "senders": [session.client_name] if session.client_name else [],
            "duration_ms": session.duration_ms or None,
        }

    def _details(self) -> Optional[AirPlayDetails]:
        session = self._session
        if not isinstance(session, AirPlaySession):
            return None
        cover = session.cover
        return AirPlayDetails(artwork_width=cover.width if cover else None)

    # === Public API ===

    def get_artwork(self) -> Optional[Tuple[bytes, str]]:
        """Return current artwork as (data, mime_type), or None."""
        session = self._session
        if isinstance(session, AirPlaySession) and session.cover is not None:
            return session.cover.data, session.cover.mime
        return None
