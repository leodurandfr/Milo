# backend/tests/test_resume_window.py
"""`audio.resume_window`: what a source left behind is shown again only when
it is selected back in time.

Read by BaseAudioSource at START against the moment of the STOP that left the
source, so one rule holds for every source with a resume point. Driven through
the radio (mpv simulated, a real state machine): what is asserted is what the
screen draws back on the source — the station a press re-tunes, or nothing —
and that a multiroom reroute, which RELEASEs and ACQUIREs the source that stays
selected, is never mistaken for a departure.
"""
import pytest

from backend.core import audio_source
from backend.tests.golden.test_wire_radio import FIP
from backend.tests.test_mpv_sessions import RadioRig

WINDOW = 300


class Clock:
    """CLOCK_MONOTONIC as the scenario moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(audio_source, "monotonic", lambda: c.now)
    return c


async def _left_playing(radio):
    """FIP playing, then another source selected."""
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()
    assert radio.playing()
    await radio.leave()


async def test_back_within_the_window_the_station_is_still_offered(monkeypatch, clock):
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": WINDOW})
    await _left_playing(radio)

    clock.advance(WINDOW - 60)
    await radio.select()

    assert radio.station() == "fip"
    assert radio.state()["resume"] is not None
    assert "resume_playback" in radio.state()["controls"]


async def test_back_past_the_window_nothing_is_offered(monkeypatch, clock):
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": WINDOW})
    await _left_playing(radio)

    clock.advance(WINDOW + 60)
    await radio.select()

    assert radio.station() is None
    assert radio.state()["resume"] is None
    assert "resume_playback" not in radio.state()["controls"]


async def test_a_window_of_zero_keeps_nothing_past_a_departure(monkeypatch, clock):
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": 0})
    await _left_playing(radio)

    await radio.select()

    assert radio.state()["resume"] is None


async def test_a_stopped_station_left_behind_follows_the_window_too(monkeypatch, clock):
    """Left stopped rather than playing: the same departure, the same rule."""
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": WINDOW})
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()
    await radio.command("stop")
    assert radio.station() == "fip"
    await radio.leave()

    clock.advance(WINDOW + 60)
    await radio.select()

    assert radio.station() is None


async def test_staying_on_the_source_keeps_the_station_however_long(monkeypatch, clock):
    """The window counts from a departure only: a stop with the source still
    selected keeps what it stopped, and coming back is not needed to see it."""
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": 0})
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()
    await radio.command("stop")

    clock.advance(10 * WINDOW)

    assert radio.station() == "fip"
    assert radio.state()["resume"] is not None


async def test_a_reroute_is_not_a_departure(monkeypatch, clock):
    """A multiroom toggle RELEASEs and ACQUIREs the source that stays selected:
    with a window of zero, a stopped station would vanish under the toggle if
    the reroute counted as leaving."""
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": 0})
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()
    await radio.command("stop")

    await radio.reroute()

    assert radio.station() == "fip"


async def test_the_window_restarts_at_each_departure(monkeypatch, clock):
    """Back in time, the point is the source's again: the next departure is
    timed from when it happens, not from the first one."""
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": WINDOW})
    await _left_playing(radio)
    clock.advance(WINDOW - 60)
    await radio.select()
    await radio.leave()

    clock.advance(WINDOW - 60)
    await radio.select()

    assert radio.station() == "fip"


async def test_no_limit_keeps_the_station_for_good(monkeypatch, clock):
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": None})
    await _left_playing(radio)

    clock.advance(30 * 86400)
    await radio.select()

    assert radio.station() == "fip"


async def test_an_unreadable_window_keeps_the_point(monkeypatch, clock):
    """A settings read that fails must not cost what was listened to."""
    radio = RadioRig(monkeypatch)
    radio.settings.get_setting.side_effect = lambda key, *a, **k: (
        (_ for _ in ()).throw(OSError("settings.json unreadable"))
        if key == "audio.resume_window" else {"audio.auto_stop_delay": 120}.get(key)
    )
    await _left_playing(radio)

    clock.advance(10 * WINDOW)
    await radio.select()

    assert radio.station() == "fip"


async def test_a_start_that_fails_is_not_a_departure(monkeypatch, clock):
    """The stop that settles a failed start finds the source still selected
    (its Retry starts it again): counted as a departure, it restarted the
    window, and a Retry pressed later lost what was never left."""
    radio = RadioRig(monkeypatch, settings={"audio.resume_window": WINDOW})
    await _left_playing(radio)
    clock.advance(WINDOW - 60)
    radio.systemd.start.return_value = False
    await radio.select()
    assert radio.machine.system_state.service_error is not None

    clock.advance(2 * WINDOW)
    radio.systemd.start.return_value = True
    await radio.select()

    assert radio.station() == "fip"
