# backend/tests/test_radio_radiofrance.py
"""Radio France's own now-playing for the stations Radio France streams.

Two halves. The pure one (sources/radio/radiofrance.py): which stream URL is
which Radio France station, what a recorded answer of the feed names, and
which song the listener hears at a given wall time — the API announces a
song's playout start, and the listener hears it ICECAST_LEAD_S (or HLS_LEAD_S)
later. The behaviour one: RadioSource driven through RadioWorld (mpv
simulated, the source's timers and wall clock on a VirtualClock) against a
fake api.radiofrance.fr, asserting what the radio card and the lock screen
would read, and what was asked of Radio France and of Shazam.

What breaks when these fail: FIP and its webradios named by Shazam again (a
title per 20-60 s, often wrong, throttled), or by Radio France at the wrong
moment — the next song's title shown half a minute before it is heard.
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import aiohttp
import pytest

from backend.sources.radio import radiofrance
from backend.sources.radio.radiofrance import (
    HLS_LEAD_S, HOLD_S, ICECAST_LEAD_S, MAX_REFRESH_S, MIN_REFRESH_S, DEFAULT_REFRESH_S,
    STATIONS, Song, lead_for, merge, names_a_song, parse, schedule, station_for,
)
from backend.tests.golden.harness import EPOCH, settle
from backend.tests.golden.test_wire_radio import NOVA
from backend.tests.radio_world import RadioWorld

# Answers of the real feed, recorded verbatim (see the file's `_recorded`).
ANSWERS = json.loads((Path(__file__).parent / "radiofrance_answers.json").read_text())


# === Which station a URL is ===

class TestStationFor:
    """The URL mpv was handed is the only thing that says a station is Radio
    France's: a miss here is a FIP named by Shazam, a false hit a request per
    song to Radio France for a station it does not carry."""

    @pytest.mark.parametrize("url,slug", [
        # The owner's favorites, as stored (http and https icecast, both HLS forms).
        ("http://icecast.radiofrance.fr/fiphiphop-hifi.aac", "fiphiphop"),
        ("https://icecast.radiofrance.fr/fippop-hifi.aac", "fippop"),
        ("http://icecast.radiofrance.fr/francemusique-hifi.aac", "francemusique"),
        ("https://stream.radiofrance.fr/fipcultes/fipcultes_hifi.m3u8", "fipcultes"),
        ("https://stream.radiofrance.fr/fipcultes/fipcultes.m3u8?id=radiofranceBose", "fipcultes"),
        # Every quality, both codecs, a query string.
        ("http://icecast.radiofrance.fr/fbalsace-midfi.mp3", "fbalsace"),
        ("http://icecast.radiofrance.fr/fip-lofi.mp3", "fip"),
        ("https://stream.radiofrance.fr/franceinfo/franceinfo_midfi.m3u8?id=radiofrance", "franceinfo"),
        ("https://stream.radiofrance.fr/mouv/mouv_lofi.m3u8", "mouv"),
        # The direct.* aliases, mapped by their measured 301, case lowered.
        ("http://direct.francebleu.fr/live/Fbelsass-midfi.mp3", "fbelsass"),
        ("http://direct.francemusique.fr/live/francemusique-hifi.aac?ID=radiofrance", "francemusique"),
        ("http://direct.franceinter.fr/live/franceinter-midfi.mp3", "franceinter"),
        ("http://direct.mouv.fr/live/mouv-hifi.aac", "mouv"),
        ("http://direct.fipradio.fr/live/fipjazz-hifi.aac", "fipjazz"),
        ("http://direct.fipradio.fr/live/fip-webradio1.mp3", "fiprock"),
        ("http://direct.fipradio.fr/live/fip-webradio3.mp3", "fipgroove"),
        ("http://direct.fipradio.fr/live/fip-webradio8.mp3", "fipelectro"),
        # The host is matched whatever its case.
        ("http://ICECAST.RadioFrance.FR/fip-hifi.aac", "fip"),
    ])
    def test_a_radio_france_stream_is_its_station(self, url, slug):
        assert station_for(url) == STATIONS[slug]

    @pytest.mark.parametrize("url", [
        "http://stream.example/fip.mp3",                          # not Radio France
        "http://icecast.radiofrance.fr.evil.example/fip-hifi.aac",
        "http://radio-metadata.fr:8000/fip",                      # a third-party relay
        "http://icecast.radiofrance.fr/fipbordeaux-midfi.mp3",    # a legacy mount, no station
        "http://icecast.radiofrance.fr/fip.aac",                  # no quality
        "https://stream.radiofrance.fr/fip/fipjazz.m3u8",         # directory ≠ file
        "http://direct.fipradio.fr/live/fip-webradio9.mp3",       # no such webradio
        "http://direct.example.fr/live/fip-hifi.aac",
        "",
        "not a url",
    ])
    def test_anything_else_is_no_station(self, url):
        assert station_for(url) is None

    def test_every_station_resolves_from_its_icecast_and_hls_urls(self):
        """The slugs carry digits and run long (fb100pour100annees80,
        francemusiqueconcertsradiofrance): a pattern too narrow for one of
        them drops that station back to Shazam in silence."""
        assert len(STATIONS) >= 78
        for slug, station in STATIONS.items():
            assert station_for(f"http://icecast.radiofrance.fr/{slug}-midfi.mp3") == station
            assert station_for(f"https://stream.radiofrance.fr/{slug}/{slug}_hifi.m3u8") == station

    def test_the_lead_follows_the_transport(self):
        assert lead_for("https://stream.radiofrance.fr/fip/fip_hifi.m3u8?id=x") == HLS_LEAD_S
        assert lead_for("http://icecast.radiofrance.fr/fip-hifi.aac") == ICECAST_LEAD_S


# === What an answer names ===

class TestParse:
    """Parsed from recordings of the real feed. A song is an entry with a
    title other than "Le direct" and its times; anything else names nothing."""

    def test_a_song_on_air_and_the_next_one(self):
        recorded = ANSWERS["fip_7_song_with_next"]
        answer = parse(recorded)

        now, upcoming = recorded["now"], recorded["next"][0]
        assert [s.title for s in answer.songs] == [now["title"], upcoming["title"]]
        song = answer.songs[0]
        assert song.artist == now["interpreters"]
        assert (song.start, song.end) == (now["startTime"], now["endTime"])
        assert song.artwork is not None and now["cover"] in song.artwork
        assert answer.malformed is None

    def test_a_talk_station_names_nothing(self):
        answer = parse(ANSWERS["inter_1_talk"])
        assert answer.songs == ()
        assert answer.malformed is None

    def test_classical_music_with_no_interpreter_is_still_a_song(self):
        recorded = ANSWERS["easyclassique_401_no_interpreters"]
        answer = parse(recorded)
        assert recorded["now"]["interpreters"] is None
        assert answer.songs and answer.songs[0].title == recorded["now"]["title"]
        assert answer.songs[0].artist == ""

    def test_a_le_direct_next_is_not_a_song(self):
        recorded = ANSWERS["fipelectro_74_next_is_le_direct"]
        assert [s.title for s in parse(recorded).songs] == [recorded["now"]["title"]]

    def test_the_refresh_delay_is_bounded(self):
        """`delayToRefresh` reaches an hour on a talk station and 10 s once a
        song's end is past: followed raw, a talk station that starts playing
        music is not seen for an hour."""
        assert parse(ANSWERS["fip_7_song_with_next"]).refresh_s == MAX_REFRESH_S   # 260 s asked
        assert parse(ANSWERS["inter_1_talk"]).refresh_s == MIN_REFRESH_S           # 10 s asked
        assert parse({**ANSWERS["inter_1_talk"], "delayToRefresh": 3_600_000}).refresh_s == MAX_REFRESH_S
        assert parse({**ANSWERS["inter_1_talk"], "delayToRefresh": 1}).refresh_s == MIN_REFRESH_S

    @pytest.mark.parametrize("payload", [
        [], "garbage", None, {"now": "x"}, {"now": {}, "next": "x"}, {"prev": [], "next": []},
    ])
    def test_an_answer_of_another_shape_is_no_song_and_says_so(self, payload):
        answer = parse(payload)
        assert answer.songs == ()
        assert answer.malformed
        assert answer.refresh_s == DEFAULT_REFRESH_S

    def test_a_refresh_that_is_not_a_number_is_the_default(self):
        assert parse({**ANSWERS["inter_1_talk"], "delayToRefresh": "soon"}).refresh_s == DEFAULT_REFRESH_S
        assert parse({**ANSWERS["inter_1_talk"], "delayToRefresh": True}).refresh_s == DEFAULT_REFRESH_S


# === When a song is shown ===

L = ICECAST_LEAD_S


def song(title: str, start: float, end: float) -> Song:
    return Song(title=title, artist="Artist", start=start, end=end, artwork=None)


A = song("A", 1000, 1200)
B = song("B", 1200, 1400)


class TestSchedule:
    """The song shown is the one the listener hears, not the one the API
    announces: the change is heard `lead` after the announced start."""

    def test_a_song_is_shown_only_once_it_is_heard(self):
        assert schedule([A], 1000 + L - 1, L) == (None, 1000 + L)
        assert schedule([A], 1000 + L, L) == (A, 1200 + L + HOLD_S)

    def test_the_next_song_takes_over_when_it_is_heard(self):
        shown, look = schedule([A, B], 1200 + L - 1, L)
        assert (shown, look) == (A, 1200 + L)
        assert schedule([A, B], 1200 + L, L)[0] == B

    def test_a_song_is_cleared_after_its_hold_when_nothing_follows(self):
        assert schedule([A], 1200 + L + HOLD_S - 1, L)[0] == A
        assert schedule([A], 1200 + L + HOLD_S, L) == (None, None)

    def test_a_song_is_held_while_a_later_one_is_known_but_not_heard(self):
        """"Le direct" between two songs, the next one late on its plan: the
        title stays rather than blinking to the station name."""
        late = song("B", 1300, 1500)
        assert schedule([A, late], 1200 + L + HOLD_S + 20, L) == (A, 1300 + L)

    def test_a_le_direct_answer_clears_nothing(self):
        """The feed answers "Le direct" between songs and once answered it on
        every station for 25 s: only the schedule clears a title."""
        known = merge([A], parse(ANSWERS["inter_1_talk"]).songs, 1000 + L, L)
        assert schedule(known, 1000 + L + 1, L)[0] == A

    def test_a_revised_start_moves_the_change(self):
        """Planned times are corrected by 6-14 s against the playout: the same
        song re-announced replaces its plan, and a song announced over another
        one's planned start replaces that one."""
        known = merge([], (A, B), 1000, L)
        revised_a = song("A", 1000, 1214)
        known = merge(known, (revised_a,), 1100, L)
        assert known == [revised_a]                     # B was planned inside A's new span
        revised_b = song("B", 1214, 1414)
        known = merge(known, (revised_b,), 1200, L)
        assert schedule(known, 1200 + L, L)[0] == revised_a
        assert schedule(known, 1214 + L, L)[0] == revised_b

        replanned = merge([B], (song("B", 1208, 1408),), 1100, L)
        assert [s.start for s in replanned] == [1208]

    def test_a_song_on_screen_stays_when_a_later_answer_corrects_its_start(self):
        """B was learned from `next[0]`, its start the planned end of A, and
        shown at that start + lead; the answer naming B on air then puts its
        start 10 s later, as the playout corrects it. Moved, B would no longer
        be heard and A would come back for those seconds: B → A → B, two
        publishes, a lyrics refetch and a lock-screen push each way."""
        known = merge([], (A, B), 1190, L)
        assert schedule(known, 1200 + L, L)[0] == B

        corrected = merge(known, (song("A", 1000, 1210), song("B", 1210, 1410)), 1240, L)
        assert schedule(corrected, 1240, L)[0].key == B.key
        assert schedule(corrected, 1247, L)[0].key == B.key

        alone = merge(known, (song("B", 1210, 1410),), 1240, L)
        assert schedule(alone, 1240, L)[0].key == B.key

    def test_a_correction_after_the_previous_song_was_dropped_does_not_blank_the_title(self):
        """A stale `now` (A, over) lands once B is heard, which drops A; the
        next answer names B alone, its start corrected 14 s later. Had B's
        start moved, nothing known would be heard and the card would fall to
        the station name for 14 s while A — then B — plays."""
        a, b = song("A", 600, 812), song("B", 812, 1000)
        known = merge([], (a, b), 800, L)
        known = merge(known, (a,), 852, L)
        assert schedule(known, 852, L)[0] == b

        known = merge(known, (song("B", 826, 1014),), 862, L)
        for t in (862, 863, 880):
            shown, _ = schedule(known, t, L)
            assert shown is not None and shown.key == b.key

    def test_two_songs_that_touch_by_a_second_are_two_songs_in_a_row(self):
        """Recorded: the song on air ends a second past the next one's start.
        Read as one announced over the other, the next song is forgotten by
        the following answer naming only `now` ("Le direct" next), and its
        title waits for a later read, half a minute or more."""
        recorded = ANSWERS["easyclassique_401_no_interpreters"]
        on_air, upcoming = parse(recorded).songs
        assert on_air.end > upcoming.start                  # the recorded overlap
        known = merge([], parse(recorded).songs, on_air.start + L + 1, L)

        only_now = {**recorded, "next": ANSWERS["fipelectro_74_next_is_le_direct"]["next"]}
        known = merge(known, parse(only_now).songs, on_air.start + L + 60, L)
        assert upcoming in known
        assert schedule(known, upcoming.start + L, L)[0] == upcoming

    def test_another_song_announced_at_the_same_start_replaces_the_planned_one(self):
        other = song("C", 1200, 1390)
        assert merge([A, B], (other,), 1100, L) == [A, other]

    def test_a_now_whose_end_is_past_is_data_like_any_other(self):
        """A `now` still answered after its end means "not updated yet"."""
        assert schedule(merge([], (A,), 1200 + L + 5, L), 1200 + L + 5, L)[0] == A

    def test_a_song_long_over_names_nothing(self):
        """A rarely read URL once answered a song that ended an hour before."""
        now = 1200 + 3600
        assert not names_a_song([A], now, L)
        assert schedule([A], now, L)[0] is None
        assert names_a_song([A], 1000, L)

    def test_merge_keeps_only_what_can_still_be_shown(self):
        known = merge([], (A, B), 1200 + L + 1, L)
        assert known == [B]


