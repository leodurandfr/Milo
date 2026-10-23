# backend/sources/airplay/source.py
"""
AirPlay 2 audio source using shairport-sync.

The session belongs to shairport-sync, not to Milō: a sender connects, streams,
pauses and leaves on its own. The source follows it through `reconcile()`
(docs: source architecture, "reconcile"), from two feeds that each say what the
other cannot:

- the metadata pipe (metadata_reader.py) carries the session: who connected
  and left, their name, each stream's begin, end, pause and type;
- shairport-sync's D-Bus object (remote.py) carries what the sender plays —
  the item, its playhead, the commands it takes, its shuffle and repeat, its
  volume slider — and is where Milō sends the transport, the modes and the
  volume back. Since 5.6 none of that comes through the pipe any more.

Measured on shairport-sync 5.5.1 (2026-09-23, an iPhone and a Mac) and 5.6
(2026-10-16, a Mac, iPhone Music and iPhone Spotify), which the phase follows:

- iPhone Music opens a **Buffered** stream, which reports its pauses on the
  pipe (`paus`/`pres`), at once; its item reports the pause (PlaybackRate 0)
  only with the next update, seconds later, stamped at the pause. A Mac and
  Spotify open a **Realtime** stream, which reports no pause on the pipe; their
  item reports it at once. So a pause is either, and an item's PlaybackRate 0
  counts only if it was stamped after the stream last began or resumed — the
  late report arrives after the resume it precedes.
- A sender with an item playing is PLAYING whatever its stream type; without
  one, a Realtime stream is CONNECTED (a Mac's system audio streams silence
  through a pause, and nothing says so).
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

The commands go to the sender (Mac, iPhone Music and Spotify all obeyed each),
and `controls` lists those it says it takes (`CommandInformation`). There is no
seek: shairport-sync 5.6 has no request that carries a position.

The cover is the picture file shairport-sync writes for the item, read once
per file and served from memory via a dedicated HTTP endpoint. shairport-sync
re-sends the item without its picture and then with it 8-90 ms later on most
updates, so a missing path withdraws nothing while the item's
ArtworkIdentifier is the one on screen; a new identifier without a picture
removes the cover only if none comes within ARTWORK_SETTLE_SECONDS.

The volume is one level on both sides: the sender's slider moves Milō's
volume, and Milō's volume — whoever moved it — moves the sender's slider.
CamillaDSP stays the only attenuation (`ignore_volume_control`): shairport-sync
reports the slider and never scales the samples. When a sender arrives, Milō's
level wins: it is pushed to the sender, and what the sender reports of its own
for VOLUME_HOLD_S after is ignored (a Mac reports a ramp as it connects).
"""
import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from backend.core import audio_source
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.audio_wire import AirPlayDetails, RepeatMode
from backend.core.models.commands import SetRepeatParams, SetShuffleParams
from backend.core.models.session import (
    CommandScope, DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy,
    Session,
)
from backend.sources.airplay.metadata_reader import MetadataReader, PipeEvent
from backend.sources.airplay.remote import NowPlaying, RemoteUpdate, ShairportRemote
from backend.shared.artwork import decode_artwork_dimensions

# How long a withdrawn cover stays on screen while its replacement is in flight.
#
# A sender changing tracks announces the new item before its picture, so taking
# the gap literally would publish a state with no cover between the two, and the
# frontend's untrusted-sender gate (UNTRUSTED_SENDER_MIN_ARTWORK_PX) reads a
# missing artwork_width as "this sender pushes no real cover": the player is
# swapped for the status card and back. A picture before the deadline replaces
# the cover; none, and the cover goes. Sized on the slowest replacement
# measured, a Mac's 5.4 s on a track change (5.5.1).
ARTWORK_SETTLE_SECONDS = 8.0

# Quitting the app on a Mac (Music, Spotify; measured 2026-10-23) leaves the Mac
# connected and empties the item (`{}`) and the commands: nothing is playing any
# more, and the player gives way to the status card. Held this long first, so a
# `{}` the next item follows at once never drops the player.
ITEM_GONE_S = 1.0

# The stream type that reports its pauses; every other one (Realtime, a
# classic AirPlay 1 stream) is CONNECTED while it flows with no item playing.
BUFFERED = "Buffered"

