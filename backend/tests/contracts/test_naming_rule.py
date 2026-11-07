"""The Lock Screen names a track exactly when Milō's screen draws one.

Four readers decide whether an audio state names a track: Milō's screen
(`richSourceFor`, frontend/src/composables/useRichDisplay.js), its Now Playing
push (`payloads.shown_track`), and Milo-iOS and Milo-Mac (`MiloAudioState.shown`,
one file in both apps). They all update what one person sees about one session:
when the push and Milō's screen disagree, the Lock Screen names a track while the
kiosk says "Connected to X", and when the push and the app disagree, the Lock
Screen flips between the two at every round trip.

`connected_naming_cases.json` is the table the push and the screen are held
to: this file holds the push to it, frontend/tests/architecture/
namingRule.test.js the screen. No Swift runs in Milō's suite, so the apps'
rule is not played against these rows: test_milo_ios_contract.py::
test_the_apps_name_a_connected_session_as_milo_does pins its text word for
word, and the apps' own MiloAudioStateTests run it. The table's `_domain` says
what it covers and what it leaves out on purpose.
"""
import json
from pathlib import Path

import pytest

from backend.core.models.audio_wire import AudioState
from backend.core.push.payloads import shown_track

_TABLE = json.loads((Path(__file__).resolve().parent / "connected_naming_cases.json").read_text())
_CASES = _TABLE["cases"]

assert len(_CASES) >= 8 and {c["shown"] for c in _CASES} == {True, False}, (
    "connected_naming_cases.json lost its cases: a table with one answer proves nothing"
)


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_the_lock_screen_names_what_milos_screen_draws(case):
    """A CONNECTED session names its track only with a title, an artist and a
    cover (Milō's D14); any other session with a title. Fails when the push
    names a track the kiosk draws as the source card, or the reverse."""
    state = AudioState.model_validate(case["state"]).wire()
    assert state["service"] == "running" and not state["switching"], "outside the table's domain"
    assert not any(state["availability"].values()), "outside the table's domain"

    assert (shown_track(state) is not None) is case["shown"]
