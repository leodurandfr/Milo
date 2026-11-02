"""Structural guardrail: BlueALSA writes the phone's samples at unity.

CamillaDSP is the only attenuation stage (invariant 7). The other sources hold
their daemon at unity by default (Spotify's and Qobuz's app volume off unless the
user opts in, AirPlay's `ignore_volume_control`); Bluetooth's daemon is the one
that has to be told. bluez-alsa 4.3.1 turns soft-volume on for every A2DP sink
transport unless asked otherwise, multiplies each decoded sample by the AVRCP
absolute volume the phone sends, and `bluealsa-aplay --volume=auto` (its
default) leaves that mode alone. `--volume=none` puts the PCM in native mode: no scaling in BlueALSA, and
no write to the card's ALSA mixer either, which `milo-alsa-passthrough` pins at
unity.

Without it the room sits below the level Milō shows by however far the phone's
slider is down, and jumps to Milō's level, with no `volume_changed` and nothing
moving on screen, when another phone whose remembered slider is at 100 % takes
the link. In multiroom the hidden stage applies to every room.

The consumer is pid 1 and BlueALSA reading a `.ini` no test runner touches, so
the argv is the contract and the only surface there is to assert. As in the
other guardrails here, the extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface.
"""
import re
import shlex
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

APLAY_UNIT = REPO_ROOT / "system" / "milo-bluealsa-aplay.service"

# A systemd line continuation: the trailing backslash and the newline it hides.
CONTINUATION_RE = re.compile(r"\\\n\s*")


def _exec_start_argv(unit: Path) -> list[str]:
    """The unit's `ExecStart=` as argv, continuations joined; empty if none."""
    joined = CONTINUATION_RE.sub(" ", unit.read_text(encoding="utf-8"))
    for line in joined.splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line.removeprefix("ExecStart="))
    return []


def _volume_modes(argv: list[str]) -> list[str]:
    """Every `--volume` value the argv passes, in either spelling."""
    modes = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--volume=")]
    modes += [value for flag, value in zip(argv, argv[1:]) if flag == "--volume"]
    return modes


def test_bluealsa_aplay_keeps_samples_at_unity():
    """`--volume=none`, and only that, or the phone's slider is a second stage."""
    assert APLAY_UNIT.is_file(), f"{APLAY_UNIT} is missing"
    argv = _exec_start_argv(APLAY_UNIT)
    assert argv, f"no ExecStart= line read from {APLAY_UNIT.name}"
    assert argv[0].endswith("bluealsa-aplay"), f"ExecStart= does not run bluealsa-aplay: {argv}"
    assert "--pcm=milo_bluetooth" in argv, f"argv parsed without its PCM: {argv}"

    modes = _volume_modes(argv)
    assert modes == ["none"], (
        f"bluealsa-aplay must run with --volume=none (BlueALSA at unity, CamillaDSP "
        f"the only attenuation stage); its --volume values are {modes}: {argv}"
    )
