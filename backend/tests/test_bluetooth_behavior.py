"""The Bluetooth source's lifecycle, exposure, link and player, driven through
the world the unit was measured in (tests/bluetooth_world.py).

The companion of test_bluetooth_sessions.py, which pins how the session
follows the phone and the daemons. What is pinned here is the rest of what a
person or a second phone can see: which units systemd was asked for, what
BlueZ was left exposing and blocking, which peer it was told to drop, what
the phone's player was told, and what the wire carries about the track, its
playhead and its cover.
"""
import logging

import pytest

from backend.tests.bluetooth_world import (
    APLAY_UNIT, BLUEALSA_UNIT, BLUEZ_UNIT, IPHONE, MAC_MINI, PIXEL, SAYS, UNKNOWN_DEMO,
    Bluetooth,
)


@pytest.fixture
async def bt(monkeypatch):
    world = Bluetooth(monkeypatch)
    yield world
    await world.source.shutdown()


async def playing_iphone(bt, track=SAYS):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, track, status="playing", position=0)


SENDERS = ["Mac mini de Léo", "Pixel 8", "iPhone de Léo"]


# === Start and stop ===

async def test_select_brings_the_stack_up_and_offers_the_appliance(bt):
    """Blocked is durable per-device state in BlueZ: senders the last stop
    blocked are still blocked when the source comes back, and the start is
    the only thing that unblocks them. Without it no phone could connect to a
    source the screen says is ready."""
    for phone in (IPHONE, MAC_MINI, PIXEL):
        bt.bluez.devices[phone.path]["Blocked"] = True

    await bt.select()

    assert bt.state()["service"] == "running" and bt.session() is None
    assert all(bt.units[unit] for unit in (BLUEZ_UNIT, BLUEALSA_UNIT, APLAY_UNIT))
    assert bt.exposed() and bt.bluez.accepts_audio()
    assert bt.blocked() == []


async def test_a_unit_that_will_not_start_fails_the_start_and_leaves_the_appliance_closed(bt):
    """A start that cannot bring the player up must not settle in a state that
    still accepts a phone the appliance cannot play."""
    bt.failing_units.add(APLAY_UNIT)

    await bt.select()

    assert bt.state()["service"] == "failed"
    assert not bt.exposed()
    assert bt.blocked() == SENDERS


async def test_a_start_failing_after_the_adapter_opened_closes_it_again(bt):
    """A failed service is neither started nor stopped, and nothing revisits it. The
    BlueALSA monitor starts after the adapter was configured and opened, so
    without the close on failure the appliance stays discoverable and every
    paired sender unblocked for as long as the service stays failed."""
    bt.bluez.cli_missing = True

    await bt.select()

    assert bt.state()["service"] == "failed"
    assert not bt.exposed()
    assert bt.blocked() == SENDERS


async def test_leaving_the_source_stops_what_it_started(bt):
    """The monitor left running would keep a `bluealsa-cli` child per
    selection, and the agent left registered would keep accepting pairings
    for a source that is off."""
    await playing_iphone(bt)

    await bt.deselect()

    assert not any(bt.units[unit] for unit in (BLUEZ_UNIT, BLUEALSA_UNIT, APLAY_UNIT))
    assert bt.bluez.monitor.returncode is not None
    assert bt.bluez.agents == []


async def test_leaving_the_source_keeps_bluetoothd_for_the_hid_remote(monkeypatch):
    """The Bluetooth remote pairs over the same bluetoothd: stopping it with the
    source would kill the remote the moment the person switches source with
    it. The daemon staying up is also why the senders must be blocked — the
    adapter stays powered and a paired phone could dial in."""
    bt = Bluetooth(monkeypatch, settings={"hardware.bt_remote": {"enabled": True}})
    try:
        await playing_iphone(bt)

        await bt.deselect()

        assert bt.units[BLUEZ_UNIT]
        assert not bt.units[BLUEALSA_UNIT] and not bt.units[APLAY_UNIT]
        assert not bt.exposed()
        assert bt.blocked() == SENDERS
    finally:
        await bt.source.shutdown()


# === Exposure ===

