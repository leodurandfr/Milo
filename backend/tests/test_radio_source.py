# backend/tests/test_radio_source.py
"""RadioSource: its lifecycle, what it publishes, its knob, and its title feeds.

Driven through the outside world only (tests/radio_world.py: mpv simulated,
the station store, the directory and the recognition service faked, a real
AudioStateMachine) and read where the clients read it: the state's `session`,
`resume` and `details`, `source/error` banners, command answers, and
what mpv was told to load. Also the station store's own persistence rules and
the two pure helpers (the in-band title parser, the pre-roll probe).
"""
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.core import audio_source
from backend.shared import mpv_audio_source
from backend.sources.radio import source as radio_module
from backend.sources.radio.data import StationDataService
from backend.sources.radio.source import RadioSource
from backend.tests.golden.harness import (
    AsyncioProxy, instant_short_sleep, make_settings, make_systemd, settle,
)
from backend.tests.golden.test_wire_radio import FIP, NOVA, FakeShazam
from backend.tests.mpv_sim import MpvSim
from backend.tests.radio_world import RadioWorld

UNIT_FILE = Path(__file__).resolve().parents[2] / "system" / "milo-radio.service"


@pytest.fixture
def radio(monkeypatch):
    return RadioWorld(monkeypatch)


class TestMpvSocket:
    """The source reaches mpv on the socket the unit opens."""

    async def test_the_default_socket_is_the_one_milo_radio_service_opens(self, monkeypatch):
        """mpv is started by milo-radio.service with its own
        `--input-ipc-server`; a source dialing another path never attaches and
        every station fails to start (the radio card shows "Failed to start")."""
        match = re.search(r"--input-ipc-server=(\S+)", UNIT_FILE.read_text())
        assert match, "milo-radio.service no longer declares its IPC socket"
        dialed = []
        monkeypatch.setattr(
            mpv_audio_source, "MpvController",
            lambda **kw: dialed.append(kw["ipc_socket_path"]) or MpvSim(),
        )
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(radio_module, "ShazamRecognitionService", FakeShazam)
        # The station file is the operator's, under /var/lib/milo: not read here.
        monkeypatch.setattr(StationDataService, "initialize", AsyncMock())
        source = RadioSource(settings_service=make_settings(), systemd_manager=make_systemd())

        assert await source.start() is True
        await source.stop()
        await source.shutdown()

        assert dialed == [match.group(1)]


class TestRadioSourceLifecycle:
    """Selecting and leaving the radio, as the state machine does it."""

    async def test_selecting_starts_the_unit_and_attaches_to_mpv(self, radio):
        """No session with nothing tuned: the card offers stations, no spinner."""
        await radio.select()

        state = radio.state()
        assert state["service"] == "running"
        assert (state["session"], state["resume"], state["details"]) == (None, None, None)
        radio.systemd.start.assert_awaited_once_with("milo-radio.service")
        assert radio.mpv.is_connected

    async def test_an_mpv_that_never_answers_fails_the_start(self, radio):
        """A start that cannot reach mpv is reported as a failed start (the
        card's Retry), and the unit it started is not left running."""
        radio.mpv.connect = AsyncMock(return_value=False)

        await radio.select()

        assert radio.state()["service"] == "failed"
        radio.systemd.stop.assert_awaited_once_with("milo-radio.service")

    async def test_leaving_stops_the_unit_and_keeps_the_station(self, radio):
        """The unit goes (it holds the ALSA device the next source needs), the
        IPC link closes, and coming back shows the station left, to re-tune."""
        await radio.select()
        await radio.tune(FIP)

        await radio.leave()
        assert radio.state()["source"] == "none"
        radio.systemd.stop.assert_awaited_once_with("milo-radio.service")
        assert not radio.mpv.is_connected

        await radio.select()
        assert not radio.active()
        assert radio.station() == "fip"

    async def test_a_tuned_station_is_active(self, radio):
        await radio.select()

        result = await radio.command("play_station", {"station_id": "fip"})

        assert result["success"] is True
        assert radio.active()
        assert radio.loads()[-1][1] == FIP["url"]


