"""The wire, source by source and phase by phase (docs: "Développeurs : le
fil", frozen 2026-09-24).

Each scenario drives one source through its outside world into one of the
states §10 of the frozen spec gives an example of. Two families of tests read
them:

- the acceptance: what the state machine would put on the wire has the shape
  the spec froze — every field present, the session's phase, senders and
  anchor, the exact `controls`, the resume point and the `details`. The three
  clients decode these fields into fixed types (Milo-Mac, Milo-iOS, the
  frontend's strict schema): a field missing or spelled another way is a
  client that stops reading the state.
- the guardrail (§4): every command a state lists in `controls` is accepted by
  the source in that state. A button drawn from `controls` that the source
  refuses is a press that does nothing; each command is sent on a scenario of
  its own, rebuilt from scratch, so one command's effect never hides the next.

What a session id or an anchor instant is cannot be frozen (random, and a
clock): they are checked for their form, not their value.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

import pytest

from backend.shared.mpv_audio_source import MpvAudioSource
from backend.tests.airplay_world import PHONE, AirPlayWorld
from backend.tests.bluetooth_world import IPHONE, MAC_MINI, SAYS, Bluetooth
from backend.tests.cd_world import CdWorld
from backend.tests.golden.harness import settle
from backend.tests.golden.test_wire_music_library import ALBUM
from backend.tests.golden.test_wire_podcast import EPISODE_A
from backend.tests.golden.test_wire_radio import FIP
from backend.tests.mac_world import MINI_IP, MINI_NAME, MacWorld
from backend.tests.qobuz_world import ON_AND_ON, QobuzWorld
from backend.tests.spotify_world import PARAPLUIE, SpotifyWorld
from backend.tests.test_mpv_sessions import LibraryRig, PodcastRig, RadioRig
from backend.tests.tidal_world import TidalWorld

STATE_KEYS = [
    "source", "switching", "service", "service_error", "availability", "session",
    "controls", "resume", "details", "multiroom_enabled", "equalizer_effects_enabled",
]
SESSION_KEYS = [
    "id", "phase", "title", "artist", "album", "artwork", "senders", "duration_ms", "position",
]
RESUME_KEYS = ["title", "artist", "album", "artwork", "duration_ms", "position_ms"]
SOURCES = [
    "radio", "podcast", "music_library", "cd", "spotify", "tidal", "qobuz",
    "airplay", "bluetooth", "mac",
]

# What the guardrail sends with a command that takes parameters.
PARAMS = {
    "seek": {"position_ms": 1000},
    "skip": {"seconds": 5},
    "set_speed": {"speed": 1.5},
    "set_shuffle": {"shuffle": True},
    "play_index": {"index": 0},
    "play_track": {"track_number": 1},
}


def wire(state: Dict[str, Any]) -> Dict[str, Any]:
    """The state as frozen: every key, in order, ids and instants checked for
    their form and then masked."""
    assert list(state) == STATE_KEYS
    assert list(state["availability"]) == SOURCES
    session = state["session"]
    if session is not None:
        assert list(session) == SESSION_KEYS
        assert re.fullmatch(r"[0-9a-f]{32}", session["id"]), session["id"]
        session = {**session, "id": "<id>"}
        position = session["position"]
        if position is not None:
            assert list(position) == ["ms", "at", "rate"]
            assert isinstance(position["ms"], int) and isinstance(position["at"], float)
            session["position"] = {**position, "at": "<at>"}
    if state["resume"] is not None:
        assert list(state["resume"]) == RESUME_KEYS
    return {**state, "session": session}


@dataclass
class Scenario:
    """A source driven into one state. `expect` is the part of the wire the
    spec froze for it, key by key; `session_has` a subset of the session."""
    name: str
    build: Callable[[Any, Any], Awaitable[Any]]
    expect: Dict[str, Any] = field(default_factory=dict)
    session_has: Dict[str, Any] = field(default_factory=dict)


# === Builders: each returns the world, in the state its scenario names ===

async def _radio(mp, tmp, *, tune=True, stop=False, favorites=True, loading=False):
    rig = RadioRig(mp)
    if not favorites:
        rig.source._station_data.favorite_ids = []
    if loading:
        # The rig's watchdog is shortened to end a stall at once; a load stays here.
        mp.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 30.0)
        rig.mpv.auto_open = False
    await rig.select()
    if tune:
        await rig.tune(FIP)
    if stop:
        await rig.command("stop")
    return rig


async def _podcast(mp, tmp, *, play=True, pause=False, loading=False, kept=False):
    rig = PodcastRig(mp)
    if loading:
        mp.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 30.0)
        rig.mpv.auto_open = False
    await rig.select()
    if play:
        await rig.play(EPISODE_A)
    if pause:
        await rig.command("pause")
    if kept:
        await rig.leave()
        await rig.select()
    return rig


async def _library(mp, tmp, *, start=0, pause=False, idle_end=False):
    rig = LibraryRig(mp, settings={"audio.auto_stop_delay": 0.5} if idle_end else None)
    await rig.select()
    await rig.play_album(start_index=start)
    if pause or idle_end:
        await rig.command("pause")
        await settle()
    return rig


async def _cd(mp, tmp, *, disc="audio", plugged=True, play=False):
    w = CdWorld(mp)
    if plugged:
        w.drive, w.media = True, disc
    await w.boot()
    await w.select()
    if play:
        await w.command("play_track", {"track_number": 1})
        await w.advance(1.1)
    return w


async def _spotify(mp, tmp, *, pause=False):
    w = SpotifyWorld(mp, tmp)
    await w.select()
    await w.phone_plays(PARAPLUIE)
    if pause:
        await w.phone_pauses()
    return w


async def _tidal(mp, tmp, *, pause=False):
    w = TidalWorld(mp, tmp)
    await w.select()
    await w.mac_plays()
    if pause:
        await w.mac_pauses()
    return w


async def _qobuz(mp, tmp, *, pause=False):
    w = QobuzWorld(mp, tmp)
    await w.boot()
    await w.select()
    await w.app_plays(ON_AND_ON)
    if pause:
        await w.app_pauses()
    return w


async def _airplay(mp, tmp, *, music=False, system_audio=False, pause=False):
    w = AirPlayWorld(mp, tmp)
    await w.select()
    await w.connects(PHONE, "iPhone de Léo")
    if music:
        await w.music_plays()
    if system_audio:
        await w.system_audio_plays()
    if pause:
        await w.pauses()
    return w


async def _bluetooth(mp, tmp, *, phone=IPHONE, player=True, status="playing"):
    w = Bluetooth(mp)
    await w.select()
    await w.pcm_added(phone)
    if player:
        await w.player_added(phone, SAYS, status=status, position=0)
    return w


async def _mac(mp, tmp):
    w = MacWorld(mp)
    await w.select()
    await w.mac_streams(MINI_IP)
    return w


FIP_ARTWORK = "/api/radio/favicon?url=https%3A%2F%2Fimg.example%2Ffip.png"
FIP_STATION = {
    "id": "fip", "name": "FIP", "url": "http://stream.example/fip.mp3", "country": "France",
    "genre": "eclectic", "favicon": "https://img.example/fip.png", "bitrate": 128, "codec": "MP3",
}
FIP_RESUME = {
    "title": "FIP", "artist": None, "album": "FIP", "artwork": FIP_ARTWORK,
    "duration_ms": None, "position_ms": None,
}
SHOW = EPISODE_A["podcast"]["name"]
FIRST = ALBUM[0]
LIBRARY_TAIL = ["set_shuffle", "play_index", "stop"]


def _cover(song: Dict[str, Any]) -> str:
    return f"/api/music-library/cover/{song.get('coverArt') or song.get('albumId')}"


SCENARIOS: List[Scenario] = [
    # Radio — no pause, no playhead; next/prev walk the favorites.
    Scenario("radio loading", lambda mp, t: _radio(mp, t, loading=True), expect={
        "session": {
            "id": "<id>", "phase": "loading", "title": "FIP", "artist": None, "album": "FIP",
            "artwork": FIP_ARTWORK, "senders": [], "duration_ms": None, "position": None,
        },
        "controls": ["stop", "next", "prev"], "resume": None,
        "details": {"kind": "radio", "station": FIP_STATION, "track": None},
    }),
    Scenario("radio playing", _radio, expect={"controls": ["stop", "next", "prev"]},
             session_has={"phase": "playing", "position": None}),
    Scenario("radio stopped", lambda mp, t: _radio(mp, t, stop=True), expect={
        "session": None, "controls": ["resume_playback", "next", "prev"], "resume": FIP_RESUME,
        "details": {"kind": "radio", "station": FIP_STATION, "track": None},
    }),
    Scenario("radio with nothing", lambda mp, t: _radio(mp, t, tune=False), expect={
        "session": None, "controls": ["next", "prev"], "resume": None, "details": None,
    }),
    Scenario("radio without favorites", lambda mp, t: _radio(mp, t, favorites=False),
             expect={"controls": ["stop"]}),
    # Podcast — the playhead moves at the chosen speed.
    Scenario("podcast loading", lambda mp, t: _podcast(mp, t, loading=True),
             expect={"controls": ["pause", "set_speed"]}, session_has={"phase": "loading"}),
    Scenario("podcast playing", _podcast, expect={
        "session": {
            "id": "<id>", "phase": "playing", "title": "The Sunday Read", "artist": SHOW,
            "album": SHOW, "artwork": EPISODE_A["image_url"], "senders": [],
            "duration_ms": 1800000, "position": {"ms": 0, "at": "<at>", "rate": 1.0},
        },
        "controls": ["pause", "seek", "skip", "set_speed"], "resume": None,
        "details": {"kind": "podcast", "episode": EPISODE_A, "speed": 1.0},
    }),
    Scenario("podcast paused", lambda mp, t: _podcast(mp, t, pause=True),
             expect={"controls": ["resume", "seek", "skip", "set_speed"]}, session_has={"phase": "paused"}),
    # E50: nothing to seek with no session.
    Scenario("podcast kept to resume", lambda mp, t: _podcast(mp, t, kept=True), expect={
        "session": None, "controls": ["resume", "set_speed"],
        "resume": {
            "title": "The Sunday Read", "artist": SHOW, "album": SHOW,
            "artwork": EPISODE_A["image_url"], "duration_ms": 1800000, "position_ms": 0,
        },
        "details": {"kind": "podcast", "episode": EPISODE_A, "speed": 1.0},
    }),
    Scenario("podcast with nothing", lambda mp, t: _podcast(mp, t, play=False), expect={
        "session": None, "controls": ["set_speed"], "resume": None, "details": None,
    }),
    # Music Library — a queue; no next on its last track.
    Scenario("library playing", _library, expect={
        "session": {
            "id": "<id>", "phase": "playing", "title": FIRST["title"], "artist": FIRST["artist"],
            "album": FIRST["album"], "artwork": _cover(FIRST), "senders": [],
            "duration_ms": FIRST["duration"] * 1000,
            "position": {"ms": 0, "at": "<at>", "rate": 1.0},
        },
        "controls": ["pause", "seek", "skip", "next", "prev", *LIBRARY_TAIL],
        "details": {
            "kind": "music_library", "queue": ALBUM, "queue_index": 0, "shuffle": False,
            "track_id": FIRST["id"], "album_id": FIRST["albumId"], "artist_id": FIRST["artistId"],
        },
    }),
    Scenario("library last track", lambda mp, t: _library(mp, t, start=len(ALBUM) - 1),
             expect={"controls": ["pause", "seek", "skip", "prev", *LIBRARY_TAIL]}),
    Scenario("library paused", lambda mp, t: _library(mp, t, pause=True),
             expect={"controls": ["resume", "seek", "skip", "next", "prev", *LIBRARY_TAIL]},
             session_has={"phase": "paused"}),
    # E50: the resume view offers what the source takes — no seek, no next.
    Scenario("library resume", lambda mp, t: _library(mp, t, idle_end=True), expect={
        "session": None, "controls": ["resume", "play_index", "stop"],
        "resume": {
            "title": FIRST["title"], "artist": FIRST["artist"], "album": FIRST["album"],
            "artwork": _cover(FIRST), "duration_ms": FIRST["duration"] * 1000, "position_ms": 0,
        },
    }),
    # CD — the drive's state is the availability; a READY disc always resumes.
    Scenario("cd opened, paused", _cd,
             expect={"controls": ["resume", "seek", "skip", "next", "prev", "play_track", "eject"]},
             session_has={"phase": "paused", "senders": []}),
    Scenario("cd playing", lambda mp, t: _cd(mp, t, play=True),
             expect={"controls": ["pause", "seek", "skip", "next", "prev", "play_track", "eject"]},
             session_has={"phase": "playing"}),
    # E67: a disc Milō cannot play still comes out.
    Scenario("cd unreadable", lambda mp, t: _cd(mp, t, disc="data"), expect={
        "session": None, "controls": ["eject"], "resume": None,
        "details": {"kind": "cd", "disc": None, "current_track": None, "artwork_pending": False},
    }),
    Scenario("cd no drive", lambda mp, t: _cd(mp, t, plugged=False), expect={
        "session": None, "controls": [], "resume": None, "details": None,
    }),
    # Spotify — no sender name (an account is an identity, D3).
    Scenario("spotify playing", _spotify, expect={"controls": ["pause", "seek", "skip", "next", "prev"]},
             session_has={"phase": "playing", "senders": [], "title": "Parapluie"}),
    Scenario("spotify paused", lambda mp, t: _spotify(mp, t, pause=True),
             expect={"controls": ["resume", "seek", "skip", "next", "prev"]}, session_has={"phase": "paused"}),
    # Tidal — transport, no seek.
    Scenario("tidal playing", _tidal, expect={"controls": ["pause", "next", "prev"]},
             session_has={"phase": "playing", "senders": []}),
    Scenario("tidal paused", lambda mp, t: _tidal(mp, t, pause=True),
             expect={"controls": ["resume", "next", "prev"]}, session_has={"phase": "paused"}),
    # Qobuz — the app is the only remote.
    Scenario("qobuz playing", _qobuz, expect={"controls": [], "resume": None, "details": None},
             session_has={"phase": "playing", "senders": []}),
    Scenario("qobuz paused", lambda mp, t: _qobuz(mp, t, pause=True), expect={"controls": []},
             session_has={"phase": "paused"}),
    # AirPlay — named by its sender; a Realtime stream is CONNECTED.
    Scenario("airplay connected", _airplay, expect={
        "session": {
            "id": "<id>", "phase": "connected", "title": None, "artist": None, "album": None,
            "artwork": None, "senders": ["iPhone de Léo"], "duration_ms": None, "position": None,
        },
        "controls": [], "details": {"kind": "airplay", "artwork_width": None},
    }),
    Scenario("airplay playing", lambda mp, t: _airplay(mp, t, music=True), expect={"controls": []},
             session_has={"phase": "playing", "senders": ["iPhone de Léo"]}),
    Scenario("airplay paused", lambda mp, t: _airplay(mp, t, music=True, pause=True),
             expect={"controls": []}, session_has={"phase": "paused"}),
    Scenario("airplay realtime", lambda mp, t: _airplay(mp, t, system_audio=True),
             expect={"controls": []}, session_has={"phase": "connected"}),
    # Bluetooth — the transport when the linked phone has a player (E33).
    Scenario("bluetooth playing", _bluetooth,
             expect={"controls": ["pause", "next", "prev", "disconnect"], "details": None},
             session_has={"phase": "playing", "senders": ["iPhone de Léo"]}),
    Scenario("bluetooth paused", lambda mp, t: _bluetooth(mp, t, status="paused"),
             expect={"controls": ["resume", "next", "prev", "disconnect"]},
             session_has={"phase": "paused"}),
    Scenario("bluetooth connected, no player", lambda mp, t: _bluetooth(mp, t, player=False),
             expect={"controls": ["disconnect"]},
             session_has={"phase": "connected", "position": None, "title": None}),
    # The Mac publishes no Status for its first 100 s: CONNECTED, transport offered.
    Scenario("bluetooth connected, player", lambda mp, t: _bluetooth(mp, t, phone=MAC_MINI, status=""),
             expect={"controls": ["resume", "next", "prev", "disconnect"]},
             session_has={"phase": "connected"}),
    # Mac — always CONNECTED, named by Bonjour.
    Scenario("mac connected", _mac, expect={
        "session": {
            "id": "<id>", "phase": "connected", "title": None, "artist": None, "album": None,
            "artwork": None, "senders": [MINI_NAME], "duration_ms": None, "position": None,
        },
        "controls": [], "resume": None, "details": None,
    }),
]


async def _close(world) -> None:
    await world.source.shutdown()
    await settle()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_the_state_is_the_frozen_one(scenario, monkeypatch, tmp_path):
    world = await scenario.build(monkeypatch, tmp_path)
    try:
        state = wire(world.state())
        assert state["switching"] is False and state["service"] == "running"
        assert state["service_error"] is None
        for key, value in scenario.expect.items():
            assert state[key] == value, f"{key}: {state[key]!r}\n!= {value!r}"
        for key, value in scenario.session_has.items():
            assert state["session"] is not None, "no session"
            assert state["session"][key] == value, f"session.{key}: {state['session'][key]!r} != {value!r}"
    finally:
        await _close(world)


CONTROL_CASES = [
    (scenario, command)
    for scenario in SCENARIOS
    for command in (scenario.expect.get("controls") or [])
]


@pytest.mark.parametrize(
    "scenario,command", CONTROL_CASES,
    ids=[f"{s.name}:{c}" for s, c in CONTROL_CASES],
)
async def test_every_listed_command_is_accepted(scenario, command, monkeypatch, tmp_path):
    """§4: a command is listed only if it is accepted and does something."""
    world = await scenario.build(monkeypatch, tmp_path)
    try:
        assert command in world.state()["controls"]
        result = await world.source.command(command, PARAMS.get(command))
        await settle()
        assert result.get("success"), f"{scenario.name}: '{command}' refused: {result}"
    finally:
        await _close(world)


def test_the_guardrail_meets_every_command_the_spec_lists():
    """Every command name §4 lists for any source appears in some scenario,
    so the guardrail cannot pass by never meeting one."""
    listed = {c for _, c in CONTROL_CASES}
    assert listed == {
        "stop", "next", "prev", "resume_playback", "pause", "resume", "seek", "skip", "set_speed",
        "set_shuffle", "play_index", "play_track", "eject", "disconnect",
    }


# === Every field, every source ===

async def test_a_fresh_machine_publishes_every_field():
    from backend.core.state import AudioStateMachine
    state = AudioStateMachine().get_current_state()
    assert list(state) == STATE_KEYS
    assert state["source"] == "none" and state["service"] == "stopped"
    assert state["availability"] == {source: None for source in SOURCES}
    assert state["controls"] == [] and state["session"] is None


async def test_the_rest_answer_and_the_event_carry_the_same_object(monkeypatch, tmp_path):
    """E59: one object, whether read over REST or received over WS."""
    rig = await _radio(monkeypatch, tmp_path)
    try:
        sent = [e["data"] for e in rig.recorder.envelopes if (e["category"], e["type"]) == ("source", "state")]
        assert sent, "no source/state on the wire"
        assert sent[-1] == rig.recorder.stable(rig.state())
    finally:
        await _close(rig)
