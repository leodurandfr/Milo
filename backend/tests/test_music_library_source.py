# backend/tests/test_music_library_source.py
"""MusicLibrarySource playback + queue (P1-6), driven through the outside world.

Covers the play_context → gapless mpv queue path, transport commands
(pause/resume/next/prev/seek/play_index/set_shuffle/stop), the now-playing on
the wire (the session's title/artist/album/artwork, the details' queue/index/
shuffle, the resume point), the live
shuffle toggle, resume-on-return, the gapless advance and end of queue as mpv
announces them, the scrobble accounting Navidrome's play history is built from,
and the whole-catalog album walk the alphabetical grid is paged from.

Playback runs on `LibraryRig` (tests/test_mpv_sessions.py): a real
AudioStateMachine, mpv simulated as measured (tests/mpv_sim.py), a fake
Navidrome that records scrobbles, a fake storage layer. Assertions read what
reaches the outside: the published state, the error banners, what mpv was
sent, what Navidrome was told, and the command's answer.
"""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.core import audio_source
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.music_library import source as library_module
from backend.sources.music_library.source import MusicLibrarySource
from backend.tests.golden.harness import AsyncioProxy, settle
from backend.tests.golden.test_wire_music_library import FakeNavidrome
from backend.tests.test_mpv_sessions import WATCHDOG_S, LibraryRig

_real_sleep = asyncio.sleep


@pytest.fixture
def config():
    return {"mpv_socket": "/tmp/test-music-library-ipc.sock"}


@pytest.fixture
def source(config):
    """MusicLibrarySource with a mocked service manager and Navidrome client,
    for the catalog half, which never touches mpv.

    The StorageManager is constructed (cheap, fail-open — no udev touched) but
    never initialized, so no monitor thread starts.
    """
    src = MusicLibrarySource(config)

    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)

    src.get_navidrome_client = AsyncMock(
        return_value=Mock(
            stream_url=lambda song_id: f"http://nav/stream/{song_id}",
            scrobble=AsyncMock(return_value=True),
        )
    )
    return src


TRACKS = [
    {"id": "s1", "title": "One", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 100},
    {"id": "s2", "title": "Two", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 200},
    {"id": "s3", "title": "Three", "artist": "DP", "album": "Disc", "coverArt": "al1", "duration": 300},
]


def url(song_id):
    """The stream URL the (fake) Navidrome client hands mpv for a song."""
    return FakeNavidrome().stream_url(song_id)


def lengths(rig, tracks):
    """mpv reports each file's length once open — the same as its tags here."""
    for track in tracks:
        rig.mpv.durations[f"id={track['id']}&"] = float(track["duration"])


@pytest.fixture
def rig(monkeypatch):
    """The library on a real state machine, pause timeout at 120 s (never
    fires unless a test asks)."""
    rig = LibraryRig(monkeypatch)
    lengths(rig, TRACKS)
    return rig


@pytest.fixture
def idle_rig(monkeypatch):
    """The same, with a pause timeout short enough to end a paused session
    inside the step that paused it."""
    rig = LibraryRig(monkeypatch, settings={"audio.auto_stop_delay": 1})
    lengths(rig, TRACKS)
    return rig


async def play(rig, tracks=TRACKS, start_index=0, **extra):
    return await rig.command(
        "play_context", {"tracks": tracks, "start_index": start_index, **extra}
    )


def session(rig):
    """The live session on the wire, or None."""
    return rig.state()["session"]


def phase(rig):
    live = session(rig)
    return live["phase"] if live else None


def details(rig):
    """The library's own content on the wire: the queue, its index, shuffle
    and the current track's ids — the live queue, or the one a play press
    would reopen. None when there is neither."""
    return rig.state()["details"]


def anchor_ms(rig):
    """Where the last discontinuity put the playhead (the anchor's `ms`):
    exact, unlike the anchor aged on the wall clock."""
    return session(rig)["position"]["ms"]


def session_ends(rig):
    return [
        e["data"]["reason"] for e in rig.recorder.envelopes
        if e["category"] == "source" and e["type"] == "session_ended"
    ]


def sent_since(rig, mark):
    return rig.mpv.sent[mark:]


def loads_since(rig, mark):
    return [c for c in sent_since(rig, mark) if c[0] == "loadfile"]


class NoCredFile:
    """Navidrome's cred file is not there (provisioning not done, or rotated
    away): no client can be built."""

    @classmethod
    def from_cred_file(cls, *a, **k):
        return None


class TestCompliance:
    def test_default_socket(self):
        assert MusicLibrarySource()._mpv_socket == "/run/milo/music_library-ipc.sock"

    def test_commands_registered(self, source):
        for cmd in ("play_context", "play_index", "pause", "resume", "next", "prev", "seek", "stop"):
            assert cmd in source.COMMANDS


