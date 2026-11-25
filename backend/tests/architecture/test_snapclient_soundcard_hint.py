# backend/tests/architecture/test_snapclient_soundcard_hint.py
"""Each snapclient's --soundcard is a PCM ALSA's hint list names.

snapclient (`client/snapclient.cpp::getPcmDevice`) looks the name it is given up
in `AlsaPlayer::pcm_list()`, which is `snd_device_name_hint(-1, "pcm")` — what
`aplay -L` prints — taking the first entry whose NAME *contains* it. A name absent
from that list logs `PCM device "<name>" not found` at err on every start, and
snapclient then opens the name anyway and plays: an error in the journal of every
unit, on every backend restart and multiroom toggle, that names no fault.

Two PCMs are concerned: the server's local snapclient writes `pcm.camilladsp`
(`system/milo-snapclient-multiroom.service`), every satellite's writes
`pcm.camilladsp_input` (`milo-client-snapclient-launcher`). Neither can be listed
by a `hint { show on }` inside it: alsa-lib (`control/namehint.c::try_config`)
follows the slave, takes a `type hw` slave's `device` for a card device and drops
the PCM from the software list, and the per-card pass cannot expand a definition
that takes no CARD argument. Measured with `aplay -L` (alsa-lib 1.2.14) and
snapclient itself. So each asound.conf lists the name in `namehint.pcm`, outside
the audio path — which alsa-lib appends *after* every hinted PCM, so no hinted
PCM may contain the name either, or snapclient would take that one instead.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# (the file that starts a snapclient, the asound.conf of its tree)
SNAPCLIENTS = (
    (ROOT / "system" / "milo-snapclient-multiroom.service", ROOT / "rootfs" / "etc" / "asound.conf"),
    (
        ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin" / "milo-client-snapclient-launcher",
        ROOT / "milo-client" / "rootfs" / "etc" / "asound.conf",
    ),
)

_SOUNDCARD = re.compile(r"--soundcard[ =]+([\w.:,=-]+)")
_PCM = re.compile(r"^pcm\.!?(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_NAMEHINT = re.compile(r"^namehint\.pcm\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_NAMEHINT_ENTRY = re.compile(r'^\s*\w+\s+"([^"|]+)\|([^"]*)"\s*$', re.MULTILINE)


def _soundcards():
    """`[(launcher, soundcard, asound.conf)]`, one per snapclient."""
    found = []
    for launcher, asound in SNAPCLIENTS:
        names = _SOUNDCARD.findall(launcher.read_text(encoding="utf-8"))
        assert len(names) == 1, f"{launcher}: expected one --soundcard, found {names}"
        found.append((launcher, names[0], asound))
    return found


def _hinted_names(asound):
    """`{NAME: DESC}` of the asound.conf's `namehint.pcm` entries."""
    blocks = _NAMEHINT.findall(asound.read_text(encoding="utf-8"))
    entries = {}
    for block in blocks:
        entries.update(_NAMEHINT_ENTRY.findall(block))
    return entries


def test_the_extraction_finds_both_snapclients():
    """A parse that stopped finding a --soundcard would leave the checks below
    passing over nothing."""
    soundcards = [name for _, name, _ in _soundcards()]
    assert len(soundcards) == 2 and all(soundcards), soundcards


def test_each_soundcard_is_a_pcm_its_tree_defines():
    """A --soundcard its tree's asound.conf does not define is a snapclient that
    opens nothing: the multiroom output of that role (server or satellite) goes
    silent."""
    for launcher, soundcard, asound in _soundcards():
        pcms = dict(_PCM.findall(asound.read_text(encoding="utf-8")))
        assert len(pcms) >= 2, f"only parsed {sorted(pcms)} from {asound}"
        assert soundcard in pcms, (
            f"{launcher.name} plays to {soundcard!r}, which {asound} does not define"
        )


def test_each_soundcard_is_listed_in_the_hint_list():
    """Otherwise snapclient logs `PCM device not found` at err on every start."""
    for launcher, soundcard, asound in _soundcards():
        listed = _hinted_names(asound)
        assert soundcard in listed, (
            f"{launcher.name} plays to {soundcard!r}, which {asound} does not list in "
            f"namehint.pcm (listed: {sorted(listed)}): snapclient logs 'PCM device not "
            "found' at err on every start"
        )
        assert listed[soundcard].strip(), f"{asound}: namehint for {soundcard!r} has no description"


def test_no_hinted_pcm_is_taken_before_the_soundcard():
    """snapclient takes the first hint whose NAME contains its --soundcard, and
    alsa-lib lists hinted PCMs before `namehint.pcm`: one named like it would
    be opened instead."""
    for launcher, soundcard, asound in _soundcards():
        for pcm, body in _PCM.findall(asound.read_text(encoding="utf-8")):
            if pcm != soundcard and re.search(r"^\s*hint\b", body, re.MULTILINE):
                assert soundcard not in pcm, (
                    f"{asound}: pcm.{pcm} is hinted and contains {soundcard!r}, so "
                    f"{launcher.name}'s snapclient would open it"
                )
        others = [name for name in _hinted_names(asound) if name != soundcard and soundcard in name]
        assert not others, f"{asound}: namehint.pcm also lists {others}, which contain {soundcard!r}"
