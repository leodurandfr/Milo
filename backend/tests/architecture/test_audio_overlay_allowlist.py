"""Structural guardrail: one overlay allowlist, derived — never restated.

`backend/hardware/registry.py::AUDIO_CARDS` is the single source of truth for
which audio cards the UI offers and which `dtoverlay=` each one writes. Three
other files restate that set, and all three are the *last* gate before the
overlay reaches `config.txt`:

  * `rootfs/usr/local/lib/milo/hardware-helpers.sh` — sourced by
    `milo-apply-hardware`, which `exit 1`s on an unknown overlay;
  * `milo-client/rootfs/…/hardware-helpers.sh` — its declared twin, sourced by
    `milo-client-apply-hardware`;
  * `milo-client/app/routes/hardware.py::VALID_OVERLAYS` — a Pydantic validator,
    so an unknown overlay is a 422 before the script ever runs.

The divergence this pins already shipped. `cab75098` added the DAC2 ADC Pro and
pinned the DAC2 Pro to the split overlay, touching `registry.py` alone: two of
the eight cards the UI offered were rejected by all three lists. Picking
"HiFiBerry DAC2 Pro" made `milo-apply-hardware` exit 1 with no reboot and no
card on the server, and made the satellite answer 422 → 502 → a failed pairing
wizard.

Nothing else catches this class, and one existing guardrail actively hides it:
`test_rootfs_deployment.py::test_twin_files_have_not_drifted` passes because
both shell copies are *equally* stale — it proves the twins agree, not that they
are right.

`milo-apply-hardware` restates `registry.py::SCREENS` the same way, twice
(`VALID_SCREENS` and the arms of its screen `case`), so that set is held here
too.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing
on an empty surface.
"""
import ast
import re
from pathlib import Path

import pytest

from backend.hardware.registry import AUDIO_CARDS, SCREENS

REPO_ROOT = Path(__file__).resolve().parents[3]

SHELL_TWINS = {
    "server": REPO_ROOT / "rootfs/usr/local/lib/milo/hardware-helpers.sh",
    "milo-client": REPO_ROOT / "milo-client/rootfs/usr/local/lib/milo/hardware-helpers.sh",
}
SATELLITE_ROUTE = REPO_ROOT / "milo-client/app/routes/hardware.py"
APPLY_SCRIPT = REPO_ROOT / "rootfs/usr/local/bin/milo-apply-hardware"

# Overlays no card selects any more, still swept out of config.txt because units
# installed before the kernel 6.1.77 overlay split carry the line. They are
# deliberately absent from the allowlist: an overlay that can be cleaned up is
# not an overlay that can be applied.
DEPRECATED_ALIASES = {"hifiberry-dacplus"}

SHELL_LIST_RE = re.compile(r'^(VALID_AUDIO_OVERLAYS|LEGACY_SWEEP_OVERLAYS)="([^"]*)"', re.MULTILINE)


def _registry_overlays() -> set[str]:
    return {card["overlay"] for card in AUDIO_CARDS.values() if card["overlay"]}


def _shell_lists(path: Path) -> dict[str, set[str]]:
    """Both overlay variables of a hardware-helpers.sh, expanded.

    `LEGACY_SWEEP_OVERLAYS` is declared as `"$VALID_AUDIO_OVERLAYS <aliases>"`,
    which is the point — the sweep is the allowlist plus what it dropped — so the
    reference has to be substituted rather than read as a literal.
    """
    found = dict(SHELL_LIST_RE.findall(path.read_text()))
    missing = {"VALID_AUDIO_OVERLAYS", "LEGACY_SWEEP_OVERLAYS"} - set(found)
    assert not missing, f"{path.name}: no double-quoted assignment for {sorted(missing)}"

    valid = set(found["VALID_AUDIO_OVERLAYS"].split())
    sweep = set(
        found["LEGACY_SWEEP_OVERLAYS"]
        .replace("$VALID_AUDIO_OVERLAYS", found["VALID_AUDIO_OVERLAYS"])
        .split()
    )
    assert "$" not in " ".join(sweep), f"{path.name}: unexpanded variable in LEGACY_SWEEP_OVERLAYS"
    return {"valid": valid, "sweep": sweep}