# === The source, against a fake api.radiofrance.fr ===

FIP_RF = {
    "id": "fip-rf", "name": "FIP", "url": "http://icecast.radiofrance.fr/fip-hifi.aac",
    "favicon": "https://img.example/fip.png",
}
FIPCULTES_HLS = {
    "id": "fipcultes-hls", "name": "FIP Cultes",
    "url": "https://stream.radiofrance.fr/fipcultes/fipcultes_hifi.m3u8",
    "favicon": "https://img.example/fipcultes.png",
}
INTER_RF = {
    "id": "inter-rf", "name": "France Inter",
    "url": "https://stream.radiofrance.fr/franceinter/franceinter_hifi.m3u8",
    "favicon": "https://img.example/inter.png",
}


class FakeRadioFrance:
    """api.radiofrance.fr: one answer per station, or a failure, read by
    RadioFranceClient through what stands in for aiohttp.ClientSession."""

    def __init__(self) -> None:
        self.answers: Dict[int, Any] = {}
        self.failure: Optional[Exception] = None
        self.reads: List[int] = []           # station ids asked, in order
        self.held: Optional[asyncio.Event] = None   # a read waits for it while set

    def module(self) -> SimpleNamespace:
        return SimpleNamespace(ClientSession=self._session, ClientTimeout=aiohttp.ClientTimeout)

    def _session(self, **_: Any) -> "_Session":
        return _Session(self)

    def song_now(self, station: int, title: str, *, started_ago: float, lasts: float = 200,
                 refresh_ms: int = 30_000) -> None:
        """The station's answer: `title` on air since `started_ago` seconds
        of wall time (the playout's start, not what is heard)."""
        start = int(EPOCH - started_ago)
        self.answers[station] = {
            "prev": [ANSWERS["inter_1_talk"]["prev"][0]],
            "now": {
                "title": title, "interpreters": "Artist", "album": None, "label": None,
                "cover": f"cover-{title}", "musicalKind": None,
                "startTime": start, "endTime": start + lasts,
            },
            "next": [ANSWERS["inter_1_talk"]["next"][0]],
            "delayToRefresh": refresh_ms,
        }


