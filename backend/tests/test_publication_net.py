"""A command that changed a source's state without saying so is published anyway.

The net behind every source (core/audio_source.py): after each message the
actor compares the source's view with the last one it published and
republishes when it moved. Radio and Podcast both end their session when mpv
refuses a load, and the card kept a loading session with an hourglass and no
stop button, for good (E41). Driven through the wire adapters: mpv refusing is
the outside world, the envelopes are what a client receives.
"""
import pytest

from backend.tests.golden.test_wire_podcast import EPISODE_A, Podcast
from backend.tests.golden.test_wire_radio import Radio


def _states(world):
    return [e["data"] for e in world.wire.recorder.envelopes
            if (e["category"], e["type"]) == ("source", "state")]


def _session_ends(world):
    return [e["data"]["reason"] for e in world.wire.recorder.envelopes
            if (e["category"], e["type"]) == ("source", "session_ended")]


@pytest.fixture
def radio(monkeypatch):
    return Radio(monkeypatch)


@pytest.fixture
def podcast(monkeypatch):
    return Podcast(monkeypatch)


async def test_a_station_mpv_refused_leaves_the_card_without_a_session(radio):
    await radio.select()
    radio.mpv.accept = False
    await radio.command("play_station", {"station_id": "fip"})

    state = _states(radio)[-1]
    assert state["session"] is None
    assert state["details"]["station"]["name"] == "FIP"     # kept for "play again"
    assert _session_ends(radio)[-1] == "load_failed"
    assert radio.machine.get_current_state()["session"] is None


async def test_an_episode_mpv_refused_leaves_the_card_without_a_session(podcast):
    await podcast.select()
    podcast.mpv.accept = False
    await podcast.play(EPISODE_A)

    state = _states(podcast)[-1]
    assert state["session"] is None
    assert _session_ends(podcast)[-1] == "load_failed"
    assert podcast.machine.get_current_state()["session"] is None


async def test_a_command_that_changed_nothing_publishes_nothing(radio):
    """The net is a view compare, not a republish per command: a stop on a
    radio already stopped changes nothing, and nothing goes on the wire."""
    await radio.select()
    before = len(radio.wire.recorder.envelopes)
    await radio.command("stop")
    await radio.command("stop")
    assert radio.wire.recorder.envelopes[before:] == []