class TestStationDataService:
    """Test StationDataService."""

    @pytest.mark.asyncio
    async def test_initial_state(self):
        """Test initial state of StationDataService."""
        service = StationDataService()

        assert service._favorites == []
        assert service._manual_stations == {}

    @pytest.mark.asyncio
    async def test_is_favorite(self):
        """Test is_favorite method."""
        service = StationDataService()
        service._favorites = ["station-1", "station-2"]

        assert service.is_favorite("station-1") is True
        assert service.is_favorite("station-3") is False

    @pytest.mark.asyncio
    async def test_favorite_ids_are_ordered_by_display_name(self, tmp_path):
        """The stored order is the order stations were added; the order every
        consumer shows — the favorites grid and RadioSource's next/prev — is by
        name, folded for case and accents. It is produced here, once: the grid
        used to re-sort client-side, which is how a physical Next could land on
        a station that was not the one shown next to the current one."""
        service = StationDataService()
        service._data_file = tmp_path / "radio_data.json"
        await service.initialize()

        for station_id, name in [
            ("s1", "Zeta"), ("s2", "alpha"), ("s3", "Étoile"), ("s4", "Beta"),
        ]:
            await service.add_favorite(station_id, {"name": name})

        ordered = [
            (await service.get_station_metadata(sid))["name"]
            for sid in service.favorite_ids
        ]

        assert ordered == ["alpha", "Beta", "Étoile", "Zeta"]

    @pytest.mark.asyncio
    async def test_favorites_with_metadata_follow_the_same_order(self, tmp_path):
        """`GET /api/radio/stations?favorites_only=true` is served from here, so
        this is the list the grid renders. It must be the list next/prev steps,
        not a second one that happens to hold the same stations."""
        service = StationDataService()
        service._data_file = tmp_path / "radio_data.json"
        await service.initialize()

        for station_id, name in [("s1", "Zeta"), ("s2", "Alpha")]:
            await service.add_favorite(station_id, {"name": name})

        stations = await service.get_favorites_with_metadata()

        assert [s["id"] for s in stations] == service.favorite_ids

    @pytest.mark.asyncio
    async def test_enrich_with_favorite_status(self):
        """Test enrich_with_favorite_status method."""
        service = StationDataService()
        service._favorites = ["fav-1"]

        stations = [
            {"id": "fav-1", "name": "Favorite"},
            {"id": "other-1", "name": "Other"}
        ]

        enriched = service.enrich_with_favorite_status(stations)

        assert enriched[0]["is_favorite"] is True
        assert enriched[1]["is_favorite"] is False


