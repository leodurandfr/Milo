"""Bluetooth sessions, driven through BlueZ and BlueALSA as the unit was
measured to run them (tests/bluetooth_world.py; docs: source architecture,
phase 4).

What is asserted is what the wire says, what Milō left BlueZ in, and which
units it asked systemd for, after a phone or a daemon did something. Each gap
test was seen red on the code before phase 4 for the reason its docstring
gives.
"""
import asyncio
import logging

import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.bluetooth_world import (
    APLAY_UNIT, FEELING_GOOD, IPHONE, MAC_MINI, PIXEL, SAYS, Bluetooth,
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


# === The phase comes from the phone's player and its stream ===

async def test_a_phone_playing_is_playing(bt):
    await playing_iphone(bt)
    assert bt.active() and bt.playing()
    assert bt.meta()["title"] == "Says" and bt.meta()["device_name"] == "iPhone de Léo"


async def test_a_pause_on_the_phone_is_a_pause_and_the_link_stays(bt):
    """Measured: a pause leaves the stream running (the Mac streams silence),
    and the iPhone kept the link through 7 min 30 of pause. The pause is the
    player's Status alone, and nothing ends the session for it."""
    await playing_iphone(bt)
    await bt.player_changed({"Status": "paused"}, {"Position": 12000})
    await bt.elapse(3600)
    assert bt.active() and not bt.playing()
    assert bt.errors() == []


async def test_a_stream_that_stops_under_a_playing_player_is_not_playing(bt):
    """E76, measured: the Mac switching its output to its own speakers keeps
    the link, the PCM and a player that says `playing` — BlueALSA alone says
    the stream stopped. The old code showed "playing" on silence for as long
    as the link lasted."""
    await playing_iphone(bt)
    await bt.stream(IPHONE, False)
    assert bt.active() and not bt.playing()
    await bt.stream(IPHONE, True)
    assert bt.playing()


async def test_a_sender_with_no_play_state_yet_is_connected_not_playing(bt):
    """Measured: the Mac publishes no Status for 100 s after connecting."""
    await bt.select()
    await bt.pcm_added(MAC_MINI)
    await bt.player_added(MAC_MINI, None, status="", position=0)
    assert bt.active() and not bt.playing()
    await bt.player_changed({"Status": "playing"})
    assert bt.playing()


async def test_a_long_pause_keeps_the_link(bt):
    """KEEP_WHILE_LINKED (owner decision): no auto-stop, whatever the delay."""
    await playing_iphone(bt)
    await bt.player_changed({"Status": "paused"})
    await bt.elapse(7200)
    assert bt.active()
    assert ("stop", "milo-bluealsa.service") not in bt.unit_calls


# === The player belongs to the phone holding the link (E30) ===

async def test_a_player_outliving_its_link_is_not_published(bt):
    """E30, measured once: the player outlived its A2DP link by 1.0 s, and the
    READY published meanwhile carried `has_avrcp: true`."""
    await playing_iphone(bt)
    await bt.pcm_removed(IPHONE)
    assert not bt.active()
    assert not bt.meta().get("has_avrcp")


async def test_another_phones_player_never_names_the_one_connected(bt):
    """E30: the player a first phone left behind was stored and published for
    the next phone to connect, and took its transport commands."""
    await bt.select()
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.pcm_added(PIXEL)
    assert bt.active() and bt.meta()["device_name"] == "Pixel 8"
    assert bt.meta().get("title") is None
    assert not bt.meta().get("has_avrcp")
    result = await bt.try_command("pause")
    assert not result.get("success")
    assert bt.bluez.transport == []


async def test_the_player_arriving_before_the_link_is_the_phones(bt):
    """Measured on the iPhone: the player and the PCM come in either order."""
    await bt.select()
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.pcm_added(IPHONE)
    assert bt.playing() and bt.meta()["title"] == "Says"


# === bluetoothd (E31, E75) ===

async def test_bluetoothd_killed_forgets_the_player(bt):
    """E31, measured: SIGKILL removes nothing from the bus; BlueALSA drops the
    PCM 7 ms later, but the old code kept the dead daemon's player for good
    (`has_avrcp: true` in READY)."""
    await playing_iphone(bt)
    await bt.bluetoothd_killed_and_restarted()
    assert not bt.active()
    assert not bt.meta().get("has_avrcp")


async def test_bluetoothd_killed_under_a_session_is_a_death_with_a_banner(bt):
    """E31: the audio stopped because a daemon died, not because the phone left."""
    await playing_iphone(bt)
    await bt.bluetoothd_killed_and_restarted()
    assert bt.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_restarted_bluetoothd_is_opened_and_given_the_agent_again(bt):
    """E75, measured twice: the bluetoothd systemd brings back is closed and has
    no agent, so it refuses every audio connection ("Authentication attempt
    without agent") while the screen says ready."""
    await bt.select()
    await bt.bluetoothd_restarts()
    assert bt.exposed()
    assert bt.bluez.accepts_audio()


async def test_a_killed_bluetoothd_is_opened_and_given_the_agent_again(bt):
    await playing_iphone(bt)
    await bt.bluetoothd_killed_and_restarted()
    assert bt.exposed()
    assert bt.bluez.accepts_audio()


async def test_a_phone_back_after_bluetoothd_restarted_clears_the_banner(bt):
    await playing_iphone(bt)
    await bt.bluetoothd_killed_and_restarted()
    await bt.pcm_added(IPHONE)
    assert bt.active()
    assert bt.cleared() == 1


async def test_a_phone_back_after_bluetoothd_died_inherits_nothing_from_it(bt):
    """The dead daemon's player went unannounced (E31): the phone linking again
    before its new player is published must not be shown the old track."""
    await playing_iphone(bt)
    await bt.bluetoothd_killed_and_restarted()
    await bt.pcm_added(IPHONE)
    assert bt.active()
    assert bt.meta().get("title") is None and not bt.meta().get("has_avrcp")


async def test_a_bluetoothd_restarted_on_purpose_is_never_a_death(bt):
    """A `systemctl restart bluetooth` whose name change is heard before the
    PCM leaves: systemd says the old process ended as asked (Result success)."""
    await playing_iphone(bt)
    await bt.bluetoothd_restarted_by_hand_name_first()
    assert not bt.active()
    assert bt.errors() == []


async def test_a_new_bluetoothd_still_powering_up_is_configured_anyway(bt):
    """The new adapter is announced while AutoEnable may still be powering it:
    BlueZ answers Busy to a write meanwhile."""
    await bt.select()
    bt.bluez.adapter_busy = 2
    await bt.bluetoothd_restarts()
    assert bt.exposed() and bt.bluez.accepts_audio()
    assert bt.errors() == []


async def test_a_new_bluetoothd_that_cannot_be_configured_is_reported(bt):
    """E75's failure is a closed appliance under a screen that says ready:
    when it cannot be undone, it is said."""
    await bt.select()
    bt.bluez.adapter_busy = 100
    await bt.bluetoothd_restarts()
    assert bt.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]