class _Session:
    def __init__(self, world: FakeRadioFrance) -> None:
        self._world = world
        self.closed = False

    def get(self, url: str, **_: Any) -> "_Response":
        return _Response(self._world, int(re.search(r"/live/(\d+)/", url).group(1)))

    async def close(self) -> None:
        self.closed = True


class _Response:
    status = 200

    def __init__(self, world: FakeRadioFrance, station: int) -> None:
        self._world, self._station = world, station

    async def __aenter__(self) -> "_Response":
        world = self._world
        world.reads.append(self._station)
        if world.held is not None:
            await world.held.wait()
        if world.failure is not None:
            raise world.failure
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def json(self, **_: Any) -> Any:
        return self._world.answers[self._station]


FIP_ID = STATIONS["fip"]
FIPCULTES_ID = STATIONS["fipcultes"]
INTER_ID = STATIONS["franceinter"]


@pytest.fixture
def feed(monkeypatch) -> FakeRadioFrance:
    world = FakeRadioFrance()
    monkeypatch.setattr(radiofrance, "aiohttp", world.module())
    world.answers[INTER_ID] = ANSWERS["inter_1_talk"]
    return world


@pytest.fixture
def radio(monkeypatch, feed) -> RadioWorld:
    world = RadioWorld(monkeypatch, clock=True)
    world.favorites(FIP_RF, FIPCULTES_HLS, INTER_RF, NOVA)
    return world


