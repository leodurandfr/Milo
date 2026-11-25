"""Structural guardrail: a unit that keeps failing is restarted less and less often.

`Restart=always` with a fixed `RestartSec=` never trips systemd's start limit
(5 starts in 10 s), so a backend that cannot start — a venv the card damaged —
came back every ~15 s for good, re-importing everything from that card at each
start, and a kiosk over a screen declared but unplugged every 3 s. Both units
now grow the delay (`RestartSteps=`, `RestartMaxDelaySec=`, systemd ≥ 254).

What breaks when this fails: a `RestartMaxDelaySec=` at or below `RestartSec=`
is silently ignored by systemd (the delay stays constant), and a progression
too steep delays the previous release a trial falls back to — milo-update-guard
runs it at the start after `MAX_ATTEMPTS - 1` failed ones, and the owner
watches the update screen until then.

The delays are systemd's documented progression (systemd.service(5),
`RestartSteps=`): the first restart waits `RestartSec=`, the i-th
`RestartSec × (max / RestartSec) ^ ((i − 1) / steps)`, and every one past
`steps` waits the maximum. Units read from `system/`, the guard from `rootfs/`.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SYSTEM_ROOT = REPO / "system"
GUARD = REPO / "rootfs" / "usr" / "local" / "bin" / "milo-update-guard"

# The two units that restart forever: everything else in system/ is a source
# the backend starts and stops, or a daemon `PartOf=` it.
UNITS = ("milo-backend.service", "milo-kiosk.service")

# A unit failing for good restarts at most this often.
SLOWEST_AT_LEAST_S = 60
# What the restart delays add up to before the start that runs the previous
# release a trial falls back to. The failed starts' own durations are not in
# it: a start that hangs before READY=1 lasts up to the unit's TimeoutStartSec=,
# so a trial that hangs twice falls back minutes after its first failure, not
# within this.
FALLBACK_WITHIN_S = 60

_SPAN = re.compile(r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>s|sec|min|m|h)?$")
_UNIT_S = {None: 1, "s": 1, "sec": 1, "min": 60, "m": 60, "h": 3600}


def _seconds(span: str) -> float:
    match = _SPAN.match(span.strip())
    assert match, f"time span not read: {span!r}"
    return float(match["n"]) * _UNIT_S[match["unit"]]


def _service(unit: str) -> dict[str, str]:
    directives, section = {}, None
    for raw in (SYSTEM_ROOT / unit).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[Service]" and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            directives[key.strip()] = value.strip()
    assert "ExecStart" in directives, f"[Service] of {unit} not read"
    return directives


def _delays(service: dict[str, str], count: int) -> list[float]:
    """The waits before the first `count` automatic restarts."""
    first = _seconds(service["RestartSec"])
    steps = int(service.get("RestartSteps", "0"))
    most = _seconds(service["RestartMaxDelaySec"]) if "RestartMaxDelaySec" in service else None
    if not steps or most is None or most <= first:
        return [first] * count
    return [
        first if i == 1 else most if i > steps else first * (most / first) ** ((i - 1) / steps)
        for i in range(1, count + 1)
    ]


def _guard_max_attempts() -> int:
    match = re.search(r"^MAX_ATTEMPTS = (\d+)$", GUARD.read_text(encoding="utf-8"), re.M)
    assert match, "MAX_ATTEMPTS not found in milo-update-guard"
    return int(match[1])


@pytest.mark.parametrize("unit", UNITS)
def test_a_unit_that_keeps_failing_ends_up_restarted_rarely(unit):
    service = _service(unit)
    assert service["Restart"] == "always"
    delays = _delays(service, 50)
    assert delays[0] < delays[-1], f"{unit} restarts at a constant {delays[0]} s"
    assert delays[-1] >= SLOWEST_AT_LEAST_S
    assert delays == sorted(delays)


def test_the_release_a_trial_falls_back_to_is_not_held_back():
    restarts = _guard_max_attempts() - 1
    assert restarts >= 1
    waited = sum(_delays(_service("milo-backend.service"), restarts))
    assert waited <= FALLBACK_WITHIN_S, f"{waited:.0f} s of restart delays before the fallback start"
