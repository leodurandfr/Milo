"""Structural guardrail: one declaration per dependency version, and it is `dependencies.env`.

Milō pins a version for nine upstream dependencies, and until this file existed
each of them was declared in two or three places at once: an `install/` module,
a `pi-gen/stage-milo/` stage script, and — for the six the Update Manager can
install — a `max_version` ceiling in the backend catalog. Nothing bound them.

That drift shipped. The Update Manager moved the fleet to go-librespot 0.9.0 and
shairport-sync 5.2.1 while the provisioning tree still cloned 0.8.0 and 4.3.7, so
a freshly flashed card landed *behind* every running unit — with no error
anywhere, because each declaration was internally consistent. The symptom was a
unit that had "just been installed" and behaved like an old one.

The manifest kills that class by construction rather than by vigilance: the
number exists once, and every consumer reads it. These tests are what makes
"reads it" true — a literal that creeps back into a provisioning script is
otherwise invisible to CI, which never runs bash and never builds an image.

Two trees, two ways of reading the same file:

  * the `install/` modules source it through `install/common.sh`, which the
    pi-gen stage scripts source in turn;
  * `pi-gen/stage-milo/` sources it as a *sibling*, because a stage is built
    from a copy of `stage-milo/` inside a cloned pi-gen checkout, often in
    Docker, and cannot reach the Milō repo — `pi-gen/build.sh` copies it in.
    This is invariant 2's shape applied to a third deployment tree, and the copy
    is checked here because nothing else can see a pi-gen build fail;
  * `backend/core/updates/catalog.py` names the line each program uses
    (`"validated_version_key"`) and `apply_validated_versions` resolves it into
    `"validated_version"`, which is what the update flow offers and installs.
    The association and the number are separate keys on purpose: `UpdateService`
    re-resolves after a `git pull`, when the process holds the old numbers and
    the disk holds the new ones.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

import pytest

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.dependency_versions import (
    MANIFEST_PATH,
    load_dependency_versions,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

def _strip_comments(text: str) -> str:
    """Drop `#` comments without touching a `#` that is not one.

    A line-wide `split("#")` corrupts both other spellings these files carry:
    `${rel_path#./}` is a prefix-strip expansion, and `\'\\033[0;31m\'` style
    literals carry `#` inside quotes. A comment opens only at line start or after
    whitespace, outside quotes. Getting one of them wrong makes this file
    under-report a restated version — the one failure mode a guardrail must not
    have.
    """
    out = []
    for line in text.splitlines():
        kept: list[str] = []
        quote = None
        for ch in line:
            if quote:
                kept.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                kept.append(ch)
            elif ch == "#" and (not kept or kept[-1].isspace()):
                break
            else:
                kept.append(ch)
        out.append("".join(kept))
    return "\n".join(out)


# `$VAR` / `${VAR}` — a manifest variable being read.
VAR_READ_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")

# `VAR=` at command position, with or without `export`/`local`. Anything the
# manifest owns must not be assigned anywhere but the manifest.
ASSIGN_RE = re.compile(r"^\s*(?:export\s+|local\s+)?([A-Z][A-Z0-9_]*)=", re.MULTILINE)

# `"validated_version_key": "VAR"` — the catalog's only reference to the manifest.
PY_READ_RE = re.compile(r'"validated_version_key"\s*:\s*"([A-Z][A-Z0-9_]*)"')

PI_GEN_STAGE = REPO_ROOT / "pi-gen" / "stage-milo"
BUILD_SH = REPO_ROOT / "pi-gen" / "build.sh"
CATALOG = REPO_ROOT / "backend" / "core" / "updates" / "catalog.py"

# The app itself, not a dependency: `milo` is updated by `git pull`, so it has no
# validated version to declare.
NOT_A_DEPENDENCY = "milo"


def _provisioning_scripts() -> list[Path]:
    """Every shell file that provisions a unit, across both trees."""
    return sorted(
        [
            *(REPO_ROOT / "install").glob("*.sh"),
            *PI_GEN_STAGE.rglob("*run.sh"),
        ]
    )


@pytest.fixture(scope="module")
def manifest() -> dict[str, str]:
    parsed = load_dependency_versions()
    # A parse that silently yields nothing, or that drops the values, would make
    # every rule below pass on an empty surface.
    assert len(parsed) >= 9, f"only {len(parsed)} versions parsed from {MANIFEST_PATH}"
    assert all(re.fullmatch(r"\d+(\.\d+)+", v) for v in parsed.values()), parsed
    return parsed


@pytest.fixture(scope="module")
def scripts() -> dict[Path, str]:
    parsed = {p: _strip_comments(p.read_text()) for p in _provisioning_scripts()}
    assert len(parsed) >= 20, f"only {len(parsed)} provisioning scripts found"
    return parsed


def test_every_updatable_program_declares_a_validated_version(manifest):
    """A program without a declared version is a program nobody validated.

    The whole point of the manifest is that the appliance installs a set someone
    signed off on. A new catalog entry with no key silently falls back to
    "whatever GitHub's releases/latest returns" — the exact button this plan
    removed, re-added by omission. This is the successor to
    `test_no_program_pins_a_ceiling_by_default`, inverted: back then no program
    pinned anything, now every dependency must.
    """
    declared = {k for k, cfg in PROGRAMS.items() if "validated_version_key" in cfg}
    expected = set(PROGRAMS) - {NOT_A_DEPENDENCY}

    assert declared == expected, (
        f"missing a validated_version_key: {sorted(expected - declared)}; "
        f"declared one but should not: {sorted(declared - expected)}"
    )

    unknown = {
        k: cfg["validated_version_key"]
        for k, cfg in PROGRAMS.items()
        if cfg.get("validated_version_key") not in manifest
        and "validated_version_key" in cfg
    }
    assert not unknown, f"keys dependencies.env does not declare: {unknown}"

    # And the key is actually *resolved* — an association nothing applies leaves
    # `validated_version` absent, which un-pins the program without changing a
    # single line anyone would look at.
    unresolved = {
        k: cfg.get("validated_version")
        for k, cfg in PROGRAMS.items()
        if "validated_version_key" in cfg
        and cfg.get("validated_version") != manifest[cfg["validated_version_key"]]
    }
    assert not unresolved, f"validated_version_key never resolved against the manifest: {unresolved}"


def test_the_catalog_declares_no_version_literal(manifest):
    """A literal in the catalog would be a second declaration that never drifts *loudly*.

    `"validated_version": "0.63.2"` matches the manifest today and goes stale the
    moment it moves, with nothing to notice — the same shape as a version literal
    in an install script, one tree over.
    """
    # Comment-stripped: the Navidrome entry documents what `navidrome --version`
    # prints, which is a version string and not a declaration. The shell rule
    # below strips comments for the same reason.
    code = _strip_comments(CATALOG.read_text())
    offenders = [
        f"catalog.py:{i} writes the literal {version!r}; "
        f'declare "validated_version_key": "{name}" instead'
        for i, line in enumerate(code.splitlines(), 1)
        for name, version in manifest.items()
        if version in line
    ]
    assert not offenders, "\n".join(offenders)


def test_no_provisioning_script_restates_a_manifest_version(manifest, scripts):
    """A version literal in an install or stage script is the drift, textually.

    This is the rule the fleet actually needed: `git clone --branch 5.2.1` in one
    tree and `5.2.1` in the manifest agree today and diverge on the next bump,
    with nothing to notice — a flashed card and a script-installed unit running
    different upstream code while both report a successful install.
    """
    offenders = []
    for path, code in scripts.items():
        for name, version in manifest.items():
            for i, line in enumerate(code.splitlines(), 1):
                if version in line:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{i} writes the literal "
                        f"{version!r}; read ${{{name}}} from dependencies.env instead"
                    )
    assert not offenders, "\n".join(offenders)


def test_no_provisioning_script_assigns_a_manifest_variable(manifest, scripts):
    """Re-assigning `NAVIDROME_VERSION=` locally shadows the manifest silently.

    A `${VAR:-default}` override reads as a courtesy and behaves as a second
    declaration: the manifest moves, the script keeps its own default, and the
    install lands on a version nobody chose.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{i} assigns {name}, which dependencies.env owns"
        for path, code in scripts.items()
        for i, line in enumerate(code.splitlines(), 1)
        for name in ASSIGN_RE.findall(line)
        if name in manifest
    ]
    assert not offenders, "\n".join(offenders)