async def play(radio: RadioWorld, station: Dict[str, Any]) -> None:
    await radio.select()
    await radio.tune(station)
    await radio.opens()


class TestARadioFranceSongIsTheTitle:

    async def test_the_song_is_named_when_it_is_heard_and_shazam_never_starts(self, radio, feed):
        """Announced 10 s ago on icecast: heard ICECAST_LEAD_S after its start.
        Before that the card names the station, as it does today; Shazam,
        which would name it 20-60 s late and often wrongly, never runs."""
        feed.song_now(FIP_ID, "Ya bismillah", started_ago=10)
        await play(radio, FIP_RF)
        await radio.tick(12)                     # past the Shazam grace

        assert feed.reads == [FIP_ID]
        assert radio.track_title() is None
        assert radio.session()["title"] == "FIP"

        await radio.advance(ICECAST_LEAD_S - 10 - 1)
        assert radio.track_title() is None
        await radio.advance(1)
        assert radio.track_title() == "Ya bismillah"
        assert radio.session()["artwork"] == radiofrance.COVER_URL.format(cover="cover-Ya bismillah")
        assert radio.details()["artwork_pending"] is False

        await radio.tick(12)
        assert not radio.shazam_running()
        assert all(not shazam.is_running for shazam in radio.shazams)

    async def test_on_hls_the_song_is_named_sooner(self, radio, feed):
        """HLS joins closer to the live edge than icecast's burst: the same
        song is heard HLS_LEAD_S after its start, not ICECAST_LEAD_S — on
        icecast's lead every title on an .m3u8 favorite would trail its song
        by 13 s."""
        feed.song_now(FIPCULTES_ID, "Ya bismillah", started_ago=10)
        await play(radio, FIPCULTES_HLS)
        assert feed.reads == [FIPCULTES_ID]

        await radio.advance(HLS_LEAD_S - 10 - 1)
        assert radio.track_title() is None
        await radio.advance(1)
        assert radio.track_title() == "Ya bismillah"

    async def test_an_answer_before_the_first_sound_names_the_song_while_loading(self, radio, feed):
        """The first read leaves with the load and usually answers before
        mpv's first sound. The song it names as heard is the one that sound
        starts in (the lead counts from the connect), so the card names it
        while loading, and a stall keeps it, as it keeps the stream's own
        title."""
        feed.song_now(FIP_ID, "Naada", started_ago=ICECAST_LEAD_S + 5)
        await radio.select()
        await radio.tune(FIP_RF)
        assert radio.session()["phase"] == "loading"
        assert radio.track_title() == "Naada"
        assert radio.session()["title"] == "Naada"

        await radio.opens()
        assert radio.session()["phase"] == "playing"
        assert radio.track_title() == "Naada"

        await radio.stalls()
        assert radio.session()["phase"] == "loading"
        assert radio.track_title() == "Naada"

    async def test_the_global_shazam_toggle_does_not_gate_it(self, radio, feed):
        """Its label reads "…using Shazam": Radio France's titles, like the
        stream's own, are not Shazam's."""
        radio.shazam_enabled = False
        feed.song_now(FIP_ID, "Naada", started_ago=ICECAST_LEAD_S + 5)
        await play(radio, FIP_RF)
        assert radio.track_title() == "Naada"

    async def test_a_station_opted_out_asks_radio_france_nothing(self, radio, feed):
        """The per-station gate is "now playing for this station: off" — the
        stream's own titles honor it, so this feed does too."""
        radio.data.is_station_shazam_enabled = Mock(return_value=False)
        feed.song_now(FIP_ID, "Naada", started_ago=ICECAST_LEAD_S + 5)
        await play(radio, FIP_RF)
        await radio.tick(12)
        await radio.advance(120)

        assert feed.reads == []
        assert radio.track_title() is None
        assert not radio.shazam_running()


