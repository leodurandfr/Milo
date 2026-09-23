# backend/tests/test_radio_playback.py
"""`play_station` — the path every "play this station" takes — and what stops it.

`POST /api/radio/play` is pinned by `tests/contracts/milo_mac_contract.json`
(`MiloAPIService.playRadioStation`), so this is a surface with two consumers
besides the web UI. Also covers `on_shazam_setting_changed`, the live half of
`PUT /api/settings/radio-settings`.

Each scenario drives the outside world only (mpv simulated, the station store
and the directory faked, a real state machine) and reads what the clients read:
the command's answer, `source_state`/`metadata`, the `source/error` banners,
and what mpv was told to load.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.tests.golden.harness import settle
from backend.tests.golden.test_old_wire_radio import FIP, NOVA
from backend.tests.radio_world import RadioWorld

# A station from a search: not a favorite, carried in the request body.
STATION = {"id": "s1", "name": "Jazz Radio", "url": "http://stream.example/jazz.mp3"}


@pytest.fixture
def radio(monkeypatch):
    return RadioWorld(monkeypatch)


def play(station_id: str, station=None) -> dict:
    data = {"station_id": station_id}
    if station is not None:
        data["station"] = station
    return data


class TestResolutionChain:
    """Three rungs, in order: local favorite → the body the caller sent → the
    directory. The order is what keeps a favorite the user renamed (or gave a
    stream URL that works from this LAN) from being silently replaced by the
    directory's copy on every play."""

    async def test_a_favorite_is_read_from_local_data_and_the_directory_is_not_asked(
        self, radio
    ):
        local = {"id": "s1", "name": "Jazz (renamed)", "url": "http://local/jazz"}
        radio.favorites(local)
        await radio.select()

        result = await radio.command("play_station", play("s1", STATION))

        assert result["success"] is True
        assert radio.loads()[-1][1] == "http://local/jazz"
        assert radio.meta()["station_name"] == "Jazz (renamed)"
        radio.api.get_station_by_id.assert_not_awaited()

    async def test_the_body_the_caller_sent_is_used_before_the_directory(self, radio):
        await radio.select()

        result = await radio.command("play_station", play("s1", STATION))

        assert result["success"] is True
        assert radio.loads()[-1][1] == STATION["url"]
        radio.api.get_station_by_id.assert_not_awaited()

    async def test_a_favorite_with_no_local_record_falls_through_to_the_body(self, radio):
        """`is_favorite` and "we hold its record" are two different questions."""
        radio.data.is_favorite = Mock(return_value=True)
        radio.data.get_favorite_metadata_local = Mock(return_value=None)
        await radio.select()

        result = await radio.command("play_station", play("s1", STATION))

        assert result["success"] is True
        assert radio.loads()[-1][1] == STATION["url"]

    async def test_a_bare_id_is_resolved_through_the_directory(self, radio):
        radio.api.get_station_by_id = AsyncMock(return_value=STATION)
        await radio.select()

        result = await radio.command("play_station", play("s1"))

        assert result["success"] is True
        radio.api.get_station_by_id.assert_awaited_once_with("s1")
        assert radio.loads()[-1][1] == STATION["url"]

    async def test_a_station_no_rung_resolves_is_refused_before_mpv_is_touched(
        self, radio
    ):
        """Handing mpv a `None` URL is a load that can only fail later; refusing
        here names the failure straight away, and leaves no spinner up."""
        await radio.select()

        result = await radio.command("play_station", play("ghost"))

        assert result["success"] is False
        assert "ghost" in result["error"]
        assert radio.loads() == []
        assert radio.state()["source_state"] == "ready"
        assert radio.meta()["is_buffering"] is False

    async def test_a_directory_that_fails_is_reported_and_leaves_no_spinner(self, radio):
        """The one arm that catches the unexpected: the directory lookup
        raising. The answer names the failure, the banner says playback
        failed, and nothing is left loading."""
        radio.api.get_station_by_id = AsyncMock(side_effect=RuntimeError("directory down"))
        await radio.select()

        result = await radio.command("play_station", play("s1"))

        assert result["success"] is False
        assert "directory down" in result["error"]
        assert radio.errors() == ["playback_failed"]
        assert radio.loads() == []
        assert radio.meta()["is_buffering"] is False

    async def test_the_click_counter_does_not_stand_between_the_tap_and_the_sound(
        self, radio
    ):
        """radio-browser's ranking counter is best effort: awaited inline, a
        slow directory would hold the load of every station behind it."""
        counted = asyncio.Event()
        held = asyncio.Event()

        async def slow_count(station_id):
            counted.set()
            await held.wait()

        radio.api.increment_station_clicks = AsyncMock(side_effect=slow_count)
        await radio.select()

        tapped = asyncio.create_task(radio.source.command("play_station", play("s1", STATION)))
        await settle()

        assert counted.is_set()
        assert tapped.done() and tapped.result()["success"] is True
        assert radio.loads()[-1][1] == STATION["url"]
        held.set()
        await settle()