class TestPlayContext:

    async def test_builds_gapless_queue(self, rig):
        """One mpv playlist of per-id stream URLs, started at the picked entry.
        Breaks: the player (frontend, Milo-Mac) shows a queue mpv is not
        playing, or the gapless advance has no next entry to step to."""
        await rig.select()
        mark = len(rig.mpv.sent)

        result = await play(rig)

        assert result["success"] is True
        assert phase(rig) == "playing"
        assert details(rig)["queue"] == TRACKS
        assert details(rig)["queue_index"] == 0
        sent = sent_since(rig, mark)
        assert [c[1] for c in sent if c[0] == "loadfile"] == [url(t["id"]) for t in TRACKS]
        assert all(c[2] == "append" for c in sent if c[0] == "loadfile")
        assert sent[-1] == ("play_index", 0)

    async def test_start_index_respected(self, rig):
        """Tapping the third row of an album plays the third track. Breaks:
        the album always starts from its first track."""
        await rig.select()

        await play(rig, start_index=2)

        assert ("play_index", 2) in rig.mpv.sent[-2:]
        assert details(rig)["queue_index"] == 2
        assert details(rig)["track_id"] == "s3"
        assert session(rig)["duration_ms"] == 300_000

    async def test_shuffle_keeps_picked_track_first(self, rig):
        """Shuffle play from a row keeps that row's track first and shuffles the
        rest behind it. Breaks: the track the user tapped is not what plays."""
        await rig.select()

        # Deterministic shuffle: only the pick-to-front move shows.
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: None):
            await play(rig, start_index=1, shuffle=True)

        assert details(rig)["shuffle"] is True
        assert details(rig)["queue"][0] == TRACKS[1]
        assert details(rig)["queue_index"] == 0
        assert ("play_index", 0) in rig.mpv.sent[-2:]

    async def test_missing_id_rejected(self, rig):
        """A track with no id has no stream URL. Breaks: mpv is handed a queue
        with a hole in it (the frontend's play button then answers 200)."""
        await rig.select()
        mark = len(rig.mpv.sent)

        result = await rig.command("play_context", {"tracks": [{"title": "x"}]})

        assert result["success"] is False
        assert loads_since(rig, mark) == []

    async def test_requires_active_mpv(self, rig):
        """A play with the source not started answers a failure. Breaks: the
        route reports success over a library that plays nothing."""
        result = await play(rig)

        assert result["success"] is False
        assert rig.mpv.sent == []

    async def test_requires_catalog(self, rig, monkeypatch):
        """No Navidrome client (cred file missing) means no stream URL for any
        track. Breaks: a queue of dead URLs loaded, and a play answered OK."""
        monkeypatch.setattr(library_module, "NavidromeClient", NoCredFile)
        await rig.select()
        mark = len(rig.mpv.sent)

        result = await play(rig)

        assert result["success"] is False
        assert loads_since(rig, mark) == []

    async def test_load_failure_resets_to_ready(self, rig):
        """mpv refusing the queue ends the session it opened, with a banner.
        Breaks: the screen (and the lock screen via Milo-iOS) keeps an ACTIVE
        track with nothing loaded behind it."""
        await rig.select()
        rig.mpv.accept = False

        result = await play(rig)

        assert result["success"] is False
        assert session(rig) is None
        assert session_ends(rig) == ["load_failed"]
        assert rig.errors() == ["playback_failed"]


class TestAPausedQueueReplacedByAnother:
    """Pausing arms the auto-stop timer; starting another context must disarm
    it before anything is awaited (E49).

    What breaks when this fails: a timer that expires while the new playlist is
    loading stops mpv under it — the play then reports success and publishes a
    track that is not playing, and the resume point the auto-stop saves is the
    queue that was never heard.
    """

    async def test_the_new_playlist_is_not_stopped_while_it_loads(self, monkeypatch):
        """The pause timer's clock is held until the new playlist is loading,
        then let run out: an expiry landing mid-load is the case."""
        rig = LibraryRig(monkeypatch)            # pause timeout 120 s
        lengths(rig, TRACKS)
        delay_over = asyncio.Event()

        async def pause_timer_clock(delay, *a, **k):
            if delay > 1.0:
                await delay_over.wait()
            else:
                await _real_sleep(0)

        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(pause_timer_clock))
        await rig.select()
        await play(rig)
        await rig.command("pause")               # the pause timer is armed
        assert phase(rig) == "paused"

        loading, loaded = asyncio.Event(), asyncio.Event()
        real_loadfile = rig.mpv.loadfile

        async def slow_loadfile(stream, **kwargs):
            if not loading.is_set():
                loading.set()
                await loaded.wait()
            return await real_loadfile(stream, **kwargs)

        rig.mpv.loadfile = slow_loadfile
        second = asyncio.create_task(
            rig.source.command("play_context", {"tracks": TRACKS, "start_index": 2})
        )
        await loading.wait()
        delay_over.set()                         # the pause's delay runs out now
        await settle()
        loaded.set()

        assert (await second)["success"] is True
        await settle()
        last_start = max(i for i, c in enumerate(rig.mpv.sent) if c[0] == "play_index")
        assert ("stop",) not in rig.mpv.sent[last_start:]
        assert details(rig)["track_id"] == "s3"
        assert phase(rig) == "playing"