class TestTheShazamFallback:

    async def test_a_talk_station_falls_back_to_shazam_as_before(self, radio, feed):
        """France Inter answers "Le direct": nothing named, so the music it
        plays is still Shazam's to name, after the same grace."""
        await play(radio, INTER_RF)
        await radio.tick(4)
        assert feed.reads == [INTER_ID]
        assert not radio.shazam_running()

        await radio.tick(4)
        assert radio.shazam_running()

    async def test_a_feed_slow_to_answer_holds_the_fallback(self, radio, feed):
        """The grace also waits for the first answer: started meanwhile,
        Shazam would title the screen until the feed's song took over."""
        feed.song_now(FIP_ID, "Ya bismillah", started_ago=ICECAST_LEAD_S + 5)
        feed.held = asyncio.Event()
        await play(radio, FIP_RF)
        await radio.tick(12)
        assert not radio.shazam_running()

        feed.held.set()
        await settle()
        await radio.tick(12)
        assert radio.track_title() == "Ya bismillah"
        assert not radio.shazam_running()

    async def test_a_feed_that_fails_lets_shazam_start_and_a_song_it_names_stops_it(self, radio, feed):
        """Unreachable at the tune: Shazam starts after the grace, as on any
        station without a title. Once the feed names a song, Shazam stops, as
        it does for the first in-band title."""
        feed.failure = aiohttp.ServerDisconnectedError()
        await play(radio, FIP_RF)
        await radio.tick(8)
        assert radio.shazam_running()
        shazam = radio.shazams[-1]
        shazam.current_track = {"title": "Heard by Shazam", "artist": "Someone", "artwork": None}
        await shazam.on_track_changed(shazam.current_track)
        await settle()
        assert radio.track_title() == "Heard by Shazam"

        feed.failure = None
        feed.song_now(FIP_ID, "Naada", started_ago=ICECAST_LEAD_S + 5)
        await radio.advance(DEFAULT_REFRESH_S)
        assert not radio.shazam_running()
        assert radio.track_title() == "Naada"