async def test_a_name_change_not_sent_by_the_bus_itself_is_ignored(bt):
    """Only the bus daemon speaks for a name leaving: any local client could
    otherwise end the session with a banner."""
    from types import SimpleNamespace
    from dbus_next.constants import MessageType
    from backend.tests.golden.harness import settle
    await playing_iphone(bt)
    forged = SimpleNamespace(
        message_type=MessageType.SIGNAL, member="NameOwnerChanged", path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus", sender=":1.666",
        body=["org.bluez", bt.bluez.bluez_owner, ""],
    )
    for handler in list(bt.bluez.handlers):
        handler(forged)
    await settle()
    assert bt.active() and bt.meta().get("has_avrcp")
    assert bt.errors() == []


async def test_configuring_a_new_bluetoothd_never_holds_the_source(bt):
    """Each adapter write can take up to its D-Bus timeout on a daemon still
    coming up, and a few are retried: none of it may hold the source's mailbox."""
    await bt.select()
    bt.bluez.adapter_gate = asyncio.Event()
    await bt.bluetoothd_restarts()
    result = await asyncio.wait_for(bt.source.command("disconnect", None), 1.0)
    assert result.get("error") == "No device connected"
    bt.bluez.adapter_gate.set()
    from backend.tests.golden.harness import settle
    await settle()
    assert bt.exposed() and bt.bluez.accepts_audio()


async def test_a_clean_bluetoothd_restart_is_the_phone_leaving(bt):
    """Measured: a clean stop removes the player and the PCM itself first."""
    await playing_iphone(bt)
    await bt.bluetoothd_restarts()
    assert not bt.active()
    assert bt.errors() == []


