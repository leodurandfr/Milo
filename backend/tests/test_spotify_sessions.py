"""Spotify sessions, driven through the go-librespot the unit was measured to
run (tests/spotify_world.py; docs: source architecture, phase 3b).

What is asserted is what the wire says, and what Milō asked of the daemon,
after the phone or the daemon did something. Each gap test was seen red on
the code before phase 3b for the reason its docstring gives.
"""
import json

import pytest
import yaml

from backend.tests.golden.harness import EPOCH, settle

from backend.core import audio_source
from backend.core.models.ws_events import SourceErrorReason
from backend.sources.spotify import routes
from backend.tests.spotify_world import (
    ACCOUNT, DEVICE_ID, LE_CHEMIN, PARAPLUIE, TROIS_NEUF_TROIS, SpotifyWorld, cover_named_for, gid_of, track,
)

DELAY = 120   # make_settings' audio.auto_stop_delay
GUEST = "31guestaccountxxxxxxxxxxxxx"
# An album cover as Spotify serves it (640 px), and the 64 px one of a row.
COVER = "https://i.scdn.co/image/ab67616d0000b2739ebaaeba7d0b85a5761ea449"
ROW_COVER = "https://i.scdn.co/image/ab67616d000048519ebaaeba7d0b85a5761ea449"


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


# === A content command needs the source started ===

async def test_a_play_while_spotify_is_not_started_is_refused_as_such(monkeypatch, tmp_path):
    """go-librespot runs only while Spotify is the source, and the account is
    cleared at each stop: a play sent then answered "Spotify is not signed in"
    for a signed-in account. It is refused for what it is, and the daemon is
    asked nothing — before the first start and after the source was left."""
    w = SpotifyWorld(monkeypatch, tmp_path)
    try:
        before = await w.command("play_context", {"uri": LE_CHEMIN["uri"]})
        await w.select()
        await w.leave()
        posted = len(w.daemon.posted)
        after = await w.command("play_context", {"uri": LE_CHEMIN["uri"]})
        take_over = await w.command("take_over")

        for result in (before, after, take_over):
            assert result["success"] is False
            assert "not started" in result["error"]
        assert len(w.daemon.posted) == posted
    finally:
        await w.source.shutdown()


# === A session opens at its first track, and follows what the daemon says ===

async def test_a_transfer_shows_nothing_until_the_track_is_known(world):
    """E14: a session opens at its first displayable track. go-librespot says
    `active` and `will_play` before it knows the track; nothing is drawn yet."""
    d = world.daemon
    d.session, d.account, d.track, d.paused, d.buffering = True, "account", None, True, True
    await world._says({"type": "active"}, {"type": "will_play", "uri": PARAPLUIE["uri"]})
    assert not world.active()


async def test_a_transfer_lands_paused_then_plays(world):
    """Measured: a transfer loads the track paused at the phone's position and
    plays 1.6 s later. What the daemon says is what the screen shows."""
    await world.phone_transfers(PARAPLUIE, at_ms=81264)
    assert world.playing()
    assert world.session()["title"] == "Parapluie"
    assert world.position_ms() == 81264


async def test_a_playing_track_moves_on_the_wire_without_a_broadcast(world):
    """The playhead is an anchor every client ages on its own clock: a track
    that simply plays sends nothing (no tick, no drift correction), and still
    reads where it is."""
    await world.phone_transfers(PARAPLUIE, at_ms=81264)
    sent = len(world.recorder.envelopes)
    await world.advance(30)
    assert world.recorder.envelopes[sent:] == []
    assert world.position_ms() == 81264 + 30_000


async def test_a_skip_is_loading_until_the_next_track_plays(world):
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.track, d.buffering = None, True
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})
    assert world.buffering() and not world.playing()
    await world.advance(0.07)
    d.track, d.buffering = dict(TROIS_NEUF_TROIS), False
    await world._says({"type": "metadata"}, {"type": "playing"})
    assert world.playing() and not world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"


async def test_a_skip_names_the_track_it_loads(world):
    """A load that waits on Spotify (1-2 s after a daemon restart, 60 s while
    spclient hung, measured 2026-11-05) showed the hourglass over the track
    that had just been left, its cover and its frozen time. /status names no
    track while one loads; the fork's GET /player/queue names the one loading,
    and that is what the screen shows, from its start."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    await world.advance(30)
    d.track, d.buffering, d.loading = None, True, TROIS_NEUF_TROIS
    d.prev_tracks, d.next_tracks = [_listed(PARAPLUIE)], []
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})

    assert world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"
    assert world.session()["duration_ms"] == TROIS_NEUF_TROIS["duration"]
    assert world.state()["details"]["track_uri"] == TROIS_NEUF_TROIS["uri"]
    assert world.position_ms() == 0

    await world.advance(1.5)
    d.track, d.buffering, d.loading = {**TROIS_NEUF_TROIS, "position": 3}, False, None
    await world._says(d.metadata(), d.playing())
    assert world.playing()
    assert world.session()["title"] == "Trois Neuf Trois"


# "Like This" in the owner's Liked Songs: listed as one uri, played as another.
LIKE_THIS = track("Like This", artists=("Brynn Elise", "Lauren Creviston"), album="Like This")
LIKE_THIS_HERE = {**LIKE_THIS, "uri": "spotify:track:3h2XdwGrmlH3nkODOVm35Z"}


@pytest.mark.parametrize("described_as", ["listed", "alternative"])
async def test_a_relinked_track_is_named_by_the_uri_its_list_holds(world, described_as):
    """A listing's row lights while `details.track_uri` names it, and a track
    Spotify relinks to a market alternative is listed under one uri and plays
    under another: /status names the alternative once the stream has loaded.
    The playing row of Liked Songs went dark at that flip and never lit again
    (measured 2026-11-09). The entry is named by the uri its context lists, the
    one go-librespot keeps on it, during the load and after — on a first load,
    when the entry is still described by the listed uri, and on a later one,
    when it is described by the alternative's from the start."""
    d = world.daemon
    d.next_tracks = [_listed(LIKE_THIS)]
    await world.phone_plays(PARAPLUIE)
    sent = len(world.published())
    d.track, d.buffering, d.listed = None, True, LIKE_THIS["uri"]
    d.loading = LIKE_THIS if described_as == "listed" else LIKE_THIS_HERE
    d.prev_tracks, d.next_tracks = [_listed(PARAPLUIE)], []
    await world._says({"type": "will_play", "uri": LIKE_THIS["uri"]})
    assert world.buffering() and world.session()["title"] == "Like This"

    await world.advance(1.5)
    d.track, d.buffering, d.loading = {**LIKE_THIS_HERE, "position": 3}, False, None
    await world._says(d.metadata(), d.playing())
    assert world.playing()

    states = world.published()[sent:]
    assert states
    for state in states:
        details = state["details"]
        assert details["track_uri"] == LIKE_THIS["uri"]
        assert details["queue"][details["queue_index"]]["uri"] == LIKE_THIS["uri"]
    # The Recent tab plays it back from its list by the listed uri.
    started = (await history(world))[0]
    assert (started["uri"], started["track_uri"]) == (LIKE_THIS["uri"], LIKE_THIS_HERE["uri"])


async def test_a_relinked_track_with_no_play_order_is_named_by_its_own_uri(world):
    """With no play order to read (a go-librespot without GET /player/queue),
    nothing names the uri a relinked entry's list holds: the state names the
    track's own, the one a listing read after its first stream gives the row
    as `uri` (useSpotifyListingPlayback.isCurrent compares both), never a
    uri left from the track before it."""
    d = world.daemon
    d.has_queue_route = False
    await world.phone_plays(PARAPLUIE)
    d.track, d.buffering, d.listed, d.loading = None, True, LIKE_THIS["uri"], LIKE_THIS
    await world._says({"type": "will_play", "uri": LIKE_THIS["uri"]})
    await world.advance(1.5)
    d.track, d.buffering, d.loading = {**LIKE_THIS_HERE, "position": 3}, False, None
    await world._says(d.metadata(), d.playing())

    assert world.playing() and world.session()["title"] == "Like This"
    details = world.state()["details"]
    assert details["track_uri"] == LIKE_THIS_HERE["uri"]
    assert details["queue"] == [] and details["queue_index"] is None


@pytest.mark.parametrize("unnamed", ["not cached", "no queue route", "queue read fails"])
async def test_a_load_nothing_names_keeps_the_track_on_screen(world, unnamed):
    """When the loading track cannot be named (its metadata not cached, a stock
    go-librespot with no GET /player/queue, a read that fails), the track on
    screen stands as it was: a field cleared while its replacement loads
    deletes the player it draws."""
    d = world.daemon
    d.has_queue_route = unnamed != "no queue route"
    d.loading_known = unnamed != "not cached"
    await world.phone_plays(PARAPLUIE)
    await world.advance(30)
    held = world.position_ms()
    d.queue_answers = unnamed != "queue read fails"
    d.track, d.buffering, d.loading = None, True, TROIS_NEUF_TROIS
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})

    assert world.buffering()
    session = world.session()
    assert session["title"] == "Parapluie"
    assert session["duration_ms"] == PARAPLUIE["duration"]
    assert world.state()["details"]["track_uri"] == PARAPLUIE["uri"]
    assert world.position_ms() == held