class TestTransport:

    async def _play(self, rig, start_index=0):
        await rig.select()
        await play(rig, start_index=start_index)
        await rig.tick()

    async def test_pause(self, rig):
        """Breaks: the pause button (UI, rotary, IR) leaves the music running,
        or the player drops to READY instead of showing the paused track."""
        await self._play(rig)

        result = await rig.command("pause")

        assert result["success"] is True
        assert rig.mpv.paused is True
        assert phase(rig) == "paused"
        assert "resume" in rig.state()["controls"]

    async def test_resume(self, rig):
        """Breaks: play after pause stays silent, or the button stays on play."""
        await self._play(rig)
        await rig.command("pause")

        result = await rig.command("resume")

        assert result["success"] is True
        assert rig.mpv.paused is False
        assert phase(rig) == "playing"

    async def test_seek(self, rig):
        """The progress bar's drag, in ms on the wire, seconds to mpv. Breaks:
        a seek lands 1000× off, or the bar snaps back to where it was."""
        await self._play(rig)

        result = await rig.command("seek", {"position_ms": 42000})

        assert result["success"] is True
        assert ("seek", 42) in rig.mpv.sent
        assert anchor_ms(rig) == 42_000

    async def test_next(self, rig):
        """Breaks: the next button leaves the track playing, or the player
        shows the next track over mpv still playing the current one."""
        await self._play(rig)

        result = await rig.command("next")

        assert result["success"] is True
        assert ("play_index", 1) in rig.mpv.sent[-2:]
        assert details(rig)["queue_index"] == 1
        assert details(rig)["track_id"] == "s2"

    async def test_next_at_end_is_noop(self, rig):
        """Breaks: next on the last track errors in the UI, or restarts it."""
        await self._play(rig, start_index=2)
        mark = len(rig.mpv.sent)

        result = await rig.command("next")

        assert result["success"] is True
        assert sent_since(rig, mark) == []
        assert details(rig)["track_id"] == "s3"
        # The last track offers no next: the button is disabled, not a no-op.
        assert "next" not in rig.state()["controls"]

    async def test_prev_restarts_current_when_past_threshold(self, rig):
        """Past 3 s, prev restarts the track (Spotify feel). Breaks: prev
        mid-track jumps back an entire track."""
        await self._play(rig, start_index=1)
        rig.mpv.playhead(5)
        mark = len(rig.mpv.sent)

        result = await rig.command("prev")

        assert result["success"] is True
        assert ("seek", 0) in sent_since(rig, mark)
        assert not [c for c in sent_since(rig, mark) if c[0] == "play_index"]
        assert details(rig)["track_id"] == "s2"
        assert anchor_ms(rig) == 0

    async def test_prev_steps_back_when_early(self, rig):
        """Within 3 s, prev steps to the previous entry. Breaks: a double press
        of prev never reaches the previous track."""
        await self._play(rig, start_index=1)
        rig.mpv.playhead(1)

        result = await rig.command("prev")

        assert result["success"] is True
        assert ("play_index", 0) in rig.mpv.sent[-2:]
        assert details(rig)["track_id"] == "s1"

    async def test_play_index(self, rig):
        """The queue view's row tap. Breaks: tapping a row plays another."""
        await self._play(rig)

        result = await rig.command("play_index", {"index": 2})

        assert result["success"] is True
        assert ("play_index", 2) in rig.mpv.sent[-2:]
        assert details(rig)["track_id"] == "s3"

    async def test_play_index_out_of_range(self, rig):
        """Breaks: a stale queue view's tap reaches mpv with an index it lacks."""
        await self._play(rig)
        mark = len(rig.mpv.sent)

        result = await rig.command("play_index", {"index": 9})

        assert result["success"] is False
        assert sent_since(rig, mark) == []

    async def test_stop_clears_queue(self, rig):
        """Breaks: Stop leaves the player's queue on screen or mpv playing."""
        await self._play(rig)

        result = await rig.command("stop")

        assert result["success"] is True
        assert ("stop",) in rig.mpv.sent
        assert rig.mpv.current is None
        state = rig.state()
        assert state["session"] is None
        assert state["details"] is None
        assert state["resume"] is None


class TestMetadata:

    async def test_nothing_played_publishes_nothing_to_play(self, rig):
        """An opened library with nothing to resume runs with no session, no
        resume point, no queue and no command to offer. Breaks: a stale track
        (or a transport button) on the shared player and on Milo-Mac."""
        await rig.select()

        state = rig.state()
        assert state["service"] == "running"
        assert state["session"] is None
        assert state["resume"] is None
        assert state["details"] is None
        assert state["controls"] == []

    async def test_now_playing_projection(self, rig):
        """The now-playing record the shared AudioPlayer, Milo-Mac and Milo-iOS
        read. Breaks: wrong art/title, a bar in seconds instead of ms, or a
        queue view that does not match what plays."""
        await rig.select()
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: None):
            await play(rig, start_index=1, shuffle=True)
        await rig.command("seek", {"position_ms": 60_000})

        live, data = session(rig), details(rig)

        assert live["title"] == "Two"
        assert live["artist"] == "DP"
        assert live["album"] == "Disc"
        assert live["artwork"] == "/api/music-library/cover/al1"
        # position/duration in ms (shared wire convention)
        assert anchor_ms(rig) == 60_000
        assert live["duration_ms"] == 200_000
        assert data["queue"] == [TRACKS[1], TRACKS[0], TRACKS[2]]
        assert data["queue_index"] == 0
        assert data["shuffle"] is True
        # `repeat` was removed (dead scaffolding) — it must not reappear.
        assert "repeat" not in data
        assert data["track_id"] == "s2"

    def test_cover_url_falls_back_to_album_id(self, source):
        assert source._cover_url({"albumId": "ab9"}) == "/api/music-library/cover/ab9"
        assert source._cover_url({}) is None