class TestAnOutage:

    async def test_reported_once_after_a_minute_and_once_when_it_answers_again(self, radio, feed, caplog):
        """A poll that misses is not a dead feed: debug per miss, one warning
        once it has lasted 60 s, one info when it ends — never an error, which
        raises the banner on every screen. The title on screen stays: the
        schedule runs on what is known. Every read is 30 s after the last."""
        caplog.set_level(logging.DEBUG, logger="source.radio.radiofrance")

        def warnings() -> List[logging.LogRecord]:
            return [r for r in caplog.records if r.levelno == logging.WARNING]

        def answers_again() -> List[logging.LogRecord]:
            return [r for r in caplog.records if r.levelno == logging.INFO and "again" in r.getMessage()]

        feed.song_now(FIP_ID, "Ya bismillah", started_ago=ICECAST_LEAD_S + 5, lasts=1000)
        await play(radio, FIP_RF)
        assert radio.track_title() == "Ya bismillah"

        feed.failure = asyncio.TimeoutError()
        await radio.advance(30 + 59)             # failing since the read at 30 s
        assert warnings() == []
        await radio.advance(1)                   # the read at 90 s: 60 s unanswered
        assert len(warnings()) == 1
        assert "TimeoutError" in warnings()[0].getMessage()
        await radio.advance(60)
        assert len(warnings()) == 1
        assert radio.track_title() == "Ya bismillah"

        feed.failure = None
        await radio.advance(30 + 60)             # three reads answered
        assert len(answers_again()) == 1

        # One read lost after the outage ended is a miss, not the old outage
        # going on: no warning, and nothing to say when the next one answers.
        feed.failure = asyncio.TimeoutError()
        await radio.advance(30)
        feed.failure = None
        await radio.advance(30)
        assert len(warnings()) == 1
        assert len(answers_again()) == 1
        assert radio.track_title() == "Ya bismillah"
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert radio.errors() == []