# A track change is not a pause (measured 2026-10-16, Milō's next/prev): iPhone
# Music writes `paus` then `pres` 90-250 ms later — or stays at `paus` for
# seconds while it loads the track —, a Mac announces the new item at rate 0
# about a second before rate 1. Both are LOADING while the sender loads the
# track, up to SKIP_LOADING_S, and only a pause after that. A `paus` Milō did
# not ask for is held PAUS_SETTLE_S before it shows (the sender's own skip is
# the same paus/pres pair); one that follows Milō's pause shows at once, and so
# does one that follows Milō's next/prev within SKIP_WINDOW_S as LOADING.
PAUS_SETTLE_S = 0.6
SKIP_WINDOW_S = 2.0
SKIP_LOADING_S = 10.0
# Where a restarted track reports its playhead: 0:00 (measured, a Mac's prev).
RESTART_AT_MS = 500

# How far the sender's clock and the pipe's reception can disagree about one
# moment. An item stamped at a resume reaches Milō with a stamp ~2 ms *before*
# Milō heard the `pres` (measured 2026-10-16: a restart by prev, 10:50:30.7745
# against 30.776); the stale items a pause leaves behind are seconds older.
CLOCK_SLACK_S = 0.5

# What opens a session when none is live: a sender connecting, naming itself,
# or starting a stream (measured, every session opens with `conn`).
_OPENINGS = frozenset({"conn", "client_name", "stream_begin"})

# The MediaRemote commands a sender lists in CommandInformation, by number
# (measured: a Mac and iPhone Music list 6/7 and 25/26, Spotify only 25/26).
_PLAY, _PAUSE, _TOGGLE, _NEXT, _PREV = 0, 1, 2, 4, 5
_SHUFFLE = frozenset({6, 25})
_REPEAT = frozenset({7, 26})

# The sender's slider: AirPlay volume runs -30..0 dB, linear on the slider, and
# -144 is muted (measured on the Mac: -22.5 put its output at 25 %).
AIRPLAY_MIN_DB = -30.0
AIRPLAY_MUTED_DB = -144.0
# How long a sender's own volume reports are ignored after Milō's level was
# pushed to it on arrival (a Mac ramps 0 -> its level within 100 ms of `conn`).
VOLUME_HOLD_S = 2.0
# Two levels this close are the same position on either slider.
VOLUME_EPSILON = 0.005


async def read_artwork(path: str) -> bytes:
    """The picture file shairport-sync wrote, read off the event loop."""
    return await asyncio.to_thread(Path(path).read_bytes)


def sender_level(airplay_db: float) -> float:
    """The sender's slider as a 0..1 level."""
    if airplay_db <= AIRPLAY_MIN_DB:
        return 0.0
    return min(1.0, (airplay_db - AIRPLAY_MIN_DB) / -AIRPLAY_MIN_DB)


def sender_db(level: float, muted: bool) -> float:
    """A 0..1 level as the sender's slider."""
    if muted:
        return AIRPLAY_MUTED_DB
    return AIRPLAY_MIN_DB * (1.0 - max(0.0, min(1.0, level)))


@dataclass(frozen=True)
class MiloVolume:
    """Milō's volume moved (VolumeService's listener): the level to show on the
    sender's slider."""
    level: float
    muted: bool


@dataclass(eq=False)
class Cover:
    """The picture of the sender's item, read from the file shairport-sync wrote."""
    data: bytes
    mime: str
    hash: str
    width: int
    path: str
    artwork_id: Optional[str]

    @property
    def url(self) -> str:
        return f"/api/airplay/artwork?v={self.hash}"


