"""Structural guardrail: THIRD_PARTY_NOTICES.md names what this release actually carries.

The notices are what makes the written offer for GPL source accompany the image
(the file sits in the checkout on the card, which pi-gen/inspect-image.sh checks).
They restate versions on purpose — a reader of the file has no manifest at hand —
and a restated version is exactly what drifts: a dependency bump that leaves the
notices behind ships a release whose license page names a program it no longer
carries, at a version it no longer runs, with a source link to the wrong tag.

So every table the file holds is read here and held to the file it restates:

  * section 1 against `dependencies.env`, both directions — every `*_VERSION`
    has its row and every row its line, at that version, with a source link to
    that version's tag (and the commit a cloned tag is held to);
  * section 3's packages against `system-manifest.env`'s SYSTEM_APT_PACKAGES;
  * section 4's packages against `requirements.lock`, at the version it pins;
  * section 5 against `frontend/package.json`'s runtime dependencies, both
    directions, at the version and license `package-lock.json` records — and
    every license the bundled tree carries is one the file names, and every
    font family `frontend/src/assets/fonts/` ships is one it names;
  * section 6, the written offer, names every copyleft program of section 1.

What no file here can hold is a license itself: a bump that crosses an upstream
relicensing is caught by the bump procedure (docs/development.md § Validating a
dependency bump), which moves the row's license with its version. The same goes
for the OpenFEC version Roc Toolkit's build downloads, pinned in roc's own tree:
the Roc row is only held to name an OpenFEC tag at all.

Each comparison is a function of the text, so the drift test feeds it a copy
with one thing moved and watches it go red. Every extractor asserts its output
is non-trivial first: a broken parse must fail loudly, not pass on an empty table.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from backend.core.updates.dependency_versions import (
    COMMIT_SUFFIX,
    VERSION_SUFFIX,
    load_dependency_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
NOTICES = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
SYSTEM_MANIFEST = REPO_ROOT / "system-manifest.env"
PYTHON_LOCK = REPO_ROOT / "requirements.lock"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"
FONTS_DIR = REPO_ROOT / "frontend" / "src" / "assets" / "fonts"

MANIFEST_SECTION = "1. Programs pinned by the dependency manifest"
SYSTEM_SECTION = "3. The operating system"
PYTHON_SECTION = "4. Python packages"
FRONTEND_SECTION = "5. The web interface"
OFFER_SECTION = "6. Written offer for source code"

# The licenses whose terms ask for the source with the binary: the ones the
# written offer has to name a program under.
COPYLEFT = re.compile(r"\b(GPL|LGPL|MPL|CeCILL|CECILL)")


# --------------------------------------------------------------------------- #
# Extractors
# --------------------------------------------------------------------------- #

def _sections(text: str) -> dict[str, str]:
    """`## <title>` → the text up to the next `## `."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {title.strip(): body for title, body in zip(parts[1::2], parts[2::2])}


def _table(body: str) -> list[dict[str, str]]:
    """The first Markdown table of a section, as {header: cell} rows."""
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    assert len(lines) >= 3, f"no table found in:\n{body[:300]}"
    cells = [[c.strip() for c in line.strip("|").split("|")] for line in lines]
    header, separator, rows = cells[0], cells[1], cells[2:]
    assert all(set(c) <= set("-: ") for c in separator), f"not a table separator: {separator}"
    for row in rows:
        assert len(row) == len(header), f"row has {len(row)} cells, header {len(header)}: {row}"
    return [dict(zip(header, row)) for row in rows]


def _ticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def _program_name(cell: str) -> str:
    """`go-librespot (Spotify Connect)` → `go-librespot`."""
    return cell.split(" (")[0].strip()


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_lock() -> dict[str, str]:
    pins = dict(re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", PYTHON_LOCK.read_text(), re.MULTILINE))
    assert len(pins) >= 30 and "fastapi" in pins, pins
    return {_normalized(name): version for name, version in pins.items()}


def _apt_packages() -> set[str]:
    """SYSTEM_APT_PACKAGES as bash reads it (comments and line breaks included)."""
    out = subprocess.run(
        ["bash", "-c", f'source "{SYSTEM_MANIFEST}"; printf "%s\\n" "${{SYSTEM_APT_PACKAGES[@]}}"'],
        capture_output=True, text=True, check=True,
    ).stdout
    packages = set(out.split())
    assert len(packages) >= 50 and {"nginx", "mpv"} <= packages, sorted(packages)
    return packages


def _font_families() -> set[str]:
    """`NotoSansSC-Medium.woff2` → `Noto Sans SC`, one per family the interface ships."""
    files = [*FONTS_DIR.glob("*.woff2"), *FONTS_DIR.glob("*.woff"), *FONTS_DIR.glob("*.ttf")]
    families = {re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f.stem.split("-")[0]) for f in files}
    assert len(families) >= 3 and "Space Mono" in families, sorted(families)
    return families