# === BlueALSA (E32) ===

async def test_bluealsa_killed_ends_the_session_with_a_banner(bt):
    """E32, measured: the monitor prints `ServiceStopped` and keeps running, so
    the old code kept the phone's name on screen and every other phone
    blocked, forever."""
    await playing_iphone(bt)
    await bt.bluealsa_killed()
    assert not bt.active()
    assert bt.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_bluealsa_back_gets_its_player_back(bt):
    """E32, measured: systemd restarts BlueALSA but not bluealsa-aplay, which
    `BindsTo=` it — a phone reconnecting would have been silent."""
    await playing_iphone(bt)
    await bt.bluealsa_killed()
    await bt.bluealsa_restarted_by_systemd()
    assert bt.units[APLAY_UNIT]
    await bt.pcm_added(IPHONE)
    assert bt.active() and bt.cleared() == 1


async def test_a_writer_that_will_not_come_back_is_reported(bt):
    """BlueALSA back but bluealsa-aplay refusing to start: a phone linking now
    would be silent, and the screen says why."""
    await playing_iphone(bt)
    await bt.bluealsa_killed()
    bt.failing_units.add(APLAY_UNIT)
    await bt.bluealsa_restarted_by_systemd()
    assert bt.errors() == [SourceErrorReason.STREAM_DISCONNECTED, SourceErrorReason.SERVICE_UNREACHABLE]


async def test_bluealsa_killed_under_an_unwatched_session_still_ends_it(bt):
    """The pid read when the session opens can be missing (systemd slow on a
    busy card): the monitor's `ServiceStopped` is then the only word of the
    death, and it is enough."""
    bt.pid_unreadable = True
    await playing_iphone(bt)
    await bt.bluealsa_killed()
    assert not bt.active()
    assert bt.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_backend_restart_under_a_phone_neither_banners_nor_opens_the_appliance(bt):
    """A backend restart stops BlueALSA first, while the backend runs, and
    bluetooth.service survives it: an appliance reopened then would stay
    discoverable, with every paired phone unblocked, and no source selected."""
    await playing_iphone(bt)
    await bt.bluealsa_stopped_by_systemd()
    assert not bt.active()
    assert bt.errors() == []
    assert not bt.exposed()
    assert all(bt.bluez.devices[p.path]["Blocked"] for p in (MAC_MINI, PIXEL))


async def test_a_backend_restart_logs_no_error(bt, caplog):
    """The monitor child gets the backend's SIGTERM with everything else in its
    cgroup: that is the backend stopping, not a feed lost (smoke check: no new
    ERROR after a restart)."""
    await playing_iphone(bt)
    await bt.bluealsa_stopped_by_systemd()
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


async def test_the_appliance_reopens_when_bluealsa_is_back(bt):
    """With no BlueALSA there is no A2DP sink: accepting a phone then would be
    a promise nothing keeps. Back, the appliance offers itself again."""
    await playing_iphone(bt)
    await bt.bluealsa_killed()
    assert not bt.exposed()
    await bt.bluealsa_restarted_by_systemd()
    assert bt.exposed()


async def test_a_monitor_terminated_while_bluealsa_runs_is_still_a_lost_feed(bt):
    """SIGTERM is the backend going away only when BlueALSA is being stopped
    with it; any other one leaves the feed dead, and that is said."""
    from backend.tests.golden.harness import settle
    await playing_iphone(bt)
    bt.bluez.monitor.returncode = -15
    bt.bluez.monitor.stdout.lines.put_nowait(b"")
    await settle()
    assert bt.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]


async def test_the_monitor_dying_is_reported_on_screen_and_changes_nothing(bt):
    """E32: the feed's death was logged under `source.bluetooth`, a logger that
    never reaches the banner. Reported once; the state is left alone."""
    await playing_iphone(bt)
    await bt.monitor_dies()
    assert bt.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]
    assert bt.active()


# === The link, the commands, the reroute ===

async def test_turning_a_phone_away_never_holds_the_holders_commands(bt):
    """A Disconnect was measured at ~3 s; the phone holding the link must not
    wait behind the one being turned away."""
    await playing_iphone(bt)
    bt.bluez.disconnect_gate = asyncio.Event()
    await bt.pcm_added(PIXEL)
    result = await asyncio.wait_for(bt.source.command("pause", None), 1.0)
    assert result.get("success")
    bt.bluez.disconnect_gate.set()