class TestSwitchingStations:
    """What the station being left must not leak onto the one being tuned."""

    async def test_the_previous_stations_recognition_loop_is_stopped(self, radio):
        """Shazam holds the *previous* stream URL and its own timer. Left
        running, it keeps pushing the old station's titles onto the new one."""
        await radio.select()
        await radio.tune(NOVA)                 # no in-band titles
        await radio.tick(8)                    # past the in-band grace
        assert radio.shazam_running()

        await radio.tune(FIP)

        assert not radio.shazam_running()

    async def test_the_new_stream_replaces_the_old_one_in_mpv(self, radio):
        """One stream at a time: the load replaces the playing entry rather
        than queueing beside it (two overlapping streams on a slow switch),
        and the old entry's end does not end the new session."""
        await radio.select()
        await radio.tune(NOVA)

        await radio.tune(FIP)

        assert radio.loads()[-1][2] == "replace"
        assert [entry.url for entry in radio.mpv.playlist] == [FIP["url"]]
        assert radio.state()["source_state"] == "active"
        assert radio.meta()["station_id"] == "fip"
        assert radio.meta()["is_playing"] is True
        assert radio.errors() == []

    async def test_the_in_band_state_of_the_previous_station_is_not_inherited(self, radio):
        """A station that named its tracks in-band suppresses the Shazam
        fallback; carried over, that suppression leaves the next station — one
        that carries no titles — with no title source at all, and its old
        title on screen."""
        radio.stream_title("Gil Evans - Snibor")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)
        assert radio.meta()["track_title"] == "Snibor"

        radio.stream_title(None)
        await radio.tune(NOVA)
        assert "track_title" not in radio.meta()
        await radio.tick(8)

        assert radio.shazam_running()

    async def test_the_spinner_is_up_the_moment_the_station_is_tapped(self, monkeypatch):
        """A station can take seconds to open; the card must show it is
        loading from the tap, not only once the sound starts."""
        radio = RadioWorld(monkeypatch, clock=True)
        await radio.select()

        await radio.tune(FIP)

        assert radio.state()["source_state"] == "active"
        assert radio.meta()["station_id"] == "fip"
        assert (radio.meta()["is_playing"], radio.meta()["is_buffering"]) == (False, True)


class TestStreamRefusedByMpv:
    """mpv answering no entry means it refused the load outright."""

    async def test_the_failure_is_named_and_the_station_is_kept(self, radio):
        """The answer and the banner name it at once; the station stays on
        the card, since a Retry is what the banner offers."""
        await radio.select()
        radio.mpv.accept = False

        result = await radio.command("play_station", play("s1", STATION))

        assert result["success"] is False
        assert result["error"] == "Unable to load stream: Jazz Radio"
        assert radio.errors() == ["stream_load_failed"]
        assert radio.state()["source_state"] == "ready"
        assert radio.meta()["station_id"] == "s1"
        assert radio.meta()["is_buffering"] is False


class TestNowPlayingGates:
    """Which of the two title feeds a station may use.

    The per-station opt-out gates *both* in-band and Shazam (it means "show no
    track at all"); the Shazam fallback additionally needs the global toggle.
    Confusing the two shows a title on a station the user muted, or leaves the
    fallback dead on every station.
    """

    async def test_the_per_station_opt_out_hides_every_title(self, radio):
        radio.data.is_station_shazam_enabled = Mock(return_value=False)
        radio.stream_title("Some Artist - Some Song")
        await radio.select()

        await radio.tune(FIP)
        await radio.tick(16)

        radio.data.is_station_shazam_enabled.assert_called_with("fip")
        assert "track_title" not in radio.meta()
        assert not radio.shazam_running()

    async def test_the_fallback_starts_after_the_grace_when_both_gates_are_open(
        self, radio
    ):
        """Both sides of the edge: started at once, Shazam records every
        station that names its tracks in-band a few seconds later anyway;
        never started, stations with no in-band titles show none."""
        await radio.select()
        await radio.tune(NOVA)

        await radio.tick(7)
        assert not radio.shazam_running()
        await radio.tick(1)
        assert radio.shazam_running()

    async def test_the_global_toggle_alone_disarms_the_fallback_not_in_band(self, radio):
        """In-band needs neither toggle: a station naming its tracks keeps
        showing them with recognition off."""
        radio.shazam_enabled = False
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(16)
        assert not radio.shazam_running()

        radio.stream_title("Miles Davis - So What")
        await radio.tune(FIP)
        await radio.tick(4)
        assert radio.meta()["track_title"] == "So What"