class TestMpvMovesTheQueue:
    """What mpv announces — start-file, end-file by entry id — is what moves
    the queue. Nothing polls it."""

    async def test_gapless_auto_advance(self, rig):
        """mpv steps to the next entry by itself. Breaks: the player keeps the
        finished track's title over the next one, for the rest of the queue."""
        await rig.select()
        await play(rig)
        await rig.tick()
        plays = len([c for c in rig.mpv.sent if c[0] == "play_index"])

        await rig.mpv.ends("eof")
        await settle()

        assert details(rig)["track_id"] == "s2"
        assert details(rig)["queue_index"] == 1
        assert session(rig)["duration_ms"] == 200_000
        assert phase(rig) == "playing"
        assert len([c for c in rig.mpv.sent if c[0] == "play_index"]) == plays

    async def test_queue_finished_when_the_last_entry_ends(self, rig):
        """Breaks: a played-out album stays on its last track, or ends with
        an error banner; the `eof` end is what the frontend's queue view reads
        to close."""
        await rig.select()
        await play(rig, start_index=2)
        await rig.tick()

        await rig.mpv.ends("eof")
        await settle()

        assert session(rig) is None
        assert session_ends(rig) == ["eof"]
        assert rig.errors() == []

    async def test_a_track_that_cannot_be_read_is_skipped(self, rig):
        """One unreadable file mid-queue: mpv skips it and the queue plays on.
        Breaks: one bad file ends the album, or the player shows the bad
        track's title over the next one."""
        rig.mpv.broken["id=s2&"] = "loading failed"
        await rig.select()
        await play(rig)
        await rig.tick()

        await rig.mpv.ends("eof")
        await settle()

        assert phase(rig) == "playing"
        assert details(rig)["track_id"] == "s3"
        assert rig.errors() == []

    async def test_buffering_until_mpv_opens_the_file(self, rig, monkeypatch):
        """A track that has not started is buffering, not playing. Breaks: the
        progress bar runs ahead of silence while the NAS spins up."""
        monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0)
        rig.mpv.auto_open = False
        await rig.select()
        await play(rig)
        assert phase(rig) == "loading"

        await rig.mpv.opens()
        await settle()

        assert phase(rig) == "playing"


class TestSetShuffle:
    """Live shuffle toggle: reorders only the upcoming tracks (current keeps
    playing), rebuilding mpv's entries after the current one in place."""

    async def test_toggle_on_rebuilds_tail_keeps_head(self, rig):
        """Breaks: toggling shuffle restarts or cuts the track playing, or the
        queue view disagrees with the order mpv will play."""
        await rig.select()
        await play(rig)
        await rig.tick()
        playing = rig.mpv.current
        mark = len(rig.mpv.sent)

        # No-op shuffle so the mechanics show without randomness.
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: None):
            result = await rig.command("set_shuffle", {"shuffle": True})

        assert result["success"] is True
        assert details(rig)["shuffle"] is True
        assert [t["id"] for t in details(rig)["queue"]] == ["s1", "s2", "s3"]
        assert rig.mpv.current is playing
        assert not [c for c in sent_since(rig, mark) if c[0] in ("stop", "play_index")]
        assert [e.url for e in rig.mpv.playlist] == [url("s1"), url("s2"), url("s3")]

    async def test_toggle_off_restores_original_order(self, rig):
        """Breaks: shuffle off keeps the shuffled order, and the next track
        mpv plays is not the one the queue view shows next."""
        await rig.select()
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: seq.reverse()):
            await play(rig, shuffle=True)
        assert [t["id"] for t in details(rig)["queue"]] == ["s1", "s3", "s2"]
        await rig.tick()

        result = await rig.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        assert details(rig)["shuffle"] is False
        assert [t["id"] for t in details(rig)["queue"]] == ["s1", "s2", "s3"]
        assert [e.url for e in rig.mpv.playlist] == [url("s1"), url("s2"), url("s3")]

        await rig.mpv.ends("eof")               # the gapless advance after it
        await settle()
        assert details(rig)["track_id"] == "s2"

    async def test_toggle_off_keeps_a_track_the_queue_lists_twice(self, rig):
        """A repeated track id must survive shuffle OFF, minus the played copies.

        The pristine order was consumed by set membership, so a queue holding the
        same id twice — an album with a reprise, a hand-built playlist — lost
        *both* copies as soon as the first had played: the track silently
        disappeared from the rest of the session.
        """
        reprise = dict(TRACKS[0], title="One (reprise)")
        pristine = [TRACKS[0], TRACKS[1], reprise, TRACKS[2]]
        await rig.select()
        # Shuffled: the first copy of s1 plays, everything else is upcoming.
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: seq.reverse()):
            await play(rig, tracks=pristine, shuffle=True)
        assert [t["title"] for t in details(rig)["queue"]] == ["One", "Three", "One (reprise)", "Two"]
        await rig.tick()

        result = await rig.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        # One copy of s1 played, so exactly one is dropped — the second returns
        # to its pristine place between s2 and s3.
        assert [t["id"] for t in details(rig)["queue"]] == ["s1", "s2", "s1", "s3"]
        assert details(rig)["queue"][2]["title"] == "One (reprise)"
        assert [e.url for e in rig.mpv.playlist] == [
            url("s1"), url("s2"), url("s1"), url("s3"),
        ]

    async def test_noop_when_already_in_target_state(self, rig):
        """Breaks: a repeated toggle rebuilds mpv's playlist for nothing."""
        await rig.select()
        await play(rig)
        mark = len(rig.mpv.sent)

        result = await rig.command("set_shuffle", {"shuffle": False})

        assert result["success"] is True
        assert sent_since(rig, mark) == []
        assert details(rig)["shuffle"] is False

    async def test_requires_active_queue(self, rig):
        """Breaks: a toggle with nothing playing answers success and moves
        nothing, and the UI's shuffle button lies."""
        await rig.select()
        mark = len(rig.mpv.sent)

        result = await rig.command("set_shuffle", {"shuffle": True})

        assert result["success"] is False
        assert sent_since(rig, mark) == []


async def navidrome(rig):
    return await rig.source.get_navidrome_client()


def submissions(client):
    """The calls that actually count a play (``submission=True``)."""
    return [song for song, submission in client.scrobbles if submission]


