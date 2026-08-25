# backend/tests/test_bluetooth_avrcp.py
"""
Unit tests for the Bluetooth source's AVRCP feed.

Two things are covered, neither reachable from any other guardrail:

  - **The BlueZ signal → player state mapping** (`AvrcpController`). Real
    `org.bluez.MediaPlayer1` signals stand in for the sender here; what is
    asserted is the snapshot the controller derived from them. The values a
    phone actually publishes are hostile in specific ways — an empty Title on a
    player with no queue, Duration 0 for "unknown", UINT32_MAX on Position to
    say a track ended — and each has a visible consequence: an empty title
    mounts the full-screen player over a blank track, and a literal UINT32_MAX
    draws a 49-day progress bar.

  - **The broadcast policy** (`BluetoothSource`). AVRCP pushes a Position about
    once a second; what is asserted is what reached the state machine, which is
    what the shared player draws.
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from dbus_next import Variant
from dbus_next.constants import MessageType

from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.bluetooth import avrcp as avrcp_module
from backend.sources.bluetooth.avrcp import (
    MEDIA_PLAYER_IFACE,
    PROPERTIES_IFACE,
    AvrcpController,
    parse_track,
)
from backend.sources.bluetooth.source import BluetoothSource

ADDRESS = "AA:BB:CC:DD:EE:FF"
PLAYER_PATH = f"/org/bluez/hci0/dev_{ADDRESS.replace(':', '_')}/player0"


class Signal:
    """A D-Bus signal in the shape dbus-next hands to a message handler."""

    message_type = MessageType.SIGNAL

    def __init__(self, member, body, path=None):
        self.member = member
        self.body = body
        self.path = path


def track_variant(**fields) -> Variant:
    """An AVRCP Track property, variant-wrapped as it arrives on the bus."""
    signatures = {"Duration": "u", "TrackNumber": "u", "NumberOfTracks": "u"}
    return Variant("a{sv}", {
        key: Variant(signatures.get(key, "s"), value) for key, value in fields.items()
    })


def player_added(**props) -> Signal:
    return Signal("InterfacesAdded", [PLAYER_PATH, {MEDIA_PLAYER_IFACE: props}])


def player_changed(**props) -> Signal:
    return Signal("PropertiesChanged", [MEDIA_PLAYER_IFACE, props, []], path=PLAYER_PATH)


class TestParseTrack:
    """The projection of an AVRCP Track onto Milō's canonical keys."""

    def test_a_full_track_yields_every_field(self):
        """The non-triviality check the rest of this class rests on: a parse
        that silently returned an empty projection would pass every 'is None'
        assertion below."""
        parsed = parse_track({
            "Title": "Says",
            "Artist": "Nils Frahm",
            "Album": "Spaces",
            "Duration": 511000,
            "TrackNumber": 4,
        })

        assert parsed == {
            "title": "Says",
            "artist": "Nils Frahm",
            "album": "Spaces",
            "duration": 511000,
        }

    def test_an_empty_string_is_not_a_title(self):
        """Senders publish `Title: ""` on a player with no queue. Truthiness is
        what the frontend's rich-display gate reads, so a surviving `""` would
        mount the full-screen player over a blank track."""
        parsed = parse_track({"Title": "", "Artist": "   ", "Album": ""})

        assert (parsed["title"], parsed["artist"], parsed["album"]) == (None, None, None)

    def test_a_track_with_no_metadata_at_all_is_all_none(self):
        assert parse_track({}) == {
            "title": None, "artist": None, "album": None, "duration": None
        }

    def test_duration_zero_means_unknown_not_zero_length(self):
        """AVRCP sends 0 when it does not know the length. Kept as 0 it would
        draw a full progress bar over a track that just started."""
        assert parse_track({"Title": "Says", "Duration": 0})["duration"] is None