async def test_a_loading_track_is_named_once_its_metadata_is_cached(world):
    """The fork raises `queue` when a fetch caches the metadata of an entry it
    lists: that event, alone in its burst, is when to name the track loading."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.track, d.buffering, d.loading, d.loading_known = None, True, TROIS_NEUF_TROIS, False
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})
    assert world.session()["title"] == "Parapluie"

    d.loading_known = True
    await world._says()
    assert world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"


async def test_a_load_taken_over_mid_track_is_anchored_where_it_was_transferred(world):
    """The play order describes the loading track at its load's start, standing
    still (go-librespot #421): for a playback transferred here, the position it
    was transferred at. Anchored at 0 instead, the bar ran from the top of a
    track that lands 81 s in, and jumped there once /status named it."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    await world.advance(30)
    d.track, d.paused, d.buffering = None, True, True
    d.loading = {**TROIS_NEUF_TROIS, "position": 81_264}
    await world._says({"type": "active"}, {"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})

    assert world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"
    assert world.position_ms() == 81_264
    await world.advance(1)
    assert world.position_ms() == 81_264

    d.track, d.buffering, d.loading = {**TROIS_NEUF_TROIS, "position": 81_264}, False, None
    await world._says(d.metadata(), {"type": "paused"})
    assert world.position_ms() == 81_264


async def test_a_skip_after_a_transfer_is_anchored_at_the_top_of_its_track(world):
    """A skip loads its track from the start, and the play order describes it
    there, at 0 — never at the playhead of the track it leaves, which a session
    transferred mid-track holds minutes in."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_transfers(PARAPLUIE, at_ms=81_264)
    await world.advance(30)
    d.track, d.buffering, d.loading = None, True, TROIS_NEUF_TROIS
    d.prev_tracks, d.next_tracks = [_listed(PARAPLUIE)], []
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})

    assert world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"
    assert world.position_ms() == 0


async def test_a_load_of_the_track_on_screen_leaves_its_playhead(world):
    """The play order describes the loading track at its load's start, here
    the top of the track. When the load is the track already on screen (it
    re-buffers), that start is no playhead: the bar stays where the track stood
    rather than snapping back to it."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    await world.advance(30)
    held = world.position_ms()
    d.track, d.buffering, d.loading = None, True, PARAPLUIE
    await world._says({"type": "will_play", "uri": PARAPLUIE["uri"]})

    assert world.buffering()
    assert world.session()["title"] == "Parapluie"
    assert world.position_ms() == held


async def test_a_loading_track_described_without_a_name_is_not_taken(world):
    """An entry the order describes but does not name is no title to draw: the
    track on screen stands rather than a player with an empty title line."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.track, d.buffering = None, True
    d.loading = {**TROIS_NEUF_TROIS, "name": ""}
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})

    assert world.session()["title"] == "Parapluie"
    assert world.state()["details"]["track_uri"] == PARAPLUIE["uri"]


async def test_a_status_with_no_track_that_is_not_loading_names_none(world):
    """Only a load points at a track. A status that names none and is not
    buffering has nothing loading, whatever the order lists as current: the
    track on screen stands."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.track, d.buffering, d.loading = None, False, TROIS_NEUF_TROIS
    await world._says({"type": "paused"})

    assert world.session()["title"] == "Parapluie"
    assert world.state()["details"]["track_uri"] == PARAPLUIE["uri"]


async def test_every_artist_of_the_track_is_published_with_its_uri(world):
    """The player draws each name of the artist line as a link to its own page
    (SpotifySource.vue): a track by two artists must name both, in the line's
    order, each with its own uri — not the first one's."""
    duet = {**track("Breathe", artists=("Télépopmusik", "Angela McCluskey")),
            "artist_uris": ["spotify:artist:tele", "spotify:artist:angela"]}
    await world.phone_plays(duet)
    assert world.session()["artist"] == "Télépopmusik, Angela McCluskey"
    assert world.state()["details"]["artists"] == [
        {"name": "Télépopmusik", "uri": "spotify:artist:tele"},
        {"name": "Angela McCluskey", "uri": "spotify:artist:angela"},
    ]


def _listed(song, known=True, uid=None, provider="context"):
    """An entry of go-librespot's GET /player/queue, its track null until
    cached (an album's tracks carry no uid, measured)."""
    return {"uri": song["uri"], "uid": uid, "provider": provider, "track": song if known else None}


async def test_the_play_order_around_the_track_is_published(world):
    """The phone's mini-bar slides the neighbour titles in under the finger
    (AudioPlayer.vue's carousel reads details.queue / queue_index) and the
    Queue page draws them as rows (SpotifyQueueView.vue): the order is
    go-librespot's window around the track, the one playing in it."""
    d = world.daemon
    d.prev_tracks = [_listed({**LE_CHEMIN, "album_cover_url": COVER})]
    d.next_tracks = [_listed(TROIS_NEUF_TROIS), _listed(track("Pas encore lu"), known=False)]
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    def row(song, title, thumbnail):
        return {"uri": song["uri"], "title": title, "artist": "Kery James",
                "thumbnail": thumbnail, "duration_ms": song["duration"]}

    assert details["queue"] == [
        row(LE_CHEMIN, "Le Chemin", ROW_COVER),
        # Not an album cover (the fixtures'): passed on as it is.
        row(PARAPLUIE, "Parapluie", PARAPLUIE["album_cover_url"]),
        row(TROIS_NEUF_TROIS, "Trois Neuf Trois", TROIS_NEUF_TROIS["album_cover_url"]),
        {"uri": "spotify:track:pasencorelu", "title": None, "artist": None, "thumbnail": None, "duration_ms": None},
    ]
    assert details["queue_index"] == 1


async def test_a_queue_event_alone_names_what_was_not_known(world):
    """go-librespot caches the window's metadata about a second after the
    track loads and says so with `queue` only: that read must reach the
    wire, or the next title stays blank until the track changes."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS, known=False)]
    await world.phone_plays(PARAPLUIE)
    assert world.state()["details"]["queue"][1]["title"] is None
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world._says({"type": "queue"})
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_no_window_is_no_order_to_swipe_through(world):
    """A daemon that lists nothing around the track publishes no queue: a
    queue of the track alone would leave the carousel no neighbour, and the
    swipe nothing to do."""
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    assert details["queue"] == [] and details["queue_index"] is None


async def test_a_daemon_without_the_queue_route_lists_no_order(world):
    """Stock go-librespot 0.10.3 (what dependencies.env pins) has no GET
    /player/queue and answers 404: the bar keeps its single cell rather
    than an order the daemon never gave, and the route is not asked again
    for the rest of the daemon's run (no warning, no retry, per track)."""
    d = world.daemon
    d.has_queue_route = False
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    await world.phone_plays(TROIS_NEUF_TROIS)
    details = world.state()["details"]
    assert details["queue"] == [] and details["queue_index"] is None
    assert world.playing()
    assert d.queue_reads == 1


async def test_the_play_order_is_read_again_when_events_reconnect(world):
    """go-librespot raises `queue` only on change, so one that went by while
    /events was down is never said again: the reconnection reads the order."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]   # moved while nobody listened
    await world.events_blip()
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_a_queue_event_read_with_an_unreadable_status_is_not_lost(world):
    """The `queue` event is said once: when /status fails in its burst, the
    order it announced still reaches the wire with the retry's read."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.status_answers = False
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world._says({"type": "queue"})
    d.status_answers = True
    await world.advance(2.1)                  # the status retry
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_an_unreadable_play_order_keeps_the_last_one_and_is_read_again(world):
    """E10 for the order: a read that fails learns nothing, so the carousel
    keeps the neighbours it had rather than collapsing to one cell — and,
    the `queue` event being said once, the read is tried again on its own."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    d.queue_answers = False
    d.next_tracks = [_listed(LE_CHEMIN)]
    await world._says({"type": "volume", "value": 40, "max": 100})
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"
    assert world.playing()
    d.queue_answers = True
    await world.advance(2.1)                  # the retry
    assert world.state()["details"]["queue"][1]["title"] == "Le Chemin"


async def test_a_play_order_that_keeps_failing_is_retried_once(world):
    """The retry is one read, not a loop: a queue route that keeps failing
    is read again by the daemon's next event, never every two seconds."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.queue_answers = False
    await world._says({"type": "queue"})
    reads = d.queue_reads
    await world.advance(10)
    assert d.queue_reads == reads + 1
    assert world.playing()


async def test_an_order_around_another_track_is_never_shown(world):
    """/status and /player/queue are two reads: a track change whose `queue`
    lands in a later burst must not draw the old neighbours around the new
    track (the new track would slide in as its own next)."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS), _listed(LE_CHEMIN)]
    await world.phone_plays(PARAPLUIE)
    d.queue_event_late = True
    d.prev_tracks, d.next_tracks = [_listed(PARAPLUIE)], [_listed(LE_CHEMIN)]
    await world.phone_plays(TROIS_NEUF_TROIS)
    details = world.state()["details"]
    assert [entry["uri"] for entry in details["queue"]] == [
        PARAPLUIE["uri"], TROIS_NEUF_TROIS["uri"], LE_CHEMIN["uri"],
    ]
    assert details["queue_index"] == 1


async def test_the_order_of_an_ended_session_is_not_the_next_ones(world):
    """A phone that leaves takes its order with it: the next session, even
    on the same track, is drawn with its own order, not the last one's."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    d.next_tracks = []
    d.queue_event_late = True
    await world.phone_leaves()
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    assert details["queue"] == [] and details["queue_index"] is None


async def test_a_play_order_that_is_not_one_changes_nothing(world):
    """A 200 whose body is no order (a proxy, a fork) is a failed read: the
    session it came with is still followed."""
    d = world.daemon
    d.queue_body = ["spotify:track:not-an-order"]
    await world.phone_plays(PARAPLUIE)
    assert world.playing() and world.session()["title"] == "Parapluie"
    assert world.state()["details"]["queue"] == []


async def test_a_seek_moves_the_published_position(world):
    """A seek done on the phone is a discontinuity: it goes out at once, as a
    position alone (nothing else about the session moved)."""
    await world.phone_plays(PARAPLUIE)
    states = len(world.published())
    await world.phone_seeks(151280)
    assert world.position_ms() == 151280
    assert [p["position"]["ms"] for p in world.positions()] == [151280]
    assert len(world.published()) == states


async def test_a_track_that_did_not_announce_its_start_is_not_left_spinning(world):
    """E12: the spinner was an override set by `metadata` that only a later
    `playing`/`paused` cleared, so a track change with no such event behind it
    spun for the rest of the track. The daemon's own status says it plays."""
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.track, d.buffering, d.paused = dict(LE_CHEMIN), False, False
    await world._says({"type": "will_play"}, {"type": "metadata"})
    assert world.playing() and not world.buffering()


# === One writer for the phase: the timer and the screen agree ===

async def test_a_stale_pause_event_arms_no_auto_stop_under_a_playing_track(world):
    """E11: the event armed the auto-stop while /status, read right after,
    published "playing" — two writers. A pause the daemon no longer reports
    must not end a session that plays."""
    await world.phone_plays(PARAPLUIE)
    await world._says({"type": "paused"})     # the phone already resumed
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0
    assert world.playing()


async def test_a_pause_asks_the_daemon_to_end_the_session_once(world):
    """REQUEST_END: after the delay Milō asks go-librespot to drop the session
    (POST /player/stop) and follows the `inactive` that comes back; no banner,
    no restart, and nothing asked twice."""
    await world.phone_plays(PARAPLUIE)
    await world.phone_pauses()
    assert world.active() and not world.playing()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 1
    assert not world.active()
    assert world.errors() == []
    assert world.restarts == []
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 1


async def test_a_phone_that_leaves_leaves_no_auto_stop_behind(world):
    """E69 (measured): `stopped` follows `inactive` and armed the pause timer
    on a source with no session left, which then posted /player/stop on nothing. (A
    next session's first `paused`/`playing` replaced that timer, so it could
    not cut the next session.)"""
    await world.phone_plays(PARAPLUIE)
    await world.phone_leaves()
    assert not world.active()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0


async def test_a_resume_withdraws_the_end_request(world):
    await world.phone_plays(PARAPLUIE)
    await world.phone_pauses()
    await world.advance(DELAY - 1)
    await world.phone_resumes()
    await world.advance(DELAY)
    assert world.stops_sent() == 0
    assert world.playing()


# === The daemon ===

async def test_a_daemon_killed_mid_play_ends_the_session_at_once(world):
    """E17 (Spotify, measured: 6 s of "playing" with no banner): the session
    belongs to go-librespot's process; its death ends it, reported once."""
    await world.phone_plays(PARAPLUIE)
    await world.kill_daemon()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]
    await world.systemd_restarts_it()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_restart_asked_from_outside_ends_the_session_without_a_banner(world):
    """Measured on the unit 2026-11-08: `systemctl restart milo-spotify` under a
    session showed "Spotify stream interrupted" and logged an ERROR, because
    the unit was already `active` again when the exit was heard. A restart
    someone asked for (a shell, an update) is not a death."""
    await world.phone_plays(PARAPLUIE)
    await world.restarted_from_outside()
    assert not world.active()
    assert world.errors() == []


async def test_a_crash_heard_after_restart_brought_the_successor_up_is_a_death(world):
    """The successor of a crash reads `active`/`success` as well; NRestarts is
    what tells it from a requested restart."""
    await world.phone_plays(PARAPLUIE)
    await world.crash_heard_late()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_daemon_that_exits_by_itself_is_a_death(world):
    """Exit 0 reads Result `success` with no restart counted yet; only the
    MainPID of 0 says no successor runs."""
    await world.phone_plays(PARAPLUIE)
    await world.exits_by_itself()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_crash_heard_before_systemd_reaped_it_is_a_death(world):
    """Read before the reap, the unit still names the dead process as its
    MainPID with a count of 0: that is no successor."""
    await world.phone_plays(PARAPLUIE)
    await world.crash_heard_before_systemd()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_an_unreadable_status_on_reconnect_keeps_the_session(world):
    """E10: when /events came back and /status could not be read, the source
    published "no session" over music still playing. A failed read learns nothing: the
    session stays as it was, and the read is tried again."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.status_answers = False
    await world.events_blip()                 # /events reconnects, /status fails
    assert world.playing()
    world.daemon.status_answers = True
    await world.advance(2.1)                  # the retry reads it
    assert world.playing()
    assert world.session()["title"] == "Parapluie"


async def test_a_state_request_with_an_unreadable_status_changes_nothing(world):
    await world.phone_plays(PARAPLUIE)
    world.daemon.status_answers = False
    state = await world.get_state()
    assert state["session"]["phase"] == "playing"
    assert state["session"]["title"] == "Parapluie"


async def test_a_settings_restart_ends_the_session_without_a_banner(world):
    """The crossfade restart is Milō's own: the session it ends is not a death."""
    await world.phone_plays(PARAPLUIE)
    assert await world.source.on_spotify_settings_changed(apply_now=True)
    await world.advance(2.1)
    assert world.restarts
    assert not world.active()
    assert world.errors() == []



def spotify_settings_pushes(world):
    return [e["data"]["config"] for e in world.envelopes("settings", "spotify_settings_changed")]


async def test_a_crossfade_saved_without_apply_now_leaves_the_applied_value_at_the_daemons(world):
    """A crossfade saved without "restart to apply" reaches config.yml and waits
    for the next start: the running daemon still plays the one it read. The
    settings page reads its button off this value — the stored one counted as
    applied hid the button for good the next time the screen opened."""
    world.settings.values["spotify.crossfade_duration"] = 6000
    assert await world.source.on_spotify_settings_changed(apply_now=False)
    await world.idle()

    assert yaml.safe_load(world.config_file.read_text())["crossfade_duration"] == 6000
    assert world.source.crossfade_applied_duration == world.daemon.crossfade
    assert world.source.crossfade_applied_duration != 6000


async def test_a_daemon_start_records_the_crossfade_it_was_given(monkeypatch, tmp_path):
    """Every start of go-librespot reads config.yml again — the settings
    restart, and a profile change, which stops and starts the daemon — so each
    one is what makes a saved crossfade audible, and the settings page is told."""
    world = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await world.select()
    await world.idle()
    try:
        world.settings.values["spotify.crossfade_duration"] = 6000
        assert await world.source.on_spotify_settings_changed(apply_now=True)
        await world.advance(2.1)              # /events reconnects to the new run
        await world.idle()
        assert world.daemon.crossfade == 6000
        assert world.source.crossfade_applied_duration == world.daemon.crossfade
        assert spotify_settings_pushes(world)[-1]["crossfade_applied_duration"] == world.daemon.crossfade

        await world.cast_from(GUEST)
        await world.idle()
        world.settings.values["spotify.crossfade_duration"] = 3000
        assert await world.source.on_spotify_settings_changed(apply_now=False)
        assert (await world.source.switch_profile(ACCOUNT))["success"]
        await world.idle()
        assert world.daemon.crossfade == 3000
        assert world.source.crossfade_applied_duration == world.daemon.crossfade
        assert spotify_settings_pushes(world)[-1]["crossfade_applied_duration"] == world.daemon.crossfade

        await world.leave()
        assert world.source.crossfade_applied_duration is None
        assert spotify_settings_pushes(world)[-1]["crossfade_applied_duration"] is None
    finally:
        await world.source.shutdown()


# === Connections that went dead under the daemon ===
#
# go-librespot's lines as the unit logged them on 2026-11-05, when eth0 was cut
# mid-play and wlan0 kept the internet (0.10.5): the state pushes and the next
# track's metadata went unanswered on the connections held from eth0.

UNANSWERED_ADVANCE = (
    'time="2026-11-05T17:32:42+01:00" level=error msg="failed advancing to next track" '
    'error="failed loading current track (advance to spotify:track:7Bxv0WL7UC6WwQpk9TzdMJ): '
    'failed creating stream for spotify:track:7Bxv0WL7UC6WwQpk9TzdMJ: failed getting track '
    'metadata: spclient request failed: context deadline exceeded"'
)
UNANSWERED_SKIP = (
    'level=warning msg="failed loading current track (skip next)" error="failed creating stream '
    'for spotify:track:7Bxv0WL7UC6WwQpk9TzdMJ: failed getting track metadata: spclient request '
    'failed: context deadline exceeded"'
)
UNANSWERED_PUT = (
    'time="2026-11-05T17:32:02+01:00" level=error msg="failed put state because '
    'PLAYER_STATE_CHANGED" error="context deadline exceeded"'
)
# The same cut on 2026-11-07, the accesspoint's turn: the first of three loads
# in a row, logged by the loader, then by its caller.
KEY_UNANSWERED = (
    'time="2026-11-07T12:17:52+01:00" level=warning msg="failed load '
    'spotify:track:77lPC0YqxUODd674qz9LmV" error="failed creating stream for '
    'spotify:track:77lPC0YqxUODd674qz9LmV: failed retrieving audio key: context deadline exceeded"',
    'time="2026-11-07T12:17:52+01:00" level=warning msg="failed skipping to next track" '
    'error="failed loading current track (advance to spotify:track:77lPC0YqxUODd674qz9LmV): '
    'failed creating stream for spotify:track:77lPC0YqxUODd674qz9LmV: failed retrieving audio '
    'key: context deadline exceeded"',
)
# Not measured: the same load on a build that retries the key on a new
# connection before giving up wraps the deadline once more.
KEY_UNANSWERED_RETRIED = tuple(
    line.replace("audio key: context", "audio key: aes key request timed out: context")
    for line in KEY_UNANSWERED
)
AP_SIGNED_IN = 'level=info msg="authenticated AP" username="p6*********************xy"'
SIGNED_IN = 'level=info msg="authenticated Login5" username="p6*********************xy"'


async def the_next_load_hangs(world, song=TROIS_NEUF_TROIS):
    """The track ends and the next one's load waits on spclient: `will_play`,
    then /status reports buffering with no track until the load gives up."""
    d = world.daemon
    d.track, d.buffering = None, True
    d.says({"type": "will_play", "uri": song["uri"]})
    await world.idle()
    assert world.buffering()


async def test_a_track_load_nobody_answered_restarts_the_daemon(world):
    """Measured: the session sat LOADING on the next track for over three
    minutes, every spclient request timing out on connections bound to an
    address eth0 no longer had, until the source was left by hand. A new
    process drops them; the session it cannot carry ends as a lost stream."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)

    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()

    assert len(world.restarts) == 1
    assert not world.active()
    assert world.session_ends()[-1] == "stream_lost"
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


@pytest.mark.parametrize("line", [
    # Milō's own play_context (POST /player/play): the loader's line alone
    'level=warning msg="failed resolve spotify:playlist:37i9dQZF1DX4sWSpwq3LiO" '
    'error="failed resolving context: spclient request failed: context deadline exceeded"',
    # A phone's play command: its callback's line
    'level=warning msg="failed loading context for play command" error="failed creating track '
    'list: failed initializing context resolver: spclient request failed: context deadline exceeded"',
    # A phone's transfer
    'level=warning msg="failed transfer spotify:playlist:37i9dQZF1DX4sWSpwq3LiO" error="failed '
    'creating track list: spclient request failed: context deadline exceeded"',
], ids=["play_context", "play_command", "transfer"])
async def test_a_context_nobody_answered_restarts_the_daemon(world, line):
    """A cut while nothing plays meets the dead connections at the next play,
    cast or transfer, whose context is resolved before any track: no "failed
    loading current track" line follows, and every new play failed the same
    way, in silence, until the source was left by hand."""
    await world.journal_says(line)
    await world.idle()

    assert len(world.restarts) == 1
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


@pytest.mark.parametrize("lines", [KEY_UNANSWERED, KEY_UNANSWERED_RETRIED], ids=["0.10.5", "retried"])
async def test_an_audio_key_nobody_answered_restarts_the_daemon_once(world, lines):
    """Measured 2026-11-07: the accesspoint stayed bound to eth0's vanished
    address for minutes, three loads in a row failed on their audio key and
    the session sat LOADING 130 s, a banner per load; a new accesspoint
    authenticated over wlan0 70 ms after the old one closed. Each load is
    logged twice, by the loader and by its caller: read twice, the second
    line would restart the run that replaced the dead one."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)

    for line in lines:
        await world.journal_says(line)
    await world.idle()

    assert len(world.restarts) == 1
    assert not world.active()
    assert world.session_ends()[-1] == "stream_lost"
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


@pytest.mark.parametrize("line", [
    # A load a newer press replaced: its key wait ends on the caller's context
    'level=warning msg="failed skipping to next track" error="failed loading current track '
    '(skip next): failed creating stream for spotify:track:x: failed retrieving audio key: '
    'context canceled"',
    # A CDN chunk: go-librespot retries its download, and it was never measured dead
    'level=error msg="failed advancing to next track" error="failed loading current track '
    '(advance to spotify:track:x): failed creating stream for spotify:track:x: failed creating '
    'chunked reader: failed creating chunked reader for any cdn url: failed downloading chunk 0: '
    'Get \\"https://audio-ak.spotifycdn.com/audio/x\\": context deadline exceeded '
    '(Client.Timeout exceeded while awaiting headers)"',
], ids=["key_canceled", "cdn_chunk"])
async def test_a_load_that_failed_otherwise_restarts_nothing(world, line):
    """Only a connection the daemon held and nobody answers is worth a new
    process: a restart ends the Connect session, and for these the next
    press plays."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)

    await world.journal_says(line)
    await world.idle()

    assert world.restarts == []
    assert world.active()
    assert SourceErrorReason.STREAM_DISCONNECTED not in world.errors()


async def test_state_pushes_nobody_answered_restart_nothing(world):
    """A state push that times out stops no music: what is loaded keeps
    playing, and a restart would cut it. Only the load that stops it does."""
    await world.phone_plays(PARAPLUIE)

    await world.journal_says(UNANSWERED_PUT)
    await world.journal_says(UNANSWERED_PUT)
    await world.idle()

    assert world.restarts == []
    assert world.playing()


async def test_a_late_line_over_music_playing_again_restarts_nothing(world):
    """go-librespot drops the stream before the load that fails, so a load
    that went unanswered never leaves music playing: a PLAYING session is a
    later load that went through, its line read late (journald, 14 s
    measured). A restart would cut that music. The run stays restartable: a
    load that stalls afterwards is still the dead pool's."""
    await world.phone_plays(PARAPLUIE)

    await world.journal_says(UNANSWERED_SKIP)
    await world.idle()

    assert world.restarts == []
    assert world.playing()
    assert world.errors() == []

    await the_next_load_hangs(world)
    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()

    assert len(world.restarts) == 1


async def test_the_banner_stands_until_music_plays_again(world):
    """The new run signs in within a second with the stored account, the
    accesspoint first: clearing the banner on either line would take the only
    word of why the music stopped off the screen before anyone read it."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)
    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()

    await world.journal_says(AP_SIGNED_IN)
    await world.journal_says(SIGNED_IN)
    assert not world.envelopes("source", "error_cleared")

    await world.journal_says('level=info msg="loaded track \\"Parapluie\\""')
    assert world.envelopes("source", "error_cleared")


async def test_a_stream_disconnected_banner_leaves_with_the_source(world):
    """The restart's banner belongs to Spotify: left standing after a switch,
    it would sit over the next source, with nothing on Spotify's side left to
    clear it."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)
    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]

    await world.leave()

    assert world.envelopes("source", "error_cleared")


async def test_a_late_line_from_the_restarted_run_restarts_nothing(world):
    """journald hands a line on seconds late (14 s measured): the dead run's
    second unanswered load, read after its restart, must not restart the run
    that replaced it."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)
    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()
    dead_run = world.runs[0]

    await world.journal_says(UNANSWERED_ADVANCE, invocation=dead_run)
    await world.idle()

    assert len(world.restarts) == 1
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_late_line_restarts_nothing_when_systemd_cannot_name_the_run(world):
    """With the run in place unreadable (InvocationID None: a D-Bus read that
    failed), the runs already restarted are all that tells the dead run's
    late line from one of the run that replaced it: without them, a second
    restart cuts the new run."""
    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)
    await world.journal_says(UNANSWERED_ADVANCE)
    await world.idle()
    dead_run = world.runs[0]
    world.systemd.invocation_id.side_effect = lambda *_: None

    await world.journal_says(UNANSWERED_ADVANCE, invocation=dead_run)
    await world.idle()

    assert len(world.restarts) == 1


@pytest.mark.parametrize("lines", [(UNANSWERED_ADVANCE,), KEY_UNANSWERED], ids=["spclient", "audio_key"])
async def test_a_load_unanswered_on_a_link_down_ends_the_session_and_keeps_the_daemon(world, lines):
    """With no internet a new process would time out the same way, and its
    sign-in would fail at every Restart=. But go-librespot does not try the
    load again, so the session sat LOADING with nothing to bring it back:
    the daemon is asked to end it (locally, link or not) and stays up."""
    from types import SimpleNamespace
    from backend.core.models.audio_state import ConnectivityLevel

    await world.phone_plays(PARAPLUIE)
    await the_next_load_hangs(world)
    world.machine.connectivity_service = SimpleNamespace(level=ConnectivityLevel.LIMITED)

    for line in lines:
        await world.journal_says(line)
    await world.idle()

    assert world.restarts == []
    assert world.daemon.up
    assert world.stops_sent() == 1
    assert not world.active()
    assert world.session_ends()[-1] == "stream_lost"
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_connection_that_could_not_open_restarts_nothing(world):
    """`dial tcp …: i/o timeout` is a fresh connection failing: the network
    itself, which a new process would meet the same way. The chain also names
    the client's timeout, so login_failure must claim it first."""
    await world.phone_plays(PARAPLUIE)

    await world.journal_says(
        'level=error msg="failed advancing to next track" error="failed loading current track '
        '(advance to spotify:track:x): failed getting track metadata: spclient request failed: '
        'Get \\"https://gew1-spclient.spotify.com/metadata/4/track/x\\": dial tcp '
        '35.186.224.24:443: i/o timeout (Client.Timeout exceeded while awaiting headers)"'
    )
    await world.idle()

    assert world.restarts == []
    assert world.active()
    assert world.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]


