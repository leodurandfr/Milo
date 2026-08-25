# backend/tests/test_radio_monitor.py
"""`RadioSource._on_monitor_tick` — the loop that turns mpv's state into the UI.

The tick was never executed by a test. The in-band arbitration suite in
`test_radio_source.py` calls `_poll_inband_metadata` directly, so the gate that
decides whether it is polled at all, the pause edge that arms auto-stop, and the
buffering timeout that is the *only* thing telling a user their station will not
tune all ran unobserved.

Consumers: `RadioSource` is driven by `MpvAudioSource._monitor_loop`; every
assertion here lands on `state_machine` (the WS `source` events the Pinia store
mirrors) or on the auto-stop timer.
"""
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.audio_state import SourceState
from backend.sources.radio.source import RadioSource
from backend.tests.conftest import drain_background_tasks


@pytest.fixture
def state_machine():
    machine = Mock()
    machine.broadcast = AsyncMock()
    machine.update_source_state = AsyncMock()
    machine.system_state = Mock()
    return machine


@pytest.fixture
def source(state_machine):
    """A radio source mid-playback, with mpv and station data stood in for."""
    src = RadioSource({"mpv_socket": "/tmp/test-radio-ipc.sock"},
                      state_machine=state_machine)
    src._mpv = Mock()
    src._mpv.is_playing = AsyncMock(return_value=False)
    src._mpv.get_property = AsyncMock(return_value=None)
    src._mpv.get_metadata = AsyncMock(return_value={})
    src._station_data = Mock()
    src._station_data.is_favorite = Mock(return_value=False)
    return src


def _errors(state_machine):
    """The `Unable to load stream` banners the tick emitted."""
    return [
        call.args[0].message
        for call in state_machine.broadcast.await_args_list
        if getattr(call.args[0], "TYPE", None) == "error"
    ]


class TestStreamThatNeverLoads:
    """A dead stream leaves mpv idle-active for ever and reports nothing itself.

    The tick is the only thing that notices, and `broadcast_error` is the only
    thing the user sees. Losing this is a spinner that never resolves on a
    station that will never play.
    """

    @staticmethod
    def _buffering(source):
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_buffering = True
        source._is_playing = False
        source._mpv.is_playing = AsyncMock(return_value=False)
        source._mpv.get_property = AsyncMock(
            side_effect=lambda prop: {"pause": False, "idle-active": True}.get(prop)
        )

    @pytest.mark.asyncio
    async def test_the_grace_is_five_ticks_and_the_fifth_is_the_one_that_reports(
        self, source, state_machine
    ):
        """The count is the user-visible delay before the failure is named.

        Asserting both sides of the edge is what keeps this from passing on a
        tick that reports immediately (every station would flash an error while
        it buffers) or one that never reports at all.
        """
        self._buffering(source)

        for _ in range(4):
            await source._on_monitor_tick()
        await drain_background_tasks()
        assert _errors(state_machine) == []
        assert source._current_station is not None

        await source._on_monitor_tick()
        await drain_background_tasks()
        assert _errors(state_machine) == ["Unable to load stream: FIP"]

    @pytest.mark.asyncio
    async def test_the_reported_station_is_dropped_and_the_source_goes_ready(
        self, source, state_machine
    ):
        """Leaving the station pinned keeps the player up over a dead stream."""
        self._buffering(source)

        for _ in range(5):
            await source._on_monitor_tick()
        await drain_background_tasks()

        assert source._current_station is None
        assert source._is_buffering is False
        assert source.state == SourceState.READY
        # `self._metadata = {}` two lines up is inert: `_update_connection_state`
        # republishes through `emit_connection_state`, which *replaces* the dict
        # with the disconnected pair. This is what the player actually reads.
        assert source._metadata == {"is_playing": False, "is_buffering": False}

    @pytest.mark.asyncio
    async def test_mpv_still_working_on_it_is_not_reported(self, source, state_machine):
        """`idle-active` is the tell, not the tick count.

        mpv goes idle-active only once it has given up. A slow stream that is
        still opening past the grace must not be declared dead — reporting on
        the counter alone turns every slow station into a failure.
        """
        self._buffering(source)
        source._mpv.get_property = AsyncMock(
            side_effect=lambda prop: {"pause": False, "idle-active": False}.get(prop)
        )

        for _ in range(12):
            await source._on_monitor_tick()
        await drain_background_tasks()

        assert _errors(state_machine) == []
        assert source._current_station is not None
        assert source._is_buffering is True

    @pytest.mark.asyncio
    async def test_a_station_that_starts_playing_is_never_reported(
        self, source, state_machine
    ):
        """The buffering arm is an `elif`: once mpv plays, the counter is moot."""
        self._buffering(source)
        for _ in range(4):
            await source._on_monitor_tick()

        source._mpv.is_playing = AsyncMock(return_value=True)
        for _ in range(4):
            await source._on_monitor_tick()
        await drain_background_tasks()

        assert _errors(state_machine) == []
        assert source._is_buffering is False


