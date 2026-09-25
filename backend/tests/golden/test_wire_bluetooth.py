"""Bluetooth's wire, scenario by scenario (see harness.py for the rules).

The outside world is BlueZ and BlueALSA. Every stimulus is written as what one
of them says: BlueALSA prints a PCMAdded/PCMRemoved line on `bluealsa-cli
monitor`, BlueZ emits InterfacesAdded/InterfacesRemoved/PropertiesChanged for
`org.bluez.MediaPlayer1`, answers a Get on Position by extrapolating from its
last anchor, and iTunes answers the cover lookup. The adapter below decides
which entry point hears it today — a later phase rewrites the adapter, never a
scenario. The adapter and the world it drives live in tests/bluetooth_world.py,
shared with the behaviour tests (phase 4).
"""
import pytest

from backend.tests.bluetooth_world import (
    FEELING_GOOD, HAMMERS, IPHONE, MAC_MINI, PIXEL, SAYS, UNKNOWN_DEMO, Bluetooth,
)
from backend.tests.golden.harness import check_recording


@pytest.fixture
def bt(monkeypatch):
    return Bluetooth(monkeypatch)


async def test_select_and_leave(bt):
    await bt.select()
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "select_and_leave", bt.wire)


async def test_phone_with_no_player_connects_and_leaves(bt):
    await bt.select()
    await bt.pcm_added(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.elapse(12)
    await bt.pcm_removed(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "phone_with_no_player_connects_and_leaves", bt.wire)


async def test_player_before_pcm_publishes_a_track(bt):
    await bt.select()
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.pcm_added(IPHONE)
    await bt.elapse(5)                              # one position poll
    await bt.wire.snapshot_rest()
    await bt.elapse(11)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "player_before_pcm_publishes_a_track", bt.wire)


async def test_pause_and_resume_from_the_phone_and_from_milo(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, FEELING_GOOD, status="playing", position=30000)
    await bt.elapse(5)
    # Paused on the phone: BlueZ sends the state, then the corrected anchor.
    await bt.player_changed({"Status": "paused"}, {"Position": 35120})
    await bt.wire.snapshot_rest()
    await bt.elapse(8)
    await bt.player_changed({"Status": "playing"}, {"Position": 35120})
    await bt.elapse(5)
    # Paused from Milō: the phone answers the same way a beat later.
    await bt.command("pause")
    await bt.elapse(0.125)
    await bt.player_changed({"Status": "paused"}, {"Position": 40250})
    await bt.elapse(6)                              # the post-command re-reads
    await bt.wire.snapshot_rest()
    await bt.command("resume")
    await bt.elapse(0.125)
    await bt.player_changed({"Status": "playing"}, {"Position": 40250})
    await bt.elapse(6)
    await bt.deselect()
    check_recording("bluetooth", "pause_and_resume_from_the_phone_and_from_milo", bt.wire)


async def test_track_changes(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.elapse(5)
    # Next from Milō: BlueZ names the new track ~900 ms after the press.
    await bt.command("next")
    await bt.elapse(0.875)
    await bt.player_changed({"Track": HAMMERS})
    await bt.elapse(6)
    # Prev from Milō restarts the track: BlueZ says nothing at all.
    await bt.command("prev")
    await bt.elapse(6)
    await bt.wire.snapshot_rest()
    # The queue advances by itself, the new Duration landing before the Title.
    await bt.elapse(10)
    await bt.player_changed({"Track": {**HAMMERS, "Duration": FEELING_GOOD["Duration"]}})
    await bt.elapse(0.625)
    await bt.player_changed({"Track": FEELING_GOOD}, {"Position": 0})
    await bt.elapse(5)
    # A track iTunes does not know: no cover, the glyph stays.
    await bt.player_changed({"Track": UNKNOWN_DEMO}, {"Position": 0})
    await bt.elapse(5)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "track_changes", bt.wire)


async def test_disconnect_command_and_a_second_phone(bt):
    await bt.select()
    await bt.pcm_added(IPHONE)
    await bt.player_added(IPHONE, SAYS, status="playing", position=0)
    await bt.elapse(2)
    # A second phone dials in while the first holds the link: it is dropped.
    await bt.pcm_added(PIXEL)
    await bt.pcm_removed(PIXEL)
    await bt.command("disconnect")
    # The link goes: BlueZ drops the player, BlueALSA the PCM.
    await bt.player_removed()
    await bt.pcm_removed(IPHONE)
    await bt.wire.snapshot_rest()
    await bt.elapse(6)
    await bt.deselect()
    check_recording("bluetooth", "disconnect_command_and_a_second_phone", bt.wire)


async def test_sender_already_connected_at_select(bt):
    # A backend restart under a live link: the PCM and a track-less player
    # (a Mac mini registers one and never serves its metadata) predate us.
    # Its Position is BlueZ extrapolating from an anchor nothing re-anchors:
    # hours into a song, inert because no duration comes with it.
    bt.already_linked(MAC_MINI, track=None, status="playing", position=9874000)
    await bt.select()
    await bt.wire.snapshot_rest()
    await bt.elapse(5)
    await bt.player_changed({"Status": "paused"})
    await bt.player_removed()
    await bt.pcm_removed(MAC_MINI)
    await bt.wire.snapshot_rest()
    await bt.deselect()
    check_recording("bluetooth", "sender_already_connected_at_select", bt.wire)