@dataclass(eq=False)
class AirPlaySession(Session):
    """One sender's session: everything it owns goes with it (E19 — the next
    sender used to inherit the previous track's position and duration).

    The stream facts are what the pipe announced, the item what D-Bus last
    said; the phase is computed from both (`AirPlaySource._phase_of`).
    """
    client_name: Optional[str] = None
    stream: bool = False                # pbeg .. pend
    paused: bool = False                # paus .. pres
    first_frame: bool = False           # pffr since this stream began
    stream_type: Optional[str] = None   # styp, kept across the session's streams
    streamed: bool = False              # sound ever arrived in this session
    resumed_at: float = 0.0             # wall time of the last pbeg / pres
    paused_at: float = 0.0              # wall time of the last paus
    paus_settled: bool = False          # that paus outlived its hold
    skip_asked_at: float = 0.0          # Milō's last next/prev
    pause_asked_at: float = 0.0         # Milō's last pause
    track_wait_over: bool = False       # a new track at rate 0 outlived its wait
    now_playing: Optional[NowPlaying] = None
    track_id: Optional[int | str] = None      # the item whose playhead is anchored
    played_track: Optional[int | str] = None  # the last item seen moving
    cover: Optional[Cover] = None
    item_gone: bool = False             # the sender emptied its item (an app quit)
    volume_synced: bool = False         # Milō's level was pushed to this sender