async def test_a_second_app_player_coming_and_going_keeps_the_phones_player(bt):
    """Measured on the iPhone: two players on one phone ("Musique" and
    "Spotify"). A second one appearing and leaving must not cost the phone
    the player it is playing from."""
    await playing_iphone(bt)
    other = bt.bluez.other_player_added(IPHONE, "player1", "stopped")
    from backend.tests.golden.harness import settle
    await settle()
    assert bt.playing() and bt.meta().get("has_avrcp")
    bt.bluez.other_player_removed(other)
    await settle()
    assert bt.playing() and bt.meta().get("has_avrcp")
    await bt.command("pause")
    assert bt.bluez.transport == ["Pause"]


async def test_the_followed_player_leaving_hands_over_to_the_one_left(bt):
    """The player followed goes while the phone's other app still publishes
    one, and nothing names it: it is the phone's player now."""
    await playing_iphone(bt)
    bt.bluez.object_manager_gate = asyncio.Event()
    bt.bluez.other_player_added(IPHONE, "player2", "playing")
    bt.bluez.player_removed()
    from backend.tests.golden.harness import settle
    await settle()
    assert bt.active() and bt.meta().get("has_avrcp")
    bt.bluez.object_manager_gate.set()


async def test_a_phone_switching_apps_never_shows_a_moment_without_a_player(bt):
    """Measured on the unit: the iPhone adds its Spotify player, BlueZ names it
    the live one, then the Music player goes — within 4 ms. Following the new
    one only after the old one left published `has_avrcp: false` for 8 ms."""
    await playing_iphone(bt)
    before = len(bt.published())
    bt.bluez.object_manager_gate = asyncio.Event()
    other = bt.bluez.other_player_added(IPHONE, "player2", "playing")
    bt.bluez.control_names(IPHONE, other)
    bt.bluez.player_removed()
    from backend.tests.golden.harness import settle
    await settle()
    bt.bluez.object_manager_gate.set()
    await settle()
    after = bt.published()[before:]
    assert after, "the switch published nothing — the assertion below would be empty"
    assert all(p.get("has_avrcp") for p in after)


async def test_the_player_bluez_names_live_is_followed_at_once(bt):
    """BlueZ naming the other app's player the live one is the switch itself —
    the old one may linger on the bus for a while."""
    await playing_iphone(bt)
    bt.bluez.object_manager_gate = asyncio.Event()
    other = bt.bluez.other_player_added(IPHONE, "player2", "paused")
    bt.bluez.control_names(IPHONE, other)
    from backend.tests.golden.harness import settle
    await settle()
    assert bt.active() and bt.meta().get("has_avrcp") and not bt.playing()
    bt.bluez.object_manager_gate.set()


async def test_a_player_named_before_it_was_announced_is_followed_for_real(bt):
    """BlueZ can name the live player before its InterfacesAdded reaches us:
    the rescan then adopts it — and its pause must still be heard."""
    from backend.tests.golden.harness import settle
    await playing_iphone(bt)
    bt.bluez.object_manager_gate = asyncio.Event()
    path = f"{IPHONE.path}/player2"
    bt.bluez.control_names(IPHONE, path)
    await settle()
    bt.bluez.other_player_added(IPHONE, "player2", "playing")
    await settle()
    bt.bluez.object_manager_gate.set()
    await settle()
    bt.bluez.other_player_changed(path, "paused")
    await settle()
    assert bt.active() and not bt.playing()


async def test_a_phone_being_turned_away_never_takes_the_holders_player(bt):
    """A second phone racing past the block publishes a player, and BlueZ names
    it on that phone: the phone holding the link keeps its own."""
    from backend.tests.golden.harness import settle
    await playing_iphone(bt)
    bt.bluez.disconnect_gate = asyncio.Event()
    await bt.pcm_added(PIXEL)
    other = bt.bluez.other_player_added(PIXEL, "player0", "playing")
    bt.bluez.control_names(PIXEL, other)
    await settle()
    assert bt.playing() and bt.meta().get("has_avrcp") and bt.meta().get("title") == "Says"
    await bt.command("pause")
    assert bt.bluez.transport == ["Pause"]
    bt.bluez.disconnect_gate.set()