class TestPlaybackEdges:
    """The two edges that publish a state change, and the one that must not."""

    @pytest.mark.asyncio
    async def test_buffering_to_playing_clears_the_spinner(self, source, state_machine):
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_buffering = True
        source._is_playing = False
        source._mpv.is_playing = AsyncMock(return_value=True)

        await source._on_monitor_tick()
        await drain_background_tasks()

        assert source._is_buffering is False
        assert source.state == SourceState.ACTIVE
        assert source._metadata["is_buffering"] is False
        assert source._metadata["is_playing"] is True

    @pytest.mark.asyncio
    async def test_a_stream_that_drops_publishes_the_stop(self, source, state_machine):
        """mpv losing the stream is not a command — nothing else announces it."""
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=False)

        await source._on_monitor_tick()
        await drain_background_tasks()

        assert source._is_playing is False
        assert source._metadata["is_playing"] is False
        state_machine.update_source_state.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_steady_tick_publishes_nothing(self, source, state_machine):
        """No edge, no event: the tick runs continuously while a station plays,
        so an unconditional publish is a broadcast storm on every unit."""
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=True)

        for _ in range(6):
            await source._on_monitor_tick()
        await drain_background_tasks()

        state_machine.update_source_state.assert_not_awaited()


class TestPauseEdge:
    """mpv's `pause` property is the only pause signal radio has.

    Radio exposes no pause control, so this is the defensive path that keeps it
    uniform with the other mpv sources — and it is what arms the auto-stop timer.
    """

    @pytest.mark.asyncio
    async def test_the_mpv_pause_property_drives_the_auto_stop_timer(self, source):
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=True)
        source._mpv.get_property = AsyncMock(
            side_effect=lambda prop: True if prop == "pause" else None
        )
        source._handle_pause_change = Mock()

        await source._on_monitor_tick()

        source._handle_pause_change.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_an_unavailable_pause_property_is_not_read_as_playing(self, source):
        """mpv answers None when the property is not available.

        Coercing that to False cancels a pause timer that is legitimately
        armed, so the auto-stop never fires — the failure is a unit that keeps
        a dead stream open for hours instead of releasing the source.
        """
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=True)
        source._mpv.get_property = AsyncMock(return_value=None)
        source._handle_pause_change = Mock()

        await source._on_monitor_tick()

        source._handle_pause_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_station_means_no_pause_read(self, source):
        """Reading mpv properties with nothing tuned is IPC traffic per tick for
        an answer that cannot mean anything."""
        source._current_station = None
        source._is_playing = False

        await source._on_monitor_tick()

        source._mpv.get_property.assert_not_called()


class TestInbandPollingGate:
    """`_poll_inband_metadata` is reached only through the tick's own gate.

    Every existing in-band test calls the poll directly, so this condition —
    which decides whether a title is read at all — had never run.
    """

    @pytest.mark.asyncio
    async def test_a_playing_station_is_polled(self, source):
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=True)
        source._poll_inband_metadata = AsyncMock()

        await source._on_monitor_tick()

        source._poll_inband_metadata.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_buffering_station_is_not_polled_yet(self, source):
        """Reading a title off a stream that has not opened pins the *previous*
        station's title onto the one now loading."""
        source._current_station = {"id": "s1", "name": "FIP", "url": "http://x"}
        source._is_buffering = True
        source._is_playing = False
        source._mpv.is_playing = AsyncMock(return_value=False)
        source._poll_inband_metadata = AsyncMock()

        await source._on_monitor_tick()

        source._poll_inband_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_tuned_is_not_polled(self, source):
        source._current_station = None
        source._is_playing = True
        source._mpv.is_playing = AsyncMock(return_value=True)
        source._poll_inband_metadata = AsyncMock()

        await source._on_monitor_tick()

        source._poll_inband_metadata.assert_not_awaited()
