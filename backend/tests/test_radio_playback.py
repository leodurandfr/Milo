# backend/tests/test_radio_playback.py
"""`RadioSource._handle_play_station` — the path every "play this station" takes.

Only the third rung of its resolution chain had ever run (`test_play_station_command`
in `test_radio_source.py` plays a station that is not a favourite and carries no
body, so it falls straight through to the API). The two rungs above it, the
not-found refusal, the teardown of the *previous* station, and the arm that
reports a stream that will not load were all at 0 %.

`POST /api/radio/play` is pinned by `tests/contracts/milo_mac_contract.json`
(`MiloAPIService.playRadioStation`), so this is a surface with two consumers.
Also covers `on_shazam_setting_changed`, the live half of
`PUT /api/settings/radio-settings`, which no test entered.
"""
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.audio_state import SourceState
from backend.sources.radio.source import RadioSource
from backend.tests.conftest import drain_background_tasks

STATION = {"id": "s1", "name": "FIP", "url": "http://stream/fip"}


@pytest.fixture
def state_machine():
    machine = Mock()
    machine.broadcast = AsyncMock()
    machine.update_source_state = AsyncMock()
    machine.system_state = Mock()
    return machine


@pytest.fixture
def source(state_machine):
    src = RadioSource({"mpv_socket": "/tmp/test-radio-ipc.sock"},
                      state_machine=state_machine)
    src._mpv = Mock()
    src._mpv.load_stream = AsyncMock(return_value=True)
    src._mpv.stop = AsyncMock()
    src._station_data = Mock()
    src._station_data.is_favorite = Mock(return_value=False)
    src._station_data.get_favorite_metadata_local = Mock(return_value=None)
    src._station_data.is_station_shazam_enabled = Mock(return_value=True)
    src._radio_api = Mock()
    src._radio_api.get_station_by_id = AsyncMock(return_value=None)
    src._radio_api.increment_station_clicks = AsyncMock()
    src._shazam = Mock()
    src._shazam.stop = AsyncMock()
    src._shazam.start = AsyncMock()
    src._shazam.is_enabled = AsyncMock(return_value=True)
    src._shazam.is_running = False
    # `_resolve_track` reads this and `_build_playback_metadata` subscripts the
    # result: a bare Mock here is truthy and blows up inside the handler's own
    # `except`, which reports the crash as an ordinary playback error.
    src._shazam.current_track = None
    return src


def _errors(state_machine):
    return [
        call.args[0].message
        for call in state_machine.broadcast.await_args_list
        if getattr(call.args[0], "TYPE", None) == "error"
    ]


class TestResolutionChain:
    """Three rungs, in order: local favourite → the body the caller sent → the API.

    The order is what keeps a favourite the user renamed (or gave a stream URL
    that works from this LAN) from being silently replaced by the directory's
    copy on every play. Only the API rung had a test.
    """

    @pytest.mark.asyncio
    async def test_a_favourite_is_read_from_local_data_and_the_api_is_not_called(
        self, source
    ):
        local = {"id": "s1", "name": "FIP (renamed)", "url": "http://local/fip"}
        source._station_data.is_favorite = Mock(return_value=True)
        source._station_data.get_favorite_metadata_local = Mock(return_value=local)

        result = await source.command("play_station",
                                      {"station_id": "s1", "station": STATION})

        assert result["success"] is True
        assert source._current_station == local
        source._mpv.load_stream.assert_awaited_once_with("http://local/fip")
        source._radio_api.get_station_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_body_the_caller_sent_is_used_before_the_api(self, source):
        result = await source.command("play_station",
                                      {"station_id": "s1", "station": STATION})

        assert result["success"] is True
        assert source._current_station == STATION
        source._radio_api.get_station_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_favourite_with_no_local_record_falls_through_to_the_body(
        self, source
    ):
        """`is_favorite` and "we hold its record" are two different questions."""
        source._station_data.is_favorite = Mock(return_value=True)
        source._station_data.get_favorite_metadata_local = Mock(return_value=None)

        result = await source.command("play_station",
                                      {"station_id": "s1", "station": STATION})

        assert result["success"] is True
        assert source._current_station == STATION

    @pytest.mark.asyncio
    async def test_a_bare_id_is_resolved_through_the_api(self, source):
        source._radio_api.get_station_by_id = AsyncMock(return_value=STATION)

        result = await source.command("play_station", {"station_id": "s1"})

        assert result["success"] is True
        source._radio_api.get_station_by_id.assert_awaited_once_with("s1")
        source._mpv.load_stream.assert_awaited_once_with("http://stream/fip")

    @pytest.mark.asyncio
    async def test_a_station_no_rung_resolves_is_refused_before_mpv_is_touched(
        self, source
    ):
        """Handing mpv a `None` URL is a stall the buffering timeout has to clean
        up 5 ticks later; refusing here names the failure straight away."""
        result = await source.command("play_station", {"station_id": "ghost"})

        assert result["success"] is False
        assert "ghost" in result["error"]
        source._mpv.load_stream.assert_not_awaited()
        assert source._current_station is None
        assert source._is_buffering is False