class TestTheFeedDiesWithTheSession:

    async def test_a_station_change_stops_the_reads(self, radio, feed):
        """A read in flight when another station is tuned is dropped, and no
        read follows: the feed is the session's, like Shazam (E44)."""
        feed.song_now(FIP_ID, "Ya bismillah", started_ago=ICECAST_LEAD_S + 5)
        feed.held = asyncio.Event()
        await play(radio, FIP_RF)
        assert feed.reads == [FIP_ID]

        await radio.tune(NOVA)
        await radio.opens()
        feed.held.set()
        await settle()
        await radio.advance(300)

        assert feed.reads == [FIP_ID]
        assert radio.track_title() is None
        assert radio.session()["title"] == NOVA["name"]

    async def test_a_song_that_ends_with_nothing_after_it_clears(self, radio, feed):
        """Past its end and the hold, with the feed answering "Le direct",
        the card names the station again."""
        feed.song_now(FIP_ID, "Polite rudboi", started_ago=ICECAST_LEAD_S + 5, lasts=60)
        await play(radio, FIP_RF)
        assert radio.track_title() == "Polite rudboi"

        feed.answers[FIP_ID] = ANSWERS["inter_1_talk"]
        await radio.advance(60 + HOLD_S)
        assert radio.track_title() is None
        assert radio.session()["title"] == "FIP"



class TestCoverURLs:
    """A song's cover is served by Milō (`/api/radio/covers/{id}`): Radio
    France answers WebP only, which the iPhone's Lock Screen cannot draw."""

    COVER = "87dd1ba6-2f17-43ca-a9fb-5862cf4b9910"

    def test_a_cover_id_becomes_milos_route(self):
        assert radiofrance.cover_artwork(self.COVER) == f"/api/radio/covers/{self.COVER}"

    def test_the_route_fetches_radio_france_and_nothing_else(self):
        assert radiofrance.cover_source(self.COVER) == radiofrance.COVER_URL.format(cover=self.COVER)
        for not_an_id in ("", "..", "a/b", "a b", "-x", "x?y=1"):
            assert radiofrance.cover_source(not_an_id) is None

    def test_an_id_that_would_not_make_a_path_keeps_radio_frances_url(self):
        assert radiofrance.cover_artwork("a b") == radiofrance.COVER_URL.format(cover="a b")

    def test_the_id_is_read_back_from_the_route_only(self):
        assert radiofrance.cover_of(radiofrance.cover_artwork(self.COVER)) == self.COVER
        assert radiofrance.cover_of(radiofrance.COVER_URL.format(cover=self.COVER)) is None
        assert radiofrance.cover_of(None) is None
