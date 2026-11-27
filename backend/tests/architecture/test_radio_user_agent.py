"""Structural guardrail: the radio's two connections to a station send one agent.

A station is reached twice from this checkout: by the radio's mpv
(`system/milo-radio.service`, its `--user-agent`) and by every Shazam capture
(`sources/radio/shazam.py::STREAM_USER_AGENT`, handed to ffmpeg). Infomaniak's
streaming server splices a 15-30 s ad over the start of a new connection whose
agent is a known player (`libmpv`, `Lavf/…`, VLC, browsers, `Milo/1.0`) and
sends `curl/…` the live stream (measured 2026-11-27 on Nova, each connection's
audio aligned against a continuous reference).

The two halves fail differently and silently. The unit drifting alone opens
every tune on the ad; the capture drifting alone makes Shazam fingerprint the
ad. Neither raises, logs or changes a state, and one is an `.ini` file pid 1
reads on a machine no test runner touches — so only a rule naming both holds.

Doctrine note: the extractor asserts its own output is non-trivial first, so a
broken parse fails loudly instead of passing on an empty surface.
"""
import re
import shlex
from pathlib import Path

from backend.sources.radio.shazam import STREAM_USER_AGENT

REPO_ROOT = Path(__file__).resolve().parents[3]
RADIO_UNIT = REPO_ROOT / "system" / "milo-radio.service"

# A systemd line continuation: the trailing backslash and the newline it hides.
CONTINUATION_RE = re.compile(r"\\\n\s*")


def _mpv_argv() -> list[str]:
    """The radio unit's `ExecStart=`, as argv."""
    text = CONTINUATION_RE.sub(" ", RADIO_UNIT.read_text())
    lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
    assert len(lines) == 1, f"expected one ExecStart= in {RADIO_UNIT.name}, found {len(lines)}"
    argv = shlex.split(lines[0].removeprefix("ExecStart="))
    assert argv[0].endswith("/mpv") and "--input-ipc-server=/run/milo/radio-ipc.sock" in argv, (
        f"the extractor did not read mpv's invocation out of {RADIO_UNIT.name}: {argv}"
    )
    return argv


def test_the_radio_unit_and_the_shazam_capture_send_the_same_agent():
    agents = [arg.removeprefix("--user-agent=") for arg in _mpv_argv()
              if arg.startswith("--user-agent=")]

    assert agents == [STREAM_USER_AGENT], (
        f"{RADIO_UNIT.name} sends {agents or 'no agent (mpv default: libmpv)'}, Shazam's "
        f"captures send {STREAM_USER_AGENT!r}: one of the two connections gets the "
        "ad a splicing server reserves for players"
    )