def _bundled_npm() -> tuple[dict[str, str], dict[str, dict]]:
    """(package.json's runtime dependencies, package-lock's non-dev packages by name)."""
    dependencies = json.loads(PACKAGE_JSON.read_text())["dependencies"]
    lock = json.loads(PACKAGE_LOCK.read_text())["packages"]
    bundled = {
        key.rsplit("node_modules/", 1)[1]: entry
        for key, entry in lock.items()
        if key and not entry.get("dev") and not entry.get("devOptional")
    }
    assert {"vue", "axios"} <= set(dependencies) <= set(bundled), (dependencies, sorted(bundled))
    return dependencies, bundled


# --------------------------------------------------------------------------- #
# Comparisons — each returns what is wrong, so a drift can be fed to it
# --------------------------------------------------------------------------- #

def manifest_problems(text: str, manifest: dict[str, str]) -> list[str]:
    rows = _table(_sections(text)[MANIFEST_SECTION])
    assert len(rows) >= 5, rows
    problems = []
    by_key = {}
    for row in rows:
        keys = _ticked(row["Manifest line"])
        assert len(keys) == 1, f"a row names one manifest line: {row}"
        by_key[keys[0]] = row

    declared = {key for key in manifest if key.endswith(VERSION_SUFFIX)}
    for key in sorted(declared - set(by_key)):
        problems.append(f"{key} is in dependencies.env and has no row in the notices")
    for key in sorted(set(by_key) - declared):
        problems.append(f"the notices list {key}, which dependencies.env does not declare")

    for key, row in by_key.items():
        if key not in declared:
            continue
        version = manifest[key]
        if row["Version"] != version:
            problems.append(f"{key}: the notices say {row['Version']}, dependencies.env {version}")
        if not re.search(rf"/tree/v?{re.escape(version)}\b", row["Source"]):
            problems.append(f"{key}: the source link is not the tag of {version}: {row['Source']}")
        commit = manifest.get(key.removesuffix(VERSION_SUFFIX) + COMMIT_SUFFIX)
        if commit and commit not in row["Source"]:
            problems.append(f"{key}: the source does not name the commit {commit} the tag is held to")
        if not row["License"]:
            problems.append(f"{key}: no license")
    return problems


def offer_problems(text: str) -> list[str]:
    sections = _sections(text)
    offer = sections[OFFER_SECTION]
    assert "three years" in offer and "3(b)" in offer, offer[:300]
    copyleft = [
        _program_name(row["Program"])
        for row in _table(sections[MANIFEST_SECTION])
        if COPYLEFT.search(row["License"])
    ]
    assert len(copyleft) >= 3, copyleft
    return [f"the written offer does not name {name}" for name in copyleft if name not in offer]


def system_problems(text: str, packages: set[str]) -> list[str]:
    rows = _table(_sections(text)[SYSTEM_SECTION])
    named = [name for row in rows for name in _ticked(row["Package"])]
    assert len(named) >= 10, named
    return [f"the notices list {name}, which system-manifest.env does not install"
            for name in named if name not in packages]


def python_problems(text: str, pins: dict[str, str]) -> list[str]:
    rows = _table(_sections(text)[PYTHON_SECTION])
    assert len(rows) >= 10, rows
    problems = []
    for row in rows:
        name = _normalized(row["Package"])
        if name not in pins:
            problems.append(f"the notices list {row['Package']}, which requirements.lock does not pin")
        elif pins[name] != row["Version"]:
            problems.append(f"{row['Package']}: the notices say {row['Version']}, the lock pins {pins[name]}")
    return problems


def frontend_problems(text: str, dependencies: dict[str, str], bundled: dict[str, dict]) -> list[str]:
    body = _sections(text)[FRONTEND_SECTION]
    rows = {row["Package"]: row for row in _table(body)}
    assert len(rows) >= 3, rows
    problems = [f"{name} is a runtime dependency of the frontend and has no row" for name in sorted(set(dependencies) - set(rows))]
    problems += [f"the notices list {name}, which package.json does not depend on" for name in sorted(set(rows) - set(dependencies))]
    for name, row in rows.items():
        entry = bundled.get(name)
        if entry is None:
            continue
        if row["Version"] != entry["version"]:
            problems.append(f"{name}: the notices say {row['Version']}, package-lock.json {entry['version']}")
        if row["License"] != entry.get("license"):
            problems.append(f"{name}: the notices say {row['License']}, package-lock.json {entry.get('license')}")
    for license_id in sorted({entry.get("license") or "(none)" for entry in bundled.values()}):
        if f"`{license_id}`" not in body:
            problems.append(f"the bundle carries a package under {license_id}, which the notices do not name")
    return problems