# === Multiroom ===

async def test_a_reroute_keeps_the_session_and_publishes_no_pause(world):
    """E09 (measured both ways): the reroute's own pause published "active,
    paused" 7 ms after STARTING and armed the auto-stop."""
    await world.phone_plays(PARAPLUIE)
    before = len(world.published())
    await world.reroute()
    during = world.published()[before:]
    assert all(p["session"]["phase"] != "paused" for p in during if p["session"]), during
    assert world.playing()
    assert world.daemon.output.startswith("milo_spotify_")
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0


async def test_a_reroute_whose_reopen_fails_does_not_leave_the_output_on_null(world):
    """E08: when the output could not be reopened, the fallback was a start —
    a no-op on a unit already running — and go-librespot went on writing to
    `null`: every later session played into nothing."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.refused_outputs = {"milo_spotify_direct", "milo_spotify_multiroom"}
    await world.reroute()
    assert world.daemon.output != "null"
    assert world.errors() == []


async def test_a_reroute_whose_output_switch_fails_reopens_the_output(world):
    """E08: snapcast refusing to move raised out of the reroute between the
    release and the reacquire, and the parked output was never reopened."""
    await world.phone_plays(PARAPLUIE)
    await world.reroute(fails=True)
    assert world.daemon.output != "null"
    assert world.playing()


async def test_two_reroutes_in_a_row_both_keep_the_session(world):
    """Each reroute parks the output and reopens it; the second must not
    find anything left over from the first (it used to read a flag)."""
    await world.phone_plays(PARAPLUIE)
    await world.reroute()
    await world.reroute()
    parks = [body for command, body in world.daemon.posted if command == "output"]
    assert [p["device"] == "null" for p in parks] == [True, False, True, False]
    assert world.playing()
    assert world.restarts == [] and world.systemd.stop.await_count == 0


async def test_a_backend_restart_under_the_session_is_not_a_death(world):
    """systemd stops go-librespot before the backend (BindsTo + After=), while
    the backend still runs: a stop that was asked for, not a crash — measured,
    the unit is `inactive` when the process is gone, `activating` after a kill."""
    from backend.tests.golden.harness import settle
    await world.phone_plays(PARAPLUIE)
    await world._unit_stop()
    await settle()
    assert not world.active()
    assert world.errors() == []


async def test_another_account_taking_over_shows_nothing_until_its_track(world):
    """E14's rule holds on a takeover too: a session of another account opens at
    its first named track — not on the untitled status that precedes it."""
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.account, d.track, d.paused, d.buffering = "someone-else", None, False, True
    await world._says({"type": "active"}, {"type": "will_play", "uri": LE_CHEMIN["uri"]})
    assert not (world.active() and world.session()["title"] is None)
    await world.advance(0.07)
    d.track, d.buffering = dict(LE_CHEMIN), False
    await world._says({"type": "metadata"}, {"type": "playing"})
    assert world.playing() and world.session()["title"] == "Le Chemin"


async def test_a_reroute_whose_resume_lags_publishes_no_pause(world):
    """E09 after the reacquire: go-librespot's /status lags its commands (the
    reason the release confirms its pause), so a /status read right after the
    resume could still say paused — and publish it, arming the auto-stop."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.resume_lag = True
    before = len(world.published())
    await world.reroute()
    during = world.published()[before:]
    assert all(p["session"]["phase"] != "paused" for p in during if p["session"]), during
    assert world.playing()