class TestPlayerTracking:
    """What the controller derives from the signals BlueZ sends it."""

    def test_a_player_appearing_is_adopted_with_its_track(self):
        avrcp = AvrcpController()

        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Says", Artist="Nils Frahm", Duration=511000),
            Status=Variant("s", "playing"),
            Position=Variant("u", 192000),
        ))

        assert avrcp.has_player
        assert avrcp.device_address == ADDRESS
        assert avrcp.snapshot() == {
            "title": "Says",
            "artist": "Nils Frahm",
            "album": None,
            "duration": 511000,
            "is_playing": True,
            "position": 192000,
        }

    def test_a_player_leaving_takes_the_track_with_it(self):
        """The player object disappears when the sender drops AVRCP. A snapshot
        that kept the track would re-publish it on the next reconnection."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Track=track_variant(Title="Says")))

        avrcp._on_dbus_message(Signal(
            "InterfacesRemoved", [PLAYER_PATH, [MEDIA_PLAYER_IFACE]]
        ))

        assert not avrcp.has_player
        assert avrcp.snapshot()["title"] is None

    async def test_a_player_leaving_is_announced(self):
        """`_clear_player` wipes the track and wakes the notifier — but every
        publish is addressed by MAC, and the MAC comes from the path that just
        went. Dropped there, nothing tells the source the sender stopped
        publishing, and the track it left stays on screen."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Says"), Status=Variant("s", "playing")))
        published = AsyncMock()
        avrcp.set_callback(published)
        avrcp._notify_task = asyncio.create_task(avrcp._notify_loop())
        await asyncio.sleep(0.02)
        published.reset_mock()

        avrcp._on_dbus_message(Signal(
            "InterfacesRemoved", [PLAYER_PATH, [MEDIA_PLAYER_IFACE]]
        ))
        await asyncio.sleep(0.02)

        published.assert_awaited_once()
        address, snapshot = published.await_args.args
        assert address == ADDRESS, "published for nobody"
        assert snapshot["title"] is None
        await avrcp.stop()

    def test_a_property_change_updates_only_what_it_carries(self):
        """PropertiesChanged is a delta — BlueZ sends Status alone on a pause.
        Applying it as a whole record would wipe the track that is still loaded."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Says", Artist="Nils Frahm"),
            Status=Variant("s", "playing"),
        ))

        avrcp._on_dbus_message(player_changed(Status=Variant("s", "paused")))

        snapshot = avrcp.snapshot()
        assert snapshot["is_playing"] is False
        assert snapshot["title"] == "Says"

    @pytest.mark.parametrize("status,playing", [
        ("playing", True),
        ("paused", False),
        ("stopped", False),
        ("error", False),
        # The sender running its own fast-forward: audio is still moving, and
        # flipping the on-screen icon for its duration would read as a stall.
        ("forward-seek", True),
        ("reverse-seek", True),
    ])
    def test_status_decides_is_playing(self, status, playing):
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Status=Variant("s", status)))

        assert avrcp.snapshot()["is_playing"] is playing

    def test_the_end_of_track_sentinel_is_not_a_playhead(self):
        """BlueZ documents UINT32_MAX on Position as "the track ended". Taken
        literally the player would draw a 49-day progress bar."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 0xFFFFFFFF)))

        assert avrcp.snapshot()["position"] is None

    async def test_the_playhead_is_fetched_because_playback_notifies_nothing(self):
        """BlueZ signals Position when it re-anchors on a state change and at no
        other time — three seconds of steady playback carry none. So between
        state changes an explicit Get is the only thing that moves the bar, and
        the message it builds is the contract with BlueZ."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 60)))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 133166)]
        )))

        assert await avrcp.read_position() is True

        sent = avrcp._bus.call.call_args.args[0]
        assert (sent.interface, sent.member) == (PROPERTIES_IFACE, "Get")
        assert sent.body == [MEDIA_PLAYER_IFACE, "Position"]
        assert avrcp.snapshot()["position"] == 133166

    async def test_a_playhead_read_that_fails_leaves_the_last_one(self):
        """BlueZ answers an error the moment the sender drops AVRCP mid-read.
        Zeroing the position there would rewind the bar on a track still
        playing."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 133166)))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.ERROR, body=["org.bluez.Error.Failed"]
        )))

        assert await avrcp.read_position() is False
        assert avrcp.snapshot()["position"] == 133166

    def test_a_signalled_position_wins_over_a_read_one(self):
        """The asymmetry the whole playhead rests on. Traced on the unit, a Get
        issued 1 ms after `Status = playing` answered 68817 while BlueZ signalled
        65681 ten milliseconds later: the Get read an anchor not yet corrected,
        the signal *is* the correction. Gate the signal and the bar keeps the
        wrong value; this test fails the moment a threshold is put in its way."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 68817)))
        avrcp._on_dbus_message(player_changed(Position=Variant("u", 65681)))

        assert avrcp.snapshot()["position"] == 65681

    async def test_a_read_taken_while_a_state_change_settles_is_discarded(self):
        """The other half, and the exact reading that made the bar stutter:
        traced on the unit, 1 ms after `Status = paused` a Get answered 41552 on
        a track playing at 65 s."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 65294)))
        avrcp._on_dbus_message(player_changed(Status=Variant("s", "paused")))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 41552)]
        )))

        assert await avrcp.read_position() is True
        assert avrcp.snapshot()["position"] == 65294

    async def test_the_notifier_never_reads_the_playhead_itself(self):
        """A signal is what wakes the notifier most often, and a Get issued next
        to a state change is precisely the reading BlueZ has not corrected yet.
        Reading there is what injected a 24-second-wrong playhead on pause, so
        the loop publishes what it holds and lets the readers read."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 65294)))
        avrcp.set_callback(AsyncMock())
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 41552)]
        )))
        avrcp._notify_task = asyncio.create_task(avrcp._notify_loop())

        avrcp._on_dbus_message(player_changed(Status=Variant("s", "paused")))
        await asyncio.sleep(0.05)

        assert not [c for c in avrcp._bus.call.call_args_list
                    if c.args[0].member == "Get"], "the notifier issued a Get"
        assert avrcp.snapshot()["position"] == 65294
        await avrcp.stop()

    def test_a_half_updated_track_is_not_published(self):
        """A skip carries the incoming track's Duration ~600 ms before its Title.
        Traced on the unit: dur 215533 → 211426 with the title still `Canto De
        Ossanha` and the playhead resetting to 31 ms under the old name. Applied,
        the bar resets once for that phantom and again for the real change — the
        0:00 → 0:01 → 0:00 on every Next."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Canto De Ossanha", Duration=215533),
            Position=Variant("u", 113492),
        ))
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Canto De Ossanha", Duration=211426),
            Position=Variant("u", 31),
        ))

        held = avrcp.snapshot()
        assert held["duration"] == 215533, "the outgoing track kept its length"
        assert held["position"] == 113492, "and its playhead"

        # The identity catches up: now it is a track change like any other.
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Revenants", Duration=211426)
        ))
        avrcp._on_dbus_message(player_changed(Position=Variant("u", 257)))

        assert avrcp.snapshot()["title"] == "Revenants"
        assert avrcp.snapshot()["position"] == 257

    def test_a_new_track_does_not_inherit_the_old_playhead(self):
        """BlueZ moves Track and Position in separate messages and they do not
        arrive together, so for a moment the incoming track would be published
        carrying the outgoing one's offset — a bar that reads 1:33 on a track
        that just started. No playhead beats the wrong one: the bar waits."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 93817)))
        avrcp._on_dbus_message(player_changed(Track=track_variant(Title="Next one")))

        assert avrcp.snapshot()["position"] is None

    async def test_previous_zeroes_the_playhead_without_waiting_for_bluez(self):
        """The one thing BlueZ will not tell us, at all, ever. A Previous that
        restarts the track republishes an identical Track dict, so no signal
        fires — and BlueZ only re-anchors on a state change, so it keeps counting
        from the old playhead. Traced on the unit: Previous at 20242 ms, then 44 s
        of smooth counting with no discontinuity, and a pause finally revealing
        20242 ms of error still there. Nothing observable corrects it, and
        nothing is worth waiting for: Previous starts from the top whichever
        thing it does, so the playhead is taken on the press."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Newbutt Lane", Duration=180000),
            Status=Variant("s", "playing"),
            Position=Variant("u", 20242),
        ))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[]
        )))

        assert await avrcp.send("Previous") is True

        # No sleep: waiting on a verdict is what left the bar reading 0:03 by
        # the time it reset.
        assert avrcp.snapshot()["position"] < 100
        await avrcp.stop()

    async def test_previous_while_paused_holds_the_bar_at_zero(self):
        """The same press, on a track that is not moving. Previous starts from
        the top whichever thing it does, so the bar belongs at zero — and it
        belongs *at* zero, not counting up from it. Counted, a paused progress
        bar walks across the screen on its own."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Newbutt Lane", Duration=180000),
            Status=Variant("s", "paused"),
            Position=Variant("u", 20242),
        ))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[]
        )))

        assert await avrcp.send("Previous") is True
        assert avrcp.snapshot()["position"] == 0

        # Thirty seconds of paused wall clock, without waiting for any.
        avrcp._own_playhead_from -= 30.0
        assert avrcp.snapshot()["position"] == 0
        await avrcp.stop()

    async def test_a_paused_restart_is_not_undone_by_a_read(self):
        """Why the playhead is still *taken* while paused. BlueZ re-anchors on
        a state change and a Previous is not one, so its Get keeps answering
        the offset the track had before the press — for as long as the pause
        lasts, since nothing else will move it."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Newbutt Lane", Duration=180000),
            Status=Variant("s", "paused"),
            Position=Variant("u", 20242),
        ))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 20242)]
        )))
        assert await avrcp.send("Previous") is True
        # Past REANCHOR_SETTLE_S, so what discards the read is the ownership
        # and not the settle window.
        avrcp._status_changed_at -= 1.0

        assert await avrcp.read_position() is True
        assert avrcp.snapshot()["position"] == 0
        await avrcp.stop()

    async def test_a_new_track_keeps_counting_when_bluez_says_nothing(self):
        """A track change clears the playhead, and BlueZ is under no obligation
        to send a new one — when it does not, the bar sat frozen at 0:00 until
        the 5 s poll, which is the 0:00-while-the-music-is-at-0:04 on a Next. The
        new track started at the press, so it is counted from there."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Newbutt Lane", Duration=180000),
            Status=Variant("s", "playing"),
            Position=Variant("u", 90000),
        ))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[]
        )))

        assert await avrcp.send("Next") is True
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Water's Path", Duration=241506)
        ))

        # No Position signal at all, and the bar still has a playhead.
        assert avrcp.snapshot()["position"] is not None
        assert avrcp.snapshot()["position"] < 1000
        await avrcp.stop()

    async def test_a_track_change_hands_the_playhead_back(self):
        """The other outcome of the same press. When Previous moves to the track
        before rather than restarting this one, BlueZ *does* re-anchor — so
        holding our own count past that point would pin the bar to a clock that
        no longer means anything. The track change gives it back."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Newbutt Lane", Duration=180000),
            Status=Variant("s", "playing"),
            Position=Variant("u", 2000),
        ))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[]
        )))

        assert await avrcp.send("Previous") is True
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Water's Path", Duration=241506)
        ))
        avrcp._on_dbus_message(player_changed(Position=Variant("u", 292)))

        assert avrcp.snapshot()["title"] == "Water's Path"
        assert avrcp.snapshot()["position"] == 292
        await avrcp.stop()

    async def test_a_restart_a_few_seconds_in_still_moves_the_bar(self):
        """Why the window is a delay and not a magnitude. A Previous restarting a
        track three seconds in moves the playhead by about as much as a stale
        anchor does, so a threshold wide enough to absorb the noise froze the bar
        on exactly the restart the user had just asked for."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 3200)))
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 210)]
        )))

        assert await avrcp.read_position() is True
        assert avrcp.snapshot()["position"] == 210

    async def test_a_transport_command_looks_at_the_playhead_again(self, monkeypatch):
        """A sender takes a moment to tell BlueZ what a command did, and BlueZ
        pushes nothing in the meantime — measured, up to ~3 s — so a command that
        does not look again leaves the bar wrong. Asserted on the bus traffic,
        not on the delays: the schedule is tuning, looking again is the contract.
        Uses Next because Previous takes the playhead outright and would answer
        from its own clock instead of the read."""
        monkeypatch.setattr(avrcp_module, "COMMAND_REREAD_DELAYS", (0.0,))
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 122700)))
        avrcp.set_callback(AsyncMock())
        avrcp._bus = Mock(call=AsyncMock(return_value=Mock(
            message_type=MessageType.METHOD_RETURN, body=[Variant("u", 1904)]
        )))
        avrcp._notify_task = asyncio.create_task(avrcp._notify_loop())
        # Adopting the player wakes the notifier on its own, and that read would
        # pass this test whether or not the command schedules one. Let it drain,
        # then count only what the command caused.
        await asyncio.sleep(0.02)
        avrcp._bus.call.reset_mock()

        assert await avrcp.send("Next") is True
        await asyncio.sleep(0.05)

        reads = [c.args[0] for c in avrcp._bus.call.call_args_list if c.args[0].member == "Get"]
        assert reads, "the command never re-read the playhead"
        assert avrcp.snapshot()["position"] == 1904
        await avrcp.stop()

    def test_the_snapshot_never_carries_artwork(self):
        """Measured on a live iPhone, the Track dict is exactly Title /
        TrackNumber / NumberOfTracks / Duration / Album / Artist — no image
        field, no `ImgHandle`. Any cover the UI shows came from elsewhere."""
        avrcp = AvrcpController()
        avrcp._on_dbus_message(player_added(Track=track_variant(
            Title="Live For You", Artist="Thee Sacred Souls",
            Album="Got a Story to Tell",
        )))

        assert "album_art_url" not in avrcp.snapshot()

    def test_a_foreign_object_is_not_taken_for_a_player(self):
        """Every BlueZ object announces itself on the same signal — the BT
        remote's HID interfaces included."""
        avrcp = AvrcpController()

        avrcp._on_dbus_message(Signal("InterfacesAdded", [
            "/org/bluez/hci0/dev_11_22_33_44_55_66",
            {"org.bluez.Device1": {"Connected": Variant("b", True)}},
        ]))

        assert not avrcp.has_player


