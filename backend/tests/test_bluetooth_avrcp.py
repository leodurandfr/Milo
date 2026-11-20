# backend/tests/test_bluetooth_avrcp.py
"""
Unit tests for the Bluetooth source's AVRCP feed.

The BlueZ signal → player state mapping (`AvrcpController`). Real
`org.bluez.MediaPlayer1` signals stand in for the sender here; what is asserted
is the snapshot the controller derived from them. The values a phone actually
publishes are hostile in specific ways — an empty Title on a player with no
queue, Duration 0 for "unknown", UINT32_MAX on Position to say a track ended —
and each has a visible consequence: an empty title mounts the full-screen
player over a blank track, and a literal UINT32_MAX draws a 49-day progress
bar.

What the source publishes from that snapshot (the broadcast policy, the cover)
is driven through the measured world in test_bluetooth_behavior.py.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dbus_next import Variant
from dbus_next.constants import MessageType

from backend.sources.bluetooth import adapter as bluez_adapter
from backend.sources.bluetooth import avrcp as avrcp_module
from backend.sources.bluetooth.avrcp import (
    BLUEZ_SERVICE,
    DBUS_DAEMON,
    MEDIA_PLAYER_IFACE,
    PROPERTIES_IFACE,
    AvrcpController,
    parse_track,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
PLAYER_PATH = f"/org/bluez/hci0/dev_{ADDRESS.replace(':', '_')}/player0"
# The unique names on the system bus: bluetoothd's, and another local account's.
BLUETOOTHD = ":1.42"
INTRUDER = ":1.666"


class Signal:
    """A D-Bus signal in the shape dbus-next hands to a message handler,
    sent by bluetoothd unless `sender` says otherwise."""

    message_type = MessageType.SIGNAL

    def __init__(self, member, body, path=None, sender=BLUETOOTHD):
        self.member = member
        self.body = body
        self.path = path
        self.sender = sender


def owner_changed(new: str, sender: str = DBUS_DAEMON) -> Signal:
    """NameOwnerChanged for org.bluez, from the bus daemon unless forged."""
    return Signal("NameOwnerChanged", [BLUEZ_SERVICE, "", new],
                  path="/org/freedesktop/DBus", sender=sender)


def listening() -> AvrcpController:
    """A controller that knows bluetoothd's unique name, told by the bus
    daemon as on the appliance."""
    avrcp = AvrcpController()
    avrcp._on_dbus_message(owner_changed(BLUETOOTHD))
    return avrcp


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
            "remote": False,
        }

    def test_an_empty_string_is_not_a_title(self):
        """Senders publish `Title: ""` on a player with no queue. Truthiness is
        what the frontend's rich-display gate reads, so a surviving `""` would
        mount the full-screen player over a blank track."""
        parsed = parse_track({"Title": "", "Artist": "   ", "Album": ""})

        assert (parsed["title"], parsed["artist"], parsed["album"]) == (None, None, None)

    def test_a_track_with_no_metadata_at_all_is_all_none(self):
        assert parse_track({}) == {
            "title": None, "artist": None, "album": None, "duration": None, "remote": False,
        }

    def test_duration_zero_means_unknown_not_zero_length(self):
        """AVRCP sends 0 when it does not know the length. Kept as 0 it would
        draw a full progress bar over a track that just started."""
        assert parse_track({"Title": "Says", "Duration": 0})["duration"] is None


class TestARemoteCard:
    """An iPhone controlling a Mac publishes the two lines of the iOS card, not
    the fields they were built from — and the track they name plays on the
    Mac, not here.

    Measured on the unit 2026-09-16 (the values below are that capture); the
    phone's stream ran silence under it (2026-11-05). Taken as a track, it put
    the Mac's song on the screen and the lock screen as playing here, and sent
    "Écoute en cours sur Mac mini de Léo" to iTunes as an artist.
    """

    REMOTE_CARD = {
        "Title": "Here but I'm Gone \u2022 Curtis Mayfield",
        "Artist": "Écoute en cours sur Mac mini de Léo",
        "TrackNumber": 8,
        "NumberOfTracks": 13,
        "Duration": 318200,
    }

    def test_a_remote_card_names_no_track(self):
        """The source reads `remote` as no player, and every key left None is
        one the screen, the cover lookup and the lock screen cannot draw."""
        assert parse_track(self.REMOTE_CARD) == {
            "title": None, "artist": None, "album": None, "duration": None, "remote": True,
        }

    def test_a_normal_session_is_left_exactly_as_it_is(self):
        """The discriminator is the absent Album — a normal iOS session carries
        one (measured, see the module header). Without that half, every track
        whose name holds a bullet would be taken for a remote card."""
        parsed = parse_track({
            "Title": "Wild Wild West \u2022 Live",
            "Artist": "Kool Moe Dee",
            "Album": "Knowledge Is King",
            "Duration": 1000,
        })

        assert not parsed["remote"]
        assert (parsed["title"], parsed["artist"]) == ("Wild Wild West \u2022 Live", "Kool Moe Dee")

    def test_two_joiners_are_not_this_shape(self):
        """A card has exactly two lines. More than one joiner is a track name
        that happens to use the character."""
        parsed = parse_track({"Title": "A \u2022 B \u2022 C", "Artist": "Real"})

        assert not parsed["remote"]
        assert (parsed["title"], parsed["artist"]) == ("A \u2022 B \u2022 C", "Real")

    def test_a_padded_joiner_is_still_this_shape(self):
        assert parse_track({"Title": "Says  \u2022  Nils Frahm"})["remote"]

    def test_a_trailing_joiner_is_not_this_shape(self):
        """`"Solo \u2022 "` arrives stripped, so the pattern's own spacing is
        gone — the title keeps the character it was given."""
        parsed = parse_track({"Title": "Solo \u2022 ", "Artist": "Real"})

        assert not parsed["remote"]
        assert (parsed["title"], parsed["artist"]) == ("Solo \u2022", "Real")


class TestPlayerTracking:
    """What the controller derives from the signals BlueZ sends it."""

    def test_a_player_appearing_is_adopted_with_its_track(self):
        avrcp = listening()

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
            "remote": False,
            "is_playing": True,
            "position": 192000,
        }

    def test_a_player_leaving_takes_the_track_with_it(self):
        """The player object disappears when the sender drops AVRCP. A snapshot
        that kept the track would re-publish it on the next reconnection."""
        avrcp = listening()
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Says"), Status=Variant("s", "playing")))
        published = AsyncMock()
        avrcp.set_callbacks(on_update=published)
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
        avrcp = listening()
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Status=Variant("s", status)))

        assert avrcp.snapshot()["is_playing"] is playing

    def test_the_end_of_track_sentinel_is_not_a_playhead(self):
        """BlueZ documents UINT32_MAX on Position as "the track ended". Taken
        literally the player would draw a 49-day progress bar."""
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 0xFFFFFFFF)))

        assert avrcp.snapshot()["position"] is None

    async def test_the_playhead_is_fetched_because_playback_notifies_nothing(self):
        """BlueZ signals Position when it re-anchors on a state change and at no
        other time — three seconds of steady playback carry none. So between
        state changes an explicit Get is the only thing that moves the bar, and
        the message it builds is the contract with BlueZ."""
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 68817)))
        avrcp._on_dbus_message(player_changed(Position=Variant("u", 65681)))

        assert avrcp.snapshot()["position"] == 65681

    async def test_a_read_taken_while_a_state_change_settles_is_discarded(self):
        """The other half, and the exact reading that made the bar stutter:
        traced on the unit, 1 ms after `Status = paused` a Get answered 41552 on
        a track playing at 65 s."""
        avrcp = listening()
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 65294)))
        avrcp.set_callbacks(on_update=AsyncMock())
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

    async def test_a_half_updated_track_is_not_published(self):
        """A skip carries the incoming track's Duration ~600 ms before its Title.
        Traced on the unit: dur 215533 → 211426 with the title still `Canto De
        Ossanha` and the playhead resetting to 31 ms under the old name. Applied,
        the bar resets once for that phantom and again for the real change — the
        0:00 → 0:01 → 0:00 on every Next."""
        avrcp = listening()
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
        avrcp._cancel_settle_flush()

    async def test_holding_a_half_updated_track_is_a_wait_and_not_a_verdict(self, monkeypatch):
        """The hold expires, and the batch after it lands.

        `_is_half_updated` recognises a shape, not an order, and the order it
        was written for is the one traced (Duration first). Nothing rules out
        the mirror — a sender whose Title moves first has its intermediate
        batch applied, since the title differs, and then every batch after it
        matches the half-update shape against a `_track` that is stale by one
        field. Rejected for ever, the true length never lands and the bar wears
        the previous track's for the whole song.

        What is pinned here is only the expiry: past TRACK_SETTLE_TIMEOUT_S a
        held batch is taken rather than refused again. A sender publishes a
        Track batch on every pause, play and skip, so that is the bound on how
        long the wrong length can stand.
        """
        monkeypatch.setattr(avrcp_module, "TRACK_SETTLE_TIMEOUT_S", 0.05)
        avrcp = listening()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Canto De Ossanha", Duration=215533)))

        # The mirror order: the title moves first, carrying the old length.
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Revenants", Duration=215533)))
        # Then the real length — half-updated against what we just applied.
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Revenants", Duration=211426)))
        assert avrcp.snapshot()["duration"] == 215533, "held, as it should be"

        # Nothing else arrives — the wait itself is what publishes it.
        await asyncio.sleep(0.08)

        assert avrcp.snapshot()["duration"] == 211426
        assert avrcp.snapshot()["title"] == "Revenants"

    async def test_the_hold_is_bounded_from_the_first_batch_not_the_last(self, monkeypatch):
        """And re-sending does not extend it: the window is stamped once. Left
        re-stamped, a sender that keeps sending the same half-updated batch
        holds it open indefinitely — which is precisely what
        `_settling`'s own docstring says must not happen."""
        monkeypatch.setattr(avrcp_module, "TRACK_SETTLE_TIMEOUT_S", 0.05)
        avrcp = listening()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Revenants", Duration=215533)))

        for _ in range(4):
            avrcp._on_dbus_message(player_changed(
                Track=track_variant(Title="Revenants", Duration=211426)))
            await asyncio.sleep(0.02)

        assert avrcp.snapshot()["duration"] == 211426
        avrcp._cancel_settle_flush()

    async def test_a_flushed_batch_is_a_correction_and_not_a_track_change(self, monkeypatch):
        """What is held always shares its Title and Artist with what is showing,
        so taking it corrects a field of the current track. Re-anchoring the
        playhead there would restart the bar at 0:00 in the middle of a song for
        a Duration that merely caught up."""
        monkeypatch.setattr(avrcp_module, "TRACK_SETTLE_TIMEOUT_S", 0.05)
        # Frozen: a playing reading ages with the clock, and what is asserted
        # here is a restart to 0:00, not the milliseconds the settle takes.
        monkeypatch.setattr(avrcp_module, "time", SimpleNamespace(monotonic=lambda: 1000.0))
        avrcp = listening()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Revenants", Duration=215533),
            Status=Variant("s", "playing"),
        ))
        avrcp._on_dbus_message(player_changed(Position=Variant("u", 91000)))
        assert avrcp.snapshot()["position"] == 91000

        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Revenants", Duration=211426)))
        await asyncio.sleep(0.08)

        assert avrcp.snapshot()["duration"] == 211426
        assert avrcp.snapshot()["position"] == 91000, "the playhead was re-anchored"

    async def test_a_held_batch_does_not_cross_to_another_sender(self, monkeypatch):
        """One phone leaves mid-skip, the next one connects. The batch held for
        the first is not a correction to the second — flushed onto it, it
        publishes the previous sender's track under the new sender's address.

        Reachable whenever the arriving player's InterfacesAdded carries no
        Track of its own to supersede it, which is exactly the case the AVRCP
        feed is built around: the player object routinely appears a beat before
        its metadata.
        """
        monkeypatch.setattr(avrcp_module, "TRACK_SETTLE_TIMEOUT_S", 0.05)
        avrcp = listening()
        avrcp._on_dbus_message(player_added(
            Track=track_variant(Title="Revenants", Duration=215533)))
        avrcp._on_dbus_message(player_changed(
            Track=track_variant(Title="Revenants", Duration=211426)))
        assert avrcp._pending_track, "nothing was held, so nothing is proven"

        # Measured: a phone leaving removes its player in the same ms.
        avrcp._on_dbus_message(Signal(
            "InterfacesRemoved", [PLAYER_PATH, [MEDIA_PLAYER_IFACE]]
        ))
        other = Signal("InterfacesAdded", [
            "/org/bluez/hci0/dev_11_22_33_44_55_66/player0",
            {MEDIA_PLAYER_IFACE: {}},
        ])
        avrcp._on_dbus_message(other)
        await asyncio.sleep(0.08)

        assert avrcp.snapshot()["title"] is None, "the previous sender's track was flushed"

    def test_a_new_track_does_not_inherit_the_old_playhead(self):
        """BlueZ moves Track and Position in separate messages and they do not
        arrive together, so for a moment the incoming track would be published
        carrying the outgoing one's offset — a bar that reads 1:33 on a track
        that just started. No playhead beats the wrong one: the bar waits."""
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Position=Variant("u", 122700)))
        avrcp.set_callbacks(on_update=AsyncMock())
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
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Track=track_variant(
            Title="Live For You", Artist="Thee Sacred Souls",
            Album="Got a Story to Tell",
        )))

        assert "album_art_url" not in avrcp.snapshot()

    def test_a_foreign_object_is_not_taken_for_a_player(self):
        """Every BlueZ object announces itself on the same signal — the BT
        remote's HID interfaces included."""
        avrcp = listening()

        avrcp._on_dbus_message(Signal("InterfacesAdded", [
            "/org/bluez/hci0/dev_11_22_33_44_55_66",
            {"org.bluez.Device1": {"Connected": Variant("b", True)}},
        ]))

        assert not avrcp.has_player


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
        # One answer for every call: AddMatch ignores the body, GetNameOwner
        # reads bluetoothd's unique name out of it.
        bus.call = AsyncMock(return_value=Mock(message_type=MessageType.METHOD_RETURN,
                                               body=[BLUETOOTHD]))
        bus.add_message_handler = Mock()
        bus.disconnect = Mock()
        return bus

    @pytest.fixture
    def controller(self, bus, monkeypatch):
        connect = AsyncMock(return_value=bus)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=connect)))
        avrcp = AvrcpController()
        avrcp._rescan = AsyncMock()
        return avrcp

    async def test_start_subscribes_to_every_rule_and_installs_the_handler(
        self, controller, bus
    ):
        assert await controller.start() is True

        added = [c.args[0].body[0] for c in bus.call.await_args_list
                 if c.args[0].member == "AddMatch"]
        assert added == list(avrcp_module._MATCH_RULES), (
            "one AddMatch per rule: a rule dropped here is a signal never seen"
        )
        bus.add_message_handler.assert_called_once_with(controller._on_dbus_message)
        await controller.stop()

    async def test_start_adopts_a_player_that_already_exists(self, controller):
        """The backend restarting under a live session sees no change signal."""
        await controller.start()
        controller._rescan.assert_awaited_once()
        await controller.stop()

    async def test_a_refused_addmatch_is_not_reported_as_a_live_listener(
        self, controller, bus
    ):
        bus.call = AsyncMock(return_value=Mock(message_type=MessageType.ERROR,
                                               body=["refused"]))

        # `@handle_errors` turns the raise into the documented fail-open value:
        # the source keeps working as a plain receiver, minus the metadata.
        assert await controller.start() is False
        controller._rescan.assert_not_awaited()

    async def test_stop_cancels_the_loops_start_spawned(self, controller):
        await controller.start()
        tasks = [controller._notify_task, controller._poll_task]
        assert all(tasks), "start spawned no loop — the assertion below is empty"

        await controller.stop()

        assert all(t.cancelled() or t.done() for t in tasks)
        assert controller._notify_task is None
        assert controller._poll_task is None