class TestStationDataPersistence:
    """WI-6: fail-loud loading — never a silent wipe of favorites."""

    def _service(self, tmp_path):
        from pathlib import Path
        service = StationDataService()
        service._data_file = Path(tmp_path) / "radio_data.json"
        return service

    @pytest.mark.asyncio
    async def test_fresh_install_seeds_versioned_defaults(self, tmp_path):
        """Missing file → defaults written with schema_version stamped."""
        import json
        service = self._service(tmp_path)

        await service.initialize()

        assert service._favorites == []
        assert service._manual_stations == {}
        on_disk = json.loads(service._data_file.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == StationDataService.SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_valid_file_loads_favorites(self, tmp_path):
        """A well-formed versioned file loads its favorites intact."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION,
            "favorites": ["s1", "s2"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        await service.initialize()

        assert service._favorites == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_schema_mismatch_fails_loud(self, tmp_path):
        """A file with the wrong schema_version raises instead of wiping."""
        import json
        from backend.shared.persistence import SchemaVersionMismatch
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION + 1,
            "favorites": ["keep-me"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_missing_schema_version_fails_loud(self, tmp_path):
        """A pre-versioning file (no schema_version) fails loud, never wiped."""
        import json
        from backend.shared.persistence import SchemaVersionMismatch
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "favorites": ["keep-me"],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }), encoding="utf-8")

        with pytest.raises(SchemaVersionMismatch):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_corrupt_json_fails_loud(self, tmp_path):
        """Invalid JSON raises rather than silently returning empty favorites."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text("{ this is not valid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_missing_required_key_fails_loud(self, tmp_path):
        """A versioned file missing a required top-level key raises."""
        import json
        service = self._service(tmp_path)
        service._data_file.write_text(json.dumps({
            "schema_version": StationDataService.SCHEMA_VERSION,
            "favorites": ["keep-me"],
        }), encoding="utf-8")

        with pytest.raises(RuntimeError, match="missing required keys"):
            await service.initialize()



class TestTheCommonFloor:
    """Radio fills the session's title/artist/album/artwork like every other source.

    It used to fill none of them: the track travelled in `track_title` and
    `track_artist` beside an empty floor, and every generic consumer re-derived
    which to show — the push layer in `core/push/payloads.py`, Milo-iOS in its
    own copy of the same cascade. The rule that decides what a one-line
    consumer shows lives in the source, the only place that knows a recognised
    track annotates a stream rather than replacing it.
    """

    async def test_a_recognised_track_is_the_title_and_the_station_the_album(self, radio):
        """The one-line view of a two-layer source: what is playing, on what."""
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)

        session, details = radio.session(), radio.details()
        assert (session["title"], session["artist"]) == ("Snibor", "Gil Evans")
        assert session["album"] == details["station"]["name"]
        # Both layers stay on the wire: the UI draws them apart.
        assert (details["track"]["title"], details["track"]["artist"]) == ("Snibor", "Gil Evans")

    async def test_without_a_track_the_station_is_the_title(self, radio):
        """A stream with no in-band metadata and no Shazam match is still
        something to show, and the station is what it is."""
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)

        session, details = radio.session(), radio.details()
        assert session["title"] == details["station"]["name"]
        assert session["artist"] is None
        assert details["track"] is None

    async def test_a_track_shazam_recognised_is_the_title(self, radio):
        """The fallback layer reaches the floor the same way in-band does."""
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(8)
        shazam = radio.shazams[-1]

        shazam.current_track = {"title": "Take Five", "artist": "Dave Brubeck", "artwork": None}
        await shazam.on_track_changed(shazam.current_track)
        await settle()

        session = radio.session()
        assert (session["title"], session["artist"]) == ("Take Five", "Dave Brubeck")

    async def test_the_cover_falls_back_to_the_station_logo_through_the_proxy(self, radio):
        """The session's `artwork` is the floor, so it has to be fetchable as-is. A
        station logo is often an external URL behind a WAF that refuses a bare
        User-Agent, which is what /api/radio/favicon exists for — a client
        should not have to know that rule to draw a cover."""
        await radio.select()
        await radio.tune(FIP)
        assert radio.session()["artwork"].startswith("/api/radio/favicon?url=https%3A")

        # A logo this unit already serves is handed over untouched.
        await radio.tune(NOVA)
        assert radio.session()["artwork"] == radio.details()["station"]["favicon"]

    async def test_an_in_band_track_gets_its_cover_resolved(self, radio):
        """In-band carries no artwork; the cover looked up from artist and
        title replaces the station logo once it arrives."""
        radio.artwork.resolve = AsyncMock(return_value="https://art.example/snibor.jpg")
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)

        radio.artwork.resolve.assert_awaited_with("Gil Evans", "Snibor")
        assert radio.session()["artwork"] == "https://art.example/snibor.jpg"
        assert radio.details()["track"]["artwork"] == "https://art.example/snibor.jpg"

    async def test_a_stopped_station_keeps_the_floor_and_drops_the_track(self, radio):
        """What a stop publishes: the station a play press would re-tune, and
        no track. The recognised track annotates a stream that is running —
        holding it would claim a stopped radio is still on that song."""
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)

        await radio.command("stop")

        state = radio.state()
        assert state["session"] is None
        assert radio.station() == "fip"
        assert state["resume"]["title"] == state["details"]["station"]["name"]
        assert state["details"]["track"] is None