def test_every_manifest_variable_has_a_reader(manifest, scripts):
    """The other direction: an entry nothing reads is a version nobody installs.

    A dependency dropped from the installers but left in the manifest reads as
    pinned and validated while no unit has ever run it.
    """
    read = {
        name
        for code in scripts.values()
        for name in VAR_READ_RE.findall(code)
    } | set(PY_READ_RE.findall(CATALOG.read_text()))

    # Non-trivial-output check: if nothing resolved, the rule above is vacuous
    # and so is `test_no_provisioning_script_restates_a_manifest_version`.
    assert len(read & set(manifest)) >= 5, f"only {sorted(read & set(manifest))} read"

    orphans = sorted(set(manifest) - read)
    assert not orphans, f"dependencies.env declares versions nothing reads: {orphans}"


def test_pi_gen_stage_scripts_can_reach_the_manifest():
    """A stage sourcing a file `build.sh` never copies dies at image-build time.

    `pi-gen/build.sh` copies `stage-milo/` into a cloned pi-gen checkout and
    builds it, often in Docker — the stage cannot reach back into the Milō repo,
    so the manifest has to travel with it. Nothing else catches this: CI never
    builds an image, and the failure is a `set -e` abort an hour into a build.
    Invariant 2's rule, applied to the third deployment tree.
    """
    build = BUILD_SH.read_text()
    assert "dependencies.env" in build, (
        "pi-gen/build.sh no longer copies dependencies.env beside the stage; "
        "every stage script that sources it aborts the image build"
    )

    readers = [
        p
        for p in PI_GEN_STAGE.rglob("*run.sh")
        if set(VAR_READ_RE.findall(_strip_comments(p.read_text()))) & set(load_dependency_versions())
    ]
    assert readers, "no pi-gen stage script reads a manifest variable any more"

    for script in readers:
        assert "dependencies.env" in script.read_text(), (
            f"{script.relative_to(REPO_ROOT)} reads a manifest variable but never "
            "sources dependencies.env — it expands to the empty string, and the "
            "download URL it builds is silently wrong"
        )


def test_the_manifest_is_tracked_by_git():
    """An untracked manifest works here and breaks every clone, install and build."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(MANIFEST_PATH.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert tracked.returncode == 0, (
        "dependencies.env is not in git: every install chain sources it and "
        "every pi-gen build copies it"
    )