async def test_a_daemon_that_never_answers_is_on_screen_until_its_events_connect(
    monkeypatch, tmp_path
):
    """E27: go-librespot never answering at start was an ERROR in the journal
    only (`source.*` loggers never reach the banner), so the phone found no
    Milō and the screen said nothing. It is a banner now, withdrawn as soon as
    the daemon's /events stream connects."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    comes_up = w.daemon.comes_up
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    assert w.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]

    comes_up()
    await w.advance(2.1)                    # the /events client retries every 2 s

    assert w.envelopes("source", "error_cleared")
    await w.source.shutdown()


async def test_a_daemon_that_answers_raises_no_banner(world):
    assert world.errors() == []


async def test_the_unanswered_banner_leaves_with_the_source(monkeypatch, tmp_path):
    """Review of E27: the banner a deaf daemon raised stayed over the next
    source once Spotify was left, the daemon still silent."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    assert w.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]

    await w.leave()

    assert w.envelopes("source", "error_cleared")
    await w.source.shutdown()


async def test_the_daemon_answering_late_leaves_a_newer_banner_standing(monkeypatch, tmp_path):
    """Review of E27: /events connecting late withdrew whatever banner stood,
    a track that failed to load meanwhile included."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    comes_up = w.daemon.comes_up
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    await w.source._handle_log_line('level=error msg="failed loading current track: no stream"', None)

    comes_up()
    await w.advance(2.1)                    # the /events client retries every 2 s

    assert w.errors() == [
        SourceErrorReason.SERVICE_UNREACHABLE, SourceErrorReason.TRACK_LOAD_FAILED,
    ]
    assert not w.envelopes("source", "error_cleared")
    await w.source.shutdown()


async def test_a_refused_login_banner_leaves_with_the_source(world):
    """The refusal belongs to the daemon the source stops: left standing, it
    would sit over the next source, and the next Spotify start would hold its
    first refusal back as a retry of the old one."""
    refused = ('level=error msg="failed creating new session from Mac mini" error="failed '
               'authenticating with login5: TRY_AGAIN_LATER"')
    from backend.tests.golden.harness import settle

    await world.source._handle_log_line(refused, None)
    await settle()
    assert world.errors() == [SourceErrorReason.PROVIDER_UNAVAILABLE]

    await world.leave()

    assert world.envelopes("source", "error_cleared")


async def test_a_track_load_banner_leaves_with_the_source(world):
    """A track that failed to load belongs to the daemon the source stops:
    left standing, the banner would sit over the next source."""
    await world.source._handle_log_line(
        'level=error msg="failed loading current track" error="no tracks"',
        None,
    )
    await settle()
    assert world.errors() == [SourceErrorReason.TRACK_LOAD_FAILED]

    await world.leave()

    assert world.envelopes("source", "error_cleared")


async def test_a_track_load_line_heard_while_the_source_stops_leaves_no_banner(world, monkeypatch):
    """journald hands a line on seconds late, so a line the daemon wrote before
    the stop can reach the journal task while the STOP is in hand. Posted, it
    runs after `_cleanup` cleared the banners, and nothing clears it again: the
    monitor is gone and Spotify is no longer the source, so the banner sat over
    the next one."""
    close = world.source._library.close

    async def close_while_a_line_arrives():
        await world.journal_says('level=error msg="failed loading current track" error="no tracks"')
        await close()

    monkeypatch.setattr(world.source._library, "close", close_while_a_line_arrives)

    await world.leave()
    await world.idle()

    banners = [
        e["type"] for e in world.recorder.envelopes
        if e["category"] == "source" and e["type"] in ("error", "error_cleared")
    ]
    assert not banners or banners[-1] == "error_cleared"


# === Another device of the account ===

@pytest.fixture
async def signed_in(monkeypatch, tmp_path):
    """A daemon signed in with stored credentials and no session here: the
    one state in which go-librespot can see another device play."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    yield w
    await w.source.shutdown()


