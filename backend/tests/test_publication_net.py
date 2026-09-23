"""A command that changed a source's state without saying so is published anyway.

The net behind every source (core/audio_source.py): after each command the
actor projects the source and republishes when the projection moved since the
last publish. Radio and Podcast both reset their session when mpv refuses a
load and publish nothing, so the card kept ACTIVE with an hourglass and no stop
button, for good (E41). Driven through the old-wire adapters: mpv refusing is
the outside world, the envelopes are what a client receives.
"""
import pytest

from backend.tests.golden.test_old_wire_podcast import EPISODE_A, Podcast
from backend.tests.golden.test_old_wire_radio import Radio


def _last_state(wire):
    changes = [e for e in wire.recorder.envelopes if e["type"] == "state_changed"]
    return changes[-1]["data"]


@pytest.fixture
def radio(monkeypatch):
    return Radio(monkeypatch)


@pytest.fixture
def podcast(monkeypatch):
    return Podcast(monkeypatch)


async def test_a_station_mpv_refused_leaves_the_card_ready(radio):
    await radio.select()
    radio.mpv.accept = False
    await radio.command("play_station", {"station_id": "fip"})

    state = _last_state(radio.wire)
    assert state["new_state"] == "ready"
    assert state["metadata"]["is_buffering"] is False
    assert state["metadata"]["station_name"] == "FIP"      # kept for "play again"
    assert radio.machine.system_state.metadata["is_buffering"] is False


async def test_an_episode_mpv_refused_leaves_the_card_ready(podcast):
    await podcast.select()
    podcast.mpv.accept = False
    await podcast.play(EPISODE_A)

    state = _last_state(podcast.wire)
    assert state["new_state"] == "ready"
    assert state["metadata"]["is_buffering"] is False
    assert podcast.machine.system_state.source_state.value == "ready"


async def test_a_command_that_changed_nothing_publishes_nothing_more(radio):
    """The net is a projection compare, not a republish per command: a stop on a
    radio already stopped puts on the wire what the source itself sends (one
    state per stop) and nothing on top of it."""
    await radio.select()
    before = len(radio.wire.recorder.envelopes)
    await radio.command("stop")
    await radio.command("stop")
    assert len(radio.wire.recorder.envelopes) - before == 2
