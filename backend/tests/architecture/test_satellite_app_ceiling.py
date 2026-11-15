"""Structural guardrail: the server waits on an app push longer than a satellite
can take to settle it.

After a push the satellite keeps the previous version and runs the new one on
trial: the new process must hold `HOLD_S` after READY, and the update guard puts
the previous version back once `MAX_ATTEMPTS` starts found the trial still open.
The server follows that through `/status` until it reads a final state, and
gives up at `APP_UPDATE_CEILING_S` with `did_not_return` — which stops the rest
of the fleet and leaves the row saying "not responding" about a satellite that
was about to answer `committed` or `rolled_back`.

The worst case is a new version that crashes just before its hold ends on every
start: each of the `MAX_ATTEMPTS` starts then costs a whole `TimeoutStartSec`
(the guard runs inside it, as ExecStartPre), the hold, and `RestartSec` — plus
`TimeoutStopSec` for a start that never reaches READY, which systemd only
kills once the stop it sends has timed out too (measured on Bureau: 40 s per
start, not 30). The five numbers live in three files of two trees that ship in the same commit but
are read by nobody else together, so only a rule reading all of them can hold
the inequality. Each extractor asserts its own output first.
"""
import ast
import re
from pathlib import Path

from backend.core.updates.satellite import APP_UPDATE_CEILING_S, APP_UPDATE_POLL_S

REPO = Path(__file__).resolve().parents[3]
CLIENT = REPO / "milo-client"
GUARD = CLIENT / "rootfs" / "usr" / "local" / "bin" / "milo-client-update-guard"
UNIT = CLIENT / "system" / "milo-client.service"

# Two polls: the final state can land just after one, and the waiter counts its
# sleeps, not the time its requests take.
MARGIN_S = 2 * APP_UPDATE_POLL_S


def _client_sources() -> list[Path]:
    return [GUARD, *sorted((CLIENT / "app").rglob("*.py"))]


def _module_int(name: str) -> int:
    """The value of a module-level `NAME = <int>` in the satellite's code.

    Defined in more than one file is allowed only with one value: the guard and
    the updater may each spell what they share.
    """
    values = {}
    for path in _client_sources():
        if "tests" in path.parts or not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, int)):
                values[path.relative_to(REPO)] = node.value.value
    assert values, f"no module-level `{name} = <int>` in milo-client — the extractor is broken"
    assert len(set(values.values())) == 1, f"{name} has more than one value: {values}"
    return next(iter(values.values()))


def _unit_seconds(key: str) -> int:
    """`Key=` of milo-client.service's [Service], in seconds (`30` or `30s`)."""
    service = UNIT.read_text(encoding="utf-8").split("[Service]", 1)
    assert len(service) == 2, f"{UNIT.name} has no [Service] section"
    body = service[1].split("\n[", 1)[0]
    found = re.findall(rf"^{key}=(\S+)\s*$", body, re.M)
    assert len(found) == 1, f"{UNIT.name}: expected one {key}=, found {found}"
    match = re.fullmatch(r"(\d+)s?", found[0])
    assert match, f"{UNIT.name}: {key}={found[0]} is not in seconds — extend this parse"
    return int(match.group(1))


def _settle_bound(
    hold_s: int, max_attempts: int, start_timeout_s: int, stop_timeout_s: int, restart_s: int
) -> int:
    return max_attempts * (start_timeout_s + stop_timeout_s + hold_s + restart_s)


def test_the_extractors_read_plausible_values():
    assert _module_int("HOLD_S") > 0
    assert _module_int("MAX_ATTEMPTS") >= 2
    assert _unit_seconds("TimeoutStartSec") > 0
    assert _unit_seconds("RestartSec") > 0
    assert _unit_seconds("TimeoutStopSec") > 0


def test_the_server_outwaits_the_slowest_trial_a_satellite_can_run():
    bound = _settle_bound(
        _module_int("HOLD_S"), _module_int("MAX_ATTEMPTS"),
        _unit_seconds("TimeoutStartSec"), _unit_seconds("TimeoutStopSec"),
        _unit_seconds("RestartSec"),
    )
    assert APP_UPDATE_CEILING_S > bound + MARGIN_S, (
        f"APP_UPDATE_CEILING_S ({APP_UPDATE_CEILING_S} s) must exceed the slowest trial "
        f"({bound} s) plus {MARGIN_S} s, or a satellite about to settle is reported "
        "as not responding and the fleet push stops on it"
    )