async def test_the_live_player_is_the_one_bluez_names_and_it_survives_the_others(bt):
    """MediaControl1.Player is BlueZ's word for the live player; when the one
    followed goes, another still on the bus is the phone's."""
    await playing_iphone(bt)
    other = bt.bluez.other_player_added(IPHONE, "player2", "playing")
    bt.bluez.control_names(IPHONE, other)
    bt.bluez.player_removed()
    from backend.tests.golden.harness import settle
    await settle()
    assert bt.meta().get("has_avrcp")
    assert bt.bluez.others and bt.active()


async def test_a_second_phone_is_turned_away_while_one_holds_the_link(bt):
    await playing_iphone(bt)
    await bt.pcm_added(PIXEL)
    assert bt.meta()["device_name"] == "iPhone de Léo"
    assert bt.bluez.devices[PIXEL.path]["Connected"] is False


async def test_the_appliance_hides_while_a_phone_holds_it_and_reopens_after(bt):
    await playing_iphone(bt)
    assert not bt.exposed()
    await bt.pcm_removed(IPHONE)
    assert bt.exposed()


async def test_disconnecting_a_paused_phone_whose_player_goes_first_is_still_the_users_stop(bt, caplog):
    """A player and its link leave in either order: the end asked for stays
    asked for when the phase moves on the way."""
    caplog.set_level(logging.INFO, logger="source.bluetooth")
    await playing_iphone(bt)
    await bt.player_changed({"Status": "paused"})
    await bt.command("disconnect")
    await bt.player_removed()
    await bt.pcm_removed(IPHONE)
    assert "Session ended (user_stop)" in caplog.text


async def test_disconnect_drops_the_phone(bt, caplog):
    """The end that comes back is the one asked for: a user's stop, not the
    phone leaving on its own (the reason the wire will carry)."""
    caplog.set_level(logging.INFO, logger="source.bluetooth")
    await playing_iphone(bt)
    await bt.command("disconnect")
    assert bt.bluez.devices[IPHONE.path]["Connected"] is False
    await bt.player_removed()
    await bt.pcm_removed(IPHONE)
    assert not bt.active() and bt.errors() == []
    assert "Session ended (user_stop)" in caplog.text


async def test_transport_commands_reach_the_phones_player(bt):
    await playing_iphone(bt)
    await bt.command("pause")
    await bt.command("next")
    assert bt.bluez.transport == ["Pause", "Next"]


async def test_a_command_with_no_phone_is_refused(bt):
    await bt.select()
    result = await bt.try_command("pause")
    assert not result.get("success")


async def test_disconnect_with_no_phone_says_so(bt):
    """disconnect acts on the device, not on a session (DEVICE scope)."""
    await bt.select()
    result = await bt.try_command("disconnect")
    assert not result.get("success") and "No device connected" in result.get("error", result.get("message", ""))


async def test_selecting_the_source_starts_the_writer_once(bt):
    """The monitor's first line states BlueALSA as found — not a return that
    owes the writer a second start."""
    await bt.select()
    assert bt.unit_calls.count(("start", APLAY_UNIT)) == 1


async def test_a_multiroom_toggle_keeps_the_phone_and_moves_only_the_writer(bt):
    """REROUTE = KEEP_SESSION: only bluealsa-aplay moves; the phone stays."""
    await playing_iphone(bt, FEELING_GOOD)
    before = len(bt.unit_calls)
    await bt.reroute()
    assert bt.unit_calls[before:] == [("stop", APLAY_UNIT), ("start", APLAY_UNIT)]
    assert bt.active() and bt.playing() and bt.meta()["title"] == "Feeling Good"


async def test_a_phone_already_linked_at_select_is_adopted_with_its_stream(bt):
    """A backend restart under a live link: nothing announces it again."""
    bt.already_linked(IPHONE, SAYS, status="playing", position=5000)
    await bt.select()
    assert bt.active() and bt.playing()
    await bt.pcm_removed(IPHONE)
    assert not bt.active()


async def test_leaving_the_source_closes_the_appliance(bt):
    await playing_iphone(bt)
    await bt.deselect()
    assert not bt.exposed()
    assert all(bt.bluez.devices[p.path]["Blocked"] for p in (IPHONE, MAC_MINI, PIXEL))