@pytest.fixture
def bluetooth():
    """A Bluetooth source with a connected sender and a recording state machine."""
    source = BluetoothSource()
    source.connected_device = {"address": ADDRESS, "name": "Leo's iPhone"}

    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_state = AsyncMock()
    state_machine.update_position_metadata = AsyncMock()
    state_machine.system_state = Mock(active_source=AudioSource.BLUETOOTH)
    source.state_machine = state_machine
    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    return source, state_machine


def published(state_machine):
    """The (state, metadata) of the last push to the state machine."""
    _, state, metadata = state_machine.update_source_state.call_args.args
    return state, metadata


PLAYING = {
    "title": "Says", "artist": "Nils Frahm", "album": "Spaces",
    "duration": 511000, "position": 192000, "is_playing": True,
}


class TestBroadcastPolicy:
    """What the source published, which is what the shared player draws."""

    async def test_a_track_is_published_active_with_the_device_name(self, bluetooth):
        """The device name rides alongside the track: it is what the status card
        draws for the same sender before any AVRCP arrives."""
        source, state_machine = bluetooth

        await source._on_avrcp_update(ADDRESS, dict(PLAYING))

        state, metadata = published(state_machine)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == "Says"
        assert metadata["artist"] == "Nils Frahm"
        assert metadata["is_playing"] is True
        assert metadata["device_name"] == "Leo's iPhone"

    async def test_the_link_alone_yields_no_cover(self, bluetooth):
        """Nothing about a Bluetooth track carries an image, so until the
        resolver answers there is no album_art_url to publish — and publishing
        an empty one would leave the player waiting on an image forever."""
        source, state_machine = bluetooth

        await source._on_avrcp_update(ADDRESS, dict(PLAYING))

        _, metadata = published(state_machine)
        assert "album_art_url" not in metadata

    async def test_a_moved_playhead_alone_skips_the_full_broadcast(self, bluetooth):
        """AVRCP ticks about once a second. A full_state per tick would push the
        whole system state to every client at that rate; the frontend
        interpolates locally and only needs the drift correction."""
        source, state_machine = bluetooth
        await source._on_avrcp_update(ADDRESS, dict(PLAYING))
        publishes = state_machine.update_source_state.call_count

        await source._on_avrcp_update(ADDRESS, {**PLAYING, "position": 193000})
        await source._on_avrcp_update(ADDRESS, {**PLAYING, "position": 194000})

        assert state_machine.update_source_state.call_count == publishes
        assert source._bg.spawn.called, "no drift correction was broadcast either"

    async def test_a_pause_is_published_immediately(self, bluetooth):
        """What interpolation cannot guess. A pause folded into the throttled
        path would leave a play button reading 'playing' for up to ten seconds."""
        source, state_machine = bluetooth
        await source._on_avrcp_update(ADDRESS, dict(PLAYING))
        publishes = state_machine.update_source_state.call_count

        await source._on_avrcp_update(ADDRESS, {**PLAYING, "is_playing": False})

        assert state_machine.update_source_state.call_count == publishes + 1
        _, metadata = published(state_machine)
        assert metadata["is_playing"] is False

    async def test_refresh_hands_back_a_live_playhead(self, bluetooth):
        """The one hook this source needs. Nothing notifies a moved playhead, so
        the stored position is whatever was captured at the last track change —
        near zero for the whole song. `GET /api/audio/state` and the WS
        handshake both land here, and `refresh_active_metadata` copies
        `source.metadata` straight into the system state, so a stale value there
        is a progress bar that restarts at 0:00 on every page load."""
        source, _ = bluetooth
        await source._on_avrcp_update(ADDRESS, {**PLAYING, "position": 60})
        assert source.metadata["position"] == 60

        source.avrcp = Mock(
            has_player=True,
            read_position=AsyncMock(return_value=True),
            snapshot=Mock(return_value={**PLAYING, "position": 133166}),
        )

        assert await source.refresh_metadata() is True
        assert source.avrcp.read_position.awaited
        assert source.metadata["position"] == 133166

    async def test_refresh_reports_nothing_without_a_player(self, bluetooth):
        """A sender with no AVRCP target has no playhead to re-read, and
        `refresh_active_metadata` must not overwrite the state with an empty
        record on the strength of a False."""
        source, _ = bluetooth
        source.avrcp = Mock(has_player=False)

        assert await source.refresh_metadata() is False

    async def test_the_album_is_used_to_look_the_cover_up(self, bluetooth):
        """AVRCP gives an album where radio's in-band metadata does not, and an
        album has one cover while a track can sit on a dozen compilations with
        a dozen different ones."""
        source, _ = bluetooth
        source._artwork = Mock(resolve=AsyncMock(return_value=None))

        await source._resolve_artwork(source._track_key(PLAYING))

        source._artwork.resolve.assert_awaited_once_with("Nils Frahm", "Says", "Spaces")

    async def test_a_resolved_cover_survives_the_position_poll(self, bluetooth):
        """The trap this guards: the poll replaces the playback dict wholesale
        with a fresh AVRCP snapshot every few seconds, and that snapshot has no
        artwork. A cover written into the dict would be wiped on the next tick,
        so the player would flash its glyph back every 5 seconds."""
        source, state_machine = bluetooth
        source._artwork = Mock(resolve=AsyncMock(return_value="https://x/600x600bb.jpg"))
        await source._on_avrcp_update(ADDRESS, dict(PLAYING))
        await source._resolve_artwork(source._track_key(PLAYING))

        _, metadata = published(state_machine)
        assert metadata["album_art_url"] == "https://x/600x600bb.jpg"

        # A later snapshot for the same track — a pause, which publishes.
        await source._on_avrcp_update(ADDRESS, {**PLAYING, "is_playing": False})

        _, metadata = published(state_machine)
        assert metadata["album_art_url"] == "https://x/600x600bb.jpg"

    async def test_a_new_track_does_not_inherit_the_previous_cover(self, bluetooth):
        source, state_machine = bluetooth
        source._artwork = Mock(resolve=AsyncMock(return_value="https://x/600x600bb.jpg"))
        await source._on_avrcp_update(ADDRESS, dict(PLAYING))
        await source._resolve_artwork(source._track_key(PLAYING))

        await source._on_avrcp_update(ADDRESS, {**PLAYING, "title": "Toilet Brush"})

        _, metadata = published(state_machine)
        assert "album_art_url" not in metadata

    async def test_a_cover_arriving_after_the_track_moved_on_is_dropped(self, bluetooth):
        """The lookup is a network round trip; the sender can change track
        during it, and the answer then describes a track that is no longer on
        screen."""
        source, state_machine = bluetooth
        stale = source._track_key(PLAYING)
        await source._on_avrcp_update(ADDRESS, {**PLAYING, "title": "Toilet Brush"})
        source._artwork = Mock(resolve=AsyncMock(return_value="https://x/600x600bb.jpg"))

        await source._resolve_artwork(stale)

        _, metadata = published(state_machine)
        assert "album_art_url" not in metadata

    async def test_another_senders_player_is_ignored(self, bluetooth):
        """One device at a time is enforced on the BlueALSA side, but a player
        object outlives its A2DP transport briefly — long enough to publish the
        wrong phone's track over the connected one's."""
        source, state_machine = bluetooth
        await source._on_avrcp_update(ADDRESS, dict(PLAYING))
        publishes = state_machine.update_source_state.call_count

        await source._on_avrcp_update("11:22:33:44:55:66", {
            **PLAYING, "title": "Someone else's track"
        })

        assert state_machine.update_source_state.call_count == publishes


