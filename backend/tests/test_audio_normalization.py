"""The loudness normalization reaches mpv as its setting says, per source.

It used to be `--af=loudnorm=…` in the radio, podcast and CD units' ExecStart,
out of reach of any setting: a CD was played through a loudness filter nobody
had asked for, and nothing could switch it off. Now `MpvAudioSource` sets mpv's
`af` over IPC, before every load and when the setting changes.

Driven through the outside world only: MpvSim for mpv (tests/mpv_sim.py), the
settings read as SettingsService answers them. What is asserted is what mpv
was sent, and in which order relative to the load.
"""
import asyncio
from typing import List

import pytest

from backend.shared.mpv_audio_source import MpvAudioSource
from backend.tests.cd_world import CdWorld
from backend.tests.golden.harness import settle
from backend.tests.golden.test_wire_podcast import EPISODE_A
from backend.tests.golden.test_wire_radio import FIP, NOVA
from backend.tests.test_mpv_sessions import LibraryRig, PodcastRig, RadioRig

FILTER = MpvAudioSource.NORMALIZATION_FILTER


def filters(mpv) -> List[str]:
    """Every `af` mpv was given, in order."""
    return [c[2] for c in mpv.sent if c[:2] == ("set_property", "af")]


def af_precedes_first_load(mpv) -> bool:
    """The filter was in place before mpv opened anything."""
    kinds = [c[0] if c[0] != "set_property" else f"set_property:{c[1]}" for c in mpv.sent]
    return kinds.index("set_property:af") < kinds.index("loadfile")


@pytest.fixture
def radio_on(monkeypatch):
    return RadioRig(monkeypatch, settings={"audio.normalization.radio": True})


async def test_a_station_is_loaded_through_the_filter(radio_on):
    await radio_on.select()
    await radio_on.tune(FIP)

    assert filters(radio_on.mpv) == [FILTER]
    assert af_precedes_first_load(radio_on.mpv), "the first seconds played unfiltered"


@pytest.mark.parametrize("rig_class, setting, play", [
    (PodcastRig, "audio.normalization.podcast", lambda rig: rig.play(EPISODE_A)),
    (LibraryRig, "audio.normalization.music_library", lambda rig: rig.play_album()),
])
async def test_every_mpv_source_applies_its_own_switch_at_load(monkeypatch, rig_class, setting, play):
    """Each source's load reaches mpv through the one place that sets it: a
    source that loaded another way would play with whatever the last one
    left in its mpv."""
    for enabled, expected in ((True, FILTER), (False, "")):
        rig = rig_class(monkeypatch, settings={setting: enabled})
        await rig.select()
        await play(rig)
        assert filters(rig.mpv) == [expected], f"{setting}={enabled}"
        assert af_precedes_first_load(rig.mpv)
        await rig.leave()


async def test_a_cd_with_normalization_off_is_played_without_the_filter(monkeypatch):
    """The CD unit used to start mpv with the filter in: switched off, the
    `af` is cleared, so what mpv was started with cannot linger."""
    w = CdWorld(monkeypatch, settings={"audio.normalization.cd": False})
    w.drive, w.media = True, "audio"
    try:
        await w.boot()
        await w.select()
        await w.command("play_track", {"track_number": 2})

        assert filters(w.mpv)[-1] == ""
        assert filters(w.mpv) == [""], "set again on the same mpv"
    finally:
        await w.source.shutdown()
        await settle()


async def test_the_filter_is_set_once_per_mpv(radio_on):
    """Re-setting an unchanged `af` makes mpv rebuild its audio chain; a new
    mpv (systemd's restart after a crash) starts without it."""
    await radio_on.select()
    await radio_on.tune(FIP)
    await radio_on.tune(NOVA)
    assert filters(radio_on.mpv) == [FILTER]

    await radio_on.mpv.dies()
    await settle()
    await radio_on.tune(FIP)

    assert filters(radio_on.mpv) == [FILTER, FILTER], "the restarted mpv played unfiltered"
    assert radio_on.loads()[-1][1] == FIP["url"]


async def test_a_changed_setting_reaches_the_mpv_playing_now(radio_on):
    await radio_on.select()
    await radio_on.tune(FIP)
    await radio_on.tick()
    loads = len(radio_on.loads())

    radio_on.settings.values["audio.normalization.radio"] = False
    assert await radio_on.source.reload_normalization() is True

    assert filters(radio_on.mpv) == [FILTER, ""]
    assert len(radio_on.loads()) == loads, "the station was loaded again"
    assert radio_on.playing()


async def test_a_source_not_started_takes_the_setting_at_its_next_load(radio_on):
    """No mpv to set: the change is stored, nothing fails, and the next load
    reads it."""
    assert await radio_on.source.reload_normalization() is True
    assert radio_on.mpv.sent == []

    await radio_on.select()
    await radio_on.tune(FIP)
    assert filters(radio_on.mpv) == [FILTER]


async def test_a_filter_mpv_refuses_does_not_refuse_the_load(radio_on, monkeypatch):
    """The sound plays unfiltered rather than not at all, and the next load
    asks again — the refusal was not recorded as applied."""
    set_property = radio_on.mpv.set_property

    async def refusing_af(name, value):
        if name == "af":
            radio_on.mpv.sent.append(("set_property", name, value))
            return False
        return await set_property(name, value)

    monkeypatch.setattr(radio_on.mpv, "set_property", refusing_af)
    await radio_on.select()
    await radio_on.tune(FIP)
    await radio_on.tick()
    assert radio_on.playing()

    await radio_on.tune(NOVA)
    assert filters(radio_on.mpv) == [FILTER, FILTER]
    assert await radio_on.source.reload_normalization() is False


async def test_a_reload_a_source_switch_cuts_is_not_a_refusal(radio_on, monkeypatch):
    """A switch flipped while the source is being left: the stop cuts the
    reload before mpv answered, the setting is stored, and the next mpv reads
    it at its load. Answered False, the PUT said "saved but runtime reload
    failed" although nothing had refused anything."""
    await radio_on.select()
    await radio_on.tune(FIP)
    await radio_on.tick()
    set_property = radio_on.mpv.set_property
    mpv_silent = asyncio.Event()

    async def unanswered_af(name, value):
        if name == "af":
            await mpv_silent.wait()
        return await set_property(name, value)

    monkeypatch.setattr(radio_on.mpv, "set_property", unanswered_af)
    radio_on.settings.values["audio.normalization.radio"] = False
    reload = asyncio.create_task(radio_on.source.reload_normalization())
    await settle()
    assert not reload.done(), "the reload never reached mpv"

    await radio_on.leave()

    assert await asyncio.wait_for(reload, 2.0) is True