def now_playings(client):
    return [song for song, submission in client.scrobbles if not submission]


async def pass_starts(rig):
    """The first tick of a pass, where its listening is measured from."""
    await rig.tick()


async def listen(rig, seconds):
    """`seconds` of sound: one tick a second, the playhead moving with it."""
    for _ in range(seconds):
        rig.mpv.playhead((rig.mpv.position or 0) + 1)
        await rig.tick()


class TestScrobble:
    """The listening history. Navidrome counts a play ONLY on
    ``scrobble(submission=true)`` — OpenSubsonic forbids it from counting a
    `stream` fetch — so this is what fills play_date/play_count and therefore
    what makes the library's ``type=recent`` and ``type=frequent`` lists
    non-empty. Every failure here is that history silently staying empty, or a
    play counted twice.
    """

    @staticmethod
    def _track(song_id, duration):
        return {"id": song_id, "title": song_id, "artist": "DP",
                "album": "Disc", "duration": duration}

    async def _play(self, rig, tracks, start_index=0):
        lengths(rig, tracks)
        await rig.select()
        await play(rig, tracks=tracks, start_index=start_index)
        await pass_starts(rig)
        return await navidrome(rig)

    async def test_the_play_counts_only_once_past_half_the_track(self, rig):
        """Half the track is the Last.fm threshold Navidrome applies. Submitting
        early inflates the history; submitting on every later tick multiplies the
        play count of whatever the user left running."""
        client = await self._play(rig, [self._track("s1", 200)])

        await listen(rig, 99)
        assert submissions(client) == []

        await listen(rig, 1)
        assert submissions(client) == ["s1"]

        # Still one after the rest of the track: the flag is not re-armed.
        await listen(rig, 100)
        assert submissions(client) == ["s1"]

    async def test_a_long_track_counts_at_four_minutes_not_at_half(self, rig):
        """The threshold is the *earlier* of the two, which is the only thing
        that makes a 40-minute live set ever count."""
        client = await self._play(rig, [self._track("s1", 2400)])

        await listen(rig, 239)
        assert submissions(client) == []

        await listen(rig, 1)
        assert submissions(client) == ["s1"]

    async def test_a_track_under_thirty_seconds_never_counts(self, rig):
        """Interludes and jingles are excluded by the same rule — an album full
        of them would otherwise dominate ``type=frequent``."""
        client = await self._play(rig, [self._track("skit", 20)])

        await listen(rig, 20)

        assert submissions(client) == []

    async def test_seeking_forward_is_not_listening(self, rig):
        """The threshold is measured in seconds actually played. Read off the
        playhead instead, a single drag to the end of the track would count a
        play of a track nobody heard."""
        client = await self._play(rig, [self._track("s1", 60)])
        await listen(rig, 5)

        await rig.command("seek", {"position_ms": 55_000})
        assert anchor_ms(rig) == 55_000           # the playhead did jump
        await listen(rig, 1)

        assert submissions(client) == []         # the listening did not

        await listen(rig, 24)
        assert submissions(client) == ["s1"]

    async def test_the_same_track_twice_in_a_queue_counts_twice(self, rig):
        """The flag belongs to a pass, not to a song id: an album with a reprise
        or a hand-built playlist can hold the same id twice, and the second
        listen is a second play."""
        track = self._track("s1", 60)
        client = await self._play(rig, [track, dict(track)])

        await listen(rig, 35)
        assert submissions(client) == ["s1"]

        await rig.mpv.ends("eof")                # gapless: the second entry starts
        await settle()
        await pass_starts(rig)
        await listen(rig, 35)

        assert submissions(client) == ["s1", "s1"]

    async def test_a_track_switch_re_arms_the_threshold(self, rig):
        """`next` mid-track must not carry the accumulated seconds over, nor
        leave the flag set so the new track never counts."""
        client = await self._play(rig, [self._track("s1", 200), self._track("s2", 200)])
        await listen(rig, 99)

        await rig.command("next")
        await pass_starts(rig)
        await listen(rig, 1)

        # 99 + 1 would have crossed the threshold had the counter carried over.
        assert submissions(client) == []

        await listen(rig, 99)
        assert submissions(client) == ["s2"]

    async def test_replaying_a_track_after_a_stop_counts_again(self, rig):
        """Breaks: the second listen of a track after an explicit Stop is lost
        from the play count."""
        client = await self._play(rig, [self._track("s1", 60)])
        await listen(rig, 30)
        assert submissions(client) == ["s1"]

        await rig.command("stop")
        await play(rig, tracks=[self._track("s1", 60)])
        await pass_starts(rig)
        await listen(rig, 30)

        assert submissions(client) == ["s1", "s1"]

    async def test_the_start_of_a_track_is_announced_without_counting_it(self, rig):
        """`submission=false` is what shows the track as now playing in
        Navidrome; it must never be the call that counts the play — and it is
        sent once per start, not per event mpv sends about it."""
        client = await self._play(rig, [self._track("s1", 200), self._track("s2", 200)])
        assert now_playings(client) == ["s1"]

        await rig.command("next")

        assert now_playings(client) == ["s1", "s2"]
        assert submissions(client) == []

    async def test_a_stalled_stream_is_not_listening(self, rig):
        """A frozen playhead with mpv still reporting `pause` False is what a
        dead share or a cache-starved stream looks like. Crediting the ticks
        would count a play of silence."""
        client = await self._play(rig, [self._track("s1", 60)])
        rig.mpv.playhead(3.0)

        await rig.tick(200)

        assert submissions(client) == []

    async def test_restarting_the_track_with_prev_is_a_second_play(self, rig):
        """Prev past the restart threshold replays the track in place instead of
        stepping back. Left out, the whole replay counts for nothing — and a
        Prev pressed mid-track would carry its listened seconds into the new
        pass."""
        client = await self._play(rig, [self._track("s1", 60)])
        await listen(rig, 30)
        assert submissions(client) == ["s1"]

        await rig.command("prev")
        await pass_starts(rig)
        await listen(rig, 30)

        assert submissions(client) == ["s1", "s1"]
        assert now_playings(client) == ["s1", "s1"]

    async def test_a_refusing_navidrome_never_reaches_the_music(self, rig):
        """Listening history is bookkeeping: a sidecar that is down, slow or
        refusing costs a log line, not a gap in playback."""
        client = await self._play(rig, [self._track("s1", 60)])
        client.scrobble = AsyncMock(side_effect=RuntimeError("navidrome down"))

        await listen(rig, 40)

        assert phase(rig) == "playing"
        assert details(rig)["track_id"] == "s1"
        assert rig.errors() == []