def remote(world):
    return world.state()["details"]["remote"]


def title_shown(world):
    shown = remote(world)
    return shown and shown["title"]


def lookups(world, song):
    """How often Milō asked Spotify's metadata service about `song`."""
    return world.daemon.track_metadata_asked.count(gid_of(song))


async def test_another_device_playing_is_shown_without_a_session(signed_in):
    """What plays on the phone is drawn by the Spotify bar, never as a session:
    nothing plays here, and the widget and the Mac read `session` as Milō's.
    The cluster names the title and the album only: the artist and the cover
    are what Spotify's metadata service answers, asked once."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)

    assert not signed_in.active()
    shown = remote(signed_in)
    assert shown["device_name"] == "iPhone"
    assert shown["device_type"] == "smartphone"
    assert shown["title"] == "Parapluie"
    assert shown["album"] == "Réel"
    assert shown["artist"] == "Kery James"
    assert shown["artwork"] == cover_named_for(PARAPLUIE)
    assert shown["position"]["ms"] == 12_000
    assert signed_in.state()["controls"] == ["take_over"]
    assert lookups(signed_in, PARAPLUIE) == 1


async def test_a_remote_track_not_yet_named_is_not_shown(signed_in):
    """Until Spotify has named the track, a bar would draw a title alone and
    then redraw with the artist and the cover: nothing is shown, and there is
    nothing to take over yet."""
    signed_in.daemon.naming_delay = 0.2
    await signed_in.plays_elsewhere(PARAPLUIE)

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []

    await signed_in.advance(0.2)

    assert remote(signed_in)["artist"] == "Kery James"
    assert signed_in.state()["controls"] == ["take_over"]


async def test_the_bar_stays_up_while_the_next_track_elsewhere_is_named(signed_in):
    """A track change on the Mac: published while its artist is looked up,
    the bar (useSourcePlaybackVisibility) would leave and come back through
    its entrance animation on every track. The track shown stands until the
    next one is named."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000, device="Mac mini", kind="computer")
    signed_in.daemon.naming_delay = 0.2
    sent = len(signed_in.published())

    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)
    assert title_shown(signed_in) == "Parapluie"
    await signed_in.advance(0.2)

    shown = [s["details"]["remote"] for s in signed_in.published()[sent:]]
    assert None not in shown
    assert remote(signed_in)["title"] == "Trois Neuf Trois"
    assert remote(signed_in)["artist"] == "Kery James"
    assert remote(signed_in)["device_name"] == "Mac mini"


async def test_a_hold_whose_expiry_read_fails_is_read_again(signed_in):
    """The hold's end is the one moment Milō reads on its own, and
    go-librespot says nothing more once the cluster settled: a read failing
    then must be retried, or the old track stays on the bar for as long as the
    daemon stays quiet, though it answers again."""
    await signed_in.plays_elsewhere(PARAPLUIE, device="Mac mini", kind="computer")
    signed_in.daemon.transfer_never_lands = True
    await signed_in.command("take_over")
    await signed_in.nothing_plays_elsewhere()
    signed_in.daemon.devices_answers = False

    await signed_in.advance(signed_in.source.TAKE_OVER_HOLD + 0.5)
    assert title_shown(signed_in) == "Parapluie"
    signed_in.daemon.devices_answers = True
    await signed_in.advance(signed_in.source.STATUS_RETRY_DELAY + 1)

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []


async def test_take_over_asks_once_and_the_session_arrives_like_a_transfer(signed_in):
    """take_over is one POST /connect/transfer naming this device; the session
    is the daemon's, reconciled from what it says, at the position the phone
    was at. It landed playing, so nothing else is asked."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)

    result = await signed_in.command("take_over")
    await signed_in.transfer_lands()

    assert result["success"] is True
    transfers = [(c, body) for c, body in signed_in.daemon.posted if c.startswith("connect/")]
    assert transfers == [("connect/transfer", {"device_id": DEVICE_ID})]
    assert signed_in.resumes_sent() == 0
    assert signed_in.playing()
    assert signed_in.session()["title"] == "Parapluie"
    assert signed_in.position_ms() == 81_264
    assert remote(signed_in) is None


async def test_take_over_of_a_paused_remote_resumes_once_where_it_paused(signed_in):
    """go-librespot asks Spotify to restore the playback as it stood, so a
    paused phone's session lands paused: Milō resumes it, once, after the
    track loaded — a resume sent on `active` is answered 200 and lost — and it
    plays from where the phone paused, not from there plus the time it sat."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264, paused=True)
    await signed_in.advance(5)

    await signed_in.command("take_over")
    await signed_in.transfer_lands()

    asked = [c for c, _ in signed_in.daemon.posted if c in ("connect/transfer", "resume")]
    assert asked == ["connect/transfer", "resume"]
    assert signed_in.daemon.resumes_lost == 0
    assert signed_in.playing()
    assert abs(signed_in.position_ms() - 81_264) <= 100
    await signed_in.advance(30)
    assert signed_in.resumes_sent() == 1