def _route_overlays() -> set[str]:
    module = ast.parse(SATELLITE_ROUTE.read_text())
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "VALID_OVERLAYS" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    pytest.fail(f"{SATELLITE_ROUTE.name}: no module-level VALID_OVERLAYS assignment")


def test_extractors_are_not_vacuous():
    """A parse that silently returns nothing would make every assertion below pass."""
    registry = _registry_overlays()
    assert len(registry) >= 5, f"registry overlay set collapsed: {sorted(registry)}"
    assert len(_route_overlays()) >= 5

    for name, path in SHELL_TWINS.items():
        lists = _shell_lists(path)
        assert len(lists["valid"]) >= 5, f"{name}: {sorted(lists['valid'])}"


@pytest.mark.parametrize("tree", sorted(SHELL_TWINS))
def test_shell_allowlist_matches_the_registry(tree):
    """`validate_audio_overlay` rejecting a card the UI offers = exit 1, no reboot, no sound."""
    valid = _shell_lists(SHELL_TWINS[tree])["valid"]
    registry = _registry_overlays()

    assert valid == registry, (
        f"{SHELL_TWINS[tree].relative_to(REPO_ROOT)} disagrees with AUDIO_CARDS — "
        f"offered but rejected: {sorted(registry - valid)}; "
        f"granted but never offered: {sorted(valid - registry)}"
    )


def test_satellite_route_allowlist_matches_the_registry():
    """A card missing from `VALID_OVERLAYS` is a 422, then a 502, then a failed pairing."""
    route = _route_overlays()
    registry = _registry_overlays()

    assert route == registry, (
        f"{SATELLITE_ROUTE.relative_to(REPO_ROOT)}::VALID_OVERLAYS disagrees with AUDIO_CARDS — "
        f"offered but rejected: {sorted(registry - route)}; "
        f"granted but never offered: {sorted(route - registry)}"
    )


@pytest.mark.parametrize("tree", sorted(SHELL_TWINS))
def test_legacy_sweep_covers_the_allowlist_and_the_aliases(tree):
    """`remove_legacy_overlays` clears a stale line before the managed block is written.

    It reads its own list, not the allowlist: an overlay dropped from the
    allowlist still has to be cleaned off units that already wrote it.
    """
    lists = _shell_lists(SHELL_TWINS[tree])

    assert lists["sweep"] == _registry_overlays() | DEPRECATED_ALIASES, (
        f"{SHELL_TWINS[tree].relative_to(REPO_ROOT)}: LEGACY_SWEEP_OVERLAYS must cover every "
        f"overlay Milō can write plus the deprecated aliases — got {sorted(lists['sweep'])}"
    )


def test_the_apply_script_screen_types_match_the_registry():
    """`registry.SCREENS` is what the UI offers and the route validates; the
    script restates it twice. A screen missing from `VALID_SCREENS` makes the
    apply exit 1 after hardware.json was written; one listed there but with no
    `case` arm writes no MILO SCREEN block and still enables the kiosk, exit 0."""
    text = APPLY_SCRIPT.read_text()
    listed = re.search(r'^VALID_SCREENS="([^"]*)"', text, re.MULTILINE)
    assert listed, "no VALID_SCREENS= assignment in milo-apply-hardware"
    valid = set(listed[1].split())

    arms_block = re.search(r'^case "\$SCREEN_TYPE" in\n(.*?)^esac', text, re.MULTILINE | re.DOTALL)
    assert arms_block, 'no `case "$SCREEN_TYPE" in … esac` in milo-apply-hardware'
    arms = {
        label
        for labels in re.findall(r'^\s+([\w|"]+)\)\s*(?:#.*)?$', arms_block[1], re.MULTILINE)
        for label in labels.split("|")
        if label != '""'
    }

    assert len(valid) >= 2 and len(arms) >= 2, (sorted(valid), sorted(arms))
    assert valid == set(SCREENS), (
        f"VALID_SCREENS disagrees with SCREENS — offered but rejected: {sorted(set(SCREENS) - valid)}; "
        f"granted but never offered: {sorted(valid - set(SCREENS))}"
    )
    assert arms == set(SCREENS), (
        f"the screen `case` disagrees with SCREENS — no arm: {sorted(set(SCREENS) - arms)}; "
        f"arm never offered: {sorted(arms - set(SCREENS))}"
    )