class TestControllerLifecycle:
    """`AvrcpController.start` / `stop` — the BlueZ link itself.

    Both green in the Lot A eviscration sweep: replaced by their neutral the
    whole suite stayed green, so nothing checked that starting subscribes to
    BlueZ at all, nor that stopping lets go of it. What that leaves unguarded
    is the metadata feed of the whole Bluetooth source -- a start that
    subscribes to nothing leaves the sender's title, artist and transport dark
    while the source still reports ACTIVE.

    The real system bus is stood in for here: the suite is checked out on the
    appliance, where BlueZ answers, and a test that reached it would be talking
    to the pairings of the room it runs in.
    """

    @pytest.fixture
    def bus(self):
        bus = Mock()
        bus.call = AsyncMock(return_value=Mock(message_type=MessageType.METHOD_RETURN))
        bus.add_message_handler = Mock()
        bus.disconnect = Mock()
        return bus

    @pytest.fixture
    def controller(self, bus, monkeypatch):
        connect = AsyncMock(return_value=bus)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=connect)))
        avrcp = AvrcpController()
        avrcp._adopt_existing_player = AsyncMock()
        return avrcp

    async def test_start_subscribes_to_every_rule_and_installs_the_handler(
        self, controller, bus
    ):
        assert await controller.start() is True

        assert bus.call.await_count == len(avrcp_module._MATCH_RULES), (
            "one AddMatch per rule: a rule dropped here is a signal never seen"
        )
        bus.add_message_handler.assert_called_once_with(controller._on_dbus_message)
        await controller.stop()

    async def test_start_adopts_a_player_that_already_exists(self, controller):
        """The backend restarting under a live session sees no change signal."""
        await controller.start()
        controller._adopt_existing_player.assert_awaited_once()
        await controller.stop()

    async def test_a_refused_addmatch_is_not_reported_as_a_live_listener(
        self, controller, bus
    ):
        bus.call = AsyncMock(return_value=Mock(message_type=MessageType.ERROR,
                                               body=["refused"]))

        # `@handle_errors` turns the raise into the documented fail-open value:
        # the source keeps working as a plain receiver, minus the metadata.
        assert await controller.start() is False
        controller._adopt_existing_player.assert_not_awaited()

    async def test_stop_cancels_the_loops_start_spawned(self, controller):
        await controller.start()
        tasks = [controller._notify_task, controller._poll_task]
        assert all(tasks), "start spawned no loop — the assertion below is empty"

        await controller.stop()

        assert all(t.cancelled() or t.done() for t in tasks)
        assert controller._notify_task is None
        assert controller._poll_task is None
