"""Structural guardrail: one declaration per dependency version, and it is `dependencies.env`.

Milō pins a version for nine upstream dependencies, and until this file existed
each of them was declared in two or three places at once: a `provisioning/` module,
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

  * the `provisioning/` modules source it through `provisioning/common.sh`, which the
    pi-gen stage scripts source in turn;
  * `pi-gen/stage-milo/` sources it as a *sibling*, because a stage is built
    from a copy of `stage-milo/` inside a cloned pi-gen checkout, often in
    Docker, and cannot reach the Milō repo — `pi-gen/build.sh` copies it in.
    This is invariant 2's shape applied to a third deployment tree, and the copy
    is checked here because nothing else can see a pi-gen build fail;
  * `backend/core/updates/catalog.py` names the line each program uses
    (`"validated_version_key"`) and `apply_validated_versions` resolves it into
    `"validated_version"`, which is what the update flow offers and installs.

The manifest also declares the sha256 of every file those versions download, a
`*_SHA256` line beside each `*_VERSION`, and that is held both ways too: every
asset the Update Manager installs has its digest, and every download of one —
the update flow's, a satellite's, the image build's — is checked against it
before anything runs as root. The Python half of that second direction is
behavior (test_update_service.py, the satellite's own tests, the helpers run
under bash in test_deploy_update_install_deb.py); the shell half is read here,
because CI never builds an image.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

import pytest

from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.dependency_versions import (
    COMMIT_SUFFIX,
    DIGEST_SUFFIX,
    MANIFEST_PATH,
    VERSION_SUFFIX,
    load_dependency_manifest,
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

# `"validated_version_key": "VAR"`, and the values of `"validated_sha256_keys"` —
# the catalog's only references to the manifest.
PY_READ_RE = re.compile(r'"validated_version_key"\s*:\s*"([A-Z][A-Z0-9_]*)"')
PY_DIGEST_READ_RE = re.compile(r'"([A-Z][A-Z0-9_]*_SHA256)"')

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
            *(REPO_ROOT / "provisioning").glob("*.sh"),
            *PI_GEN_STAGE.rglob("*run.sh"),
        ]
    )


@pytest.fixture(scope="module")
def manifest() -> dict[str, str]:
    """Every line, versions and digests: a digest restated in a script drifts
    exactly as a version does."""
    parsed = load_dependency_manifest()
    # A parse that silently yields nothing, or that drops the values, would make
    # every rule below pass on an empty surface.
    versions = {k: v for k, v in parsed.items() if k.endswith(VERSION_SUFFIX)}
    digests = {k: v for k, v in parsed.items() if k.endswith(DIGEST_SUFFIX)}
    assert len(versions) >= 9, f"only {len(versions)} versions parsed from {MANIFEST_PATH}"
    assert len(digests) >= 8, f"only {len(digests)} digests parsed from {MANIFEST_PATH}"
    assert all(re.fullmatch(r"\d+(\.\d+)+", v) for v in versions.values()), versions
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in digests.values()), digests
    return parsed


def _digest_keys(cfg: dict) -> set[str]:
    """The manifest lines a catalog entry's digests come from, NQPTP's included."""
    return {
        key
        for entry in (cfg, cfg.get("nqptp") or {})
        for key in (entry.get("validated_sha256_keys") or {}).values()
    }


@pytest.fixture(scope="module")
def scripts() -> dict[Path, str]:
    parsed = {p: _strip_comments(p.read_text()) for p in _provisioning_scripts()}
    assert len(parsed) >= 15, f"only {len(parsed)} provisioning scripts found"
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


def test_every_asset_the_update_manager_installs_has_a_declared_digest(manifest):
    """A file installed as root with no digest is whatever GitHub served that day.

    Every program the catalog pins, and NQPTP with shairport-sync, names the
    manifest line holding the sha256 of each file its validated version
    downloads — one per binary it installs (`commands`), so multiroom's two
    .debs each have theirs and the satellites' snapclient and CamillaDSP are
    the server's own. Fails when a program gains a pin without a digest, or a
    digest key the manifest does not declare: `UpdateService` would then
    install that validated version unchecked, or not at all.
    """
    # (label, entry, the components it installs)
    entries = [
        (key, cfg, set(cfg["commands"])) for key, cfg in PROGRAMS.items() if key != NOT_A_DEPENDENCY
    ] + [
        (f"{key}/nqptp", cfg["nqptp"], {"main"}) for key, cfg in PROGRAMS.items() if cfg.get("nqptp")
    ]
    assert len(entries) >= 7, f"only {[e[0] for e in entries]} catalog entries read"

    missing = {}
    for key, entry, components in entries:
        declared = entry.get("validated_sha256_keys") or {}
        if set(declared) != components:
            missing[key] = f"digests for {sorted(declared)}, installs {sorted(components)}"
        elif entry.get("validated_sha256") != {a: manifest.get(k) for a, k in declared.items()} \
                or not all(manifest.get(k) for k in declared.values()):
            missing[key] = f"{declared} not resolved against dependencies.env"
    assert not missing, f"assets with no declared digest: {missing}"


def test_every_digest_sits_beside_the_version_it_hashes():
    """A digest is of one version's file: read under another, it refuses the
    install, or worse, nobody notices which version it was taken for.

    Fails when a `*_SHA256` line no longer follows its program's `*_VERSION`
    in dependencies.env (only comments between them), so a bump edits both in
    one place and a reviewer reads them together.
    """
    lines = [ln for ln in MANIFEST_PATH.read_text().splitlines() if ln and not ln.startswith("#")]
    keys = [ln.split("=", 1)[0] for ln in lines]
    assert len(keys) >= 17, f"only {len(keys)} manifest lines read"
    owners = {
        digest: entry["validated_version_key"]
        for cfg in PROGRAMS.values()
        for entry in (cfg, cfg.get("nqptp") or {})
        for digest in (entry.get("validated_sha256_keys") or {}).values()
    }
    assert len(owners) >= 8, owners

    misplaced = []
    for digest, version in owners.items():
        at = keys.index(digest)
        preceding = keys[keys.index(version) + 1:at] if keys.index(version) < at else None
        if preceding is None or any(not k.endswith(DIGEST_SUFFIX) or owners.get(k) != version for k in preceding):
            misplaced.append(f"{digest} is not right after {version}")
    assert not misplaced, "\n".join(misplaced)


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

    A dependency dropped from the provisioning tree but left in the manifest
    reads as pinned and validated while no unit has ever run it — and a digest
    nothing reads is a file nobody checks, while the manifest says it is.
    """
    read = {
        name
        for code in scripts.values()
        for name in VAR_READ_RE.findall(code)
    } | set(PY_READ_RE.findall(CATALOG.read_text())) | set(PY_DIGEST_READ_RE.findall(CATALOG.read_text()))

    # Non-trivial-output check: if nothing resolved, the rule above is vacuous
    # and so is `test_no_provisioning_script_restates_a_manifest_version`.
    assert len(read & set(manifest)) >= 5, f"only {sorted(read & set(manifest))} read"

    orphans = sorted(set(manifest) - read)
    assert not orphans, f"dependencies.env declares versions nothing reads: {orphans}"