class TestOnlyBluetoothdIsHeard:
    """Any local account may signal on the system bus, as anyone, and a signal
    addressed to this connection reaches it whatever its match rules say. A
    forged PropertiesChanged put its title on screen and on the Lock Screen,
    and a forged Adapter1 made the source reapply the adapter. Only the
    connection holding org.bluez is listened to, and only the bus daemon says
    which one that is — as for shairport-sync (airplay/remote.py)."""

    @staticmethod
    def _title_from(avrcp: AvrcpController, sender: str, title: str) -> bool:
        """Whether a track signalled by `sender` reached the snapshot."""
        avrcp._on_dbus_message(Signal(
            "PropertiesChanged",
            [MEDIA_PLAYER_IFACE, {"Track": track_variant(Title=title, Artist="A", Album="B")}, []],
            path=PLAYER_PATH, sender=sender,
        ))
        return avrcp.snapshot()["title"] == title

    def test_a_signal_from_another_sender_is_ignored(self):
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))
        assert avrcp.has_player, "bluetoothd's own player was not taken"
        assert self._title_from(avrcp, BLUETOOTHD, "Real")

        assert not self._title_from(avrcp, INTRUDER, "Forged")

        # Nor can the intruder name itself the owner.
        avrcp._on_dbus_message(owner_changed(INTRUDER, sender=INTRUDER))
        assert not self._title_from(avrcp, INTRUDER, "Forged")
        assert self._title_from(avrcp, BLUETOOTHD, "Still real")

    def test_a_forged_adapter_does_not_reapply_the_adapter(self):
        daemon = Mock()
        avrcp = listening()
        avrcp.set_callbacks(on_update=AsyncMock(), on_daemon=daemon)
        adapter = ["/org/bluez/hci0", {"org.bluez.Adapter1": {}}]

        avrcp._on_dbus_message(Signal("InterfacesAdded", adapter, path="/", sender=INTRUDER))
        daemon.assert_not_called()

        avrcp._on_dbus_message(Signal("InterfacesAdded", adapter, path="/"))
        daemon.assert_called_once_with(True)

    def test_the_owner_follows_bluetoothd_across_a_restart(self):
        """systemd's new bluetoothd holds a new unique name: the old one's is
        nobody's now, and nothing is read while the name has no owner."""
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))

        avrcp._on_dbus_message(owner_changed(""))
        assert not avrcp.has_player
        avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))
        assert not avrcp.has_player, "a signal was read while nobody held org.bluez"

        avrcp._on_dbus_message(owner_changed(":1.43"))
        avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))
        assert not avrcp.has_player, "the old bluetoothd's name was still listened to"
        avrcp._on_dbus_message(Signal(
            "InterfacesAdded", [PLAYER_PATH, {MEDIA_PLAYER_IFACE: {}}], sender=":1.43"))
        assert avrcp.has_player

    @staticmethod
    def _bus_answering(owner_reply):
        """The system bus at start: dbus-daemon takes every AddMatch and answers
        GetNameOwner with `owner_reply` (a coroutine function, so it can hang);
        bluetoothd lists no object yet."""
        async def call(msg):
            if msg.member == "GetNameOwner":
                return await owner_reply()
            if msg.member == "GetManagedObjects":
                return Mock(message_type=MessageType.METHOD_RETURN, body=[{}])
            return Mock(message_type=MessageType.METHOD_RETURN, body=[])
        return Mock(add_message_handler=Mock(), remove_message_handler=Mock(),
                    disconnect=Mock(), call=call)

    async def test_start_reads_the_owner(self, monkeypatch):
        """The owner is read at start: bluetoothd is usually up before the
        backend, and announces nothing to a listener that arrives after it."""
        async def owner():
            return Mock(message_type=MessageType.METHOD_RETURN, body=[BLUETOOTHD])
        bus = self._bus_answering(owner)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=AsyncMock(return_value=bus))))
        avrcp = AvrcpController()
        try:
            assert await avrcp.start() is True
            assert self._title_from(avrcp, BLUETOOTHD, "x") is False  # no player yet
            avrcp._on_dbus_message(player_added())
            assert avrcp.has_player, "the owner read at start was not kept"
        finally:
            await avrcp.stop()

    async def test_a_late_owner_reply_does_not_undo_a_newer_owner(self, monkeypatch):
        """bluetoothd restarting while GetNameOwner is on its way: the bus
        daemon's NameOwnerChanged is newer than the reply that follows it.
        Taken anyway, the reply named the dead bluetoothd the owner, and every
        signal from the new one was dropped with nothing said."""
        asked, answer = asyncio.Event(), asyncio.Event()

        async def owner():
            asked.set()
            await answer.wait()
            return Mock(message_type=MessageType.METHOD_RETURN, body=[BLUETOOTHD])
        bus = self._bus_answering(owner)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=AsyncMock(return_value=bus))))
        avrcp = AvrcpController()
        try:
            start = asyncio.create_task(avrcp.start())
            await asyncio.wait_for(asked.wait(), 1.0)
            avrcp._on_dbus_message(owner_changed(":1.43"))
            answer.set()
            assert await asyncio.wait_for(start, 1.0) is True

            avrcp._on_dbus_message(player_added())
            assert not avrcp.has_player, "the stale owner reply replaced the newer owner"
            avrcp._on_dbus_message(Signal(
                "InterfacesAdded", [PLAYER_PATH, {MEDIA_PLAYER_IFACE: {}}], sender=":1.43"))
            assert avrcp.has_player, "the new bluetoothd was not listened to"
        finally:
            await avrcp.stop()

    async def test_an_unanswered_owner_read_fails_the_start(self, monkeypatch):
        """Started with no owner known, the controller drops every bluetoothd
        signal until bluetoothd next restarts — a feed deaf with no word of
        it. The start fails instead, and the source says the feed is
        unavailable."""
        monkeypatch.setattr(bluez_adapter, "DBUS_CALL_TIMEOUT", 0.05)

        async def never():
            await asyncio.Event().wait()
        bus = self._bus_answering(never)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=AsyncMock(return_value=bus))))
        avrcp = AvrcpController()
        try:
            assert await asyncio.wait_for(avrcp.start(), 1.0) is False
        finally:
            await avrcp.stop()


