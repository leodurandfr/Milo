"""Structural guardrail: both CamillaDSP units start muted at the same floor.

CamillaDSP is the only attenuation stage (invariant 7) and it is started twice
from this checkout — `system/milo-camilladsp.service` on the server,
`milo-client/system/milo-client-camilladsp.service` on every satellite. Two
`ExecStart=` lines, two deployment trees, and until now nothing tying them
together.

Measured 2026-09-20 01:43:23 on the Canapé satellite (HiFiBerry Amp2). An app
update restarted `milo-client.service`; `BindsTo=` took CamillaDSP down with it
and brought it back 2.5 s later. The server pushed the level — 400, the
satellite's daemon was not accepting connections yet — then pushed the unmute,
which answered 200 because it was what opened the connection. `--gain=-60.0` had
been added to the server's unit in `06593280` and to the server's unit only, so
the satellite's main fader came up at 0 dB: ~3 s of full-scale music in a room
set to -52.8 dB, about 437x in amplitude, into an amplifier.

Nothing else catches this class. These are two `.ini` files read by pid 1 on
machines no test runner touches: there is no import to fail and no route to 404.
`test_rootfs_deployment.py`'s twin mechanism does not apply either — the two
units are deliberately different (user, paths, `BindsTo=` vs `PartOf=`, the
`sh -c` hardware.json guard), so only a rule naming the shared *flags* can hold.

The Python mirror is in scope for the same reason. The satellite's connection
loop is the only thing that opens a connection to its daemon, and it pushes
`_volume["main"]` on every connect, the first one included — so that cache is
what the fader holds until the server's first push lands. A unit at the floor and
a cache at unity is the same bug wearing the other half's clothes.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import shlex
from pathlib import Path

import pytest

from backend.config.constants import MIN_VOLUME_DB

REPO_ROOT = Path(__file__).resolve().parents[3]

UNITS = {
    "server": REPO_ROOT / "system" / "milo-camilladsp.service",
    "milo-client": REPO_ROOT / "milo-client" / "system" / "milo-client-camilladsp.service",
}

SATELLITE_SERVICE = REPO_ROOT / "milo-client" / "app" / "services" / "equalizer.py"

# The declaration the satellite's volume cache starts from, mirrored from its unit.
STARTUP_GAIN_RE = re.compile(r"^STARTUP_GAIN_DB\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.MULTILINE)

# A systemd line continuation: the trailing backslash and the newline it hides.
CONTINUATION_RE = re.compile(r"\\\n\s*")


def _camilladsp_argv(unit: Path) -> list[str]:
    """The camilladsp invocation inside an `ExecStart=`, as argv.

    Both spellings have to read: the server's bare multi-line `ExecStart=`, and
    the satellite's `/bin/sh -c '…'` wrapper, whose closing quote is stripped
    before tokenising since shlex refuses an unbalanced one.
    """
    joined = CONTINUATION_RE.sub(" ", unit.read_text(encoding="utf-8"))
    for line in joined.splitlines():
        if not line.startswith("ExecStart="):
            continue
        match = re.search(r"/usr/local/bin/camilladsp\s+(.*)", line)
        if match:
            return shlex.split(match.group(1).rstrip().rstrip("'"))
    return []


def _gain(argv: list[str]) -> float | None:
    """The startup gain the argv asks for, in dB, in either spelling."""
    for arg in argv:
        if arg.startswith("--gain="):
            return float(arg.split("=", 1)[1])
        if arg.startswith("-g="):
            return float(arg.split("=", 1)[1])
    for flag, value in zip(argv, argv[1:]):
        if flag in ("--gain", "-g"):
            return float(value)
    return None


def test_the_argv_extractor_reads_both_invocations():
    """A unit parsed as an empty argv would make every rule below vacuous."""
    for name, unit in UNITS.items():
        assert unit.is_file(), f"{unit} is missing"
        argv = _camilladsp_argv(unit)
        assert len(argv) >= 5, f"{name}: camilladsp argv parsed as {argv}"
        assert argv[-1].endswith("config.yml"), f"{name}: argv does not end on a config: {argv}"
        assert "-p" in argv, f"{name}: no websocket port in {argv}"


def _declared_startup_gain() -> float:
    """The satellite's mirrored constant, or a failure naming what is missing."""
    found = STARTUP_GAIN_RE.findall(SATELLITE_SERVICE.read_text(encoding="utf-8"))
    assert len(found) == 1, (
        f"STARTUP_GAIN_DB is declared {len(found)} time(s) in "
        f"{SATELLITE_SERVICE.relative_to(REPO_ROOT)}"
    )
    return float(found[0])


