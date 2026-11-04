"""Structural guardrail: every source's `service_name` names a unit system/ ships.

`service_name` is the string a source hands to `SystemdServiceManager` when it
starts, and nothing confronted it with reality. The eleven per-source identity
assertions this replaces (`test_has_required_attributes`, `test_identity`)
compared the literal to a copy of itself, so renaming a unit in `system/`
without the source following left every one of them green while the source
never started on the appliance -- `systemctl start` on a unit that does not
exist, no metadata, no sound.

Both sides are derived: the source list from the typed `AudioSource` enum, the
unit list from `system/`, which is the tree the image build copies verbatim
(`pi-gen/stage-milo/02-install-milo/01-run.sh`). A source
added to the enum is covered the day it is added.

Doctrine note (same as `test_source_conformance.py`): each extractor asserts its
own output is non-trivial first, so a broken glob or a renamed attribute fails
loudly instead of passing on an empty list.

`service_name` is not the only unit the backend spells: `bluealsa_aplay_service`,
`QOBUZ_SERVICE`, the settings `services_map`, the update catalog and the
multiroom routing all name units as literals of their own. The tests at the end
read every one of them out of the backend source and hold it to the same tree.
"""
import ast
import functools
import importlib
import inspect
import re
from pathlib import Path

import pytest

from backend.core.models.audio_state import AudioSource
from backend.core.systemd import SystemdServiceManager
from backend.sources.bluetooth import BluetoothSource
from backend.tests.architecture.test_unit_hardening import service_section, sets_no_new_privileges

REPO_ROOT = Path(__file__).resolve().parents[3]
SYSTEM_ROOT = REPO_ROOT / "system"
BACKEND_ROOT = REPO_ROOT / "backend"


def shipped_units():
    """Every systemd unit the image build copies out of system/."""
    units = {p.name for p in SYSTEM_ROOT.glob("*.service")}
    assert len(units) >= 15, (
        f"only {sorted(units)} found under {SYSTEM_ROOT} — the glob is broken"
    )
    return units


SHIPPED_UNITS = shipped_units()


def source_ids():
    """Every real audio source, from the typed enum (NONE is not a source)."""
    ids = sorted(s.value for s in AudioSource if s is not AudioSource.NONE)
    assert len(ids) >= 10, (
        f"AudioSource enum yielded only {ids} — the extractor is broken"
    )
    return ids


SOURCE_IDS = source_ids()


def declared_service_name(source_id):
    """The unit the source will ask systemd for, read off a real instance.

    Read from an instance rather than from the source text: `service_name` is a
    constructor argument, so the literal in `source.py` is not proof that it
    reaches the attribute `_start_service` uses.
    """
    package = importlib.import_module(f"backend.sources.{source_id}")
    exported = getattr(package, "__all__", [])
    assert len(exported) == 1, (
        f"backend.sources.{source_id}.__all__ = {exported} — the extractor is broken"
    )
    name = getattr(package, exported[0])().service_name
    assert name, f"{source_id} exposes an empty service_name — the extractor is broken"
    return name


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_source_service_name_is_a_unit_the_repo_ships(source_id):
    """A source may only name a unit that system/ actually deploys."""
    name = declared_service_name(source_id)
    assert name in SHIPPED_UNITS, (
        f"{source_id}.service_name is {name!r}, which system/ does not ship. "
        f"Either the unit was renamed without the source following, or the "
        f"unit file is missing: systemctl would fail to start it on the unit. "
        f"Shipped: {sorted(SHIPPED_UNITS)}"
    )


def test_no_two_sources_claim_the_same_unit():
    """One unit per source: a shared unit means stopping one stops the other."""
    by_unit = {}
    for source_id in SOURCE_IDS:
        by_unit.setdefault(declared_service_name(source_id), []).append(source_id)
    shared = {unit: ids for unit, ids in by_unit.items() if len(ids) > 1}
    assert not shared, (
        f"sources share a systemd unit: {shared} — stopping one source would "
        f"tear down the other's daemon"
    )


def source_units():
    """Every unit a source starts: its `service_name`, plus Bluetooth's player."""
    units = {declared_service_name(source_id) for source_id in SOURCE_IDS}
    units.add(BluetoothSource().bluealsa_aplay_service)
    assert len(units) >= 11, f"only {sorted(units)} extracted — the extractor is broken"
    return sorted(units)


@pytest.mark.parametrize("unit_name", source_units())
def test_every_source_unit_runs_without_new_privileges_and_a_read_only_home(unit_name):
    """A source daemon parses what the network or the radio sends it.

    `NoNewPrivileges` keeps a compromised daemon out of the backend's sudo grants,
    and `ProtectHome=read-only` keeps it from rewriting the backend's own code in
    /home/milo/milo, which systemd would then run with those grants. A daemon that
    needs a writable HOME is given one under /var/lib/milo, never a hole in /home.
    """
    directives = service_section(SYSTEM_ROOT / unit_name)
    assert directives.get("User") == ["milo"], (
        f"{unit_name}'s [Service] section did not parse (User={directives.get('User')})"
    )
    assert sets_no_new_privileges(directives), (
        f"{unit_name} lacks NoNewPrivileges=true: it can run the backend's sudo grants"
    )
    assert directives.get("ProtectHome", [""])[-1] == "read-only", (
        f"{unit_name} lacks ProtectHome=read-only: it can rewrite the backend's checkout"
    )
    reopened = [
        path
        for key in ("ReadWritePaths", "BindPaths")
        for value in directives.get(key, [])
        for path in value.split()
        if path.lstrip("-+").startswith("/home")
    ]
    assert not reopened, f"{unit_name} reopens /home: {reopened}"