class TestMergedAlbumCache:
    """The whole-catalog walk behind the alphabetical grid. It is cached for a
    TTL, so what lands in the cache has to be the catalog — a walk cut short by a
    failed page would otherwise BE the catalog until it expires, and the albums
    past the break read as deleted."""

    @staticmethod
    def _client(pages):
        """A Navidrome client answering the paged walk with ``pages`` in order."""
        return Mock(get_album_list=AsyncMock(side_effect=list(pages)))

    @pytest.mark.asyncio
    async def test_a_failed_page_is_served_but_never_cached(self, source):
        source.get_navidrome_client = AsyncMock(
            return_value=self._client([[{"id": "a1"}, {"id": "a2"}], None])
        )

        with patch("backend.sources.music_library.source._ALBUM_PAGE", 2):
            albums = await source.get_merged_albums([2])

        # This call still answers with what it got — a partial grid beats none.
        assert [a["id"] for a in albums] == ["a1", "a2"]
        assert source._album_cache == {}

    @pytest.mark.asyncio
    async def test_a_walk_that_reached_the_end_is_cached(self, source):
        client = self._client([[{"id": "a1"}, {"id": "a2"}], [{"id": "a3"}]])
        source.get_navidrome_client = AsyncMock(return_value=client)

        with patch("backend.sources.music_library.source._ALBUM_PAGE", 2):
            albums = await source.get_merged_albums([2])
            again = await source.get_merged_albums([2])

        assert [a["id"] for a in albums] == ["a1", "a2", "a3"]
        assert again == albums
        # The short second page ended the walk; the second call asked nothing.
        assert client.get_album_list.await_count == 2