# A download, and a check of one, at the start of a statement.
FETCH_START_RE = re.compile(r"^\s*(?:sudo\s+)?(?:wget|curl)\b")
DIGEST_CHECK_RE = re.compile(r"\bsha256sum\s+-c\b")


def test_every_image_download_of_a_digested_asset_is_checked(manifest, scripts):
    """The image installs as root what its build downloads, so the build checks
    each file the manifest has a digest for, as the in-app update does.

    Fails when a stage script or a module it sources fetches a file built from a
    `*_VERSION` whose program declares digests, with fewer `sha256sum -c`
    statements naming one of that program's `*_SHA256` lines in the same file
    than it has such fetches: an image would then ship whatever the URL served
    the day it was built, with the digest sitting unread beside its version.
    """
    owners = {
        entry["validated_version_key"]: set((entry.get("validated_sha256_keys") or {}).values())
        for cfg in PROGRAMS.values()
        for entry in (cfg, cfg.get("nqptp") or {})
        if entry.get("validated_version_key")
    }
    assert sum(1 for d in owners.values() if d) >= 7, owners

    counted = 0
    unchecked = []
    for path, code in scripts.items():
        statements = re.sub(r"\\\n\s*", " ", code).splitlines()
        for version, digests in owners.items():
            fetches = [s for s in statements if FETCH_START_RE.search(s) and version in VAR_READ_RE.findall(s)]
            checks = [s for s in statements if DIGEST_CHECK_RE.search(s) and digests & set(VAR_READ_RE.findall(s))]
            counted += len(fetches)
            if fetches and len(checks) < len(fetches):
                unchecked.append(
                    f"{path.relative_to(REPO_ROOT)}: {len(fetches)} download(s) of ${{{version}}}, "
                    f"{len(checks)} sha256sum -c against {sorted(digests)}"
                )
    # The extractor must see the downloads it judges: go-librespot, Navidrome,
    # CamillaDSP, the two snapcast .debs, NQPTP, shairport-sync and qobuz-proxy.
    assert counted >= 8, f"only {counted} downloads of a digested asset recognized"
    assert not unchecked, "\n".join(unchecked)