def test_the_python_mirror_is_declared_exactly_once():
    """A regex that matched nothing would make the equality below vacuous."""
    _declared_startup_gain()


@pytest.mark.parametrize("name", sorted(UNITS))
def test_both_camilladsp_units_start_muted(name):
    """`-m` is the first half of the floor: silent until someone says otherwise."""
    argv = _camilladsp_argv(UNITS[name])
    assert "-m" in argv or "--mute" in argv, (
        f"{name} does not start CamillaDSP muted: {argv}"
    )


def test_both_camilladsp_units_start_at_the_same_gain_floor():
    """The second half: what the fader holds the instant the mute is lifted.

    An unmute can reach a daemon before its level does — the server sends them
    as two calls and only the second one is unconditional
    (`websocket.py::_apply_target_volume_to_client`, deliberately: a muted
    speaker at the wrong level is worse than an unmuted one). Whichever unit is
    missing this flag is the speaker that answers that unmute at full scale.
    """
    gains = {name: _gain(_camilladsp_argv(unit)) for name, unit in UNITS.items()}
    missing = sorted(name for name, gain in gains.items() if gain is None)
    assert not missing, (
        f"no startup gain on the CamillaDSP unit(s): {', '.join(missing)} (have {gains})"
    )
    assert len(set(gains.values())) == 1, (
        f"the two units disagree on the startup floor: {gains}"
    )


def test_the_startup_floor_is_at_or_below_the_appliance_minimum():
    """Equal floors are not enough — they have to be *low*.

    The rule above only pins the two units to each other, so moving both to
    `--gain=0.0` would satisfy it and reinstate the incident on every speaker at
    once. What makes a floor a floor is that nothing can be heard through it, so
    it is measured against `MIN_VOLUME_DB`, the lowest level the backend will
    represent: the clamp on `limit_min_db`, on the state store, and on the
    satellite's own fader all stop there. CamillaDSP accepts down to -120 dB,
    but a floor below what the rest of the code can hold would be reported by a
    satellite and silently clamped by the server — one state, two numbers.
    """
    gains = {name: _gain(_camilladsp_argv(unit)) for name, unit in UNITS.items()}
    too_loud = {name: gain for name, gain in gains.items() if gain is None or gain > MIN_VOLUME_DB}
    assert not too_loud, (
        f"startup floor above the appliance minimum ({MIN_VOLUME_DB} dB): {too_loud}"
    )
    declared = _declared_startup_gain()
    assert declared <= MIN_VOLUME_DB, (
        f"STARTUP_GAIN_DB is {declared}, above the appliance minimum ({MIN_VOLUME_DB} dB)"
    )


def test_the_satellite_volume_cache_starts_at_its_units_floor():
    """The loop pushes this cache on every connect, the first one included.

    Nothing else writes the fader between the daemon's start and the server's
    first push, so a cache above the unit's floor re-opens the very speaker the
    flag was added to close.
    """
    declared = _declared_startup_gain()
    unit_gain = _gain(_camilladsp_argv(UNITS["milo-client"]))
    assert declared == unit_gain, (
        f"STARTUP_GAIN_DB is {declared} but milo-client-camilladsp.service "
        f"starts the daemon at {unit_gain}"
    )


@pytest.mark.parametrize("name", sorted(UNITS))
def test_both_camilladsp_units_pin_the_card_mixer_at_unity(name):
    """Invariant 7's other half, unguarded until now.

    CamillaDSP is the only attenuation stage only if the card's own mixer sits
    at unity, and that is `milo-alsa-passthrough`, wired as `ExecStartPre=` of
    both units. A unit that loses the line is a card attenuating under a DSP
    that believes it owns the level — and it shows up on DAC boards only, so one
    unit in a two-unit fleet hides it.
    """
    text = UNITS[name].read_text(encoding="utf-8")
    assert re.search(
        r"^ExecStartPre=-?/usr/local/bin/milo-alsa-passthrough\s*$", text, re.MULTILINE
    ), f"{name} does not run milo-alsa-passthrough before CamillaDSP opens the card"