class TestPlaybackMetadata:
    """The station the radio card and Milo-Mac read (`details.station`)."""

    async def test_a_tuned_station_publishes_its_card(self, radio):
        await radio.select()
        await radio.tune(FIP)

        station = radio.details()["station"]
        assert station["id"] == "fip"
        assert station["name"] == FIP["name"]
        assert station["url"] == FIP["url"]
        assert (station["country"], station["genre"]) == (FIP["country"], FIP["genre"])
        assert radio.phase() == "playing"

class TestInbandTrackParsing:
    """Test _parse_inband_track (WI-1)."""

    def test_empty_metadata(self):
        from backend.sources.radio.source import _parse_inband_track
        assert _parse_inband_track({}) is None
        assert _parse_inband_track({"icy-name": "Some Station"}) is None
        assert _parse_inband_track({"icy-title": "   "}) is None

    def test_artist_title_split(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Jay-Z - Empire State of Mind"})
        assert track == {
            "title": "Empire State of Mind",
            "artist": "Jay-Z",
            "artwork": None,
        }

    def test_title_only_when_no_separator(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"streamtitle": "Morning News"})
        assert track == {"title": "Morning News", "artist": "", "artwork": None}

    def test_strips_station_promo_suffix(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Bill Evans - Waltz for Debby - WALM Radio on walmradio.com"}
        )
        assert track["artist"] == "Bill Evans"
        assert track["title"] == "Waltz for Debby"

    def test_icy_title_preferred_over_name(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Artist - Song", "icy-name": "Station"}
        )
        assert track["title"] == "Song"

    def test_title_by_artist_split(self):
        # walmradio format: "Title by Artist" (title first, "by" separator).
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track(
            {"icy-title": "Grant's Tune by Grant Green - Adroit Jazz on walmradio.com"}
        )
        assert track["title"] == "Grant's Tune"
        assert track["artist"] == "Grant Green"

    def test_strips_trailing_vinyl_marker(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "So What (Vinyl) by Miles Davis"})
        assert track["title"] == "So What"
        assert track["artist"] == "Miles Davis"

    def test_dash_takes_precedence_over_by(self):
        # A legit "Artist - Title" whose title contains "by" must split on " - ".
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Metallica - Killed by Death"})
        assert track["artist"] == "Metallica"
        assert track["title"] == "Killed by Death"

    def test_meaningful_parenthetical_kept_in_title(self):
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Bill Evans - Waltz (with Trio)"})
        assert track["title"] == "Waltz (with Trio)"

    def test_by_not_split_when_both_sides_single_word(self):
        # " by " is ambiguous with titles that literally contain it. With a
        # single word on each side ("Stand by Me"), keep the title whole rather
        # than invent a wrong artist (a wrong artist is worse than none).
        from backend.sources.radio.source import _parse_inband_track
        track = _parse_inband_track({"icy-title": "Stand by Me"})
        assert track["title"] == "Stand by Me"
        assert track["artist"] == ""