async def test_a_paused_cast_from_the_phone_is_not_resumed(signed_in):
    """Only the session take_over asked for is resumed: a phone handing its
    paused session over (the iOS picker does) keeps it paused."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264, paused=True)

    signed_in.daemon._transfer_starts()
    await signed_in.transfer_lands()
    await signed_in.advance(5)

    assert signed_in.phase() == "paused"
    assert signed_in.resumes_sent() == 0


async def test_the_bar_stays_up_while_the_session_it_takes_over_arrives(signed_in):
    """Between the transfer and its session the daemon is active with no track
    and the cluster names it: published as such, the bar would leave and come
    back. The remote is held until the session takes its place."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    sent = len(signed_in.published())

    await signed_in.command("take_over")
    await signed_in.transfer_lands()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert shown[-1] == (True, False)


@pytest.mark.parametrize("arrival", ["asked here", "picked on the phone"])
async def test_take_over_while_the_session_arrives_asks_nothing_more(signed_in, arrival):
    """go-librespot does not refuse a transfer to the device already active:
    Spotify sends that session back and it loads again. A press while the
    session is on its way here asks nothing."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    if arrival == "asked here":
        await signed_in.command("take_over")
    else:
        signed_in.daemon._transfer_starts()
        await settle()
    asked = signed_in.transfers_sent()

    again = await signed_in.command("take_over")
    await signed_in.transfer_lands()

    assert again["success"] is True
    assert signed_in.transfers_sent() == asked
    assert signed_in.daemon.self_transfers == 0
    assert signed_in.playing()


@pytest.mark.parametrize("daemon", ["master", "the fork's queue route"])
async def test_the_bar_moves_to_the_phone_that_takes_the_session_back(signed_in, daemon):
    """go-librespot hands a session taken by a phone back to Spotify and signs
    in again, answering 204 meanwhile: the bar goes straight to the phone, on
    the cluster the daemon announced while the session still played here — a
    frame of its own, ahead of the `inactive` that ends it — rather than
    leaving and coming back."""
    signed_in.daemon.has_queue_route = daemon != "master"
    await signed_in.phone_plays(PARAPLUIE)
    sent = len(signed_in.published())

    await signed_in.phone_takes_it_back()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert remote(signed_in)["device_name"] == "iPhone"
    assert remote(signed_in)["position"]["ms"] == 90_000


async def test_an_announced_phone_that_never_shows_up_is_dropped(signed_in):
    """The announcement only bridges until /connect/devices answers again: if
    the daemon is still signing in past the hold, and then names nobody, the
    bar follows it."""
    await signed_in.phone_plays(PARAPLUIE)
    sent = len(signed_in.published())

    await signed_in.phone_takes_it_back(back_after=signed_in.source.HANDOVER_HOLD + 1, names_phone=False)

    shown = [s["details"]["remote"] for s in signed_in.published()[sent:]]
    assert any(r and r["device_name"] == "iPhone" for r in shown)
    assert remote(signed_in) is None


async def test_a_phone_taking_back_another_track_is_shown_at_once(signed_in):
    """The phone takes the session back on a track Milō never played, so
    Spotify has not named it yet, and the daemon, signing in again, has no
    token to ask with: the bar goes to the phone at once with what the cluster
    names, and the artist and the cover follow once the daemon answers. Only
    for a sender whose cluster names its track: the iPhone's names none."""
    signed_in.daemon.cluster_names_track = True
    await signed_in.phone_plays(PARAPLUIE)
    sent = len(signed_in.published())

    await signed_in.phone_takes_it_back(song=TROIS_NEUF_TROIS)
    await signed_in.advance(0.1)

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert remote(signed_in)["title"] == "Trois Neuf Trois"
    assert remote(signed_in)["artist"] == "Kery James"
    assert remote(signed_in)["artwork"] == cover_named_for(TROIS_NEUF_TROIS)
    assert lookups(signed_in, TROIS_NEUF_TROIS) == 1


async def test_the_phone_stays_shown_while_the_daemon_has_no_cluster_yet(signed_in):
    """Signed in again, go-librespot answers /connect/devices 404 until its
    first push is answered, and a state request (Milo-iOS polls) can read in
    that window: the announced phone stands, and a 404 is no failure to read
    again."""
    d = signed_in.daemon
    await signed_in.phone_plays(PARAPLUIE)
    sent = len(signed_in.published())
    await signed_in.phone_takes_it_back(first_push=False)

    await signed_in.get_state()
    assert remote(signed_in)["device_name"] == "iPhone"
    reads = d.devices_reads
    await signed_in.advance(signed_in.source.STATUS_RETRY_DELAY + 0.5)
    assert d.devices_reads == reads
    await signed_in.first_push_answers()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert remote(signed_in)["device_name"] == "iPhone"


async def test_an_old_announcement_does_not_come_back_on_a_later_end(signed_in):
    """Only a cluster heard while the session that ends played stands in for
    the read: a phone announced before it is not shown when Milō's own
    session ends."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.command("take_over")
    await signed_in.transfer_lands()
    signed_in.daemon.signed_in = False   # the 204 of the sign-in after the end
    signed_in.daemon._ends()
    await signed_in.advance(0.1)

    assert remote(signed_in) is None


async def test_the_bar_stays_up_when_the_phone_picks_milo(signed_in):
    """The same arrival started from the phone's Spotify app: no take-over
    was asked, and the daemon is still active with no track for a moment."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    sent = len(signed_in.published())

    signed_in.daemon._transfer_starts()
    await signed_in.transfer_lands()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert signed_in.playing()
    assert remote(signed_in) is None


async def test_a_second_take_over_before_the_daemon_moves_asks_nothing(signed_in):
    """POST /connect/transfer answers before the daemon says `active`: a
    second press in between is the same take-over, and asks nothing."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    signed_in.daemon.transfer_start_delay = 0.2

    await signed_in.command("take_over")
    again = await signed_in.command("take_over")
    await signed_in.advance(0.2)
    await signed_in.transfer_lands()

    assert again["success"] is True
    assert signed_in.transfers_sent() == 1
    assert signed_in.playing()


async def test_a_take_over_resumes_once_the_track_has_loaded(signed_in):
    """/status may name the transferred track while it still buffers: a resume
    then would land in the load, so Milō waits for the track to be ready, and
    resumes it once."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264, paused=True)

    await signed_in.command("take_over")
    await signed_in.transfer_lands(buffers_for=0.2)

    assert signed_in.resumes_sent() == 1
    assert signed_in.playing()


async def test_a_take_over_that_never_lands_lets_the_bar_follow_status(signed_in):
    """A transfer Spotify accepted and never delivered: past the hold, the
    wire says what the cluster says — here, nothing plays anywhere. A session
    a phone casts paused after it is the phone's, and is not resumed."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.transfer_never_lands = True
    await signed_in.command("take_over")
    await signed_in.nothing_plays_elsewhere()
    assert title_shown(signed_in) == "Parapluie"

    await signed_in.advance(signed_in.source.TAKE_OVER_HOLD + 1)

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []
    await signed_in.phone_transfers(PARAPLUIE)
    assert signed_in.resumes_sent() == 0


async def test_the_phone_moving_on_during_a_take_over_is_shown(signed_in):
    """A hold keeps the bar up while nothing names the phone; what the cluster
    does name is taken at once — here the phone skipped while Milō waited for
    a transfer that never came, and the bar shows the new track, not a frozen
    copy."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.transfer_never_lands = True
    await signed_in.command("take_over")

    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)

    assert remote(signed_in)["title"] == "Trois Neuf Trois"


async def test_a_take_over_that_never_landed_resumes_no_later_session(signed_in):
    """What the phone does after a take-over that never came (here a skip,
    which ends the hold before its time) is the phone's: the paused session it
    hands over much later is not the one take_over asked for."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.transfer_never_lands = True
    await signed_in.command("take_over")
    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)
    await signed_in.pauses_elsewhere()

    await signed_in.advance(signed_in.source.TAKE_OVER_HOLD + 1)
    signed_in.daemon._transfer_starts()
    await signed_in.transfer_lands()

    assert signed_in.phase() == "paused"
    assert signed_in.resumes_sent() == 0


async def test_an_arrival_that_never_loads_does_not_hold_the_bar_for_ever(signed_in):
    """A session arriving holds the remote while its track loads — for a
    bounded time: a daemon left buffering with no track shows nothing after."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon._transfer_starts()
    await settle()
    assert remote(signed_in) is not None

    await signed_in.advance(signed_in.source.ARRIVAL_HOLD + 1)

    assert remote(signed_in) is None


async def test_a_refused_take_over_does_not_hold_the_bar(signed_in):
    """Spotify refused the transfer (go-librespot's 502): nothing is coming,
    so nothing is held — the next read that names no phone takes the bar down
    at once."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.transfer_refused = True

    result = await signed_in.command("take_over")
    await signed_in.nothing_plays_elsewhere()

    assert result["success"] is False
    assert remote(signed_in) is None


async def test_what_played_elsewhere_does_not_outlive_the_daemon_run(monkeypatch, tmp_path):
    """Leaving Spotify stops go-librespot: coming back, the bar shows nothing
    of a phone until the new daemon names one — no stale take_over to press —
    and what Spotify named belonged to the run: the new one asks again."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    await w.plays_elsewhere(PARAPLUIE)
    await w.leave()
    w.daemon.active_device_id = ""
    sent = len(w.published())

    await w.select()

    assert all(s["details"] is None or s["details"].get("remote") is None for s in w.published()[sent:])
    assert all("take_over" not in s["controls"] for s in w.published()[sent:])
    await w.plays_elsewhere(PARAPLUIE)
    assert lookups(w, PARAPLUIE) == 2
    assert remote(w)["artist"] == "Kery James"
    await w.source.shutdown()