class TestResume:
    """Resume-on-return: a queue left by a source switch or the idle timeout
    comes back on the next activation, paused at its track and second; an
    explicit Stop, a queue played out and a storage that left forget it."""

    async def test_a_return_reopens_the_queue_paused_at_its_second(self, rig):
        """Breaks: coming back to the library after a detour to the radio loses
        the album, or starts it playing in a room nobody asked, or from 0:00.
        The second rides on the load itself (E57: a seek before the file is
        open is refused)."""
        await rig.select()
        await play(rig, start_index=1)
        rig.mpv.playhead(60)
        await rig.tick()
        await rig.leave()
        mark = len(rig.mpv.sent)

        await rig.select()

        assert phase(rig) == "paused"
        assert details(rig)["track_id"] == "s2"
        assert details(rig)["queue_index"] == 1
        assert anchor_ms(rig) == 60_000
        sent = sent_since(rig, mark)
        assert ("set_property", "pause", True) in sent
        assert [c[3] for c in sent if c[0] == "loadfile"] == [None, 60, None]
        assert sent[-1] == ("play_index", 1)
        assert not [c for c in sent if c[0] == "seek"]

    async def test_a_return_keeps_the_shuffled_order(self, rig):
        """A shuffled queue comes back in the order it was playing, flagged
        shuffled. Breaks: the return plays a different upcoming order than the
        queue view showed, or the shuffle button reads off."""
        await rig.select()
        with patch("backend.sources.music_library.source.random.shuffle", lambda seq: seq.reverse()):
            await play(rig, shuffle=True)
        await rig.command("next")
        await rig.tick()
        await rig.leave()

        await rig.select()

        data = details(rig)
        assert [t["id"] for t in data["queue"]] == ["s1", "s3", "s2"]
        assert data["queue_index"] == 1
        assert data["track_id"] == "s3"
        assert data["shuffle"] is True
        assert [e.url for e in rig.mpv.playlist] == [url("s1"), url("s3"), url("s2")]

    async def test_the_idle_timeout_publishes_what_a_play_press_would_reopen(self, idle_rig):
        """No session, and the saved queue on the wire as the resume point and
        the queue it would reopen, with the commands that reopen it. Breaks: a
        consumer outside this checkout (Milo-Mac, Milo-iOS) cannot tell a paused
        detour from a library that never played, and the frontend has to keep
        its own sticky copy."""
        await idle_rig.select()
        await play(idle_rig, start_index=2)
        idle_rig.mpv.playhead(30)
        await idle_rig.tick()

        await idle_rig.command("pause")          # the idle timeout ends the session

        state = idle_rig.state()
        assert state["session"] is None
        assert session_ends(idle_rig) == ["idle_timeout"]
        assert state["resume"]["title"] == "Three"
        assert state["resume"]["position_ms"] == 30_000
        assert state["details"]["track_id"] == "s3"
        assert state["details"]["queue_index"] == 2
        assert "resume" in state["controls"]

    async def test_idle_timeout_then_source_switch_keeps_the_queue(self, idle_rig):
        """The documented resume case, end to end: pause long enough for the idle
        timeout, then switch to another source — coming back must still reopen
        the queue where it was. Breaks: the switch, finding no session, erases
        the one the timeout saved."""
        await idle_rig.select()
        await play(idle_rig, start_index=2)
        idle_rig.mpv.playhead(30)
        await idle_rig.tick()
        await idle_rig.command("pause")
        await idle_rig.leave()
        mark = len(idle_rig.mpv.sent)

        await idle_rig.select()

        reopened = loads_since(idle_rig, mark)
        assert [c[3] for c in reopened] == [None, None, 30]
        assert details(idle_rig)["track_id"] == "s3"
        # Reopened paused, the short pause timeout ends it again at once: the
        # second it offers is still the one it was left at.
        assert idle_rig.state()["resume"]["position_ms"] == 30_000

    async def test_an_explicit_stop_leaves_nothing_to_resume(self, rig):
        """Stopping on purpose is not a detour. Breaks: the payload cannot tell
        the two apart, and the next open reopens a queue the user stopped."""
        await rig.select()
        await play(rig, start_index=2)
        await rig.tick()

        await rig.command("stop")
        assert rig.state()["resume"] is None
        assert details(rig) is None

        await rig.leave()
        mark = len(rig.mpv.sent)
        await rig.select()

        assert loads_since(rig, mark) == []
        assert session(rig) is None
        assert details(rig) is None

    async def test_a_played_out_queue_leaves_nothing_to_resume(self, rig):
        """Breaks: reopening the library puts the last track of an album that
        finished long ago back on the player."""
        await rig.select()
        await play(rig, start_index=2)
        await rig.tick()
        await rig.mpv.ends("eof")
        await settle()

        await rig.leave()
        mark = len(rig.mpv.sent)
        await rig.select()

        assert loads_since(rig, mark) == []
        assert session(rig) is None
        assert details(rig) is None

    async def test_a_new_context_retires_the_saved_queue(self, idle_rig):
        """A fresh play supersedes the saved queue before it loads. Breaks: a
        new play mpv refuses leaves the screen offering the queue the user
        just replaced."""
        await idle_rig.select()
        await play(idle_rig, start_index=2)
        await idle_rig.tick()
        await idle_rig.command("pause")          # idle timeout: the resume view
        assert details(idle_rig)["track_id"] == "s3"
        idle_rig.mpv.accept = False

        result = await play(idle_rig, tracks=[TRACKS[0]])

        assert result["success"] is False
        assert session(idle_rig) is None
        assert details(idle_rig) is None or details(idle_rig)["track_id"] != "s3"

    async def test_a_resumed_queue_still_knows_which_key_it_came_from(self, rig):
        """Capture → restore → the storage-gone end must still fire. Breaks: a
        queue reopened after a detour is attributed to no storage space, and
        pulling its key leaves mpv skipping silently through unreachable
        tracks instead of ending the session."""
        await rig.select()
        await play(rig, library_id=3)
        await rig.tick()
        await rig.leave()
        await rig.select()
        assert session(rig) is not None

        await rig.key_pulled()

        assert session(rig) is None
        assert session_ends(rig)[-1] == "storage_gone"
        assert details(rig) is None

    async def test_a_storage_gone_end_leaves_nothing_to_resume(self, rig):
        """The storage-gone end must not save the queue it just condemned.
        Breaks: replugging the key and reopening the library restores a
        now-playing whose load then fails, titles scrolling over silence."""
        await rig.select()
        await play(rig, library_id=3)
        await rig.tick()
        await rig.key_pulled()
        assert details(rig) is None

        for entry in rig.shares.entries:          # the key comes back
            entry["mounted"] = True
        await rig.shares.on_storages_changed()
        await rig.leave()
        mark = len(rig.mpv.sent)
        await rig.select()

        assert loads_since(rig, mark) == []
        assert details(rig) is None

    async def test_a_stale_resume_point_is_not_reopened_by_itself(self, monkeypatch):
        """Past its TTL the library opens on nothing, not on a paused track.
        Breaks: reopened hours later, a now-playing appears for music the user
        does not remember starting, and the docked player with it."""
        monkeypatch.setattr(
            MusicLibrarySource, "RESUME_POLICY",
            replace(MusicLibrarySource.RESUME_POLICY, ttl_s=WATCHDOG_S),
        )
        rig = LibraryRig(monkeypatch)
        await rig.select()
        await play(rig, start_index=1)
        await rig.tick()
        await rig.leave()
        mark = len(rig.mpv.sent)

        await rig.select()

        assert loads_since(rig, mark) == []
        assert session(rig) is None
        assert details(rig) is None

    async def test_resume_with_nothing_playing_and_nothing_saved_refuses(self, rig):
        """With no queue and no saved one there is no end state that makes
        "Resumed" true, and a client cannot detect a success that did nothing.

        The obvious way to reach it is an explicit Stop, which forgets the
        saved queue on purpose, followed by the rotary — playback_dispatch
        sends `resume` and knows no other name.
        """
        await rig.select()
        await play(rig)
        await rig.command("stop")
        mark = len(rig.mpv.sent)

        result = await rig.command("resume")

        assert result["success"] is False
        assert sent_since(rig, mark) == []