class TestSwitchingStations:
    """What must be torn down before the new stream is handed to mpv."""

    @pytest.mark.asyncio
    async def test_the_previous_stations_recognition_loop_is_stopped(self, source):
        """Shazam holds the *previous* stream URL and its own timer. Left
        running, it keeps pushing the old station's titles onto the new one."""
        source._current_station = {"id": "s0", "name": "Old", "url": "http://old"}
        source._is_playing = True

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        source._shazam.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mpv_is_stopped_only_when_something_is_playing(self, source):
        source._is_playing = False
        await source.command("play_station", {"station_id": "s1", "station": STATION})
        source._mpv.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mpv_is_stopped_before_the_new_stream_is_loaded(self, source):
        """Order, not presence: `loadfile` on a still-playing mpv is what
        produced two overlapping streams on a slow switch."""
        calls = []
        source._is_playing = True
        source._mpv.stop = AsyncMock(side_effect=lambda: calls.append("stop"))
        source._mpv.load_stream = AsyncMock(
            side_effect=lambda url: calls.append("load") or True
        )

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert calls == ["stop", "load"]

    @pytest.mark.asyncio
    async def test_the_in_band_state_of_the_previous_station_is_cleared(self, source):
        """A leftover `_inband_seen` suppresses the Shazam fallback for the new
        station for as long as it plays."""
        source._inband_seen = True
        source._inband_track = {"title": "Old", "artist": "Gone"}
        source._empty_inband_ticks = 9

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert source._inband_seen is False
        assert source._inband_track is None
        assert source._empty_inband_ticks == 0

    @pytest.mark.asyncio
    async def test_the_spinner_is_published_before_mpv_is_asked_to_load(self, source):
        """The buffering state is broadcast up front on purpose — a station that
        takes seconds to open must show something the moment it is tapped."""
        published = []
        source._mpv.load_stream = AsyncMock(
            side_effect=lambda url: published.append(source._is_buffering) or True
        )

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert published == [True]


class TestStreamRefusedByMpv:
    """`_load_stream` False means mpv refused the command outright."""

    @pytest.mark.asyncio
    async def test_the_failure_is_named_and_the_station_is_dropped(
        self, source, state_machine
    ):
        source._mpv.load_stream = AsyncMock(return_value=False)

        result = await source.command("play_station",
                                      {"station_id": "s1", "station": STATION})
        await drain_background_tasks()

        assert result["success"] is False
        assert result["error"] == "Unable to load stream: FIP"
        assert _errors(state_machine) == ["Unable to load stream: FIP"]
        assert source._current_station is None
        assert source._is_buffering is False

    @pytest.mark.asyncio
    async def test_shazam_is_not_armed_for_a_station_that_did_not_load(self, source):
        source._mpv.load_stream = AsyncMock(return_value=False)

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_an_unexpected_failure_clears_the_spinner_and_reports(
        self, source, state_machine
    ):
        """Without this arm the source is left `is_buffering` for ever: nothing
        else resets it, and the monitor's timeout only runs with a station set."""
        source._mpv.load_stream = AsyncMock(side_effect=RuntimeError("ipc gone"))

        result = await source.command("play_station",
                                      {"station_id": "s1", "station": STATION})
        await drain_background_tasks()

        assert result["success"] is False
        assert "ipc gone" in result["error"]
        assert source._is_buffering is False
        assert _errors(state_machine) == ["ipc gone"]