async def test_a_phone_holding_the_link_blocks_every_other_sender_until_it_leaves(bt):
    """Discoverable off stops a new device finding Milō, not a paired one
    dialling its address: only Blocked does. The holder is exempt — blocking
    it would drop the audio it is playing."""
    await playing_iphone(bt)
    assert bt.blocked() == ["Mac mini de Léo", "Pixel 8"]

    await bt.pcm_removed(IPHONE)

    assert bt.blocked() == []


async def test_the_appliance_hides_before_the_phone_is_published(bt):
    """Hide first: the appliance has a sender, so it must stop offering itself
    to a second one rather than kick it afterwards. The session naming the
    phone is what a second person looks at before trying their own."""
    await bt.select()

    await bt.pcm_added(IPHONE)

    with_session = [
        exposed for envelope, exposed in zip(bt.recorder.envelopes, bt.exposed_at)
        if (envelope["category"], envelope["type"]) == ("source", "state")
        and envelope["data"]["session"] is not None
    ]
    assert with_session and not any(with_session)


async def test_an_adapter_refusing_a_property_is_reported_not_taken_for_applied(bt, caplog):
    """bluetoothctl exited 0 whatever happened, which is how a unit came to
    report a configured adapter that was not pairable at all."""
    bt.bluez.adapter_refuses.add("Pairable")

    with caplog.at_level(logging.ERROR, logger="source.bluetooth"):
        await bt.select()

    assert "exposure not applied" in caplog.text


async def test_an_adapter_that_will_not_power_is_reported(bt, caplog):
    bt.bluez.adapter_refuses.add("Powered")

    with caplog.at_level(logging.WARNING, logger="source.bluetooth"):
        await bt.select()

    assert "Adapter configuration failed" in caplog.text


# === The link ===

async def test_disconnect_with_no_phone_is_refused_and_bluez_is_asked_nothing(bt):
    await bt.select()

    result = await bt.try_command("disconnect")

    assert not result.get("success")
    assert bt.bluez.disconnects == []


async def test_a_phone_that_keeps_the_link_is_reported_by_name(bt):
    """This text reaches the UI, and the sender's name is the whole of its
    usefulness: the person is looking at a room with two paired Apple
    devices in it. The phone that stayed still holds the source."""
    await playing_iphone(bt)
    bt.bluez.keeps_link.add(IPHONE.path)

    result = await bt.try_command("disconnect")

    assert not result.get("success")
    assert "iPhone de Léo" in result["error"]
    assert bt.active() and bt.session()["senders"] == ["iPhone de Léo"]


async def test_the_disconnect_is_traced_at_info_naming_the_sender(bt, caplog):
    """`command()` traces at debug, so without this a disconnect leaves no mark
    in the journal — which is how a button that worked and a link taken back
    seconds later by a second paired device became indistinguishable."""
    await playing_iphone(bt)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="source.bluetooth"):
        await bt.command("disconnect")

    assert any(
        "iPhone de Léo" in r.getMessage() and IPHONE.address in r.getMessage()
        for r in caplog.records if r.levelno >= logging.INFO
    )


