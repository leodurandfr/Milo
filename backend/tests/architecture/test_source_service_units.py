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
"""
import importlib
from pathlib import Path

import pytest

from backend.core.models.audio_state import AudioSource
from backend.sources.bluetooth import BluetoothSource
from backend.tests.architecture.test_unit_hardening import service_section, sets_no_new_privileges

SYSTEM_ROOT = Path(__file__).resolve().parents[3] / "system"


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