UNIT_LITERAL = re.compile(r"[A-Za-z0-9@_-]+\.service")

# Units the backend names that the OS installs, not system/. Each one is held
# below to two facts -- system/ does not ship it, and the backend still names
# it -- so an entry cannot outlive its reason or excuse a unit of our own.
OS_UNITS = {
    "bluetooth.service": "BlueZ, from the bluez package",
    "ssh.service": "openssh-server",
    "avahi-daemon.service": "the avahi package; system/ ships only its override",
    "NetworkManager.service": "the network-manager package",
    "wpa_supplicant.service": "the wpasupplicant package",
}


def systemd_unit_methods():
    """`SystemdServiceManager` methods whose first parameter is the unit."""
    methods = {
        name
        for name, member in inspect.getmembers(SystemdServiceManager, inspect.isfunction)
        if not name.startswith("_")
        and list(inspect.signature(member).parameters)[1:2] == ["service"]
    }
    assert {"start", "restart", "probe_active"} <= methods, (
        f"SystemdServiceManager unit methods read as {sorted(methods)} — the extractor is broken"
    )
    return methods


# Callees that take a unit name, which systemctl and journalctl both accept
# without its `.service`: the keyword that names it, and whether the receiver
# must be a systemd manager (`start`/`stop` alone are far too common a name).
UNIT_CALLEES = {
    "follow_unit": ("unit", False),
    **{name: ("service", True) for name in systemd_unit_methods()},
}


def _receiver_is_systemd(func):
    receiver = func.value if isinstance(func, ast.Attribute) else None
    while isinstance(receiver, ast.Attribute):
        if "systemd" in receiver.attr:
            return True
        receiver = receiver.value
    return isinstance(receiver, ast.Name) and "systemd" in receiver.id


def unit_sites(tree, where):
    """Every unit name spelled in one module, as (file:line, unit, by_call).

    Two shapes: a string literal that is a whole `<name>.service`, and a
    suffix-less literal handed to a callee of UNIT_CALLEES (`by_call`).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if UNIT_LITERAL.fullmatch(node.value):
                yield f"{where}:{node.lineno}", node.value, False
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in UNIT_CALLEES:
            continue
        keyword, needs_systemd = UNIT_CALLEES[name]
        if needs_systemd and not _receiver_is_systemd(func):
            continue
        unit = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == keyword), None
        )
        if (
            isinstance(unit, ast.Constant)
            and isinstance(unit.value, str)
            and not unit.value.endswith(".service")
        ):
            yield f"{where}:{node.lineno}", f"{unit.value}.service", True


@functools.cache
def backend_unit_sites():
    """Every unit name the backend spells, as (file:line, unit), outside its tests."""
    tests_root = BACKEND_ROOT / "tests"
    sites = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if tests_root not in path.parents:
            tree = ast.parse(path.read_text(), str(path))
            sites.extend(unit_sites(tree, path.relative_to(REPO_ROOT)))
    names = {unit for _, unit, _ in sites}
    files = {where.rsplit(":", 1)[0] for where, _, _ in sites}
    by_call = [where for where, _, call in sites if call]
    assert len(sites) >= 40 and len(files) >= 15, (
        f"only {len(sites)} unit names in {len(files)} files extracted — the extractor is broken"
    )
    assert {"milo-bluealsa-aplay.service", "milo-qobuz.service"} <= names, (
        f"the extractor missed a unit it is known to name: {sorted(names)}"
    )
    assert len(by_call) >= 2, (
        f"only {by_call} suffix-less unit names extracted from calls — that arm is broken"
    )
    return [(where, unit) for where, unit, _ in sites]


def test_the_extractor_reads_every_shape_a_unit_is_spelled_in():
    """Run the extractor on a drift it must see, so a green run means something."""
    tree = ast.parse(
        'A = "milo-typo.service"\n'
        'follow_unit("milo-typo-a", consequence="")\n'
        'follow_unit(unit="milo-typo-b", consequence="")\n'
        'self._systemd.restart("milo-typo-c")\n'
        'manager.start("not-a-unit")\n'
        'B = "core.push.service"\n'
    )
    found = sorted((unit, by_call) for _, unit, by_call in unit_sites(tree, "x.py"))
    assert found == [
        ("milo-typo-a.service", True),
        ("milo-typo-b.service", True),
        ("milo-typo-c.service", True),
        ("milo-typo.service", False),
    ]


def test_os_units_excuse_only_what_the_backend_names_and_system_does_not_ship():
    """An OS_UNITS entry is a reason the backend may name an unshipped unit.

    Excusing a unit system/ ships would hide its rename; an entry nothing names
    any more is a comment, not a contract.
    """
    shipped = sorted(set(OS_UNITS) & SHIPPED_UNITS)
    assert not shipped, f"system/ ships {shipped}: it must not be excused in OS_UNITS"
    named = {unit for _, unit in backend_unit_sites()}
    stale = sorted(set(OS_UNITS) - named)
    assert not stale, f"OS_UNITS lists {stale}, which the backend no longer names: drop them"


def test_every_unit_the_backend_names_is_shipped():
    """Every unit the backend asks systemd or journalctl about is one that exists.

    A literal that drifts from system/ fails on the unit and nowhere else: a
    Bluetooth source whose writer never starts, a Qobuz login answering 409
    because it probes a unit that is never active.
    """
    known = SHIPPED_UNITS | set(OS_UNITS)
    unknown = [f"{where}: {unit}" for where, unit in backend_unit_sites() if unit not in known]
    assert not unknown, (
        "the backend names units system/ does not ship and OS_UNITS does not excuse:\n  "
        + "\n  ".join(unknown)
        + f"\nShipped: {sorted(SHIPPED_UNITS)}"
    )