class TestASilentBluetoothd:
    """bluetoothd on the bus but not answering. Every call the controller
    makes runs inside the source's mailbox — a transport command through
    `send`, a playhead read through `refresh_metadata` — so an unbounded one
    held every message behind it (the link, the stream) until the next STOP."""

    @pytest.fixture
    def silent(self, monkeypatch):
        monkeypatch.setattr(bluez_adapter, "DBUS_CALL_TIMEOUT", 0.05)
        avrcp = listening()
        avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))

        async def never_answers(msg):
            await asyncio.Event().wait()

        avrcp._bus = Mock(call=never_answers)
        return avrcp

    async def test_a_command_gives_up_within_the_bound(self, silent):
        assert await asyncio.wait_for(silent.send("Pause"), 1.0) is False

    async def test_a_playhead_read_gives_up_within_the_bound(self, silent):
        assert await asyncio.wait_for(silent.read_position(), 1.0) is False

    async def test_the_silence_is_said_once(self, silent, caplog):
        """The position poll asks every few seconds while playing: one line
        per outage, not one per poll."""
        with caplog.at_level(logging.DEBUG, logger="source.bluetooth.avrcp"):
            for _ in range(3):
                await asyncio.wait_for(silent.read_position(), 1.0)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1, [r.message for r in warnings]
        assert "did not answer" in warnings[0].message

    async def test_the_bus_daemon_answering_does_not_end_bluetoothds_outage(
        self, silent, caplog,
    ):
        """The outage is bluetoothd's: dbus-daemon answering an AddMatch in
        between neither says bluetoothd "answers again" nor makes its next
        silence a second warning."""
        await asyncio.wait_for(silent.read_position(), 1.0)
        bluetoothd_silent = silent._bus.call

        async def daemon_answers(msg):
            return Mock(message_type=MessageType.METHOD_RETURN, body=[])
        silent._bus = Mock(call=daemon_answers)
        with caplog.at_level(logging.DEBUG, logger="source.bluetooth.avrcp"):
            caplog.clear()
            await silent._call_daemon("AddMatch", "type='signal'")
            silent._bus = Mock(call=bluetoothd_silent)
            await asyncio.wait_for(silent.read_position(), 1.0)

        said = [r.message for r in caplog.records if r.levelno >= logging.INFO]
        assert said == [], said

    async def test_a_second_outage_is_said_again(self, silent, caplog):
        """Once per outage, not once per process: bluetoothd answering again
        ends the first, and its next silence is a new one, warned like it."""
        bluetoothd_silent = silent._bus.call

        async def answers(msg):
            return Mock(message_type=MessageType.METHOD_RETURN, body=[Variant("u", 1000)])
        with caplog.at_level(logging.DEBUG, logger="source.bluetooth.avrcp"):
            await asyncio.wait_for(silent.read_position(), 1.0)
            silent._bus = Mock(call=answers)
            assert await asyncio.wait_for(silent.read_position(), 1.0) is True
            silent._bus = Mock(call=bluetoothd_silent)
            await asyncio.wait_for(silent.read_position(), 1.0)

        said = [(r.levelno, r.message) for r in caplog.records if r.levelno >= logging.INFO]
        assert [level for level, _ in said] == [logging.WARNING, logging.INFO, logging.WARNING], said
        assert "answers again" in said[1][1]

    @staticmethod
    def _connect_to(monkeypatch, bluez) -> None:
        """The next `start` connects to a bus whose daemon answers (AddMatch,
        GetNameOwner naming bluetoothd) and whose bluetoothd answers through
        `bluez`, a coroutine function that may never return."""
        async def call(msg):
            if msg.destination == DBUS_DAEMON:
                return Mock(message_type=MessageType.METHOD_RETURN, body=[BLUETOOTHD])
            return await bluez(msg)
        bus = Mock(add_message_handler=Mock(), remove_message_handler=Mock(),
                   disconnect=Mock(), call=call)
        monkeypatch.setattr(avrcp_module, "MessageBus",
                            Mock(return_value=Mock(connect=AsyncMock(return_value=bus))))

    async def test_a_start_against_a_silent_bluetoothd_still_returns(self, monkeypatch):
        """`start` reads the players already on the bus inline, and the
        source's start awaits it: a GetManagedObjects nothing answers held
        the Bluetooth source's start for as long as bluetoothd stayed silent."""
        monkeypatch.setattr(bluez_adapter, "DBUS_CALL_TIMEOUT", 0.05)

        async def never(msg):
            await asyncio.Event().wait()
        self._connect_to(monkeypatch, never)
        avrcp = AvrcpController()
        try:
            assert await asyncio.wait_for(avrcp.start(), 1.0) is True
            assert not avrcp.has_player
        finally:
            await avrcp.stop()

    async def test_an_outage_across_a_restart_is_said_again(self, silent, monkeypatch, caplog):
        """`stop` forgets the outage: the same controller started again on a
        bluetoothd still silent warns once more, not at debug — its first
        unanswered call is the GetManagedObjects of the start itself."""
        async def never(msg):
            await asyncio.Event().wait()

        with caplog.at_level(logging.DEBUG, logger="source.bluetooth.avrcp"):
            await asyncio.wait_for(silent.read_position(), 1.0)
            await silent.stop()

            self._connect_to(monkeypatch, never)
            try:
                assert await asyncio.wait_for(silent.start(), 1.0) is True
            finally:
                await silent.stop()

        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 2, warnings
        assert "GetManagedObjects" in warnings[1], warnings


async def test_a_failed_re_read_is_said_and_the_next_one_still_runs(monkeypatch, caplog):
    """The re-reads after a command were wrapped for cancellation alone: a
    D-Bus error on one ended the schedule with no line, and the bar stayed
    wrong until the next poll with nothing saying why."""
    monkeypatch.setattr(avrcp_module, "COMMAND_REREAD_DELAYS", (0.0, 0.0, 0.0))
    avrcp = listening()
    avrcp._on_dbus_message(player_added(Status=Variant("s", "playing")))
    position = Mock(message_type=MessageType.METHOD_RETURN, body=[Variant("u", 4200)])
    avrcp._bus = Mock(call=AsyncMock(side_effect=[RuntimeError("device gone"), position, position]))

    with caplog.at_level(logging.ERROR, logger="source.bluetooth.avrcp"):
        await avrcp._reread_loop()

    assert avrcp._bus.call.await_count == 3
    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "re-read failed" in errors[0], errors