class AirPlaySource(BaseAudioSource):
    """AirPlay 2 source (Family B — passive player, with the sender's transport)."""

    NETWORK_REQUIREMENT = NetworkRequirement.LAN

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # shairport-sync writes to the output it was started on: a multiroom toggle
    # restarts it, and the sender has to reconnect.
    REROUTE = ReroutePolicy.END_SESSION
    # Milō cannot restart a sender's stream: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    COMMANDS = {
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
        "set_shuffle": SetShuffleParams,
        "set_repeat": SetRepeatParams,
    }
    # Every one is asked of the sender holding the receiver.
    COMMAND_SCOPES = {name: CommandScope.SESSION for name in COMMANDS}

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
        self._remote: Optional[ShairportRemote] = None
        self._volume_service = None
        self.auto_stop_enabled = True
        self._reset_daemon_mirror()

    def _reset_daemon_mirror(self) -> None:
        """What shairport-sync's D-Bus object says, for this run of the daemon."""
        self._now_playing: Optional[NowPlaying] = None
        self._commands: frozenset = frozenset()
        # The commands an emptied list withdrew, shown while the item is held
        # (ITEM_GONE_S) so the buttons leave with the player, not before it.
        self._withdrawn: frozenset = frozenset()
        self._shuffle = False
        self._repeat: RepeatMode = "off"
        # The level both sliders were last put at, and until when the sender's
        # own reports are ignored.
        self._volume_level: Optional[Tuple[float, bool]] = None
        self._volume_hold_until = 0.0

    def set_volume_service(self, volume_service) -> None:
        """The volume the sender's slider and Milō's share (dependencies.py)."""
        self._volume_service = volume_service
        volume_service.add_state_listener(self.on_volume_changed)

    def on_volume_changed(self, level: float, muted: bool) -> None:
        """VolumeService's listener: runs outside the actor, so it posts."""
        if self._remote is not None:
            self._post_feed(MiloVolume(level, muted))

    # === Lifecycle ===

    async def _do_start(self) -> bool:
        """Start shairport-sync, the metadata reader and the D-Bus link."""
        try:
            if not await self._start_service_and_wait():
                return False
            await self._load_auto_stop_config()
            await self._ensure_metadata_pipe()
            self._metadata_reader = MetadataReader(self._metadata_pipe, on_event=self._on_pipe_event)
            await self._metadata_reader.start()
            await self._start_remote()
            self._publish()
            return True
        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _start_remote(self) -> None:
        """Fail open: without D-Bus the sender still plays, with no item and
        no commands."""
        remote = ShairportRemote(on_update=self._post_feed)
        try:
            await remote.start()
        except Exception as e:
            self._logger.error(f"shairport-sync's D-Bus interface is unreachable: {e!r}")
            await remote.stop()
            return
        self._remote = remote

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
        if self._remote:
            await self._remote.stop()
            self._remote = None
        # What the feeds posted and nobody handled belongs to this run: the
        # next one starts from nothing.
        self._discard_feed()
        self._reset_daemon_mirror()

    async def _request_end(self, session: Session) -> bool:
        return self._remote is not None and await self._remote.drop_session()

    # === The feeds ===

    async def _on_pipe_event(self, event: PipeEvent) -> None:
        """The reader's callback: it runs on the reader's task, so it posts."""
        self._post_feed(event)

    async def _handle_feed(self, events) -> None:
        # A STOP or RELEASE that cuts this handler ends the session and
        # discards the feed with it (_cleanup).
        if self._metadata_reader is None:
            return
        while events:
            event = events.pop(0)
            if isinstance(event, RemoteUpdate):
                await self._apply_remote(event)
            elif isinstance(event, MiloVolume):
                # A knob turn is dozens of states: the slider goes to the last.
                if not any(isinstance(later, MiloVolume) for later in events):
                    await self._push_volume(event.level, event.muted)
            else:
                await self._apply(event)
        session = self._session
        if isinstance(session, AirPlaySession):
            await self._follow_now_playing(session)
            await self.reconcile(DaemonSnapshot(session.sender, self._phase_of(session)))
            if self._session is session:
                self._follow_playhead(session)
        # Compared, not repeated: an item update that only moved the playhead
        # travels on the position axis, never as a full state.
        self._publish_changes()
        if self._session is not None:
            # A live session is the answer to the only error this source
            # raises — the daemon dying under the previous one. Without this
            # the banner would outlive its cause and sit over a sender that
            # reconnected fine; a no-op when no error is active.
            self.broadcast_error_cleared()

    async def _apply(self, event: PipeEvent) -> None:
        """One pipe announcement, applied to the session it is about."""
        if event.kind == "disc":
            await self._sender_left(event.value)
            return
        session = self._session
        if session is None and event.kind not in _OPENINGS:
            return
        sender = event.value if event.kind == "conn" else (session.sender if session else None)
        if event.kind == "conn":
            self._logger.info(f"AirPlay client connected (IP: {event.value})")
            if session is not None and None not in (session.sender, sender) and session.sender != sender:
                # A takeover whose goodbye is late (a minute, measured): what
                # D-Bus holds is the previous sender's until the newcomer's
                # own arrives, which follows its `conn`.
                self._forget_sender()
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
            session.resumed_at = audio_source.wall_time()
        elif kind == "stream_end":
            session.stream, session.first_frame = False, False
        elif kind == "paused":
            session.paused, session.paus_settled = True, False
            session.paused_at = audio_source.wall_time()
            # Looked at again once it can no longer be a track change.
            self._arm_timer("paus", SKIP_LOADING_S if self._skipping(session) else PAUS_SETTLE_S, session)
        elif kind == "resumed":
            session.paused = False
            session.resumed_at = audio_source.wall_time()
            self._disarm_timer("paus")
        elif kind == "first_frame":
            session.first_frame = session.streamed = True
            if not session.volume_synced:
                await self._send_milo_volume(session)
        elif kind == "stream_type":
            session.stream_type = event.value

    async def _apply_remote(self, update: RemoteUpdate) -> None:
        """One property the sender moved, mirrored for the session to read."""
        if update.kind == "now_playing":
            self._now_playing = update.value
        elif update.kind == "commands":
            if not update.value and self._commands:
                self._withdrawn = self._commands
            self._commands = update.value
        elif update.kind == "shuffle":
            self._shuffle = update.value
        elif update.kind == "repeat":
            self._repeat = update.value
        elif update.kind == "volume":
            await self._follow_sender_volume(update.value)

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
        self._forget_sender()
        await self.reconcile(None)

    def _forget_sender(self) -> None:
        """The departed sender's item, commands and modes: shairport-sync
        clears the first two only when the next sender connects (and never
        writes the modes itself), and that one must not show them."""
        self._now_playing, self._commands, self._withdrawn = None, frozenset(), frozenset()
        self._shuffle, self._repeat = False, "off"

    @staticmethod
    def _skipping(session: AirPlaySession) -> bool:
        """The last `paus` came with Milō's next/prev."""
        return 0.0 <= session.paused_at - session.skip_asked_at <= SKIP_WINDOW_S

    @staticmethod
    def _changing_track(session: AirPlaySession, item: NowPlaying) -> bool:
        """An item at rate 0 that is a track change, not a pause: another
        track than the one last seen moving, or the same one right after
        Milō's next/prev — a prev that restarts the track keeps its item
        (measured 2026-10-23, a Mac: 0.9 s at rate 0 and 0:00, then rate 1).
        Only at the top of the track, and only if no pause was asked since:
        a pause right after a skip is still a pause."""
        if session.played_track is None:
            return False
        if item.track_id != session.played_track:
            return True
        return (
            item.at is not None
            and item.elapsed_ms is not None and item.elapsed_ms <= RESTART_AT_MS
            and session.skip_asked_at > session.pause_asked_at
            and -CLOCK_SLACK_S <= item.at - session.skip_asked_at <= SKIP_WINDOW_S
        )

    def _phase_of(self, session: AirPlaySession) -> Phase:
        """The phase the announced facts give (owner decisions 2026-09-23,
        the item's rate and track changes since 5.6 — see the module docstring
        and SKIP_LOADING_S)."""
        item = session.now_playing
        if session.item_gone:
            # Linked, with nothing playing: the status card, whatever the
            # stream did before the app quit.
            return Phase.CONNECTED
        if session.paused:
            if self._skipping(session):
                return Phase.PAUSED if session.paus_settled else Phase.LOADING
            asked = 0.0 <= session.paused_at - session.pause_asked_at <= SKIP_WINDOW_S
            if asked or session.paus_settled:
                return Phase.PAUSED
            # Within the hold: the stream's own facts below decide.
        if item is not None and item.rate <= 0 and (item.at or 0.0) >= session.resumed_at:
            if self._changing_track(session, item) and not session.track_wait_over:
                return Phase.LOADING
            return Phase.PAUSED
        if not session.stream:
            # Played, then sends nothing while still connected: a pause.
            return Phase.PAUSED if session.streamed else Phase.CONNECTED
        if not (session.first_frame and session.stream_type):
            return Phase.LOADING
        if item is not None and item.rate > 0:
            return Phase.PLAYING
        return Phase.PLAYING if session.stream_type == BUFFERED else Phase.CONNECTED

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return AirPlaySession(phase=snapshot.phase, sender=snapshot.sender)

    # === The sender's item ===

    async def _follow_now_playing(self, session: AirPlaySession) -> None:
        """Take the item D-Bus last reported. An empty one (`{}`) under an item
        on screen is the app quitting: the item goes after ITEM_GONE_S unless
        another replaces it first."""
        item = self._now_playing
        if item is None:
            if session.now_playing is not None and not self._timer_armed("item"):
                self._arm_timer("item", ITEM_GONE_S, session)
            return
        self._disarm_timer("item")
        if item is session.now_playing:
            return
        session.now_playing, session.item_gone = item, False
        if item.rate > 0:
            session.played_track = item.track_id
            self._disarm_timer("track")
        elif self._changing_track(session, item):
            # A new track at rate 0: LOADING until it moves, or a pause.
            if not self._timer_armed("track"):
                session.track_wait_over = False
                self._arm_timer("track", SKIP_LOADING_S, session)
        cover = session.cover
        if item.artwork_path is not None:
            if cover is None or cover.path != item.artwork_path:
                await self._load_cover(session, item)
            else:
                cover.artwork_id = item.artwork_id
                self._disarm_timer("artwork")
        elif cover is not None and item.artwork_id != cover.artwork_id:
            # A new picture is announced and not written yet.
            if not self._timer_armed("artwork"):
                self._arm_timer("artwork", ARTWORK_SETTLE_SECONDS, session)

    async def _load_cover(self, session: AirPlaySession, item: NowPlaying) -> None:
        """The picture file of the item: it is the cover, kept in memory and
        served via the endpoint, until another replaces it.

        Also decodes pixel dimensions so the frontend can gate the rich
        player on artwork quality: browser audio (no MediaSession cover) ends
        up as a small favicon / app-icon, whereas real senders (Apple Music,
        Spotify) push a 600x600 cover. The width is published as
        `details.artwork_width`; the display policy lives on the frontend
        (useRichDisplay).
        """
        try:
            data = await read_artwork(item.artwork_path)
        except OSError as e:
            self._logger.warning(f"AirPlay artwork unreadable: {e}")
            # Another track's picture must not stay on this one for good.
            cover = session.cover
            if cover is not None and cover.artwork_id != item.artwork_id and not self._timer_armed("artwork"):
                self._arm_timer("artwork", ARTWORK_SETTLE_SECONDS, session)
            return
        self._disarm_timer("artwork")
        digest = hashlib.md5(data).hexdigest()[:12]
        if session.cover is not None and session.cover.hash == digest:
            session.cover.path, session.cover.artwork_id = item.artwork_path, item.artwork_id
            return
        mime = "image/png" if data[:8] == b'\x89PNG\r\n\x1a\n' else "image/jpeg"
        width, height = decode_artwork_dimensions(data, self._logger, "AirPlay")
        session.cover = Cover(
            data=data, mime=mime, hash=digest, width=width,
            path=item.artwork_path, artwork_id=item.artwork_id,
        )
        self._logger.info(f"AirPlay artwork {width}x{height} ({mime})")

    def _follow_playhead(self, session: AirPlaySession) -> None:
        """The item's playhead, on the position axis: anchored on a new item,
        observed otherwise (the base moves the anchor only past its tolerance).

        An item stamped before the stream last paused or resumed (give or take
        CLOCK_SLACK_S), or whose rate disagrees with the phase, is not a
        reading: iPhone Music pauses and resumes with the item it had before,
        which carried forward plays the pause too (its own pause report comes
        after the resume). The anchor, rebased at each phase change, already
        knows where it stands."""
        item = session.now_playing
        if item is None:
            return
        position = item.position_ms(audio_source.wall_time())
        if position is None:
            return
        # A session with no playhead takes the first one it is given: a Mac
        # connecting re-sends its item while the stream is still loading.
        if item.track_id != session.track_id or session.anchor is None:
            session.track_id = item.track_id
            self._anchor_position(position)
            return
        # An item that says playing over a pause (or the reverse) is about
        # another moment than this one, however recent its stamp.
        agrees = (item.rate > 0) is (session.phase is Phase.PLAYING)
        if agrees and (item.at or 0.0) >= max(session.resumed_at, session.paused_at) - CLOCK_SLACK_S:
            self._observe_position(position)

    async def _on_timer(self, name: str, token: object) -> None:
        session = self._session
        if session is not token or not isinstance(session, AirPlaySession):
            return
        if name == "artwork":
            self._logger.info("AirPlay artwork withdrawn and not replaced: removed")
            session.cover = None
            self._publish()
        elif name == "item":
            self._logger.info("AirPlay sender's item emptied and not replaced: cleared")
            session.now_playing, session.cover = None, None
            session.item_gone = True
            self._clear_position()
            self._disarm_timer("track")
            self._disarm_timer("artwork")
            await self.reconcile(DaemonSnapshot(session.sender, self._phase_of(session)))
            self._publish_changes()
        elif name in ("paus", "track"):
            # A held `paus` or a track change ran out: what it is now. A pause
            # stopped the playhead when it came, not when it was believed.
            if name == "paus":
                session.paus_settled = True
            else:
                session.track_wait_over = True
            anchor = session.anchor
            stopped = None
            if anchor is not None and session.paused:
                stopped = anchor.now(session.paused_at, self._session_fields(session).get("duration_ms"))
            await self.reconcile(DaemonSnapshot(session.sender, self._phase_of(session)))
            if stopped is not None and self._session is session and session.phase is Phase.PAUSED:
                self._anchor_position(stopped)
            self._publish_changes()

    # === Commands ===

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Asked of the sender. shairport-sync answering is all the reply says:
        the state follows what the sender then reports."""
        remote = self._remote
        if remote is None:
            return self.error_response("shairport-sync's D-Bus interface is unreachable")
        session = self._session
        if isinstance(session, AirPlaySession):
            if cmd in ("next", "prev"):
                session.skip_asked_at = audio_source.wall_time()
            elif cmd == "pause":
                session.pause_asked_at = audio_source.wall_time()
        # Milō's names are canonical across sources; shairport-sync's are its
        # own. A sender that lists only the toggle gets the toggle.
        if cmd == "pause":
            taken = await remote.send("Pause" if _PAUSE in self._commands else "PlayPause")
        elif cmd == "resume":
            taken = await remote.send("Play" if _PLAY in self._commands else "PlayPause")
        elif cmd == "next":
            taken = await remote.send("Next")
        elif cmd == "prev":
            taken = await remote.send("Previous")
        elif cmd == "set_shuffle":
            taken = await remote.set_shuffle(params.shuffle)
        elif cmd == "set_repeat":
            taken = await remote.set_repeat(params.mode)
        else:
            return self.error_response(f"Unknown command: {cmd}")
        if not taken:
            return self.error_response(f"'{cmd}' was refused by shairport-sync")
        return self.success_response()

    def _controls(self) -> List[str]:
        """What the sender says it takes (CommandInformation), the toggle by
        the phase."""
        session = self._session
        if not isinstance(session, AirPlaySession):
            return []
        commands = self._commands
        if not commands and self._timer_armed("item"):
            commands = self._withdrawn
        playing = session.phase in (Phase.PLAYING, Phase.LOADING)
        offered = (
            ("pause", playing and bool(commands & {_PAUSE, _TOGGLE})),
            ("resume", not playing and bool(commands & {_PLAY, _TOGGLE})),
            ("next", _NEXT in commands),
            ("prev", _PREV in commands),
            ("set_shuffle", bool(commands & _SHUFFLE)),
            ("set_repeat", bool(commands & _REPEAT)),
        )
        return [name for name, takes in offered if takes]

    # === Volume ===

    async def _follow_sender_volume(self, airplay_db: float) -> None:
        """The sender's slider moved: Milō's volume follows."""
        session = self._session
        volume = self._volume_service
        if volume is None or not isinstance(session, AirPlaySession):
            return
        if not session.volume_synced:
            # The sender's arrival: Milō's level wins.
            await self._send_milo_volume(session)
            return
        if audio_source.wall_time() < self._volume_hold_until:
            return
        if airplay_db <= AIRPLAY_MUTED_DB and self._volume_level is not None and self._volume_level[1]:
            # The mute Milō put on the sender's slider, written back.
            return
        level = sender_level(airplay_db)
        if self._volume_level is not None and abs(level - self._volume_level[0]) < VOLUME_EPSILON:
            return
        self._volume_level = (level, False)
        await volume.set_volume_db(volume.volume_config.denormalize(level), show_bar=True)

    async def _send_milo_volume(self, session: AirPlaySession) -> None:
        """Put the sender's slider at Milō's level, as a sender arrives."""
        volume = self._volume_service
        if volume is None:
            return
        state = await volume.get_volume_state()
        session.volume_synced = True
        self._volume_hold_until = audio_source.wall_time() + VOLUME_HOLD_S
        self._volume_level = None
        await self._push_volume(volume.volume_config.normalize(state.global_volume_db), state.global_mute)

    async def _push_volume(self, level: float, muted: bool) -> None:
        """Milō's volume moved: the sender's slider follows. A level both
        sides already show is not sent again — including the one the sender's
        own move just put Milō at."""
        session = self._session
        if self._remote is None or not isinstance(session, AirPlaySession) or not session.volume_synced:
            return
        previous = self._volume_level
        if previous is not None and previous[1] == muted and abs(previous[0] - level) < VOLUME_EPSILON:
            return
        self._volume_level = (level, muted)
        await self._remote.set_volume(sender_db(level, muted))

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
        item = session.now_playing
        cover = session.cover
        return {
            "title": item.title if item else None,
            "artist": item.artist if item else None,
            "album": item.album if item else None,
            "artwork": cover.url if cover else None,
            "senders": [session.client_name] if session.client_name else [],
            "duration_ms": item.duration_ms if item else None,
        }

    def _details(self) -> Optional[AirPlayDetails]:
        session = self._session
        if not isinstance(session, AirPlaySession):
            return None
        cover = session.cover
        return AirPlayDetails(
            artwork_width=cover.width if cover else None,
            shuffle=self._shuffle,
            repeat=self._repeat,
        )

    # === Public API ===

    def get_artwork(self) -> Optional[Tuple[bytes, str]]:
        """Return current artwork as (data, mime_type), or None."""
        session = self._session
        if isinstance(session, AirPlaySession) and session.cover is not None:
            return session.cover.data, session.cover.mime
        return None