async def test_a_lookup_cut_by_a_stop_is_asked_again_by_the_next_run(monkeypatch, tmp_path):
    """A lookup still in flight when Spotify is left belongs to that run: its
    answer is not shown in the next, which asks again and shows its own."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    w.daemon.naming_delay = 1.0
    await w.plays_elsewhere(PARAPLUIE)
    await w.leave()
    await w.advance(0.5)
    await w.select()

    await w.advance(0.6)
    assert remote(w) is None
    await w.advance(0.5)

    assert lookups(w, PARAPLUIE) == 2
    assert remote(w)["artist"] == "Kery James"
    await w.source.shutdown()


async def test_a_lookup_spotify_never_answers_costs_the_artist_only(signed_in, caplog):
    """Spotify's metadata service is given TRACK_NAMING_TIMEOUT: past it, the
    track is shown without its artist and cover, said once (a sender whose
    cluster names its track)."""
    signed_in.daemon.cluster_names_track = True
    signed_in.daemon.naming_delay = signed_in.source.TRACK_NAMING_TIMEOUT + 1
    await signed_in.plays_elsewhere(PARAPLUIE)

    await signed_in.advance(signed_in.source.TRACK_NAMING_TIMEOUT + 0.1)

    shown = remote(signed_in)
    assert shown["title"] == "Parapluie"
    assert (shown["artist"], shown["artwork"]) == (None, None)
    assert len([r for r in caplog.records if "did not name" in r.getMessage()]) == 1


async def test_what_spotify_named_is_kept_for_so_many_tracks(signed_in, monkeypatch):
    """The names are kept per track up to REMOTE_NAMES_KEPT: past it, the
    oldest track is asked again when it comes back."""
    monkeypatch.setattr(signed_in.source, "REMOTE_NAMES_KEPT", 1)
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)
    await signed_in.next_track_elsewhere(PARAPLUIE)

    assert lookups(signed_in, PARAPLUIE) == 2
    assert lookups(signed_in, TROIS_NEUF_TROIS) == 1


async def test_take_over_with_nothing_elsewhere_asks_nothing(signed_in):
    result = await signed_in.command("take_over")

    assert result["success"] is False
    assert signed_in.transfers_sent() == 0


async def test_take_over_without_a_device_id_asks_nothing(monkeypatch, tmp_path):
    """The transfer names this device by the id /status gives: without it,
    there is nothing to ask for, and nothing is posted."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.daemon.device_id = None
    await w.select()
    await w.plays_elsewhere(PARAPLUIE)

    result = await w.command("take_over")

    assert result["success"] is False
    assert w.transfers_sent() == 0
    await w.source.shutdown()


async def test_a_state_request_rereading_the_same_remote_publishes_nothing(signed_in):
    """Milo-iOS polls the state: a read of the same remote playback, its
    playhead where the anchor says, must not re-stamp the anchor, nor ask
    Spotify to name the track again."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)
    sent = len(signed_in.published())
    await signed_in.advance(30)

    await signed_in.get_state()

    assert len(signed_in.published()) == sent
    assert lookups(signed_in, PARAPLUIE) == 1


async def test_a_clock_step_re_reads_the_other_devices_playhead(signed_in, monkeypatch):
    """The remote card's anchor is stamped on the wall clock and nothing re-dated
    it across a step: the unit boots ~5 min behind and steps at the first NTP
    answer, and the bar sat at the end of the track until go-librespot next
    said something. The kernel's notice of the step re-reads
    `GET /connect/devices`, whose position is the daemon's own."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)
    monkeypatch.setattr(audio_source, "wall_time", lambda: EPOCH + signed_in.clock.now + 600)

    signed_in.machine.clock_stepped()
    await settle()

    anchor = remote(signed_in)["position"]
    read = anchor["ms"] + (audio_source.wall_time() - anchor["at"]) * 1000
    assert abs(read - 12_000) <= 2000


async def test_the_other_device_leaving_takes_the_remote_away(signed_in):
    """A phone gone to the background leaves the cluster within seconds: the
    bar goes, and with it the button."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.nothing_plays_elsewhere()

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []


async def test_a_paused_remote_is_still_shown(signed_in):
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=5_000, paused=True)

    assert remote(signed_in)["paused"] is True
    assert signed_in.state()["controls"] == ["take_over"]


async def test_a_track_spotify_cannot_name_is_shown_without_its_artist(signed_in, caplog):
    """A refusal of Spotify's metadata service (a 429, an outage) costs the
    artist and the cover, never the bar; it is said once, and kept for the
    run: the cluster's later words about the same track ask nothing more.
    For a sender whose cluster names its track: the iPhone's names none."""
    signed_in.daemon.cluster_names_track = True
    signed_in.daemon.unnamed.add(PARAPLUIE["uri"])

    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.pauses_elsewhere()
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=60_000)

    shown = remote(signed_in)
    assert (shown["title"], shown["album"]) == ("Parapluie", "Réel")
    assert (shown["artist"], shown["artwork"]) == (None, None)
    assert lookups(signed_in, PARAPLUIE) == 1
    assert len([r for r in caplog.records if "did not name" in r.getMessage()]) == 1


async def test_one_lookup_per_track_whatever_the_cluster_says(signed_in):
    """The cluster moves at every volume change and every pause: during one
    lookup, each of its words asks nothing more, and the one that counts is
    the last — the remote shows paused once it is named."""
    d = signed_in.daemon
    d.naming_delay = 0.5
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=1_000)
    await signed_in._says(d.cluster_event())
    await signed_in.pauses_elsewhere()
    await signed_in._says(d.cluster_event())
    assert remote(signed_in) is None

    await signed_in.advance(0.5)

    assert lookups(signed_in, PARAPLUIE) == 1
    assert remote(signed_in)["paused"] is True


async def test_a_stale_cluster_with_nobody_active_shows_nothing(signed_in):
    """With nobody active, the cluster still holds the last playback —
    `playing`, pinned at the track's end: it is no device's, and shows
    nothing, nor asks anything of Spotify."""
    await signed_in.plays_elsewhere(PARAPLUIE, announced=False)
    await signed_in.advance(PARAPLUIE["duration"] / 1000 + 5)

    await signed_in.nobody_active_stale()

    stale = signed_in.daemon.cluster()["now_playing"]
    assert stale["playing"] and stale["position"] == PARAPLUIE["duration"]
    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []
    assert signed_in.daemon.track_metadata_asked == []


async def test_a_track_spotify_could_not_name_yet_is_asked_again_later(signed_in):
    """A failure that may pass (an outage, a 429, a sign-in) is not kept: the
    iPhone's cluster names nothing but the uri, so a card kept unnamed would
    stay away for the whole run. It is asked again TRACK_NAMING_RETRY later,
    at the next read that names it, and never in between."""
    d = signed_in.daemon
    d.unnamed.add(PARAPLUIE["uri"])
    await signed_in.plays_elsewhere(PARAPLUIE)
    assert remote(signed_in) is None

    await signed_in.pauses_elsewhere()
    assert lookups(signed_in, PARAPLUIE) == 1

    d.unnamed.discard(PARAPLUIE["uri"])
    await signed_in.advance(signed_in.source.TRACK_NAMING_RETRY + 1)
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=60_000)

    assert lookups(signed_in, PARAPLUIE) == 2
    assert remote(signed_in)["title"] == "Parapluie"


async def test_a_track_spotify_refuses_is_not_asked_again(signed_in):
    """A refusal (404) is an answer: kept for the run, never asked again."""
    signed_in.daemon.refused.add(PARAPLUIE["uri"])
    await signed_in.plays_elsewhere(PARAPLUIE)

    await signed_in.advance(signed_in.source.TRACK_NAMING_RETRY + 1)
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=60_000)

    assert lookups(signed_in, PARAPLUIE) == 1
    assert remote(signed_in) is None


async def test_the_clusters_artist_stands_in_when_spotify_names_none(signed_in):
    """A sender that puts the artist in its cluster (go-librespot's
    artist_name) keeps it on the card when Spotify's lookup fails."""
    d = signed_in.daemon
    d.cluster_names_track = True
    d.unnamed.add(PARAPLUIE["uri"])
    playing = d._now_playing
    d._now_playing = lambda: {**playing(), "artist_name": "Kery James"} if playing() else None

    await signed_in.plays_elsewhere(PARAPLUIE)

    assert (remote(signed_in)["title"], remote(signed_in)["artist"]) == ("Parapluie", "Kery James")


async def test_an_album_a_track_names_empty_is_published_as_null(signed_in):
    """go-librespot names a track's album with an empty string when it has
    none; a phone taking that track back shows it from what was played here,
    and an empty string is never published."""
    await signed_in.phone_plays({**PARAPLUIE, "album_name": ""})
    await signed_in.phone_takes_it_back()

    assert remote(signed_in)["title"] == "Parapluie"
    assert remote(signed_in)["album"] is None


async def test_a_cluster_naming_milo_active_holds_the_bar_until_the_session(signed_in):
    """A transfer here can be named by the cluster before the daemon's own
    events: the bar holds what played elsewhere (ARRIVAL_HOLD) instead of
    going blank until `active` comes."""
    d = signed_in.daemon
    await signed_in.plays_elsewhere(PARAPLUIE)
    assert remote(signed_in) is not None

    d.active_device_id = DEVICE_ID
    await signed_in._says(d.cluster_event())

    assert remote(signed_in)["title"] == "Parapluie"


