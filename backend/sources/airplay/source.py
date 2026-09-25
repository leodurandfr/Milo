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

Artwork is stored in memory and served via a dedicated HTTP endpoint.
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

# How long the cover in hand may outlive its pairing while the next one is
# still in flight.
#
# The tags and the picture are two SET_PARAMETER requests in no guaranteed
# order, so a track change leaves one of the two unpaired whichever way round
# it arrives. This bounds the picture that is far enough from the tags on
# screen to be another track's (see _artwork_is_current, which is what decides
# that one stamped a few milliseconds off is simply this track's).
# Publishing that gap drops the artwork, and the frontend's untrusted-sender
# gate (UNTRUSTED_SENDER_MIN_ARTWORK_PX) reads a missing artwork_width as
# "this sender pushes no real cover": AudioPlayerFull is swapped for the status
# card and back, which is visible as the player animating itself out and in.
#
# Holding the cover across the gap keeps what the pairing is for -- a coverless
# track must not wear the previous one's for its whole duration -- and bounds it
# explicitly instead of deciding on an instant. Two delays were measured
# against a macOS sender: ~30 ms when the sender merely re-sends its bundle
# under a fresh rtptime, which every transport action makes it do, and 5.4 s on
# a genuine track change where it had to produce the new cover. The bound has
# to cover the second, so what it sizes is not the gap but how long a cover may
# be wrong -- and at a track change inside one album the held cover IS the new
# track's, so only an album boundary onto a coverless track shows a stale one,
# with the title and artist beside it already correct throughout.
ARTWORK_SETTLE_SECONDS = 8.0

# How far apart the two stamps may be and still be one track's.
#
# The rtptime is a playback position, not an identity: a sender writes the tags
# and the picture as two SET_PARAMETER requests and stamps each with where it
# was at the time, so one track's two stamps differ by the few milliseconds
# between the writes. Measured on both senders 2026-09-03, the whole spread:
#
#   same track, picture stamped later    +1056 frames   (+24 ms, iPhone)
#   same track, picture stamped earlier  -1408 frames   (-32 ms, macOS)
#   a track change (the cover is the previous track's)
#                                 -23584 .. -1019040    (-535 ms .. -23.1 s)
#   two senders, unrelated RTP clocks       -215021171  (-81 min)
#
# 250 ms sits in the gap: eight times the largest drift seen inside a track,
# half the smallest step seen across a track change. Equality is what this
# replaced, and equality is what a drifting stamp never satisfies -- the hold
# expired on covers that were the playing track's own, taking the player off
# the screen mid-track on both senders.
ARTWORK_PAIRING_TOLERANCE_FRAMES = int(0.250 * AIRPLAY_SAMPLE_RATE)

# The stream type that reports its pauses; every other one (Realtime, a
# classic AirPlay 1 stream) is CONNECTED while it flows.
BUFFERED = "Buffered"

# What opens a session when none is live: a sender connecting, naming itself,
# or starting a stream (measured, every session opens with `conn`). Tags, a
# cover, a progress report arriving with no session are about one that ended.
_OPENINGS = frozenset({"conn", "client_name", "stream_begin"})