async def test_disconnect_works_on_a_phone_with_no_avrcp_player(bt):
    """It is the one command that does not go through the player, and plenty
    of senders publish none."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)

    await bt.command("disconnect")

    assert bt.bluez.disconnects == [MAC_MINI.address]
    assert bt.bluez.devices[MAC_MINI.path]["Connected"] is False


async def test_the_phone_holding_the_link_announced_again_is_not_kicked(bt):
    """BlueALSA re-announces a PCM on a codec change; kicking there would drop
    the phone that is playing."""
    await playing_iphone(bt)

    await bt.pcm_announced_again(IPHONE)

    assert bt.bluez.disconnects == []
    assert bt.bluez.devices[IPHONE.path]["Connected"] is True
    assert bt.active() and bt.playing()


async def test_a_turned_away_phone_leaving_does_not_end_the_session(bt):
    """The second phone's PCM goes as it was dropped; acting on that departure
    would clear the card for the phone still playing."""
    await playing_iphone(bt)
    await bt.pcm_added(PIXEL)

    await bt.pcm_removed(PIXEL)

    assert bt.bluez.disconnects == [PIXEL.address]
    assert bt.active() and bt.playing()
    assert bt.session()["senders"] == ["iPhone de Léo"]


async def test_a_phone_back_after_leaving_inherits_nothing_from_its_last_session(bt):
    """A phone reconnecting before its AVRCP player is back would otherwise
    re-publish the previous track and its cover. `is_playing` is what the
    rotary and the BT remote read to choose pause or resume: a stale True made
    the first press after a reconnect pause a phone that was not playing."""
    await playing_iphone(bt)
    assert bt.session()["artwork"]
    await bt.player_removed()
    await bt.pcm_removed(IPHONE)
    assert bt.source.is_playing is False

    await bt.pcm_added(IPHONE)

    assert bt.active()
    assert bt.session()["title"] is None and bt.session()["artwork"] is None
    assert bt.phase() == "connected" and bt.source.is_playing is False


async def test_a_phone_leaving_during_a_multiroom_toggle_is_seen_leaving(bt):
    """The toggle holds the source while the writer is down; a departure then
    waits its turn and must still end the session once the writer is back."""
    await playing_iphone(bt)

    await bt.reroute(during=lambda: bt.bluez.pcm_removed(IPHONE))

    assert not bt.active()
    assert bt.exposed()


async def test_a_writer_that_will_not_come_back_after_a_toggle_is_reported(bt, caplog):
    """The mode switch itself stands, so the failure is not raised — but it is
    reported, and the screen is not left on the STARTING the toggle put up."""
    await playing_iphone(bt)
    bt.failing_units.add(APLAY_UNIT)

    with caplog.at_level(logging.WARNING):
        await bt.reroute()

    assert "re-acquire returned False" in caplog.text
    assert bt.state()["service"] == "running" and not bt.state()["switching"]
    assert bt.active()


# === The phone's player ===

async def test_each_transport_command_reaches_the_player_under_its_avrcp_name(bt):
    """Milō's vocabulary is canonical across sources, AVRCP's is its own: the
    mapping keeps `Previous` out of the API and `prev` out of the D-Bus call."""
    await playing_iphone(bt)

    for command in ("pause", "resume", "next", "prev"):
        await bt.command(command)

    assert bt.bluez.transport == ["Pause", "Play", "Next", "Previous"]


async def test_a_command_to_a_phone_with_no_player_says_why(bt):
    """`controls` leaves the transport out for a sender that publishes no
    player, but the rotary and the BT remote send one anyway: it must say why
    rather than fail silently."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)

    result = await bt.try_command("pause")

    assert not result.get("success")
    assert "no AVRCP player" in result["error"]
    assert bt.bluez.transport == []


async def test_a_command_the_phone_refuses_fails_alone(bt):
    """An AVRCP target answers NotSupported per method — a sender may take
    Pause and refuse Next — so a refusal is that command's failure, not the
    source's."""
    await playing_iphone(bt)
    bt.bluez.refused_methods.add("Next")

    result = await bt.try_command("next")
    await bt.command("pause")

    assert not result.get("success")
    assert "'next' was refused" in result["error"]
    assert bt.bluez.transport == ["Pause"]
    assert bt.active() and bt.errors() == []


async def test_a_player_with_nothing_to_say_is_still_announced(bt):
    """The transport buttons are drawn from `controls`, and the screensaver
    can only tell a pause from a sender that never reports playback by reading
    them: a Mac registers a player that serves no track and no Status for
    100 s, and its transport works meanwhile (E33)."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)

    await bt.player_added(MAC_MINI, None, status="", position=0)

    assert bt.state()["controls"] == ["resume", "next", "prev", "disconnect"]
    assert bt.session()["title"] is None


async def test_a_phone_with_no_player_offers_only_the_disconnect(bt):
    """A transport drawn for a phone with no AVRCP player is a row of buttons
    that each answer "no AVRCP player"; the disconnect is the one command that
    does not go through the player."""
    await bt.select()

    await bt.pcm_added(MAC_MINI)

    assert bt.state()["controls"] == ["disconnect"]


async def test_a_player_leaving_with_nothing_else_to_say_is_published(bt):
    """A trackless, paused player looks the same present or gone on every other
    field. Its departure must still be published, or the transport stays on
    screen for the rest of the link and the screensaver never arms again."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)
    await bt.player_added(MAC_MINI, None, status="paused", position=0)
    assert bt.state()["controls"] == ["resume", "next", "prev", "disconnect"]

    await bt.player_removed()

    assert bt.active() and bt.published()[-1]["controls"] == ["disconnect"]


