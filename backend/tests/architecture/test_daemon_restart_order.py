"""Structural guardrail: the backend stops before the daemons it is connected to.

`PartOf=milo-backend.service` propagates a stop and a restart, and orders
nothing. Without an `After=` on the backend, systemd stopped CamillaDSP and the
two snapcast units in parallel with it, often before its teardown began, and
the old process — its snapcast WebSocket, its registry, CrossoverService and
the CamillaDSP keepalive still running — reacted to its own daemons going down:
`CLIENT DISCONNECTED`, keepalive warnings in errors.log, and a crossover and a
lowpass pushed over HTTP to a satellite, which rewrote its config on the SD
card. Measured on every backend restart and at shutdown.

`After=` on the backend reverses at stop: the backend goes first, then the
daemons. A daemon that is itself `After=milo-backend.service` (Navidrome, which
the backend starts) is the other direction on purpose and is left out.

The units are read from `system/`, the tree the image build copies verbatim.
"""

import re
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parents[3] / "system"
BACKEND = "milo-backend.service"

_SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")


def unit_section(unit: Path) -> dict[str, list[str]]:
    """The `[Unit]` section's list directives, each split on whitespace.

    A trailing backslash continues the line and an empty assignment resets the
    list, as systemd reads them.
    """
    directives: dict[str, list[str]] = {}
    section = None
    text = re.sub(r"\\\n", " ", unit.read_text(encoding="utf-8"))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        header = _SECTION.match(line)
        if header:
            section = header.group("name")
            continue
        if section == "Unit" and "=" in line:
            key, _, value = line.partition("=")
            names = value.split()
            if names:
                directives.setdefault(key.strip(), []).extend(names)
            else:
                directives[key.strip()] = []
    return directives


def units() -> dict[str, dict[str, list[str]]]:
    found = {unit.name: unit_section(unit) for unit in sorted(SYSTEM_ROOT.glob("*.service"))}
    assert BACKEND in found, f"{BACKEND} not found under {SYSTEM_ROOT}"
    return found


def held_daemons(all_units: dict[str, dict[str, list[str]]]) -> list[str]:
    """The units the backend holds: `PartOf=` it, and not started behind it."""
    return [
        name
        for name, directives in all_units.items()
        if BACKEND in directives.get("PartOf", [])
        and BACKEND not in directives.get("After", [])
    ]


def test_the_backend_is_ordered_after_every_partof_daemon_it_holds_a_connection_to():
    all_units = units()
    daemons = held_daemons(all_units)
    # CamillaDSP and the two snapcast units: a parse that finds fewer has
    # stopped reading the files, not proved the ordering.
    assert len(daemons) >= 3, f"expected at least three PartOf daemons, found {daemons}"

    backend_after = set(all_units[BACKEND].get("After", []))
    missing = [name for name in daemons if name not in backend_after]
    assert not missing, (
        f"{BACKEND} is not After= {missing}: they are PartOf= it, so a restart or a "
        "shutdown stops them in parallel with the backend, which then reacts to its "
        "own daemons going down before its teardown runs"
    )


def test_no_partof_daemon_is_ordered_after_the_backend_it_precedes():
    """An `After=` chain from a held daemon back to the backend is an ordering
    cycle: systemd would break it by dropping a job at boot, not refuse it."""
    all_units = units()
    after: dict[str, set[str]] = {name: set(d.get("After", [])) for name, d in all_units.items()}
    for name, directives in all_units.items():
        for later in directives.get("Before", []):
            after.setdefault(later, set()).add(name)

    # Every PartOf unit the backend is ordered after, not only the held ones: a
    # daemon also `After=` the backend would drop out of held_daemons and hide
    # the direct cycle.
    backend_after = set(all_units[BACKEND].get("After", []))
    daemons = [
        name for name, d in all_units.items()
        if BACKEND in d.get("PartOf", []) and name in backend_after
    ]
    assert len(daemons) >= 3, f"expected at least three PartOf daemons, found {daemons}"

    for daemon in daemons:
        seen, frontier = set(), [daemon]
        while frontier:
            unit = frontier.pop()
            for earlier in after.get(unit, ()):
                assert earlier != BACKEND, (
                    f"{daemon} reaches {BACKEND} through After= ({unit} is After= it), "
                    f"while {BACKEND} is After= {daemon}: an ordering cycle"
                )
                if earlier not in seen:
                    seen.add(earlier)
                    frontier.append(earlier)