def font_problems(text: str, families: set[str]) -> list[str]:
    body = _sections(text)[FRONTEND_SECTION]
    return [f"the interface ships the font {family}, which the notices do not name"
            for family in sorted(families) if family not in body]


# --------------------------------------------------------------------------- #
# The tree as it stands
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def text() -> str:
    return NOTICES.read_text(encoding="utf-8")


def test_the_extractor_reads_every_section(text):
    sections = _sections(text)
    for title in (MANIFEST_SECTION, SYSTEM_SECTION, PYTHON_SECTION, FRONTEND_SECTION, OFFER_SECTION):
        assert title in sections, f"section missing: {title!r} (found {sorted(sections)})"
    rows = _table(sections[MANIFEST_SECTION])
    assert {"go-librespot", "NQPTP", "Snapcast"} <= {_program_name(r["Program"]) for r in rows}


def test_the_notices_carry_every_manifest_program_at_its_version(text):
    assert manifest_problems(text, load_dependency_manifest()) == []


def test_the_written_offer_names_every_copyleft_program(text):
    assert offer_problems(text) == []


def test_the_system_packages_named_are_ones_the_manifest_installs(text):
    assert system_problems(text, _apt_packages()) == []


def test_the_python_packages_named_are_pinned_at_that_version(text):
    assert python_problems(text, _python_lock()) == []


def test_the_frontend_rows_are_the_bundled_dependencies(text):
    assert frontend_problems(text, *_bundled_npm()) == []


def test_every_bundled_font_is_named(text):
    assert font_problems(text, _font_families()) == []


def test_the_roc_row_names_the_openfec_it_builds(text):
    rows = _table(_sections(text)[MANIFEST_SECTION])
    (roc,) = [row for row in rows if _ticked(row["Manifest line"]) == ["ROC_TOOLKIT_VERSION"]]
    assert re.search(r"github\.com/roc-streaming/openfec/tree/v\d", roc["Source"]), roc["Source"]


# --------------------------------------------------------------------------- #
# The guardrail goes red on a drift
# --------------------------------------------------------------------------- #

def test_a_bumped_manifest_line_left_behind_is_caught(text):
    manifest = dict(load_dependency_manifest())
    manifest["NQPTP_VERSION"] = "9.9.9"
    problems = manifest_problems(text, manifest)
    assert any("NQPTP_VERSION: the notices say" in p for p in problems), problems
    assert any("NQPTP_VERSION: the source link" in p for p in problems), problems


def test_a_manifest_line_with_no_row_and_a_row_with_no_line_are_caught(text):
    manifest = dict(load_dependency_manifest())
    manifest["NEW_DAEMON_VERSION"] = "1.0.0"
    del manifest["NAVIDROME_VERSION"]
    problems = manifest_problems(text, manifest)
    assert any(p.startswith("NEW_DAEMON_VERSION is in dependencies.env") for p in problems), problems
    assert any("NAVIDROME_VERSION, which dependencies.env does not declare" in p for p in problems), problems


def test_a_moved_commit_is_caught(text):
    manifest = dict(load_dependency_manifest())
    manifest["ROC_TOOLKIT_COMMIT"] = "0" * 40
    assert any("ROC_TOOLKIT_VERSION: the source does not name the commit" in p
               for p in manifest_problems(text, manifest))


def test_a_copyleft_program_missing_from_the_offer_is_caught(text):
    offer_start = text.index(f"## {OFFER_SECTION}")
    drifted = text[:offer_start] + text[offer_start:].replace("NQPTP", "the timing daemon")
    assert offer_problems(drifted) == ["the written offer does not name NQPTP"]


def test_stale_package_rows_are_caught(text):
    pins = dict(_python_lock())
    pins["fastapi"] = "0.0.1"
    assert any(p.startswith("fastapi:") for p in python_problems(text, pins))
    assert any("mpv" in p for p in system_problems(text, _apt_packages() - {"mpv"}))

    dependencies, bundled = _bundled_npm()
    dependencies = {**dependencies, "left-pad": "^1.0.0"}
    bundled = {**bundled, "left-pad": {"version": "1.3.0", "license": "WTFPL"}}
    problems = frontend_problems(text, dependencies, bundled)
    assert "left-pad is a runtime dependency of the frontend and has no row" in problems
    assert any("under WTFPL" in p for p in problems), problems

    assert font_problems(text, _font_families() | {"Inter Display"}) == [
        "the interface ships the font Inter Display, which the notices do not name"
    ]
