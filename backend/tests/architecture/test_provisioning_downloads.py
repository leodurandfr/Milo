"""Structural guardrail: what the image build may abort on, and what it may swallow.

Milō is provisioned one way: a flash of the pi-gen image built by
`pi-gen/stage-milo/`, which sources the shared modules under `provisioning/` for the
work it does not restate. Every one of those files runs under `set -e` inside a
chroot, so an unguarded command that fails ends the build — and a command whose
failure is *silenced* ends nothing and ships an image missing a package.

Two rules, both from defects measured on 2026-09-02:

  * **An unpinned download must be guarded.** A fetch built from a `*_VERSION`
    that `dependencies.env` declares *should* abort: a Milō without go-librespot
    is not a Milō, and failing loudly beats a half-provisioned unit. There is
    exactly one unpinned fetch — the Waveshare 8" DSI brightness driver, from a
    third-party vendor site, for a panel most units do not have. Unguarded, a
    vendor outage costs the whole run instead of one optional feature.
  * **An `apt install` must not be silenced.** `provisioning/common.sh` carried an
    `apt install … || true` for months: it named `libflac12t64` with a
    `libflac12` fallback, neither of which trixie has, so the call failed and
    took `libavahi-client3`/`libavahi-common3` down with it — and said nothing.
    The first CI image build is what surfaced it, in a log nobody reads when the
    build is green. A fallback onto a *different package name* is a different
    thing and stays allowed: `chromium || chromium-browser` is real cross-distro
    variance, and it is not silenced — if both arms fail, `set -e` ends the run,
    which is right.

The first rule has a second half, measured later: **a pinned download has no
unpinned fallback.** "Should abort" means nothing if a failed pinned fetch falls
through to installing the same package from the distribution. The snapcast block
did exactly that — its `.deb` fetches failing fell through to `apt-get install
snapserver snapclient`, trixie's 0.31.0 under a manifest that says 0.35.0, and the
build stayed green.

Nothing else catches any of these. These are shell files run in a chroot on a
builder CI reaches only through a three-hour job; there is no import to fail and
no route to 404, and every symptom is a green build that produced a wrong image.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# A fetch in *command position*: at the start of a statement, or after a
# separator or a condition keyword. Anchored this way so `if wget …` counts —
# missing the guarded form is what makes the rule read as having nothing to
# check — while `log_info "downloading with wget"` does not.
FETCH_RE = re.compile(
    r"(?:^|\bif\s+|\belif\s+|\bwhile\s+|\buntil\s+|\bthen\s+|&&\s*|\|\|\s*|;\s*|\|\s*)"
    r"\s*(?:sudo\s+)?(?:wget|curl)\b"
)

VERSION_RE = re.compile(r"\$\{?([A-Za-z0-9_]+)\}?")

# The two halves of the one provisioning path: the stage scripts, and the shared
# modules they source. Both are read, because a stage block and the module it
# sources are the same run and abort the same way.
FETCH_TREES = {
    "provisioning": sorted((REPO_ROOT / "provisioning").glob("*.sh")),
    "pi-gen": sorted((REPO_ROOT / "pi-gen" / "stage-milo").rglob("*run.sh")),
}


def _declared_versions() -> set[str]:
    text = (REPO_ROOT / "dependencies.env").read_text()
    names = set(re.findall(r"^([A-Z0-9_]+_VERSION)=", text, re.MULTILINE))
    assert len(names) >= 5, f"dependencies.env yielded only {names}"
    return names


def _statements(path: Path) -> list[str]:
    """Logical statements: backslash continuations joined, comments dropped."""
    text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def _fetches() -> list[tuple[str, Path, str]]:
    return [
        (tree, path, line)
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        if FETCH_RE.search(line)
    ]


def _is_pinned(line: str, declared: set[str]) -> bool:
    """Does this fetch name a version `dependencies.env` declares?

    The constant has to appear in the statement itself. An indirection —
    `local v="$CAMILLADSP_VERSION"` a few lines above, then `.../$v/...` — reads
    as unpinned, so the rule below would demand a guard on a download that should
    abort. That is a loud false positive, never a hidden defect, and no file does
    it: the two that did were the script installers' binary downloads.
    """
    return any(name in declared for name in VERSION_RE.findall(line))


def _is_guarded(line: str) -> bool:
    """Can this statement fail without ending the run?

    Either it is a condition (`if`/`while`/`until` — `set -e` is suspended
    there), or its failure is handled with `||`.
    """
    stripped = line.strip()
    return stripped.startswith(("if ", "elif ", "while ", "until ")) or "||" in stripped


def test_the_fetch_extractor_sees_both_kinds():
    """Both arms of the rule must have something to stand on.

    If every fetch read as pinned, the rule below would be vacuous; if none did,
    it would be wrong about the dependency downloads it deliberately exempts.
    """
    declared = _declared_versions()
    fetches = _fetches()

    # No total floor: the two counts below are what it would be the sum of, and a
    # broken FETCH_RE reads as zero pinned.
    pinned = [f for f in fetches if _is_pinned(f[2], declared)]
    unpinned = [f for f in fetches if not _is_pinned(f[2], declared)]
    assert len(pinned) >= 4, f"only {len(pinned)} pinned fetches recognised"
    assert unpinned, "no unpinned fetch found — the rule below has nothing to check"

    # The guard test must discriminate, not rubber-stamp.
    assert _is_guarded("if wget -q http://x/f.zip; then")
    assert _is_guarded("wget http://x/f.zip || true")
    assert not _is_guarded("wget http://x/f.zip")


def test_an_unpinned_download_cannot_abort_the_image_build():
    """A vendor outage must cost a feature, never the whole provisioning run.

    Pinned dependency downloads are exempt on purpose: those *should* abort —
    which `test_a_pinned_dependency_has_no_unpinned_install_fallback` holds.
    """
    declared = _declared_versions()
    unguarded = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, path, line in _fetches()
        if not _is_pinned(line, declared) and not _is_guarded(line)
    ]
    assert not unguarded, (
        "these downloads are not pinned by dependencies.env and can end the "
        "image build under `set -e`; guard them so the failure costs only the "
        "feature:\n" + "\n".join(unguarded)
    )


APT_INSTALL_RE = re.compile(r"apt(?:-get)?\s+install\b")


def test_no_apt_install_is_silenced():
    """A package that vanished upstream must end the run, not be swallowed."""
    silenced = []
    for tree, paths in FETCH_TREES.items():
        for path in paths:
            for line in _statements(path):
                if not APT_INSTALL_RE.search(line):
                    continue
                # Only the trailing `|| true` silences it; `|| apt install <other>`
                # is a fallback that still fails loudly when both arms fail.
                if re.search(r"\|\|\s*true\s*$", line.strip()):
                    silenced.append(f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}")
    assert not silenced, (
        "these apt installs are swallowed, so a package that no longer exists "
        "becomes a silent no-op:\n" + "\n".join(silenced)
    )


def test_the_apt_extractor_sees_the_installs():
    """The rule above is vacuous if no `apt install` is extracted at all."""
    found = [
        line
        for paths in FETCH_TREES.values()
        for path in paths
        for line in _statements(path)
        if APT_INSTALL_RE.search(line)
    ]
    assert len(found) >= 5, f"only {len(found)} apt install statements extracted"
    # ...and the matcher must discriminate.
    assert re.search(r"\|\|\s*true\s*$", "apt install -y x || true")
    assert not re.search(r"\|\|\s*true\s*$", "apt install -y x || apt install -y y")


# The package a pinned URL delivers, read from its basename: `snapserver_0.35.0-1_…`,
# `go-librespot_linux_arm64…`, `camilladsp-linux-aarch64…` — the name stops at the
# `_`/`-` before the version variable or the platform.
PINNED_PACKAGE_RE = re.compile(
    r"/([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*?)[_-](?:linux|\$)"
)

# `NN-packages[-nr]` lists are installed by pi-gen itself, one name per word, with no
# version: a pinned package listed there is the same fallback by another path.
PACKAGE_LISTS = sorted((REPO_ROOT / "pi-gen" / "stage-milo").rglob("*-packages*"))


def _pinned_packages(declared: set[str]) -> set[str]:
    names = {
        name
        for _tree, _path, line in _fetches()
        if _is_pinned(line, declared)
        for name in PINNED_PACKAGE_RE.findall(line)
    }
    assert len(names) >= 4 and {"snapserver", "snapclient"} <= names, (
        f"pinned package names extracted: {sorted(names)}"
    )
    return names


def _unpinned_install_of(line: str, packages: set[str], declared: set[str]) -> set[str]:
    """The pinned packages this statement's `apt install`s take from the distribution.

    Read per command, so `pinned || unpinned` on one line is still caught: an
    install that names a declared `*_VERSION` is the pinned `.deb` itself, one
    that names a pinned package bare installs whatever version the mirror has.
    """
    found: set[str] = set()
    for command in re.split(r"\|\||&&|[;|]", line):
        if APT_INSTALL_RE.search(command) and not _is_pinned(command, declared):
            found |= {arg.strip("\"'") for arg in command.split()} & packages
    return found


def test_a_pinned_dependency_has_no_unpinned_install_fallback():
    """A pinned fetch that fails must end the image build, not swap in Debian's.

    The image would ship a version `dependencies.env` never validated, on the
    server and on every satellite flashed from it, with a green build and a
    passing inspection.
    """
    declared = _declared_versions()
    packages = _pinned_packages(declared)

    # The rule must discriminate before it is trusted.
    assert _unpinned_install_of("apt-get install -y snapserver snapclient", packages, declared)
    assert not _unpinned_install_of(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_x.deb"', packages, declared
    )
    assert not _unpinned_install_of("apt-get install -y libportaudio2", packages, declared)
    assert _unpinned_install_of(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_x.deb" '
        "|| apt-get install -y snapserver",
        packages,
        declared,
    )

    fallbacks = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        if _unpinned_install_of(line, packages, declared)
    ]
    assert PACKAGE_LISTS, "no pi-gen 00-packages list found"
    fallbacks += [
        f"pi-gen: {path.relative_to(REPO_ROOT)}: {name}"
        for path in PACKAGE_LISTS
        for line in _statements(path)
        for name in sorted(set(line.split()) & packages)
    ]
    assert not fallbacks, (
        "these installs take a package dependencies.env pins from the "
        "distribution, at whatever version it carries; the pinned fetch must "
        "abort the build instead:\n" + "\n".join(fallbacks)
    )


# A Debian release spelled out in a build statement. The image's own is read from
# its /etc/os-release, so a literal can only be a default or a second attempt.
DEBIAN_RELEASE_RE = re.compile(r"\b(?:buster|bullseye|bookworm|trixie|forky|sid)\b")

# Another way to put a package on the image than the one `apt-get install ./x.deb`:
# `dpkg -i` then a repair, which satisfies a Depends it cannot meet by removing the
# package it just unpacked.
SECOND_METHOD_RE = re.compile(
    r"\bdpkg\s+(?:-i|--install)\b|--fix-broken\b|\bapt(?:-get)?\s+-f\s+install\b"
)

LATEST_RE = re.compile(r"releases/latest\b|/latest/download\b")

# What puts a pinned dependency's source or package on the image. `git clone` is
# here and not in FETCH_RE: an unpinned clone (the Milō checkout itself) must abort
# like a pinned fetch, so the guard rule above has nothing to say about it.
ACQUISITION_RE = re.compile(rf"{FETCH_RE.pattern}|\bgit\s+clone\b|{APT_INSTALL_RE.pattern}")


def _install_fallbacks(line: str, declared: set[str]) -> list[str]:
    """What in this statement lets a pinned install land something else."""
    found = []
    if DEBIAN_RELEASE_RE.search(line):
        found.append("names a Debian release instead of reading the image's")
    if SECOND_METHOD_RE.search(line):
        found.append("a second install method")
    if FETCH_RE.search(line) and LATEST_RE.search(line):
        found.append("fetches 'latest'")
    if _is_pinned(line, declared) and ACQUISITION_RE.search(line) and "||" in line:
        found.append("a pinned fetch or install with an alternative")
    return found


def test_a_pinned_dependency_installs_one_way_for_one_system():
    """A pinned install lands exactly its pin, built for this image, or the build fails.

    The snapcast block tried the image's codename, then bookworm, then fell back
    from `apt-get install` to `dpkg -i` and a repair — each arm one more way to
    ship a package the manifest never validated, for a system it does not run
    on, from a build that stayed green.
    """
    declared = _declared_versions()

    pinned_installs = [
        line
        for paths in FETCH_TREES.values()
        for path in paths
        for line in _statements(path)
        if _is_pinned(line, declared) and APT_INSTALL_RE.search(line)
    ]
    assert pinned_installs, "no pinned apt install extracted — the rule has nothing to check"

    # The rule must discriminate before it is trusted.
    assert _install_fallbacks('for V in "$C" "bookworm"; do', declared)
    assert _install_fallbacks("CODENAME=${CODENAME:-trixie}", declared)
    assert _install_fallbacks('dpkg -i "x.deb" && apt-get --fix-broken install -y', declared)
    assert _install_fallbacks(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}.deb" || dpkg -i x.deb', declared
    )
    assert _install_fallbacks("wget https://github.com/a/b/releases/latest/download/x.tgz", declared)
    assert not _install_fallbacks(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_${CODENAME}.deb"', declared
    )
    assert not _install_fallbacks("apt-get install -y chromium || apt-get install -y chromium-browser", declared)
    # A pinned fetch or clone has no alternative either: another version, another
    # architecture, or the default branch is still not the pin for this image.
    assert _install_fallbacks(
        "wget https://x/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_arm64.tar.gz "
        "|| wget https://x/v0.8.0/go-librespot_linux_arm64.tar.gz",
        declared,
    )
    assert _install_fallbacks(
        "wget https://x/snapserver_${SNAPCAST_VERSION}-1_arm64_${CODENAME}.deb "
        "|| wget https://x/snapserver_${SNAPCAST_VERSION}-1_armhf_${CODENAME}.deb",
        declared,
    )
    assert _install_fallbacks(
        "git clone --branch ${NQPTP_VERSION} --depth 1 https://github.com/mikebrady/nqptp.git "
        "|| git clone --depth 1 https://github.com/mikebrady/nqptp.git",
        declared,
    )
    assert not _install_fallbacks(
        "git clone --branch ${NQPTP_VERSION} --depth 1 https://github.com/mikebrady/nqptp.git", declared
    )

    fallbacks = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {reason}: {line.strip()[:80]}"
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        for reason in _install_fallbacks(line, declared)
    ]
    assert not fallbacks, (
        "these statements let a pinned dependency install something other than "
        "its pin for this image; the build must fail instead:\n" + "\n".join(fallbacks)
    )