class TestStopPlayback:
    """`stop` must leave nothing running and still know what to re-tune."""

    async def test_stop_shuts_the_recognition_loop_down(self, radio):
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(8)
        assert radio.shazam_running()

        result = await radio.command("stop")

        assert result["success"] is True
        assert not radio.shazam_running()
        assert radio.state()["source_state"] == "ready"
        assert ("stop",) in radio.mpv.sent

    async def test_stop_keeps_the_station_so_resume_can_retune_it(self, radio):
        """A live stream has no unpause: the play button after a stop
        re-tunes, and it has to know which station."""
        await radio.select()
        await radio.tune(FIP)
        await radio.command("stop")
        assert radio.meta()["station_id"] == "fip"

        result = await radio.command("resume_playback")

        assert result["success"] is True
        assert radio.loads()[-1][1] == FIP["url"]
        assert radio.state()["source_state"] == "active"

    async def test_stop_with_mpv_unreachable_still_ends_the_session(self, radio):
        """`stop` also comes from the idle timeout and the lock screen; mpv not
        answering must not leave the card claiming a station plays."""
        await radio.select()
        await radio.tune(FIP)
        radio.mpv.accept = False

        result = await radio.command("stop")

        assert result["success"] is True
        assert radio.state()["source_state"] == "ready"
        assert radio.meta()["is_playing"] is False


class TestResumeDispatch:
    """`resume_playback` re-tunes the station on screen."""

    async def test_resume_with_nothing_ever_tuned_is_refused(self, radio):
        await radio.select()

        result = await radio.command("resume_playback")

        assert result["success"] is False
        assert radio.loads() == []

    async def test_resume_of_a_station_without_an_id_is_refused(self, radio):
        """A station body with no id (a custom stream played once) cannot be
        looked up again; resuming it is refused rather than loading nothing."""
        await radio.select()
        await radio.command("play_station", play("anon", {"name": "No ID", "url": "http://x/anon"}))
        await radio.command("stop")

        result = await radio.command("resume_playback")

        assert result["success"] is False
        assert len(radio.loads()) == 1


class TestShazamSettingChanged:
    """`PUT /api/settings/radio-settings` → `on_shazam_setting_changed`.

    The setting is persisted by the route either way; this method is the only
    thing that acts on it *now*. Losing it means the toggle takes effect only
    at the next station change.
    """

    async def toggle(self, radio, enabled: bool) -> bool:
        radio.shazam_enabled = enabled
        answer = await radio.source.on_shazam_setting_changed(enabled)
        await settle()
        return answer

    async def test_turning_it_off_stops_a_running_loop_for_good(self, radio):
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(8)
        assert radio.shazam_running()

        assert await self.toggle(radio, False) is True
        assert not radio.shazam_running()

        await radio.tick(16)
        assert not radio.shazam_running()

    async def test_turning_it_on_restarts_the_grace_for_the_playing_station(self, radio):
        """The grace restarts from the toggle, not from where the station's
        empty polls had got to — otherwise Shazam fires the instant it is
        re-enabled, over a station that may be about to name its track."""
        radio.shazam_enabled = False
        await radio.select()
        await radio.tune(NOVA)
        await radio.tick(12)

        assert await self.toggle(radio, True) is True
        await radio.tick(4)
        assert not radio.shazam_running()
        await radio.tick(4)
        assert radio.shazam_running()

    async def test_turning_it_on_does_not_rearm_when_in_band_already_won(self, radio):
        """In-band stays primary. Re-arming here starts a second title feed on
        a station that is already naming its own tracks."""
        radio.shazam_enabled = False
        radio.stream_title("Miles Davis - So What")
        await radio.select()
        await radio.tune(FIP)
        await radio.tick(4)

        await self.toggle(radio, True)
        radio.stream_title(None)
        await radio.tick(16)

        assert not radio.shazam_running()

    async def test_turning_it_on_respects_the_per_station_opt_out(self, radio):
        radio.shazam_enabled = False
        radio.data.is_station_shazam_enabled = Mock(return_value=False)
        await radio.select()
        await radio.tune(NOVA)

        await self.toggle(radio, True)
        await radio.tick(16)

        assert not radio.shazam_running()

    async def test_turning_it_on_with_nothing_playing_arms_nothing(self, radio):
        await radio.select()

        assert await self.toggle(radio, True) is True
        await radio.tick(16)

        assert not radio.shazam_running()

    async def test_the_toggle_succeeds_while_the_source_is_stopped(self, radio):
        """Settings are editable with radio off, when no recognition service
        exists. Reporting failure there paints the red banner on a settings
        save that worked."""
        assert await self.toggle(radio, False) is True