@dataclass(eq=False)
class Cover:
    """The cover in hand and the rtptime it was stamped with."""
    data: bytes
    mime: str
    hash: str
    width: int
    rtptime: Optional[str]

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
            self._on_artwork(session, event.value, event.rtptime)
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
        """Track metadata from the pipe (title, artist, album).

        Recording which track is on screen is all that is needed to move the
        cover with it: the publish pairs the two by rtptime. A track whose
        sender pushes no PICT of its own — plenty do not — would otherwise wear
        the previous one's cover for its whole duration.

        The tags are paired by the same stamp, and for the same reason. A
        bundle carries only the DAAP tags the sender put in it, so one arriving
        without an `asar` used to leave the previous track's artist standing —
        published under the new title, for the whole of it. A bundle under a
        *new* rtptime is a different track and owns all three fields, absences
        included; one under the stamp already on screen is an amendment to it
        and merges. A sender that sends no RTP-Info stamps nothing, so every
        bundle reads as an amendment and keeps what it had — the same trade
        the cover makes there, and for the same want of anything to pair on.
        """
        amendment = track_id is None or track_id == session.track_id
        session.track_id = track_id
        self._sync_artwork_hold(session)
        session.tags = {
            key: (tags.get(key, session.tags.get(key, "")) if amendment else tags.get(key, ""))
            for key in ("title", "artist", "album")
        }

    def _on_artwork(self, session: AirPlaySession, data: bytes, track_id: Optional[str]) -> None:
        """Artwork from the pipe: kept in memory, served via the endpoint.

        Also decodes pixel dimensions so the frontend can gate the rich
        player on artwork quality: browser audio (no MediaSession cover) ends
        up as a small favicon / app-icon, whereas real senders (Apple Music,
        Spotify desktop) push a high-resolution cover. The width is published
        as `details.artwork_width`; the display policy lives on the frontend
        (useRichDisplay).

        The rtptime is recorded before the dedupe: two tracks off one album send
        the identical image, and the picture that changed nothing still moved
        which track the cover belongs to.
        """
        digest = hashlib.md5(data).hexdigest()[:12]
        if session.cover is not None and session.cover.hash == digest:
            session.cover.rtptime = track_id
            self._sync_artwork_hold(session)
            return
        # shairport-sync sends JPEG or PNG
        mime = "image/png" if data[:8] == b'\x89PNG\r\n\x1a\n' else "image/jpeg"
        width, height = decode_artwork_dimensions(data, self._logger, "AirPlay")
        session.cover = Cover(data=data, mime=mime, hash=digest, width=width, rtptime=track_id)
        self._logger.info(f"AirPlay artwork {width}x{height} ({mime})")
        self._sync_artwork_hold(session)

    @staticmethod
    def _artwork_is_current(session: AirPlaySession) -> bool:
        """Whether the cover in hand belongs to the tags on screen.

        Nearness, not equality, and the difference is a sender's. The rtptime
        is where the sender was when it wrote the request, not the per-track
        identity rtsp.c describes: a sender re-sends its bundle inside one
        track and each copy carries a fresh stamp, so the picture's and the
        tags' differ by the milliseconds between the two writes. Equality then
        holds only when a bundle happens to land exactly on the picture's
        stamp, and when it does not, no later one comes to meet it -- the hold
        expired on a cover that was the playing track's own and took
        AudioPlayerFull off the screen mid-track, for as long as 11 s.

        Measured on both senders, the drift runs both ways: +24 ms on an
        iPhone, -32 ms on a Mac. What separates that from a real track change
        is distance, not direction -- the nearest track change measured is
        535 ms away, seventeen times further. ARTWORK_PAIRING_TOLERANCE_FRAMES
        carries the numbers.
        """
        cover = session.cover
        if cover is None:
            return False
        if cover.rtptime == session.track_id:
            return True
        if cover.rtptime is None or session.track_id is None:
            return False
        try:
            # RTP timestamps are 32-bit and wrap, so the distance between two
            # of them is the serial one, not the integer one.
            delta = (int(cover.rtptime) - int(session.track_id)) % (1 << 32)
        except ValueError:
            return False
        if delta >= (1 << 31):
            delta -= 1 << 32
        return abs(delta) <= ARTWORK_PAIRING_TOLERANCE_FRAMES

    def _sync_artwork_hold(self, session: AirPlaySession) -> None:
        """Arm or release the hold on the cover in hand.

        The tags and the picture are two SET_PARAMETER requests in no
        guaranteed order, so either can be the one still in flight — and the
        gap is the same gap. Tags first leaves the new stamp with no picture
        yet; picture first leaves a picture stamped for a track the tags have
        not announced, and the publish, judging on equality alone, dropped the
        cover from a state still carrying the *previous* track's title. The
        frontend reads a missing artwork_width as "this sender pushes no real
        cover" and swapped the player for the status card and back, which is
        the same flicker from the other side.

        Which of the two is in flight is what `_artwork_is_current` reads off
        the distance between the stamps, and only a picture far enough from the
        tags to be another track's is held on a deadline. Arming the deadline
        for a picture that was merely stamped a few milliseconds off its own
        track is what emptied the cover mid-track, on both senders.
        """
        if session.cover is not None and not self._artwork_is_current(session):
            self._arm_timer("artwork", ARTWORK_SETTLE_SECONDS, session)
        else:
            self._disarm_timer("artwork")

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
            # The hold ran out: the cover goes, once, instead of on every event.
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

    def _published_cover(self, session: AirPlaySession) -> Optional[Cover]:
        """The cover is published for the track it was stamped for; a pending
        hold keeps it for the few ms a newly-stamped track's own picture may
        still be in flight."""
        cover = session.cover
        if cover is not None and (self._artwork_is_current(session) or self._timer_armed("artwork")):
            return cover
        return None

    def _session_fields(self, session: AirPlaySession) -> Dict[str, Any]:
        cover = self._published_cover(session)
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
        cover = self._published_cover(session)
        return AirPlayDetails(artwork_width=cover.width if cover else None)

    # === Public API ===

    def get_artwork(self) -> Optional[Tuple[bytes, str]]:
        """Return current artwork as (data, mime_type), or None."""
        session = self._session
        if isinstance(session, AirPlaySession) and session.cover is not None:
            return session.cover.data, session.cover.mime
        return None