class TestNowPlayingGates:
    """Which of the two title feeds a station is allowed to use.

    `_recognition_enabled` gates *both* in-band and Shazam (the per-station
    opt-out means "show no track at all"); `_shazam_candidate` additionally
    needs the global toggle. Getting the two confused shows a title on a station
    the user muted, or leaves the fallback dead on every station.
    """

    @pytest.mark.asyncio
    async def test_the_per_station_opt_out_is_read_for_the_station_being_played(
        self, source
    ):
        source._station_data.is_station_shazam_enabled = Mock(return_value=False)

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        source._station_data.is_station_shazam_enabled.assert_called_once_with("s1")
        assert source._recognition_enabled is False
        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_the_fallback_is_armed_when_both_gates_are_open(self, source):
        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert source._recognition_enabled is True
        assert source._shazam_candidate is True

    @pytest.mark.asyncio
    async def test_the_global_toggle_alone_disarms_the_fallback(self, source):
        """In-band needs neither toggle, so `_recognition_enabled` stays on —
        only the Shazam candidacy drops."""
        source._shazam.is_enabled = AsyncMock(return_value=False)

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert source._recognition_enabled is True
        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_the_click_counter_is_fired_and_not_awaited_inline(self, source):
        """radio-browser's ranking counter is best-effort: awaiting it inline
        puts a directory round-trip between the tap and the sound."""
        spawned = []
        source._bg = Mock()
        source._bg.spawn = Mock(side_effect=lambda coro, **kw: spawned.append(kw.get("label")) or coro.close())

        await source.command("play_station", {"station_id": "s1", "station": STATION})

        assert "increment_station_clicks" in spawned


class TestStopPlayback:
    """`stop` is the only command that must leave nothing running."""

    @pytest.mark.asyncio
    async def test_stop_shuts_the_recognition_loop_down(self, source):
        source._current_station = STATION
        source._is_playing = True

        result = await source.command("stop", {})

        assert result["success"] is True
        source._shazam.stop.assert_awaited_once()
        assert source._current_station is None
        assert source.state == SourceState.READY

    @pytest.mark.asyncio
    async def test_stop_remembers_the_station_so_resume_can_retune(self, source):
        source._current_station = STATION
        await source.command("stop", {})
        assert source._last_station == STATION

    @pytest.mark.asyncio
    async def test_a_failure_inside_stop_is_reported_not_raised(self, source):
        """`stop` is reached from the auto-stop timer as well as the UI; an
        exception escaping there kills the timer task silently."""
        source._current_station = STATION
        source._mpv.stop = AsyncMock(side_effect=RuntimeError("socket closed"))

        result = await source.command("stop", {})

        assert result["success"] is False
        assert "socket closed" in result["error"]


class TestResumeDispatch:
    """`resume_playback` re-tunes; a live stream has no unpause."""

    @pytest.mark.asyncio
    async def test_resume_goes_through_the_same_play_path(self, source):
        source._last_station = STATION

        result = await source.command("resume_playback", {})

        assert result["success"] is True
        source._mpv.load_stream.assert_awaited_once_with("http://stream/fip")


class TestShazamSettingChanged:
    """`PUT /api/settings/radio-settings` → `on_shazam_setting_changed`.

    The setting is persisted by the route either way; this method is the only
    thing that acts on it *now*. Losing it means the toggle takes effect only at
    the next station change.
    """

    @pytest.mark.asyncio
    async def test_turning_it_off_stops_a_running_loop(self, source):
        source._current_station = STATION
        source._is_playing = True
        source._shazam_candidate = True

        assert await source.on_shazam_setting_changed(False) is True

        source._shazam.stop.assert_awaited_once()
        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_turning_it_on_rearms_the_fallback_for_the_playing_station(
        self, source
    ):
        source._current_station = STATION
        source._is_playing = True
        source._empty_inband_ticks = 7

        assert await source.on_shazam_setting_changed(True) is True

        assert source._shazam_candidate is True
        # The grace restarts from the toggle, not from where the previous
        # station left off — otherwise Shazam fires immediately on re-enable.
        assert source._empty_inband_ticks == 0
        source._shazam.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turning_it_on_does_not_rearm_when_in_band_already_won(self, source):
        """In-band stays primary. Re-arming here starts a second title feed on a
        station that is already naming its own tracks."""
        source._current_station = STATION
        source._is_playing = True
        source._inband_seen = True

        await source.on_shazam_setting_changed(True)

        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_turning_it_on_respects_the_per_station_opt_out(self, source):
        source._current_station = STATION
        source._is_playing = True
        source._station_data.is_station_shazam_enabled = Mock(return_value=False)

        await source.on_shazam_setting_changed(True)

        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_turning_it_on_with_nothing_playing_arms_nothing(self, source):
        source._current_station = None
        source._is_playing = False

        assert await source.on_shazam_setting_changed(True) is True

        assert source._shazam_candidate is False

    @pytest.mark.asyncio
    async def test_the_toggle_succeeds_while_the_source_is_stopped(self, source):
        """Settings are editable with radio off, when `_shazam` does not exist.
        Reporting failure there paints the red banner on a settings save that
        worked."""
        source._shazam = None

        assert await source.on_shazam_setting_changed(False) is True
