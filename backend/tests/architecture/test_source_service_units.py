"""Structural guardrail: every source's `service_name` names a unit system/ ships.

`service_name` is the string a source hands to `SystemdServiceManager` when it
starts, and nothing confronted it with reality. The eleven per-source identity
assertions this replaces (`test_has_required_attributes`, `test_identity`)
compared the literal to a copy of itself, so renaming a unit in `system/`
without the source following left every one of them green while the source
never started on the appliance -- `systemctl start` on a unit that does not
exist, no metadata, no sound.

Both sides are derived: the source list from the typed `AudioSource` enum, the
unit list from `system/`, which is the tree both installers copy verbatim
(`install/system.sh`, `pi-gen/stage-milo/02-install-milo/01-run.sh`). A source
added to the enum is covered the day it is added.

Doctrine note (same as `test_source_conformance.py`): each extractor asserts its
own output is non-trivial first, so a broken glob or a renamed attribute fails
loudly instead of passing on an empty list.
"""
import importlib
from pathlib import Path

import pytest

from backend.core.models.audio_state import AudioSource

SYSTEM_ROOT = Path(__file__).resolve().parents[3] / "system"


def shipped_units():
    """Every systemd unit the two installers copy out of system/."""
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