class TestInbandShazamArbitration:
    """In-band metadata is the primary title feed, Shazam the fallback.

    In-band (ICY StreamTitle) is instant and exact when present; Shazam costs a
    recording every 20 s and is only for stations that name nothing. Read
    every fourth second of sound.
    """

    async def test_in_band_overrides_and_stops_shazam(self, radio):
        """A station that starts naming its tracks after the fallback began
        must not run two title feeds side by side."""
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(8)
        assert radio.shazam_running()

        radio.stream_title("Miles Davis - So What")
        await radio.tick(4)

        assert radio.track_title() == "So What"
        assert not radio.shazam_running()

    async def test_recognition_disabled_suppresses_in_band_before_reading_it(self, radio):
        """The per-station opt-out must hide the title even for an in-band
        station, and costs no read of mpv's metadata."""
        radio.data.is_station_shazam_enabled = Mock(return_value=False)
        radio.stream_title("Some Artist - Some Song")
        reads = []
        real_read = radio.mpv.get_metadata

        async def counted_read():
            reads.append(1)
            return await real_read()

        radio.mpv.get_metadata = counted_read
        await radio.select()
        await radio.tune(FIP)

        await radio.tick(16)

        assert radio.track_title() is None
        assert reads == []
        radio.artwork.resolve.assert_not_awaited()

    async def test_the_fallback_starts_once_and_only_after_the_grace(self, radio):
        """Two empty reads, then Shazam — once: its candidacy is consumed, so
        a fallback that stops is not restarted on every read after it."""
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(4)
        assert radio.shazams[-1].is_running is False

        await radio.tick(4)
        assert radio.shazam_running()
        radio.shazams[-1].is_running = False       # the loop gave up on its own

        await radio.tick(16)
        assert not radio.shazam_running()

    async def test_no_shazam_when_the_global_toggle_is_off(self, radio):
        radio.shazam_enabled = False
        await radio.select()
        await radio.tune(NOVA)

        await radio.tick(16)

        assert not radio.shazam_running()

    async def test_in_band_stale_title_clears_after_sustained_silence(self, radio):
        """A brief gap between tracks keeps the last in-band title; sustained
        empty metadata (ad, talk, dead air) clears it, so no phantom title
        stays pinned on the card and the lock screen."""
        radio.stream_title("Miles Davis - So What")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)
        assert radio.track_title() == "So What"

        radio.stream_title(None)
        await radio.tick(4 * 3)
        assert radio.track_title() == "So What"

        await radio.tick(4)
        assert radio.track_title() is None
        # Still an in-band station: silence does not hand over to Shazam.
        assert not radio.shazam_running()