class TestRescanOnOpen:
    """Opening the library asks Navidrome to re-index.

    It is the only moment Milō can infer that freshness matters: music copied
    straight onto a NAS raises no event this appliance can observe — inotify
    crosses neither a network mount nor a mount itself — so without this the
    catalog moves only on the 6-hourly scheduled pass, which is deliberately
    slow so a sleeping NAS is not woken 24 times a day.
    """

    async def test_opening_the_library_requests_a_rescan(self, rig):
        """Breaks: music copied onto the NAS stays invisible until the
        scheduled pass."""
        await rig.select()

        assert rig.shares.scan_requests == 1

    async def test_a_wedged_catalog_cannot_delay_the_source(self, rig):
        """The scan is spawned, not awaited. A Navidrome that never answers must
        cost the user nothing — the source is up for playback either way, and the
        request is the layer below's problem (it defers on a busy scanner)."""
        released = asyncio.Event()

        async def never_answers():
            await released.wait()

        rig.shares.request_scan = never_answers

        await rig.select()

        assert rig.state()["service"] == "running"
        assert (await play(rig))["success"] is True
        released.set()
        await settle()

    @pytest.mark.asyncio
    async def test_a_failed_start_asks_for_nothing(self, source):
        """No mpv, no library on screen — nothing to refresh for."""
        source._start_service_and_wait = AsyncMock(return_value=False)
        source._load_auto_stop_config = AsyncMock()
        source.shares.request_scan = AsyncMock()

        assert await source._do_start() is False
        source.shares.request_scan.assert_not_awaited()


class TestMpvRefusesTheTransportCommand:
    """mpv answers False whenever its IPC socket is down, and says so only at
    debug level.

    If these fail, a transport command the daemon never took is answered with
    `success` and the source publishes a change that did not happen: the UI
    draws a play button over a track that is still playing, or moves its
    now-playing to a track mpv never switched to.
    """

    async def _playing(self, rig, paused=False):
        """A queue playing track 2 (paused through the pause command when
        asked), then an mpv that refuses every command."""
        await rig.select()
        await play(rig, start_index=1)
        await rig.tick()
        if paused:
            await rig.command("pause")
        rig.mpv.accept = False
        return len(rig.recorder.envelopes)

    async def test_pause_refused_keeps_the_track_playing(self, rig):
        published = await self._playing(rig)

        result = await rig.command("pause")

        assert result["success"] is False
        assert phase(rig) == "playing"
        assert len(rig.recorder.envelopes) == published

    async def test_resume_refused_keeps_the_track_paused(self, rig):
        published = await self._playing(rig, paused=True)

        result = await rig.command("resume")

        assert result["success"] is False
        assert phase(rig) == "paused"
        assert len(rig.recorder.envelopes) == published

    async def test_seek_refused_keeps_the_position(self, rig):
        published = await self._playing(rig)
        before = anchor_ms(rig)

        result = await rig.command("seek", {"position_ms": 42000})

        assert result["success"] is False
        assert anchor_ms(rig) == before
        assert len(rig.recorder.envelopes) == published

    async def test_track_switch_refused_keeps_the_queue_index(self, rig):
        """next/play_index/prev-to-previous all land in the same switch."""
        published = await self._playing(rig)

        result = await rig.command("next")

        assert result["success"] is False
        assert details(rig)["queue_index"] == 1
        assert phase(rig) == "playing"
        assert len(rig.recorder.envelopes) == published

    async def test_prev_restart_refused_keeps_the_playhead(self, rig):
        """Past the threshold, prev restarts the current track in place."""
        await rig.select()
        await play(rig, start_index=1)
        rig.mpv.playhead(5)
        await rig.command("seek", {"position_ms": 5000})
        rig.mpv.accept = False
        published = len(rig.recorder.envelopes)

        result = await rig.command("prev")

        assert result["success"] is False
        assert anchor_ms(rig) == 5000
        assert details(rig)["queue_index"] == 1
        assert len(rig.recorder.envelopes) == published


# === A reorder mpv refuses partway ===

async def test_a_reorder_refused_halfway_still_ends_the_queue(rig):
    """mpv refusing one removal of a reorder left the session listing entries
    mpv no longer held: the queue never ended, ACTIVE over an idle mpv."""
    await rig.select()
    await play(rig)
    await rig.tick()
    real = rig.mpv.remove_entry
    calls = []

    async def refuse_second(index):
        calls.append(index)
        if len(calls) == 2:
            return False                     # mpv's reply lost (timeout)
        return await real(index)

    rig.mpv.remove_entry = refuse_second
    result = await rig.command("set_shuffle", {"shuffle": True})
    assert result["success"] is False

    await rig.mpv.ends("eof")               # s1 ends, mpv moves on
    await settle()
    await rig.mpv.ends("eof")               # the last entry mpv still holds
    await settle()

    assert rig.mpv.current is None           # mpv is idle: nothing plays
    assert session(rig) is None


async def test_a_reorder_whose_append_is_refused_keeps_the_now_playing_true(rig):
    """mpv refusing one append of a reorder: the session must not name a track
    other than the one mpv plays next (the now-playing on screen and the lock
    screen)."""
    await rig.select()
    await play(rig)
    await rig.tick()
    real = rig.mpv.loadfile
    calls = []

    async def refuse_second(stream, **kw):
        calls.append(stream)
        if len(calls) == 2:
            return None
        return await real(stream, **kw)

    rig.mpv.loadfile = refuse_second
    result = await rig.command("set_shuffle", {"shuffle": True})
    assert result["success"] is False

    await rig.mpv.ends("eof")               # s1 ends, mpv starts what it holds next
    await settle()

    playing_url = rig.mpv.current.url
    assert f"id={details(rig)['track_id']}&" in playing_url