@pytest.mark.parametrize("case", ["loading here", "named active, no session"])
async def test_milos_own_playback_in_the_cluster_is_never_a_remote(signed_in, case):
    """The cluster's playback is this device's own when this device is the
    active one — a session arriving here before its track is named, or a
    cluster naming it active though no session plays here."""
    d = signed_in.daemon
    if case == "loading here":
        d.session, d.account, d.track, d.paused, d.buffering = True, ACCOUNT, None, False, True
        d.loading = PARAPLUIE
        events = ({"type": "active"}, d.cluster_event())
    else:
        d.active_device_id = DEVICE_ID
        signed_in._reports(PARAPLUIE, 0, False)
        events = (d.cluster_event(),)
    assert events[-1]["data"]["active_device_id"] == DEVICE_ID
    assert events[-1]["data"]["now_playing"]["track_uri"] == PARAPLUIE["uri"]

    await signed_in._says(*events)

    assert remote(signed_in) is None
    assert signed_in.daemon.track_metadata_asked == []


async def test_a_playback_nothing_names_shows_nothing(signed_in):
    """The iPhone's cluster names no title (measured 2026-11-09): a track
    Spotify cannot name either has nothing to draw (an empty string is never
    published), and is asked once."""
    d = signed_in.daemon
    d.unnamed.add(PARAPLUIE["uri"])
    await signed_in.plays_elsewhere(PARAPLUIE)

    assert d.cluster()["now_playing"]["track_name"] == ""
    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []
    assert lookups(signed_in, PARAPLUIE) == 1


async def test_an_active_device_missing_from_the_list_shows_nothing(signed_in):
    """An active id the cluster does not list has no name to show."""
    d = signed_in.daemon
    d.active_device_id = "f00dfeedf00dfeedf00dfeedf00dfeedf00dfeed"
    signed_in._reports(PARAPLUIE, 0, False)

    await signed_in._says(d.cluster_event())

    assert remote(signed_in) is None
    assert d.track_metadata_asked == []


async def test_a_cluster_burst_while_milo_plays_reads_nothing(signed_in):
    """`cluster` comes at every re-send of Milō's own pushes and every volume
    change: with a session here, a burst of them alone reads nothing and
    publishes nothing."""
    d = signed_in.daemon
    await signed_in.phone_plays(PARAPLUIE)
    reads = (d.status_reads, d.devices_reads)
    sent = len(signed_in.published())

    for _ in range(5):
        await signed_in._says(d.cluster_event())

    assert (d.status_reads, d.devices_reads) == reads
    assert len(signed_in.published()) == sent


async def test_the_first_cluster_of_a_session_is_read_without_its_event(monkeypatch, tmp_path):
    """go-librespot keeps the first cluster of a session from the answer to
    its first push and announces it with no `cluster`: a phone already playing
    when Spotify is selected is shown from the read at connection."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.plays_elsewhere(PARAPLUIE, at_ms=5_000, announced=False)

    await w.select()

    assert remote(w)["title"] == "Parapluie"
    assert remote(w)["artist"] == "Kery James"
    await w.source.shutdown()


async def test_a_devices_read_that_fails_changes_nothing(signed_in):
    """A /connect/devices read that did not answer learned nothing (E10):
    what is shown stands, and it is read again, since the daemon may say
    nothing more."""
    d = signed_in.daemon
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)
    d.devices_answers = False

    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)
    assert title_shown(signed_in) == "Parapluie"
    d.devices_answers = True
    await signed_in.advance(signed_in.source.STATUS_RETRY_DELAY + 0.5)

    assert title_shown(signed_in) == "Trois Neuf Trois"


async def test_a_devices_read_is_retried_once_per_outage(signed_in):
    """One retry per outage, not a poll: a daemon that keeps failing is read
    once more, and an answer in between gives the next outage its own retry."""
    d = signed_in.daemon
    await signed_in.plays_elsewhere(PARAPLUIE)
    d.devices_answers = False
    await signed_in.next_track_elsewhere(TROIS_NEUF_TROIS)
    reads = d.devices_reads

    await signed_in.advance(signed_in.source.STATUS_RETRY_DELAY * 5)
    assert d.devices_reads == reads + 1

    d.devices_answers = True
    await signed_in.pauses_elsewhere()
    assert title_shown(signed_in) == "Trois Neuf Trois"
    d.devices_answers = False
    await signed_in.next_track_elsewhere(LE_CHEMIN)
    d.devices_answers = True
    await signed_in.advance(signed_in.source.STATUS_RETRY_DELAY + 0.5)

    assert title_shown(signed_in) == "Le Chemin"


async def test_an_episode_elsewhere_is_shown_without_a_lookup(signed_in):
    """Spotify's track metadata names tracks: an episode is shown as the
    cluster names it, with no artist and no cover, and nothing is asked."""
    signed_in.daemon.cluster_names_track = True
    episode = {**track("Le Code a changé", artists=()), "uri": "spotify:episode:4rOoJ6Egrf8K2IrywzwOMk"}

    await signed_in.plays_elsewhere(episode)

    assert remote(signed_in)["title"] == "Le Code a changé"
    assert (remote(signed_in)["artist"], remote(signed_in)["artwork"]) == (None, None)
    assert signed_in.daemon.track_metadata_asked == []


# === The history: what this player started, as each Spotify app keeps its own ===

PLAYLIST = "spotify:playlist:37i9dQZF1EIfQlcOmZWfLS"


async def history(world):
    """The signed-in account's history, once its writes landed."""
    await world.idle()
    return world.source.history.tracks(ACCOUNT)


async def test_a_track_started_here_is_listed_newest_first_with_the_context_it_played_in(world):
    """The queue page's recently played tab plays a track again in the context
    it played in, and draws its title, artists, cover and length: each entry
    carries them, newest first, and the file holds the same."""
    world.daemon.context = (PLAYLIST, "Mix")
    await world.phone_plays({**LE_CHEMIN, "album_cover_url": COVER})
    await world.phone_plays(PARAPLUIE)

    listed = await history(world)
    assert [(t["uri"], t["context_uri"], t["title"]) for t in listed] == [
        (PARAPLUIE["uri"], PLAYLIST, "Parapluie"),
        (LE_CHEMIN["uri"], PLAYLIST, "Le Chemin"),
    ]
    assert listed[1]["artist"] == "Kery James"
    assert listed[1]["duration_ms"] == LE_CHEMIN["duration"]
    assert listed[1]["thumbnail"] == ROW_COVER
    kept = json.loads(world.history_file.read_text())["accounts"][ACCOUNT]
    assert [t["uri"] for t in kept] == [PARAPLUIE["uri"], LE_CHEMIN["uri"]]


async def test_a_pause_and_a_resume_are_one_play_and_a_transferred_track_counts_when_it_starts(world):
    """go-librespot says `playing` on every resume; only the one after a track
    loaded (`metadata`) is a start — and a track a phone hands over loads
    paused, so its start is a resume."""
    await world.phone_transfers(PARAPLUIE)
    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"]]

    await world.phone_pauses()
    await world.phone_resumes()
    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"]]


async def test_a_track_played_again_is_listed_again(world):
    """The Spotify apps keep every play, the same track twice in a row
    included (measured on the Mac app's history)."""
    await world.phone_plays(PARAPLUIE)
    await world.phone_plays(PARAPLUIE)

    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"], PARAPLUIE["uri"]]


async def test_a_start_whose_status_read_failed_is_listed_once_the_retry_names_it(world):
    """The start is said once, in a burst whose /status read can fail: it waits
    for a read that confirms the account — even one already known, as a guest's
    cast changes it — rather than being lost or listed under another."""
    await world.phone_plays(LE_CHEMIN)
    world.daemon.status_answers = False
    await world.phone_plays(PARAPLUIE)
    assert [t["uri"] for t in await history(world)] == [LE_CHEMIN["uri"]]

    world.daemon.status_answers = True
    await world.advance(2.1)                  # the retry reads it
    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"], LE_CHEMIN["uri"]]


async def test_two_starts_in_one_burst_are_two_plays(world):
    """Two quick skips can reach Milō in one burst of events: each start is
    listed, as the Spotify apps list every play."""
    d = world.daemon
    await world.phone_plays(LE_CHEMIN)
    d.track = dict(TROIS_NEUF_TROIS)
    first = (d.metadata(), d.playing())
    d.track = dict(PARAPLUIE)
    await world._says(*first, d.metadata(), d.playing())

    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"], TROIS_NEUF_TROIS["uri"], LE_CHEMIN["uri"]]


async def test_a_start_keeps_its_own_track_when_status_already_names_the_next(world):
    """The entry is made from the events that said the track started, never
    from a later /status: read after a skip, that names the next track, and
    the tab would show the first track's uri under the second one's title."""
    world.daemon.status_answers = False
    await world.phone_plays(PARAPLUIE)
    world.daemon.track = dict(LE_CHEMIN)       # skipped before the retry reads
    world.daemon.status_answers = True
    await world.advance(2.1)

    listed = await history(world)
    assert [(t["uri"], t["title"]) for t in listed] == [(PARAPLUIE["uri"], "Parapluie")]


async def test_a_start_that_names_no_track_is_not_listed(world):
    """An entry with no uri could be neither drawn nor played again."""
    await world.phone_plays(PARAPLUIE)
    await world._says({"type": "metadata", "data": {}}, {"type": "playing"})

    assert [t["uri"] for t in await history(world)] == [PARAPLUIE["uri"]]


async def test_the_history_route_answers_after_the_session_ended(world):
    """The tab is drawn while nothing plays: the route reads what Milō kept,
    never the daemon."""
    await world.phone_plays(PARAPLUIE)
    await world.phone_leaves()
    await world.idle()

    answer = await routes.get_history(source=world.source)
    assert [t["uri"] for t in answer["tracks"]] == [PARAPLUIE["uri"]]