# A clone by tag, `git clone --branch v${X_VERSION} …`, and the check of its HEAD.
CLONE_RE = re.compile(r"\bgit\s+clone\b[^\n]*--branch\s+\S*?\$\{?([A-Z][A-Z0-9_]*" + VERSION_SUFFIX + r")\b")
HEAD_CHECK_RE = re.compile(r"\bgit\s+rev-parse\s+HEAD\b")


def test_every_tag_the_image_clones_is_held_to_its_commit(manifest, scripts):
    """A tag is a pointer its owner can move: a clone by tag alone builds, as
    root, whatever the tag names the day the image is built, which is no
    longer what was validated once it moves.

    Fails when a stage script or a module it sources clones a `*_VERSION`'s
    tag and the manifest declares no `*_COMMIT` right after that version, or
    the same file has fewer `git rev-parse HEAD` checks against that commit
    than it has clones of the tag.
    """
    lines = [ln.split("=", 1)[0] for ln in MANIFEST_PATH.read_text().splitlines() if ln and not ln.startswith("#")]
    counted = 0
    failures = []
    for path, code in scripts.items():
        statements = re.sub(r"\\\n\s*", " ", code).splitlines()
        for version in {v for st in statements for v in CLONE_RE.findall(st)}:
            commit = version[: -len(VERSION_SUFFIX)] + COMMIT_SUFFIX
            clones = [st for st in statements if version in CLONE_RE.findall(st)]
            counted += len(clones)
            where = path.relative_to(REPO_ROOT)
            if commit not in manifest:
                failures.append(f"{where} clones ${{{version}}}'s tag, and dependencies.env declares no {commit}")
                continue
            if lines.index(commit) != lines.index(version) + 1:
                failures.append(f"{commit} is not right after {version} in dependencies.env")
            checks = [st for st in statements if HEAD_CHECK_RE.search(st) and commit in VAR_READ_RE.findall(st)]
            if len(checks) < len(clones):
                failures.append(
                    f"{where}: {len(clones)} clone(s) of ${{{version}}}'s tag, "
                    f"{len(checks)} check(s) of git rev-parse HEAD against ${{{commit}}}"
                )
    # bluez-alsa and roc-toolkit, the two the image builds from a clone.
    assert counted >= 2, f"only {counted} clone(s) by tag recognized"
    assert not failures, "\n".join(failures)


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
        if set(VAR_READ_RE.findall(_strip_comments(p.read_text()))) & set(load_dependency_manifest())
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
        "dependencies.env is not in git: the install modules source it and "
        "every pi-gen build copies it"
    )