# === The playhead ===

async def test_steady_playback_publishes_nothing_and_a_moved_playhead_only_its_position(bt):
    """The clients move the bar from the anchor themselves: a state (or even a
    position) per poll would push the whole thing to every client every five
    seconds for nothing. A playhead that left the anchor — a seek on the phone
    that BlueZ never signals, caught by the poll — is a `source/position`
    alone, and puts the bar where BlueZ now is."""
    await playing_iphone(bt)
    published = len(bt.published())

    await bt.elapse(20)

    assert len(bt.published()) == published and bt.positions() == []

    bt.sender_seeks(200_000)
    await bt.elapse(5)

    assert len(bt.published()) == published
    assert len(bt.positions()) == 1
    assert bt.position_ms() == bt.bluez.position()


async def test_an_unrelated_event_between_polls_does_not_move_the_bar(bt):
    """The playhead is read every five seconds, and every feed burst looks at
    it again. Read as it was taken, a reading four seconds old is four seconds
    behind the anchor — past the tolerance — so a BlueALSA stream flip between
    two polls pulled the bar back, and the next poll pushed it forward again."""
    await playing_iphone(bt)
    await bt.elapse(5)
    positions = len(bt.positions())

    await bt.elapse(4)
    await bt.stream(IPHONE, True)

    assert len(bt.positions()) == positions


async def test_a_state_request_hands_back_the_live_playhead(bt):
    """Nothing notifies a moved playhead between polls, so the anchor is
    whatever the last read captured. `GET /api/audio/state` and the WS
    handshake re-read it first: a stale anchor there is a progress bar that
    jumps on every page load."""
    await playing_iphone(bt)
    await bt.elapse(2)
    bt.sender_seeks(90_000)
    stale = bt.position_ms()

    assert await bt.machine.refresh_active_view() is True

    assert bt.position_ms() == bt.bluez.position() != stale


async def test_a_state_request_without_a_player_has_nothing_to_read(bt):
    """A False here keeps the stored view; a True would have the state machine
    copy the source's over it for nothing."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)

    assert await bt.machine.refresh_active_view() is False


# === The cover ===

async def test_the_cover_is_the_albums_and_absent_until_it_is_found(bt):
    """AVRCP carries no image. An album has one cover where a track sits on a
    dozen compilations, so the album is what is looked up; and until iTunes
    answers there is no artwork at all — an empty one would leave the player
    waiting on an image forever."""
    bt.hold_itunes()
    await playing_iphone(bt)
    assert bt.session()["artwork"] is None

    await bt.release_itunes()

    assert "nils/spaces" in bt.session()["artwork"]


async def test_a_resolved_cover_survives_the_position_poll_and_a_pause(bt):
    """The poll replaces the player's snapshot wholesale and the snapshot has
    no artwork: a cover kept in it would be wiped on the next tick and the
    player would flash its glyph back every five seconds."""
    await playing_iphone(bt)
    cover = bt.session()["artwork"]
    assert cover

    await bt.elapse(20)
    await bt.player_changed({"Status": "paused"})

    assert bt.phase() == "paused" and bt.session()["artwork"] == cover


async def test_a_new_track_does_not_inherit_the_previous_cover(bt):
    await playing_iphone(bt)
    assert bt.session()["artwork"]

    await bt.player_changed({"Track": UNKNOWN_DEMO}, {"Position": 0})
    await bt.elapse(5)

    assert bt.session()["title"] == UNKNOWN_DEMO["Title"]
    assert bt.session()["artwork"] is None


async def test_a_cover_answered_after_the_track_moved_on_is_dropped(bt):
    """The lookup is a network round trip; the phone can change track during
    it, and the answer then describes a track no longer on screen."""
    bt.hold_itunes()
    await playing_iphone(bt)
    await bt.player_changed({"Track": UNKNOWN_DEMO}, {"Position": 0})
    await bt.elapse(5)

    await bt.release_itunes()

    assert bt.session()["title"] == UNKNOWN_DEMO["Title"]
    assert bt.session()["artwork"] is None