class TestPrerollProbe:
    """The ffprobe pass that reads a station's pre-roll ad out of ICY tags."""

    @pytest.mark.asyncio
    async def test_a_preroll_is_read_off_the_stream(self, radio_source):
        """The non-triviality check: a probe that parsed nothing would satisfy
        the timeout test below on an empty surface. Infomaniak injects the ad
        duration as ICY tags on the connection, and the skip is that plus the
        two seconds of slack the source adds."""
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(json.dumps({
            "format": {"tags": {
                "insertionType": "preroll", "durationMilliseconds": "15000",
            }}
        }).encode(), b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            assert await radio_source._detect_preroll("http://stream.example/live") == 17

    @pytest.mark.asyncio
    async def test_a_probe_that_hangs_is_killed(self, radio_source):
        """ffprobe holds an open HTTP connection to the station for as long as
        it runs. Timed out and left alone it goes on pulling the stream for the
        rest of the session — one leaked reader per station that stalls, on a
        box whose whole job is the audio path."""
        process = Mock(returncode=None)
        process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        process.kill = Mock()
        process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            assert await radio_source._detect_preroll("http://stream.example/live") == 0

        process.kill.assert_called_once()
        process.wait.assert_awaited_once()



class TestTransportOnAnIdleSource:
    """A transport command sent while nothing plays must not serve a crash.

    `command()` catches everything `_handle_command` raises and hands the text
    to `run_source_command`, which serves it as HTTP 400 — so a crash arrives
    at the client wearing a client error's clothes, and Milo-iOS could only
    tell it from a real refusal by matching "NoneType" in the detail string.
    """

    async def test_stop_before_the_source_ever_started_reports_success(self, radio):
        """No mpv exists before the first start and after a stop — the exact
        state a lock screen stops from. Stop is idempotent: not playing is the
        end state asked for, so this is a success, not an invented failure."""
        result = await radio.command("stop")

        assert result["success"] is True
        assert "NoneType" not in str(result)
        assert radio.mpv.sent == []

    async def test_stop_with_nothing_tuned_reports_success(self, radio):
        await radio.select()

        result = await radio.command("stop")

        assert result["success"] is True
        assert not radio.active()


A = {"id": "a", "name": "A", "url": "http://stream.example/a"}
B = {"id": "b", "name": "B", "url": "http://stream.example/b"}
C = {"id": "c", "name": "C", "url": "http://stream.example/c"}
SEARCHED = {"id": "not-a-favorite", "name": "Found", "url": "http://stream.example/found"}


class TestFavoriteStepping:
    """next/prev walk the favorites list — a live stream has no track to skip.

    The senders are the rotary multi-click, the IR remote and the Milo-iOS lock
    screen, all of which reach `command()` with no argument: the station the
    press lands on is chosen by the source and nowhere else. Each case reads
    the station that ended up tuned and what mpv was told to load.
    """

    @pytest.fixture
    async def tuned(self, radio):
        """Radio with three favorites, playing the middle one."""
        radio.favorites(A, B, C)
        await radio.select()
        await radio.tune(B)
        return radio

    async def test_next_takes_the_following_favorite(self, tuned):
        assert (await tuned.command("next"))["success"] is True
        assert tuned.station() == "c"
        assert tuned.loads()[-1][1] == C["url"]

    async def test_prev_takes_the_preceding_favorite(self, tuned):
        await tuned.command("prev")
        assert tuned.station() == "a"

    async def test_the_list_wraps_in_both_directions(self, tuned):
        await tuned.tune(C)
        await tuned.command("next")
        assert tuned.station() == "a"

        await tuned.command("prev")
        assert tuned.station() == "c"

    async def test_a_searched_station_enters_the_list_at_an_end(self, tuned):
        """A station played from search has no place in the list, so there is no
        neighbor to step to — next enters at the first favorite, prev at the
        last, rather than refusing the press."""
        await tuned.command("play_station", {"station_id": SEARCHED["id"], "station": SEARCHED})
        await tuned.command("next")
        assert tuned.station() == "a"

        await tuned.command("play_station", {"station_id": SEARCHED["id"], "station": SEARCHED})
        await tuned.command("prev")
        assert tuned.station() == "c"

    async def test_a_stopped_source_steps_from_the_station_it_stopped_on(self, tuned):
        """The press has to know where the walk left off, or every press after
        a stop would restart at one end of the list."""
        await tuned.command("stop")
        assert not tuned.active()

        await tuned.command("next")

        assert tuned.station() == "c"
        assert tuned.active()

    async def test_the_only_favorite_is_not_re_tuned(self, tuned):
        """Re-tuning the station already playing costs a re-buffer and buys
        nothing — the press is answered, no stream is loaded."""
        tuned.favorites(B)
        loads = len(tuned.loads())

        result = await tuned.command("next")

        assert result["success"] is True
        assert len(tuned.loads()) == loads
        assert tuned.station() == "b"

    async def test_a_second_stop_does_not_forget_where_the_walk_was(self, tuned):
        """`stop` is idempotent, so it can arrive twice — the idle timeout next
        to a user press, or the iOS lock screen. The second one must not blank
        the station kept, or the next press restarts at the head of the list
        (and `resume_playback` answers "No station to resume")."""
        await tuned.command("stop")
        await tuned.command("stop")

        await tuned.command("next")

        assert tuned.station() == "c"

    async def test_two_presses_move_two_stations(self, tuned):
        """Two presses that overlap must not collapse into one step.

        The IR remote dispatches on every key event, the lock screen on every
        tap, and nothing upstream serializes them. The first press is held
        where it has let go of the station it left and not yet tuned the next
        one (the recognition service stopping with the old session): a second
        press read there would compute the same target.
        """
        release = asyncio.Event()
        shazam = tuned.shazams[-1]
        real_stop = shazam.stop

        async def slow_stop():
            await release.wait()
            await real_stop()

        shazam.stop = slow_stop
        presses = asyncio.gather(
            tuned.source.command("next", None), tuned.source.command("next", None),
        )
        await settle()
        release.set()
        await presses
        await settle()

        # b → c → a (the list wraps), not b → c twice.
        assert tuned.station() == "a"
        assert [load[1] for load in tuned.loads()] == [B["url"], C["url"], A["url"]]

    async def test_no_favorites_refuses(self, tuned):
        tuned.favorites()
        loads = len(tuned.loads())

        result = await tuned.command("next")

        assert result["success"] is False
        assert len(tuned.loads()) == loads


@pytest.fixture
def radio_source():
    """A bare source, for the pre-roll probe (no lifecycle involved)."""
    return RadioSource({"mpv_socket": "/nonexistent/radio.sock"})
